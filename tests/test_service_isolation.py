"""Synthetic regressions for current service authority and exact legacy actors."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from demo_services import AccessDenied
from reporting_workspace.application import Runtime
from reporting_workspace.config import Settings
from reporting_workspace.crud import AccessDenied as DefinitionAccessDenied
from reporting_workspace.crud import Conflict, NotFound, ReportDefinitions, StateUnavailable
from reporting_workspace.etl_dispatch import ETLDispatch
from reporting_workspace.job_monitor import JobMonitor
from reporting_workspace.legacy_crud import LegacyCrudService, InvalidStage, PermissionDenied
from reporting_workspace.legacy_policy import Policy
from reporting_workspace.legacy_jobs import LegacyJobAdapter, JobAccessDenied
from reporting_workspace.operations import OperationsService, DEMO_REPORT_ID
from reporting_workspace.providers import DemoIdentityProvider, DemoReportProvider
from reporting_workspace.report_builder import ReportBuilderService
from reporting_workspace.state import StateStore


OWNER = dict(id='owner', role='user', org='A')
PEER = dict(id='peer', role='user', org='A')
ADMIN = dict(id='admin', role='admin', org='A')
FOREIGN = dict(id='foreign', role='admin', org='B')


def identities():
    return DemoIdentityProvider({u['id']: dict(password='fixture', role=u['role'], org=u['org'])
                                 for u in (OWNER, PEER, ADMIN, FOREIGN)})


def legacy_actor(identifier='writer', organization='ORG_QA01'):
    return SimpleNamespace(id=identifier, orgcode=organization, is_authenticated=True,
                           is_dev=True, is_admin=False)


def qsl_record():
    return dict(Material_Type='Synthetic', Vendor_Code='ONE', Vendor_Name='Fixture',
                Country='TW', City='Demo', Rev='A', Supplier_Level='LEVEL 1')


class CurrentDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = StateStore(Path(self.temp.name) / 'state.sqlite')
        self.identities = identities()
        self.runtime = Runtime(Settings(), self.identities, DemoReportProvider(), self.store)
        self.service = self.runtime.definitions
        self.row = self.service.create(OWNER, {'name': 'Owner record'}, request_key='a' * 32)

    def test_runtime_service_rejects_forged_role_org_and_unknown_identity(self):
        for supplied in (dict(PEER, role='admin'), dict(FOREIGN, org='A'),
                         dict(ADMIN, id='unknown')):
            with self.subTest(supplied=supplied), self.assertRaises(DefinitionAccessDenied):
                self.service.update(supplied, self.row['id'], 1, {'name': 'forged'})
        self.assertEqual(self.service.get(OWNER, self.row['id']), self.row)
        self.assertEqual(len(self.store.list_audit()), 1)

    def test_retained_scope_rechecks_revocation_before_reads_writes_and_replay(self):
        bound = self.service.bind(OWNER)
        del self.identities.user_db[OWNER['id']]
        operations = (lambda: bound.list(), lambda: bound.get(self.row['id']),
                      lambda: bound.create({'name': 'Owner record'}, request_key='a' * 32),
                      lambda: bound.update(self.row['id'], 1, {'name': 'revoked'}),
                      lambda: bound.soft_delete(self.row['id'], 1),
                      lambda: bound.identity(), lambda: bound.can_change(self.row))
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(DefinitionAccessDenied):
                operation()
        self.assertEqual(len(self.store.list_audit()), 1)

    def test_revocation_after_audit_rolls_back_data_audit_and_create_receipt(self):
        original = self.store._audit
        def revoke(*args, **kwargs):
            original(*args, **kwargs)
            self.identities.user_db[OWNER['id']]['org'] = 'B'
        with patch.object(self.store, '_audit', side_effect=revoke):
            with self.assertRaises(DefinitionAccessDenied):
                self.service.create(OWNER, {'name': 'Rollback'}, request_key='b' * 32)
        self.identities.user_db[OWNER['id']]['org'] = 'A'
        self.assertEqual(self.service.list(OWNER)['items'], [self.row])
        self.assertEqual(len(self.store.list_audit()), 1)
        created = self.service.create(OWNER, {'name': 'Rollback'}, request_key='b' * 32)
        self.assertEqual(created['version'], 1)
        self.assertEqual(len(self.store.list_audit()), 2)

    def test_provider_unavailable_is_sanitized_and_nonmutating(self):
        with patch.object(self.identities, 'get_user', side_effect=RuntimeError('PRIVATE_BACKEND')):
            with self.assertRaises(StateUnavailable) as caught:
                self.service.update(OWNER, self.row['id'], 1, {'name': 'unavailable'})
        self.assertNotIn('PRIVATE_BACKEND', str(caught.exception))
        self.assertEqual(self.service.get(OWNER, self.row['id']), self.row)

    def test_shared_read_owner_admin_write_and_cross_org_denial_remain(self):
        self.assertEqual(self.service.get(PEER, self.row['id']), self.row)
        with self.assertRaises(DefinitionAccessDenied):
            self.service.update(PEER, self.row['id'], 1, {'name': 'peer'})
        with self.assertRaises(NotFound):
            self.service.get(FOREIGN, self.row['id'])
        self.assertEqual(self.service.update(ADMIN, self.row['id'], 1, {'name': 'admin'})['version'], 2)

    def test_concurrent_current_callers_preserve_one_version_winner(self):
        def change(user):
            try:
                return self.service.update(user, self.row['id'], 1, {'name': user['id']})['version']
            except Conflict:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(change, (OWNER, ADMIN)))
        self.assertCountEqual(results, [2, 'conflict'])
        self.assertEqual(len(self.store.list_audit()), 2)

    def test_standalone_trusted_facade_keeps_mapping_and_principal_contract(self):
        service = ReportDefinitions(self.store)
        bound = service.bind(dict(id='offline-owner', role='user', org='C'))
        self.assertEqual(bound.create({'name': 'Trusted offline'})['org'], 'C')


class LegacyActorIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.policy = Policy(orgcode=['ORG_QA'], crud_roles=['dev'])
        self.service = LegacyCrudService(str(Path(self.temp.name) / 'qsl.sqlite'),
                                         lambda user: self.policy, allow_upload=True)
        self.addCleanup(self.service.close)

    def test_upload_tokens_do_not_merge_distinct_exact_actor_ids(self):
        original, other = legacy_actor('writer'), legacy_actor(' writer ')
        token = self.service.stage_rows(original, [qsl_record()])
        with self.assertRaises(InvalidStage):
            self.service.stage_preview(other, token)
        self.assertEqual(self.service.submit_stage(original, token).created, 1)
        self.assertEqual(self.service.audit(original)[0]['actor'], 'writer')

    def test_direct_legacy_services_reject_malformed_identity_before_sqlite(self):
        builder = ReportBuilderService(str(Path(self.temp.name) / 'builder.sqlite'),
                                       self.service, lambda user: self.policy)
        self.addCleanup(builder.close)
        for identifier in (None, True, [], '', ' ', 'bad\nactor', '\ud800'):
            user = legacy_actor(identifier)
            for operation in (lambda: self.service.query(user),
                              lambda: self.service.stage_rows(user, [qsl_record()]),
                              lambda: builder.list_definitions(user)):
                with self.subTest(identifier=repr(identifier), operation=operation):
                    with self.assertRaises(PermissionDenied):
                        operation()
        self.assertEqual(self.service.query(legacy_actor()), [])

    def test_close_releases_both_legacy_databases_for_local_file_lifecycle(self):
        builder_path = Path(self.temp.name) / 'builder.sqlite'
        builder = ReportBuilderService(str(builder_path), self.service, lambda user: self.policy)
        self.addCleanup(builder.close)
        builder.close()
        self.service.close()
        for path in (builder_path, Path(self.temp.name) / 'qsl.sqlite'):
            renamed = path.with_suffix('.closed')
            path.rename(renamed)
            renamed.unlink()

    def test_failed_qsl_initialization_closes_the_opened_connection(self):
        path = Path(self.temp.name) / 'incompatible.sqlite'
        connection = sqlite3.connect(str(path))
        self.addCleanup(connection.close)
        connection.execute('CREATE TABLE legacy_qsl_records (id INTEGER)')
        connection.commit()
        with patch('reporting_workspace.legacy_crud.sqlite3.connect', return_value=connection):
            with self.assertRaises(sqlite3.DatabaseError):
                LegacyCrudService(str(path), lambda user: self.policy)
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute('SELECT 1')

    def test_legacy_job_authorizer_failure_is_safe_and_malformed_actor_is_denied(self):
        jobs = LegacyJobAdapter(str(Path(self.temp.name) / 'jobs.sqlite'), lambda *args: True)
        for actor in ('bad\nactor', '\ud800'):
            with self.subTest(actor=repr(actor)), self.assertRaises(JobAccessDenied):
                jobs.list_runs(actor, 'synthetic-job')
        with patch.object(jobs, 'authorize', side_effect=RuntimeError('PRIVATE_POLICY_BACKEND')):
            with self.assertRaises(JobAccessDenied) as caught:
                jobs.list_runs('writer', 'synthetic-job')
        self.assertNotIn('PRIVATE_POLICY_BACKEND', str(caught.exception))


class BackgroundRevocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.identities = identities()

    def test_monitor_revocation_during_publication_rolls_back_final_effect(self):
        monitor = JobMonitor(Path(self.temp.name) / 'monitor.sqlite', self.identities)
        self.addCleanup(monitor.stop)
        original = monitor._publish_summary
        def revoke(*args):
            original(*args)
            self.identities.user_db[ADMIN['id']]['role'] = 'user'
        with patch.object(monitor, '_publish_summary', side_effect=revoke):
            result = monitor.run_now(ADMIN)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error_code'], 'permission_revoked')
        self.assertIsNone(monitor.status(OWNER)['published_summary'])

    def test_operations_revocation_during_quality_gate_blocks_mock_capture(self):
        service = OperationsService(Path(self.temp.name) / 'operations.sqlite', self.identities)
        service.seed_demo(ADMIN)
        rows = service.quality_fixture(ADMIN, DEMO_REPORT_ID)['rows']
        original = service._quality
        def revoke(*args):
            result = original(*args)
            self.identities.user_db[ADMIN['id']]['role'] = 'user'
            return result
        with patch.object(service, '_quality', side_effect=revoke):
            with self.assertRaises(AccessDenied):
                service.simulate_send(ADMIN, DEMO_REPORT_ID, rows, ['owner@example.invalid'])
        self.assertEqual(service.mail.messages, [])
        self.assertEqual(service.list_simulations(OWNER, DEMO_REPORT_ID), [])

    def test_operations_revocation_after_metadata_rolls_back_mock_record(self):
        service = OperationsService(Path(self.temp.name) / 'operations.sqlite', self.identities)
        service.seed_demo(ADMIN)
        rows = service.quality_fixture(ADMIN, DEMO_REPORT_ID)['rows']
        original = service._increment
        def revoke(*args):
            original(*args)
            self.identities.user_db[ADMIN['id']]['role'] = 'user'
        with patch.object(service, '_increment', side_effect=revoke):
            with self.assertRaises(AccessDenied):
                service.simulate_send(ADMIN, DEMO_REPORT_ID, rows, ['owner@example.invalid'])
        self.assertEqual(service.mail.messages, [])
        self.assertEqual(service.list_simulations(OWNER, DEMO_REPORT_ID), [])
        self.assertEqual(service.usage_summary(OWNER)['totals']['mock_send'], 0)

    def test_etl_revoked_actor_cannot_replay_a_prior_request(self):
        service = ETLDispatch(Path(self.temp.name) / 'etl.sqlite', self.identities)
        self.addCleanup(service.stop)
        run = service.run_now(ADMIN, 'synthetic-sales-daily', request_id='same-request')
        self.identities.user_db[ADMIN['id']]['org'] = 'B'
        with self.assertRaises(AccessDenied):
            service.run_now(ADMIN, 'synthetic-sales-daily', request_id='same-request')
        self.assertEqual(len(service.status(OWNER, 'synthetic-sales-daily')['runs']), 1)
        self.assertEqual(run['status'], 'succeeded')


if __name__ == '__main__':
    unittest.main()
