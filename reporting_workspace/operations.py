"""Offline, tenant-local reporting operations with bounded server-owned inputs.

The identity provider is reloaded on every public action. Identity dictionaries
must be resolved by the server, never accepted as authorization from a browser.
Sources and delivery channels below are metadata, not executable adapters. This
module opens no company database, URL, SMTP connection, or background worker.
Report snapshots retain source record IDs for explicit data lineage. Usage keeps
aggregate counters only: no user IDs, request text, IPs, or individual view logs.
"""
from collections import deque
from collections.abc import Mapping
from contextlib import contextmanager
from decimal import Decimal, localcontext
import csv
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

from demo_services import AccessDenied, MailSink
from .crud import Conflict, NotFound, StateUnavailable, ValidationError, _text
from .providers import validate_identity
from .state import _ddl_tokens, _path


SCHEMA_VERSION = 1
DEMO_REPORT_ID = 'monthly-performance'
MAX_REPORTS = 200
MAX_VERSIONS = 100
MAX_ROWS = 5000
MAX_COLUMNS = 32
MAX_SNAPSHOT_BYTES = 2_000_000
MAX_CONTRIBUTION_GROUPS = 50
MAX_CONTRIBUTION_RECORD_IDS = 100
CHANNELS = ('export', 'api', 'mail')
USAGE_EVENTS = ('report_view', 'self_service', 'selfservice_save', 'catalog_search',
                'version_diff', 'source_drilldown', 'reuse', 'filter', 'clone')
_INTERNAL_EVENTS = ('mock_send', 'quality_block')
_KEY = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,63}\Z')
_RECORD_KEY = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')
_RECIPIENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,63}@example\.invalid\Z')
_DDL = (
    '''CREATE TABLE operations_reports (
        org TEXT NOT NULL, id TEXT NOT NULL, metadata TEXT NOT NULL,
        latest_version INTEGER NOT NULL CHECK(latest_version>=0),
        PRIMARY KEY(org,id))''',
    '''CREATE TABLE operations_versions (
        org TEXT NOT NULL, report_id TEXT NOT NULL,
        version INTEGER NOT NULL CHECK(version>0), definition TEXT NOT NULL,
        snapshot TEXT NOT NULL, created_at REAL NOT NULL,
        PRIMARY KEY(org,report_id,version),
        FOREIGN KEY(org,report_id) REFERENCES operations_reports(org,id))''',
    '''CREATE TABLE operations_usage (
        org TEXT NOT NULL, report_id TEXT NOT NULL, event TEXT NOT NULL,
        count INTEGER NOT NULL CHECK(count>=0),
        last_event_at REAL NOT NULL CHECK(last_event_at>=0),
        PRIMARY KEY(org,report_id,event))''',
    '''CREATE TABLE operations_simulations (
        org TEXT NOT NULL, id TEXT NOT NULL, report_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('blocked','simulated')),
        issue_codes TEXT NOT NULL, row_count INTEGER NOT NULL CHECK(row_count>=0),
        recipient_count INTEGER NOT NULL CHECK(recipient_count BETWEEN 1 AND 20),
        created_at REAL NOT NULL, PRIMARY KEY(org,id),
        FOREIGN KEY(org,report_id) REFERENCES operations_reports(org,id))''',
)


def _key(value, name='identifier'):
    if not isinstance(value, str) or not _KEY.fullmatch(value):
        raise ValidationError('{} must be a bounded metadata key.'.format(name))
    return value


def _record_key(value):
    if not isinstance(value, str) or not _RECORD_KEY.fullmatch(value):
        raise ValidationError('Source record IDs must be bounded opaque identifiers.')
    return value


def _integer(value, minimum=1, maximum=MAX_VERSIONS):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValidationError('An integer within the documented bounds is required.')
    return value


def _fields(value, maximum=MAX_COLUMNS, empty=False):
    if (not isinstance(value, list) or len(value) > maximum or
            (not empty and not value)):
        raise ValidationError('Select a bounded list of distinct fields.')
    result = [_key(item, 'field') for item in value]
    if len(set(result)) != len(result) or 'record_id' in result:
        raise ValidationError('Fields must be distinct and cannot use reserved record_id.')
    return result


def _scalar(value):
    if value is None or type(value) is bool:
        return value
    if isinstance(value, str):
        if len(value) > 500:
            raise ValidationError('Cells must use at most 500 characters.')
        _text(value, 'cell', 500)
        return value  # Whitespace is data in immutable source-record snapshots.
    if type(value) is int and abs(value) <= 9_007_199_254_740_991:
        return value
    if type(value) is float and math.isfinite(value) and abs(value) <= 1e15:
        return value
    raise ValidationError('Cells must be bounded text, finite numbers, booleans, or null.')


def _dump(value):
    result = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(result.encode('utf-8')) > MAX_SNAPSHOT_BYTES:
        raise ValidationError('The snapshot exceeds the bounded local storage limit.')
    return result


