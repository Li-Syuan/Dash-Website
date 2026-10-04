"""Opt-in report-template wiring through the existing single app factory."""
from contextlib import ExitStack
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from reporting_workspace.application import create_app
from reporting_workspace.config import Settings
from reporting_workspace.lifecycle import dispose_app
from reporting_workspace.legacy_crud import PermissionDenied


class ReportTemplateConfigurationTests(unittest.TestCase):
    def test_launch_flag_is_explicit_and_uses_strict_boolean_values(self):
        self.assertFalse(Settings.from_env({}).enable_report_template)
        for raw in ('true', 'TRUE', '1'):
            self.assertTrue(Settings.from_env({
                'REPORTING_ENABLE_REPORT_TEMPLATE': raw}).enable_report_template)
        for raw in ('false', 'FALSE', '0'):
            self.assertFalse(Settings.from_env({
                'REPORTING_ENABLE_REPORT_TEMPLATE': raw}).enable_report_template)
        for raw in ('', 'yes', ' true', '1 ', 'report_template.SPEC', '2'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                Settings.from_env({'REPORTING_ENABLE_REPORT_TEMPLATE': raw})
        for value in (1, 0, 'true', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Settings(enable_report_template=value)

    def test_synthetic_template_never_silently_enables_in_production(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'not-created.sqlite'
            with self.assertRaisesRegex(ValueError, 'only in demo mode'):
                Settings(mode='production', secret_key='synthetic-test-value-' * 3,
                         state_path=str(destination), enable_report_template=True)
            self.assertFalse(destination.exists())

    def test_factory_flag_is_app_scoped_and_imports_start_no_workers(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as apps:
            enabled = create_app(Settings(
                state_path=str(Path(folder) / 'enabled.sqlite'),
                enable_report_template=True))
            apps.callback(dispose_app, enabled)
            disabled = create_app(Settings(
                state_path=str(Path(folder) / 'disabled.sqlite')))
            apps.callback(dispose_app, disabled)
            path = '/QA_portal/report-template'
            self.assertIsNotNone(enabled.extensions['page_registry'].get(path))
            self.assertIsNone(disabled.extensions['page_registry'].get(path))
            self.assertEqual(enabled.test_client().get('/healthz').status_code, 200)
            self.assertEqual(disabled.test_client().get('/healthz').status_code, 200)
            for server in (enabled, disabled):
                payload = server.test_client().get('/readyz').get_json()
                self.assertEqual(payload['status'], 'ready')
                self.assertEqual(payload['scheduler'], 'not-started')


class IntegratedLongExportAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.server = create_app(Settings(
            state_path=str(Path(self.directory.name) / 'workspace.sqlite')))
        self.addCleanup(dispose_app, self.server)
        self.runtime = self.server.extensions['workspace']
        self.service = self.server.extensions['qa_demo_crud']
        self.actor = SimpleNamespace(id='demo-admin', orgcode='ORG_QA01',
                                     is_authenticated=True, is_dev=True,
                                     is_admin=False)

    def test_direct_integrated_service_rejects_forged_or_changed_claims(self):
        self.assertTrue(self.service.query(self.actor))
        for changes in ({'id': 'not-a-main-account'}, {'id': 'demo-user-b'},
                        {'id': 'demo-user-a'}, {'is_admin': True},
                        {'orgcode': 'ORG_QA02'}, {'is_dev': 'true'}):
            with self.subTest(changes=changes), self.assertRaises(PermissionDenied):
                actor = SimpleNamespace(**dict(vars(self.actor), **changes))
                self.service.export_xlsx(actor)

    def test_same_organization_reader_keeps_export_permission(self):
        reader = SimpleNamespace(id='demo-user-a', orgcode='ORG_QA01',
                                 is_authenticated=True, is_dev=False,
                                 is_admin=False)
        self.assertTrue(self.service.export_xlsx(reader).startswith(b'PK'))

    def test_provider_revocation_before_publication_returns_no_xlsx(self):
        import openpyxl
        original_save = openpyxl.Workbook.save
        record = dict(self.runtime.identities.user_db['demo-admin'])
        for change in ('account_removed', 'organization_changed', 'role_changed'):
            with self.subTest(change=change):
                self.runtime.identities.user_db['demo-admin'] = dict(record)

                def save_then_revoke(workbook, destination):
                    result = original_save(workbook, destination)
                    if change == 'account_removed':
                        del self.runtime.identities.user_db['demo-admin']
                    elif change == 'organization_changed':
                        self.runtime.identities.user_db['demo-admin']['org'] = 'B'
                    else:
                        self.runtime.identities.user_db['demo-admin']['role'] = 'user'
                    return result

                with patch.object(openpyxl.Workbook, 'save', save_then_revoke):
                    with self.assertRaises(PermissionDenied):
                        self.service.export_xlsx(self.actor)
        self.runtime.identities.user_db['demo-admin'] = record
        self.assertEqual(len(self.service.query(self.actor)), 2)

    def test_provider_cannot_resolve_export_actor_to_a_different_account(self):
        original = self.runtime.identities.get_user
        with patch.object(self.runtime.identities, 'get_user',
                          lambda identifier: original('demo-user-a')):
            with self.assertRaises(PermissionDenied):
                self.service.export_xlsx(self.actor)


if __name__ == '__main__':
    unittest.main()
