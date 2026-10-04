"""Offline regressions for authoritative identities at service boundaries."""
from pathlib import Path
from contextlib import contextmanager
import hashlib
import tempfile
import unittest
from unittest.mock import patch

from demo_services import AccessDenied
from reporting_workspace.application import Runtime, create_app
from reporting_workspace.config import Settings
from reporting_workspace.errors import ProviderUnavailable
from reporting_workspace.governance import ManagedReports
from reporting_workspace.providers import DemoIdentityProvider
from reporting_workspace.state import StateStore


ADMIN = dict(id='admin-a', role='admin', org='A')
USER = dict(id='user-a', role='user', org='A')


class CountingReports:
    is_demo = False
    columns = ['period', 'department', 'revenue', 'cost', 'profit']

    def __init__(self):
        self.calls = 0
        self.on_read = lambda: None

    def rows(self, user):
        self.calls += 1
        self.on_read()
        return [dict(period='2026-01', department='Synthetic', revenue=2, cost=1, profit=1)]

    def export(self, user):
        self.rows(user)
        return 'period,department,revenue,cost,profit\n2026-01,Synthetic,2,1,1\n'


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.identities = DemoIdentityProvider({
            u['id']: dict(password='test-only', role=u['role'], org=u['org'])
            for u in (ADMIN, USER)
        })
        self.reports = CountingReports()
        self.runtime = Runtime(Settings(), self.identities, self.reports)
        self.store = StateStore(Path(self.temp.name) / 'state.sqlite')
        self.managed = ManagedReports(self.store, self.identities)
        self.report = self.managed.create_report(
            ADMIN, dict(name='Synthetic', source_key='synthetic-monthly'))
        self.schedule = self.managed.create_schedule(
            ADMIN, self.report['id'], self.report['version'],
            dict(recipients=['tester@example.invalid'], enabled=True))

    def test_direct_report_rejects_forged_or_malformed_claims_before_provider(self):
        for user in (dict(USER, role='admin'), dict(ADMIN, id='missing'),
                     dict(ADMIN, org='B'), {'role': 'admin'},
                     dict(ADMIN, id=''), dict(ADMIN, org=True)):
            for operation in (self.runtime.reports.rows, self.runtime.reports.export):
                with self.subTest(user=user, operation=operation.__name__):
                    with self.assertRaises(AccessDenied):
                        operation(user)
        self.assertEqual(self.reports.calls, 0)

    def test_direct_report_rejects_deleted_and_demoted_identity(self):
        for change in ('demoted', 'deleted'):
            if change == 'demoted':
                self.identities.user_db[ADMIN['id']]['role'] = 'user'
            else:
                self.identities.user_db.pop(ADMIN['id'])
            for operation in (self.runtime.reports.rows, self.runtime.reports.export):
                with self.subTest(change=change, operation=operation.__name__):
                    with self.assertRaises(AccessDenied):
                        operation(ADMIN)
        self.assertEqual(self.reports.calls, 0)

    def test_legacy_report_rechecks_revocation_before_releasing_data(self):
        for operation in (self.runtime.reports.rows, self.runtime.reports.export):
            self.identities.user_db[ADMIN['id']]['role'] = 'admin'
            self.reports.on_read = lambda: self.identities.user_db[ADMIN['id']].update(role='user')
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(AccessDenied):
                    operation(ADMIN)

    def test_direct_simulation_rejects_stale_identity_before_effects(self):
        self.identities.user_db[ADMIN['id']]['role'] = 'user'
        with self.assertRaises(AccessDenied):
            self.runtime.run_simulation_result(ADMIN)
        self.assertEqual(self.runtime.mail.messages, [])

    def test_simulation_isolated_for_a_b_a_and_tenant_local_capture_count(self):
        other = dict(id='admin-b', role='admin', org='B')
        self.identities.user_db[other['id']] = dict(password='test-only', role='admin', org='B')
        first = self.runtime.run_simulation_result(ADMIN)
        lease_key = 'demo-report:' + hashlib.sha256(b'B').hexdigest()
        self.runtime.locks.acquire(lease_key, 'synthetic-blocker')
        blocked = self.runtime.run_simulation_result(other)
        self.assertFalse(blocked['acquired'])
        self.assertEqual(blocked['captured'], 0)
        self.runtime.locks.release(lease_key, 'synthetic-blocker')
        second = self.runtime.run_simulation_result(other)
        again = self.runtime.run_simulation_result(ADMIN)
        self.assertEqual([first['ran'], second['ran'], again['ran']], [True, True, False])
        self.assertEqual([first['captured'], second['captured'], again['captured']], [1, 1, 1])
        self.assertEqual(len(self.runtime.mail.messages), 2)

    def test_persistent_simulation_deduplicates_each_tenant_after_reopen(self):
        other = dict(id='admin-b', role='admin', org='B')
        self.identities.user_db[other['id']] = dict(password='test-only', role='admin', org='B')
        runtime = Runtime(Settings(), self.identities, self.reports, self.store)
        results = [runtime.run_simulation_result(user) for user in (ADMIN, other, ADMIN)]
        self.assertEqual([item['ran'] for item in results], [True, True, False])
        reopened = Runtime(Settings(), self.identities, self.reports,
                           StateStore(Path(self.temp.name) / 'state.sqlite'))
        for user in (ADMIN, other, ADMIN):
            result = reopened.run_simulation_result(user)
            self.assertFalse(result['ran'])
            self.assertEqual(result['captured'], 0)
            job_key = 'demo-mail:' + hashlib.sha256(user['org'].encode('utf-8')).hexdigest()
            self.assertEqual(reopened.state.get_job(job_key, 'fixture-1').state, 'succeeded')
        self.assertEqual(reopened.mail.messages, [])

    def test_legacy_unscoped_claims_block_new_namespace_without_replay(self):
        job_key = 'demo-mail:' + hashlib.sha256(b'A').hexdigest()
        for status in ('running', 'failed', 'succeeded'):
            store = StateStore(Path(self.temp.name) / ('legacy-' + status + '.sqlite'))
            token = store.claim_job('demo-mail', 'fixture-1', 'legacy-owner')
            if status != 'running':
                store.finish_job('demo-mail', 'fixture-1', 'legacy-owner', token,
                                 success=status == 'succeeded',
                                 failure_code='execution_failed' if status == 'failed' else None)
            old = store.get_job('demo-mail', 'fixture-1')
            runtime = Runtime(Settings(), self.identities, self.reports, store)
            with self.subTest(status=status):
                with self.assertRaises(ProviderUnavailable):
                    runtime.run_simulation_result(ADMIN)
                self.assertEqual(runtime.mail.messages, [])
                self.assertIsNone(store.get_job(job_key, 'fixture-1'))
                self.assertEqual(store.get_job('demo-mail', 'fixture-1'), old)

    def test_managed_metadata_rejects_stale_admin_across_public_methods(self):
        report_id, version = self.report['id'], self.report['version']
        schedule_id, schedule_version = self.schedule['id'], self.schedule['version']
        actions = {
            'permissions': lambda: self.managed.permissions(ADMIN, report_id),
            'list': lambda: self.managed.list_reports(ADMIN, admin=True),
            'get': lambda: self.managed.get_report(ADMIN, report_id),
            'create': lambda: self.managed.create_report(ADMIN, dict(name='Denied', source_key='synthetic-monthly')),
            'update': lambda: self.managed.update_report(ADMIN, report_id, version, dict(name='Denied')),
            'archive': lambda: self.managed.archive_report(ADMIN, report_id, version),
            'grants': lambda: self.managed.list_grants(ADMIN, report_id),
            'set_grant': lambda: self.managed.set_grant(ADMIN, report_id, version, 'user', USER['id'], ['view']),
            'create_schedule': lambda: self.managed.create_schedule(ADMIN, report_id, version, dict(recipients=['other@example.invalid'])),
            'get_schedule': lambda: self.managed.get_schedule(ADMIN, schedule_id),
            'list_schedules': lambda: self.managed.list_schedules(ADMIN, report_id),
            'update_schedule': lambda: self.managed.update_schedule(ADMIN, schedule_id, schedule_version, dict(enabled=False)),
            'runs': lambda: self.managed.list_runs(ADMIN, report_id),
            'audit': lambda: self.managed.list_audit(ADMIN),
            'mock': lambda: self.managed.simulate_schedule(ADMIN, schedule_id, schedule_version),
        }
        self.identities.user_db[ADMIN['id']]['role'] = 'user'
        for name, action in actions.items():
            with self.subTest(action=name):
                with self.assertRaises(AccessDenied):
                    action()

    def test_managed_rejects_forged_admin_before_metadata_or_provider(self):
        forged = dict(USER, role='admin')
        with self.assertRaises(AccessDenied):
            self.managed.list_reports(forged, admin=True)
        with patch.object(self.managed.provider, 'managed_rows', wraps=self.managed.provider.managed_rows) as provider:
            with self.assertRaises(AccessDenied):
                self.managed.rows(forged, self.report['id'])
            provider.assert_not_called()

    def test_managed_revoked_before_read_never_invokes_provider(self):
        self.identities.user_db.pop(ADMIN['id'])
        with patch.object(self.managed.provider, 'managed_rows', wraps=self.managed.provider.managed_rows) as provider:
            with self.assertRaises(AccessDenied):
                self.managed.export(ADMIN, self.report['id'])
            provider.assert_not_called()

    def test_managed_rechecks_identity_after_waiting_for_transaction(self):
        transaction = self.store._transaction

        @contextmanager
        def revoke_on_entry(*args, **kwargs):
            with transaction(*args, **kwargs) as connection:
                self.identities.user_db[ADMIN['id']]['role'] = 'user'
                yield connection

        with patch.object(self.store, '_transaction', side_effect=revoke_on_entry):
            with self.assertRaises(AccessDenied):
                self.managed.create_report(ADMIN, dict(name='Denied', source_key='synthetic-monthly'))
        self.identities.user_db[ADMIN['id']]['role'] = 'admin'
        self.assertEqual(len(self.managed.list_reports(ADMIN, admin=True)), 1)

    def test_managed_revocation_during_mutation_rolls_back_metadata_and_audit(self):
        audit = self.managed._audit
        before = self.managed.list_audit(ADMIN)

        def revoke_after_audit(*args, **kwargs):
            audit(*args, **kwargs)
            self.identities.user_db[ADMIN['id']]['role'] = 'user'

        with patch.object(self.managed, '_audit', side_effect=revoke_after_audit):
            with self.assertRaises(AccessDenied):
                self.managed.update_report(ADMIN, self.report['id'], self.report['version'], dict(name='Denied'))
        self.identities.user_db[ADMIN['id']]['role'] = 'admin'
        self.assertEqual(self.managed.get_report(ADMIN, self.report['id'])['name'], 'Synthetic')
        self.assertEqual(self.managed.list_audit(ADMIN), before)

    def test_provider_wrong_id_is_rejected_before_direct_report(self):
        with patch.object(self.identities, 'get_user', return_value=dict(ADMIN, id='other-admin')):
            with self.assertRaises(ProviderUnavailable):
                self.runtime.reports.rows(ADMIN)
        self.assertEqual(self.reports.calls, 0)

    def test_service_lookup_outage_is_sanitized_and_fail_closed(self):
        with patch.object(self.identities, 'get_user', side_effect=RuntimeError('private-provider-detail')):
            for action in (lambda: self.runtime.reports.rows(ADMIN),
                           lambda: self.managed.list_reports(ADMIN, admin=True)):
                with self.assertRaises(ProviderUnavailable) as error:
                    action()
                self.assertNotIn('private-provider-detail', str(error.exception))
        self.assertEqual(self.reports.calls, 0)

    def test_current_admin_still_reads_and_export_remains_admin_only(self):
        self.assertEqual(len(self.runtime.reports.rows(ADMIN)), 1)
        self.assertIn('Synthetic', self.runtime.reports.export(ADMIN))
        self.assertEqual(self.managed.get_report(ADMIN, self.report['id'])['id'], self.report['id'])
        with self.assertRaises(AccessDenied):
            self.runtime.reports.export(USER)

    def test_http_user_loader_rejects_identity_substitution(self):
        self.identities.is_demo = False
        server = create_app(Settings(mode='production', secret_key='boundary-test-' + 'x' * 48,
                                     state_path=str(Path(self.temp.name) / 'http.sqlite'),
                                     session_cookie_secure=True), self.identities, self.reports)
        server.config['TESTING'] = True
        client = server.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = USER['id']
            session['_fresh'] = True
        with patch.object(self.identities, 'get_user', return_value=ADMIN):
            response = client.get('/api/reports/export.csv')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.reports.calls, 0)

    def test_factory_cleanup_keeps_original_construction_error(self):
        self.identities.is_demo = False
        settings = Settings(mode='production', secret_key='boundary-test-' + 'x' * 48,
                            state_path=str(Path(self.temp.name) / 'factory.sqlite'),
                            session_cookie_secure=True)
        for cleanup_error in (None, RuntimeError('private-cleanup-detail')):
            original = ValueError('invalid page declaration')
            with self.subTest(cleanup_fails=cleanup_error is not None):
                with patch('reporting_workspace.web.create_dash_app', side_effect=original), \
                     patch('reporting_workspace.lifecycle.dispose_app', side_effect=cleanup_error) as dispose:
                    with self.assertRaises(ValueError) as caught:
                        create_app(settings, self.identities, self.reports)
                    self.assertIs(caught.exception, original)
                    dispose.assert_called_once()


if __name__ == '__main__':
    unittest.main()
