"""Tenant-bound SQLite repository for the report-definition vertical slice.

A transaction owns one immutable principal and one connection. Reads never
accept a caller-selected organization. Each write checks the centralized policy
inside the transaction and repeats tenant/owner/version/lifecycle predicates in
SQL. Row mutation and payload-free audit succeed or roll back together.
"""
from contextlib import contextmanager
import sqlite3

from .definition_domain import Conflict, NotFound, StateUnavailable, _record
from .definition_policy import DefinitionPrincipal
from .state import StateError, StateStore


class ReportDefinitionRepository:
    """App-scoped storage configuration; never holds an actor or connection."""

    def __init__(self, store):
        if store is not None and not isinstance(store, StateStore):
            raise TypeError('store must be a StateStore or None')
        self.store = store

    @contextmanager
    def transaction(self, principal, write=False):
        if not isinstance(principal, DefinitionPrincipal):
            raise TypeError('A validated definition principal is required')
        if self.store is None:
            raise StateUnavailable('Durable state is unavailable.')
        unit = None
        try:
            with self.store._transaction(write=write) as connection:
                unit = _DefinitionTransaction(connection, self.store, principal, write)
                yield unit
        except (StateError, sqlite3.Error, OSError):
            raise StateUnavailable('Durable state is unavailable.') from None
        finally:
            if unit is not None:
                unit.close()


class _DefinitionTransaction:
    """Internal transaction object; it cannot outlive its context manager."""

    def __init__(self, connection, store, principal, write):
        self._connection = connection
        self._store = store
        self._principal = principal
        self._write = write

    def close(self):
        self._connection = None

    def _db(self, write=False):
        if self._connection is None or (write and not self._write):
            raise StateUnavailable('Durable state is unavailable.')
        return self._connection

    def get(self, identifier):
        row = self._db().execute(
            'SELECT * FROM report_definitions WHERE id = ? AND org = ?',
            (identifier, self._principal.org),
        ).fetchone()
        if row is None:
            raise NotFound('Report definition not found.')
        return _record(row)

    def list(self, query, limit, offset, include_deleted):
        pattern = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        where = "org = ? AND (name LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')"
        if not include_deleted:
            where += ' AND deleted_at IS NULL'
        args = (self._principal.org, pattern, pattern)
        total = self._db().execute(
            'SELECT COUNT(*) FROM report_definitions WHERE ' + where, args,
        ).fetchone()[0]
        rows = self._db().execute(
            'SELECT * FROM report_definitions WHERE ' + where +
            ' ORDER BY updated_at DESC, id ASC LIMIT ? OFFSET ?', args + (limit, offset),
        ).fetchall()
        return {'items': [_record(row) for row in rows], 'total': total, 'limit': limit, 'offset': offset}

    def created_with(self, create_key):
        row = self._db().execute(
            'SELECT * FROM report_definitions WHERE org = ? AND owner_id = ? AND create_key = ?',
            (self._principal.org, self._principal.actor_id, create_key),
        ).fetchone()
        return _record(row) if row is not None else None

    def _audit(self, event, identifier, now, request_id):
        self._store._audit(self._db(write=True), event, self._principal.actor_id,
                           identifier, 'success', now, request_id=request_id)

    def insert(self, identifier, create_key, fields, now, request_id):
        self._db(write=True).execute(
            'INSERT INTO report_definitions '
            '(id, org, owner_id, create_key, name, description, cadence, enabled, version, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)',
            (identifier, self._principal.org, self._principal.actor_id, create_key,
             fields['name'], fields['description'], fields['cadence'], int(fields['enabled']), now, now),
        )
        self._audit('crud.created', identifier, now, request_id)
        return self.get(identifier)

    def update(self, identifier, version, fields, now, request_id):
        row = self.get(identifier)
        self._principal.require_change(row, version, deleted=False)
        merged = dict(row)
        merged.update(fields)
        changed = self._db(write=True).execute(
            'UPDATE report_definitions SET name = ?, description = ?, cadence = ?, enabled = ?, '
            'version = version + 1, updated_at = ? WHERE id = ? AND org = ? AND version = ? '
            "AND deleted_at IS NULL AND (owner_id = ? OR ? = 'admin')",
            (merged['name'], merged['description'], merged['cadence'], int(merged['enabled']),
             now, identifier, self._principal.org, version, self._principal.actor_id, self._principal.role),
        ).rowcount
        if changed != 1:
            raise Conflict('The definition changed. Reload it and try again.')
        self._audit('crud.updated', identifier, now, request_id)
        return self.get(identifier)

    def set_deleted(self, identifier, version, deleted, now, request_id):
        row = self.get(identifier)
        self._principal.require_change(row, version, deleted=not deleted)
        # The SQL fragment comes solely from this server boolean, never input.
        previous_state = 'IS NULL' if deleted else 'IS NOT NULL'
        changed = self._db(write=True).execute(
            'UPDATE report_definitions SET deleted_at = ?, updated_at = ?, version = version + 1 '
            'WHERE id = ? AND org = ? AND version = ? AND deleted_at ' + previous_state +
            " AND (owner_id = ? OR ? = 'admin')",
            (now if deleted else None, now, identifier, self._principal.org, version,
             self._principal.actor_id, self._principal.role),
        ).rowcount
        if changed != 1:
            raise Conflict('The definition changed. Reload it and try again.')
        self._audit('crud.deleted' if deleted else 'crud.restored', identifier, now, request_id)
        return self.get(identifier)
