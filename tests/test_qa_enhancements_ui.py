"""Real HTTP callback contracts for synthetic QSL revisions and report wizard.

Exercise Dash's registered callback metadata and Flask's signed demo login,
including the string values actually emitted by Mantine Select components.
No callback functions, policy checks, company adapters, or network calls are
mocked. The service extensions are used only to verify persisted outcomes.
"""
import copy
import re
import tempfile
import unittest
from types import SimpleNamespace

from reporting_workspace.legacy_demo_ui import create_demo, DEMO_USERS, FIELDS, PREFIX


class QaEnhancementTransportTests(unittest.TestCase):
    def setUp(self):
        self.apps = []
        self.start_app()
        self.editor = SimpleNamespace(**DEMO_USERS['editor'])

    def start_app(self, directory=None):
        self.app = create_demo(data_directory=directory)
        self.apps.append(self.app)
        self.server = self.app.server
        self.server.config['TESTING'] = True
        self.client = self.server.test_client()
        self.assertEqual(self.client.get(PREFIX).status_code, 200)

    def close_app(self, app):
        if app not in self.apps:
            return
        app.server.extensions['qa_demo_builder'].close()
        app.server.extensions['qa_demo_crud'].close()
        app.server.extensions['qa_demo_temporary'].cleanup()
        self.apps.remove(app)

    def tearDown(self):
        for app in list(self.apps):
            self.close_app(app)

    def login(self, identity):
        response = self.client.get(PREFIX + 'demo-login')
        self.assertEqual(response.status_code, 200)
        csrf = re.search('name="csrf" value="([^"]+)"', response.get_data(as_text=True)).group(1)
        response = self.client.post(PREFIX + 'demo-login', data={'csrf': csrf, 'identity': identity})
        self.assertEqual(response.status_code, 302)

    def callback(self, output_prefix, trigger, values=None):
        """POST the same component-property payload a Dash browser sends."""
        values = values or {}
        matches = [(key, item) for key, item in self.app.callback_map.items()
                   if output_prefix in key and any(entry['id'] == trigger for entry in item['inputs'])]
        self.assertEqual(len(matches), 1, (output_prefix, trigger))
        key, spec = matches[0]
        outputs = spec['output']
        if not isinstance(outputs, list):
            outputs = [outputs]
        trigger_property = next(item['property'] for item in spec['inputs'] if item['id'] == trigger)

        def value(item, default=None):
            return values.get(item['id'] + '.' + item['property'], values.get(item['id'], default))

        payload = {
            'output': key,
            'outputs': [{'id': item.component_id, 'property': item.component_property} for item in outputs],
            'inputs': [dict(item, value=value(item, 1 if item['id'] == trigger else None))
                       for item in spec['inputs']],
            'state': [dict(item, value=value(item)) for item in spec['state']],
            'changedPropIds': [trigger + '.' + trigger_property],
        }
        response = self.client.post(PREFIX + '_dash-update-component', json=payload)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()['response']

    def row(self, record_id=1):
        return self.server.extensions['qa_demo_crud'].get(self.editor, record_id)

    def crud(self, trigger, extra=None):
        values = {'qa-filter': '', 'qa-record-id': 1, 'qa-record-version': 1,
                  'qa-delete-confirm': True, 'qa-change-token': None}
        fields = dict(Material_Type='IC', Vendor_Code='SYN001', Vendor_Name='Synthetic Vendor 1',
                      Country='TW', City='Taipei', Rev='B', Supplier_Level='LEVEL 1')
        values.update({'qa-field-' + field: fields[field] for field in FIELDS})
        values.update(extra or {})
        return self.callback('qa-status.children', trigger, values)

    def prepare_update(self, extra=None):
        result = self.crud('qa-update', extra)
        self.assertNotIn('未完成', result['qa-status']['children'])
        token = result['qa-change-token']['data']
        self.assertIsInstance(token, str)
        self.assertTrue(token)
        return token, result

    def confirm_update(self, token, extra=None):
        values = {'qa-change-token': token}
        values.update(extra or {})
        return self.crud('qa-confirm-update', values)

    def history(self, trigger='qa-history-load', extra=None):
        values = {'qa-history-id': 1, 'qa-history-version': '1',
                  'qa-history-current': 1, 'qa-restore-token': None}
        values.update(extra or {})
        return self.callback('qa-history-status.children', trigger, values)

    def wizard_values(self, extra=None):
        values = {'qb-source': 'synthetic-qsl', 'qb-columns': ['Vendor_Code', 'Vendor_Name', 'Country', 'City'],
                  'qb-filter-field': None, 'qb-filter-op': 'contains', 'qb-filter-value': '',
                  'qb-group': [], 'qb-limit': 100, 'qb-step': 0, 'qb-name': 'My QSL Report',
                  'qb-preview-definition': None, 'qb-saved-version': None}
        values.update(extra or {})
        return values

    def report_preview(self, extra=None):
        return self.callback('qb-preview-status.children', 'qb-preview', self.wizard_values(extra))

    def report_save(self, definition, extra=None):
        values = self.wizard_values({'qb-preview-definition': definition})
        values.update(extra or {})
        return self.callback('qb-save-status.children', 'qb-save', values)

    def save_report(self, extra=None):
        preview = self.report_preview(extra)
        self.assertNotIn('未完成', preview['qb-preview-status']['children'])
        definition = preview['qb-preview-definition']['data']
        result = self.report_save(definition, extra)
        self.assertNotIn('未完成', result['qb-save-status']['children'])
        self.assertEqual(len(result['qb-saved-id']['data']), 1)
        return result['qb-saved-id']['data'][0]['value'], definition, result

    def report_load(self, selected):
        return self.callback('qb-source.value', 'qb-load', {'qb-saved-id': selected})

    def test_layout_and_dependencies_register_enhanced_controls(self):
        layout = self.client.get(PREFIX + '_dash-layout')
        self.assertEqual(layout.status_code, 200)
        for component in ('qa-change-token', 'qa-confirm-update', 'qa-history-table',
                          'qa-restore-confirm', 'qb-step', 'qb-preview', 'qb-load'):
            self.assertIn('"id":"' + component + '"', layout.get_data(as_text=True))
        self.assertEqual(self.client.get(PREFIX + '_dash-dependencies').status_code, 200)
        self.assertIn('qa_demo_builder', self.server.extensions)

    def test_update_preview_does_not_commit_and_confirmation_uses_staged_values(self):
        self.login('editor')
        before = self.row()
        token, preview = self.prepare_update()
        self.assertEqual(self.row(), before)
        self.assertEqual(len(self.server.extensions['qa_demo_crud'].history(self.editor, 1)), 1)
        self.assertIn('qa-change-preview', preview)
        # Editing the form after preview must not change the server-owned payload.
        committed = self.confirm_update(token, {'qa-field-Rev': 'C', 'qa-record-id': 2, 'qa-record-version': 900})
        self.assertIn('committed', committed['qa-status']['children'])
        self.assertEqual(self.row()['Rev'], 'B')
        self.assertEqual(self.row()['version'], 2)
        self.assertEqual(self.row(2)['version'], 1)
        self.assertIsNone(committed['qa-change-token']['data'])
        replay = self.confirm_update(token)
        self.assertIn('未完成', replay['qa-status']['children'])
        self.assertEqual(self.row()['version'], 2)

    def test_cancel_update_invalidates_retained_browser_token(self):
        self.login('editor')
        token, unused = self.prepare_update()
        canceled = self.crud('qa-cancel-update', {'qa-change-token': token})
        self.assertIsNone(canceled['qa-change-token']['data'])
        self.assertEqual(self.row()['version'], 1)
        replay = self.confirm_update(token)
        self.assertIn('未完成', replay['qa-status']['children'])
        self.assertEqual(self.row()['Rev'], 'A')
        self.assertEqual(self.row()['version'], 1)

    def test_replacing_update_preview_revokes_previous_token(self):
        self.login('editor')
        old, unused = self.prepare_update()
        current, unused = self.prepare_update({'qa-change-token': old, 'qa-field-Rev': 'C'})
        self.assertNotEqual(old, current)
        result = self.confirm_update(old)
        self.assertIn('未完成', result['qa-status']['children'])
        self.assertEqual(self.row()['version'], 1)
        self.confirm_update(current)
        self.assertEqual((self.row()['Rev'], self.row()['version']), ('C', 2))

    def test_update_token_is_bound_to_server_identity_and_requires_write_access(self):
        self.login('editor')
        token, unused = self.prepare_update()
        for identity in ('anonymous', 'reader', 'admin', 'outsider', 'developer'):
            with self.subTest(identity=identity):
                self.login(identity)
                result = self.confirm_update(token)
                self.assertIn('未完成', result['qa-status']['children'])
                self.assertEqual(self.row()['version'], 1)
        self.login('editor')
        self.assertIn('committed', self.confirm_update(token)['qa-status']['children'])

    def test_forbidden_users_cannot_preview_or_read_revision_history(self):
        for identity in ('anonymous', 'reader', 'admin', 'outsider'):
            with self.subTest(identity=identity):
                self.login(identity)
                result = self.crud('qa-update')
                self.assertIn('未完成', result['qa-status']['children'])
                self.assertNotIn('qa-change-token', result)
                history = self.history()
                self.assertIn('未完成', history['qa-history-status']['children'])
                self.assertEqual(history['qa-history-table']['data'], [])
                self.assertEqual(self.row()['version'], 1)

    def test_stale_update_preview_cannot_overwrite_committed_change(self):
        self.login('editor')
        first, unused = self.prepare_update({'qa-field-Rev': 'B'})
        stale, unused = self.prepare_update({'qa-field-Rev': 'C'})
        self.assertIn('committed', self.confirm_update(first)['qa-status']['children'])
        result = self.confirm_update(stale)
        self.assertIn('未完成', result['qa-status']['children'])
        self.assertEqual((self.row()['Rev'], self.row()['version']), ('B', 2))
        self.assertEqual(len(self.server.extensions['qa_demo_crud'].history(self.editor, 1)), 2)

    def test_history_restore_creates_new_version_and_preserves_actor_source(self):
        self.login('editor')
        token, unused = self.prepare_update()
        self.confirm_update(token)
        history = self.history()
        self.assertEqual([row['version'] for row in history['qa-history-table']['data']], [2, 1])
        self.assertEqual(history['qa-history-current']['data'], 2)
        self.assertTrue(all(isinstance(item['value'], str) for item in history['qa-history-version']['data']))
        prepared = self.history('qa-restore-preview', {'qa-history-current': 2, 'qa-history-version': '1'})
        restore_token = prepared['qa-restore-token']['data']
        self.assertEqual((self.row()['Rev'], self.row()['version']), ('B', 2))
        result = self.history('qa-restore-confirm', {'qa-history-current': 2, 'qa-restore-token': restore_token})
        self.assertNotIn('未完成', result['qa-history-status']['children'])
        self.assertEqual((self.row()['Rev'], self.row()['version']), ('A', 3))
        latest = result['qa-history-table']['data'][0]
        self.assertEqual((latest['version'], latest['actor'], latest['source_version']), (3, 'owner01', 1))
        self.assertEqual(latest['action'], 'restore_version')
        self.assertEqual([item['version'] for item in result['qa-history-table']['data']], [3, 2, 1])
        replay = self.history('qa-restore-confirm', {'qa-restore-token': restore_token})
        self.assertIn('未完成', replay['qa-history-status']['children'])

    def test_cancel_restore_invalidates_token_and_stale_restore_conflicts(self):
        self.login('editor')
        token, unused = self.prepare_update()
        self.confirm_update(token)
        values = {'qa-history-current': 2, 'qa-history-version': '1'}
        prepared = self.history('qa-restore-preview', values)
        token = prepared['qa-restore-token']['data']
        self.history('qa-restore-cancel', {'qa-restore-token': token})
        self.assertIn('未完成', self.history('qa-restore-confirm', {'qa-restore-token': token})['qa-history-status']['children'])
        prepared = self.history('qa-restore-preview', values)
        stale = prepared['qa-restore-token']['data']
        newer, unused = self.prepare_update({'qa-record-version': 2, 'qa-field-Rev': 'C'})
        self.confirm_update(newer)
        result = self.history('qa-restore-confirm', {'qa-restore-token': stale})
        self.assertIn('未完成', result['qa-history-status']['children'])
        self.assertEqual((self.row()['Rev'], self.row()['version']), ('C', 3))

    def test_history_restore_can_reactivate_deleted_record(self):
        self.login('editor')
        self.assertIn('committed', self.crud('qa-delete')['qa-status']['children'])
        loaded = self.history()
        self.assertEqual(loaded['qa-history-current']['data'], 2)
        prepared = self.history('qa-restore-preview', {'qa-history-current': 2, 'qa-history-version': '1'})
        result = self.history('qa-restore-confirm', {'qa-restore-token': prepared['qa-restore-token']['data']})
        self.assertNotIn('未完成', result['qa-history-status']['children'])
        self.assertEqual(self.row()['version'], 3)
        self.assertEqual(len(self.crud('qa-read')['qa-table']['data']), 2)

    def test_history_reload_revokes_pending_restore_token(self):
        self.login('editor')
        token, unused = self.prepare_update()
        self.confirm_update(token)
        preview = self.history('qa-restore-preview', {'qa-history-current': 2})
        token = preview['qa-restore-token']['data']
        loaded = self.history('qa-history-load', {'qa-restore-token': token})
        self.assertIsNone(loaded['qa-restore-token']['data'])
        self.assertEqual(loaded['qa-restore-diff']['children'], '')
        result = self.history('qa-restore-confirm', {'qa-restore-token': token})
        self.assertIn('未完成', result['qa-history-status']['children'])
        self.assertEqual((self.row()['Rev'], self.row()['version']), ('B', 2))

    def test_restore_confirmation_rejects_update_preview_token(self):
        self.login('editor')
        token, unused = self.prepare_update()
        result = self.history('qa-restore-confirm', {'qa-restore-token': token})
        self.assertIn('未完成', result['qa-history-status']['children'])
        self.assertEqual((self.row()['Rev'], self.row()['version']), ('A', 1))
        self.assertEqual(len(self.server.extensions['qa_demo_crud'].history(self.editor, 1)), 1)
        # A wrong action does not consume an otherwise valid update preview.
        self.assertIn('committed', self.confirm_update(token)['qa-status']['children'])
        self.assertEqual((self.row()['Rev'], self.row()['version']), ('B', 2))

    def test_report_wizard_navigation_has_one_visible_panel_and_validates_settings(self):
        self.login('editor')
        panels = ['qb-source-panel', 'qb-columns-panel', 'qb-options-panel', 'qb-preview-panel']
        for old, new, trigger in ((0, 1, 'qb-next'), (1, 2, 'qb-next'), (2, 3, 'qb-next'),
                                  (3, 3, 'qb-next'), (3, 2, 'qb-back'), (0, 0, 'qb-back')):
            result = self.callback('qb-step.data', trigger, self.wizard_values({'qb-step': old}))
            self.assertEqual(result['qb-step']['data'], new)
            for index, panel in enumerate(panels):
                self.assertEqual(result[panel]['style'], {} if index == new else {'display': 'none'})
        for bad in ({'qb-step': True}, {'qb-step': 4}, {'qb-source': 'company-oracle'}, {'qb-columns': []}):
            result = self.callback('qb-step.data', 'qb-next', self.wizard_values(bad))
            self.assertTrue(result['qb-nav-status']['children'])
            self.assertNotIn('qb-step', result)

    def test_report_filters_grouping_order_and_preview_limit(self):
        self.login('reader')
        result = self.report_preview({'qb-filter-field': 'City', 'qb-filter-op': 'eq', 'qb-filter-value': 'Taipei',
                                      'qb-columns': ['City', 'Vendor_Code']})
        self.assertEqual(result['qb-table']['data'], [{'City': 'Taipei', 'Vendor_Code': 'SYN001'}])
        self.assertEqual([item['id'] for item in result['qb-table']['columns']], ['City', 'Vendor_Code'])
        result = self.report_preview({'qb-group': ['Country']})
        self.assertEqual(result['qb-table']['data'], [{'Country': 'TW', 'count': 2}])
        self.assertEqual([item['id'] for item in result['qb-table']['columns']], ['Country', 'count'])
        self.assertEqual(result['qb-preview-definition']['data']['aggregate'], 'count')
        result = self.report_preview({'qb-limit': 1})
        self.assertEqual(len(result['qb-table']['data']), 1)
        self.assertIn('上限', result['qb-preview-status']['children'])
        for literal in ('%', '_', "' OR 1=1 --"):
            result = self.report_preview({'qb-filter-field': 'Vendor_Name', 'qb-filter-value': literal})
            self.assertEqual(result['qb-table']['data'], [])

    def test_report_preview_rejects_tampered_source_fields_operations_and_bounds(self):
        self.login('editor')
        for bad in ({'qb-source': 'SELECT * FROM legacy_qsl_records'},
                    {'qb-columns': ['Vendor_Code; DROP TABLE legacy_qsl_records']},
                    {'qb-filter-field': 'owner_id'}, {'qb-filter-field': 'Country', 'qb-filter-op': 'sql'},
                    {'qb-group': ['Country', 'Country']}, {'qb-limit': 501}, {'qb-limit': True}):
            with self.subTest(config=bad):
                result = self.report_preview(bad)
                self.assertIn('未完成', result['qb-preview-status']['children'])
                self.assertEqual(result['qb-table']['data'], [])
                self.assertIsNone(result['qb-preview-definition']['data'])
        self.assertEqual(len(self.crud('qa-read')['qa-table']['data']), 2)

    def test_report_save_requires_current_preview_and_forbids_injected_definition(self):
        self.login('editor')
        definition = self.report_preview()['qb-preview-definition']['data']
        for supplied, changes in ((None, {}), (definition, {'qb-limit': 1}),
                                  (dict(definition, sql='SELECT * FROM legacy_qsl_records'), {}),
                                  (dict(definition, python='__import__("os")'), {}),
                                  (dict(definition, owner_id='developer'), {})):
            with self.subTest(supplied=supplied, changes=changes):
                result = self.report_save(supplied, changes)
                self.assertIn('未完成', result['qb-save-status']['children'])
                self.assertEqual(self.server.extensions['qa_demo_builder'].list_definitions(self.editor), [])
        self.assertEqual(self.row()['version'], 1)

    def test_report_save_load_and_optimistic_version_conflict(self):
        self.login('editor')
        selected, definition, result = self.save_report({'qb-filter-field': 'Country', 'qb-filter-op': 'eq',
                                                         'qb-filter-value': 'TW', 'qb-group': ['Country']})
        self.assertIsInstance(selected, str)  # Mantine Select serializes IDs as strings.
        loaded = self.report_load(selected)
        self.assertNotIn('無法載入', loaded['qb-save-status']['children'])
        self.assertEqual(loaded['qb-group']['value'], ['Country'])
        self.assertEqual(loaded['qb-filter-value']['value'], 'TW')
        self.assertEqual(loaded['qb-saved-version']['data'], {'name': 'My QSL Report', 'version': 1})
        self.assertIsNone(loaded['qb-preview-definition']['data'])
        self.assertEqual(loaded['qb-table']['data'], [])
        self.assertEqual(loaded['qb-table']['columns'], [])
        self.assertTrue(loaded['qb-preview-status']['children'])
        changes = {'qb-filter-field': 'Country', 'qb-filter-op': 'eq', 'qb-filter-value': 'TW',
                   'qb-group': ['Country'], 'qb-saved-version': copy.deepcopy(loaded['qb-saved-version']['data'])}
        updated = self.report_save(definition, changes)
        self.assertEqual(updated['qb-saved-version']['data']['version'], 2)
        conflict = self.report_save(definition, changes)
        self.assertIn('未完成', conflict['qb-save-status']['children'])
        saved = self.server.extensions['qa_demo_builder'].load(self.editor, int(selected))
        self.assertEqual(saved['version'], 2)

    def test_saved_reports_are_private_and_readers_cannot_save(self):
        self.login('editor')
        selected, definition, unused = self.save_report()
        for identity in ('developer', 'reader', 'admin'):
            with self.subTest(identity=identity):
                self.login(identity)
                listing = self.callback('qb-save-status.children', 'qb-list', self.wizard_values())
                self.assertEqual(listing['qb-saved-id']['data'], [])
                # Try both the real Select string and a forged integer ID.
                for identifier in (selected, int(selected)):
                    loaded = self.report_load(identifier)
                    self.assertIn('無法載入', loaded['qb-save-status']['children'])
                    self.assertNotIn('qb-source', loaded)
                    self.assertEqual(loaded['qb-table']['data'], [])
        self.login('reader')
        self.assertEqual(len(self.report_preview()['qb-table']['data']), 2)
        result = self.report_save(definition)
        self.assertIn('未完成', result['qb-save-status']['children'])
        self.login('editor')
        self.assertEqual(len(self.server.extensions['qa_demo_builder'].list_definitions(self.editor)), 1)

    def test_unauthenticated_and_outsider_reports_do_not_release_data(self):
        for identity in ('anonymous', 'outsider'):
            with self.subTest(identity=identity):
                self.login(identity)
                preview = self.report_preview()
                self.assertIn('未完成', preview['qb-preview-status']['children'])
                self.assertEqual(preview['qb-table']['data'], [])
                self.assertIsNone(preview['qb-preview-definition']['data'])
                nav = self.callback('qb-step.data', 'qb-next', self.wizard_values())
                self.assertNotIn('qb-step', nav)
                self.assertTrue(nav['qb-nav-status']['children'])
                listing = self.callback('qb-save-status.children', 'qb-list', self.wizard_values())
                self.assertIn('未完成', listing['qb-save-status']['children'])
                self.assertNotIn('qb-saved-id', listing)

    def test_persisted_restart_preserves_revisions_reports_and_does_not_reseed(self):
        with tempfile.TemporaryDirectory(prefix='qa-restart-test-') as directory:
            self.close_app(self.app)
            self.start_app(directory)
            self.login('editor')
            token, unused = self.prepare_update()
            self.confirm_update(token)
            created = self.crud('qa-create', {'qa-field-Vendor_Code': 'PERSIST01', 'qa-field-Vendor_Name': 'Restart fixture'})
            self.assertEqual(len(created['qa-table']['data']), 3)
            selected, definition, unused = self.save_report()
            self.close_app(self.app)
            self.start_app(directory)
            self.login('editor')
            self.assertEqual(len(self.crud('qa-read')['qa-table']['data']), 3)
            self.assertEqual((self.row()['Rev'], self.row()['version']), ('B', 2))
            self.assertEqual([item['version'] for item in self.history()['qa-history-table']['data']], [2, 1])
            saved = self.server.extensions['qa_demo_builder'].load(self.editor, int(selected))
            self.assertEqual(saved['definition'], definition)
            loaded = self.report_load(selected)
            self.assertEqual(loaded['qb-name']['value'], 'My QSL Report')
            self.assertEqual(loaded['qb-saved-version']['data']['version'], 1)
            self.close_app(self.app)


if __name__ == '__main__':
    unittest.main()
