"""Tenant-local admin metadata and deny-by-default governed report access.

Identity arguments are server-owned provider claims, never browser payloads.
No expression, query, import path, SMTP setting or executable job is accepted.
Manual simulations use the local synthetic adapter and an in-memory sink only.
"""
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import csv
import io
import json
import math
import re
import sqlite3
import time
import uuid

from demo_services import AccessDenied, MailSink
from .crud import (Conflict, NotFound, StateUnavailable, ValidationError,
                   _create_key, _record_id, _request_id, _text, _version)
from .errors import ProviderUnavailable
from .providers import validate_identity
from .authorization import current_identity
from .state import StateError


MAX_REPORTS = 200
MAX_GRANTS = 64
MAX_SCHEDULES = 50
ACTIONS = ('view', 'export', 'maintain')
COLUMNS = ['period', 'department', 'revenue', 'cost', 'profit']
_ZONES = {'UTC': timezone.utc, 'Asia/Taipei': timezone(timedelta(hours=8), 'Asia/Taipei')}
_SOURCE = re.compile(r'[a-z][a-z0-9_-]{0,63}\Z')
_TIME = re.compile(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z')
_RECIPIENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,63}@example\.invalid\Z')
_REPORT_FIELDS = frozenset(('name', 'category', 'description', 'enabled'))
_SCHEDULE_FIELDS = frozenset(('recipients', 'cadence', 'time', 'timezone', 'weekday', 'enabled'))
_AUDIT_FIELDS = _REPORT_FIELDS | _SCHEDULE_FIELDS | frozenset(('source_key', 'archived_at', 'permissions', 'status'))


def _user(value, admin=False):
    try:
        user = validate_identity(value)
    except ValueError:
        raise AccessDenied('A valid identity is required.') from None
    if user['role'] not in (('admin',) if admin else ('admin', 'user')):
        raise AccessDenied('This action is not permitted.')
    return user


def _bool(value, name):
    if type(value) is not bool:
        raise ValidationError('{} must be a boolean.'.format(name))
    return value


def _report_payload(value, create=False):
    allowed = _REPORT_FIELDS | ({'source_key'} if create else set())
    if not isinstance(value, Mapping) or not value or set(value) - allowed:
        raise ValidationError('Only documented report fields are editable.')
    if create and not {'name', 'source_key'}.issubset(value):
        raise ValidationError('A report name and supported source are required.')
    result = dict(category='General', description='', enabled=True) if create else {}
    for name, item in value.items():
        if name == 'enabled':
            result[name] = _bool(item, name)
        else:
            maximum = {'name': 120, 'category': 64, 'description': 240, 'source_key': 64}[name]
            result[name] = _text(item, name, maximum, required=name != 'description')
    if create and not _SOURCE.fullmatch(result['source_key']):
        raise ValidationError('Select a supported report source.')
    return result


def _schedule_payload(value, create=False):
    if not isinstance(value, Mapping) or not value or set(value) - _SCHEDULE_FIELDS:
        raise ValidationError('Only documented schedule fields are editable.')
    if create and 'recipients' not in value:
        raise ValidationError('Synthetic recipients are required.')
    result = dict(cadence='daily', time='09:00', timezone='UTC', weekday=0, enabled=False) if create else {}
    for name, item in value.items():
        if name == 'recipients':
            if (not isinstance(item, list) or not 1 <= len(item) <= 20 or
                    any(not isinstance(address, str) or not _RECIPIENT.fullmatch(address) for address in item)):
                raise ValidationError('Use 1–20 synthetic example.invalid recipients.')
            if len({address.casefold() for address in item}) != len(item):
                raise ValidationError('Recipients must be distinct.')
            result[name] = list(item)
        elif name == 'enabled':
            result[name] = _bool(item, name)
        elif name == 'weekday':
            if type(item) is not int or not 0 <= item <= 6:
                raise ValidationError('Select a weekday from Monday to Sunday.')
            result[name] = item
        elif name == 'time':
            if not isinstance(item, str) or not _TIME.fullmatch(item):
                raise ValidationError('Time must be HH:MM in the selected timezone.')
            result[name] = item
        elif name == 'timezone':
            if not isinstance(item, str) or item not in _ZONES:
                raise ValidationError('Select UTC or Asia/Taipei.')
            result[name] = item
        elif name == 'cadence':
            if item not in ('daily', 'weekly'):
                raise ValidationError('Select daily or weekly.')
            result[name] = item
    return result


