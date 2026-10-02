"""App-scoped page, policy, chart and notification transport integration.

These tests use the real Flask/Dash dispatcher with synthetic providers. A
serialized Graph is evidence of correct server output, not browser rendering.
"""
import json
import math
import unittest
from unittest.mock import patch

import dash
from dash import Input, Output, html
from dash import _callback

from reporting_workspace.application import create_app
from reporting_workspace.notifications import notification_store_id
from reporting_workspace.registry import AccessPolicy, PageSpec
from reporting_workspace.ui_pages import default_pages

try:
    from .test_application import (
        AppTestCase, FixtureIdentity, FixtureReports, PROVIDER_DETAIL,
        callback_output_spec, components, notification_events,
    )
except ImportError:
    from test_application import (
        AppTestCase, FixtureIdentity, FixtureReports, PROVIDER_DETAIL,
        callback_output_spec, components, notification_events,
    )


PRIVATE_RESULT = 'synthetic-extra-page-private-result'
EXTRA_POLICY = AccessPolicy.require(roles=('admin',), org='A')


def extra_page(calls=None, path='/extra-report', page_id='extra-report',
               output_id='extra-result', marker=PRIVATE_RESULT):
    """A complete trusted page plugin requiring no changes to the shell."""
    calls = calls if calls is not None else []

    def layout(runtime):
        calls.append(('layout', runtime.identity()['id']))
        return [html.H1('Extra report'), html.Button('Load', id='extra-run'),
                html.Div(marker, id=output_id)]

    def register(callbacks, runtime):
        @callbacks.callback(Output(output_id, 'children'), Input('extra-run', 'n_clicks'),
                            callback_id=page_id + '.load', page_id=page_id,
                            policy=EXTRA_POLICY, prevent_initial_call=True)
        def load(clicks):
            calls.append(('callback', runtime.identity()['id']))
            return marker

    return PageSpec(page_id, path, 'Extra report', layout, EXTRA_POLICY,
                    nav_label='Extra report', nav_order=35, register_callbacks=register)