def _rows(value, unique=False):
    if not isinstance(value, list) or len(value) > MAX_ROWS:
        raise ValidationError('Rows must be a bounded list of source records.')
    result, seen = [], set()
    for row in value:
        if not isinstance(row, Mapping) or not 1 <= len(row) <= MAX_COLUMNS + 1 or 'record_id' not in row:
            raise ValidationError('Every source row requires record_id and bounded scalar fields.')
        identifier = _record_key(row['record_id'])
        if unique and identifier in seen:
            raise ValidationError('Version source record IDs must be distinct.')
        seen.add(identifier)
        item = {'record_id': identifier}
        for name, value in row.items():
            if name != 'record_id':
                item[_key(name, 'field')] = _scalar(value)
        result.append(item)
    _dump(result)
    return result


def _filters(value, allowed=None):
    if not isinstance(value, list) or len(value) > 8:
        raise ValidationError('At most eight declarative filters are supported.')
    result = []
    for item in value:
        if (not isinstance(item, Mapping) or set(item) != {'field', 'op', 'value'} or
                not isinstance(item['op'], str) or item['op'] not in ('eq', 'contains', 'gte', 'lte')):
            raise ValidationError('Filters require a field, supported operator, and scalar value.')
        field = _key(item['field'], 'filter field')
        if allowed is not None and field not in allowed:
            raise ValidationError('Filters must reference the registered report fields.')
        if item['op'] == 'contains' and not isinstance(item['value'], str):
            raise ValidationError('Contains filters require text.')
        result.append({'field': field, 'op': item['op'], 'value': _scalar(item['value'])})
    # Declarative AND filters are order-independent, making comparisons stable.
    return sorted(result, key=_dump)


def _definition(value, metadata):
    allowed = {'source', 'columns', 'filters', 'group_by', 'aggregate', 'limit'}
    if not isinstance(value, Mapping) or set(value) - allowed:
        raise ValidationError('Only documented declarative report settings are accepted.')
    if value.get('source', metadata['source_key']) != metadata['source_key']:
        raise ValidationError('A version cannot switch to an unregistered source.')
    columns = _fields(value.get('columns', metadata['columns']))
    group_by = _fields(value.get('group_by', []), maximum=4, empty=True)
    if not set(columns + group_by).issubset(metadata['columns']):
        raise ValidationError('Version fields must be registered report fields.')
    aggregate = value.get('aggregate')
    if aggregate is not None and aggregate not in ('count', 'sum'):
        raise ValidationError('Only documented aggregates are supported.')
    return dict(source=metadata['source_key'], columns=columns,
                filters=_filters(value.get('filters', []), metadata['columns']),
                group_by=group_by, aggregate=aggregate,
                limit=_integer(value.get('limit', 100), maximum=MAX_ROWS),
                provenance='registered-local-snapshot')


def _metadata(value):
    allowed = {'id', 'name', 'description', 'category', 'source_key', 'columns',
               'key_fields', 'tags', 'channels', 'depends_on', 'max_count_drop'}
    if (not isinstance(value, Mapping) or set(value) - allowed or
            not {'name', 'source_key', 'columns'}.issubset(value)):
        raise ValidationError('Unknown or missing report catalog fields.')
    columns = _fields(value['columns'])
    key_fields = _fields(value.get('key_fields', columns[:1]), maximum=8)
    if not set(key_fields).issubset(columns):
        raise ValidationError('Quality keys must reference registered columns.')
    channels = value.get('channels', list(CHANNELS))
    if (not isinstance(channels, list) or len(channels) > 3 or
            any(not isinstance(item, str) or item not in CHANNELS for item in channels) or
            len(set(channels)) != len(channels)):
        raise ValidationError('Only local export, API, and mock-mail channel metadata is supported.')
    tags = value.get('tags', [])
    if not isinstance(tags, list) or len(tags) > 12:
        raise ValidationError('At most twelve catalog tags are supported.')
    tags = [_text(item, 'tag', 40, required=True) for item in tags]
    if len({item.casefold() for item in tags}) != len(tags):
        raise ValidationError('Catalog tags must be distinct.')
    dependencies = value.get('depends_on', [])
    if not isinstance(dependencies, list) or len(dependencies) > 20:
        raise ValidationError('At most twenty local report dependencies are supported.')
    dependencies = [_key(item, 'report dependency') for item in dependencies]
    if len(set(dependencies)) != len(dependencies):
        raise ValidationError('Report dependencies must be distinct.')
    drop = value.get('max_count_drop', .25)
    if type(drop) not in (int, float) or not 0 <= drop <= 1 or not math.isfinite(drop):
        raise ValidationError('The count-drop threshold must be between zero and one.')
    return dict(id=_key(value.get('id', 'report-' + uuid.uuid4().hex)),
                name=_text(value['name'], 'name', 120, required=True),
                description=_text(value.get('description', ''), 'description', 500),
                category=_text(value.get('category', 'General'), 'category', 64, required=True),
                source_key=_key(value['source_key'], 'source'), columns=columns,
                key_fields=key_fields, tags=tags, channels=sorted(channels),
                depends_on=sorted(dependencies), max_count_drop=float(drop))


