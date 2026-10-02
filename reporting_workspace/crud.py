"""Bounded report-definition metadata CRUD, without any report/job execution.

Identity mappings must come from the server's current identity provider, never
from a request body. All SQL values are bound parameters. The same validated
StateStore transaction commits a mutation and its payload-free audit event.
"""

from collections.abc import Mapping
from contextlib import contextmanager
import hashlib
import re
import sqlite3
import time
import unicodedata
import uuid

from .state import StateError, StateStore, _identifier


_MAX_VERSION = 9223372036854775807
_EDITABLE = frozenset({'name', 'description', 'cadence', 'enabled'})
_CADENCES = frozenset({'manual', 'daily', 'weekly', 'monthly'})
_RECORD_ID = re.compile(r'[0-9a-f]{32}\Z')
_IDENTITY_LIMITS = {'id': 255, 'org': 128, 'role': 64}


class CrudError(Exception):
    """A safe, fixed-message domain failure for the presentation layer."""


class ValidationError(CrudError):
    """Invalid editable fields, optimistic version or pagination (HTTP 400)."""


class AccessDenied(CrudError):
    """Unauthenticated identity or forbidden same-organization write (HTTP 403)."""


class NotFound(CrudError):
    """No record visible to this identity (HTTP 404)."""


class Conflict(CrudError):
    """Stale version or incompatible current lifecycle state (HTTP 409)."""


class StateUnavailable(CrudError):
    """Durable state cannot safely complete the operation (HTTP 503)."""


def _identity(user):
    if not isinstance(user, Mapping):
        raise AccessDenied('A valid identity is required.')
    for name, maximum in _IDENTITY_LIMITS.items():
        value = user.get(name)
        if (not isinstance(value, str) or not 0 < len(value) <= maximum
                or not value.strip()
                or any(unicodedata.category(char) in ('Cc', 'Cs') for char in value)):
            raise AccessDenied('A valid identity is required.')
    if user['role'] not in ('admin', 'user'):
        raise AccessDenied('A supported role is required.')
    # Preserve authoritative claims exactly; trimming IDs/orgs could merge tenants.
    return user['org'], hashlib.sha256(user['id'].encode('utf-8')).hexdigest(), user['role']


def _text(value, name, maximum, required=False, multiline=False):
    if not isinstance(value, str):
        raise ValidationError('{} must be text.'.format(name))
    value = value.strip()
    if len(value) > maximum or (required and not value):
        raise ValidationError('{} has an invalid length.'.format(name))
    if any(unicodedata.category(char) in ('Cc', 'Cs')
           and not (multiline and char in '\n\t') for char in value):
        raise ValidationError('{} contains unsupported characters.'.format(name))
    return value


def _payload(payload, create=False):
    if not isinstance(payload, Mapping):
        raise ValidationError('The payload must be an editable-field mapping.')
    if any(key not in _EDITABLE for key in payload):
        raise ValidationError('Unknown or server-owned fields are not editable.')
    if not payload or (create and 'name' not in payload):
        raise ValidationError('A name is required.' if create else 'At least one editable field is required.')
    result = {'description': '', 'cadence': 'manual', 'enabled': True} if create else {}
    for name, value in payload.items():
        if name == 'name':
            value = _text(value, 'name', 120, required=True)
        elif name == 'description':
            value = _text(value, 'description', 1000, multiline=True)
        elif name == 'cadence':
            value = _text(value, 'cadence', 7, required=True)
            if value not in _CADENCES:
                raise ValidationError('cadence must be manual, daily, weekly or monthly.')
        elif not isinstance(value, bool):
            raise ValidationError('enabled must be a boolean.')
        result[name] = value
    return result