class PageFactoryIntegrationTests(AppTestCase):
    def extra_app(self, spec, identities=None):
        server = create_app(self.settings(), identities or FixtureIdentity(),
                            FixtureReports(), extra_pages=(spec,))
        server.config['TESTING'] = True
        return server

    @staticmethod
    def links(client):
        response = client.get('/_dash-layout')
        if response.status_code != 200:
            raise AssertionError(response.get_data(as_text=True))
        return [item['props']['href'] for item in components(response.get_json())
                if item.get('type') == 'NavLink']

    def test_default_catalog_preserves_exact_paths_titles_and_policies(self):
        expected = {
            'overview': ('/', 'Report catalog', True, None, None),
            'login': ('/login', 'Welcome back', False, None, None),
            'logout': ('/logout', 'Sign out', False, None, None),
            'page1': ('/page1', 'Page 1', True, ('admin',), None),
            'page2': ('/page2', 'Page 2', True, ('admin', 'user'), 'A'),
            'reports': ('/page3', 'Monthly performance', True, ('admin',), None),
            'administration': ('/admin', 'Adapter laboratory', True, ('admin',), None),
            'maintenance': ('/maintenance', 'Report definitions', True, ('admin', 'user'), None),
        }
        actual = {spec.page_id: (spec.path, spec.title, spec.policy.authenticated,
                                 spec.policy.roles, spec.policy.org)
                  for spec in default_pages()}
        self.assertEqual(actual, expected)
        self.assertEqual(len(default_pages()), len(expected))

    def test_main_header_uses_only_authorized_section_links(self):
        identities = FixtureIdentity()
        identities.users['other-role'] = {'id': 'other-role', 'role': 'other', 'org': 'A'}
        server = self.app(identities=identities)
        for username, expected in ((None, []), ('fixture-admin', ['/', '/admin', '/maintenance']),
                                   ('fixture-user', ['/', '/maintenance']), ('other-role', ['/'])):
            with self.subTest(username=username):
                client = server.test_client() if username is None else self.logged_in(server, username)
                self.assertEqual(self.links(client), expected)
                layout = client.get('/_dash-layout').get_json()
                navigation = [node for node in components(layout)
                              if node.get('type') == 'Nav' and node['props'].get('className') == 'top-navigation']
                self.assertEqual(len(navigation), 1)
                labels = [node['props']['children'] for node in components(navigation)
                          if node.get('type') == 'NavLink']
                self.assertEqual(labels, [{'/' : 'Reports', '/admin': 'Admin',
                                            '/maintenance': 'Maintenance'}[path] for path in expected])
                self.assertTrue(set(self.links(client)).isdisjoint(('/page1', '/page2', '/page3')))
        catalog_pages = {page.page_id: page for page in default_pages()}
        for page_id in ('page1', 'page2', 'reports'):
            self.assertIsNone(catalog_pages[page_id].nav_label)
            self.assertTrue(catalog_pages[page_id].catalog_category)

    def test_dependency_wire_outputs_parse_like_dash_291_renderer(self):
        server = self.app()
        client = server.test_client()
        response = client.get('/_dash-dependencies')
        self.assertEqual(response.status_code, 200)
        callback_map = server.extensions['dash_app'].callback_map
        declarations = server.extensions['callback_registry'].callbacks.values()
        action_keys = {spec.output_key: spec.callback_id for spec in declarations
                       if spec.callback_id in {
                           'reports.refresh', 'reports.export', 'admin.simulate',
                           'maintenance.list', 'maintenance.select', 'maintenance.mutate'}}
        covered = set()
        for dependency in response.get_json():
            key = dependency['output']
            # Dash 2.9.1 dash_renderer: parseMultipleOutputs -> splitIdAndProp
            # -> parseWildcardId. Do NOT use Dash's Python split_callback_id,
            # or unescape backslashes: the renderer passes this straight to JSON.parse.
            wire_outputs = key[2:-2].split('...') if key.startswith('..') else [key]
            parsed = []
            for wire_output in wire_outputs:
                component_id, prop = wire_output.rsplit('.', 1)
                if component_id.startswith('{'):
                    component_id = json.loads(component_id)
                parsed.append({'id': component_id, 'property': prop})
            outputs = callback_map[key]['output']
            outputs = outputs if isinstance(outputs, (list, tuple)) else [outputs]
            self.assertEqual(parsed, [{'id': output.component_id,
                                       'property': output.component_property} for output in outputs])
            if key in action_keys:
                action = action_keys[key]
                self.assertIn({'id': notification_store_id(action), 'property': 'data'}, parsed)
                covered.add(action)
        self.assertEqual(covered, set(action_keys.values()))
        self.assertEqual(len(covered), 6)

    def test_stable_control_ids_and_properties_survive_grouped_notifications(self):
        server = self.app()
        client = server.test_client()
        app = server.extensions['dash_app']
        declarations = {spec.callback_id: spec for spec in
                        server.extensions['callback_registry'].callbacks.values()}
        expected = {
            'login.submit': ([('redirectHome', 'pathname'), ('login-alert', 'is_open')],
                             [('username-box', 'value'), ('password-box', 'value'), ('login-box', 'n_clicks')]),
            'shell.route': ([('_pages_content', 'children'), ('_pages_store', 'data')],
                            [('_pages_location', 'pathname'), ('_pages_location', 'search')]),
            'shell.sidebar': ([('sidebar', 'style'), ('page-content', 'style'), ('side_click', 'data')],
                              [('btn_sidebar', 'n_clicks'), ('assistant-close', 'n_clicks')]),
            'shell.info': ([('popover', 'is_open')], [('popover-target', 'n_clicks')]),
            'shell.sidebar_accessibility': ([('btn_sidebar', 'aria-expanded'), ('sidebar', 'aria-hidden'),
                                             ('btn_sidebar', 'aria-label')], [('side_click', 'data')]),
        }
        for callback_id, (outputs, inputs) in expected.items():
            entry = app.callback_map[declarations[callback_id].output_key]
            dependencies = entry['output'] if isinstance(entry['output'], list) else [entry['output']]
            self.assertEqual([(item.component_id, item.component_property) for item in dependencies], outputs)
            self.assertEqual([(item['id'], item['property']) for item in entry['inputs']], inputs)
        for callback_id, component, prop in (('reports.refresh', 'table', 'data'),
                                              ('reports.export', 'report-download', 'data'),
                                              ('admin.simulate', 'adapter-result', 'children')):
            _, outputs = callback_output_spec(client, component, prop)
            self.assertEqual(outputs, [{'id': component, 'property': prop},
                                       {'id': notification_store_id(callback_id), 'property': 'data'}])
            self.assertEqual(declarations[callback_id].policy, AccessPolicy.require(roles=('admin',)))
        renderer = app.callback_map[declarations['shell.notifications'].output_key]
        self.assertEqual(declarations['shell.notifications'].policy, AccessPolicy.require())
        self.assertEqual(json.loads(renderer['inputs'][0]['id']),
                         {'type': 'workspace-notify', 'action': ['ALL']})
        shell = client.get('/_dash-layout')
        self.assertEqual(shell.status_code, 200)
        shell_nodes = {node['props']['id']: node for node in components(shell.get_json())
                       if isinstance(node['props'].get('id'), str)}
        for component_id in ('sidebar', 'page-content', 'btn_sidebar', 'side_click', 'popover',
                             'popover-target', '_pages_location', '_pages_content', '_pages_store',
                             'workspace-notifications', 'workspace-notification-seen',
                             'assistant-close', 'assistant-message'):
            self.assertIn(component_id, shell_nodes)
        self.assertEqual(shell_nodes['side_click']['props']['data'], 'HIDDEN')
        self.assertEqual(shell_nodes['sidebar']['props']['style'], {'display': 'none'})
        self.assertEqual(shell_nodes['sidebar']['props']['aria-hidden'], 'true')
        self.assertEqual(shell_nodes['btn_sidebar']['props']['aria-expanded'], 'false')
        self.assertEqual(shell_nodes['btn_sidebar']['props']['aria-label'], 'Open assistant panel')
        self.assertTrue(shell_nodes['assistant-message']['props']['disabled'])
        seen = shell_nodes['workspace-notification-seen']
        self.assertEqual(seen['type'], 'Store')
        self.assertEqual(seen['props'].get('storage_type', 'memory'), 'memory')
        self.assertIsNone(seen['props'].get('data'))

    def test_assistant_panel_open_close_and_repeated_close_preserve_accessibility(self):
        server = self.app()
        client = server.test_client()
        sidebar_key, sidebar_outputs = callback_output_spec(client, 'sidebar', 'style')
        accessibility_key, accessibility_outputs = callback_output_spec(client, 'btn_sidebar', 'aria-expanded')
        for state, clicks, closes, trigger, expected in (
                ('HIDDEN', 0, 0, 'btn_sidebar', 'HIDDEN'),
                ('HIDDEN', 1, 0, 'btn_sidebar', 'SHOW'),
                ('SHOW', 1, 1, 'assistant-close', 'HIDDEN'),
                ('HIDDEN', 1, 2, 'assistant-close', 'HIDDEN'),
                ('HIDDEN', 2, 2, 'btn_sidebar', 'SHOW'),
                ('SHOW', 3, 2, 'btn_sidebar', 'HIDDEN')):
            with self.subTest(state=state, trigger=trigger, clicks=clicks, closes=closes):
                response = client.post('/_dash-update-component', json={
                    'output': sidebar_key, 'outputs': sidebar_outputs,
                    'inputs': [{'id': 'btn_sidebar', 'property': 'n_clicks', 'value': clicks},
                               {'id': 'assistant-close', 'property': 'n_clicks', 'value': closes}],
                    'state': [{'id': 'side_click', 'property': 'data', 'value': state}],
                    'changedPropIds': [trigger + '.n_clicks'],
                })
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
                payload = response.get_json()['response']
                opened = expected == 'SHOW'
                self.assertEqual(payload['side_click']['data'], expected)
                self.assertEqual(payload['sidebar']['style'], {'display': 'block' if opened else 'none'})
                self.assertEqual(payload['page-content']['style'], {})
                accessibility = client.post('/_dash-update-component', json={
                    'output': accessibility_key, 'outputs': accessibility_outputs,
                    'inputs': [{'id': 'side_click', 'property': 'data', 'value': expected}],
                    'state': [], 'changedPropIds': ['side_click.data'],
                })
                self.assertEqual(accessibility.status_code, 200, accessibility.get_data(as_text=True))
                payload = accessibility.get_json()['response']
                self.assertEqual(payload['btn_sidebar']['aria-expanded'], 'true' if opened else 'false')
                self.assertEqual(payload['sidebar']['aria-hidden'], 'false' if opened else 'true')
                self.assertEqual(payload['btn_sidebar']['aria-label'],
                                 'Close assistant panel' if opened else 'Open assistant panel')

    def test_extra_page_registers_layout_navigation_and_real_callback(self):
        calls = []
        spec = extra_page(calls)
        server = self.extra_app(spec)
        pages = server.extensions['page_registry']
        callbacks = server.extensions['callback_registry']
        self.assertIs(pages.get('/extra-report'), spec)
        self.assertIs(pages.get_by_id('extra-report'), spec)
        self.assertTrue(pages.frozen)
        self.assertTrue(callbacks.frozen)
        self.assertEqual(calls, [])
        client = self.logged_in(server)
        self.assertEqual(self.links(client), ['/', '/extra-report', '/admin', '/maintenance'])
        response = self.page(client, '/extra-report')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertIn(PRIVATE_RESULT, response.get_data(as_text=True))
        self.assertEqual(response.get_json()['response']['_pages_store']['data'],
                         {'path': '/extra-report', 'title': 'Extra report'})
        self.assertEqual(calls, [('layout', 'fixture-admin')])
        result = self.callback(client, 'extra-result', 'children', 'extra-run')
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        self.assertEqual(result.get_json()['response']['extra-result']['children'], PRIVATE_RESULT)
        self.assertEqual(calls[-1], ('callback', 'fixture-admin'))
        key, _ = callback_output_spec(client, 'extra-result', 'children')
        declaration = callbacks.callbacks[key]
        self.assertEqual(declaration.page_id, spec.page_id)
        self.assertEqual(declaration.policy, EXTRA_POLICY)

    def test_extra_page_denies_before_layout_or_callback_body_and_emits_no_payload(self):
        calls = []
        identities = FixtureIdentity()
        identities.users['admin-b'] = {'id': 'admin-b', 'role': 'admin', 'org': 'B'}
        server = self.extra_app(extra_page(calls), identities)
        for username, status in ((None, 401), ('fixture-user', 403), ('admin-b', 403)):
            with self.subTest(username=username):
                client = server.test_client() if username is None else self.logged_in(server, username)
                self.assertNotIn('/extra-report', self.links(client))
                response = self.page(client, '/extra-report')
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(PRIVATE_RESULT, response.get_data(as_text=True))
                content = response.get_json()['response']['_pages_content']['children']
                if username is None:
                    self.assertEqual(content['type'], 'Location')
                    self.assertEqual(content['props']['pathname'], '/login')
                else:
                    self.assertEqual(content['type'], 'Alert')
                    self.assertIn('403: Forbidden', content['props']['children'])
                denied = self.callback(client, 'extra-result', 'children', 'extra-run')
                self.assertEqual(denied.status_code, status, denied.get_data(as_text=True))
                self.assertEqual(set(denied.get_json()), {'error'})
                self.assertNotIn(PRIVATE_RESULT, denied.get_data(as_text=True))
                self.assertEqual(calls, [])

    def test_duplicate_or_missing_page_declarations_fail_at_build(self):
        for spec in (extra_page(page_id='reports'), extra_page(path='/page3'), None,
                     {'page_id': 'untrusted', 'path': '/untrusted'}):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                self.extra_app(spec)
        with self.assertRaises(TypeError):
            PageSpec('missing', '/missing', 'Missing policy', lambda runtime: None)
        with self.assertRaises(ValueError):
            PageSpec('missing', '/missing', 'Missing policy', lambda runtime: None, None)

    def test_extra_page_cannot_shadow_infrastructure_routes(self):
        for path in ('/healthz', '/readyz', '/api/reports/export.csv',
                     '/demo-api/report.csv', '/assets/hidden', '/_dash-layout', '/_reload-hash'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.extra_app(extra_page(path=path))

    def test_missing_weaker_or_unknown_callback_policy_fails_at_build(self):
        for case in ('missing', 'none', 'weaker', 'unknown-page', 'direct-app'):
            def register(callbacks, runtime):
                dependencies = (Output('extra-result', 'children'), Input('extra-run', 'n_clicks'))
                if case == 'direct-app':
                    callbacks._registry._app.callback(*dependencies)(lambda clicks: PRIVATE_RESULT)
                    return
                options = {'callback_id': 'extra.load', 'page_id': 'extra'}
                if case != 'missing':
                    options['policy'] = (None if case == 'none' else AccessPolicy.public()
                                         if case == 'weaker' else EXTRA_POLICY)
                if case == 'unknown-page':
                    options['page_id'] = 'missing'
                callbacks.callback(*dependencies, **options)(lambda clicks: PRIVATE_RESULT)

            spec = PageSpec('extra', '/extra', 'Extra', lambda runtime: html.Div(),
                            EXTRA_POLICY, register_callbacks=register)
            with self.subTest(case=case), self.assertRaises((TypeError, ValueError)):
                self.extra_app(spec)

    def test_page_registrar_cannot_omit_page_id_to_weaken_its_policy(self):
        for page_id_present in (False, True):
            def register(callbacks, runtime):
                options = {'callback_id': 'extra.omitted-scope', 'policy': AccessPolicy.public()}
                if page_id_present:
                    options['page_id'] = None
                callbacks.callback(Output('extra-result', 'children'), Input('extra-run', 'n_clicks'),
                                   **options)(lambda clicks: PRIVATE_RESULT)
            spec = PageSpec('extra', '/extra', 'Extra', lambda runtime: html.Div(),
                            EXTRA_POLICY, register_callbacks=register)
            message = 'cannot change or remove' if page_id_present else 'cannot be weaker'
            with self.subTest(explicit_none=page_id_present), self.assertRaisesRegex(ValueError, message):
                self.extra_app(spec)

    def test_page_registrar_automatically_records_the_correct_page_scope(self):
        def register(callbacks, runtime):
            @callbacks.callback(Output('extra-result', 'children'), Input('extra-run', 'n_clicks'),
                                callback_id='extra.bound', policy=EXTRA_POLICY)
            def bound(clicks):
                return PRIVATE_RESULT
        spec = PageSpec('extra', '/extra', 'Extra', lambda runtime: html.Div(),
                        EXTRA_POLICY, register_callbacks=register)
        server = self.extra_app(spec)
        client = self.logged_in(server)
        response = self.callback(client, 'extra-result', 'children', 'extra-run')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        key, _ = callback_output_spec(client, 'extra-result', 'children')
        declaration = server.extensions['callback_registry'].callbacks[key]
        self.assertEqual(declaration.page_id, 'extra')
        self.assertEqual(declaration.policy, EXTRA_POLICY)
        user = self.logged_in(server, 'fixture-user')
        denied = self.callback(user, 'extra-result', 'children', 'extra-run')
        self.assertEqual(denied.status_code, 403)
        self.assertNotIn(PRIVATE_RESULT, denied.get_data(as_text=True))

    def test_unknown_and_direct_injected_callbacks_deny_even_an_admin(self):
        server = self.app()
        client = self.logged_in(server)
        calls = []
        server.extensions['dash_app'].callback(
            Output('injected', 'children'), Input('injected-run', 'n_clicks'))(
                lambda clicks: calls.append(clicks) or PRIVATE_RESULT)
        for key in ('unknown.children', 'injected.children'):
            response = client.post('/_dash-update-component', json={
                'output': key, 'outputs': {'id': key.split('.')[0], 'property': 'children'},
                'inputs': [{'id': 'injected-run', 'property': 'n_clicks', 'value': 1}],
                'state': [], 'changedPropIds': ['injected-run.n_clicks'],
            })
            self.assertEqual(response.status_code, 403, response.get_data(as_text=True))
            self.assertEqual(response.get_json(), {'error': 'Unregistered callback'})
            self.assertNotIn(PRIVATE_RESULT, response.get_data(as_text=True))
        self.assertEqual(calls, [])

    def test_replaced_registered_callback_denies_before_its_new_body(self):
        for replacement in ('entry', 'function', 'direct-app'):
            with self.subTest(replacement=replacement):
                server = self.app()
                client = self.logged_in(server)
                app = server.extensions['dash_app']
                key = 'popover.is_open'
                calls = []
                def injected(*args, **kwargs):
                    calls.append(True)
                    return PRIVATE_RESULT
                if replacement == 'entry':
                    app.callback_map[key] = dict(app.callback_map[key], callback=injected)
                elif replacement == 'function':
                    app.callback_map[key]['callback'] = injected
                else:
                    app.callback(Output('popover', 'is_open'), Input('popover-target', 'n_clicks'))(injected)
                response = self.callback(client, 'popover', 'is_open', 'popover-target')
                self.assertEqual(response.status_code, 403, response.get_data(as_text=True))
                self.assertEqual(response.get_json(), {'error': 'Unregistered callback'})
                self.assertEqual(calls, [])

    def test_extra_page_and_callbacks_stay_local_to_one_app(self):
        global_pages = dict(dash.page_registry)
        global_callbacks = dict(_callback.GLOBAL_CALLBACK_MAP)
        global_dependencies = list(_callback.GLOBAL_CALLBACK_LIST)
        first = self.extra_app(extra_page(marker='first-only-extra'))
        second = self.app()
        third = self.extra_app(extra_page(marker='third-only-extra'))
        a, b, c = self.logged_in(first), self.logged_in(second), self.logged_in(third)
        self.assertIsNot(first.extensions['page_registry'], third.extensions['page_registry'])
        self.assertIsNot(first.extensions['callback_registry'], third.extensions['callback_registry'])
        self.assertNotIn('/extra-report', self.links(b))
        self.assertIn('404: Not found', self.page(b, '/extra-report').get_data(as_text=True))
        self.assertIsNone(second.extensions['callback_registry'].policy_for('extra-result.children'))
        for client, marker, excluded in ((a, 'first-only-extra', 'third-only-extra'),
                                        (c, 'third-only-extra', 'first-only-extra')):
            response = self.callback(client, 'extra-result', 'children', 'extra-run')
            self.assertEqual(response.status_code, 200)
            self.assertIn(marker, response.get_data(as_text=True))
            self.assertNotIn(excluded, response.get_data(as_text=True))
        self.assertEqual(dict(dash.page_registry), global_pages)
        self.assertEqual(dict(_callback.GLOBAL_CALLBACK_MAP), global_callbacks)
        self.assertEqual(_callback.GLOBAL_CALLBACK_LIST, global_dependencies)

    def test_cross_origin_callbacks_deny_before_login_provider_or_logout(self):
        identities, reports = FixtureIdentity(), FixtureReports()
        server = self.app(identities=identities, reports=reports)
        denied_headers = (
            {'Origin': 'https://untrusted.example.invalid'},
            {'Origin': 'null'}, {'Origin': ''}, {'Origin': 'https://localhost'},
            {'Origin': 'http://localhost:8050'},
            {'Sec-Fetch-Site': 'cross-site'},
            {'Origin': 'http://localhost', 'Sec-Fetch-Site': 'cross-site'},
        )
        for headers in denied_headers:
            with self.subTest(headers=headers):
                anonymous = server.test_client()
                login = self.login(anonymous, headers=headers)
                self.assertEqual(login.status_code, 403, login.get_data(as_text=True))
                self.assertEqual(login.get_json(), {'error': 'Cross-origin callback denied'})
                with anonymous.session_transaction() as session:
                    self.assertNotIn('_user_id', session)
        self.assertEqual(identities.authenticate_calls, 0)
        client = self.logged_in(server)
        for headers in denied_headers:
            with self.subTest(headers=headers):
                refresh = self.callback(client, 'table', 'data', 'report-refresh', headers=headers)
                logout = self.page(client, '/logout', headers=headers)
                for response in (refresh, logout):
                    self.assertEqual(response.status_code, 403, response.get_data(as_text=True))
                    self.assertEqual(response.get_json(), {'error': 'Cross-origin callback denied'})
                    self.assertNotIn('workspace-notify', response.get_data(as_text=True))
                with client.session_transaction() as session:
                    self.assertEqual(session['_user_id'], 'fixture-admin')
        self.assertEqual(reports.rows_calls, 0)
        self.assertEqual(reports.export_calls, 0)
        self.assertEqual(identities.authenticate_calls, 1)

    def test_same_origin_and_originless_cli_callbacks_remain_supported(self):
        server = self.app()
        client = self.logged_in(server)
        for headers in ({}, {'Origin': 'http://localhost'},
                        {'Origin': 'http://localhost', 'Sec-Fetch-Site': 'same-origin'}):
            with self.subTest(headers=headers):
                response = self.callback(client, 'table', 'data', 'report-refresh', headers=headers)
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
                self.assertEqual(response.get_json()['response']['table']['data'][0]['department'], 'fixture')


class ReportAndNotificationIntegrationTests(AppTestCase):
    def render_notifications(self, client, events, seen=None):
        output, outputs = callback_output_spec(client, 'workspace-notifications', 'children')
        entry = client.application.extensions['dash_app'].callback_map[output]
        self.assertEqual(len(entry['inputs']), 1)
        self.assertEqual(len(entry['state']), 1)
        dependency = entry['inputs'][0]
        return client.post('/_dash-update-component', json={
            'output': output, 'outputs': outputs,
            'inputs': [dict(dependency, value=events)],
            'state': [dict(entry['state'][0], value=[] if seen is None else seen)],
            'changedPropIds': [dependency['id'] + '.' + dependency['property']],
        })

    def assert_event(self, response, code, kind):
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        events = notification_events(response)
        self.assertEqual(len(events), 1, response.get_data(as_text=True))
        event = events[0]
        self.assertEqual(event['code'], code)
        self.assertEqual(event['kind'], kind)
        self.assertRegex(event['event_id'], r'^[0-9a-f]{32}$')
        self.assertRegex(event['audience'], r'^[0-9a-f]{64}$')
        self.assertEqual(event['request_id'], response.headers['X-Request-ID'])
        self.assertEqual(set(event), {'event_id', 'audience', 'code', 'kind', 'request_id'})
        self.assertNotIn('fixture-admin', json.dumps(event))
        return event

    def test_successful_callbacks_emit_safe_request_local_action_events(self):
        server = self.app()
        client = self.logged_in(server)
        loaded = self.assert_event(self.page(client, '/page3'), 'report.loaded', 'success')
        refreshed = self.assert_event(self.callback(client, 'table', 'data', 'report-refresh'),
                                      'report.loaded', 'success')
        exported = self.assert_event(self.callback(client, 'report-download', 'data', 'report-export'),
                                     'report.export.ready', 'success')
        simulated = self.assert_event(self.callback(client, 'adapter-result', 'children', 'adapter-run'),
                                      'simulation.done', 'success')
        skipped = self.assert_event(self.callback(client, 'adapter-result', 'children', 'adapter-run'),
                                    'simulation.skipped', 'info')
        runtime = server.extensions['workspace']
        self.assertTrue(runtime.locks.acquire('demo-report', 'another-synthetic-owner'))
        busy = self.assert_event(self.callback(client, 'adapter-result', 'children', 'adapter-run'),
                                 'simulation.busy', 'warning')
        events = (loaded, refreshed, exported, simulated, skipped, busy)
        self.assertEqual(len({event['event_id'] for event in events}), len(events))
        self.assertEqual(len({event['audience'] for event in events}), 1)
        self.assertEqual(len(runtime.mail.messages), 1)

    def test_failure_events_render_catalog_text_without_provider_details(self):
        reports = FixtureReports()
        server = self.app(reports=reports)
        client = self.logged_in(server)
        with patch.object(reports, 'rows', side_effect=RuntimeError(PROVIDER_DETAIL)):
            response = self.callback(client, 'table', 'data', 'report-refresh')
        self.assert_ui_error(response, 'report.failed', 'table')
        event = notification_events(response)[0]
        rendered = self.render_notifications(client, [event])
        self.assertEqual(rendered.status_code, 200, rendered.get_data(as_text=True))
        body = rendered.get_json()['response']
        notices = body['workspace-notifications']['children']
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]['namespace'], 'dash_mantine_components')
        self.assertEqual(notices[0]['type'], 'Notification')
        self.assertEqual(notices[0]['props']['title'], 'Report unavailable')
        self.assertIn('The report could not be loaded.', notices[0]['props']['message'])
        self.assertIn(event['request_id'], notices[0]['props']['message'])
        self.assertEqual(body['workspace-notification-seen']['data'], [event['event_id']])
        self.assertNotIn(PROVIDER_DETAIL, rendered.get_data(as_text=True))
        self.assertNotIn('fixture-admin', rendered.get_data(as_text=True))

    def test_renderer_rejects_cross_user_events_and_changed_identity_claims(self):
        identities = FixtureIdentity()
        identities.users['second-admin'] = {'id': 'second-admin', 'role': 'admin', 'org': 'A'}
        server = self.app(identities=identities)
        first, second = self.logged_in(server), self.logged_in(server, 'second-admin')
        first_event = self.assert_event(self.callback(first, 'table', 'data', 'report-refresh'),
                                        'report.loaded', 'success')
        second_event = self.assert_event(self.callback(second, 'table', 'data', 'report-refresh'),
                                         'report.loaded', 'success')
        self.assertNotEqual(first_event['audience'], second_event['audience'])
        for client, event in ((first, second_event), (second, first_event)):
            response = self.render_notifications(client, [event])
            self.assertEqual(response.status_code, 204, response.get_data(as_text=True))
            self.assertEqual(response.data, b'')
        own = self.render_notifications(first, [first_event])
        self.assertEqual(own.status_code, 200)
        self.assertEqual(len(own.get_json()['response']['workspace-notifications']['children']), 1)
        identities.users['fixture-admin']['role'] = 'user'
        changed = self.render_notifications(first, [first_event])
        self.assertEqual(changed.status_code, 204)
        self.assertEqual(changed.data, b'')

    def test_renderer_is_authenticated_and_notifications_do_not_survive_logout(self):
        server = self.app()
        client = self.logged_in(server)
        event = notification_events(self.callback(client, 'table', 'data', 'report-refresh'))[0]
        self.assertEqual(self.page(client, '/logout').status_code, 200)
        for anonymous in (client, server.test_client()):
            denied = self.render_notifications(anonymous, [event])
            self.assertEqual(denied.status_code, 401, denied.get_data(as_text=True))
            self.assertEqual(denied.get_json(), {'error': 'Login required'})
            shell = anonymous.get('/_dash-layout')
            self.assertEqual(shell.status_code, 200)
            self.assertNotIn(event['event_id'], shell.get_data(as_text=True))
            self.assertNotIn(event['audience'], shell.get_data(as_text=True))

    def test_renderer_acknowledges_duplicates_without_creating_a_server_queue(self):
        server = self.app()
        client = self.logged_in(server)
        first = notification_events(self.callback(client, 'table', 'data', 'report-refresh'))[0]
        second = notification_events(self.callback(client, 'table', 'data', 'report-refresh'))[0]
        rendered = self.render_notifications(client, [first, first, second])
        self.assertEqual(rendered.status_code, 200)
        body = rendered.get_json()['response']
        self.assertEqual(len(body['workspace-notifications']['children']), 1)
        self.assertEqual(body['workspace-notification-seen']['data'], [first['event_id'], second['event_id']])
        replay = self.render_notifications(client, [first, second], body['workspace-notification-seen']['data'])
        self.assertEqual(replay.status_code, 204)
        self.assertEqual(replay.data, b'')
        empty = self.render_notifications(self.logged_in(server), [])
        self.assertEqual(empty.status_code, 204)
        self.assertEqual(empty.data, b'')

    def test_unauthorized_actions_emit_no_data_download_or_notification(self):
        reports = FixtureReports()
        server = self.app(reports=reports)
        for username, status in ((None, 401), ('fixture-user', 403)):
            client = server.test_client() if username is None else self.logged_in(server, username)
            for component, prop, button in (('table', 'data', 'report-refresh'),
                                             ('report-download', 'data', 'report-export'),
                                             ('adapter-result', 'children', 'adapter-run')):
                response = self.callback(client, component, prop, button)
                self.assertEqual(response.status_code, status, response.get_data(as_text=True))
                self.assertEqual(set(response.get_json()), {'error'})
                for leaked in ('response', 'workspace-notify', 'event_id', 'content', 'filename'):
                    self.assertNotIn(leaked, response.get_data(as_text=True))
            page = self.page(client, '/page3')
            self.assertEqual(page.status_code, 200)
            self.assertEqual(notification_events(page), [])
            self.assertNotIn('performance-chart', page.get_data(as_text=True))
        self.assertEqual(reports.rows_calls, 0)
        self.assertEqual(reports.export_calls, 0)
        self.assertEqual(server.extensions['workspace'].mail.messages, [])

    def test_routing_serializes_a_valid_two_trace_report_chart_and_stable_controls(self):
        server = create_app(self.settings())
        server.config['TESTING'] = True
        client = server.test_client()
        self.assertEqual(self.login(client, 'demo-admin', 'demo-only').status_code, 200)
        response = self.page(client, '/page3')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        nodes = list(components(response.get_json()['response']['_pages_content']['children']))
        ids = {node['props']['id']: node for node in nodes if isinstance(node['props'].get('id'), str)}
        self.assertTrue({'performance-chart', 'table', 'report-refresh', 'report-export',
                         'report-download'}.issubset(ids))
        graph = ids['performance-chart']
        self.assertEqual(graph['type'], 'Graph')
        self.assertEqual(graph['namespace'], 'dash_core_components')
        figure = graph['props']['figure']
        self.assertEqual(len(figure['data']), 2)
        self.assertEqual([trace['name'] for trace in figure['data']], ['Revenue', 'Cost'])
        for trace in figure['data']:
            self.assertEqual(trace['type'], 'bar')
            self.assertTrue(trace['x'])
            self.assertTrue(trace['y'])
            self.assertEqual(len(trace['x']), len(trace['y']))
            self.assertTrue(all(isinstance(x, str) and x for x in trace['x']))
            self.assertTrue(all(isinstance(y, (int, float)) and math.isfinite(y) for y in trace['y']))
            self.assertGreater(sum(trace['y']), 0)
        self.assertEqual(figure['data'][0]['x'], figure['data'][1]['x'])
        self.assertEqual(figure['layout']['barmode'], 'group')
        self.assertTrue(graph['props']['config']['responsive'])
        table = ids['table']['props']
        self.assertEqual(len(table['data']), 12)
        for prop, expected in {'editable': True, 'row_deletable': True,
                               'filter_action': 'native', 'sort_action': 'native',
                               'sort_mode': 'multi', 'row_selectable': 'multi',
                               'page_action': 'native', 'page_size': 10}.items():
            self.assertEqual(table[prop], expected)
        for path in ('/', '/login', '/logout', '/admin', '/page1', '/page2', '/page3', '/maintenance'):
            self.assertEqual(client.get(path).status_code, 200)


if __name__ == '__main__':
    unittest.main()


class LogoutOriginTests(AppTestCase):
    def test_explicit_foreign_origin_cannot_force_logout(self):
        app = self.app()
        client = self.logged_in(app)
        for headers in ({'Origin': 'https://untrusted.example'}, {'Sec-Fetch-Site': 'cross-site'}, {'Origin': 'null'}):
            response = client.get('/logout', headers=headers)
            self.assertEqual(response.status_code, 403)
            with client.session_transaction() as current:
                self.assertIn('_user_id', current)
        self.assertEqual(client.get('/logout').status_code, 200)
        with client.session_transaction() as current:
            self.assertNotIn('_user_id', current)