def _request(value):
    if isinstance(value, str):
        value = {'text': value}
    allowed = {'text', 'source_key', 'columns', 'filters', 'tags', 'category'}
    if not isinstance(value, Mapping) or set(value) - allowed:
        raise ValidationError('Only bounded catalog request fields are accepted.')
    result = {'text': _text(value.get('text', ''), 'request', 1000)}
    for name in ('source_key', 'category'):
        if name in value:
            result[name] = (_key(value[name], 'source') if name == 'source_key' else
                            _text(value[name], 'category', 64, required=True))
    if 'columns' in value:
        result['columns'] = _fields(value['columns'])
    if 'filters' in value:
        result['filters'] = _filters(value['filters'])
    if 'tags' in value:
        if not isinstance(value['tags'], list) or len(value['tags']) > 12:
            raise ValidationError('At most twelve request tags are supported.')
        result['tags'] = [_text(tag, 'tag', 40, required=True) for tag in value['tags']]
    return result


def _tokens(text):
    return set(re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]', text.casefold()))


def _numeric_measure(rows, field):
    values = [row[field] for row in rows if field in row and type(row[field]) in (int, float)]
    # 400 digits cover every accepted finite float plus the bounded row total.
    with localcontext() as context:
        context.prec = 400
        total = sum((Decimal(str(value)) for value in values), Decimal(0))
    return dict(total=total, numeric_count=len(values),
                missing_count=sum(field not in row for row in rows),
                nonnumeric_count=sum(field in row and type(row[field]) not in (int, float) for row in rows))


def _numeric_change(before, after, field):
    old, new = _numeric_measure(before, field), _numeric_measure(after, field)
    with localcontext() as context:
        context.prec = 400
        delta = new['total'] - old['total']
    def number(value):
        return int(value) if value == value.to_integral_value() else float(value)
    return dict(field=field, before=number(old['total']), after=number(new['total']), delta=number(delta),
                before_exact=format(old['total'], 'f'), after_exact=format(new['total'], 'f'), delta_exact=format(delta, 'f'),
                before_numeric_count=old['numeric_count'], after_numeric_count=new['numeric_count'],
                before_missing_count=old['missing_count'], after_missing_count=new['missing_count'],
                before_nonnumeric_count=old['nonnumeric_count'], after_nonnumeric_count=new['nonnumeric_count'])


def _arithmetic(before, after, report, changed_ids):
    numeric = [field for field in report['columns'] if
               any(field in row and type(row[field]) in (int, float) for row in before + after)]
    totals = [_numeric_change(before, after, field) for field in numeric]
    contributions, group_limits = [], []
    for field in report['key_fields']:
        # Group only available categorical keys, not measures or invented labels.
        if not any(field in row and isinstance(row[field], (str, bool)) for row in before + after):
            continue
        groups = {}
        for side, rows in (('before', before), ('after', after)):
            for row in rows:
                if field in row:
                    key = _dump(row[field])
                    group = groups.setdefault(key, dict(value=row[field], before=[], after=[]))
                    group[side].append(row)
        measured = []
        for key, group in groups.items():
            changes = [_numeric_change(group['before'], group['after'], numeric_field) for numeric_field in numeric]
            identifiers = sorted({row['record_id'] for row in group['before'] + group['after']} & changed_ids)
            item = dict(group_by=field, value=group['value'], before_count=len(group['before']),
                        after_count=len(group['after']), count_delta=len(group['after']) - len(group['before']),
                        numeric_changes=changes, source_record_ids=identifiers[:MAX_CONTRIBUTION_RECORD_IDS],
                        source_record_ids_truncated=len(identifiers) > MAX_CONTRIBUTION_RECORD_IDS)
            magnitude = sum(abs(Decimal(change['delta_exact'])) for change in changes)
            measured.append((magnitude, abs(item['count_delta']), key, item))
        measured.sort(key=lambda item: (-item[0], -item[1], item[2]))
        contributions.extend(item[3] for item in measured[:MAX_CONTRIBUTION_GROUPS])
        group_limits.append(dict(field=field, groups_total=len(groups),
                                 groups_shown=min(len(groups), MAX_CONTRIBUTION_GROUPS),
                                 truncated=len(groups) > MAX_CONTRIBUTION_GROUPS))
    return dict(numeric_totals=totals, group_contributions=contributions,
                group_limits=group_limits, arithmetic_only=True)