def _version(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _MAX_VERSION:
        raise ValidationError('expected_version must be a positive integer.')
    return value


def _record_id(value):
    # Malformed and absent/cross-organization identifiers have the same response.
    if not isinstance(value, str) or _RECORD_ID.fullmatch(value) is None:
        raise NotFound('Report definition not found.')
    return value


def _request_id(value):
    if value is not None:
        try:
            _identifier(value, 'request_id')
        except ValueError:
            raise ValidationError('request_id must be an opaque identifier.') from None
    return value


def _create_key(value):
    if value is None:
        return uuid.uuid4().hex
    if not isinstance(value, str) or _RECORD_ID.fullmatch(value) is None:
        raise ValidationError('request_key must be a 32-character lowercase hex identifier.')
    return value


def _record(row):
    result = dict(row)
    result.pop('create_key')
    result['enabled'] = bool(result['enabled'])
    return result


class ReportDefinitions:
    """Metadata service backed by local StateStore; no in-memory write fallback.

    list returns {items, total, limit, offset}. All records are plain dictionaries.
    get includes a soft-deleted record so authorized clients can inspect/restore
    it. Normal list hides deleted records. Same-organization users may read every
    record, but only owners and organization administrators may change one.
    A per-draft request_key makes create retries idempotent only while that exact
    canonical initial payload remains active at version one. Keys are internal.
    """

    def __init__(self, store):
        if store is not None and not isinstance(store, StateStore):
            raise TypeError('store must be a StateStore or None')
        self.store = store

    @contextmanager
    def _transaction(self, write=False):
        if self.store is None:
            raise StateUnavailable('Durable state is unavailable.')
        try:
            with self.store._transaction(write=write) as connection:
                yield connection
        except (StateError, sqlite3.Error, OSError):
            # Never expose SQL, database paths, row data or backend error text.
            raise StateUnavailable('Durable state is unavailable.') from None

    @staticmethod
    def _get(connection, org, identifier):
        row = connection.execute(
            'SELECT * FROM report_definitions WHERE id = ? AND org = ?',
            (identifier, org),
        ).fetchone()
        if row is None:
            raise NotFound('Report definition not found.')
        return row

    @staticmethod
    def _can_write(row, actor, role, expected_version, deleted):
        if role != 'admin' and row['owner_id'] != actor:
            raise AccessDenied('Only the owner or an organization administrator may change this definition.')
        if row['version'] != expected_version:
            raise Conflict('The definition changed. Reload it and try again.')
        if (row['deleted_at'] is not None) != deleted:
            raise Conflict('The definition has a different deletion state. Reload it and try again.')
        if row['version'] == _MAX_VERSION:
            raise Conflict('The definition version is exhausted.')

    def list(self, user, q='', limit=20, offset=0, include_deleted=False):
        org, actor, role = _identity(user)
        q = _text(q, 'q', 120)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValidationError('limit must be an integer between 1 and 100.')
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 100000:
            raise ValidationError('offset must be an integer between 0 and 100000.')
        if not isinstance(include_deleted, bool):
            raise ValidationError('include_deleted must be a boolean.')
        # Wildcards are literal user text; neither search nor sorting accepts SQL.
        query = '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        where = "org = ? AND (name LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')"
        if not include_deleted:
            where += ' AND deleted_at IS NULL'
        with self._transaction() as connection:
            total = connection.execute(
                'SELECT COUNT(*) FROM report_definitions WHERE ' + where, (org, query, query),
            ).fetchone()[0]
            rows = connection.execute(
                'SELECT * FROM report_definitions WHERE ' + where +
                ' ORDER BY updated_at DESC, id ASC LIMIT ? OFFSET ?',
                (org, query, query, limit, offset),
            ).fetchall()
            return {'items': [_record(row) for row in rows], 'total': total, 'limit': limit, 'offset': offset}

    def get(self, user, identifier):
        org, actor, role = _identity(user)
        identifier = _record_id(identifier)
        with self._transaction() as connection:
            return _record(self._get(connection, org, identifier))

    def create(self, user, payload, *, request_id=None, request_key=None):
        """Create once per owner/org/key; omit the key for an independent create."""
        org, actor, role = _identity(user)
        fields, request_id = _payload(payload, create=True), _request_id(request_id)
        create_key = _create_key(request_key)
        with self._transaction(write=True) as connection:
            previous = connection.execute(
                'SELECT * FROM report_definitions WHERE org = ? AND owner_id = ? AND create_key = ?',
                (org, actor, create_key),
            ).fetchone()
            if previous is not None:
                if (previous['version'] != 1 or previous['deleted_at'] is not None
                        or any(previous[name] != fields[name] for name in _EDITABLE)):
                    raise Conflict('This create request was already used. Reload the definition or start a new draft.')
                return _record(previous)
            identifier = uuid.uuid4().hex
            now = time.time()
            connection.execute(
                'INSERT INTO report_definitions '
                '(id, org, owner_id, create_key, name, description, cadence, enabled, version, created_at, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)',
                (identifier, org, actor, create_key, fields['name'], fields['description'], fields['cadence'],
                 int(fields['enabled']), now, now),
            )
            self.store._audit(connection, 'crud.created', actor, identifier, 'success', now, request_id=request_id)
            return _record(self._get(connection, org, identifier))

    def update(self, user, identifier, expected_version, payload, *, request_id=None):
        org, actor, role = _identity(user)
        identifier, expected_version = _record_id(identifier), _version(expected_version)
        fields, request_id = _payload(payload), _request_id(request_id)
        with self._transaction(write=True) as connection:
            row = self._get(connection, org, identifier)
            self._can_write(row, actor, role, expected_version, deleted=False)
            merged = dict(row)
            merged.update(fields)
            now = time.time()
            changed = connection.execute(
                'UPDATE report_definitions SET name = ?, description = ?, cadence = ?, enabled = ?, '
                'version = version + 1, updated_at = ? WHERE id = ? AND org = ? AND version = ?',
                (merged['name'], merged['description'], merged['cadence'], int(merged['enabled']),
                 now, identifier, org, expected_version),
            ).rowcount
            if changed != 1:
                raise Conflict('The definition changed. Reload it and try again.')
            self.store._audit(connection, 'crud.updated', actor, identifier, 'success', now, request_id=request_id)
            return _record(self._get(connection, org, identifier))

    def _set_deleted(self, user, identifier, expected_version, deleted, request_id):
        org, actor, role = _identity(user)
        identifier, expected_version = _record_id(identifier), _version(expected_version)
        request_id = _request_id(request_id)
        with self._transaction(write=True) as connection:
            row = self._get(connection, org, identifier)
            self._can_write(row, actor, role, expected_version, deleted=not deleted)
            now = time.time()
            changed = connection.execute(
                'UPDATE report_definitions SET deleted_at = ?, updated_at = ?, version = version + 1 '
                'WHERE id = ? AND org = ? AND version = ?',
                (now if deleted else None, now, identifier, org, expected_version),
            ).rowcount
            if changed != 1:
                raise Conflict('The definition changed. Reload it and try again.')
            self.store._audit(connection, 'crud.deleted' if deleted else 'crud.restored', actor,
                              identifier, 'success', now, request_id=request_id)
            return _record(self._get(connection, org, identifier))

    def soft_delete(self, user, identifier, expected_version, *, request_id=None):
        return self._set_deleted(user, identifier, expected_version, True, request_id)

    def restore(self, user, identifier, expected_version, *, request_id=None):
        return self._set_deleted(user, identifier, expected_version, False, request_id)
