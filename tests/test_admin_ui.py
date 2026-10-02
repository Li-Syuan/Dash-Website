"""Real pinned-Dash transport coverage for managed reports and admin forms."""
import json
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import patch

from reporting_workspace.application import create_app
from reporting_workspace.config import Settings
from reporting_workspace.ui_pages.managed_reports import SPEC, POLICY, report_from_search
from reporting_workspace.crud import ValidationError


USERS = {
    'admin-a': dict(id='admin-a', role='admin', org='A'),
    'user-a': dict(id='user-a', role='user', org='A'),
    'admin-b': dict(id='admin-b', role='admin', org='B'),
    'user-b': dict(id='user-b', role='user', org='B'),
    'guest-a': dict(id='guest-a', role='guest', org='A'),
}


class Identities:
    is_demo = True

    def get_user(self, identifier):
        return USERS.get(identifier)

    def authenticate(self, username, password):
        return USERS.get(username) if password == 'fixture-only' else None


class Reports:
    is_demo = True
    columns = ['period', 'department', 'revenue', 'cost', 'profit']

    def rows(self, user):
        return []

    def export(self, user):
        return ','.join(self.columns) + '\n'


def walk(node):
    if isinstance(node, list):
        for item in node:
            yield from walk(item)
    elif isinstance(node, dict):
        if 'props' in node:
            yield node
        for value in node.values():
            if isinstance(value, (dict, list)):
                yield from walk(value)


class AdminUITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.server = self.make_app(True)
        self.runtime = self.server.extensions['workspace']
        self.service = self.runtime.managed
        self.client = self.authenticated(self.server, 'admin-a')
        self.values = {}
        self.route('/admin')

    def make_app(self, state):
        server = create_app(Settings(secret_key='admin-ui-transport-secret-01234567890123456789',
            state_path=str(Path(self.directory.name) / 'state.sqlite3') if state else None), Identities(), Reports())
        server.config['TESTING'] = True
        return server

    @staticmethod
    def authenticated(server, identifier):
        client = server.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = identifier
            session['_fresh'] = True
        return client

    def absorb(self, response):
        if response.status_code != 200:
            return
        body = response.get_json()['response']
        for component_id, props in body.items():
            if not component_id.startswith('{'):
                for prop, value in props.items():
                    self.values[(component_id, prop)] = value
        for node in walk(body):
            props = node['props']
            if isinstance(props.get('id'), str):
                for key, value in props.items():
                    self.values[(props['id'], key)] = value
                if node['type'] == 'Store' and 'data' not in props:
                    self.values[(props['id'], 'data')] = None

    def route(self, pathname, client=None):
        response = (client or self.client).post('/_dash-update-component', json={
            'output': '.._pages_content.children..._pages_store.data..',
            'outputs': [{'id': '_pages_content', 'property': 'children'}, {'id': '_pages_store', 'property': 'data'}],
            'inputs': [{'id': '_pages_location', 'property': 'pathname', 'value': pathname},
                       {'id': '_pages_location', 'property': 'search', 'value': ''}],
            'state': [], 'changedPropIds': ['_pages_location.pathname'],
        })
        self.absorb(response)
        return response

    def call(self, callback_id, changed, client=None, request_query='', auto_click=True):
        client = client or self.client
        if auto_click:
            for target in changed if isinstance(changed, list) else [changed] if changed else []:
                if target.endswith('.n_clicks'):
                    self.values[(target[:-9], 'n_clicks')] = 1
        registry = client.application.extensions['callback_registry']
        spec = next(item for item in registry.callbacks.values() if item.callback_id == callback_id)
        entry = client.application.extensions['dash_app'].callback_map[spec.output_key]
        output = entry['output']
        outputs = output if isinstance(output, (list, tuple)) else (output,)
        wire = [{'id': item.component_id, 'property': item.component_property} for item in outputs]
        response = client.post('/_dash-update-component' + request_query, json={
            'output': spec.output_key, 'outputs': wire if isinstance(output, (list, tuple)) else wire[0],
            'inputs': [dict(item, value=self.values.get((item['id'], item['property']))) for item in entry['inputs']],
            'state': [dict(item, value=self.values.get((item['id'], item['property']))) for item in entry['state']],
            'changedPropIds': changed if isinstance(changed, list) else [changed] if changed else [],
        })
        self.absorb(response)
        return response

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()['response']

    def event(self, response, code):
        body = self.ok(response)
        events = [value['data'] for key, value in body.items() if key.startswith('{') and
                  json.loads(key).get('type') == 'workspace-notify']
        self.assertEqual(len(events), 1, body)
        self.assertEqual(events[0]['code'], code)
        self.assertEqual(events[0]['request_id'], response.headers['X-Request-ID'])
        self.assertEqual(set(events[0]), {'event_id', 'kind', 'code', 'audience', 'request_id'})
        return body

    def seed(self, name='Operations report', user='admin-a'):
        return self.service.create_report(USERS[user], dict(name=name, category='Operations',
            description='Synthetic reporting fixture', source_key='synthetic-monthly', enabled=True),
            request_key=secrets.token_hex(16))

    def select(self, record):
        self.values[('admin-report-picker', 'value')] = record['id']
        self.ok(self.call('admin.select', 'admin-report-picker.value'))
        self.ok(self.call('admin.schedule', 'admin-record.data'))

    def update_after_mutation(self):
        self.ok(self.call('admin.select', 'admin-change.data'))
        self.ok(self.call('admin.schedule', 'admin-record.data'))

    def create_form(self):
        self.values.update({('admin-report-name', 'value'): 'Created in browser',
                            ('admin-report-source', 'value'): 'synthetic-monthly'})
        return self.call('admin.mutate', 'admin-report-save.n_clicks')

    def schedule_form(self):
        self.values.update({('admin-schedule-recipients', 'value'): 'demo@example.invalid',
                            ('admin-schedule-enabled', 'value'): True})
        return self.call('admin.mutate', 'admin-schedule-save.n_clicks')

    def grant(self, record, actions):
        current = self.service.get_report(USERS['admin-a'], record['id'], for_maintenance=True)
        return self.service.set_grant(USERS['admin-a'], record['id'], current['version'], 'user', 'user-a', actions)

    def managed(self, record, client):
        self.route('/reports', client)
        self.values[('managed-url', 'search')] = '?report=' + record['id']
        return self.call('managed.load', 'managed-url.search', client)

    def test_explicit_page_and_callback_policies_legacy_lab_preserved(self):
        self.assertEqual((SPEC.path, SPEC.page_id, POLICY.roles), ('/reports', 'managed-reports', ('admin', 'user')))
        callbacks = self.server.extensions['callback_registry'].callbacks.values()
        for item in callbacks:
            if item.callback_id in ('admin.list', 'admin.select', 'admin.schedule', 'admin.mutate'):
                self.assertEqual(item.page_id, 'administration')
                self.assertEqual(item.policy.roles, ('admin',))
            if item.callback_id.startswith('managed.'):
                self.assertEqual(item.page_id, 'managed-reports')
                self.assertEqual(item.policy, POLICY)
        self.assertIn(('adapter-run', 'id'), self.values)
        self.assertRegex(self.values[('admin-draft', 'data')], r'^[0-9a-f]{32}$')
        self.assertIn('ENGINE STOPPED', json.dumps(self.ok(self.route('/admin'))))
        self.assertEqual(self.values[('admin-report-category', 'maxLength')], 64)
        self.assertEqual(self.values[('admin-report-description', 'maxLength')], 240)

    def test_admin_transports_deny_nonadmins_and_anonymous_before_service(self):
        with patch.object(self.service, 'list_reports', side_effect=AssertionError('must not enter')):
            for client, status in ((self.server.test_client(), 401), (self.authenticated(self.server, 'user-a'), 403),
                                   (self.authenticated(self.server, 'user-b'), 403), (self.authenticated(self.server, 'guest-a'), 403)):
                for action in ('list', 'select', 'schedule', 'mutate'):
                    with self.subTest(status=status, action=action):
                        self.assertEqual(self.call('admin.' + action, 'admin-report-save.n_clicks', client).status_code, status)

    def test_create_repeated_click_is_idempotent_and_selection_is_reference_only(self):
        self.event(self.create_form(), 'maintenance.created')
        self.event(self.create_form(), 'maintenance.created')
        self.assertEqual(len(self.service.list_reports(USERS['admin-a'], admin=True)), 1)
        self.update_after_mutation()
        reference = self.values[('admin-record', 'data')]
        self.assertEqual(set(reference), {'id', 'version'})
        self.assertEqual(self.values[('admin-report-name', 'value')], 'Created in browser')
        self.assertTrue(self.values[('admin-report-source', 'disabled')])
        self.assertFalse(self.values[('admin-report-archive', 'disabled')])

    def test_admin_selection_always_reloads_server_fields(self):
        record = self.seed('Authoritative title')
        self.values[('admin-record', 'data')] = dict(record, name='FORGED', version=999)
        self.select(record)
        self.assertEqual(self.values[('admin-report-name', 'value')], 'Authoritative title')
        self.assertEqual(self.values[('admin-record', 'data')], {'id': record['id'], 'version': 1})

    def test_failed_admin_selection_clears_old_save_target_and_fields(self):
        original = self.seed('Original report')
        self.select(original)
        foreign = self.seed('Forbidden other report', 'admin-b')
        self.values[('admin-report-picker', 'value')] = foreign['id']
        response = self.call('admin.select', 'admin-report-picker.value')
        self.event(response, 'maintenance.denied')
        self.assertIsNone(self.values[('admin-record', 'data')])
        self.assertEqual(self.values[('admin-report-picker', 'value')], '')
        self.assertEqual(self.values[('admin-report-name', 'value')], '')
        self.assertTrue(self.values[('admin-report-save', 'disabled')])
        self.assertNotIn('Forbidden other report', response.get_data(as_text=True))
        self.assertEqual(self.service.get_report(USERS['admin-a'], original['id'])['version'], 1)
        self.ok(self.call('admin.select', 'admin-report-new.n_clicks'))
        self.assertFalse(self.values[('admin-report-save', 'disabled')])

    def test_update_uses_version_source_is_not_editable(self):
        record = self.seed()
        self.select(record)
        self.values[('admin-report-name', 'value')] = 'Edited title'
        self.values[('admin-report-source', 'value')] = 'arbitrary-import-path'
        self.event(self.call('admin.mutate', 'admin-report-save.n_clicks'), 'maintenance.updated')
        saved = self.service.get_report(USERS['admin-a'], record['id'])
        self.assertEqual((saved['name'], saved['source_key'], saved['version']), ('Edited title', 'synthetic-monthly', 2))

    def test_conflict_preserves_unsaved_form_and_reference(self):
        record = self.seed()
        self.select(record)
        self.values[('admin-report-name', 'value')] = 'Unsaved draft'
        self.service.update_report(USERS['admin-a'], record['id'], 1, {'name': 'Concurrent edit'})
        body = self.event(self.call('admin.mutate', 'admin-report-save.n_clicks'), 'maintenance.conflict')
        self.assertNotIn('admin-change', body)
        self.assertEqual(self.values[('admin-report-name', 'value')], 'Unsaved draft')
        self.assertEqual(self.values[('admin-record', 'data')]['version'], 1)
        self.ok(self.call('admin.select', 'admin-report-reload.n_clicks'))
        self.assertEqual(self.values[('admin-report-name', 'value')], 'Concurrent edit')

    def test_archive_restore_is_recoverable_with_disabled_edit_controls(self):
        record = self.seed()
        self.select(record)
        self.event(self.call('admin.mutate', 'admin-report-archive.n_clicks'), 'maintenance.archived')
        self.update_after_mutation()
        self.assertTrue(self.values[('admin-report-save', 'disabled')])
        self.assertTrue(self.values[('admin-grant-save', 'disabled')])
        self.assertTrue(self.values[('admin-schedule-save', 'disabled')])
        self.assertFalse(self.values[('admin-report-restore', 'disabled')])
        self.event(self.call('admin.mutate', 'admin-report-restore.n_clicks'), 'maintenance.restored')
        self.update_after_mutation()
        self.assertFalse(self.values[('admin-report-save', 'disabled')])
        self.assertEqual(self.values[('admin-record', 'data')]['version'], 3)

    def test_new_report_resets_reference_and_creation_key(self):
        record = self.seed()
        self.select(record)
        key = self.values[('admin-draft', 'data')]
        self.ok(self.call('admin.select', 'admin-report-new.n_clicks'))
        self.assertIsNone(self.values[('admin-record', 'data')])
        self.assertNotEqual(self.values[('admin-draft', 'data')], key)
        self.assertEqual(self.values[('admin-report-name', 'value')], '')

    def test_forged_reference_unknown_fields_and_foreign_report_never_mutate(self):
        record = self.seed()
        self.select(record)
        for reference in ({'id': record['id'], 'version': True}, {'id': record['id'], 'version': 1, 'role': 'admin'}, {}, 'bad'):
            self.values[('admin-record', 'data')] = reference
            self.event(self.call('admin.mutate', 'admin-report-save.n_clicks'), 'maintenance.invalid')
        foreign = self.seed('Private foreign title', 'admin-b')
        self.values[('admin-record', 'data')] = {'id': foreign['id'], 'version': 1}
        response = self.call('admin.mutate', 'admin-report-archive.n_clicks')
        self.event(response, 'maintenance.denied')
        self.assertNotIn('Private foreign title', response.get_data(as_text=True))

    def test_grants_reload_authoritative_version_and_explain_additive_access(self):
        record = self.seed()
        self.select(record)
        self.values.update({('admin-grant-kind', 'value'): 'user', ('admin-grant-subject', 'value'): 'user-a',
                            ('admin-grant-actions', 'value'): ['view', 'maintain']})
        self.event(self.call('admin.mutate', 'admin-grant-save.n_clicks'), 'maintenance.updated')
        body = self.ok(self.call('admin.select', 'admin-change.data'))
        self.assertIn('Rules add together', json.dumps(body))
        self.assertEqual(self.values[('admin-record', 'data')]['version'], 2)
        self.assertEqual(self.values[('admin-grants-table', 'data')][0]['subject'], 'user-a')

    def test_schedule_creation_repeated_click_and_weekly_zone_fields(self):
        record = self.seed()
        self.select(record)
        self.values.update({('admin-schedule-cadence', 'value'): 'weekly', ('admin-schedule-timezone', 'value'): 'Asia/Taipei',
                            ('admin-schedule-weekday', 'value'): '4', ('admin-schedule-time', 'value'): '17:35'})
        self.event(self.schedule_form(), 'maintenance.updated')
        self.event(self.schedule_form(), 'maintenance.updated')
        schedules = self.service.list_schedules(USERS['admin-a'], record['id'])
        self.assertEqual(len(schedules), 1)
        self.assertEqual((schedules[0]['weekday'], schedules[0]['time'], schedules[0]['timezone']), (4, '17:35', 'Asia/Taipei'))
        self.update_after_mutation()
        self.assertEqual(set(self.values[('admin-schedule-record', 'data')]), {'id', 'version'})
        self.assertFalse(self.values[('admin-schedule-simulate', 'disabled')])

    def test_schedule_selector_reloads_server_record_and_rejects_foreign_report_schedule(self):
        first, second = self.seed('First'), self.seed('Second')
        schedule = self.service.create_schedule(USERS['admin-a'], first['id'], first['version'],
                    {'recipients': ['one@example.invalid']}, request_key=secrets.token_hex(16))
        self.select(second)
        self.values[('admin-schedule-picker', 'value')] = schedule['id']
        self.event(self.call('admin.schedule', 'admin-schedule-picker.value'), 'maintenance.invalid')
        self.assertIsNone(self.values[('admin-schedule-record', 'data')])
        self.values[('admin-schedule-record', 'data')] = {'id': schedule['id'], 'version': 1}
        self.event(self.schedule_form(), 'maintenance.invalid')
        self.assertEqual(self.service.get_schedule(USERS['admin-a'], schedule['id'])['version'], 1)

    def test_schedule_simulation_repeated_click_once_and_logs_visible(self):
        self.select(self.seed())
        self.event(self.schedule_form(), 'maintenance.updated')
        self.update_after_mutation()
        self.event(self.call('admin.mutate', 'admin-schedule-simulate.n_clicks'), 'simulation.done')
        self.event(self.call('admin.mutate', 'admin-schedule-simulate.n_clicks'), 'simulation.skipped')
        self.assertEqual(len(self.service.mail.messages), 1)
        self.update_after_mutation()
        runs = self.values[('admin-runs-table', 'data')]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]['status'], 'succeeded')
        self.assertIn('UTC', runs[0]['started'])
        self.assertTrue(self.values[('admin-audit-table', 'data')])

    def test_disabled_schedule_does_not_offer_simulate(self):
        self.select(self.seed())
        self.values[('admin-schedule-recipients', 'value')] = 'demo@example.invalid'
        self.event(self.call('admin.mutate', 'admin-schedule-save.n_clicks'), 'maintenance.updated')
        self.update_after_mutation()
        self.assertTrue(self.values[('admin-schedule-simulate', 'disabled')])
        self.event(self.call('admin.mutate', 'admin-schedule-simulate.n_clicks'), 'maintenance.denied')

    def test_schedule_edit_conflict_preserves_fields_and_reference(self):
        self.select(self.seed())
        self.schedule_form()
        self.update_after_mutation()
        ref = self.values[('admin-schedule-record', 'data')]
        self.service.update_schedule(USERS['admin-a'], ref['id'], ref['version'], {'time': '12:00'})
        self.values[('admin-schedule-time', 'value')] = '13:00'
        self.event(self.call('admin.mutate', 'admin-schedule-save.n_clicks'), 'maintenance.conflict')
        self.assertEqual(self.values[('admin-schedule-time', 'value')], '13:00')
        self.assertEqual(self.values[('admin-schedule-record', 'data')], ref)

    def test_no_state_controls_disabled_notice_and_writes_fail_closed(self):
        server = self.make_app(False)
        client = self.authenticated(server, 'admin-a')
        response = self.route('/admin', client)
        self.ok(response)
        self.assertIn('require configured local SQLite storage', response.get_data(as_text=True))
        for component in ('admin-report-save', 'admin-report-name', 'admin-report-picker', 'admin-schedule-save', 'admin-grant-save'):
            self.assertTrue(self.values[(component, 'disabled')])
        self.event(self.call('admin.mutate', 'admin-report-save.n_clicks', client), 'maintenance.unavailable')
        self.route('/reports', client)
        self.event(self.call('managed.load', 'managed-url.search', client), 'maintenance.unavailable')
        self.assertTrue(self.values[('managed-export', 'disabled')])

    def test_callback_failures_never_echo_exception_or_form_payload(self):
        marker = 'private-sql-path-token-348975'
        self.values[('admin-report-name', 'value')] = 'private-form-name-93875'
        with patch.object(self.service, 'create_report', side_effect=RuntimeError(marker)):
            response = self.call('admin.mutate', 'admin-report-save.n_clicks')
        self.event(response, 'maintenance.failed')
        self.assertNotIn(marker, response.get_data(as_text=True))
        self.assertNotIn('private-form-name-93875', response.get_data(as_text=True))
        record = self.seed()
        self.values[('admin-report-picker', 'value')] = record['id']
        with patch.object(self.service, 'list_audit', side_effect=RuntimeError(marker)):
            response = self.call('admin.select', 'admin-report-picker.value')
        self.event(response, 'maintenance.failed')
        self.assertNotIn(marker, response.get_data(as_text=True))

    def test_managed_location_input_controls_selection_and_ignores_flask_query(self):
        first, second = self.seed('First route'), self.seed('Second route')
        self.managed(first, self.client)
        self.assertEqual(self.values[('managed-record', 'data')]['id'], first['id'])
        self.values[('managed-url', 'search')] = '?report=' + second['id']
        self.ok(self.call('managed.load', 'managed-url.search', request_query='?report=' + first['id']))
        self.assertEqual(self.values[('managed-record', 'data')]['id'], second['id'])
        registry = self.server.extensions['callback_registry']
        spec = next(s for s in registry.callbacks.values() if s.callback_id == 'managed.load')
        inputs = self.server.extensions['dash_app'].callback_map[spec.output_key]['inputs']
        self.assertIn({'id': 'managed-url', 'property': 'search'}, inputs)

    def test_managed_no_grant_denies_metadata_data_and_cross_org(self):
        record = self.seed('Restricted private title')
        client = self.authenticated(self.server, 'user-a')
        response = self.managed(record, client)
        self.event(response, 'maintenance.denied')
        self.assertNotIn('Restricted private title', response.get_data(as_text=True))
        self.assertEqual(self.values[('managed-table', 'data')], [])
        self.assertIsNone(self.values[('managed-record', 'data')])
        foreign = self.seed('Foreign private title', 'admin-b')
        self.values[('managed-url', 'search')] = '?report=' + foreign['id']
        response = self.call('managed.load', 'managed-url.search', self.client)
        self.event(response, 'maintenance.denied')
        self.assertNotIn('Foreign private title', response.get_data(as_text=True))

    def test_view_only_user_cannot_forge_export_or_maintain(self):
        record = self.seed()
        self.grant(record, ['view'])
        client = self.authenticated(self.server, 'user-a')
        self.ok(self.managed(record, client))
        self.assertEqual(len(self.values[('managed-table', 'data')]), 12)
        self.assertTrue(self.values[('managed-export', 'disabled')])
        self.assertTrue(self.values[('managed-save', 'disabled')])
        self.event(self.call('managed.save', 'managed-save.n_clicks', client), 'maintenance.denied')
        body = self.event(self.call('managed.export', 'managed-export.n_clicks', client), 'report.export.failed')
        self.assertNotIn('managed-download', body)

    def test_explicit_maintainer_edits_only_metadata_with_current_identity(self):
        record = self.seed()
        self.grant(record, ['view', 'maintain'])
        client = self.authenticated(self.server, 'user-a')
        self.ok(self.managed(record, client))
        self.assertFalse(self.values[('managed-save', 'disabled')])
        self.values[('managed-name', 'value')] = 'Maintainer edit'
        with patch.object(self.service, 'update_report', wraps=self.service.update_report) as update:
            self.event(self.call('managed.save', 'managed-save.n_clicks', client), 'maintenance.updated')
        self.assertEqual(update.call_args[0][0], USERS['user-a'])
        self.assertEqual(set(update.call_args[0][3]), {'name', 'category', 'description'})
        self.assertEqual(self.call('admin.mutate', 'admin-grant-save.n_clicks', client).status_code, 403)
        self.assertEqual(self.call('admin.mutate', 'admin-schedule-save.n_clicks', client).status_code, 403)

    def test_disabled_report_retains_explicit_metadata_maintenance(self):
        record = self.seed()
        record = self.grant(record, ['view', 'export', 'maintain'])
        self.service.update_report(USERS['admin-a'], record['id'], record['version'], {'enabled': False})
        client = self.authenticated(self.server, 'user-a')
        self.ok(self.managed(record, client))
        self.assertEqual(self.values[('managed-table', 'data')], [])
        self.assertTrue(self.values[('managed-export', 'disabled')])
        self.assertFalse(self.values[('managed-save', 'disabled')])
        self.values[('managed-description', 'value')] = 'Updated while disabled'
        self.event(self.call('managed.save', 'managed-save.n_clicks', client), 'maintenance.updated')

    def test_authorized_export_ignores_browser_rows_and_uses_server_data(self):
        record = self.seed()
        self.grant(record, ['view', 'export'])
        client = self.authenticated(self.server, 'user-a')
        self.ok(self.managed(record, client))
        self.values[('managed-table', 'data')] = [{'department': 'FORGED ROW'}]
        response = self.call('managed.export', 'managed-export.n_clicks', client)
        body = self.event(response, 'report.export.ready')
        download = body['managed-download']['data']
        self.assertEqual(download['filename'], 'managed-report.csv')
        self.assertIn('Demo Sales', download['content'])
        self.assertNotIn('FORGED ROW', download['content'])

    def test_managed_conflict_keeps_form_and_forged_reference_is_rejected(self):
        record = self.seed()
        self.managed(record, self.client)
        self.values[('managed-name', 'value')] = 'Pending metadata'
        self.service.update_report(USERS['admin-a'], record['id'], 1, {'name': 'Concurrent metadata'})
        self.event(self.call('managed.save', 'managed-save.n_clicks'), 'maintenance.conflict')
        self.assertEqual(self.values[('managed-name', 'value')], 'Pending metadata')
        self.values[('managed-record', 'data')]['role'] = 'admin'
        self.event(self.call('managed.save', 'managed-save.n_clicks'), 'maintenance.invalid')

    def test_managed_callbacks_deny_anonymous_and_unlisted_role(self):
        for client, status in ((self.server.test_client(), 401), (self.authenticated(self.server, 'guest-a'), 403)):
            for name in ('load', 'save', 'export'):
                self.assertEqual(self.call('managed.' + name, 'managed-save.n_clicks', client).status_code, status)

    def test_search_filters_only_authorized_reports(self):
        first, second = self.seed('Revenue'), self.seed('Quality')
        self.grant(first, ['view'])
        client = self.authenticated(self.server, 'user-a')
        self.route('/reports', client)
        self.values[('managed-search', 'value')] = 'revenue'
        self.values[('managed-url', 'search')] = ''
        body = self.ok(self.call('managed.load', 'managed-search.value', client))
        text = json.dumps(body['managed-catalog'])
        self.assertIn('Revenue', text)
        self.assertNotIn('Quality', text)

    def test_query_parser_rejects_ambiguous_and_oversized_selection(self):
        self.assertIsNone(report_from_search(''))
        self.assertEqual(report_from_search('?report=abc'), 'abc')
        for search in ('report=abc', '?report=a&report=b', '?report=', '?report=a&other=b', '?x', '?' + 'r' * 256, 8):
            with self.subTest(search=search), self.assertRaises(ValidationError):
                report_from_search(search)

    def test_remounted_mutation_buttons_do_not_save_or_export_without_positive_click(self):
        record = self.seed()
        self.select(record)
        self.schedule_form()
        self.update_after_mutation()
        self.managed(record, self.client)
        before = self.service.get_report(USERS['admin-a'], record['id'], for_maintenance=True)
        for value in (None, 0, False, -1, 1.5, '1', True):
            for callback, button in (
                ('admin.mutate', 'admin-report-save'), ('admin.mutate', 'admin-report-archive'),
                ('admin.mutate', 'admin-report-restore'), ('admin.mutate', 'admin-grant-save'),
                ('admin.mutate', 'admin-schedule-save'), ('admin.mutate', 'admin-schedule-simulate'),
                ('managed.save', 'managed-save'), ('managed.export', 'managed-export'),
            ):
                with self.subTest(value=value, button=button):
                    self.values[(button, 'n_clicks')] = value
                    with patch.object(self.service, 'update_report', side_effect=AssertionError('must not write')), \
                         patch.object(self.service, 'export', side_effect=AssertionError('must not export')):
                        response = self.call(callback, button + '.n_clicks', auto_click=False)
                    self.assertEqual(response.status_code, 204, response.get_data(as_text=True))
        self.assertEqual(self.service.get_report(USERS['admin-a'], record['id'], for_maintenance=True), before)
        self.assertEqual(len(self.service.list_schedules(USERS['admin-a'], record['id'])), 1)
        self.assertEqual(self.service.list_runs(USERS['admin-a'], record['id']), [])

    def test_remounted_new_schedule_button_does_not_clear_selection(self):
        self.select(self.seed())
        self.schedule_form()
        self.update_after_mutation()
        reference = self.values[('admin-schedule-record', 'data')]
        self.assertIsNotNone(reference)
        for value in (None, 0, False, -1, True):
            self.values[('admin-schedule-new', 'n_clicks')] = value
            response = self.call('admin.schedule', 'admin-schedule-new.n_clicks', auto_click=False)
            self.assertEqual(response.status_code, 204)
            self.assertEqual(self.values[('admin-schedule-record', 'data')], reference)
        self.ok(self.call('admin.schedule', 'admin-schedule-new.n_clicks'))
        self.assertIsNone(self.values[('admin-schedule-record', 'data')])

    def test_all_rendered_ids_unique_local_css_and_no_external_urls(self):
        for path in ('/admin', '/reports'):
            response = self.route(path)
            nodes = list(walk(self.ok(response)['_pages_content']['children']))
            identifiers = [json.dumps(node['props']['id'], sort_keys=True) for node in nodes if 'id' in node['props']]
            self.assertEqual(len(identifiers), len(set(identifiers)), path)
        css = (Path(__file__).resolve().parents[1] / 'assets' / 'admin.css').read_text()
        self.assertIn('var(--surface)', css)
        self.assertIn('@media (max-width: 700px)', css)
        self.assertNotIn('url(', css)
        self.assertEqual(css.count('{'), css.count('}'))


if __name__ == '__main__':
    unittest.main()
