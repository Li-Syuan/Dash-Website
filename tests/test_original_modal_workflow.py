"""Original CRUD modal contract through the main app HTTP + SQLite boundary.

These tests do not certify visual rendering or simulate a browser click pass.
"""
import base64
import json
import unittest
import test_unified_portal as helpers
from reporting_workspace.legacy_demo_ui import FIELDS


class OriginalModalWorkflowTests(unittest.TestCase):
    setUp = helpers.UnifiedPortalTransportTests.setUp
    tearDown = helpers.UnifiedPortalTransportTests.tearDown
    start_app = helpers.UnifiedPortalTransportTests.start_app
    close_app = helpers.UnifiedPortalTransportTests.close_app
    call = helpers.UnifiedPortalTransportTests.call
    login = helpers.UnifiedPortalTransportTests.login
    row = helpers.UnifiedPortalTransportTests.row
    page = helpers.UnifiedPortalTransportTests.page

    def lifecycle(self, name, opening=True, extra=None):
        return self.call('qa-modal-' + name + '.is_open', 'qa-' + ('open-' if opening else 'close-') + name, extra)

    def load(self, name='update', rid=1):
        return self.call('qa-load-status.children' if name == 'update' else 'qa-delete-record.children',
                         'qa-load-id' if name == 'update' else 'qa-query-delete',
                         {'qa-record-id' if name == 'update' else 'qa-delete-id': rid})

    def submit(self, action, extra=None):
        values = {'qa-filter': '', 'qa-record-id': 1, 'qa-delete-id': 1, 'qa-delete-confirm': True}
        row = self.row()
        values.update({'qa-field-' + key: row[key] for key in FIELDS})
        values.update({'qa-create-field-' + key: row[key] for key in FIELDS})
        values.update(extra or {})
        return self.call('qa-status.children', action, values)

    def test_original_operation_buttons_and_four_modals_are_in_main_page(self):
        self.login()
        page = self.page(helpers.PORTAL)['_pages_content']['children']
        by_id = {node['props'].get('id'): node for node in helpers.components(page) if isinstance(node['props'].get('id'), str)}
        for name in ('create', 'update', 'delete', 'upload'):
            self.assertEqual(by_id['qa-modal-' + name]['type'], 'Modal')
            self.assertFalse(by_id['qa-modal-' + name]['props']['is_open'])
            self.assertIn('qa-close-' + name, by_id)
        for cid, text in [('qa-open-create', 'Create'), ('qa-read', 'Read'), ('qa-open-update', 'Update'),
                          ('qa-open-delete', 'Delete'), ('qa-export-xlsx', 'Download data'), ('qa-template', 'Download template'),
                          ('qa-open-upload', 'Upload CSV')]:
            self.assertEqual(by_id[cid]['props']['children'], text)
        self.assertEqual(by_id['qa-record-version']['props']['disabled'], True)

    def test_create_cancel_reopen_clears_values_without_writing(self):
        self.login()
        before = self.server.extensions['qa_demo_crud'].query(self.editor)
        for opening in (True, False, True):
            result = self.lifecycle('create', opening)
            self.assertEqual(result['qa-modal-create']['is_open'], opening)
            self.assertEqual(result['qa-create-field-Vendor_Code']['value'], '')
        self.assertEqual(before, self.server.extensions['qa_demo_crud'].query(self.editor))

    def test_create_failure_stays_open_then_success_closes_and_refreshes(self):
        self.login()
        self.lifecycle('create')
        failed = self.submit('qa-create')  # duplicate five-column business key
        self.assertIn('未完成', failed['qa-create-status']['children'])
        self.assertNotIn('qa-modal-create', failed)  # no_update: remains open
        good = self.submit('qa-create', {'qa-create-field-Vendor_Code': 'MODAL03'})
        self.assertFalse(good['qa-modal-create']['is_open'])
        self.assertEqual(len(good['qa-table']['data']), 3)

    def test_query_then_direct_update_needs_no_extra_preview_step(self):
        self.login()
        self.lifecycle('update')
        loaded = self.load()
        self.assertEqual(loaded['qa-update-fields']['style'], {'display': 'block'})
        result = self.submit('qa-submit-update', {'qa-loaded-update': loaded['qa-loaded-update']['data'], 'qa-field-Rev': 'B'})
        self.assertFalse(result['qa-modal-update']['is_open'])
        self.assertEqual(self.row()['Rev'], 'B')
        self.assertEqual(self.row()['version'], 2)

    def test_update_requires_query_rejects_mismatched_id_and_stale_version(self):
        self.login()
        initial = self.row()
        bad = self.submit('qa-submit-update')
        self.assertIn('Query', bad['qa-update-status']['children'])
        loaded = self.load()['qa-loaded-update']['data']
        bad = self.submit('qa-submit-update', {'qa-loaded-update': loaded, 'qa-record-id': 2})
        self.assertIn('ID', bad['qa-update-status']['children'])
        self.assertEqual(self.row(), initial)
        self.server.extensions['qa_demo_crud'].update(self.editor, 1, 1, dict({f: initial[f] for f in FIELDS}, Rev='C'))
        bad = self.submit('qa-submit-update', {'qa-loaded-update': loaded, 'qa-field-Rev': 'D'})
        self.assertIn('未完成', bad['qa-update-status']['children'])
        self.assertNotIn('qa-modal-update', bad)
        self.assertEqual(self.row()['Rev'], 'C')

    def test_failed_query_clears_previous_fields_and_loaded_proof(self):
        self.login()
        self.load()
        result = self.load(rid=99999)
        self.assertIsNone(result['qa-loaded-update']['data'])
        self.assertEqual(result['qa-field-Rev']['value'], '')
        self.assertEqual(result['qa-update-fields']['style'], {'display': 'none'})

    def test_cancel_update_revokes_preview_and_reopen_has_no_stale_fields(self):
        self.login()
        proof = self.load()['qa-loaded-update']['data']
        preview = self.submit('qa-update', {'qa-loaded-update': proof, 'qa-field-Rev': 'B'})
        token = preview['qa-change-token']['data']
        result = self.lifecycle('update', False, {'qa-change-token': token})
        self.assertFalse(result['qa-modal-update']['is_open'])
        self.assertIsNone(result['qa-loaded-update']['data'])
        denied = self.submit('qa-confirm-update', {'qa-change-token': token})
        self.assertIn('未完成', denied['qa-update-status']['children'])
        reopened = self.lifecycle('update')
        self.assertEqual(reopened['qa-field-Rev']['value'], '')
        self.assertIsNone(reopened['qa-record-id']['value'])
        self.assertEqual(self.row()['Rev'], 'A')

    def test_delete_query_shows_record_cancel_no_write_then_explicit_submit(self):
        self.login()
        self.lifecycle('delete')
        loaded = self.load('delete')
        self.assertIn('SYN001', json.dumps(loaded['qa-delete-record']))
        self.lifecycle('delete', False)
        self.assertEqual(self.row()['version'], 1)
        self.lifecycle('delete')
        loaded = self.load('delete')
        denied = self.submit('qa-delete', {'qa-loaded-delete': loaded['qa-loaded-delete']['data'], 'qa-delete-id': 2})
        self.assertIn('未完成', denied['qa-delete-status']['children'])
        self.assertNotIn('qa-modal-delete', denied)
        deleted = self.submit('qa-delete', {'qa-loaded-delete': loaded['qa-loaded-delete']['data']})
        self.assertFalse(deleted['qa-modal-delete']['is_open'])
        self.assertEqual(len(deleted['qa-table']['data']), 1)
        self.assertEqual(self.server.extensions['qa_demo_crud'].history(self.editor, 1)[0]['action'], 'delete')

    def test_upload_close_revokes_stage_and_reopen_clears_preview(self):
        self.login()
        self.lifecycle('upload')
        content = ','.join(['id'] + FIELDS) + '\n,IC,MODALCSV,Vendor,TW,Tainan,A,LEVEL 2\n'
        staged = self.submit('qa-upload', {'qa-upload.contents': 'data:text/csv;base64,' + base64.b64encode(content.encode()).decode(), 'qa-upload.filename': 'rows.csv'})
        token = staged['qa-stage-token']['data']
        result = self.lifecycle('upload', False, {'qa-stage-token': token})
        self.assertIsNone(result['qa-stage-token']['data'])
        self.assertIsNone(result['qa-upload']['contents'])
        denied = self.submit('qa-import', {'qa-stage-token': token})
        self.assertIn('未完成', denied['qa-upload-status']['children'])
        self.assertEqual(self.lifecycle('upload')['qa-preview']['children'], '')

    def test_reader_cannot_open_write_modals_or_forge_signed_query(self):
        self.login('demo-user-a')
        for name in ('create', 'update', 'delete', 'upload'):
            self.assertFalse(self.lifecycle(name)['qa-modal-' + name]['is_open'])
        self.login()
        result = self.submit('qa-submit-update', {'qa-loaded-update': 'forged', 'qa-field-Rev': 'B'})
        self.assertIn('未完成', result['qa-update-status']['children'])
        self.assertEqual(self.row()['Rev'], 'A')

    def test_proof_from_other_user_cannot_write(self):
        self.login('demo-user-a')
        proof = self.load()['qa-loaded-update']['data']
        self.login()
        result = self.submit('qa-submit-update', {'qa-loaded-update': proof, 'qa-field-Rev': 'B'})
        self.assertIn('未完成', result['qa-update-status']['children'])
        self.assertEqual(self.row()['Rev'], 'A')

    def test_real_sqlite_write_failure_keeps_modal_open_and_preserves_record(self):
        self.login()
        loaded = self.load()['qa-loaded-update']['data']
        before = self.row()
        service = self.server.extensions['qa_demo_crud']
        service._db.execute("CREATE TEMP TRIGGER fail_modal_update BEFORE UPDATE ON legacy_qsl_records BEGIN SELECT RAISE(ABORT, 'synthetic write failure'); END")
        try:
            failed = self.submit('qa-submit-update', {'qa-loaded-update': loaded, 'qa-field-Rev': 'B'})
        finally:
            service._db.execute('DROP TRIGGER fail_modal_update')
        self.assertIn('未完成', failed['qa-update-status']['children'])
        self.assertNotIn('qa-modal-update', failed)
        self.assertEqual(self.row(), before)
        self.assertEqual(len(service.history(self.editor, 1)), 1)

    def test_modal_styles_served_locally_without_cdn(self):
        result = self.client.get('/assets/crud_modals.css')
        self.assertEqual(result.status_code, 200)
        css = result.get_data(as_text=True)
        self.assertIn('.modal.portal-crud-modal', css)
        self.assertIn('position: fixed', css)
        self.assertIn('.modal-backdrop', css)
        self.assertNotIn('@import', css)

    def test_replacement_invalid_upload_revokes_previous_stage(self):
        self.login()
        content = ','.join(['id'] + FIELDS) + '\n,IC,REPLACECSV,Vendor,TW,Tainan,A,LEVEL 2\n'
        staged = self.submit('qa-upload', {'qa-upload.contents': 'data:text/csv;base64,' + base64.b64encode(content.encode()).decode(), 'qa-upload.filename': 'rows.csv'})
        token = staged['qa-stage-token']['data']
        rejected = self.submit('qa-upload', {'qa-stage-token': token, 'qa-upload.contents': 'bad', 'qa-upload.filename': 'wrong.txt'})
        self.assertIsNone(rejected['qa-stage-token']['data'])
        self.assertEqual(rejected['qa-preview']['children'], '')
        replay = self.submit('qa-import', {'qa-stage-token': token})
        self.assertIn('未完成', replay['qa-upload-status']['children'])
        self.assertEqual(len(self.server.extensions['qa_demo_crud'].query(self.editor)), 2)

    def test_upload_component_clear_does_not_report_false_error(self):
        self.login()
        self.call('qa-status.children', 'qa-upload', {'qa-upload.contents': None}, expected=204)

    def test_atomic_upload_conflict_keeps_modal_open_and_reports_rollback(self):
        self.login()
        self.lifecycle('upload')
        content = ','.join(['id'] + FIELDS) + '\n,IC,GOODCSV,Vendor,TW,Tainan,A,LEVEL 2\n,IC,SYN001,Synthetic Vendor 1,TW,Taipei,A,LEVEL 1\n'
        staged = self.submit('qa-upload', {'qa-upload.contents': 'data:text/csv;base64,' + base64.b64encode(content.encode()).decode(), 'qa-upload.filename': 'rows.csv'})
        result = self.submit('qa-import', {'qa-stage-token': staged['qa-stage-token']['data']})
        self.assertIn('整批回滾', result['qa-upload-status']['children'])
        self.assertNotIn('qa-modal-upload', result)
        self.assertIsNone(result['qa-stage-token']['data'])
        self.assertEqual(len(self.server.extensions['qa_demo_crud'].query(self.editor)), 2)
