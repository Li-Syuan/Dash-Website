"""Main-factory QSL portal contracts using real HTTP, login and SQLite.

All callbacks are dispatched through the root Flask/Dash application. No
company adapters, network clients, callback functions or policy checks are
mocked; service access is used only to inspect committed outcomes.
"""
import base64
import copy
import csv
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from reporting_workspace.application import create_app
from reporting_workspace.lifecycle import dispose_app
from reporting_workspace.config import Settings
from reporting_workspace.legacy_demo_ui import FIELDS
from reporting_workspace.providers import DemoIdentityProvider


PORTAL = '/QA_portal/maintenance'
PASSWORD = 'demo-only'
SECRET = 'unified-portal-local-test-secret-never-for-deployment'


def components(node):
    if isinstance(node, list):
        for child in node:
            yield from components(child)
    elif isinstance(node, dict) and 'props' in node:
        yield node
        yield from components(node['props'].get('children'))


class UnifiedPortalTransportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='unified-portal-test-')
        self.addCleanup(self.directory.cleanup)
        self.servers = []
        self.identities = DemoIdentityProvider()
        self.settings = Settings(secret_key=SECRET,
                                 state_path=str(Path(self.directory.name) / 'workspace.sqlite'))
        self.editor = SimpleNamespace(id='demo-admin', orgcode='ORG_QA01',
                                      is_authenticated=True, is_dev=True, is_admin=False)
        self.start_app()

    def start_app(self, settings=None):
        self.server = create_app(settings or self.settings, identity_provider=self.identities)
        self.servers.append(self.server)
        self.server.config['TESTING'] = True
        self.app = self.server.extensions['dash_app']
        self.client = self.server.test_client()
        self.assertEqual(self.client.get('/').status_code, 200)

    def close_app(self, server):
        if server not in self.servers:
            return
        dispose_app(server)
        self.servers.remove(server)

    def tearDown(self):
        for server in list(self.servers):
            self.close_app(server)

    def call(self, output_prefix, trigger, values=None, headers=None, expected=200, changed=None):
        """Build the real registered wire shape, including duplicate outputs."""
        values = values or {}
        matches = [(key, spec) for key, spec in self.app.callback_map.items()
                   if output_prefix in key and any(item['id'] == trigger for item in spec['inputs'])]
        self.assertEqual(len(matches), 1, (output_prefix, trigger))
        key, spec = matches[0]
        outputs = spec['output']
        many = isinstance(outputs, (tuple, list))
        outputs = outputs if many else [outputs]
        output_specs = [{'id': item.component_id, 'property': item.component_property} for item in outputs]
        trigger_prop = next(item['property'] for item in spec['inputs'] if item['id'] == trigger)

        def value(item, default=None):
            return values.get(item['id'] + '.' + item['property'], values.get(item['id'], default))

        response = self.client.post('/_dash-update-component', headers=headers, json={
            'output': key, 'outputs': output_specs if many else output_specs[0],
            'inputs': [dict(item, value=value(item, 1 if item['id'] == trigger else None))
                       for item in spec['inputs']],
            'state': [dict(item, value=value(item)) for item in spec['state']],
            'changedPropIds': changed if changed is not None else [trigger + '.' + trigger_prop],
        })
        self.assertEqual(response.status_code, expected, response.get_data(as_text=True))
        return response.get_json()['response'] if expected == 200 else response

    def login(self, username='demo-admin', password=PASSWORD, headers=None, expected=200):
        result = self.call('redirectHome.pathname', 'login-box', {
            'username-box': username, 'password-box': password}, headers, expected)
        if expected == 200:
            self.assertEqual(result['login-alert']['is_open'], password != PASSWORD)
        return result

    def page(self, path, headers=None):
        return self.call('_pages_content.children', '_pages_location', {
            '_pages_location.pathname': path, '_pages_location.search': ''}, headers)

    def crud(self, trigger, extra=None, headers=None, expected=200):
        fields = dict(Material_Type='IC', Vendor_Code='SYN001', Vendor_Name='Synthetic Vendor 1',
                      Country='TW', City='Taipei', Rev='B', Supplier_Level='LEVEL 1')
        values = {'qa-filter': '', 'qa-record-id': 1, 'qa-record-version': 1,
                  'qa-delete-confirm': True, 'qa-change-token': None}
        values.update({'qa-field-' + field: fields[field] for field in FIELDS})
        values.update(extra or {})
        # Browser workflow now queries the target before update/delete and
        # keeps create fields in a separate modal.
        values.update({'qa-create-field-' + f: values.get('qa-field-' + f) for f in FIELDS})
        if trigger in ('qa-update', 'qa-submit-update', 'qa-delete') and expected == 200:
            is_delete = trigger == 'qa-delete'
            target = values.get('qa-record-id', 1)
            loaded = self.call('qa-delete-record.children' if is_delete else 'qa-load-status.children',
                'qa-query-delete' if is_delete else 'qa-load-id',
                {'qa-delete-id' if is_delete else 'qa-record-id': target})
            proof_id = 'qa-loaded-delete' if is_delete else 'qa-loaded-update'
            values[proof_id] = loaded.get(proof_id, {}).get('data')
            values['qa-delete-id'] = target
        return self.call('qa-status.children', trigger, values, headers, expected)

    def row(self, record_id=1):
        return self.server.extensions['qa_demo_crud'].get(self.editor, record_id)

    def test_revocation_after_export_prevents_download_publication(self):
        service = self.server.extensions['qa_demo_crud']
        account = dict(self.identities.user_db['demo-admin'])
        for action, method in (('qa-export', 'export_csv'),
                               ('qa-export-xlsx', 'export_xlsx'),
                               ('qa-template', 'template_csv')):
            for change in ('account_removed', 'organization_changed', 'role_changed'):
                with self.subTest(action=action, change=change):
                    self.identities.user_db['demo-admin'] = dict(account)
                    self.login()
                    original = getattr(service, method)

                    def export_then_revoke(*args, **kwargs):
                        content = original(*args, **kwargs)
                        self.assertTrue(content)
                        if change == 'account_removed':
                            del self.identities.user_db['demo-admin']
                        elif change == 'organization_changed':
                            self.identities.user_db['demo-admin']['org'] = 'B'
                        else:
                            self.identities.user_db['demo-admin']['role'] = 'user'
                        return content

                    with patch.object(service, method, export_then_revoke):
                        response = self.crud(action)
                    self.assertNotIn('qa-download', response)
                    self.assertEqual(response['qa-table']['data'], [])
                    self.assertTrue(response['qa-status']['children'].startswith('操作未完成'))
        self.identities.user_db['demo-admin'] = account

    def prepare_update(self, extra=None):
        result = self.crud('qa-update', extra)
        self.assertNotIn('未完成', result['qa-status']['children'])
        token = result['qa-change-token']['data']
        self.assertIsInstance(token, str)
        self.assertTrue(token)
        return token

    def confirm_update(self, token, extra=None, headers=None, expected=200):
        values = {'qa-change-token': token}
        values.update(extra or {})
        return self.crud('qa-confirm-update', values, headers, expected)

    def history(self, trigger='qa-history-load', extra=None, expected=200):
        values = {'qa-history-id': 1, 'qa-history-version': '1',
                  'qa-history-current': 1, 'qa-restore-token': None}
        values.update(extra or {})
        return self.call('qa-history-status.children', trigger, values, expected=expected)

    def wizard_values(self, extra=None):
        values = {'qb-source': 'synthetic-qsl', 'qb-columns': ['Vendor_Code', 'Vendor_Name', 'Country', 'City'],
                  'qb-filter-field': None, 'qb-filter-op': 'contains', 'qb-filter-value': '',
                  'qb-group': [], 'qb-limit': 100, 'qb-step': 0, 'qb-name': 'Main QSL Report',
                  'qb-preview-definition': None, 'qb-saved-version': None}
        values.update(extra or {})
        return values

    def preview_report(self, extra=None, expected=200):
        return self.call('qb-preview-status.children', 'qb-preview', self.wizard_values(extra), expected=expected)

    def save_report(self, definition, extra=None):
        values = self.wizard_values({'qb-preview-definition': definition})
        values.update(extra or {})
        return self.call('qb-save-status.children', 'qb-save', values)

    def create_report(self, extra=None):
        definition = self.preview_report(extra)['qb-preview-definition']['data']
        saved = self.save_report(definition, extra)
        self.assertNotIn('未完成', saved['qb-save-status']['children'])
        return saved['qb-saved-id']['data'][0]['value'], definition

    def load_report(self, selected):
        return self.call('qb-source.value', 'qb-load', {'qb-saved-id': selected})

    def test_main_login_navigation_catalog_and_registered_page_share_one_session(self):
        registry = self.server.extensions['page_registry']
        spec = registry.get_by_id('qa-maintenance')
        self.assertIsNotNone(spec)
        self.assertEqual(spec.path, PORTAL)
        self.assertEqual(self.client.get('/_dash-dependencies').status_code, 200)
        self.login()
        with self.client.session_transaction() as session:
            self.assertEqual(session['_user_id'], 'demo-admin')
            self.assertNotIn('qa_demo_identity', session)
            self.assertNotIn('role', session)
            self.assertNotIn('org', session)
        shell = self.client.get('/_dash-layout').get_json()
        self.assertIn(PORTAL, [item['props'].get('href') for item in components(shell)])
        home = self.page('/')['_pages_content']['children']
        links = [item['props'] for item in components(home) if item['props'].get('href') == PORTAL]
        self.assertTrue(links)
        self.assertTrue(any(item.get('data-report-id') == 'qa-maintenance' for item in links))
        page = self.page(PORTAL)['_pages_content']['children']
        ids = [item['props'].get('id') for item in components(page)]
        for component in ('qa-table', 'qa-change-token', 'qa-confirm-update', 'qa-history-table',
                          'qa-restore-confirm', 'qb-step', 'qb-preview', 'qb-load'):
            self.assertIn(component, ids)
        self.assertNotIn('/QA_portal/demo-login', json.dumps(page))
        self.assertEqual(len(self.crud('qa-read')['qa-table']['data']), 2)
        self.assertEqual(self.client.get('/api/reports/export.csv').status_code, 200)
        callbacks = self.server.extensions['callback_registry']
        qa_callbacks = [entry for entry in callbacks.callbacks.values() if entry.page_id == 'qa-maintenance']
        self.assertGreaterEqual(len(qa_callbacks), 5)
        covered = ' '.join(entry.output_key for entry in qa_callbacks)
        for output in ('qa-status.children', 'qa-load-status.children', 'qa-history-status.children',
                       'qa-mail-result.children', 'qb-step.data', 'qb-preview-status.children',
                       'qb-save-status.children', 'qb-source.value'):
            self.assertIn(output, covered)
        targets = []
        for callback in self.app.callback_map.values():
            output = callback['output']
            output = output if isinstance(output, (list, tuple)) else [output]
            targets.extend((item.component_id_str(), item.component_property) for item in output)
        self.assertEqual(len(targets), len(set(targets)))
        for entry in qa_callbacks:
            self.assertIs(callbacks.policy_for(entry.output_key), entry.policy)
            self.assertTrue(entry.policy.authenticated)
            self.assertEqual(entry.policy.org, 'A')
            self.assertEqual(set(entry.policy.roles), {'admin', 'user'})
        self.assertFalse(any(rule.rule == '/QA_portal/demo-login' for rule in self.server.url_map.iter_rules()))
        self.assertEqual(self.client.post('/QA_portal/demo-login', data={'identity': 'developer'}).status_code, 405)

    def test_anonymous_and_wrong_login_cannot_use_any_portal_callback(self):
        page = self.page(PORTAL)['_pages_content']['children']
        self.assertEqual(page['props']['pathname'], '/login')
        self.login(password='incorrect')
        self.crud('qa-read', expected=401)
        self.crud('qa-create', expected=401)
        self.history(expected=401)
        self.preview_report(expected=401)
        self.call('qb-save-status.children', 'qb-list', self.wizard_values(), expected=401)
        self.assertEqual(self.row()['version'], 1)

    def test_reader_can_read_export_and_preview_but_cannot_write(self):
        self.login('demo-user-a')
        self.assertEqual(len(self.crud('qa-read')['qa-table']['data']), 2)
        exported = self.crud('qa-export')['qa-download']['data']
        rows = list(csv.DictReader(io.StringIO(base64.b64decode(exported['content']).decode('utf-8-sig'))))
        self.assertEqual(len(rows), 2)
        loaded = self.call('qa-load-status.children', 'qa-load-id', {'qa-record-id': 1})
        self.assertEqual(loaded['qa-field-Vendor_Code']['value'], 'SYN001')
        definition = self.preview_report()['qb-preview-definition']['data']
        for action in ('qa-create', 'qa-update', 'qa-delete', 'qa-import', 'qa-confirm-update'):
            with self.subTest(action=action):
                result = self.crud(action)
                self.assertIn('未完成', result['qa-status']['children'])
                self.assertEqual(result['qa-table']['data'], [])
        self.assertIn('未完成', self.history()['qa-history-status']['children'])
        self.assertIn('未完成', self.save_report(definition)['qb-save-status']['children'])
        self.assertEqual(self.row()['version'], 1)
        self.assertEqual(self.server.extensions['qa_demo_builder'].list_definitions(self.editor), [])

    def test_readonly_layout_disables_write_controls_but_admin_can_use_them(self):
        controls = ('qa-create', 'qa-update', 'qa-confirm-update', 'qa-delete', 'qa-upload',
                    'qa-import', 'qa-history-load', 'qa-restore-preview', 'qa-restore-confirm',
                    'qa-mail-save', 'qa-mail-run', 'qb-save')
        for identity, disabled in (('demo-user-a', True), ('demo-admin', False)):
            with self.subTest(identity=identity):
                self.login(identity)
                page = self.page(PORTAL)['_pages_content']['children']
                by_id = {item['props']['id']: item['props'] for item in components(page)
                         if isinstance(item['props'].get('id'), str)}
                for identifier in controls:
                    self.assertEqual(bool(by_id[identifier].get('disabled', False)), disabled,
                                     (identity, identifier))
                self.assertFalse(by_id['qa-read'].get('disabled', False))
                self.assertFalse(by_id['qb-preview'].get('disabled', False))

    def test_legacy_owner_id_has_no_special_write_permission_in_main_app(self):
        self.identities.user_db['owner01'] = dict(password=PASSWORD, role='user', org='A')
        self.login('owner01')
        self.assertEqual(len(self.crud('qa-read')['qa-table']['data']), 2)
        for action in ('qa-create', 'qa-update', 'qa-delete'):
            self.assertIn('未完成', self.crud(action)['qa-status']['children'])
        self.assertIn('未完成', self.history()['qa-history-status']['children'])
        definition = self.preview_report()['qb-preview-definition']['data']
        self.assertIn('未完成', self.save_report(definition)['qb-save-status']['children'])
        self.assertEqual(self.row()['version'], 1)

    def test_other_organization_has_no_navigation_layout_or_callback_access(self):
        self.login('demo-user-b')
        shell = self.client.get('/_dash-layout').get_json()
        self.assertNotIn(PORTAL, [item['props'].get('href') for item in components(shell)])
        home = self.page('/')['_pages_content']['children']
        self.assertNotIn(PORTAL, [item['props'].get('href') for item in components(home)])
        page = self.page(PORTAL)['_pages_content']['children']
        self.assertIn('403', json.dumps(page))
        self.assertNotIn('qa-table', json.dumps(page))
        for action in ('qa-read', 'qa-create', 'qa-export', 'qa-confirm-update'):
            self.crud(action, expected=403)
        self.history(expected=403)
        self.preview_report(expected=403)
        self.call('qb-save-status.children', 'qb-list', self.wizard_values(), expected=403)
        self.assertEqual(self.row()['version'], 1)

    def test_standalone_identity_and_forged_claims_do_not_bypass_main_login(self):
        with self.client.session_transaction() as session:
            session['qa_demo_identity'] = 'developer'
            session['role'] = 'admin'
            session['org'] = 'A'
        self.crud('qa-read', expected=401)
        self.login('demo-user-b')
        with self.client.session_transaction() as session:
            session['qa_demo_identity'] = 'developer'
            session['role'] = 'admin'
            session['org'] = 'A'
        self.crud('qa-read', {'qa-field-role': 'admin', 'qa-field-org': 'A'}, expected=403)

    def test_live_role_organization_and_account_revocation_apply_to_pending_preview(self):
        self.login()
        token = self.prepare_update()
        record = self.identities.user_db['demo-admin']
        record['role'] = 'user'
        self.assertIn('未完成', self.confirm_update(token)['qa-status']['children'])
        self.assertEqual(len(self.crud('qa-read')['qa-table']['data']), 2)
        self.assertIn('未完成', self.history()['qa-history-status']['children'])
        record['role'] = 'admin'
        record['org'] = 'B'
        self.confirm_update(token, expected=403)
        self.preview_report(expected=403)
        self.assertIn('403', json.dumps(self.page(PORTAL)))
        record['org'] = 'A'
        record['role'] = 'unrecognized'
        self.crud('qa-read', expected=403)
        self.identities.user_db.pop('demo-admin')
        self.confirm_update(token, expected=401)
        # The revoked actor also loses direct service access. An independent
        # same-organization reader verifies that no pending change committed.
        from reporting_workspace.legacy_crud import PermissionDenied
        with self.assertRaises(PermissionDenied):
            self.row()
        reader = SimpleNamespace(id='demo-user-a', orgcode='ORG_QA01',
                                 is_authenticated=True, is_dev=False,
                                 is_admin=False)
        row = self.server.extensions['qa_demo_crud'].get(reader, 1)
        self.assertEqual((row['Rev'], row['version']), ('A', 1))

    def test_both_main_logout_transports_revoke_portal_access(self):
        for transport in ('get', 'callback'):
            with self.subTest(transport=transport):
                self.login()
                token = self.prepare_update()
                if transport == 'get':
                    self.assertEqual(self.client.get('/logout').status_code, 200)
                else:
                    page = self.page('/logout')['_pages_content']['children']
                    self.assertEqual(page['props']['pathname'], '/login')
                with self.client.session_transaction() as session:
                    self.assertNotIn('_user_id', session)
                    self.assertNotIn('qa_demo_identity', session)
                self.confirm_update(token, expected=401)
                self.preview_report(expected=401)
                self.history(expected=401)
        self.assertEqual(self.row()['version'], 1)

    def test_main_origin_guard_blocks_login_logout_and_portal_confirmation(self):
        hostile = ({'Origin': 'https://evil.invalid'}, {'Sec-Fetch-Site': 'cross-site'}, {'Origin': 'null'})
        for headers in hostile:
            self.login(headers=headers, expected=403)
        self.login()
        token = self.prepare_update()
        for headers in hostile:
            self.confirm_update(token, headers=headers, expected=403)
            self.assertEqual(self.client.get('/logout', headers=headers).status_code, 403)
            with self.client.session_transaction() as session:
                self.assertEqual(session['_user_id'], 'demo-admin')
            self.assertEqual(self.row()['version'], 1)
        committed = self.confirm_update(token, headers={'Origin': 'http://localhost', 'Sec-Fetch-Site': 'same-origin'})
        self.assertIn('committed', committed['qa-status']['children'])
        self.assertEqual(self.row()['version'], 2)

    def test_admin_preview_uses_staged_values_and_is_single_use(self):
        self.login()
        before = self.row()
        token = self.prepare_update()
        self.assertEqual(self.row(), before)
        committed = self.confirm_update(token, {'qa-record-id': 2, 'qa-record-version': 999, 'qa-field-Rev': 'C'})
        self.assertIn('committed', committed['qa-status']['children'])
        self.assertIsNone(committed['qa-change-token']['data'])
        self.assertEqual((self.row()['Rev'], self.row()['version']), ('B', 2))
        self.assertEqual(self.row(2)['version'], 1)
        self.assertIn('未完成', self.confirm_update(token)['qa-status']['children'])
        self.assertEqual(len(self.history()['qa-history-table']['data']), 2)

    def test_update_preview_is_bound_to_main_identity_even_for_another_admin(self):
        self.login()
        token = self.prepare_update()
        self.identities.user_db['other-admin'] = dict(password=PASSWORD, role='admin', org='A')
        for identity in ('other-admin', 'demo-user-a'):
            self.login(identity)
            self.assertIn('未完成', self.confirm_update(token)['qa-status']['children'])
            self.assertEqual(self.row()['version'], 1)
        self.login()
        self.assertIn('committed', self.confirm_update(token)['qa-status']['children'])
        self.assertEqual(self.history()['qa-history-table']['data'][0]['actor'], 'demo-admin')

    def test_canceled_replaced_and_stale_previews_cannot_commit(self):
        self.login()
        canceled = self.prepare_update()
        self.crud('qa-cancel-update', {'qa-change-token': canceled})
        self.assertIn('未完成', self.confirm_update(canceled)['qa-status']['children'])
        old = self.prepare_update()
        current = self.prepare_update({'qa-change-token': old, 'qa-field-Rev': 'C'})
        self.assertIn('未完成', self.confirm_update(old)['qa-status']['children'])
        stale = self.prepare_update({'qa-field-Rev': 'D'})
        self.assertIn('committed', self.confirm_update(current)['qa-status']['children'])
        self.assertIn('未完成', self.confirm_update(stale)['qa-status']['children'])
        self.assertEqual((self.row()['Rev'], self.row()['version']), ('C', 2))

    def test_history_restore_records_main_actor_and_source_and_rejects_replay(self):
        self.login()
        self.confirm_update(self.prepare_update())
        loaded = self.history()
        self.assertEqual([row['version'] for row in loaded['qa-history-table']['data']], [2, 1])
        self.assertTrue(all(isinstance(item['value'], str) for item in loaded['qa-history-version']['data']))
        prepared = self.history('qa-restore-preview', {'qa-history-current': 2})
        token = prepared['qa-restore-token']['data']
        self.assertEqual(self.row()['version'], 2)
        self.identities.user_db['other-admin'] = dict(password=PASSWORD, role='admin', org='A')
        self.login('other-admin')
        denied = self.history('qa-restore-confirm', {'qa-restore-token': token})
        self.assertIn('未完成', denied['qa-history-status']['children'])
        self.login()
        restored = self.history('qa-restore-confirm', {'qa-restore-token': token})
        self.assertEqual((self.row()['Rev'], self.row()['version']), ('A', 3))
        latest = restored['qa-history-table']['data'][0]
        self.assertEqual((latest['actor'], latest['source_version'], latest['action']),
                         ('demo-admin', 1, 'restore_version'))
        self.assertIn('未完成', self.history('qa-restore-confirm', {'qa-restore-token': token})['qa-history-status']['children'])

    def test_wizard_navigation_preview_filters_grouping_and_bounds(self):
        self.login('demo-user-a')
        panels = ('qb-source-panel', 'qb-columns-panel', 'qb-options-panel', 'qb-preview-panel')
        for old, new, trigger in ((0, 1, 'qb-next'), (1, 2, 'qb-next'), (2, 3, 'qb-next'),
                                  (3, 3, 'qb-next'), (3, 2, 'qb-back'), (0, 0, 'qb-back')):
            result = self.call('qb-step.data', trigger, self.wizard_values({'qb-step': old}))
            self.assertEqual(result['qb-step']['data'], new)
            for index, panel in enumerate(panels):
                self.assertEqual(result[panel]['style'], {} if index == new else {'display': 'none'})
        filtered = self.preview_report({'qb-filter-field': 'City', 'qb-filter-op': 'eq',
                                        'qb-filter-value': 'Taipei', 'qb-columns': ['City', 'Vendor_Code']})
        self.assertEqual(filtered['qb-table']['data'], [{'City': 'Taipei', 'Vendor_Code': 'SYN001'}])
        grouped = self.preview_report({'qb-group': ['Country']})
        self.assertEqual(grouped['qb-table']['data'], [{'Country': 'TW', 'count': 2}])
        self.assertEqual(len(self.preview_report({'qb-limit': 1})['qb-table']['data']), 1)
        for bad in ({'qb-source': 'company-oracle'}, {'qb-columns': ['owner_id']},
                    {'qb-filter-field': 'Country', 'qb-filter-op': 'sql'}, {'qb-limit': 501}):
            with self.subTest(config=bad):
                result = self.preview_report(bad)
                self.assertEqual(result['qb-table']['data'], [])
                self.assertIsNone(result['qb-preview-definition']['data'])
                self.assertIn('未完成', result['qb-preview-status']['children'])

    def test_saved_wizard_definitions_require_preview_and_remain_owner_private(self):
        self.login()
        definition = self.preview_report()['qb-preview-definition']['data']
        for supplied, extra in ((None, {}), (definition, {'qb-limit': 1}),
                                (dict(definition, sql='SELECT * FROM company'), {}),
                                (dict(definition, owner_id='other-admin'), {})):
            result = self.save_report(supplied, extra)
            self.assertIn('未完成', result['qb-save-status']['children'])
        selected, definition = self.create_report()
        self.assertIsInstance(selected, str)
        loaded = self.load_report(selected)
        self.assertEqual(loaded['qb-name']['value'], 'Main QSL Report')
        self.assertEqual(loaded['qb-saved-version']['data'], {'name': 'Main QSL Report', 'version': 1})
        self.assertIsNone(loaded['qb-preview-definition']['data'])
        self.assertEqual(loaded['qb-table']['data'], [])
        version = {'qb-saved-version': copy.deepcopy(loaded['qb-saved-version']['data'])}
        self.assertEqual(self.save_report(definition, version)['qb-saved-version']['data']['version'], 2)
        self.assertIn('未完成', self.save_report(definition, version)['qb-save-status']['children'])
        self.identities.user_db['other-admin'] = dict(password=PASSWORD, role='admin', org='A')
        for identity in ('other-admin', 'demo-user-a'):
            self.login(identity)
            listing = self.call('qb-save-status.children', 'qb-list', self.wizard_values())
            self.assertEqual(listing['qb-saved-id']['data'], [])
            for identifier in (selected, int(selected)):
                denied = self.load_report(identifier)
                self.assertIn('無法載入', denied['qb-save-status']['children'])
                self.assertNotIn('qb-source', denied)
        self.assertEqual(self.server.extensions['qa_demo_builder'].load(self.editor, int(selected))['version'], 2)

    def test_ambiguous_consolidated_wizard_dispatch_does_not_save_or_leak_data(self):
        self.login()
        definition = self.preview_report()['qb-preview-definition']['data']
        values = self.wizard_values({'qb-preview-definition': definition, 'qb-saved-id': '1'})
        for changed in (['qb-save.n_clicks', 'qb-load.n_clicks'],
                        ['qb-preview.n_clicks', 'qb-save.n_clicks'],
                        ['unregistered.n_clicks'], []):
            with self.subTest(changed=changed):
                response = self.call('qb-save-status.children', 'qb-save', values,
                                     expected=204, changed=changed)
                self.assertEqual(response.get_data(), b'')
                self.assertEqual(self.server.extensions['qa_demo_builder'].list_definitions(self.editor), [])
        saved = self.save_report(definition)
        self.assertEqual(saved['qb-saved-version']['data']['version'], 1)

    def test_csv_import_and_delete_use_main_identity_with_single_use_staging(self):
        self.login()
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=['id', 'version'] + FIELDS)
        writer.writeheader()
        writer.writerow(dict(Material_Type='IC', Vendor_Code='MAINCSV', Vendor_Name='Main import fixture',
                             Country='TW', City='Tainan', Rev='A', Supplier_Level='LEVEL 2'))
        content = 'data:text/csv;base64,' + base64.b64encode(stream.getvalue().encode()).decode()
        staged = self.crud('qa-upload', {'qa-upload.contents': content, 'qa-upload.filename': 'fixture.csv'})
        token = staged['qa-stage-token']['data']
        self.assertEqual(json.loads(staged['qa-preview']['children'])['count'], 1)
        self.assertEqual(len(self.crud('qa-read')['qa-table']['data']), 2)
        self.identities.user_db['other-admin'] = dict(password=PASSWORD, role='admin', org='A')
        self.login('other-admin')
        self.assertIn('未完成', self.crud('qa-import', {'qa-stage-token': token})['qa-status']['children'])
        self.login()
        imported = self.crud('qa-import', {'qa-stage-token': token})
        self.assertEqual(len(imported['qa-table']['data']), 3)
        row = next(item for item in imported['qa-table']['data'] if item['Vendor_Code'] == 'MAINCSV')
        self.assertIn('未完成', self.crud('qa-import', {'qa-stage-token': token})['qa-status']['children'])
        args = {'qa-record-id': row['id'], 'qa-record-version': row['version'], 'qa-delete-confirm': False}
        self.assertIn('未完成', self.crud('qa-delete', args)['qa-status']['children'])
        args['qa-delete-confirm'] = True
        self.assertEqual(len(self.crud('qa-delete', args)['qa-table']['data']), 2)
        history = self.history(extra={'qa-history-id': row['id']})['qa-history-table']['data']
        self.assertEqual([item['action'] for item in history], ['delete', 'create'])
        self.assertTrue(all(item['actor'] == 'demo-admin' for item in history))

    def test_main_admin_mock_job_uses_same_identity_and_never_duplicates_run(self):
        self.login()
        values = {'qa-mail-to': 'qa@example.invalid', 'qa-run-key': 'main-once', 'qa-mail-version': 0,
                  'qa-mail-cadence': 'daily', 'qa-mail-time': '10:00', 'qa-mail-timezone': 'UTC',
                  'qa-mail-enabled': True, 'qa-mail-delivery': 'accepted', 'qa-source-ok': True, 'qa-report-ok': True}
        saved = self.call('qa-mail-result.children', 'qa-mail-save', values)
        self.assertEqual(saved['qa-mail-version']['data'], 1)
        values['qa-mail-version'] = 1
        run = self.call('qa-mail-result.children', 'qa-mail-run', values)
        self.assertEqual(json.loads(run['qa-mail-result']['children'])['status'], 'accepted')
        repeated = self.call('qa-mail-result.children', 'qa-mail-run', values)
        self.assertIn('already claimed', repeated['qa-mail-result']['children'])
        records = self.call('qa-mail-result.children', 'qa-mail-refresh', values)
        runs = json.loads(records['qa-mail-result']['children'])['runs']
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]['actor'], 'demo-admin')
        self.login('demo-user-a')
        for action in ('qa-mail-save', 'qa-mail-run'):
            self.assertIn('未完成', self.call('qa-mail-result.children', action, values)['qa-mail-result']['children'])

    def test_restart_retains_real_sqlite_records_history_and_wizard_definitions(self):
        self.login()
        self.confirm_update(self.prepare_update())
        created = self.crud('qa-create', {'qa-field-Vendor_Code': 'PERSIST01', 'qa-field-Vendor_Name': 'Restart fixture'})
        self.assertEqual(len(created['qa-table']['data']), 3)
        selected, definition = self.create_report({'qb-filter-field': 'Country', 'qb-filter-op': 'eq',
                                                   'qb-filter-value': 'TW', 'qb-group': ['Country']})
        qa_directory = Path(self.server.extensions['qa_demo_directory'])
        for filename in ('qsl.sqlite', 'report_builder.sqlite', 'jobs.sqlite'):
            with (qa_directory / filename).open('rb') as handle:
                self.assertEqual(handle.read(16), b'SQLite format 3\x00')
        self.close_app(self.server)
        self.start_app()
        self.assertEqual(Path(self.server.extensions['qa_demo_directory']), qa_directory)
        self.crud('qa-read', expected=401)
        self.login()
        self.assertEqual(len(self.crud('qa-read')['qa-table']['data']), 3)
        self.assertEqual((self.row()['Rev'], self.row()['version']), ('B', 2))
        self.assertEqual([item['version'] for item in self.history()['qa-history-table']['data']], [2, 1])
        saved = self.server.extensions['qa_demo_builder'].load(self.editor, int(selected))
        self.assertEqual(saved['definition'], definition)
        loaded = self.load_report(selected)
        self.assertEqual(loaded['qb-group']['value'], ['Country'])
        self.assertEqual(loaded['qb-filter-value']['value'], 'TW')
        self.assertEqual(loaded['qb-saved-version']['data']['version'], 1)


if __name__ == '__main__':
    unittest.main()
