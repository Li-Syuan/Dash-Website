"""Actual synthetic SQLite and isolated fake Oracle session contract tests.

No SQLAlchemy/Oracle driver is imported; no company database or network is used.
The fake-session suite proves the adapter boundary, not live Oracle behavior.
"""
import copy
from dataclasses import replace
import os
import sqlite3
from contextlib import closing
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from reporting_workspace.legacy_crud import (AdapterUnavailable, InvalidInput,
    PermissionDenied, RecordConflict, RecordNotFound, StorageUnavailable)
from reporting_workspace.legacy_policy import Policy
from reporting_workspace.maintenance_registry import (BindingMismatch,
    MaintenanceDefinition, MaintenancePolicy, MaintenanceRegistry,
    ORMMaintenanceAdapter, SQLiteDatabase, SQLiteMaintenanceAdapter,
    build_fixture_registry, build_synthetic_registry)
from reporting_workspace.providers import DemoIdentityProvider


def user(name='writer', role='admin', org='A'):
    return {'id': name, 'role': role, 'org': org}


def model(table='fixture_rows', bind='local', extra=(), primary=True):
    def column(name, kind, **flags):
        return SimpleNamespace(name=name, type=type(kind, (), {'length': 255})(),
                               primary_key=flags.get('primary', False),
                               nullable=flags.get('nullable', True),
                               unique=flags.get('unique', False))
    columns = (column('id', 'Integer', primary=primary),
               column('code', 'String', nullable=False, unique=True),
               column('description', 'String'), column('enabled', 'Boolean')) + tuple(extra)
    def initialize(self, **values):
        self.__dict__.update(values)
    attributes = dict(__tablename__=table, __bind_key__=bind,
                      __table__=SimpleNamespace(name=table, columns=columns), __init__=initialize)
    attributes.update({item.name: item.name for item in columns})
    return type('SyntheticModel', (), attributes)


CONFIG = {'code': {'required': True, 'Update_Lock': True},
          'description': {'default': ''}, 'enabled': {'default': True}}


class MaintenanceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.database = SQLiteDatabase(os.path.join(self.tmp.name, 'local.sqlite'), 'local')
        self.policy = MaintenancePolicy()
        self.policy_calls = 0
        def resolver(current):
            self.policy_calls += 1
            return self.policy
        self.spec = MaintenanceDefinition('maintenance.local', 'Synthetic local table', model(),
            self.database, 'local', CONFIG, resolver, SQLiteMaintenanceAdapter(self.database))
        self.registry = MaintenanceRegistry()
        self.registry.register(self.spec)
        self.registry.freeze()
        self.writer, self.reader = user(), user('reader', 'user')

    def tearDown(self):
        self.registry.close()
        self.tmp.cleanup()

    def create(self, code='SYNTH-NEW'):
        return self.registry.create(self.writer, self.spec.key, {'code': code})

    def test_original_registration_contract_and_bind_mismatch(self):
        self.assertIs(self.spec.model, self.registry.definitions[0].model)
        self.assertIs(self.spec.init_db, self.database)
        self.assertEqual(self.spec.model.__bind_key__, self.spec.bind)
        self.assertEqual(self.spec.model_config['code']['Update_Lock'], True)
        with self.assertRaises(BindingMismatch):
            replace(self.spec, bind='other')
        with self.assertRaises(TypeError):
            self.spec.model_config['code']['Update_Lock'] = False
        with self.assertRaises(RuntimeError):
            self.registry.register(replace(self.spec, key='second'))
        self.spec.model.__bind_key__ = 'other'
        with self.assertRaises(BindingMismatch):
            self.registry.query(self.writer, self.spec.key)
        self.assertFalse(os.path.exists(self.database.path))

    def test_one_hundred_mixed_definitions_without_eager_connection(self):
        with patch('sqlite3.connect', side_effect=AssertionError('must remain lazy')):
            registry = build_fixture_registry(self.tmp.name)
            self.assertEqual(len(registry.definitions), 100)
            first = registry.catalog(self.writer, limit=20)
            second = registry.catalog(self.writer, limit=20, offset=20)
            self.assertEqual(first['total'], 100)
            self.assertEqual(len(first['items']), 20)
            self.assertFalse(set(item['key'] for item in first['items']).intersection(
                             item['key'] for item in second['items']))
            self.assertEqual(registry.catalog(self.writer, backend='sqlite')['total'], 75)
            oracle = registry.catalog(self.writer, backend='oracle')
            self.assertEqual(oracle['total'], 25)
            self.assertTrue(all(not item['available'] for item in oracle['items']))
            detail = registry.describe(self.writer, 'fixture-001')
            self.assertEqual(detail['primary_key'], 'id')
            self.assertNotIn('model', detail)
            self.assertNotIn('init_db', detail)
            self.assertNotIn(self.tmp.name, str(detail))
            self.assertEqual(registry.catalog(user(org='B'))['total'], 0)
            self.assertEqual(registry.catalog(self.writer, q='fixture-100')['total'], 1)
        self.assertEqual(os.listdir(self.tmp.name), [])
        page = registry.query(self.writer, 'fixture-001')
        self.assertEqual(page['items'][0]['code'], 'SYNTH-001')
        # Selecting one definition initializes one table, not all 100.
        with closing(sqlite3.connect(os.path.join(self.tmp.name, 'fixture-sqlite-a.sqlite'))) as connection, connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(len([name for name in tables if name.startswith('synthetic_maintenance_')]), 1)
        with self.assertRaises(AdapterUnavailable):
            registry.query(self.writer, 'fixture-100')
        self.assertEqual(len(os.listdir(self.tmp.name)), 1)

    def test_actual_sqlite_create_query_update_soft_delete_and_audit(self):
        row = self.create()
        self.assertEqual(row, {'id': 1, 'code': 'SYNTH-NEW', 'description': '', 'enabled': True,
                               'version': 1, 'deleted': False})
        self.assertEqual(self.registry.get(self.reader, self.spec.key, row['id']), row)
        changed = self.registry.update(self.writer, self.spec.key, row['id'], 1,
                                       {'description': 'Changed', 'enabled': False})
        self.assertEqual(changed['version'], 2)
        self.assertFalse(changed['enabled'])
        deleted = self.registry.delete(self.writer, self.spec.key, row['id'], 2)
        self.assertTrue(deleted['deleted'])
        self.assertEqual(deleted['version'], 3)
        self.assertEqual(self.registry.query(self.reader, self.spec.key)['total'], 0)
        with self.assertRaises(RecordNotFound):
            self.registry.get(self.writer, self.spec.key, row['id'])
        with closing(sqlite3.connect(self.database.path)) as connection, connection:
            stored = connection.execute('SELECT code,deleted,version FROM fixture_rows').fetchone()
            audit = connection.execute('SELECT action,actor_hash,changed_fields FROM maintenance_audit ORDER BY id').fetchall()
        self.assertEqual(stored, ('SYNTH-NEW', 1, 3))
        self.assertEqual([item[0] for item in audit], ['create', 'update', 'delete'])
        self.assertNotIn('writer', str(audit))
        self.assertNotIn('SYNTH-NEW', str(audit))
        self.assertNotIn('Changed', str(audit))

    def test_all_seventy_five_sqlite_definitions_use_their_declared_bind(self):
        registry = build_fixture_registry(self.tmp.name)
        for number in range(1, 76):
            key = 'fixture-{:03d}'.format(number)
            row = registry.query(self.writer, key)['items'][0]
            self.assertEqual(row['code'], 'SYNTH-{:03d}'.format(number))
            created = registry.create(self.writer, key, {'code': 'SYNTH-SHARED-CODE'})
            self.assertEqual(created['version'], 1)
            self.assertEqual(registry.query(self.writer, key)['total'], 2)
        for bind in ('fixture-sqlite-a', 'fixture-sqlite-b', 'fixture-sqlite-c'):
            with closing(sqlite3.connect(os.path.join(self.tmp.name, bind + '.sqlite'))) as connection, connection:
                tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                self.assertEqual(len([name for name in tables if name.startswith('synthetic_maintenance_')]), 25)
        self.assertFalse(any('oracle' in name for name in os.listdir(self.tmp.name)))

    def test_identity_and_policy_reloaded_on_every_request(self):
        identities = DemoIdentityProvider({'writer': {'password': 'not-used', 'role': 'admin', 'org': 'A'}})
        registry = MaintenanceRegistry(identities)
        registry.register(self.spec)
        row = registry.create(self.writer, self.spec.key, {'code': 'RELOAD'})
        identities.user_db['writer']['role'] = 'user'
        with self.assertRaises(PermissionDenied):
            registry.describe(self.writer, self.spec.key)
        self.writer = identities.get_user('writer')
        self.assertEqual(registry.describe(self.writer, self.spec.key)['access'], 'read')
        with self.assertRaises(PermissionDenied):
            registry.update(self.writer, self.spec.key, row['id'], 1, {'description': 'No'})
        self.policy = MaintenancePolicy(read_roles=('admin',), write_roles=('admin',))
        self.assertEqual(registry.catalog(self.writer)['total'], 0)
        with self.assertRaises(PermissionDenied):
            registry.get(self.writer, self.spec.key, row['id'])
        del identities.user_db['writer']
        with self.assertRaises(PermissionDenied):
            registry.catalog(self.writer)
        self.assertGreaterEqual(self.policy_calls, 4)

    def test_tenant_isolation_and_read_only_access(self):
        row = self.create()
        other_org = user(org='B')
        self.assertEqual(self.registry.query(other_org, self.spec.key)['total'], 0)
        for operation in (lambda: self.registry.get(other_org, self.spec.key, row['id']),
                          lambda: self.registry.update(other_org, self.spec.key, row['id'], 1, {'description': 'No'}),
                          lambda: self.registry.delete(other_org, self.spec.key, row['id'], 1)):
            with self.assertRaises(RecordNotFound):
                operation()
        other = self.registry.create(other_org, self.spec.key, {'code': row['code']})
        self.assertNotEqual(other['id'], row['id'])
        for operation in (lambda: self.registry.create(self.reader, self.spec.key, {'code': 'NO'}),
                          lambda: self.registry.update(self.reader, self.spec.key, row['id'], 1, {'description': 'No'}),
                          lambda: self.registry.delete(self.reader, self.spec.key, row['id'], 1)):
            with self.assertRaises(PermissionDenied):
                operation()
        self.assertEqual(self.registry.query(self.writer, self.spec.key)['total'], 1)

    def test_policy_errors_and_unsupported_claims_fail_closed_without_io(self):
        def broken(current):
            raise RuntimeError('private provider failure')
        registry = MaintenanceRegistry()
        registry.register(replace(self.spec, policy_resolver=broken))
        with patch('sqlite3.connect', side_effect=AssertionError('denied must not open DB')):
            self.assertEqual(registry.catalog(self.writer)['total'], 0)
            for current in (None, {}, user(role='outsider'), user(org=''), user(name='\n')):
                with self.assertRaises(PermissionDenied):
                    self.registry.query(current, self.spec.key)
            with self.assertRaises(PermissionDenied):
                registry.query(self.writer, self.spec.key)
        self.assertFalse(os.path.exists(self.database.path))

    def test_legacy_policy_preserves_entry_vs_crud_roles(self):
        policy = Policy(orgcode=['A'], crud_roles=['dev'])
        registry = MaintenanceRegistry()
        registry.register(replace(self.spec, policy_resolver=lambda current: policy))
        admin = SimpleNamespace(id='admin', orgcode='B', is_authenticated=True, is_admin=True)
        dev = SimpleNamespace(id='dev', orgcode='A', is_authenticated=True, is_dev=True)
        self.assertEqual(registry.describe(admin, self.spec.key)['access'], 'read')
        with self.assertRaises(PermissionDenied):
            registry.create(admin, self.spec.key, {'code': 'NO'})
        self.assertEqual(registry.create(dev, self.spec.key, {'code': 'DEV'})['version'], 1)
        self.assertEqual(registry.catalog(admin)['total'], 1)
        self.assertEqual(registry.query(admin, self.spec.key)['total'], 0)

    def test_fields_locks_required_primary_and_scalar_validation(self):
        invalid = ({}, {'id': 12, 'code': 'NO'}, {'org': 'B', 'code': 'NO'},
                   {'version': 999, 'code': 'NO'}, {'deleted': True, 'code': 'NO'},
                   {'code': None}, {'code': ''}, {'code': ' '}, {'code': 'x' * 256},
                   {'code': 'NO\x00'}, {'code': 123}, {'code': 'NO', 'enabled': 1})
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(InvalidInput):
                    self.registry.create(self.writer, self.spec.key, payload)
        row = self.create()
        with self.assertRaises(InvalidInput):
            self.registry.update(self.writer, self.spec.key, row['id'], 1, {'code': 'CHANGED'})
        config = copy.deepcopy(CONFIG)
        config['enabled']['Create_Lock'] = True
        config['enabled']['default'] = False
        locked_registry = MaintenanceRegistry()
        locked_registry.register(replace(self.spec, key='locked', model_config=config))
        self.assertFalse(locked_registry.create(self.writer, 'locked', {'code': 'LOCKED'})['enabled'])
        with self.assertRaises(InvalidInput):
            locked_registry.create(self.writer, 'locked', {'code': 'LOCKED2', 'enabled': True})

    def test_no_arbitrary_sql_and_bounded_pagination(self):
        literal = "X'; DROP TABLE fixture_rows;--"
        self.create(literal)
        self.assertEqual(self.registry.query(self.reader, self.spec.key, {'code': literal})['total'], 1)
        self.assertEqual(self.registry.query(self.reader, self.spec.key, {'code': '%'})['total'], 0)
        for filters in ({'code OR 1=1': 'x'}, {'org': 'B'}, 'SELECT *', {'description': ['x']}):
            with self.assertRaises(InvalidInput):
                self.registry.query(self.writer, self.spec.key, filters=filters)
        for limit, offset in ((0, 0), (101, 0), (True, 0), (1, -1), (1, 100001), (1, False)):
            with self.assertRaises(InvalidInput):
                self.registry.query(self.writer, self.spec.key, limit=limit, offset=offset)
            with self.assertRaises(InvalidInput):
                self.registry.catalog(self.writer, limit=limit, offset=offset)
        for identifier in (True, 0, -1, '1', '1 OR 1=1'):
            with self.assertRaises(InvalidInput):
                self.registry.get(self.writer, self.spec.key, identifier)
        for table in ('rows;DROP_TABLE', 'x.y', 'x"', '_internal'):
            with self.assertRaises(InvalidInput):
                replace(self.spec, model=model(table))
        self.assertEqual(self.registry.query(self.writer, self.spec.key)['total'], 1)

    def test_explicit_pk_unknown_config_and_only_update(self):
        with self.assertRaises(InvalidInput):
            replace(self.spec, model=model(primary=False))
        with self.assertRaises(InvalidInput):
            replace(self.spec, model_config={'code': {'primarykey': True}})
        with self.assertRaises(InvalidInput):
            replace(self.spec, model_config={'unknown': {}})
        with self.assertRaises(InvalidInput):
            replace(self.spec, model_config={'code': {'sql': 'SELECT'}})
        row = self.create()
        registry = MaintenanceRegistry()
        registry.register(replace(self.spec, only_update=True))
        with self.assertRaises(PermissionDenied):
            registry.create(self.writer, self.spec.key, {'code': 'NO'})
        with self.assertRaises(PermissionDenied):
            registry.delete(self.writer, self.spec.key, row['id'], 1)
        self.assertEqual(registry.update(self.writer, self.spec.key, row['id'], 1,
                         {'description': 'Allowed'})['version'], 2)

    def test_version_conflict_duplicate_atomicity_and_recoverable_delete(self):
        row = self.create()
        with self.assertRaises(RecordConflict):
            self.create()
        with self.assertRaises(RecordConflict):
            self.registry.update(self.writer, self.spec.key, row['id'], 2, {'description': 'No'})
        updated = self.registry.update(self.writer, self.spec.key, row['id'], 1, {'description': 'Winner'})
        with self.assertRaises(RecordConflict):
            self.registry.delete(self.writer, self.spec.key, row['id'], 1)
        self.assertEqual(self.registry.get(self.writer, self.spec.key, row['id']), updated)
        self.registry.delete(self.writer, self.spec.key, row['id'], 2)
        replacement_row = self.create()
        self.assertNotEqual(replacement_row['id'], row['id'])

    def test_real_sqlite_trigger_failure_rolls_back_row_and_audit(self):
        row = self.create()
        with closing(sqlite3.connect(self.database.path)) as connection, connection:
            connection.execute("CREATE TRIGGER reject_audit BEFORE INSERT ON maintenance_audit BEGIN SELECT RAISE(ABORT,'private diagnostic'); END")
        with self.assertRaises(RecordConflict) as caught:
            self.registry.update(self.writer, self.spec.key, row['id'], 1, {'description': 'No'})
        self.assertNotIn('private', str(caught.exception))
        self.assertEqual(self.registry.get(self.writer, self.spec.key, row['id'])['version'], 1)
        with closing(sqlite3.connect(self.database.path)) as connection, connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM maintenance_audit').fetchone()[0], 1)

    def test_concurrent_sqlite_writes_have_one_version_winner(self):
        row = self.create()
        other = MaintenanceRegistry()
        other.register(replace(self.spec, adapter=SQLiteMaintenanceAdapter(self.database)))
        barrier = threading.Barrier(2)
        outcomes = []
        def update(registry):
            barrier.wait()
            try:
                registry.update(self.writer, self.spec.key, row['id'], 1, {'description': 'Winner'})
                outcomes.append('committed')
            except RecordConflict:
                outcomes.append('conflict')
        threads = [threading.Thread(target=update, args=(registry,)) for registry in (self.registry, other)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(outcomes, ['committed', 'conflict'])
        self.assertEqual(self.registry.get(self.writer, self.spec.key, row['id'])['version'], 2)

    def test_reopen_and_unavailable_schema_error_are_sanitized(self):
        row = self.create()
        self.registry.close()
        reopened = MaintenanceRegistry()
        reopened.register(replace(self.spec, adapter=SQLiteMaintenanceAdapter(self.database)))
        self.assertEqual(reopened.get(self.writer, self.spec.key, row['id']), row)
        with closing(sqlite3.connect(self.database.path)) as connection, connection:
            connection.execute('ALTER TABLE fixture_rows ADD COLUMN unexpected TEXT')
        with self.assertRaises(StorageUnavailable) as caught:
            reopened.query(self.writer, self.spec.key)
        self.assertNotIn(self.tmp.name, str(caught.exception))

    def test_corrupt_stored_boolean_or_version_is_not_coerced_to_success(self):
        row = self.create()
        with closing(sqlite3.connect(self.database.path)) as connection, connection:
            connection.execute('UPDATE fixture_rows SET enabled=? WHERE id=?', ('not-boolean', row['id']))
        with self.assertRaises(StorageUnavailable):
            self.registry.query(self.writer, self.spec.key)
        with closing(sqlite3.connect(self.database.path)) as connection, connection:
            connection.execute('UPDATE fixture_rows SET enabled=1,version=0 WHERE id=?', (row['id'],))
        with self.assertRaises(StorageUnavailable):
            self.registry.get(self.writer, self.spec.key, row['id'])

    def test_synthetic_alias_accepts_trusted_policy_without_mutating_fixture(self):
        registry = build_synthetic_registry(self.tmp.name, lambda current: MaintenancePolicy(read_roles=('user',), write_roles=()), count=4)
        self.assertTrue(registry.frozen)
        self.assertEqual(len(registry.definitions), 4)
        self.assertEqual(registry.catalog(self.reader)['total'], 4)
        self.assertEqual(registry.catalog(self.writer)['total'], 0)


class FakeQuery:
    def __init__(self, session, filters=None, limit=None, offset=0):
        self.session, self.filters, self.bound, self.start = session, dict(filters or {}), limit, offset
    def filter_by(self, **values):
        return FakeQuery(self.session, dict(self.filters, **values), self.bound, self.start)
    def order_by(self, attribute):
        return self
    def limit(self, value):
        return FakeQuery(self.session, self.filters, value, self.start)
    def offset(self, value):
        return FakeQuery(self.session, self.filters, self.bound, value)
    def _rows(self):
        return [row for row in self.session.rows if all(getattr(row, name) == value for name, value in self.filters.items())]
    def count(self):
        return len(self._rows())
    def all(self):
        rows = self._rows()
        return rows[self.start:self.start + self.bound] if self.bound is not None else rows[self.start:]
    def first(self):
        rows = self._rows()
        return rows[0] if rows else None
    def update(self, changes, synchronize_session):
        self.session.events.append(('cas', dict(self.filters), dict(changes), synchronize_session))
        rows = self._rows()
        for row in rows:
            row.__dict__.update(changes)
        return len(rows)


class FakeSession:
    def __init__(self, database, engine, commit_failure=False):
        self.database, self.engine = database, engine
        self.rows = copy.deepcopy(database.rows)
        self.audit = copy.deepcopy(database.audit)
        self.events = []
        self.commit_failure = commit_failure
    def get_bind(self, mapper):
        self.events.append(('get_bind', mapper.__bind_key__))
        return self.engine
    def query(self, model_class):
        self.events.append(('query', model_class.__tablename__))
        return FakeQuery(self)
    def add(self, row):
        row.id = max([item.id for item in self.rows] or [0]) + 1
        self.rows.append(row)
        self.events.append(('add', row.org))
    def flush(self):
        self.events.append(('flush',))
    def expire_all(self):
        self.events.append(('expire_all',))
    def commit(self):
        self.events.append(('commit',))
        if self.commit_failure:
            raise RuntimeError('private Oracle URL and credentials must never leak')
        self.database.rows = copy.deepcopy(self.rows)
        self.database.audit = copy.deepcopy(self.audit)
    def rollback(self):
        self.events.append(('rollback',))
    def close(self):
        self.events.append(('close',))


class FakeInitDB:
    def __init__(self, bind='oracle-contract'):
        self.bind = bind
        self.engine = SimpleNamespace(dialect=SimpleNamespace(name='oracle'))
        self.rows, self.audit, self.calls, self.sessions = [], [], [], []
        self.mismatch, self.commit_failure = False, False
    def get_engine(self, bind):
        self.calls.append(bind)
        if bind != self.bind:
            raise RuntimeError('unknown configured bind')
        return self.engine
    def new_session(self, definition):
        engine = SimpleNamespace(dialect=SimpleNamespace(name='oracle')) if self.mismatch else self.engine
        session = FakeSession(self, engine, self.commit_failure)
        self.sessions.append(session)
        return session

    def audit_hook(self, session, definition, event):
        session.audit.append(dict(event))
        session.events.append(('audit', event['action']))


class OracleSessionContractTests(unittest.TestCase):
    def setUp(self):
        self.database = FakeInitDB()
        extra = tuple(SimpleNamespace(name=name, type=type(kind, (), {})(), primary_key=False,
                                     nullable=False, unique=False)
                      for name, kind in (('org', 'String'), ('version', 'Integer'), ('deleted', 'Boolean')))
        self.spec = MaintenanceDefinition('oracle.contract', 'Fake session contract only',
            model(bind='oracle-contract', extra=extra), self.database, 'oracle-contract', CONFIG,
            lambda current: MaintenancePolicy(), ORMMaintenanceAdapter(
                session_factory=self.database.new_session, audit_hook=self.database.audit_hook),
            backend='oracle')
        self.registry = MaintenanceRegistry()
        self.registry.register(self.spec)
        self.writer = user()

    def test_no_io_during_registration_catalog_and_description(self):
        self.assertEqual(self.registry.catalog(self.writer)['total'], 1)
        self.assertEqual(self.registry.describe(self.writer, self.spec.key)['backend'], 'oracle')
        self.assertEqual(self.database.calls, [])
        self.assertEqual(self.database.sessions, [])

    def test_fake_oracle_contract_create_read_cas_delete_and_session_cleanup(self):
        row = self.registry.create(self.writer, self.spec.key, {'code': 'FAKE-ORACLE'})
        self.assertEqual(row['version'], 1)
        self.assertEqual(self.registry.query(user('reader', 'user'), self.spec.key, limit=1)['total'], 1)
        changed = self.registry.update(self.writer, self.spec.key, row['id'], 1, {'description': 'Updated'})
        self.assertEqual(changed['version'], 2)
        cas = [event for event in self.database.sessions[-1].events if event[0] == 'cas'][0]
        self.assertEqual(cas[1], {'org': 'A', 'deleted': False, 'id': row['id'], 'version': 1})
        self.assertEqual(cas[3], False)
        deleted = self.registry.delete(self.writer, self.spec.key, row['id'], 2)
        self.assertTrue(deleted['deleted'])
        self.assertTrue(self.database.rows[0].deleted)  # recoverable, not removed
        self.assertEqual(self.registry.query(self.writer, self.spec.key)['total'], 0)
        self.assertTrue(all(session.events[-1] == ('close',) for session in self.database.sessions))
        self.assertTrue(all(bind == 'oracle-contract' for bind in self.database.calls))
        self.assertEqual([event['action'] for event in self.database.audit], ['create', 'update', 'delete'])
        self.assertNotIn('FAKE-ORACLE', str(self.database.audit))
        self.assertNotIn('Updated', str(self.database.audit))
        for session in self.database.sessions:
            actions = [event[0] for event in session.events]
            if 'commit' in actions:
                self.assertLess(actions.index('audit'), actions.index('commit'))

    def test_fake_oracle_tenant_and_policy_checks_precede_session_use(self):
        row = self.registry.create(self.writer, self.spec.key, {'code': 'FAKE'})
        self.assertEqual(self.registry.query(user(org='B'), self.spec.key)['total'], 0)
        with self.assertRaises(RecordNotFound):
            self.registry.get(user(org='B'), self.spec.key, row['id'])
        calls = len(self.database.calls)
        with self.assertRaises(PermissionDenied):
            self.registry.update(user(role='user'), self.spec.key, row['id'], 1, {'description': 'No'})
        self.assertEqual(len(self.database.calls), calls)
        with self.assertRaises(RecordConflict):
            self.registry.update(self.writer, self.spec.key, row['id'], 2, {'description': 'No'})
        self.assertEqual(self.database.rows[0].version, 1)
        self.assertIn(('rollback',), self.database.sessions[-1].events)

    def test_fake_oracle_wrong_engine_cannot_query_or_commit(self):
        self.database.mismatch = True
        with self.assertRaises(BindingMismatch):
            self.registry.create(self.writer, self.spec.key, {'code': 'NO'})
        events = self.database.sessions[-1].events
        self.assertFalse(any(event[0] in ('query', 'add', 'commit') for event in events))
        self.assertIn(('rollback',), events)
        self.assertEqual(events[-1], ('close',))

    def test_fake_oracle_commit_failure_does_not_report_success_or_leak(self):
        self.database.commit_failure = True
        with self.assertRaises(StorageUnavailable) as caught:
            self.registry.create(self.writer, self.spec.key, {'code': 'NO'})
        self.assertNotIn('credentials', str(caught.exception))
        self.assertEqual(self.database.rows, [])
        self.assertEqual(self.database.audit, [])
        self.assertIn(('rollback',), self.database.sessions[-1].events)
        self.assertEqual(self.database.sessions[-1].events[-1], ('close',))

    def test_fake_oracle_missing_tenant_contract_is_explicitly_unavailable(self):
        registry = MaintenanceRegistry()
        registry.register(replace(self.spec, model=model(bind='oracle-contract')))
        with self.assertRaises(AdapterUnavailable):
            registry.query(self.writer, self.spec.key)
        self.assertEqual(self.database.calls, [])

    def test_fake_oracle_without_audit_hook_rejects_writes_before_session(self):
        registry = MaintenanceRegistry()
        registry.register(replace(self.spec, adapter=ORMMaintenanceAdapter(session_factory=self.database.new_session)))
        self.assertEqual(registry.query(self.writer, self.spec.key)['total'], 0)
        self.database.calls.clear()
        with self.assertRaises(AdapterUnavailable):
            registry.create(self.writer, self.spec.key, {'code': 'NO'})
        self.assertEqual(self.database.calls, [])

    def test_fake_oracle_audit_failure_rolls_back_data_and_audit(self):
        def fail(session, definition, event):
            session.audit.append(event)
            raise RuntimeError('private audit failure')
        registry = MaintenanceRegistry()
        registry.register(replace(self.spec, adapter=ORMMaintenanceAdapter(
            session_factory=self.database.new_session, audit_hook=fail)))
        with self.assertRaises(StorageUnavailable):
            registry.create(self.writer, self.spec.key, {'code': 'NO'})
        self.assertEqual(self.database.rows, [])
        self.assertEqual(self.database.audit, [])
        self.assertFalse(any(event[0] == 'commit' for event in self.database.sessions[-1].events))


if __name__ == '__main__':
    unittest.main()
