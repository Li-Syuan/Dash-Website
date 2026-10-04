"""Bounded, configuration-only reports over the authorized synthetic QSL service.

Callers provide a server-resolved identity and a server-owned policy factory.
Definitions contain no identities, permissions, SQL, Python, paths or adapters.
Saved definitions belong to exactly one identity in one organization, including
for administrators. SQLite is local-host storage; no company adapter is opened.
"""
import json
import sqlite3
import threading
from contextlib import contextmanager

from .legacy_crud import (
    LegacyCrudService, QSL_FIELDS, InvalidInput, PermissionDenied,
    RecordConflict, RecordNotFound, StorageUnavailable,
)
from .legacy_policy import authorize, identity_id, identity_text


SCHEMA_VERSION = 1
SOURCE_ID = 'synthetic-qsl'
MAX_PREVIEW_ROWS = 500
MAX_SOURCE_ROWS = 5000
MAX_FILTERS = 8
MAX_GROUP_FIELDS = 3
MAX_SAVED_DEFINITIONS = 100
_DEFINITION_KEYS = frozenset((
    'schema_version', 'source', 'columns', 'filters', 'group_by', 'aggregate', 'limit',
))
_FILTER_KEYS = frozenset(('field', 'op', 'value'))


def _integer(value, maximum, minimum=1):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise InvalidInput('Invalid report bounds.')
    return value


