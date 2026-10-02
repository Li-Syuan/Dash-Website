"""Actual app.py factory + HTTP transport for operations with real SQLite."""
import unittest
import test_unified_portal as helpers


class OperationsTransportTests(unittest.TestCase):
    setUp = helpers.UnifiedPortalTransportTests.setUp
    start_app = helpers.UnifiedPortalTransportTests.start_app
    close_app = helpers.UnifiedPortalTransportTests.close_app
    tearDown = helpers.UnifiedPortalTransportTests.tearDown
    call = helpers.UnifiedPortalTransportTests.call
    login = helpers.UnifiedPortalTransportTests.login

    def test_operations_navigation_and_todo(self):
        self.login()
        response = self.call('_pages_content.children', '_pages_location', {
            '_pages_location.pathname': '/QA_portal/operations', '_pages_location.search': ''})
        self.assertIn('報表營運中心', str(response))
        self.assertEqual(self.client.get('/QA_portal/feature-todo').status_code, 200)

    def test_catalog_pagination_and_bounds(self):
        self.login()
        response = self.call('ops-tables-result.children', 'ops-tables', {
            'ops-tables-query': '', 'ops-tables-page': 1})
        self.assertIn('fixture-001', str(response))
        self.assertNotIn('fixture-021', str(response))
        response = self.call('ops-tables-result.children', 'ops-tables', {
            'ops-tables-query': '', 'ops-tables-page': 6})
        self.assertNotIn('fixture-001', str(response))

    def test_operations_callbacks_deny_anonymous_and_other_org(self):
        values = {'ops-tables-query': '', 'ops-tables-page': 1}
        self.call('ops-tables-result.children', 'ops-tables', values, expected=401)
        self.assertEqual(self.client.get('/QA_portal/feature-todo').status_code, 401)
        self.login('demo-user-b')
        self.call('ops-tables-result.children', 'ops-tables', values, expected=403)
        self.assertEqual(self.client.get('/QA_portal/feature-todo').status_code, 403)

    def test_quality_bad_snapshot_and_versions(self):
        self.login()
        result = self.call('ops-quality-result.children', 'ops-send', {
            'ops-report': 'monthly-performance', 'ops-scenario': 'bad'})
        self.assertNotIn('Internal service error', str(result))
        self.assertIn('false', str(result).lower())
        result = self.call('ops-diff-result.children', 'ops-diff', {
            'ops-report': 'monthly-performance', 'ops-before': 1, 'ops-after': 2})
        self.assertNotIn('輸入或資料版本不正確', str(result))

    def test_cross_origin_denied(self):
        self.login()
        self.call('ops-usage-result.children', 'ops-usage', headers={'Origin': 'https://outside.invalid'}, expected=403)

    def test_source_link_and_revocation(self):
        self.login()
        path = '/QA_portal/source/monthly-performance/2/monthly-row-002'
        self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.client.get('/QA_portal/source/monthly-performance/2/missing').status_code, 404)
        self.identities.user_db.pop('demo-admin')
        self.assertEqual(self.client.get(path).status_code, 401)

    def test_scheduler_admin_controls_readonly_user_status(self):
        self.login('demo-user-a')
        result = self.call('ops-job-result.children', 'ops-job-save', {'ops-job-enabled': True, 'ops-job-interval': 60})
        self.assertIn('沒有權限', str(result))
        result = self.call('ops-job-result.children', 'ops-job-status', {'ops-job-enabled': False, 'ops-job-interval': 300})
        self.assertIn('Asia/Taipei', str(result))
        self.login()
        result = self.call('ops-job-result.children', 'ops-job-run', {'ops-job-enabled': False, 'ops-job-interval': 300})
        self.assertIn('atomic_publish', str(result))
        self.assertIn('succeeded', str(result))

    def test_table_reads_on_demand_sqlite_and_unavailable_oracle(self):
        self.login()
        result = self.call('ops-table-data.children', 'ops-table-read', {'ops-table-key': 'fixture-001'})
        self.assertIn('SYNTH-001', str(result))
        result = self.call('ops-table-data.children', 'ops-table-read', {'ops-table-key': 'fixture-100'})
        self.assertIn('此資料來源尚未接線', str(result))

    def test_report_view_records_actual_successful_outcome(self):
        self.login()
        self.call('_pages_content.children', '_pages_location', {'_pages_location.pathname': '/page3', '_pages_location.search': ''})
        summary = self.server.extensions['operations_service'].usage_summary(self.identities.get_user('demo-admin'))
        self.assertEqual(summary['totals']['report_view'], 1)

    def test_dedup_includes_own_wizard_definitions_without_sharing(self):
        builder = self.server.extensions['qa_demo_builder']
        definition = {'schema_version': 1, 'source': 'synthetic-qsl',
                      'columns': ['Vendor_Code', 'Vendor_Name'], 'filters': [],
                      'group_by': [], 'aggregate': None, 'limit': 100}
        builder.save(self.editor, 'PrivateUniqueReport', definition)
        self.login()
        result = self.call('ops-search-result.children', 'ops-search', {'ops-request': 'PrivateUniqueReport'})
        self.assertIn('PrivateUniqueReport', str(result))
        self.login('demo-user-a')
        result = self.call('ops-search-result.children', 'ops-search', {'ops-request': 'PrivateUniqueReport'})
        self.assertNotIn('PrivateUniqueReport', str(result))

    def test_diagnostic_zip_authorized_selected_run_only(self):
        import base64
        import io
        import json
        import zipfile
        self.login()
        monitor = self.server.extensions['job_monitor']
        run = monitor.run_now(self.identities.get_user('demo-admin'))
        response = self.call('ops-job-download.data', 'ops-job-export', {'ops-job-run-id': run['run_id']})
        download = response['ops-job-download']['data']
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(download['content']))) as archive:
            self.assertEqual(set(archive.namelist()), {'README.txt', 'diagnostic.json'})
            diagnostic = json.loads(archive.read('diagnostic.json'))
            self.assertIn(run['run_id'], str(diagnostic))
            self.assertNotIn('lease_token', str(diagnostic))
            self.assertNotIn('demo-admin', str(diagnostic))
        response = self.call('ops-job-download.data', 'ops-job-export', {'ops-job-run-id': 'missing'})
        self.assertNotIn('ops-job-download', response)
        self.login('demo-user-b')
        self.call('ops-job-download.data', 'ops-job-export', {'ops-job-run-id': run['run_id']}, expected=403)

    def test_job_owner_notifications_are_mock_and_admin_only(self):
        self.login('demo-user-a')
        result = self.call('ops-job-notify-result.children', 'ops-job-notify-save', {
            'ops-job-owner': 'owner@example.invalid', 'ops-job-notify-enabled': True})
        self.assertIn('沒有權限', str(result))
        self.login()
        result = self.call('ops-job-notify-result.children', 'ops-job-notify-save', {
            'ops-job-owner': 'owner@example.invalid', 'ops-job-notify-enabled': True})
        self.assertIn('owner@example.invalid', str(result))
        result = self.call('ops-job-notify-result.children', 'ops-job-notify-save', {
            'ops-job-owner': 'real@company.example', 'ops-job-notify-enabled': True})
        self.assertIn('輸入或資料版本不正確', str(result))