class OperationsService:
    """Separate local SQLite service. All read/write boundaries reload identity.

    Report metadata is shared only within the authenticated organization. Admins
    register reports, capture immutable versions, and run simulated sends. Users
    may inspect authorized organization catalog/lineage and aggregate usage.
    No caller can select SQL, a URL, credentials, a filesystem export destination,
    or an SMTP recipient outside the reserved example.invalid domain.
    """
    def __init__(self, database, identities):
        if not callable(getattr(identities, 'get_user', None)):
            raise ValueError('A current server-owned identity provider is required.')
        self.path = str(_path(database))
        self.identities, self.mail = identities, MailSink()
        self.available = True
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        with self._transaction(initialize=True) as connection:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version == 0:
                if connection.execute("SELECT 1 FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'").fetchone():
                    raise StateUnavailable('Unversioned operations storage is not empty.')
                for statement in _DDL:
                    connection.execute(statement)
                connection.execute('PRAGMA user_version=1')
            self._validate_schema(connection)

    def _auth(self, user, admin=False):
        try:
            supplied = validate_identity(user)
            current = validate_identity(self.identities.get_user(supplied['id']))
        except Exception:
            raise AccessDenied('A current valid identity is required.') from None
        if supplied != current or current['role'] not in (('admin',) if admin else ('admin', 'user')):
            raise AccessDenied('This operations action is not permitted.')
        return current

    @staticmethod
    def _validate_schema(connection):
        if connection.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
            raise StateUnavailable('Unsupported operations storage schema.')
        actual = {_ddl_tokens(row['sql']) for row in connection.execute(
            "SELECT sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'")}
        if actual != {_ddl_tokens(statement) for statement in _DDL}:
            raise StateUnavailable('Operations storage schema does not match its contract.')

    @contextmanager
    def _transaction(self, write=False, initialize=False):
        connection = None
        try:
            connection = sqlite3.connect(Path(self.path).as_uri() + '?mode=rw', uri=True,
                                         isolation_level=None, timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA synchronous=FULL')
            connection.execute('BEGIN IMMEDIATE' if write or initialize else 'BEGIN')
            if not initialize:
                self._validate_schema(connection)
            yield connection
            connection.commit()
        except (sqlite3.Error, OSError):
            if connection is not None:
                connection.rollback()
            raise StateUnavailable('Operations storage is unavailable.') from None
        except BaseException:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _report(connection, user, report_id):
        # All identifier probing, including malformed IDs, uses one safe response.
        if not isinstance(report_id, str) or not _KEY.fullmatch(report_id):
            raise NotFound('Report not found.')
        row = connection.execute('SELECT * FROM operations_reports WHERE org=? AND id=?',
                                 (user['org'], report_id)).fetchone()
        if row is None:
            raise NotFound('Report not found.')
        return dict(json.loads(row['metadata']), latest_version=row['latest_version'])

    @staticmethod
    def _version(connection, user, report_id, version):
        version = _integer(version)
        row = connection.execute('SELECT * FROM operations_versions WHERE org=? AND report_id=? AND version=?',
                                 (user['org'], report_id, version)).fetchone()
        if row is None:
            raise NotFound('Report version not found.')
        rows = json.loads(row['snapshot'])
        return dict(report_id=report_id, version=version, definition=json.loads(row['definition']),
                    rows=rows, source_record_ids=[item['record_id'] for item in rows],
                    created_at=row['created_at'])

    @classmethod
    def _catalog(cls, connection, user):
        records = connection.execute('SELECT id FROM operations_reports WHERE org=? ORDER BY id',
                                     (user['org'],)).fetchall()
        result = []
        for record in records:
            report = cls._report(connection, user, record['id'])
            report['row_count'] = (len(cls._version(connection, user, report['id'], report['latest_version'])['rows'])
                                   if report['latest_version'] else 0)
            result.append(report)
        return sorted(result, key=lambda item: (item['name'].casefold(), item['id']))

    @staticmethod
    def _increment(connection, user, report_id, event):
        # No actor or request payload ever enters the aggregate table.
        connection.execute('INSERT INTO operations_usage(org,report_id,event,count,last_event_at) VALUES(?,?,?,1,?) '
                           'ON CONFLICT(org,report_id,event) DO UPDATE SET count=count+1, '
                           'last_event_at=MAX(last_event_at,excluded.last_event_at)',
                           (user['org'], report_id, event, time.time()))

    def list_reports(self, user):
        user = self._auth(user)
        with self._transaction() as connection:
            return self._catalog(connection, user)

    def register_report(self, user, payload):
        user, metadata = self._auth(user, admin=True), _metadata(payload)
        with self._transaction(write=True) as connection:
            if connection.execute('SELECT 1 FROM operations_reports WHERE org=? AND id=?',
                                  (user['org'], metadata['id'])).fetchone():
                raise Conflict('This report catalog ID already exists.')
            if connection.execute('SELECT COUNT(*) FROM operations_reports WHERE org=?',
                                  (user['org'],)).fetchone()[0] >= MAX_REPORTS:
                raise ValidationError('The organization report limit has been reached.')
            for dependency in metadata['depends_on']:
                self._report(connection, user, dependency)
            connection.execute('INSERT INTO operations_reports VALUES(?,?,?,0)',
                               (user['org'], metadata['id'], _dump(metadata)))
            return dict(metadata, latest_version=0, row_count=0)

    def save_version(self, user, report_id, definition, rows, expected_version=None):
        user, rows = self._auth(user, admin=True), _rows(rows, unique=True)
        if expected_version is not None:
            expected_version = _integer(expected_version, minimum=0)
        with self._transaction(write=True) as connection:
            report = self._report(connection, user, report_id)
            latest = report['latest_version']
            if expected_version is not None and latest != expected_version:
                raise Conflict('The report changed. Reload before saving a version.')
            if latest >= MAX_VERSIONS:
                raise ValidationError('The report version limit has been reached.')
            definition = _definition(definition, report)
            connection.execute('INSERT INTO operations_versions VALUES(?,?,?,?,?,?)',
                               (user['org'], report_id, latest + 1, _dump(definition), _dump(rows), time.time()))
            connection.execute('UPDATE operations_reports SET latest_version=? WHERE org=? AND id=?',
                               (latest + 1, user['org'], report_id))
            return self._version(connection, user, report_id, latest + 1)

    def get_version(self, user, report_id, version):
        user = self._auth(user)
        with self._transaction(write=True) as connection:
            self._report(connection, user, report_id)
            result = self._version(connection, user, report_id, version)
            self._increment(connection, user, report_id, 'report_view')
            return result

    def source_record(self, user, report_id, version, record_id):
        user = self._auth(user)
        try:
            record_id = _record_key(record_id)
        except ValidationError:
            raise NotFound('Source record not found.') from None
        with self._transaction(write=True) as connection:
            self._report(connection, user, report_id)
            rows = self._version(connection, user, report_id, version)['rows']
            for row in rows:
                if row['record_id'] == record_id:
                    self._increment(connection, user, report_id, 'source_drilldown')
                    return dict(report_id=report_id, version=version, record_id=record_id, row=row)
            raise NotFound('Source record not found.')

    def version_diff(self, user, report_id, before_version, after_version):
        user = self._auth(user)
        with self._transaction(write=True) as connection:
            report = self._report(connection, user, report_id)
            before = self._version(connection, user, report_id, before_version)
            after = self._version(connection, user, report_id, after_version)
            self._increment(connection, user, report_id, 'version_diff')
            self._increment(connection, user, report_id, 'report_view')
        old = {row['record_id']: row for row in before['rows']}
        new = {row['record_id']: row for row in after['rows']}
        changed = []
        for identifier in sorted(old.keys() & new.keys()):
            fields = [field for field in sorted(old[identifier].keys() | new[identifier].keys())
                      if field != 'record_id' and
                      (field not in old[identifier] or field not in new[identifier] or
                       _dump(old[identifier][field]) != _dump(new[identifier][field]))]
            if fields:
                changed.append(dict(record_id=identifier, changed_fields=fields,
                                    before=old[identifier], after=new[identifier]))
        definitions = [dict(field=field, before=before['definition'].get(field), after=after['definition'].get(field))
                       for field in sorted(before['definition'].keys() | after['definition'].keys())
                       if before['definition'].get(field) != after['definition'].get(field)]
        added = [new[key] for key in sorted(new.keys() - old.keys())]
        removed = [old[key] for key in sorted(old.keys() - new.keys())]
        source_ids = sorted({row['record_id'] for row in added + removed + changed})
        arithmetic = _arithmetic(before['rows'], after['rows'], report, set(source_ids))
        return dict(report_id=report_id, before_version=before_version, after_version=after_version,
                    definition_changes=definitions, added=added, removed=removed, changed=changed,
                    source_record_ids=source_ids, **arithmetic,
                    summary=dict(added=len(added), removed=len(removed), changed=len(changed),
                                 definition_changes=len(definitions)))

    @classmethod
    def _graph(cls, connection, user):
        nodes, edges = {}, set()
        for report in cls._catalog(connection, user):
            source_id, report_node = 'source:' + report['source_key'], 'report:' + report['id']
            nodes[source_id] = dict(id=source_id, kind='source', resource_id=report['source_key'], label=report['source_key'])
            nodes[report_node] = dict(id=report_node, kind='report', resource_id=report['id'], label=report['name'])
            edges.add((source_id, report_node))
            definition = (cls._version(connection, user, report['id'], report['latest_version'])['definition']
                          if report['latest_version'] else {'columns': report['columns'], 'group_by': [], 'filters': []})
            fields = set(definition['columns'] + definition['group_by'] + report['key_fields'])
            fields.update(item['field'] for item in definition['filters'])
            for field in sorted(fields):
                field_id = 'field:' + report['source_key'] + ':' + field
                nodes[field_id] = dict(id=field_id, kind='field', resource_id=field,
                                       source_key=report['source_key'], label=field)
                edges.add((source_id, field_id))
                edges.add((field_id, report_node))
            for dependency in report['depends_on']:
                edges.add(('report:' + dependency, report_node))
            for channel in report['channels']:
                channel_id = channel + ':' + report['id']
                nodes[channel_id] = dict(id=channel_id, kind=channel, resource_id=report['id'],
                                         label=report['name'] + ' / ' + channel)
                edges.add((report_node, channel_id))
        return dict(nodes=[nodes[key] for key in sorted(nodes)],
                    edges=[dict(source=source, target=target) for source, target in sorted(edges)])

    def list_dependencies(self, user):
        user = self._auth(user)
        with self._transaction() as connection:
            return self._graph(connection, user)

    dependency_graph = list_dependencies

    def impact(self, user, kind, identifier, field=None):
        user = self._auth(user)
        if not isinstance(kind, str) or kind not in ('source', 'field', 'report') + CHANNELS:
            raise ValidationError('Select a supported dependency kind.')
        try:
            identifier = _key(identifier)
            if kind == 'source' and field is not None:
                kind = 'field'
            if kind == 'field':
                field = _key(field, 'field')
            elif field is not None:
                raise ValidationError('A field is used only with field dependency changes.')
        except ValidationError:
            raise ValidationError('Select a bounded dependency identifier.') from None
        with self._transaction() as connection:
            graph = self._graph(connection, user)
        nodes = {node['id']: node for node in graph['nodes']}
        origin = kind + ':' + identifier + (':' + field if kind == 'field' else '')
        if origin not in nodes:
            raise NotFound('Dependency not found.')
        adjacency = {}
        for edge in graph['edges']:
            adjacency.setdefault(edge['source'], []).append(edge['target'])
        depths, queue = {origin: 0}, deque([origin])
        while queue:
            node = queue.popleft()
            for child in adjacency.get(node, []):
                if child not in depths:
                    depths[child] = depths[node] + 1
                    queue.append(child)
        impacted = [dict(nodes[key], depth=depths[key]) for key in sorted(depths)
                    if key != origin and nodes[key]['kind'] in ('report',) + CHANNELS]
        groups = {group: [item for item in impacted if item['kind'] == group] for group in ('report',) + CHANNELS}
        return dict(origin=nodes[origin], impacted=impacted, groups=groups,
                    counts={key: len(value) for key, value in groups.items()},
                    graph=graph, preview_only=True)

    def export_impact(self, user, kind, identifier, field=None):
        result = self.impact(user, kind, identifier, field)
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(('kind', 'resource_id', 'label', 'depth'))
        for node in result['impacted']:
            label = node['label']
            if label.lstrip().startswith(('=', '+', '-', '@')):
                label = "'" + label
            writer.writerow((node['kind'], node['resource_id'], label, node['depth']))
        return output.getvalue()

    def search_catalog(self, user, request):
        user, request = self._auth(user), _request(request)
        with self._transaction(write=True) as connection:
            reports = self._catalog(connection, user)
            matches = []
            for report in reports:
                definition = (self._version(connection, user, report['id'], report['latest_version'])['definition']
                              if report['latest_version'] else _definition({}, report))
                wanted = _tokens(request['text'] + ' ' + ' '.join(request.get('tags', [])))
                actual = _tokens(' '.join([report['name'], report['description'], report['category']] + report['tags']))
                parts = []
                if wanted:
                    parts.append((.45, len(wanted & actual) / len(wanted | actual)))
                if 'source_key' in request:
                    parts.append((.25, float(request['source_key'] == report['source_key'])))
                if 'columns' in request:
                    parts.append((.20, len(set(request['columns']) & set(definition['columns'])) / len(request['columns'])))
                if 'category' in request:
                    parts.append((.10, float(request['category'].casefold() == report['category'].casefold())))
                score = round(sum(weight * value for weight, value in parts) / sum(weight for weight, value in parts), 6) if parts else 1.0
                source_ok = request.get('source_key', report['source_key']) == report['source_key']
                columns_ok = set(request.get('columns', definition['columns'])).issubset(definition['columns'])
                filters = request.get('filters', definition['filters'])
                exact = (source_ok and request.get('columns', definition['columns']) == definition['columns']
                         and filters == definition['filters'])
                # Filtering can add constraints, but cannot silently remove a saved constraint.
                filter_ok = (set(map(_dump, definition['filters'])).issubset(set(map(_dump, filters)))
                             and all(item['field'] in report['columns'] for item in filters))
                action = 'reuse' if exact else 'filter' if source_ok and columns_ok and filter_ok else 'clone'
                reasons = {'reuse': 'Existing source, columns, and filters satisfy the request.',
                           'filter': 'Reuse this report with narrower columns or additional filters.',
                           'clone': 'Create a separately reviewed definition for the requested changes.'}
                if action == 'reuse' and not {'source_key', 'columns', 'filters'}.intersection(request):
                    reasons['reuse'] = 'Inspect this catalog candidate for reuse; text similarity is advisory.'
                matches.append(dict(report_id=report['id'], name=report['name'], category=report['category'],
                                    source_key=report['source_key'], columns=definition['columns'],
                                    latest_version=report['latest_version'], score=score,
                                    suggestion=action, reason=reasons[action]))
            self._increment(connection, user, '', 'catalog_search')
            self._increment(connection, user, '', 'self_service')
        matches.sort(key=lambda item: (-item['score'], item['name'].casefold(), item['report_id']))
        return dict(matches=matches, total=len(matches), algorithm='deterministic-overlap-v1')

    @classmethod
    def _quality(cls, connection, user, report_id, rows, update_failed):
        report = cls._report(connection, user, report_id)
        baseline = len(cls._version(connection, user, report_id, report['latest_version'])['rows']) if report['latest_version'] else 0
        issues = []
        if update_failed:
            issues.append(dict(code='update_failed', count=1))
        missing = sorted({column for column in report['columns'] if
                          not rows or any(column not in row for row in rows)})
        if missing:
            issues.append(dict(code='missing_columns', fields=missing, count=len(missing)))
        drop = max(0.0, (baseline - len(rows)) / baseline) if baseline else 0.0
        if baseline and drop > report['max_count_drop']:
            issues.append(dict(code='count_drop', baseline_count=baseline, current_count=len(rows), ratio=round(drop, 6)))
        keys, duplicates = set(), 0
        for row in rows:
            if all(field in row for field in report['key_fields']):
                key = tuple(_dump(row[field]) for field in report['key_fields'])
                if key in keys:
                    duplicates += 1
                keys.add(key)
        if duplicates:
            issues.append(dict(code='duplicate_keys', fields=report['key_fields'], count=duplicates))
        return dict(report_id=report_id, passed=not issues, status='passed' if not issues else 'blocked',
                    issues=issues, row_count=len(rows), baseline_count=baseline,
                    count_drop_ratio=round(drop, 6), max_count_drop=report['max_count_drop'])

    def check_quality(self, user, report_id, rows, update_failed=False):
        user = self._auth(user)
        if type(update_failed) is not bool:
            raise ValidationError('update_failed must be a boolean.')
        rows = _rows(rows)
        with self._transaction() as connection:
            return self._quality(connection, user, report_id, rows, update_failed)

    def simulate_send(self, user, report_id, rows, recipients, update_failed=False):
        user = self._auth(user, admin=True)
        if type(update_failed) is not bool:
            raise ValidationError('update_failed must be a boolean.')
        if (not isinstance(recipients, list) or not 1 <= len(recipients) <= 20 or
                any(not isinstance(item, str) or not _RECIPIENT.fullmatch(item) for item in recipients) or
                len({item.casefold() for item in recipients}) != len(recipients)):
            raise ValidationError('Use 1–20 distinct synthetic example.invalid recipients.')
        rows = _rows(rows)
        recipients = list(recipients)
        with self._transaction(write=True) as connection:
            if 'mail' not in self._report(connection, user, report_id)['channels']:
                raise AccessDenied('Mock mail is not configured for this report.')
            quality = self._quality(connection, user, report_id, rows, update_failed)
            self._auth(user, admin=True)
            status, identifier = ('simulated' if quality['passed'] else 'blocked'), uuid.uuid4().hex
            connection.execute('INSERT INTO operations_simulations VALUES(?,?,?,?,?,?,?,?)',
                               (user['org'], identifier, report_id, status,
                                _dump([item['code'] for item in quality['issues']]), len(rows), len(recipients), time.time()))
            self._increment(connection, user, report_id, 'mock_send' if quality['passed'] else 'quality_block')
            if quality['passed']:
                # The fixed in-memory sink shares this local transaction with
                # its record. Revocation must not leave a false simulated send.
                self._auth(user, admin=True)
                self.mail.send('Synthetic reporting quality-gated simulation',
                               '{} synthetic rows passed the local gate.'.format(len(rows)), recipients)
        return dict(id=identifier, report_id=report_id, status=status, quality=quality,
                    recipient_count=len(recipients), real_delivery=False)

    def list_simulations(self, user, report_id, limit=50):
        user = self._auth(user)
        limit = _integer(limit, maximum=100)
        with self._transaction() as connection:
            self._report(connection, user, report_id)
            rows = connection.execute('SELECT * FROM operations_simulations WHERE org=? AND report_id=? '
                                      'ORDER BY created_at DESC,id LIMIT ?', (user['org'], report_id, limit)).fetchall()
            return [dict(id=row['id'], report_id=row['report_id'], status=row['status'],
                         issue_codes=json.loads(row['issue_codes']), row_count=row['row_count'],
                         recipient_count=row['recipient_count'], created_at=row['created_at'], real_delivery=False)
                    for row in rows]

    def record_usage(self, user, report_id, event):
        user = self._auth(user)
        if not isinstance(event, str) or event not in USAGE_EVENTS:
            raise ValidationError('Select a documented aggregate usage event.')
        with self._transaction(write=True) as connection:
            self._report(connection, user, report_id)
            self._increment(connection, user, report_id, event)
        return dict(recorded=True, event=event)

    def usage_summary(self, user):
        user = self._auth(user)
        with self._transaction() as connection:
            reports = self._catalog(connection, user)
            rows = connection.execute('SELECT report_id,event,count,last_event_at FROM operations_usage WHERE org=? '
                                      'ORDER BY report_id,event', (user['org'],)).fetchall()
        totals = {event: 0 for event in USAGE_EVENTS + _INTERNAL_EVENTS}
        last = {event: None for event in totals}
        metrics = {report['id']: dict(report_id=report['id'], name=report['name'],
                                     counts=dict(totals), last_event_at=dict(last), last_view_at=None,
                                     view_observation='unobserved', view_observation_label='未觀測') for report in reports}
        for row in rows:
            totals[row['event']] += row['count']
            last[row['event']] = max(last[row['event']] or 0, row['last_event_at'])
            if row['report_id'] in metrics:
                metrics[row['report_id']]['counts'][row['event']] = row['count']
                metrics[row['report_id']]['last_event_at'][row['event']] = row['last_event_at']
                if row['event'] == 'report_view':
                    metrics[row['report_id']].update(last_view_at=row['last_event_at'],
                                                     view_observation='observed', view_observation_label='已觀測')
        return dict(totals=totals, reports=[metrics[report['id']] for report in reports],
                    last_event_at=last, as_of=time.time(),
                    privacy='Organization-level counters only; no individual tracking.')

    def quality_fixture(self, user, report_id, scenario='clean'):
        user = self._auth(user)
        if scenario not in ('clean', 'bad') or not isinstance(scenario, str):
            raise ValidationError('Select the clean or bad synthetic fixture.')
        # A fixture never fetches real source data or modifies an immutable version.
        with self._transaction() as connection:
            report = self._report(connection, user, report_id)
            if report_id != DEMO_REPORT_ID or getattr(self.identities, 'is_demo', False) is not True:
                raise AccessDenied('Synthetic fixture controls are unavailable for this report.')
            version = self._version(connection, user, report_id, report['latest_version'])
            if version['definition'].get('provenance') != 'synthetic-fixture':
                raise AccessDenied('This version is not an explicit synthetic fixture.')
            rows = version['rows']
        if scenario == 'bad':
            rows = rows[:2]
            if len(rows) == 2:
                for field in report['key_fields']:
                    rows[1][field] = rows[0].get(field)
            if rows:
                rows[0].pop(report['columns'][-1], None)
        return dict(rows=rows, update_failed=scenario == 'bad', scenario=scenario)

    def seed_demo(self, user):
        """Explicit, idempotent synthetic fixtures; never enabled in production."""
        user = self._auth(user, admin=True)
        if getattr(self.identities, 'is_demo', False) is not True:
            raise AccessDenied('Synthetic seeding requires an explicit demo identity provider.')
        first = [dict(record_id='monthly-row-{:03d}'.format(index), period='2026-{:02d}'.format((index + 1) // 2),
                      department='Demo Sales' if index % 2 else 'Demo Operations',
                      revenue=100000 + index * 1000, cost=60000 + index * 500, profit=40000 + index * 500)
                 for index in range(1, 7)]
        second = [dict(row) for row in first if row['record_id'] != 'monthly-row-003']
        second[1]['revenue'] += 2500
        second[1]['profit'] += 2500
        second.append(dict(record_id='monthly-row-007', period='2026-04', department='Demo Sales',
                           revenue=107000, cost=63500, profit=43500))
        fixtures = [dict(id=DEMO_REPORT_ID, name='Monthly performance / 月度績效',
                         description='Revenue, cost and profit with source-record lineage.', category='Performance',
                         source_key='synthetic-monthly', columns=['period', 'department', 'revenue', 'cost', 'profit'],
                         key_fields=['period', 'department'], tags=['monthly', 'finance', '績效']),
                    dict(id='department-cost', name='Department cost / 部門成本',
                         description='Monthly department costs for reusable self-service reporting.', category='Cost',
                         source_key='synthetic-monthly', columns=['period', 'department', 'cost'],
                         key_fields=['period', 'department'], tags=['cost', 'monthly', '成本'], channels=['export', 'api']),
                    dict(id='executive-summary', name='Executive summary / 管理摘要',
                         description='Derived management report depending on monthly performance.', category='Performance',
                         source_key='synthetic-monthly', columns=['period', 'department', 'profit'],
                         key_fields=['period', 'department'], tags=['summary', 'profit'], channels=['export', 'mail'],
                         depends_on=[DEMO_REPORT_ID]),
                    dict(id='qsl-workspace', name='QSL workspace / 供應商維護',
                         description='Existing synthetic QSL report wizard; actual preview/save counters.', category='Maintenance',
                         source_key='synthetic-qsl',
                         columns=['Material_Type', 'Vendor_Code', 'Vendor_Name', 'Country', 'City', 'Rev', 'Supplier_Level'],
                         key_fields=['Material_Type', 'Vendor_Code', 'Vendor_Name', 'Country', 'City'],
                         tags=['QSL', 'supplier', '供應商', '材料'], channels=['export', 'api'])]
        with self._transaction(write=True) as connection:
            for fixture in fixtures:
                if connection.execute('SELECT 1 FROM operations_reports WHERE org=? AND id=?',
                                      (user['org'], fixture['id'])).fetchone():
                    continue
                metadata = _metadata(fixture)
                if connection.execute('SELECT COUNT(*) FROM operations_reports WHERE org=?',
                                      (user['org'],)).fetchone()[0] >= MAX_REPORTS:
                    raise ValidationError('The organization report limit has been reached.')
                for dependency in metadata['depends_on']:
                    self._report(connection, user, dependency)
                connection.execute('INSERT INTO operations_reports VALUES(?,?,?,0)',
                                   (user['org'], metadata['id'], _dump(metadata)))
                snapshots = (() if metadata['id'] == 'qsl-workspace' else
                             (first, second) if metadata['id'] == DEMO_REPORT_ID else (second,))
                for version, snapshot in enumerate(snapshots, 1):
                    definition = _definition({}, metadata)
                    definition['provenance'] = 'synthetic-fixture'
                    if version == 2:
                        definition['limit'] = 200
                    rows = [{key: value for key, value in row.items() if key == 'record_id' or key in metadata['columns']}
                            for row in snapshot]
                    connection.execute('INSERT INTO operations_versions VALUES(?,?,?,?,?,?)',
                                       (user['org'], metadata['id'], version, _dump(definition), _dump(rows), time.time()))
                connection.execute('UPDATE operations_reports SET latest_version=? WHERE org=? AND id=?',
                                   (len(snapshots), user['org'], metadata['id']))
            return dict(report_id=DEMO_REPORT_ID, reports=self._catalog(connection, user), synthetic=True)