def next_candidate(schedule, now=None):
    """Preview only, never a promise of execution; supported zones have no DST."""
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValidationError('An aware timestamp is required.')
    local = now.astimezone(_ZONES[schedule['timezone']])
    hour, minute = map(int, schedule['time'].split(':'))
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if schedule['cadence'] == 'weekly':
        candidate += timedelta(days=(schedule['weekday'] - candidate.weekday()) % 7)
        if candidate <= local:
            candidate += timedelta(days=7)
    elif candidate <= local:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc).isoformat()


class SyntheticManagedProvider:
    """Independent public fixture; never elevates a granted user to admin."""
    is_demo = True
    managed_sources = ('synthetic-monthly',)
    columns = COLUMNS

    def managed_rows(self, user, source_key):
        _user(user)
        if source_key not in self.managed_sources:
            raise AccessDenied('Unsupported source.')
        return [dict(period='2026-{:02d}'.format(month), department=department,
                     revenue=100000 + month * 2500, cost=60000 + month * 1400,
                     profit=40000 + month * 1100)
                for month in range(1, 7) for department in ('Demo Sales', 'Demo Operations')]


@dataclass(frozen=True)
class CatalogEntry:
    page_id: str
    path: str
    title: str
    catalog_category: str
    catalog_description: str
    catalog_tags: tuple
    nav_label: object = None


class CombinedCatalog:
    """Read-only catalog projection, not a route or callback registration."""
    def __init__(self, registry, managed):
        self.registry, self.managed = registry, managed

    def catalog(self, user):
        original = self.registry.catalog(user)
        if not self.managed.available or not user or user.get('role') not in ('admin', 'user'):
            return original
        reports = self.managed.list_reports(user)
        return original + tuple(CatalogEntry(
            page_id='managed.' + row['id'], path='/reports?report=' + row['id'],
            title=row['name'], catalog_category=row['category'],
            catalog_description=row['description'] or 'Admin-maintained report with explicit access permissions.',
            catalog_tags=('synthetic' if self.managed.is_demo else 'configured', 'managed'),
        ) for row in reports)