def _text(value, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise InvalidInput('Invalid report text.')
    if required and (not value.strip() or value != value.strip()):
        raise InvalidInput('Invalid report text.')
    return value


def _field_list(value, maximum, allow_empty=False):
    if (not isinstance(value, list) or len(value) > maximum or
            (not allow_empty and not value) or
            any(not isinstance(field, str) or field not in QSL_FIELDS for field in value) or
            len(set(value)) != len(value)):
        raise InvalidInput('Invalid report fields.')
    return list(value)


def _normalize(definition):
    if (not isinstance(definition, dict) or not set(definition).issubset(_DEFINITION_KEYS) or
            not {'schema_version', 'source', 'columns'}.issubset(definition)):
        raise InvalidInput('Unknown or missing report settings.')
    if (type(definition['schema_version']) is not int or definition['schema_version'] != SCHEMA_VERSION or
            definition['source'] != SOURCE_ID):
        raise InvalidInput('Unsupported report schema or source.')
    aggregate = definition.get('aggregate')
    if aggregate is not None and aggregate != 'count':
        raise InvalidInput('Unsupported report aggregation.')
    group_by = _field_list(definition.get('group_by', []), MAX_GROUP_FIELDS, True)
    columns = _field_list(definition['columns'], len(QSL_FIELDS), aggregate == 'count')
    if (aggregate is None and group_by) or (aggregate == 'count' and columns != group_by):
        raise InvalidInput('Count reports must select exactly the grouping fields.')
    supplied_filters = definition.get('filters', [])
    if not isinstance(supplied_filters, list) or len(supplied_filters) > MAX_FILTERS:
        raise InvalidInput('Invalid report filters.')
    filters = []
    for item in supplied_filters:
        if (not isinstance(item, dict) or set(item) != _FILTER_KEYS or
                not isinstance(item['field'], str) or item['field'] not in QSL_FIELDS or
                item['op'] not in ('eq', 'contains')):
            raise InvalidInput('Invalid report filter.')
        filters.append({'field': item['field'], 'op': item['op'],
                        'value': _text(item['value'], 200)})
    return {
        'schema_version': SCHEMA_VERSION, 'source': SOURCE_ID, 'columns': columns,
        'filters': filters, 'group_by': group_by, 'aggregate': aggregate,
        'limit': _integer(definition.get('limit', 100), MAX_PREVIEW_ROWS),
    }


class ReportBuilderService:
    """Validated preview and owner-scoped optimistic definition persistence.

    ``sources(user)`` returns authorized source metadata. ``validate(user, d)``
    returns a detached normalized definition. ``preview(user, d)`` returns
    definition, columns, rows, matched_rows, source_rows and truncated.

    ``save(user, name, d, expected_version=None)`` creates a new owner/name.
    A positive expected_version updates that exact owner/name, never upserts.
    It returns id/name/version/definition; load has the same shape and list
    returns only id/name/version. Duplicate creation and stale writes conflict.
    Names identify saves, so renaming is a separate new definition. No delete or
    export permission is implied by this service.
    """
    def __init__(self, database, qsl_service, policy_factory):
        if not callable(policy_factory):
            raise TypeError('A server policy factory is required.')
        if not isinstance(qsl_service, LegacyCrudService) or qsl_service.target != SOURCE_ID:
            raise InvalidInput('Only the secure synthetic QSL source is supported.')
        self._qsl = qsl_service
        self._policy_factory = policy_factory
        self._lock = threading.RLock()
        try:
            self._db = sqlite3.connect(database, timeout=10, check_same_thread=False,
                                       isolation_level=None)
            self._db.row_factory = sqlite3.Row
            with self._transaction():
                self._db.execute('''CREATE TABLE IF NOT EXISTS report_builder_schema (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL)''')
                rows = self._db.execute('SELECT singleton,version FROM report_builder_schema').fetchall()
                if not rows:
                    self._db.execute('INSERT INTO report_builder_schema VALUES(1,?)', (SCHEMA_VERSION,))
                elif len(rows) != 1 or tuple(rows[0]) != (1, SCHEMA_VERSION):
                    raise StorageUnavailable('Unsupported report definition storage.')
                self._db.execute('''CREATE TABLE IF NOT EXISTS report_builder_definitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id TEXT NOT NULL, owner_org TEXT NOT NULL,
                    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120),
                    version INTEGER NOT NULL CHECK(version>=1),
                    definition TEXT NOT NULL,
                    UNIQUE(owner_id,owner_org,name))''')
        except Exception as exc:
            connection = getattr(self, '_db', None)
            if connection is not None:
                connection.close()
            if isinstance(exc, sqlite3.Error):
                raise StorageUnavailable('Report definition storage unavailable.') from None
            raise

    def close(self):
        with self._lock:
            self._db.close()

    @contextmanager
    def _transaction(self):
        with self._lock:
            try:
                self._db.execute('BEGIN IMMEDIATE')
                yield
                self._db.execute('COMMIT')
            except Exception as exc:
                if self._db.in_transaction:
                    self._db.execute('ROLLBACK')
                if isinstance(exc, sqlite3.Error):
                    raise StorageUnavailable('Report definition storage unavailable.') from None
                raise

    def _authorize(self, user, action):
        if action not in ('read', 'crud'):
            raise PermissionDenied('Report operation denied.')
        try:
            allowed = authorize(user, self._policy_factory(user), action)
            if not allowed:
                raise ValueError()
            owner_id = identity_id(getattr(user, 'id', None))
            owner_org = identity_text(getattr(user, 'orgcode', None))
        except Exception:
            raise PermissionDenied('Report operation denied.') from None
        return owner_id, owner_org

    def _query_source(self, user, **kwargs):
        # A later server reconfiguration must not redirect this fixed source.
        if self._qsl.target != SOURCE_ID:
            raise PermissionDenied('Report source unavailable.')
        try:
            return self._qsl.query(user, **kwargs)
        except sqlite3.Error:
            raise StorageUnavailable('Report source unavailable.') from None

    def _authorize_source(self, user):
        # Use the public secure boundary; never inspect QSL storage directly.
        self._query_source(user, limit=1)

    def sources(self, user):
        self._authorize(user, 'read')
        self._authorize_source(user)
        self._authorize(user, 'read')
        return [{'id': SOURCE_ID, 'label': 'Synthetic QSL', 'columns': list(QSL_FIELDS),
                 'filter_ops': ['eq', 'contains'], 'schema_version': SCHEMA_VERSION,
                 'max_rows': MAX_PREVIEW_ROWS, 'max_source_rows': MAX_SOURCE_ROWS}]

    def validate(self, user, definition):
        self._authorize(user, 'read')
        normalized = _normalize(definition)
        self._authorize_source(user)
        self._authorize(user, 'read')
        return normalized

    def preview(self, user, definition):
        owner = self._authorize(user, 'read')
        normalized = _normalize(definition)
        # Every pushed filter is only a necessary literal-substring condition.
        # Repeated field filters still all run below, with AND semantics.
        pushed = {}
        for item in normalized['filters']:
            field, value = item['field'], item['value']
            if field not in pushed or len(value) > len(pushed[field]):
                pushed[field] = value
        scan_limit = min(MAX_SOURCE_ROWS, self._qsl.max_export_rows)
        rows = self._query_source(user, filters=pushed, limit=scan_limit)
        if self._query_source(user, filters=pushed, limit=1, offset=scan_limit):
            raise InvalidInput('Source row limit exceeded; narrow the report filters.')
        if not isinstance(rows, list) or len(rows) > scan_limit:
            raise StorageUnavailable('Report source unavailable.')
        matched = []
        for row in rows:
            if (not isinstance(row, dict) or any(field not in row or not isinstance(row[field], str)
                                                or len(row[field]) > 255 for field in QSL_FIELDS)):
                raise StorageUnavailable('Report source unavailable.')
            if all((row[item['field']] == item['value'] if item['op'] == 'eq'
                    else item['value'] in row[item['field']]) for item in normalized['filters']):
                matched.append(row)
        columns = list(normalized['columns'])
        if normalized['aggregate'] == 'count':
            groups = {}
            for row in matched:
                key = tuple(row[field] for field in normalized['group_by'])
                groups[key] = groups.get(key, 0) + 1
            if not normalized['group_by'] and not groups:
                groups[()] = 0
            output = []
            for key in sorted(groups):
                group = dict(zip(normalized['group_by'], key))
                group['count'] = groups[key]
                output.append(group)
            columns.append('count')
        else:
            output = [{field: row[field] for field in columns} for row in matched]
        # Re-resolve both policies before releasing results, including if the
        # underlying provider changed authorization during the bounded read.
        self._authorize_source(user)
        if self._authorize(user, 'read') != owner:
            raise PermissionDenied('Report operation denied.')
        return {'definition': normalized, 'columns': columns,
                'rows': output[:normalized['limit']], 'matched_rows': len(matched),
                'source_rows': len(rows), 'truncated': len(output) > normalized['limit']}

    def save(self, user, name, definition, expected_version=None):
        owner = self._authorize(user, 'crud')
        name = _text(name, 120, True)
        normalized = _normalize(definition)
        if expected_version is not None:
            _integer(expected_version, 9223372036854775806)
        self._authorize_source(user)
        encoded = json.dumps(normalized, ensure_ascii=False, separators=(',', ':'))
        with self._transaction():
            if self._authorize(user, 'crud') != owner:
                raise PermissionDenied('Report operation denied.')
            existing = self._db.execute('''SELECT id,version FROM report_builder_definitions
                WHERE owner_id=? AND owner_org=? AND name=?''', owner + (name,)).fetchone()
            if expected_version is None:
                if existing is not None:
                    raise RecordConflict('A report with this name already exists.')
                count = self._db.execute('''SELECT COUNT(*) FROM report_builder_definitions
                    WHERE owner_id=? AND owner_org=?''', owner).fetchone()[0]
                if count >= MAX_SAVED_DEFINITIONS:
                    raise InvalidInput('Saved report limit reached.')
                cursor = self._db.execute('''INSERT INTO report_builder_definitions
                    (owner_id,owner_org,name,version,definition) VALUES(?,?,?,1,?)''', owner + (name, encoded))
                record_id, version = cursor.lastrowid, 1
            else:
                if existing is None:
                    raise RecordNotFound('Saved report unavailable.')
                if existing['version'] != expected_version:
                    raise RecordConflict('Saved report version changed; reload before saving.')
                cursor = self._db.execute('''UPDATE report_builder_definitions SET definition=?,version=version+1
                    WHERE id=? AND owner_id=? AND owner_org=? AND version=?''',
                    (encoded, existing['id']) + owner + (expected_version,))
                if cursor.rowcount != 1:
                    raise RecordConflict('Saved report version changed; reload before saving.')
                record_id, version = existing['id'], expected_version + 1
        return {'id': record_id, 'name': name, 'version': version, 'definition': normalized}

    def list_definitions(self, user):
        owner = self._authorize(user, 'read')
        self._authorize_source(user)
        with self._transaction():
            if self._authorize(user, 'read') != owner:
                raise PermissionDenied('Report operation denied.')
            rows = self._db.execute('''SELECT id,name,version FROM report_builder_definitions
                WHERE owner_id=? AND owner_org=? ORDER BY name,id LIMIT ?''',
                owner + (MAX_SAVED_DEFINITIONS,)).fetchall()
        return [dict(row) for row in rows]

    def load(self, user, record_id):
        owner = self._authorize(user, 'read')
        _integer(record_id, 9223372036854775807)
        self._authorize_source(user)
        with self._transaction():
            if self._authorize(user, 'read') != owner:
                raise PermissionDenied('Report operation denied.')
            row = self._db.execute('''SELECT id,name,version,definition FROM report_builder_definitions
                WHERE id=? AND owner_id=? AND owner_org=?''', (record_id,) + owner).fetchone()
            if row is None:
                raise RecordNotFound('Saved report unavailable.')
            result = dict(row)
            try:
                if len(result['definition']) > 12000:
                    raise ValueError()
                result['definition'] = _normalize(json.loads(result['definition']))
            except (InvalidInput, ValueError, TypeError):
                raise StorageUnavailable('Saved report definition unavailable.') from None
        return result
