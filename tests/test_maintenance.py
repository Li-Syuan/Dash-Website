"""Real Flask/Dash callback transport tests for durable metadata maintenance."""

import hashlib
import json
import secrets
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from reporting_workspace.application import create_app
from reporting_workspace.config import Settings
from reporting_workspace.ui_pages import default_pages
from reporting_workspace.ui_pages.maintenance import PAGE_SIZE, POLICY, SPEC


USERS = {
    'admin-a': {'id': 'admin-a', 'role': 'admin', 'org': 'A'},
    'user-a': {'id': 'user-a', 'role': 'user', 'org': 'A'},
    'other-a': {'id': 'other-a', 'role': 'user', 'org': 'A'},
    'admin-b': {'id': 'admin-b', 'role': 'admin', 'org': 'B'},
    'guest-a': {'id': 'guest-a', 'role': 'guest', 'org': 'A'},
}


class Identities:
    is_demo = False

    def get_user(self, user_id):
        return USERS.get(user_id)

    def authenticate(self, username, password):
        return USERS.get(username) if password == 'fixture-only' else None


class Reports:
    is_demo = False
    columns = ['period', 'department', 'revenue', 'cost', 'profit']

    def rows(self, user):
        return []

    def export(self, user):
        return ','.join(self.columns) + '\n'


