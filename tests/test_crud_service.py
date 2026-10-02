"""Real local SQLite metadata CRUD, authorization and atomicity regressions."""

import hashlib
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from reporting_workspace.crud import (
    AccessDenied, Conflict, CrudError, NotFound, ReportDefinitions,
    StateUnavailable, ValidationError,
)
from reporting_workspace.state import StateStore


USER = dict(id='owner@example.test', role='user', org='A')
PEER = dict(id='peer@example.test', role='user', org='A')
ADMIN = dict(id='admin@example.test', role='admin', org='A')
OTHER_USER = dict(id=USER['id'], role='user', org='B')
OTHER_ADMIN = dict(id=ADMIN['id'], role='admin', org='B')
CREATE_KEY = '1234567890abcdef1234567890abcdef'


def _competing_update(path, identifier, name, start, output):
    try:
        service = ReportDefinitions(StateStore(path))
        if not start.wait(15):
            raise RuntimeError('start signal missing')
        row = service.update(USER, identifier, 1, {'name': name})
        output.put(('updated', row['name'], row['version']))
    except Conflict:
        output.put(('conflict', None, None))
    except BaseException as error:
        output.put(('error', type(error).__name__, str(error)))


def _competing_create(path, name, request_key, start, output):
    try:
        service = ReportDefinitions(StateStore(path))
        if not start.wait(15):
            raise RuntimeError('start signal missing')
        row = service.create(USER, {'name': name}, request_key=request_key)
        output.put(('created', row['id'], row['name']))
    except Conflict:
        output.put(('conflict', None, None))
    except BaseException as error:
        output.put(('error', type(error).__name__, str(error)))


class CrudServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'state.sqlite'
        self.store = StateStore(self.path)
        self.service = ReportDefinitions(self.store)

    def create(self, user=USER, **fields):
        values = {'name': 'Daily totals'}
        values.update(fields)
        return self.service.create(user, values)

    def test_create_defaults_trim_server_owned_fields_and_persistence(self):
        with patch('reporting_workspace.crud.time.time', return_value=100):
            row = self.create(name='  Daily totals  ', description='  Team summary  ')
        self.assertEqual(set(row), {'id', 'org', 'owner_id', 'name', 'description', 'cadence',
                                    'enabled', 'version', 'created_at', 'updated_at', 'deleted_at'})
        self.assertRegex(row['id'], '^[0-9a-f]{32}$')
        self.assertEqual(row['org'], 'A')
        self.assertEqual(row['owner_id'], hashlib.sha256(USER['id'].encode('utf-8')).hexdigest())
        self.assertEqual(row['name'], 'Daily totals')
        self.assertEqual(row['description'], 'Team summary')
        self.assertEqual(row['cadence'], 'manual')
        self.assertIs(row['enabled'], True)
        self.assertEqual(row['version'], 1)
        self.assertEqual(row['created_at'], 100)
        self.assertEqual(row['updated_at'], 100)
        self.assertIsNone(row['deleted_at'])
        self.assertEqual(ReportDefinitions(StateStore(self.path)).get(USER, row['id']), row)
        self.assertEqual(len(self.store.list_audit()), 1)

    def test_same_create_key_replays_canonical_payload_without_duplicate_audit(self):
        with patch('reporting_workspace.crud.time.time', return_value=100):
            created = self.service.create(USER, {'name': '  Stable draft  '},
                                          request_key=CREATE_KEY, request_id='first-request')
        # Defaults, whitespace and key ordering normalize to the same initial payload.
        with patch('reporting_workspace.crud.time.time', return_value=200):
            replayed = self.service.create(USER,
                {'enabled': True, 'description': '  ', 'cadence': ' manual ', 'name': 'Stable draft'},
                request_key=CREATE_KEY, request_id='second-request')
        self.assertEqual(replayed, created)
        self.assertEqual(replayed['updated_at'], 100)
        self.assertEqual(self.service.list(USER)['total'], 1)
        self.assertEqual(len(self.store.list_audit()), 1)
        self.assertEqual(self.store.list_audit()[0].request_id, 'first-request')
        restarted = ReportDefinitions(StateStore(self.path))
        self.assertEqual(restarted.create(USER, {'name': 'Stable draft'}, request_key=CREATE_KEY), created)
        self.assertEqual(len(self.store.list_audit()), 1)

    def test_create_key_reuse_with_different_payload_is_conflict(self):
        created = self.service.create(USER, {'name': 'Original'}, request_key=CREATE_KEY)
        for changed in ({'name': 'Different'}, {'name': 'Original', 'description': 'changed'},
                        {'name': 'Original', 'cadence': 'daily'}, {'name': 'Original', 'enabled': False}):
            with self.subTest(changed=changed), self.assertRaises(Conflict):
                self.service.create(USER, changed, request_key=CREATE_KEY)
        self.assertEqual(self.service.get(USER, created['id']), created)
        self.assertEqual(self.service.list(USER)['total'], 1)
        self.assertEqual(len(self.store.list_audit()), 1)

    def test_create_key_reuse_after_edit_archive_or_restore_is_conflict(self):
        for index, action in enumerate(('edit', 'archive', 'restore')):
            key = '{:032x}'.format(index)
            payload = {'name': action}
            created = self.service.create(USER, payload, request_key=key)
            if action == 'edit':
                # Even a no-op update advances the version and closes the replay window.
                current = self.service.update(USER, created['id'], 1, payload)
            else:
                current = self.service.soft_delete(USER, created['id'], 1)
                if action == 'restore':
                    current = self.service.restore(USER, created['id'], 2)
            audits_before = len(self.store.list_audit())
            with self.subTest(action=action), self.assertRaises(Conflict):
                self.service.create(USER, payload, request_key=key)
            self.assertEqual(self.service.get(USER, created['id']), current)
            self.assertEqual(len(self.store.list_audit()), audits_before)
        self.assertEqual(self.service.list(USER, include_deleted=True)['total'], 3)

    def test_create_key_scope_is_org_and_owner_and_new_keys_create_independent_records(self):
        rows = [self.service.create(user, {'name': 'Same name'}, request_key=CREATE_KEY)
                for user in (USER, PEER, ADMIN, OTHER_USER)]
        self.assertEqual(len({row['id'] for row in rows}), 4)
        additional = self.service.create(USER, {'name': 'Same name'}, request_key='f' * 32)
        self.assertNotIn(additional['id'], {row['id'] for row in rows})
        # A role change for the same authenticated ID does not change the owner scope.
        self.assertEqual(self.service.create(dict(USER, role='admin'), {'name': 'Same name'},
                                             request_key=CREATE_KEY), rows[0])
        self.assertEqual(self.service.list(USER)['total'], 4)
        self.assertEqual(self.service.list(OTHER_USER)['total'], 1)
        self.assertEqual(len(self.store.list_audit()), 5)

    def test_omitted_or_none_create_keys_preserve_independent_create_intent(self):
        rows = [self.create(name='Same name'), self.create(name='Same name'),
                self.service.create(USER, {'name': 'Same name'}, request_key=None)]
        self.assertEqual(len({row['id'] for row in rows}), 3)
        with sqlite3.connect(str(self.path)) as connection:
            keys = [row[0] for row in connection.execute('SELECT create_key FROM report_definitions')]
        self.assertEqual(len(set(keys)), 3)
        for key in keys:
            self.assertRegex(key, '^[0-9a-f]{32}$')
        self.assertEqual(len(self.store.list_audit()), 3)

    def test_create_key_has_strict_bounded_format(self):
        for key in ('', 'a' * 31, 'a' * 33, 'A' * 32, 'g' * 32, ' ' + CREATE_KEY,
                    CREATE_KEY + ' ', CREATE_KEY + '\n', True, 1, {}, [], '\ud800' * 32):
            with self.subTest(key=repr(key)), self.assertRaises(ValidationError):
                self.service.create(USER, {'name': 'invalid'}, request_key=key)
        self.assertEqual(self.service.list(USER)['total'], 0)
        self.assertEqual(self.store.list_audit(), [])

    def test_internal_create_key_never_appears_in_service_records_or_audit(self):
        created = self.service.create(USER, {'name': 'Draft'}, request_key=CREATE_KEY)
        records = [created, self.service.create(USER, {'name': 'Draft'}, request_key=CREATE_KEY),
                   self.service.get(USER, created['id']), self.service.list(USER)['items'][0],
                   self.service.update(USER, created['id'], 1, {'name': 'Changed'}),
                   self.service.soft_delete(USER, created['id'], 2),
                   self.service.restore(USER, created['id'], 3)]
        for record in records:
            self.assertNotIn('create_key', record)
            self.assertNotIn('request_key', record)
            self.assertNotIn(CREATE_KEY, repr(record))
        self.assertNotIn(CREATE_KEY, repr(self.store.list_audit()))
        with sqlite3.connect(str(self.path)) as connection:
            self.assertEqual(connection.execute('SELECT create_key FROM report_definitions').fetchone()[0], CREATE_KEY)

    def test_failed_create_audit_does_not_reserve_key_and_retry_can_succeed(self):
        with patch.object(self.store, '_audit', side_effect=sqlite3.OperationalError('write failure')):
            with self.assertRaises(StateUnavailable):
                self.service.create(USER, {'name': 'Retry'}, request_key=CREATE_KEY)
        self.assertEqual(self.service.list(USER)['total'], 0)
        self.assertEqual(self.store.list_audit(), [])
        created = self.service.create(USER, {'name': 'Retry'}, request_key=CREATE_KEY)
        self.assertEqual(self.service.create(USER, {'name': 'Retry'}, request_key=CREATE_KEY), created)
        self.assertEqual(self.service.list(USER)['total'], 1)
        self.assertEqual(len(self.store.list_audit()), 1)

    def test_database_unique_constraint_independently_enforces_create_key_scope(self):
        self.service.create(USER, {'name': 'Draft'}, request_key=CREATE_KEY)
        with sqlite3.connect(str(self.path)) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    'INSERT INTO report_definitions '
                    'SELECT ?, org, owner_id, create_key, name, description, cadence, enabled, '
                    'version, created_at, updated_at, deleted_at FROM report_definitions', ('f' * 32,))
        self.assertEqual(self.service.list(USER)['total'], 1)
        self.assertEqual(len(self.store.list_audit()), 1)

    def _create_competition(self, names):
        context = multiprocessing.get_context('spawn')
        start, output = context.Event(), context.Queue()
        processes = [context.Process(target=_competing_create,
                                     args=(str(self.path), name, CREATE_KEY, start, output))
                     for name in names]
        for process in processes:
            process.start()
        try:
            start.set()
            results = [output.get(timeout=30) for _ in processes]
            for process in processes:
                process.join(20)
                self.assertEqual(process.exitcode, 0)
            return results
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(5)
            output.close()
            output.join_thread()

    def test_two_process_same_create_key_returns_one_record_with_one_audit(self):
        results = self._create_competition(('Shared draft', 'Shared draft'))
        self.assertEqual([result[0] for result in results], ['created', 'created'], results)
        self.assertEqual(results[0], results[1])
        persisted = ReportDefinitions(StateStore(self.path)).list(USER)
        self.assertEqual(persisted['total'], 1)
        self.assertEqual(persisted['items'][0]['id'], results[0][1])
        self.assertEqual(persisted['items'][0]['version'], 1)
        self.assertEqual([event.event for event in self.store.list_audit()], ['crud.created'])

    def test_two_process_same_key_different_payload_one_conflicts_without_second_audit(self):
        results = self._create_competition(('First draft', 'Second draft'))
        self.assertEqual(sorted(result[0] for result in results), ['conflict', 'created'], results)
        winner = next(result for result in results if result[0] == 'created')
        persisted = ReportDefinitions(StateStore(self.path)).list(USER)
        self.assertEqual(persisted['total'], 1)
        self.assertEqual(persisted['items'][0]['id'], winner[1])
        self.assertEqual(persisted['items'][0]['name'], winner[2])
        self.assertEqual([event.event for event in self.store.list_audit()], ['crud.created'])

    def test_partial_update_preserves_identity_and_increments_version(self):
        row = self.create(description='unchanged', cadence='weekly')
        original = dict(row)
        with patch('reporting_workspace.crud.time.time', return_value=row['created_at'] + 1):
            changed = self.service.update(USER, row['id'], 1, {'name': ' New name ', 'enabled': False})
        for key in ('id', 'org', 'owner_id', 'created_at', 'description', 'cadence', 'deleted_at'):
            self.assertEqual(changed[key], original[key])
        self.assertEqual(changed['name'], 'New name')
        self.assertIs(changed['enabled'], False)
        self.assertEqual(changed['version'], 2)
        self.assertGreater(changed['updated_at'], original['updated_at'])
        self.assertEqual(row, original)

    def test_same_org_can_read_all_but_only_owner_or_admin_can_write(self):
        row = self.create()
        for reader in (USER, PEER, ADMIN):
            self.assertEqual(self.service.get(reader, row['id']), row)
            self.assertEqual(self.service.list(reader)['items'], [row])
        for method in (
                lambda: self.service.update(PEER, row['id'], 1, {'name': 'forbidden'}),
                lambda: self.service.soft_delete(PEER, row['id'], 1),
                lambda: self.service.restore(PEER, row['id'], 1)):
            with self.assertRaises(AccessDenied):
                method()
        changed = self.service.update(ADMIN, row['id'], 1, {'description': 'admin changed'})
        self.assertEqual(changed['owner_id'], row['owner_id'])
        deleted = self.service.soft_delete(ADMIN, row['id'], 2)
        with self.assertRaises(AccessDenied):
            self.service.restore(PEER, row['id'], 3)
        restored = self.service.restore(ADMIN, row['id'], 3)
        self.assertIsNotNone(deleted['deleted_at'])
        self.assertIsNone(restored['deleted_at'])
        self.assertEqual(self.service.update(USER, row['id'], 4, {'cadence': 'daily'})['version'], 5)

    def test_cross_org_is_indistinguishable_from_missing_even_for_admin_and_same_id(self):
        row = self.create()
        unknown = '0' * 32
        for user in (OTHER_USER, OTHER_ADMIN):
            self.assertEqual(self.service.list(user, include_deleted=True)['total'], 0)
            for identifier in (row['id'], unknown, 'invalid', "' OR 1=1 --", None):
                operations = (
                    lambda: self.service.get(user, identifier),
                    lambda: self.service.update(user, identifier, 1, {'name': 'forbidden'}),
                    lambda: self.service.soft_delete(user, identifier, 1),
                    lambda: self.service.restore(user, identifier, 1),
                )
                for operation in operations:
                    with self.assertRaisesRegex(NotFound, '^Report definition not found.$'):
                        operation()
        self.assertEqual(self.service.get(USER, row['id']), row)
        self.assertEqual(len(self.store.list_audit()), 1)

    def test_identity_claims_are_not_trimmed_or_merged(self):
        row = self.create()
        alternate = dict(USER, org=' A ')
        self.assertEqual(self.service.list(alternate)['items'], [])
        with self.assertRaises(NotFound):
            self.service.get(alternate, row['id'])
        second = self.create(user=dict(USER, id=' ' + USER['id']))
        self.assertNotEqual(second['owner_id'], row['owner_id'])
        with self.assertRaises(AccessDenied):
            self.service.update(USER, second['id'], 1, {'name': 'forbidden'})

    def test_invalid_identity_and_role_fail_closed_everywhere(self):
        row = self.create()
        users = [None, {}, [], 'user', dict(USER, role='superadmin'), dict(USER, role='USER'),
                 dict(USER, org=''), dict(USER, org='A' * 129), dict(USER, id='x' * 256),
                 dict(USER, id='\ud800'), dict(USER, id='x\x00'), dict(USER, org='   ')]
        for user in users:
            with self.subTest(user=user):
                operations = (
                    lambda: self.service.list(user), lambda: self.service.get(user, row['id']),
                    lambda: self.service.create(user, {'name': 'forbidden'}),
                    lambda: self.service.update(user, row['id'], 1, {'name': 'forbidden'}),
                    lambda: self.service.soft_delete(user, row['id'], 1),
                    lambda: self.service.restore(user, row['id'], 1),
                )
                for operation in operations:
                    with self.assertRaises(AccessDenied):
                        operation()
        self.assertEqual(len(self.store.list_audit()), 1)

    def test_server_owned_and_unknown_fields_rejected_without_mutation(self):
        row = self.create()
        for field, value in (
                ('id', '0' * 32), ('org', 'B'), ('owner_id', '0' * 64), ('version', 1),
                ('created_at', 0), ('updated_at', 0), ('deleted_at', None), ('sql', 'DROP TABLE leases'),
                ('expression', '1+1'), ('path', '/tmp/data'), ('smtp', {}), ('job', 'run'),
                ('role', 'admin'), ('request_id', 'override'), ('create_key', CREATE_KEY),
                ('request_key', CREATE_KEY)):
            payload = {'name': 'New', field: value}
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    self.service.create(USER, payload)
                with self.assertRaises(ValidationError):
                    self.service.update(USER, row['id'], 1, payload)
        self.assertEqual(self.service.get(USER, row['id']), row)
        self.assertEqual(len(self.store.list_audit()), 1)

    def test_payload_type_lengths_boolean_and_cadence_validation(self):
        row = self.create()
        bad = [None, [], 'name', {}, {'name': ''}, {'name': '  '}, {'name': 'a' * 121},
               {'name': 1}, {'name': 'line\nbreak'}, {'name': '\ud800'}, {'name': 'a\x00b'},
               {'description': 'x' * 1001}, {'description': None}, {'description': '\ud800'},
               {'enabled': 1}, {'enabled': 0}, {'enabled': 'true'}, {'enabled': None},
               {'cadence': 'hourly'}, {'cadence': []}, {'cadence': 'DAILY'}]
        for payload in bad:
            with self.subTest(payload=repr(payload)[:100]), self.assertRaises(ValidationError):
                self.service.update(USER, row['id'], 1, payload)
        for payload in (None, {}, [], {'description': 'missing name'}):
            with self.assertRaises(ValidationError):
                self.service.create(USER, payload)
        for cadence in ('manual', 'daily', 'weekly', 'monthly'):
            self.assertEqual(self.create(cadence=' ' + cadence + ' ')['cadence'], cadence)
        maximum = self.create(name='n' * 120, description='d' * 1000, enabled=False)
        self.assertEqual(len(maximum['name']), 120)
        self.assertEqual(len(maximum['description']), 1000)
        self.assertIs(maximum['enabled'], False)
        multiline = self.create(description='Line one\n\tLine two')
        self.assertEqual(multiline['description'], 'Line one\n\tLine two')

    def test_input_mappings_are_not_changed(self):
        user, payload = dict(USER), {'name': '  Name  ', 'cadence': ' daily '}
        row = self.service.create(user, payload)
        self.assertEqual(user, USER)
        self.assertEqual(payload, {'name': '  Name  ', 'cadence': ' daily '})
        update = {'description': '  New  '}
        self.service.update(user, row['id'], 1, update)
        self.assertEqual(update, {'description': '  New  '})

    def test_expected_version_is_strict_and_conflicts_never_mutate_or_audit(self):
        row = self.create()
        for version in (None, True, False, 0, -1, 1.0, '1', 9223372036854775808):
            for operation in (
                    lambda: self.service.update(USER, row['id'], version, {'name': 'invalid'}),
                    lambda: self.service.soft_delete(USER, row['id'], version),
                    lambda: self.service.restore(USER, row['id'], version)):
                with self.assertRaises(ValidationError):
                    operation()
        changed = self.service.update(USER, row['id'], 1, {'name': 'changed'})
        for operation in (
                lambda: self.service.update(USER, row['id'], 1, {'name': 'stale'}),
                lambda: self.service.soft_delete(USER, row['id'], 1),
                lambda: self.service.restore(USER, row['id'], 1)):
            with self.assertRaises(Conflict):
                operation()
        self.assertEqual(self.service.get(USER, row['id']), changed)
        self.assertEqual(len(self.store.list_audit()), 2)

    def test_soft_delete_restore_lifecycle_and_restart_without_hard_delete(self):
        row = self.create(enabled=False)
        with self.assertRaises(Conflict):
            self.service.restore(USER, row['id'], 1)
        deleted = self.service.soft_delete(USER, row['id'], 1)
        self.assertEqual(deleted['version'], 2)
        self.assertIsNotNone(deleted['deleted_at'])
        service = ReportDefinitions(StateStore(self.path))
        self.assertEqual(service.get(USER, row['id']), deleted)
        self.assertEqual(service.list(USER)['items'], [])
        self.assertEqual(service.list(USER, include_deleted=True)['items'], [deleted])
        with self.assertRaises(Conflict):
            service.soft_delete(USER, row['id'], 2)
        with self.assertRaises(Conflict):
            service.update(USER, row['id'], 2, {'name': 'blocked'})
        with self.assertRaises(Conflict):
            service.restore(USER, row['id'], 1)
        restored = service.restore(USER, row['id'], 2)
        self.assertEqual(restored['version'], 3)
        self.assertIsNone(restored['deleted_at'])
        self.assertEqual(restored['created_at'], row['created_at'])
        self.assertIs(restored['enabled'], False)
        self.assertEqual(service.list(USER)['items'], [restored])
        with sqlite3.connect(str(self.path)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM report_definitions').fetchone()[0], 1)

    def test_literal_injection_and_wildcard_search_are_safe_metadata(self):
        injection = "Robert'); DROP TABLE leases; --"
        literal = self.create(name=injection, description='<script>alert(1)</script> 100% _total \\path')
        self.create(name='another record', description='1000 total')
        for query in ("Robert');", '100%', '_total', '\\path', '<script>'):
            page = self.service.list(USER, q=query)
            self.assertEqual(page['items'], [literal])
            self.assertEqual(page['total'], 1)
        self.assertEqual(self.service.list(USER, q="%' OR 1=1 --")['total'], 0)
        self.assertEqual(self.service.get(USER, literal['id'])['name'], injection)
        self.assertIsNotNone(self.store.acquire('still-exists', 'worker'))

    def test_list_bounds_and_types(self):
        for kwargs in ({'q': None}, {'q': 'x' * 121}, {'q': '\ud800'}, {'q': 'x\x00'},
                       {'limit': 0}, {'limit': 101}, {'limit': True}, {'limit': 1.0}, {'limit': '1'},
                       {'offset': -1}, {'offset': 100001}, {'offset': True}, {'offset': 1.0},
                       {'offset': '0'}, {'include_deleted': 1}, {'include_deleted': 'false'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                self.service.list(USER, **kwargs)
        self.assertEqual(self.service.list(USER, limit=100, offset=100000)['items'], [])
        self.assertEqual(self.service.list(USER, q=' ' * 20)['total'], 0)

    def test_filtered_total_pagination_and_fixed_deterministic_sort(self):
        with patch('reporting_workspace.crud.time.time', return_value=100):
            first, second = self.create(name='matched one'), self.create(name='matched two')
        with patch('reporting_workspace.crud.time.time', return_value=101):
            third = self.create(name='matched three')
        self.create(name='unrelated')
        self.create(user=OTHER_USER, name='matched foreign')
        expected = [third] + sorted([first, second], key=lambda row: row['id'])
        page = self.service.list(USER, q=' matched ', limit=2)
        self.assertEqual(page, {'items': expected[:2], 'total': 3, 'limit': 2, 'offset': 0})
        self.assertEqual(self.service.list(USER, q='matched', limit=2, offset=2)['items'], expected[2:])
        self.assertEqual(self.service.list(USER, q='matched', offset=3)['items'], [])
        self.service.soft_delete(USER, third['id'], 1)
        self.assertEqual(self.service.list(USER, q='matched')['total'], 2)
        self.assertEqual(self.service.list(USER, q='matched', include_deleted=True)['total'], 3)

    def test_audit_is_fixed_metadata_and_hashes_actor(self):
        row = self.service.create(USER, {'name': 'SECRET NAME', 'description': 'SECRET DESCRIPTION'},
                                  request_id='req-123')
        self.service.update(ADMIN, row['id'], 1, {'cadence': 'monthly'}, request_id='req-124')
        self.service.soft_delete(USER, row['id'], 2)
        self.service.restore(USER, row['id'], 3)
        events = self.store.list_audit()
        self.assertEqual([event.event for event in events],
                         ['crud.created', 'crud.updated', 'crud.deleted', 'crud.restored'])
        self.assertEqual(events[0].request_id, 'req-123')
        self.assertEqual(events[1].actor_id, hashlib.sha256(ADMIN['id'].encode('utf-8')).hexdigest())
        self.assertEqual([event.resource_id for event in events], [row['id']] * 4)
        self.assertTrue(all(event.outcome == 'success' and event.code is None for event in events))
        for forbidden in ('SECRET NAME', 'SECRET DESCRIPTION', USER['id'], ADMIN['id']):
            self.assertNotIn(forbidden, repr(events))
        for event in ('crud.created', 'crud.updated', 'crud.deleted', 'crud.restored'):
            self.store.record_audit(event)
        with self.assertRaises(ValueError):
            self.store.record_audit('crud.NAME')

    def test_invalid_request_ids_rejected_without_mutation(self):
        row = self.create()
        for request_id in ('', 'name@example.test', 'x' * 129, '\n', 1, {}):
            for operation in (
                    lambda: self.service.create(USER, {'name': 'invalid'}, request_id=request_id),
                    lambda: self.service.update(USER, row['id'], 1, {'name': 'invalid'}, request_id=request_id),
                    lambda: self.service.soft_delete(USER, row['id'], 1, request_id=request_id),
                    lambda: self.service.restore(USER, row['id'], 1, request_id=request_id)):
                with self.assertRaises(ValidationError):
                    operation()
        self.assertEqual(self.service.get(USER, row['id']), row)
        self.assertEqual(len(self.store.list_audit()), 1)

    def test_create_and_audit_failure_roll_back_together(self):
        with patch.object(self.store, '_audit', side_effect=sqlite3.OperationalError('SECRET backend details')):
            with self.assertRaisesRegex(StateUnavailable, '^Durable state is unavailable.$') as caught:
                self.create()
        self.assertNotIn('SECRET', str(caught.exception))
        self.assertEqual(self.service.list(USER)['total'], 0)
        self.assertEqual(self.store.list_audit(), [])
        self.assertEqual(self.create()['version'], 1)

    def test_update_delete_restore_audit_failures_roll_back_whole_mutation(self):
        row = self.create()
        with patch.object(self.store, '_audit', side_effect=sqlite3.OperationalError('write failure')):
            with self.assertRaises(StateUnavailable):
                self.service.update(USER, row['id'], 1, {'name': 'must rollback'})
            with self.assertRaises(StateUnavailable):
                self.service.soft_delete(USER, row['id'], 1)
        self.assertEqual(self.service.get(USER, row['id']), row)
        self.assertEqual(len(self.store.list_audit()), 1)
        deleted = self.service.soft_delete(USER, row['id'], 1)
        with patch.object(self.store, '_audit', side_effect=sqlite3.OperationalError('write failure')):
            with self.assertRaises(StateUnavailable):
                self.service.restore(USER, row['id'], 2)
        self.assertEqual(ReportDefinitions(StateStore(self.path)).get(USER, row['id']), deleted)
        self.assertEqual(len(self.store.list_audit()), 2)

    def test_state_unavailable_fails_closed_without_fallback(self):
        disabled = ReportDefinitions(None)
        with self.assertRaises(StateUnavailable):
            disabled.list(USER)
        with self.assertRaises(StateUnavailable):
            disabled.create(USER, {'name': 'not persisted'})
        row = self.create()
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute('PRAGMA user_version = 3')
        for operation in (
                lambda: self.service.list(USER), lambda: self.service.get(USER, row['id']),
                lambda: self.create(), lambda: self.service.update(USER, row['id'], 1, {'name': 'bad'}),
                lambda: self.service.soft_delete(USER, row['id'], 1),
                lambda: self.service.restore(USER, row['id'], 1)):
            with self.assertRaises(StateUnavailable):
                operation()
        with sqlite3.connect(str(self.path)) as connection:
            self.assertEqual(connection.execute('SELECT version FROM report_definitions').fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0], 1)

    def test_schema_tamper_and_removed_database_are_unavailable(self):
        row = self.create()
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute('DROP INDEX report_definitions_org_updated')
        with self.assertRaises(StateUnavailable):
            self.service.update(USER, row['id'], 1, {'name': 'blocked'})
        self.path.unlink()
        with self.assertRaises(StateUnavailable):
            self.service.get(USER, row['id'])
        self.assertFalse(self.path.exists())

    def test_version_exhaustion_does_not_overflow_or_mutate(self):
        row = self.create()
        maximum = 9223372036854775807
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute('UPDATE report_definitions SET version = ? WHERE id = ?', (maximum, row['id']))
        for operation in (
                lambda: self.service.update(USER, row['id'], maximum, {'name': 'overflow'}),
                lambda: self.service.soft_delete(USER, row['id'], maximum)):
            with self.assertRaises(Conflict):
                operation()
        self.assertEqual(self.service.get(USER, row['id'])['version'], maximum)
        self.assertEqual(len(self.store.list_audit()), 1)

    def test_two_processes_same_version_exactly_one_wins(self):
        row = self.create()
        context = multiprocessing.get_context('spawn')
        start, output = context.Event(), context.Queue()
        processes = [context.Process(target=_competing_update,
                                     args=(str(self.path), row['id'], name, start, output))
                     for name in ('first contender', 'second contender')]
        for process in processes:
            process.start()
        try:
            start.set()
            results = [output.get(timeout=30) for _ in processes]
            for process in processes:
                process.join(20)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(sorted(result[0] for result in results), ['conflict', 'updated'], results)
            winner = next(result for result in results if result[0] == 'updated')
            persisted = ReportDefinitions(StateStore(self.path)).get(USER, row['id'])
            self.assertEqual(persisted['name'], winner[1])
            self.assertEqual(persisted['version'], 2)
            self.assertEqual([event.event for event in self.store.list_audit()], ['crud.created', 'crud.updated'])
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(5)
            output.close()
            output.join_thread()

    def test_backup_preserves_report_definition_metadata_and_deletion_state(self):
        row = self.create()
        deleted = self.service.soft_delete(USER, row['id'], 1)
        backup_path = Path(self.directory.name) / 'backup.sqlite'
        self.store.backup_to(backup_path)
        backup = ReportDefinitions(StateStore(backup_path))
        self.assertEqual(backup.get(USER, row['id']), deleted)
        self.assertEqual(backup.list(USER)['total'], 0)
        backup.restore(USER, row['id'], 2)
        self.assertIsNotNone(self.service.get(USER, row['id'])['deleted_at'])

    def test_all_domain_errors_have_shared_base(self):
        for kind in (AccessDenied, Conflict, NotFound, ValidationError, StateUnavailable):
            self.assertTrue(issubclass(kind, CrudError))


if __name__ == '__main__':
    unittest.main()