class ManagedReports:
    """Governed metadata, ACL, schedule configuration and mock-run service.

    Admin claims come only from the identity provider, apply to their own org,
    and cannot be changed here. Positive grants are additive, not deny rules.
    In production, only an explicit provider with managed_sources and
    managed_rows(user, source_key) is supported. Legacy rows() is never used.
    """
    columns = COLUMNS

    def __init__(self, store, identities, provider=None, is_demo=True):
        self.store, self.identities, self.is_demo = store, identities, is_demo
        self.available = store is not None
        self.provider = SyntheticManagedProvider() if is_demo else provider
        self.mail = MailSink()
        keys = getattr(self.provider, 'managed_sources', ())
        if (not isinstance(keys, (tuple, list)) or len(keys) > 50
                or any(not isinstance(key, str) or not _SOURCE.fullmatch(key) for key in keys)
                or len(set(keys)) != len(keys)
                or not callable(getattr(self.provider, 'managed_rows', None))
                or (not is_demo and getattr(self.provider, 'is_demo', True) is not False)):
            keys = ()
        self._sources = tuple(keys)

    def _identity(self, user, admin=False):
        return _user(current_identity(self.identities, user), admin=admin)

    def sources(self):
        return [{'label': self.source_label(key), 'value': key} for key in self._sources]

    def source_label(self, key):
        if key == 'synthetic-monthly' and self.is_demo:
            return 'Synthetic monthly performance'
        return 'Configured source: ' + key if key in self._sources else 'Unavailable source'

    @contextmanager
    def _transaction(self, write=False, user=None):
        if self.store is None:
            raise StateUnavailable('Configured local storage is required.')
        try:
            with self.store._transaction(write=write) as connection:
                if user is not None:
                    current_identity(self.identities, user)
                yield connection
                if user is not None:
                    current_identity(self.identities, user)
        except (StateError, sqlite3.Error, OSError):
            raise StateUnavailable('Administration storage is unavailable.') from None

    @staticmethod
    def _report(connection, org, identifier):
        row = connection.execute('SELECT * FROM managed_reports WHERE org = ? AND id = ?',
                                 (org, _record_id(identifier))).fetchone()
        if row is None:
            raise NotFound('Report not found.')
        return row

    @staticmethod
    def _schedule(connection, org, identifier):
        row = connection.execute('SELECT * FROM mail_schedules WHERE org = ? AND id = ?',
                                 (org, _record_id(identifier))).fetchone()
        if row is None:
            raise NotFound('Schedule not found.')
        return row

    @staticmethod
    def _record(row):
        value = dict(row)
        value.pop('create_key', None)
        value['enabled'] = bool(value['enabled'])
        return value

    @classmethod
    def _schedule_record(cls, row):
        value = cls._record(row)
        value['time'] = value.pop('send_time')
        value['recipients'] = json.loads(value['recipients'])
        value['engine_status'] = 'not_started'
        value['next_run_at_utc'] = next_candidate(value) if value['enabled'] else None
        return value

    @staticmethod
    def _check_version(row, expected):
        if row['version'] != _version(expected) or row['version'] >= 9223372036854775807:
            raise Conflict('Settings changed. Reload before trying again.')

    @staticmethod
    def _audit(connection, user, report_id, resource_id, action, fields, before=None, after=None, request_id=None):
        if any(field not in _AUDIT_FIELDS for field in fields):
            raise ValueError('Audit fields must use the fixed field allowlist')
        connection.execute(
            'INSERT INTO admin_changes (org, report_id, resource_id, actor_id, action, changed_fields, '
            'before_version, after_version, occurred_at, request_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (user['org'], report_id, resource_id, user['id'], action, json.dumps(sorted(fields)),
             before, after, time.time(), _request_id(request_id)))

    @staticmethod
    def _can(connection, user, row, action):
        if action not in ACTIONS or row['org'] != user['org']:
            return False
        if user['role'] == 'admin':
            return True
        rows = connection.execute(
            'SELECT can_view, can_export, can_maintain FROM report_grants WHERE org = ? AND report_id = ? '
            'AND ((kind = ? AND subject = ?) OR (kind = ? AND subject = ?) OR (kind = ? AND subject = ?))',
            (user['org'], row['id'], 'user', user['id'], 'role', user['role'], 'org', user['org'])).fetchall()
        return any(item['can_view'] for item in rows) and any(item['can_' + action] for item in rows)

    def _require(self, connection, user, row, action, data=True):
        if (row['archived_at'] is not None or (data and (not row['enabled'] or row['source_key'] not in self._sources))
                or not self._can(connection, user, row, action)):
            raise AccessDenied('This report action is not permitted.')

    def permissions(self, user, identifier):
        user = self._identity(user)
        with self._transaction(user=user) as connection:
            row = self._report(connection, user['org'], identifier)
            if row['archived_at'] is not None:
                return ()
            return tuple(action for action in ACTIONS if self._can(connection, user, row, action)
                         and (action == 'maintain' or (row['enabled'] and row['source_key'] in self._sources)))

    def list_reports(self, user, include_archived=False, admin=False):
        _bool(admin, 'admin')
        user = self._identity(user, admin=admin)
        _bool(include_archived, 'include_archived')
        if include_archived and not admin:
            raise AccessDenied('Archived config requires administrator access.')
        with self._transaction(user=user) as connection:
            rows = connection.execute('SELECT * FROM managed_reports WHERE org = ? ORDER BY name, id',
                                      (user['org'],)).fetchall()
            # Creation has an explicit per-organization cap. Do not silently
            # truncate search/category counts or authorized catalog metadata.
            return [self._record(row) for row in rows
                    if (include_archived or row['archived_at'] is None)
                    and (admin or (row['archived_at'] is None and row['enabled']
                                   and row['source_key'] in self._sources
                                   and self._can(connection, user, row, 'view')))]

    def get_report(self, user, identifier, for_maintenance=False):
        user = self._identity(user)
        _bool(for_maintenance, 'for_maintenance')
        with self._transaction(user=user) as connection:
            row = self._report(connection, user['org'], identifier)
            if not (for_maintenance and user['role'] == 'admin'):
                self._require(connection, user, row, 'maintain' if for_maintenance else 'view',
                              data=not for_maintenance)
            return self._record(row)

    def create_report(self, user, payload, *, request_key=None, request_id=None):
        user = self._identity(user, admin=True)
        fields, key = _report_payload(payload, create=True), _create_key(request_key)
        if fields['source_key'] not in self._sources:
            raise ValidationError('Select a supported source from this deployment.')
        with self._transaction(write=True, user=user) as connection:
            previous = connection.execute('SELECT * FROM managed_reports WHERE org = ? AND created_by = ? AND create_key = ?',
                                          (user['org'], user['id'], key)).fetchone()
            if previous is not None:
                if previous['version'] != 1 or previous['archived_at'] is not None or any(previous[k] != v for k, v in fields.items()):
                    raise Conflict('This create request was already used.')
                return self._record(previous)
            if connection.execute('SELECT COUNT(*) FROM managed_reports WHERE org = ?', (user['org'],)).fetchone()[0] >= MAX_REPORTS:
                raise ValidationError('The organization report limit has been reached.')
            identifier, now = uuid.uuid4().hex, time.time()
            connection.execute(
                'INSERT INTO managed_reports (id,org,source_key,name,category,description,enabled,version,created_by,create_key,created_at,updated_at) '
                'VALUES (?,?,?,?,?,?,?,1,?,?,?,?)',
                (identifier,user['org'],fields['source_key'],fields['name'],fields['category'],fields['description'],
                 int(fields['enabled']),user['id'],key,now,now))
            self._audit(connection,user,identifier,identifier,'report.created',fields,None,1,request_id)
            return self._record(self._report(connection,user['org'],identifier))

    def update_report(self, user, identifier, expected_version, payload, *, request_id=None):
        user, fields = self._identity(user), _report_payload(payload)
        if user['role'] != 'admin' and 'enabled' in fields:
            raise AccessDenied('Only organization administrators may change report availability.')
        with self._transaction(write=True, user=user) as connection:
            row = self._report(connection,user['org'],identifier)
            self._require(connection,user,row,'maintain',data=False)
            self._check_version(row,expected_version)
            changed = [key for key in fields if fields[key] != row[key]]
            if not changed:
                return self._record(row)
            merged = dict(row, **fields)
            connection.execute('UPDATE managed_reports SET name=?,category=?,description=?,enabled=?,version=version+1,updated_at=? '
                               'WHERE org=? AND id=? AND version=?',
                               (merged['name'],merged['category'],merged['description'],int(merged['enabled']),time.time(),
                                user['org'],identifier,expected_version))
            self._audit(connection,user,identifier,identifier,'report.updated',changed,expected_version,expected_version+1,request_id)
            return self._record(self._report(connection,user['org'],identifier))

    def _archive(self, user, identifier, expected_version, archived, request_id):
        user = self._identity(user,admin=True)
        with self._transaction(write=True, user=user) as connection:
            row = self._report(connection,user['org'],identifier)
            self._check_version(row,expected_version)
            if (row['archived_at'] is not None) == archived:
                raise Conflict('The report lifecycle changed. Reload it.')
            now = time.time()
            connection.execute('UPDATE managed_reports SET archived_at=?,updated_at=?,version=version+1 WHERE org=? AND id=? AND version=?',
                               (now if archived else None,now,user['org'],identifier,expected_version))
            self._audit(connection,user,identifier,identifier,'report.archived' if archived else 'report.restored',
                        ['archived_at'],expected_version,expected_version+1,request_id)
            return self._record(self._report(connection,user['org'],identifier))

    def archive_report(self,user,identifier,expected_version,*,request_id=None):
        return self._archive(user,identifier,expected_version,True,request_id)

    def restore_report(self,user,identifier,expected_version,*,request_id=None):
        return self._archive(user,identifier,expected_version,False,request_id)

    def list_grants(self,user,identifier):
        user = self._identity(user,admin=True)
        with self._transaction(user=user) as connection:
            self._report(connection,user['org'],identifier)
            rows = connection.execute('SELECT * FROM report_grants WHERE org=? AND report_id=? ORDER BY kind,subject',
                                      (user['org'],identifier)).fetchall()
            return [dict(kind=row['kind'],subject=row['subject'],actions=[a for a in ACTIONS if row['can_'+a]]) for row in rows]

    def set_grant(self,user,identifier,expected_version,kind,subject,actions,*,request_id=None):
        user = self._identity(user,admin=True)
        if kind not in ('role','user','org') or not isinstance(subject,str) or not subject:
            raise ValidationError('Select a valid grant principal.')
        if (not isinstance(actions,list) or any(action not in ACTIONS for action in actions)
                or len(set(actions)) != len(actions) or (actions and 'view' not in actions)):
            raise ValidationError('Export and maintain require view; select valid distinct permissions.')
        if kind == 'role' and subject not in ('admin','user'):
            raise ValidationError('Only the existing admin and user roles are supported.')
        if kind == 'org' and subject != user['org']:
            raise AccessDenied('Cross-organization grants are not permitted.')
        if kind == 'user' and actions:
            # Exact principal strings, never strip or case-fold identity claims.
            if len(subject) > 255 or _text(subject,'subject',255,required=True) != subject:
                raise ValidationError('Use the exact user identifier.')
            try:
                target = validate_identity(self.identities.get_user(subject))
            except Exception:
                raise ValidationError('Select an existing user in your organization.') from None
            if target['id'] != subject or target['org'] != user['org'] or target['role'] not in ('admin','user'):
                raise ValidationError('Select an existing user in your organization.')
        with self._transaction(write=True, user=user) as connection:
            row = self._report(connection,user['org'],identifier)
            self._check_version(row,expected_version)
            if row['archived_at'] is not None:
                raise Conflict('Restore the report before changing permissions.')
            existing = connection.execute('SELECT * FROM report_grants WHERE org=? AND report_id=? AND kind=? AND subject=?',
                                          (user['org'],identifier,kind,subject)).fetchone()
            values = tuple(int(a in actions) for a in ACTIONS)
            if existing is not None and values == tuple(existing['can_'+a] for a in ACTIONS):
                return self._record(row)
            if not actions and existing is None:
                return self._record(row)
            if existing is None:
                count = connection.execute('SELECT COUNT(*) FROM report_grants WHERE org=? AND report_id=?',
                                           (user['org'],identifier)).fetchone()[0]
                if count >= MAX_GRANTS:
                    raise ValidationError('The report principal limit has been reached.')
            if actions:
                connection.execute('INSERT INTO report_grants (org,report_id,kind,subject,can_view,can_export,can_maintain,updated_at) '
                                   'VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(org,report_id,kind,subject) DO UPDATE SET '
                                   'can_view=excluded.can_view,can_export=excluded.can_export,can_maintain=excluded.can_maintain,updated_at=excluded.updated_at',
                                   (user['org'],identifier,kind,subject)+values+(time.time(),))
            else:
                connection.execute('DELETE FROM report_grants WHERE org=? AND report_id=? AND kind=? AND subject=?',
                                   (user['org'],identifier,kind,subject))
            connection.execute('UPDATE managed_reports SET version=version+1,updated_at=? WHERE org=? AND id=? AND version=?',
                               (time.time(),user['org'],identifier,expected_version))
            self._audit(connection,user,identifier,identifier,'grant.changed',['permissions'],expected_version,expected_version+1,request_id)
            return self._record(self._report(connection,user['org'],identifier))

    def create_schedule(self,user,identifier,expected_report_version,payload,*,request_key=None,request_id=None):
        user = self._identity(user,admin=True)
        fields,key = _schedule_payload(payload,create=True),_create_key(request_key)
        with self._transaction(write=True, user=user) as connection:
            report = self._report(connection,user['org'],identifier)
            self._check_version(report,expected_report_version)
            if report['archived_at'] is not None:
                raise Conflict('Restore the report before configuring schedules.')
            previous = connection.execute('SELECT * FROM mail_schedules WHERE org=? AND created_by=? AND create_key=?',
                                          (user['org'],user['id'],key)).fetchone()
            if previous is not None:
                public = self._schedule_record(previous)
                if (previous['version'] != 1 or previous['report_id'] != identifier or any(public[k] != v for k,v in fields.items())):
                    raise Conflict('This create request was already used.')
                return public
            count = connection.execute('SELECT COUNT(*) FROM mail_schedules WHERE org=? AND report_id=?',
                                       (user['org'],identifier)).fetchone()[0]
            if count >= MAX_SCHEDULES:
                raise ValidationError('The report schedule limit has been reached.')
            schedule_id,now = uuid.uuid4().hex,time.time()
            connection.execute('INSERT INTO mail_schedules (id,org,report_id,recipients,cadence,send_time,timezone,weekday,enabled,version,created_by,create_key,created_at,updated_at) '
                               'VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?,?)',
                               (schedule_id,user['org'],identifier,json.dumps(fields['recipients']),fields['cadence'],fields['time'],fields['timezone'],
                                fields['weekday'],int(fields['enabled']),user['id'],key,now,now))
            self._audit(connection,user,identifier,schedule_id,'schedule.created',fields,None,1,request_id)
            return self._schedule_record(self._schedule(connection,user['org'],schedule_id))

    def get_schedule(self,user,identifier):
        user = self._identity(user,admin=True)
        with self._transaction(user=user) as connection:
            return self._schedule_record(self._schedule(connection,user['org'],identifier))

    def list_schedules(self,user,identifier):
        user = self._identity(user,admin=True)
        with self._transaction(user=user) as connection:
            self._report(connection,user['org'],identifier)
            return [self._schedule_record(row) for row in connection.execute(
                'SELECT * FROM mail_schedules WHERE org=? AND report_id=? ORDER BY updated_at DESC,id',
                (user['org'],identifier)).fetchall()]

    def update_schedule(self,user,identifier,expected_version,payload,*,request_id=None):
        user,fields = self._identity(user,admin=True),_schedule_payload(payload)
        with self._transaction(write=True, user=user) as connection:
            row = self._schedule(connection,user['org'],identifier)
            self._check_version(row,expected_version)
            report = self._report(connection,user['org'],row['report_id'])
            if report['archived_at'] is not None:
                raise Conflict('Restore the report before editing schedules.')
            current = self._schedule_record(row)
            changed = [key for key in fields if fields[key] != current[key]]
            if not changed:
                return current
            merged = dict(current,**fields)
            connection.execute('UPDATE mail_schedules SET recipients=?,cadence=?,send_time=?,timezone=?,weekday=?,enabled=?,version=version+1,updated_at=? '
                               'WHERE org=? AND id=? AND version=?',
                               (json.dumps(merged['recipients']),merged['cadence'],merged['time'],merged['timezone'],merged['weekday'],
                                int(merged['enabled']),time.time(),user['org'],identifier,expected_version))
            self._audit(connection,user,row['report_id'],identifier,'schedule.updated',changed,expected_version,expected_version+1,request_id)
            return self._schedule_record(self._schedule(connection,user['org'],identifier))

    def _authorize_data(self,user,identifier,action):
        with self._transaction(user=user) as connection:
            row = self._report(connection,user['org'],identifier)
            self._require(connection,user,row,action)
            return self._record(row)

    def _fresh_user(self, user):
        return self._identity(user)

    def _rows(self,user,identifier,action):
        user = self._identity(user)
        report = self._authorize_data(user,identifier,action)
        try:
            rows = self.provider.managed_rows(user,report['source_key'])
            if not isinstance(rows,list) or len(rows) > 10000:
                raise ValueError('invalid provider result')
            canonical = []
            for row in rows:
                if not isinstance(row,dict) or set(row) != set(self.columns):
                    raise ValueError('invalid provider row')
                row = dict(row)
                for key in ('period','department'):
                    row[key] = _text(row[key],key,120,required=True)
                for key in ('revenue','cost','profit'):
                    if isinstance(row[key],bool) or not isinstance(row[key],(int,float)) or not math.isfinite(row[key]):
                        raise ValueError('invalid provider numeric value')
                canonical.append(row)
        except AccessDenied:
            raise
        except Exception:
            raise ProviderUnavailable() from None
        # Revocation/disable while a provider was reading must not release data.
        latest = self._authorize_data(self._fresh_user(user),identifier,action)
        if latest['source_key'] != report['source_key']:
            raise AccessDenied('Report source changed.')
        return canonical

    def rows(self,user,identifier):
        return self._rows(user,identifier,'view')

    def export(self,user,identifier):
        rows = self._rows(user,identifier,'export')
        output = io.StringIO(newline='')
        writer = csv.DictWriter(output,fieldnames=self.columns)
        writer.writeheader()
        # Protect spreadsheet consumers against formula injection in text cells.
        writer.writerows({key:("'"+value if isinstance(value,str) and value[:1] in ('=','+','-','@','\t','\r') else value)
                          for key,value in row.items()} for row in rows)
        return output.getvalue()

    def simulate_schedule(self,user,identifier,expected_version,*,request_id=None):
        user = self._identity(user,admin=True)
        if not self.is_demo:
            raise AccessDenied('Mock execution is disabled outside demo mode.')
        with self._transaction(write=True, user=user) as connection:
            schedule = self._schedule(connection,user['org'],identifier)
            self._check_version(schedule,expected_version)
            report = self._report(connection,user['org'],schedule['report_id'])
            self._require(connection,user,report,'export')
            if not schedule['enabled']:
                raise AccessDenied('Enable the schedule before simulating it.')
            existing = connection.execute('SELECT * FROM schedule_runs WHERE org=? AND schedule_id=? AND schedule_version=?',
                                          (user['org'],identifier,expected_version)).fetchone()
            if existing is not None:
                return dict(existing,duplicate=True)
            run_id = uuid.uuid4().hex
            connection.execute('INSERT INTO schedule_runs (id,org,report_id,schedule_id,schedule_version,actor_id,status,started_at) '
                               'VALUES (?,?,?,?,?,?,?,?)',
                               (run_id,user['org'],report['id'],identifier,expected_version,user['id'],'running',time.time()))
            self._audit(connection,user,report['id'],identifier,'mock.claimed',['status'],expected_version,expected_version,request_id)
            recipients = json.loads(schedule['recipients'])
            report_id, report_version = report['id'],report['version']
        error_code = None
        try:
            # No browser claims or persisted creator role is reused as authority.
            fresh = self._fresh_user(user)
            with self._transaction(write=True, user=user) as connection:
                latest = self._schedule(connection,user['org'],identifier)
                latest_report = self._report(connection,user['org'],report_id)
                self._require(connection,fresh,latest_report,'export')
                if latest['version'] != expected_version or not latest['enabled'] or latest_report['version'] != report_version:
                    raise Conflict('Configuration changed.')
            self.export(fresh,report_id)
            # Recheck after provider work. Hold a local SQLite write transaction
            # during this fixed, in-memory capture so a settings write cannot
            # commit between the final check and the mock side effect. A real
            # SMTP adapter needs a separately reviewed outbox/fencing design.
            with self._transaction(write=True) as connection:
                fresh = self._fresh_user(user)
                latest = self._schedule(connection,user['org'],identifier)
                latest_report = self._report(connection,user['org'],report_id)
                self._require(connection,fresh,latest_report,'export')
                if latest['version'] != expected_version or not latest['enabled'] or latest_report['version'] != report_version:
                    raise Conflict('Configuration changed.')
                self.mail.send('Synthetic report simulation','Synthetic fixture captured; no external delivery.',recipients)
        except (AccessDenied,Conflict,NotFound):
            error_code = 'configuration_changed'
        except ProviderUnavailable:
            error_code = 'provider_unavailable'
        except Exception:
            error_code = 'mock_failed'
        # Completion failure leaves the committed running claim uncertain.
        # It must never be overwritten as failed or replayed automatically.
        # Persist the already-claimed outcome even if its actor was revoked.
        with self._transaction(write=True) as connection:
            status = 'failed' if error_code else 'succeeded'
            changed = connection.execute('UPDATE schedule_runs SET status=?,error_code=?,finished_at=? WHERE org=? AND id=? AND status=?',
                                         (status,error_code,time.time(),user['org'],run_id,'running')).rowcount
            if changed != 1:
                raise StateUnavailable('Run completion is uncertain.')
            self._audit(connection,user,report_id,identifier,'mock.failed' if error_code else 'mock.succeeded',
                        ['status'],expected_version,expected_version,request_id)
            return dict(connection.execute('SELECT * FROM schedule_runs WHERE org=? AND id=?',(user['org'],run_id)).fetchone(),duplicate=False)

    def list_runs(self,user,identifier):
        user = self._identity(user,admin=True)
        with self._transaction(user=user) as connection:
            self._report(connection,user['org'],identifier)
            return [dict(row) for row in connection.execute('SELECT * FROM schedule_runs WHERE org=? AND report_id=? ORDER BY started_at DESC,id LIMIT 50',
                                                          (user['org'],identifier)).fetchall()]

    def list_audit(self,user,identifier=None):
        user = self._identity(user,admin=True)
        with self._transaction(user=user) as connection:
            if identifier is not None:
                self._report(connection,user['org'],identifier)
            sql = 'SELECT * FROM admin_changes WHERE org=?'
            params = [user['org']]
            if identifier is not None:
                sql += ' AND report_id=?'
                params.append(identifier)
            rows = connection.execute(sql+' ORDER BY id DESC LIMIT 100',params).fetchall()
            return [dict(row,changed_fields=json.loads(row['changed_fields'])) for row in rows]