def walk(node):
    if isinstance(node, list):
        for child in node:
            yield from walk(child)
    elif isinstance(node, dict) and 'props' in node:
        yield node
        yield from walk(node['props'].get('children'))


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.server = self.make_app(state=True)
        self.runtime = self.server.extensions['workspace']
        self.service = self.runtime.definitions
        self.client = self.authenticated(self.server, 'user-a')
        self.values = self.defaults()

    def make_app(self, state):
        settings = Settings(secret_key='maintenance-transport-test-secret-423789',
                            state_path=str(Path(self.directory.name) / 'state.sqlite3') if state else None)
        extras = () if any(page.page_id == 'maintenance' for page in default_pages()) else (SPEC,)
        server = create_app(settings, Identities(), Reports(), extra_pages=extras)
        server.config['TESTING'] = True
        return server

    @staticmethod
    def authenticated(server, username):
        client = server.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = username
            session['_fresh'] = True
        return client

    @staticmethod
    def defaults():
        return {
            ('maintenance-search', 'value'): '', ('maintenance-show-archived', 'value'): [],
            ('maintenance-prev', 'n_clicks'): 0, ('maintenance-next', 'n_clicks'): 0,
            ('maintenance-refresh', 'n_clicks'): 0, ('maintenance-new', 'n_clicks'): 0,
            ('maintenance-page', 'data'): 0, ('maintenance-mutation', 'data'): None,
            ('maintenance-table', 'selected_rows'): [], ('maintenance-table', 'data'): [],
            ('maintenance-record', 'data'): None, ('maintenance-reload', 'n_clicks'): 0,
            ('maintenance-draft', 'data'): secrets.token_hex(16),
            ('maintenance-save', 'n_clicks'): 0, ('maintenance-archive', 'n_clicks'): 0,
            ('maintenance-restore', 'n_clicks'): 0, ('maintenance-name', 'value'): '',
            ('maintenance-description', 'value'): '', ('maintenance-cadence', 'value'): 'manual',
            ('maintenance-enabled', 'value'): True, ('maintenance-name', 'disabled'): False,
        }

    def call(self, callback_id, changed, client=None, values=None):
        client = client or self.client
        values = self.values if values is None else values
        server = client.application
        registry = server.extensions['callback_registry']
        specs = [spec for spec in registry.callbacks.values() if spec.callback_id == callback_id]
        self.assertEqual(len(specs), 1)
        entry = server.extensions['dash_app'].callback_map[specs[0].output_key]
        output = entry['output']
        outputs = output if isinstance(output, (list, tuple)) else (output,)
        wire_outputs = [{'id': item.component_id, 'property': item.component_property} for item in outputs]
        response = client.post('/_dash-update-component', json={
            'output': specs[0].output_key,
            'outputs': wire_outputs if isinstance(output, (list, tuple)) else wire_outputs[0],
            'inputs': [dict(item, value=values.get((item['id'], item['property']))) for item in entry['inputs']],
            'state': [dict(item, value=values.get((item['id'], item['property']))) for item in entry['state']],
            'changedPropIds': list(changed) if isinstance(changed, (list, tuple)) else [changed] if changed else [],
        })
        if response.status_code == 200:
            for component_id, properties in response.get_json()['response'].items():
                if not component_id.startswith('{'):
                    for prop, value in properties.items():
                        values[(component_id, prop)] = value
        return response

    def assert_ok(self, response):
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()['response']

    def assert_notice(self, response, code, kind):
        body = self.assert_ok(response)
        events = [value['data'] for key, value in body.items()
                  if key.startswith('{') and json.loads(key).get('type') == 'workspace-notify']
        self.assertEqual(len(events), 1, body)
        event = events[0]
        self.assertEqual((event['code'], event['kind']), (code, kind))
        self.assertEqual(set(event), {'event_id', 'code', 'kind', 'audience', 'request_id'})
        self.assertEqual(event['request_id'], response.headers['X-Request-ID'])
        return body

    def create(self, name='Quarterly plan'):
        self.values[('maintenance-name', 'value')] = name
        self.values[('maintenance-description', 'value')] = 'Shared planning metadata'
        self.values[('maintenance-cadence', 'value')] = 'monthly'
        response = self.call('maintenance.mutate', 'maintenance-save.n_clicks')
        self.assert_notice(response, 'maintenance.created', 'success')
        self.call('maintenance.select', 'maintenance-mutation.data')
        return self.values[('maintenance-record', 'data')]

    def seed(self, name, username='user-a'):
        return self.service.create(USERS[username], {'name': name})

    def route(self, client=None):
        return (client or self.client).post('/_dash-update-component', json={
            'output': '.._pages_content.children..._pages_store.data..',
            'outputs': [{'id': '_pages_content', 'property': 'children'},
                        {'id': '_pages_store', 'property': 'data'}],
            'inputs': [{'id': '_pages_location', 'property': 'pathname', 'value': '/maintenance'},
                       {'id': '_pages_location', 'property': 'search', 'value': ''}],
            'state': [], 'changedPropIds': ['_pages_location.pathname'],
        })

    def test_page_metadata_and_each_callback_have_explicit_matching_policy(self):
        self.assertEqual((SPEC.path, SPEC.title, SPEC.nav_order), ('/maintenance', 'Report definitions', 50))
        self.assertEqual(POLICY.roles, ('admin', 'user'))
        specs = [spec for spec in self.server.extensions['callback_registry'].callbacks.values()
                 if spec.page_id == 'maintenance']
        self.assertEqual({spec.callback_id for spec in specs},
                         {'maintenance.list', 'maintenance.select', 'maintenance.mutate', 'maintenance.feedback'})
        self.assertTrue(all(spec.policy == POLICY for spec in specs))
        body = self.assert_ok(self.route())
        nodes = list(walk(body['_pages_content']['children']))
        by_id = {node['props']['id']: node for node in nodes if isinstance(node['props'].get('id'), str)}
        self.assertFalse(by_id['maintenance-table']['props']['editable'])
        draft = by_id['maintenance-draft']['props']
        self.assertEqual(draft['storage_type'], 'memory')
        self.assertRegex(draft['data'], r'^[0-9a-f]{32}$')
        self.assertEqual(by_id['maintenance-description']['props']['maxLength'], 1000)
        self.assertIn('metadata only', json.dumps(body))
        self.assertEqual(len([node for node in nodes if node['type'] == 'Store'
                              and isinstance(node['props']['id'], dict)]), 3)

    def test_field_feedback_is_pinned_component_compatible_and_resets(self):
        body = self.assert_ok(self.route())
        nodes = {node['props'].get('id'): node for node in walk(body['_pages_content']['children'])
                 if isinstance(node['props'].get('id'), str)}
        for field in ('name', 'description', 'cadence'):
            self.assertFalse(nodes['maintenance-' + field]['props']['invalid'])
            self.assertEqual(nodes['maintenance-' + field + '-feedback']['type'], 'FormFeedback')
        response = self.call('maintenance.feedback', 'maintenance-save.n_clicks')
        body = self.assert_ok(response)
        self.assertTrue(body['maintenance-name']['invalid'])
        self.assertIn('1–120', body['maintenance-name-feedback']['children'])
        self.assertFalse(body['maintenance-description']['invalid'])
        self.assertEqual(self.service.list(USERS['user-a'])['total'], 0)
        self.values[('maintenance-name', 'value')] = 'Valid corrected name'
        body = self.assert_ok(self.call('maintenance.feedback', 'maintenance-name.value'))
        self.assertFalse(body['maintenance-name']['invalid'])
        self.assertEqual(body['maintenance-name-feedback']['children'], '')
        self.values[('maintenance-name', 'value')] = ''
        for changed in ('maintenance-draft.data', 'maintenance-record.data'):
            body = self.assert_ok(self.call('maintenance.feedback', changed))
            self.assertFalse(body['maintenance-name']['invalid'])
        self.values[('maintenance-name', 'disabled')] = True
        body = self.assert_ok(self.call('maintenance.feedback', 'maintenance-name.disabled'))
        self.assertFalse(body['maintenance-name']['invalid'])

    def test_field_feedback_reuses_service_validation_without_echoing_values(self):
        for field, value in (('name', 'x' * 121), ('name', 'bad\x00name'),
                             ('name', None), ('description', 'x' * 1001),
                             ('description', 'bad\x00description'), ('cadence', 'private-invalid-marker')):
            with self.subTest(field=field):
                self.values.update({('maintenance-name', 'value'): 'Valid',
                                    ('maintenance-description', 'value'): '',
                                    ('maintenance-cadence', 'value'): 'manual'})
                self.values[('maintenance-' + field, 'value')] = value
                response = self.call('maintenance.feedback', 'maintenance-' + field + '.value')
                body = self.assert_ok(response)
                self.assertTrue(body['maintenance-' + field]['invalid'])
                if isinstance(value, str):
                    self.assertNotIn(value, response.get_data(as_text=True))
                self.assert_notice(self.call('maintenance.mutate', 'maintenance-save.n_clicks'),
                                   'maintenance.invalid', 'warning')
        self.assertEqual(self.service.list(USERS['user-a'])['total'], 0)
        self.values.update({('maintenance-name', 'value'): '  Trimmed  ',
                            ('maintenance-description', 'value'): 'Line one\nLine two\tEnd',
                            ('maintenance-cadence', 'value'): 'monthly'})
        body = self.assert_ok(self.call('maintenance.feedback', 'maintenance-save.n_clicks'))
        for field in ('name', 'description', 'cadence'):
            self.assertFalse(body['maintenance-' + field]['invalid'])

    def test_create_select_update_and_server_owned_reference(self):
        reference = self.create()
        self.assertEqual(set(reference), {'id', 'version'})
        self.assertEqual(reference['version'], 1)
        record = self.service.get(USERS['user-a'], reference['id'])
        self.assertEqual((record['org'], record['cadence']), ('A', 'monthly'))
        self.assertEqual(record['owner_id'], hashlib.sha256(b'user-a').hexdigest())
        self.values[('maintenance-name', 'value')] = 'Updated name'
        self.values[('maintenance-enabled', 'value')] = False
        body = self.assert_notice(self.call('maintenance.mutate', 'maintenance-save.n_clicks'),
                                  'maintenance.updated', 'success')
        self.assertIn('maintenance-mutation', body)
        selected = self.assert_ok(self.call('maintenance.select', 'maintenance-mutation.data'))
        self.assertEqual(selected['maintenance-record']['data']['version'], 2)
        self.assertEqual(selected['maintenance-name']['value'], 'Updated name')
        self.assertFalse(selected['maintenance-enabled']['value'])
        self.assertIn('Version 2', selected['maintenance-record-meta']['children'])

    def test_double_save_of_new_draft_creates_one_record_and_audit(self):
        self.values[('maintenance-name', 'value')] = 'Double-click save'
        draft = self.values[('maintenance-draft', 'data')]
        first = self.assert_notice(self.call('maintenance.mutate', 'maintenance-save.n_clicks'),
                                   'maintenance.created', 'success')
        self.assertIsNone(self.values[('maintenance-record', 'data')])
        self.values[('maintenance-save', 'n_clicks')] = 2
        second = self.assert_notice(self.call('maintenance.mutate', 'maintenance-save.n_clicks'),
                                    'maintenance.created', 'success')
        self.assertEqual(first['maintenance-mutation']['data']['id'],
                         second['maintenance-mutation']['data']['id'])
        self.assertEqual(self.values[('maintenance-draft', 'data')], draft)
        self.assertEqual(self.service.list(USERS['user-a'])['total'], 1)
        created = [event for event in self.runtime.state.list_audit() if event.event == 'crud.created']
        self.assertEqual(len(created), 1)
        self.assert_ok(self.call('maintenance.select', 'maintenance-mutation.data'))
        self.assertEqual(self.values[('maintenance-draft', 'data')], draft)
        self.assert_ok(self.call('maintenance.select', 'maintenance-new.n_clicks'))
        self.assertNotEqual(self.values[('maintenance-draft', 'data')], draft)
        self.values[('maintenance-name', 'value')] = 'Double-click save'
        third = self.assert_notice(self.call('maintenance.mutate', 'maintenance-save.n_clicks'),
                                   'maintenance.created', 'success')
        self.assertNotEqual(third['maintenance-mutation']['data']['id'],
                            first['maintenance-mutation']['data']['id'])
        self.assertEqual(self.service.list(USERS['user-a'])['total'], 2)

    def test_missing_or_invalid_new_draft_key_never_creates_a_record(self):
        self.values[('maintenance-name', 'value')] = 'Valid draft name'
        for draft in (None, '', True, 4, {}, [], 'f' * 31, 'f' * 33, 'G' * 32, 'f' * 32 + '\n'):
            with self.subTest(draft=draft):
                self.values[('maintenance-draft', 'data')] = draft
                response = self.call('maintenance.mutate', 'maintenance-save.n_clicks')
                body = self.assert_notice(response, 'maintenance.invalid', 'warning')
                self.assertNotIn('maintenance-mutation', body)
                self.assertEqual(self.values[('maintenance-draft', 'data')], draft)
        self.assertEqual(self.service.list(USERS['user-a'])['total'], 0)

    def test_draft_key_is_retained_after_failure_and_update_ignores_it(self):
        draft = self.values[('maintenance-draft', 'data')]
        self.assert_notice(self.call('maintenance.mutate', 'maintenance-save.n_clicks'),
                           'maintenance.invalid', 'warning')
        self.assertEqual(self.values[('maintenance-draft', 'data')], draft)
        self.create()
        self.assertEqual(self.values[('maintenance-draft', 'data')], draft)
        self.values[('maintenance-draft', 'data')] = None
        self.values[('maintenance-name', 'value')] = 'Updated selected record'
        self.assert_notice(self.call('maintenance.mutate', 'maintenance-save.n_clicks'),
                           'maintenance.updated', 'success')

    def test_conflict_preserves_form_and_reload_gets_fresh_version(self):
        reference = self.create()
        self.service.update(USERS['user-a'], reference['id'], 1, {'name': 'Saved elsewhere'})
        self.values[('maintenance-name', 'value')] = 'Unsaved local edit'
        body = self.assert_notice(self.call('maintenance.mutate', 'maintenance-save.n_clicks'),
                                  'maintenance.conflict', 'warning')
        self.assertNotIn('maintenance-mutation', body)
        self.assertEqual(self.values[('maintenance-name', 'value')], 'Unsaved local edit')
        self.assertEqual(self.values[('maintenance-record', 'data')]['version'], 1)
        body = self.assert_ok(self.call('maintenance.select', 'maintenance-reload.n_clicks'))
        self.assertEqual(body['maintenance-name']['value'], 'Saved elsewhere')
        self.assertEqual(body['maintenance-record']['data']['version'], 2)

    def test_archive_hide_show_restore_and_version_controls(self):
        reference = self.create()
        self.assert_notice(self.call('maintenance.mutate', 'maintenance-archive.n_clicks'),
                           'maintenance.archived', 'success')
        body = self.assert_ok(self.call('maintenance.select', 'maintenance-mutation.data'))
        self.assertTrue(body['maintenance-save']['disabled'])
        self.assertTrue(body['maintenance-archive']['disabled'])
        self.assertFalse(body['maintenance-restore']['disabled'])
        self.assertIn('Archived', body['maintenance-record-meta']['children'])
        self.assert_ok(self.call('maintenance.list', 'maintenance-mutation.data'))
        self.assertEqual(self.values[('maintenance-table', 'data')], [])
        self.values[('maintenance-show-archived', 'value')] = ['archived']
        self.assert_ok(self.call('maintenance.list', 'maintenance-show-archived.value'))
        self.assertEqual(self.values[('maintenance-table', 'data')][0]['status'], 'Archived')
        self.assert_notice(self.call('maintenance.mutate', 'maintenance-restore.n_clicks'),
                           'maintenance.restored', 'success')
        body = self.assert_ok(self.call('maintenance.select', 'maintenance-mutation.data'))
        self.assertFalse(body['maintenance-save']['disabled'])
        self.assertTrue(body['maintenance-restore']['disabled'])
        self.assertEqual(self.service.get(USERS['user-a'], reference['id'])['version'], 3)

    def test_new_resets_editor_without_mutating_existing_record(self):
        self.create()
        body = self.assert_ok(self.call('maintenance.select', 'maintenance-new.n_clicks'))
        self.assertIsNone(body['maintenance-record']['data'])
        self.assertEqual(body['maintenance-name']['value'], '')
        self.assertTrue(body['maintenance-archive']['disabled'])
        self.assertTrue(body['maintenance-reload']['disabled'])
        self.assertEqual(self.service.list(USERS['user-a'])['total'], 1)

    def test_batched_selection_reset_does_not_override_new_or_saved_record(self):
        self.create()
        body = self.assert_ok(self.call('maintenance.select',
            ['maintenance-table.selected_rows', 'maintenance-mutation.data']))
        self.assertEqual(body['maintenance-name']['value'], 'Quarterly plan')
        body = self.assert_ok(self.call('maintenance.select',
            ['maintenance-table.selected_rows', 'maintenance-new.n_clicks']))
        self.assertIsNone(body['maintenance-record']['data'])
        self.assertEqual(body['maintenance-name']['value'], '')

    def test_pagination_search_and_cleared_selection(self):
        for index in range(PAGE_SIZE + 1):
            self.seed('Plan {:02d}'.format(index))
        self.seed('Foreign tenant marker', 'admin-b')
        self.assert_ok(self.call('maintenance.list', None))
        self.assertEqual(len(self.values[('maintenance-table', 'data')]), PAGE_SIZE)
        self.assertFalse(self.values[('maintenance-next', 'disabled')])
        self.assert_ok(self.call('maintenance.list', 'maintenance-next.n_clicks'))
        self.assertEqual(len(self.values[('maintenance-table', 'data')]), 1)
        self.assertEqual(self.values[('maintenance-page', 'data')], 1)
        self.assertTrue(self.values[('maintenance-next', 'disabled')])
        self.values[('maintenance-search', 'value')] = 'Plan 20'
        self.assert_ok(self.call('maintenance.list', 'maintenance-search.value'))
        self.assertEqual(self.values[('maintenance-page', 'data')], 0)
        self.assertEqual([row['name'] for row in self.values[('maintenance-table', 'data')]], ['Plan 20'])
        self.assertEqual(self.values[('maintenance-table', 'selected_rows')], [])
        self.assertIn('of 1', self.values[('maintenance-page-label', 'children')])

    def test_row_selection_reloads_server_record_not_browser_fields(self):
        record = self.seed('Authoritative name')
        self.values[('maintenance-table', 'data')] = [dict(record, name='Forged browser name', version=900)]
        self.values[('maintenance-table', 'selected_rows')] = [0]
        body = self.assert_ok(self.call('maintenance.select', 'maintenance-table.selected_rows'))
        self.assertEqual(body['maintenance-name']['value'], 'Authoritative name')
        self.assertEqual(body['maintenance-record']['data']['version'], 1)
        self.values[('maintenance-table', 'selected_rows')] = []
        response = self.call('maintenance.select', 'maintenance-table.selected_rows')
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.values[('maintenance-name', 'value')], 'Authoritative name')

    def test_user_can_read_org_but_cannot_write_another_owners_record(self):
        record = self.seed('Owned by colleague', 'other-a')
        self.assert_ok(self.call('maintenance.list', None))
        self.assertEqual(self.values[('maintenance-table', 'data')][0]['access'], 'Read only')
        self.values[('maintenance-table', 'selected_rows')] = [0]
        body = self.assert_ok(self.call('maintenance.select', 'maintenance-table.selected_rows'))
        self.assertTrue(body['maintenance-save']['disabled'])
        self.assertTrue(body['maintenance-name']['disabled'])
        self.assert_notice(self.call('maintenance.mutate', 'maintenance-save.n_clicks'),
                           'maintenance.denied', 'error')
        self.assert_notice(self.call('maintenance.mutate', 'maintenance-archive.n_clicks'),
                           'maintenance.denied', 'error')
        self.assertEqual(self.service.get(USERS['user-a'], record['id'])['version'], 1)

    def test_admin_can_edit_same_org_colleague_but_cannot_read_other_org(self):
        record = self.seed('Colleague definition')
        admin = self.authenticated(self.server, 'admin-a')
        self.values[('maintenance-table', 'data')] = [record]
        self.values[('maintenance-table', 'selected_rows')] = [0]
        body = self.assert_ok(self.call('maintenance.select', 'maintenance-table.selected_rows', client=admin))
        self.assertFalse(body['maintenance-save']['disabled'])
        self.values[('maintenance-name', 'value')] = 'Admin edit'
        self.assert_notice(self.call('maintenance.mutate', 'maintenance-save.n_clicks', client=admin),
                           'maintenance.updated', 'success')
        foreign = self.seed('Never disclose this tenant marker', 'admin-b')
        self.values[('maintenance-record', 'data')] = {'id': foreign['id'], 'version': 1}
        response = self.call('maintenance.select', 'maintenance-reload.n_clicks', client=admin)
        self.assert_notice(response, 'maintenance.denied', 'error')
        self.assertNotIn(foreign['name'], response.get_data(as_text=True))
        response = self.call('maintenance.mutate', 'maintenance-save.n_clicks', client=admin)
        self.assert_notice(response, 'maintenance.denied', 'error')
        self.assertEqual(self.service.get(USERS['admin-b'], foreign['id'])['version'], 1)

    def test_forged_record_extra_fields_and_invalid_form_never_write(self):
        record = self.seed('Original')
        for invalid in ({'id': record['id'], 'version': 1, 'org': 'B'},
                        {'id': record['id'], 'version': True}, {}, 'bad-reference'):
            with self.subTest(reference=invalid):
                self.values[('maintenance-record', 'data')] = invalid
                self.assert_notice(self.call('maintenance.mutate', 'maintenance-save.n_clicks'),
                                   'maintenance.invalid', 'warning')
        self.values[('maintenance-record', 'data')] = None
        for field, value in (('name', ''), ('description', 'x' * 1001),
                             ('cadence', 'run SQL now'), ('enabled', 'true')):
            with self.subTest(field=field):
                self.values.update({('maintenance-name', 'value'): 'Valid',
                                    ('maintenance-description', 'value'): '',
                                    ('maintenance-cadence', 'value'): 'manual',
                                    ('maintenance-enabled', 'value'): True})
                self.values[('maintenance-' + field, 'value')] = value
                self.assert_notice(self.call('maintenance.mutate', 'maintenance-save.n_clicks'),
                                   'maintenance.invalid', 'warning')
        self.assertEqual(self.service.list(USERS['user-a'])['total'], 1)
        self.assertEqual(self.service.get(USERS['user-a'], record['id'])['version'], 1)

    def test_unauthenticated_and_unlisted_role_are_denied_before_callback(self):
        for client, status in ((self.server.test_client(), 401),
                               (self.authenticated(self.server, 'guest-a'), 403)):
            for callback_id, changed in (('maintenance.list', 'maintenance-refresh.n_clicks'),
                                         ('maintenance.select', 'maintenance-reload.n_clicks'),
                                         ('maintenance.mutate', 'maintenance-save.n_clicks'),
                                         ('maintenance.feedback', 'maintenance-save.n_clicks')):
                with self.subTest(status=status, callback=callback_id):
                    response = self.call(callback_id, changed, client=client)
                    self.assertEqual(response.status_code, status)
        self.assertEqual(self.service.list(USERS['user-a'])['total'], 0)

    def test_unconfigured_storage_is_explicit_and_never_creates_memory_records(self):
        server = self.make_app(state=False)
        client = self.authenticated(server, 'user-a')
        self.assertIsNone(server.extensions['workspace'].definitions)
        body = self.assert_ok(self.route(client))
        self.assertIn('require configured local SQLite storage', json.dumps(body))
        self.assert_notice(self.call('maintenance.list', None, client=client),
                           'maintenance.unavailable', 'error')
        self.assert_notice(self.call('maintenance.mutate', 'maintenance-save.n_clicks', client=client),
                           'maintenance.unavailable', 'error')

    def test_unexpected_failures_use_fixed_catalog_without_exception_or_input_data(self):
        marker = 'private-backend-SQL-path-and-record-48372'
        self.values[('maintenance-name', 'value')] = 'private-form-payload-97223'
        with patch.object(self.service, 'create', side_effect=RuntimeError(marker)):
            response = self.call('maintenance.mutate', 'maintenance-save.n_clicks')
        body = self.assert_notice(response, 'maintenance.failed', 'error')
        self.assertNotIn(marker, response.get_data(as_text=True))
        self.assertNotIn('private-form-payload-97223', response.get_data(as_text=True))
        self.assertNotIn('maintenance-mutation', body)
        with patch.object(self.service, 'list', side_effect=RuntimeError(marker)):
            response = self.call('maintenance.list', 'maintenance-refresh.n_clicks')
        self.assert_notice(response, 'maintenance.failed', 'error')
        self.assertNotIn(marker, response.get_data(as_text=True))
        self.assertEqual(self.values[('maintenance-table', 'data')], [])


if __name__ == '__main__':
    unittest.main()
