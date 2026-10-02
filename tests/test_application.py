"""Factory/config/provider integration tests using real Flask/Dash transports.

Providers below are local test doubles, not production authentication examples.
No company services, network calls, browser renderer, or background worker is used.
"""

import ast
from dataclasses import asdict
from datetime import timedelta
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from demo_services import AccessDenied
from reporting_workspace.application import ProviderUnavailable, create_app
from reporting_workspace.config import Settings
from reporting_workspace.providers import (
    DemoIdentityProvider, DemoReportProvider, validate_identity, validate_providers,
)


PASSWORD = 'test-password-never-log-7319'
SECRET = '64-character-local-fixture-secret-never-used-for-deployment-7319'
PROVIDER_DETAIL = 'private-provider-diagnostic-never-disclose-5820'
COLUMNS = ['period', 'department', 'revenue', 'cost', 'profit']
LOGIN_OUTPUT = '..redirectHome.pathname...login-alert.is_open..'
PAGE_OUTPUT = '.._pages_content.children..._pages_store.data..'


def callback_output_spec(client, component, prop):
    """Resolve the real Dash wire key, including grouped and dict-ID outputs."""
    matches = []
    for key, entry in client.application.extensions['dash_app'].callback_map.items():
        output = entry['output']
        outputs = output if isinstance(output, (list, tuple)) else (output,)
        if any(item.component_id == component and item.component_property == prop
               for item in outputs):
            specs = [{'id': item.component_id, 'property': item.component_property}
                     for item in outputs]
            matches.append((key, specs if isinstance(output, (list, tuple)) else specs[0]))
    if len(matches) != 1:
        raise AssertionError('Expected one callback for {}.{}; got {}'.format(
            component, prop, len(matches)))
    return matches[0]


def components(node):
    """Walk serialized Dash component trees, without interpreting their payloads."""
    if isinstance(node, list):
        for child in node:
            yield from components(child)
    elif isinstance(node, dict) and 'props' in node:
        yield node
        yield from components(node['props'].get('children'))


def notification_events(response):
    """Read action event Stores from a real callback response or routed layout."""
    body = response.get_json()['response']
    events = []
    for key, value in body.items():
        if key.startswith('{'):
            component_id = json.loads(key)
            if component_id.get('type') == 'workspace-notify' and value.get('data'):
                events.append(value['data'])
    for node in components(body.get('_pages_content', {}).get('children')):
        component_id = node['props'].get('id')
        if (node.get('type') == 'Store' and isinstance(component_id, dict)
                and component_id.get('type') == 'workspace-notify'
                and node['props'].get('data')):
            events.append(node['props']['data'])
    return events


class FixtureIdentity:
    """An explicitly selected in-process fake, solely for production gate tests."""

    is_demo = False

    def __init__(self, users=None):
        self.users = users if users is not None else {
            'fixture-admin': {'id': 'fixture-admin', 'role': 'admin', 'org': 'A'},
            'fixture-user': {'id': 'fixture-user', 'role': 'user', 'org': 'A'},
        }
        self.authenticate_calls = 0
        self.lookup_calls = 0

    def authenticate(self, username, password):
        self.authenticate_calls += 1
        if password != PASSWORD:
            return None
        record = self.users.get(username)
        return dict(record) if record is not None else None

    def get_user(self, user_id):
        self.lookup_calls += 1
        record = self.users.get(user_id)
        return dict(record) if record is not None else None


class FixtureReports:
    is_demo = False
    columns = COLUMNS

    def __init__(self, marker='fixture'):
        self.marker = marker
        self.rows_calls = 0
        self.export_calls = 0

    def rows(self, user):
        self.rows_calls += 1
        return [dict(period='2026-01', department=self.marker,
                     revenue=12, cost=7, profit=5)]

    def export(self, user):
        self.export_calls += 1
        return ','.join(self.columns) + '\n2026-01,{},12,7,5\n'.format(self.marker)


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def settings(self, mode='demo', filename='state.sqlite3', **overrides):
        values = {'mode': mode, 'secret_key': SECRET}
        if mode == 'production':
            values.update(state_path=str(Path(self.directory.name) / filename),
                          session_cookie_secure=True)
        values.update(overrides)
        return Settings(**values)

    def app(self, settings=None, identities=None, reports=None):
        server = create_app(
            settings if settings is not None else self.settings(),
            identities if identities is not None else FixtureIdentity(),
            reports if reports is not None else FixtureReports(),
        )
        server.config['TESTING'] = True
        return server

    @staticmethod
    def login(client, username='fixture-admin', password=PASSWORD, headers=None):
        return client.post('/_dash-update-component', headers=headers, json={
            'output': LOGIN_OUTPUT,
            'outputs': [{'id': 'redirectHome', 'property': 'pathname'},
                        {'id': 'login-alert', 'property': 'is_open'}],
            'inputs': [{'id': 'username-box', 'property': 'value', 'value': username},
                       {'id': 'password-box', 'property': 'value', 'value': password},
                       {'id': 'login-box', 'property': 'n_clicks', 'value': 1}],
            'state': [], 'changedPropIds': ['login-box.n_clicks'],
        })

    @staticmethod
    def callback(client, component, prop, button, headers=None):
        output, outputs = callback_output_spec(client, component, prop)
        return client.post('/_dash-update-component', headers=headers, json={
            'output': output, 'outputs': outputs,
            'inputs': [{'id': button, 'property': 'n_clicks', 'value': 1}],
            'state': [], 'changedPropIds': [button + '.n_clicks'],
        })

    @staticmethod
    def page(client, path, headers=None):
        return client.post('/_dash-update-component', headers=headers, json={
            'output': PAGE_OUTPUT,
            'outputs': [{'id': '_pages_content', 'property': 'children'},
                        {'id': '_pages_store', 'property': 'data'}],
            'inputs': [{'id': '_pages_location', 'property': 'pathname', 'value': path},
                       {'id': '_pages_location', 'property': 'search', 'value': ''}],
            'state': [], 'changedPropIds': ['_pages_location.pathname'],
        })

    def logged_in(self, server, username='fixture-admin'):
        client = server.test_client()
        response = self.login(client, username)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertFalse(response.get_json()['response']['login-alert']['is_open'])
        return client

    def assert_ui_error(self, response, code, primary_component=None):
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        if primary_component is not None:
            self.assertNotIn(primary_component, response.get_json()['response'])
        events = notification_events(response)
        self.assertEqual(len(events), 1, response.get_data(as_text=True))
        self.assertEqual(events[0]['kind'], 'error')
        self.assertEqual(events[0]['code'], code)
        self.assertEqual(events[0]['request_id'], response.headers['X-Request-ID'])
        self.assertEqual(set(events[0]), {'event_id', 'code', 'kind', 'audience', 'request_id'})
        self.assertNotIn(PASSWORD, response.get_data(as_text=True))
        self.assertNotIn(PROVIDER_DETAIL, response.get_data(as_text=True))

    @staticmethod
    def transfer_cookie(source, destination, name='session'):
        # Flask/Werkzeug 2.2's supported cookie jar API, pinned by this project.
        cookies = [cookie for cookie in source.cookie_jar if cookie.name == name]
        if len(cookies) != 1:
            raise AssertionError('Expected one signed session cookie')
        destination.set_cookie('localhost', name, cookies[0].value,
                               secure=cookies[0].secure, httponly=True)


class ConfigurationTests(AppTestCase):
    def test_demo_settings_generate_independent_hidden_secrets(self):
        first, second = Settings(), Settings()
        self.assertEqual(first.mode, 'demo')
        self.assertFalse(first.session_cookie_secure)
        self.assertGreaterEqual(len(first.secret_key), 32)
        self.assertNotEqual(first.secret_key, second.secret_key)
        self.assertNotIn(first.secret_key, repr(first))

    def test_explicit_empty_environment_does_not_inherit_host(self):
        with patch.dict(os.environ, {'REPORTING_MODE': 'production',
                                     'REPORTING_SECRET_KEY': 'host-secret'}):
            settings = Settings.from_env({})
        self.assertEqual(settings.mode, 'demo')
        self.assertNotEqual(settings.secret_key, 'host-secret')

    def test_production_environment_and_flask_config(self):
        path = str(Path(self.directory.name) / 'from-env.sqlite3')
        settings = Settings.from_env({
            'REPORTING_MODE': 'production', 'REPORTING_SECRET_KEY': SECRET,
            'REPORTING_STATE_PATH': path, 'REPORTING_SESSION_COOKIE_SECURE': 'true',
            'REPORTING_MAX_CONTENT_LENGTH': '4096',
            'REPORTING_SESSION_LIFETIME_SECONDS': '1800',
        })
        server = self.app(settings)
        self.assertFalse(server.config['DEMO_MODE'])
        self.assertEqual(server.config['SECRET_KEY'], SECRET)
        self.assertEqual(server.config['MAX_CONTENT_LENGTH'], 4096)
        self.assertEqual(server.config['PERMANENT_SESSION_LIFETIME'], timedelta(minutes=30))
        self.assertTrue(server.config['SESSION_COOKIE_SECURE'])
        self.assertTrue(server.config['SESSION_COOKIE_HTTPONLY'])
        self.assertEqual(server.config['SESSION_COOKIE_SAMESITE'], 'Lax')

    def test_production_rejects_missing_weak_or_public_secrets(self):
        for value in (None, '', 'short', 'demo-secret-key-do-not-use-in-production',
                      'change-this-secret-key-before-production'):
            with self.subTest(secret_type=type(value).__name__), self.assertRaises(ValueError):
                self.settings('production', secret_key=value)

    def test_production_requires_absolute_state_and_secure_cookie(self):
        for overrides in ({'state_path': None}, {'state_path': 'state.sqlite3'},
                          {'state_path': ':memory:'}, {'state_path': '//remote/state.db'},
                          {'session_cookie_secure': False}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.settings('production', **overrides)

    def test_invalid_explicit_config_fails_closed(self):
        for overrides in ({'mode': 'staging'}, {'mode': 'DEMO'}, {'mode': None},
                          {'secret_key': ''}, {'secret_key': 42},
                          {'max_content_length': 0}, {'max_content_length': True},
                          {'max_content_length': 1.5}, {'session_cookie_secure': 'false'},
                          {'session_lifetime': timedelta(0)}, {'session_lifetime': 30},
                          {'state_path': '/tmp/invalid\x00.db'}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.settings(**overrides)

    def test_invalid_environment_values_fail_closed(self):
        for key, value in (
            ('REPORTING_MODE', 'prod'), ('REPORTING_MODE', 1),
            ('REPORTING_MAX_CONTENT_LENGTH', '0'),
            ('REPORTING_MAX_CONTENT_LENGTH', '-1'),
            ('REPORTING_MAX_CONTENT_LENGTH', ' 10'),
            ('REPORTING_MAX_CONTENT_LENGTH', '1.0'),
            ('REPORTING_MAX_CONTENT_LENGTH', '１２'),
            ('REPORTING_SESSION_LIFETIME_SECONDS', ''),
            ('REPORTING_SESSION_LIFETIME_SECONDS', '9' * 30),
            ('REPORTING_SESSION_COOKIE_SECURE', 'yes'),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                Settings.from_env({key: value})


class ProviderContractTests(AppTestCase):
    def test_production_requires_both_explicit_non_demo_instances(self):
        settings = self.settings('production')
        for identity, reports in (
            (None, None), (FixtureIdentity(), None), (None, FixtureReports()),
            (DemoIdentityProvider(), FixtureReports()),
            (FixtureIdentity(), DemoReportProvider()),
            ('some.module:provider', FixtureReports()),
            (FixtureIdentity, FixtureReports()),
            (FixtureIdentity(), FixtureReports),
        ):
            with self.subTest(identity=type(identity).__name__, reports=type(reports).__name__):
                with self.assertRaises(ValueError):
                    create_app(settings, identity, reports)
        self.assertFalse(Path(settings.state_path).exists())

    def test_missing_or_non_boolean_demo_marker_is_rejected(self):
        settings = self.settings('production')
        for marker in (None, True, 0, '', 'false'):
            for provider in (FixtureIdentity(), FixtureReports()):
                provider.is_demo = marker
                identities = provider if isinstance(provider, FixtureIdentity) else FixtureIdentity()
                reports = provider if isinstance(provider, FixtureReports) else FixtureReports()
                with self.subTest(provider=type(provider).__name__, marker=marker):
                    with self.assertRaises(ValueError):
                        validate_providers(settings, identities, reports)
        class MissingMarker:
            authenticate = lambda self, username, password: None
            get_user = lambda self, user_id: None
        with self.assertRaises(ValueError):
            validate_providers(settings, MissingMarker(), FixtureReports())

    def test_provider_methods_and_report_columns_are_validated(self):
        settings = self.settings()
        with self.assertRaises(ValueError):
            validate_providers(settings, object(), FixtureReports())
        for columns in (None, [], ['period', 'period'], [''], [1], 'period'):
            provider = FixtureReports()
            provider.columns = columns
            with self.subTest(columns=columns), self.assertRaises(ValueError):
                validate_providers(settings, FixtureIdentity(), provider)
        provider = FixtureReports()
        provider.columns = ['unmapped-company-schema']
        with self.assertRaises(ValueError):
            self.app(reports=provider)

    def test_identity_validation_discards_extra_fields_without_mutating_input(self):
        original = dict(id='fixture-admin', role='admin', org='A',
                        password=PASSWORD, access_token='test-token')
        self.assertEqual(validate_identity(original),
                         dict(id='fixture-admin', role='admin', org='A'))
        self.assertEqual(original['password'], PASSWORD)
        for value in (None, [], {}, {'id': 'x', 'role': 'admin'},
                      {'id': 'x', 'role': '', 'org': 'A'},
                      {'id': 'x\n', 'role': 'admin', 'org': 'A'},
                      {'id': 'x' * 256, 'role': 'admin', 'org': 'A'},
                      {'id': 'x', 'role': ['admin'], 'org': 'A'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_identity(value)

    def test_default_demo_providers_have_independent_user_mappings(self):
        first, second = create_app(Settings()), create_app(Settings())
        a, b = first.extensions['workspace'], second.extensions['workspace']
        self.assertIsNot(a.identities, b.identities)
        self.assertIsNot(a.identities.user_db, b.identities.user_db)
        a.identities.user_db.pop('demo-admin')
        self.assertIsNone(a.identities.get_user('demo-admin'))
        self.assertIsNotNone(b.identities.get_user('demo-admin'))

    def test_factory_validates_without_authenticating_or_loading_reports(self):
        identities, reports = FixtureIdentity(), FixtureReports()
        self.app(self.settings('production'), identities, reports)
        self.assertEqual(identities.authenticate_calls, 0)
        self.assertEqual(identities.lookup_calls, 0)
        self.assertEqual(reports.rows_calls, 0)
        self.assertEqual(reports.export_calls, 0)


class FactoryIsolationTests(AppTestCase):
    def test_apps_and_callbacks_are_independent_without_global_pages(self):
        import dash
        before = list(dash.page_registry.items())
        first = self.app(reports=FixtureReports('first-only'))
        second = self.app(reports=FixtureReports('second-only'))
        self.assertIsNot(first, second)
        self.assertEqual(list(dash.page_registry.items()), before)
        a, b = first.extensions['dash_app'], second.extensions['dash_app']
        self.assertIsNot(a, b)
        self.assertIsNot(a.callback_map, b.callback_map)
        expected = {'shell.route', 'shell.info', 'shell.sidebar', 'shell.sidebar_accessibility',
                    'shell.notifications', 'login.submit', 'reports.refresh',
                    'reports.export', 'admin.simulate', 'maintenance.list',
                    'maintenance.select', 'maintenance.mutate', 'maintenance.feedback', 'shell.theme',
                    'shell.theme_chart', 'catalog.render', 'catalog.preferences',
                    'admin.list', 'admin.select', 'admin.schedule', 'admin.mutate',
                    'managed.load', 'managed.save', 'managed.export'}
        client_callbacks = {'shell.theme', 'shell.theme_chart', 'catalog.preferences'}
        for server, app in ((first, a), (second, b)):
            declarations = server.extensions['callback_registry'].callbacks
            self.assertEqual({spec.callback_id for spec in declarations.values()}, expected)
            self.assertEqual(set(app.callback_map), set(declarations))
            for key in app.callback_map:
                declaration = declarations[key]
                policy = server.extensions['callback_registry'].policy_for(key)
                if declaration.callback_id in client_callbacks:
                    self.assertEqual(declaration.kind, 'client')
                    self.assertIsNone(policy)
                    self.assertNotIn('callback', app.callback_map[key])
                else:
                    self.assertEqual(declaration.kind, 'server')
                    self.assertIs(policy, declaration.policy)
        self.assertEqual(set(a.callback_map), set(b.callback_map))
        client_a, client_b = self.logged_in(first), self.logged_in(second)
        for client, marker in ((client_a, 'first-only'), (client_b, 'second-only')):
            response = self.callback(client, 'table', 'data', 'report-refresh')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()['response']['table']['data'][0]['department'], marker)
        self.page(client_a, '/logout')
        self.assertEqual(client_a.get('/demo-api/report.csv').status_code, 401)
        self.assertEqual(client_b.get('/demo-api/report.csv').status_code, 200)

    def test_same_secret_and_provider_accept_existing_session_on_new_app(self):
        identities = FixtureIdentity()
        settings = self.settings('production')
        first, second = self.app(settings, identities), self.app(settings, identities)
        client_a, client_b = self.logged_in(first), second.test_client()
        self.transfer_cookie(client_a, client_b)
        response = client_b.get('/demo-api/report.csv')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(identities.authenticate_calls, 1)
        self.assertGreater(identities.lookup_calls, 0)

    def test_different_secret_rejects_transferred_session(self):
        identities = FixtureIdentity()
        first = self.app(self.settings('production'), identities)
        second = self.app(self.settings('production', secret_key=SECRET + '-different'), identities)
        client_a, client_b = self.logged_in(first), second.test_client()
        self.transfer_cookie(client_a, client_b)
        self.assertEqual(client_b.get('/demo-api/report.csv').status_code, 401)
        self.assertEqual(self.callback(client_b, 'table', 'data', 'report-refresh').status_code, 401)

    def test_sessions_reload_current_provider_claims_and_revocation(self):
        identities = FixtureIdentity()
        server = self.app(identities=identities)
        client = self.logged_in(server)
        identities.users['fixture-admin']['role'] = 'user'
        self.assertEqual(client.get('/demo-api/report.csv').status_code, 403)
        self.assertEqual(self.callback(client, 'table', 'data', 'report-refresh').status_code, 403)
        identities.users.pop('fixture-admin')
        self.assertEqual(client.get('/demo-api/report.csv').status_code, 401)

    def test_logout_clears_session_during_identity_outage_even_on_first_request(self):
        for fresh_app in (False, True):
            for transport in ('get', 'callback'):
                with self.subTest(fresh_app=fresh_app, transport=transport):
                    identities = FixtureIdentity()
                    settings = self.settings('production', filename='logout-{}-{}.sqlite3'.format(
                        fresh_app, transport))
                    source = self.app(settings, identities)
                    authenticated = self.logged_in(source)
                    if fresh_app:
                        server = self.app(settings, identities)
                        client = server.test_client()
                        self.transfer_cookie(authenticated, client)
                    else:
                        server, client = source, authenticated
                    with patch.object(identities, 'get_user', side_effect=RuntimeError(PROVIDER_DETAIL)) as lookup:
                        response = (client.get('/logout') if transport == 'get'
                                    else self.page(client, '/logout'))
                        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
                        self.assertNotIn(PROVIDER_DETAIL, response.get_data(as_text=True))
                        with client.session_transaction() as session:
                            self.assertNotIn('_user_id', session)
                        if transport == 'callback':
                            content = response.get_json()['response']['_pages_content']['children']
                            self.assertEqual(content['props']['pathname'], '/login')
                        self.assertEqual(client.get('/api/reports/export.csv').status_code, 401)
                        self.assertEqual(self.callback(client, 'table', 'data', 'report-refresh').status_code, 401)
                        lookup.assert_not_called()
                    events = server.extensions['workspace'].state.list_audit()
                    self.assertIn('logout.succeeded', [event.event for event in events])

    def test_same_secret_does_not_supply_identity_to_other_provider(self):
        first = self.app(identities=FixtureIdentity())
        second = self.app(identities=FixtureIdentity(users={}))
        client_a, client_b = self.logged_in(first), second.test_client()
        self.transfer_cookie(client_a, client_b)
        self.assertEqual(client_b.get('/demo-api/report.csv').status_code, 401)

    def test_sessions_contain_only_identity_id_and_framework_metadata(self):
        identities = FixtureIdentity()
        identities.users['fixture-admin']['password'] = PASSWORD
        identities.users['fixture-admin']['access_token'] = PROVIDER_DETAIL
        server = self.app(identities=identities)
        client = self.logged_in(server)
        with client.session_transaction() as session:
            values = dict(session)
        self.assertEqual(values['_user_id'], 'fixture-admin')
        self.assertTrue(values['_permanent'])
        for key in ('role', 'org', 'password', 'access_token'):
            self.assertNotIn(key, values)
        self.assertNotIn(PASSWORD, json.dumps(values))
        self.assertNotIn(PROVIDER_DETAIL, json.dumps(values))

    def test_demo_simulation_state_is_isolated_between_factories(self):
        first, second = self.app(), self.app()
        runtime_a, runtime_b = first.extensions['workspace'], second.extensions['workspace']
        for field in ('locks', 'scheduler', 'mail'):
            self.assertIsNot(getattr(runtime_a, field), getattr(runtime_b, field))
        client_a, client_b = self.logged_in(first), self.logged_in(second)
        for client in (client_a, client_b):
            response = self.callback(client, 'adapter-result', 'children', 'adapter-run')
            self.assertEqual(response.status_code, 200)
            self.assertIn('Fixed job executed: True', response.get_data(as_text=True))
        response = self.callback(client_a, 'adapter-result', 'children', 'adapter-run')
        self.assertIn('Fixed job executed: False', response.get_data(as_text=True))
        self.assertEqual(len(runtime_a.mail.messages), 1)
        self.assertEqual(len(runtime_b.mail.messages), 1)


class AuthorizationAndFailureTests(AppTestCase):
    def test_direct_report_services_authorize_before_calling_provider(self):
        provider = FixtureReports()
        runtime = self.app(reports=provider).extensions['workspace']
        for user in (None, {'id': 'fixture-user', 'role': 'user', 'org': 'A'},
                     {'id': 'other', 'role': 'other', 'org': 'B'}):
            for method in (runtime.reports.rows, runtime.reports.export):
                with self.subTest(user=user, method=method.__name__), self.assertRaises(AccessDenied):
                    method(user)
        self.assertEqual(provider.rows_calls, 0)
        self.assertEqual(provider.export_calls, 0)
        self.assertEqual(len(runtime.reports.rows(dict(id='fixture-admin', role='admin', org='A'))), 1)

    def test_production_disables_simulation_in_direct_and_transport_calls(self):
        server = self.app(self.settings('production'))
        runtime = server.extensions['workspace']
        with self.assertRaises(AccessDenied):
            runtime.run_simulation(dict(id='fixture-admin', role='admin', org='A'))
        client = self.logged_in(server)
        response = self.callback(client, 'adapter-result', 'children', 'adapter-run')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(runtime.mail.messages, [])
        self.assertIsNone(runtime.state.get_job('demo-mail', 'fixture-1'))

    def test_bad_auth_provider_is_sanitized_503_without_session(self):
        identities = FixtureIdentity()
        server = self.app(self.settings('production'), identities)
        client = server.test_client()
        with patch.object(identities, 'authenticate', side_effect=RuntimeError(PASSWORD + PROVIDER_DETAIL)):
            with self.assertLogs(server.logger, level=logging.INFO) as captured:
                response = self.login(client)
        self.assertEqual(response.status_code, 503, response.get_data(as_text=True))
        self.assertEqual(response.get_json()['error'], 'Service temporarily unavailable')
        self.assertEqual(response.get_json()['request_id'], response.headers['X-Request-ID'])
        audit = json.dumps([asdict(item) for item in server.extensions['workspace'].state.list_audit()])
        for output in (response.get_data(as_text=True), '\n'.join(captured.output), audit):
            self.assertNotIn(PASSWORD, output)
            self.assertNotIn(PROVIDER_DETAIL, output)
        with client.session_transaction() as session:
            self.assertNotIn('_user_id', session)
        self.assertEqual(client.get('/demo-api/report.csv').status_code, 401)

    def test_malformed_successful_identity_is_a_provider_failure(self):
        identities = FixtureIdentity()
        server = self.app(identities=identities)
        for record in ({'id': 'fixture-admin'}, {'id': 'x', 'role': '', 'org': 'A'}, []):
            client = server.test_client()
            with patch.object(identities, 'authenticate', return_value=record):
                response = self.login(client)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(client.get('/demo-api/report.csv').status_code, 401)

    def test_report_provider_errors_are_sanitized_across_all_transports(self):
        reports = FixtureReports()
        server = self.app(self.settings('production'), reports=reports)
        client = self.logged_in(server)
        error = RuntimeError(PASSWORD + PROVIDER_DETAIL)
        with patch.object(reports, 'rows', side_effect=error), patch.object(reports, 'export', side_effect=error):
            with self.assertLogs(server.logger, level=logging.INFO) as captured:
                responses = [client.get('/demo-api/report.csv?credential=' + PASSWORD),
                             self.callback(client, 'table', 'data', 'report-refresh'),
                             self.callback(client, 'report-download', 'data', 'report-export'),
                             self.page(client, '/page3')]
        self.assertEqual(responses[0].status_code, 503, responses[0].get_data(as_text=True))
        self.assertEqual(responses[0].get_json()['error'], 'Service temporarily unavailable')
        self.assert_ui_error(responses[1], 'report.failed', 'table')
        self.assert_ui_error(responses[2], 'report.export.failed', 'report-download')
        self.assert_ui_error(responses[3], 'report.failed')
        layout = responses[3].get_json()['response']['_pages_content']['children']
        tables = [item for item in components(layout) if item['props'].get('id') == 'table']
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]['props']['data'], [])
        for response in responses:
            self.assertNotIn(PASSWORD, response.get_data(as_text=True))
            self.assertNotIn(PROVIDER_DETAIL, response.get_data(as_text=True))
        audit = json.dumps([asdict(item) for item in server.extensions['workspace'].state.list_audit()])
        for output in ('\n'.join(captured.output), audit):
            self.assertNotIn(PASSWORD, output)
            self.assertNotIn(PROVIDER_DETAIL, output)
        self.assertTrue(server.extensions['workspace'].state.list_audit())

    def test_user_lookup_failure_never_retains_authority_or_leaks_details(self):
        identities = FixtureIdentity()
        server = self.app(self.settings('production'), identities)
        client = self.logged_in(server)
        with patch.object(identities, 'get_user', side_effect=RuntimeError(PROVIDER_DETAIL)):
            response = client.get('/demo-api/report.csv')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(PROVIDER_DETAIL, response.get_data(as_text=True))

    def test_valid_unicode_identity_is_pseudonymized_in_persistent_audit(self):
        username = '測試使用者@organization.invalid'
        identities = FixtureIdentity(users={username: dict(id=username, role='admin', org='A')})
        server = self.app(self.settings('production'), identities)
        client = self.logged_in(server, username)
        response = client.get('/demo-api/report.csv')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        entries = server.extensions['workspace'].state.list_audit()
        self.assertTrue(entries)
        self.assertNotIn(username, json.dumps([asdict(item) for item in entries], ensure_ascii=False))
        for entry in entries:
            if entry.actor_id is not None:
                self.assertRegex(entry.actor_id, r'^[0-9a-f]{64}$')

    def test_failed_login_does_not_persist_password_in_sqlite_or_logs(self):
        server = self.app(self.settings('production'))
        client = server.test_client()
        with self.assertLogs(server.logger, level=logging.INFO) as captured:
            response = self.login(client, password=PASSWORD + '-wrong')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(response.get_json()['response']['login-alert']['is_open'])
        raw_database = Path(server.extensions['workspace'].settings.state_path).read_bytes()
        self.assertNotIn(PASSWORD.encode('utf-8'), raw_database)
        self.assertNotIn(PASSWORD, '\n'.join(captured.output))
        audit = server.extensions['workspace'].state.list_audit()
        self.assertTrue(audit)
        self.assertIn('denied', [entry.outcome for entry in audit])

    def test_direct_provider_exception_becomes_opaque_service_exception(self):
        reports = FixtureReports()
        runtime = self.app(reports=reports).extensions['workspace']
        with patch.object(reports, 'rows', side_effect=RuntimeError(PROVIDER_DETAIL)):
            with self.assertRaises(ProviderUnavailable) as caught:
                runtime.reports.rows(dict(id='fixture-admin', role='admin', org='A'))
        self.assertNotIn(PROVIDER_DETAIL, str(caught.exception))


class HttpAndStorageTests(AppTestCase):
    def test_security_headers_and_server_generated_request_id(self):
        server = self.app()
        client = server.test_client()
        responses = [client.get('/healthz', headers={'X-Request-ID': PASSWORD}),
                     client.get('/readyz'), client.get('/demo-api/report.csv'),
                     client.post('/_dash-update-component', json={'output': []})]
        self.assertEqual([response.status_code for response in responses], [200, 200, 401, 400])
        ids = set()
        for response in responses:
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            self.assertEqual(response.headers['X-Frame-Options'], 'SAMEORIGIN')
            self.assertEqual(response.headers['Referrer-Policy'], 'same-origin')
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            request_id = response.headers['X-Request-ID']
            self.assertRegex(request_id, r'^[0-9a-f]{24}$')
            ids.add(request_id)
        self.assertEqual(len(ids), len(responses))

    def test_production_session_cookie_flags(self):
        server = self.app(self.settings('production'))
        response = self.login(server.test_client())
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        cookie = response.headers.get('Set-Cookie', '')
        for attribute in ('Secure', 'HttpOnly', 'SameSite=Lax', 'Expires='):
            self.assertIn(attribute, cookie)
        self.assertNotIn(PASSWORD, cookie)

    def test_oversized_request_fails_with_sanitized_413(self):
        server = self.app(self.settings(max_content_length=64))
        response = self.login(server.test_client())
        self.assertEqual(response.status_code, 413, response.get_data(as_text=True))
        self.assertNotIn(PASSWORD, response.get_data(as_text=True))
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_unknown_length_callback_body_is_rejected_before_authentication(self):
        identities = FixtureIdentity()
        server = self.app(self.settings(max_content_length=64), identities)
        response = server.test_client().post(
            '/_dash-update-component',
            data=json.dumps({'output': LOGIN_OUTPUT, 'credential': PASSWORD * 50}),
            content_type='application/json', environ_overrides={'CONTENT_LENGTH': ''})
        self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
        self.assertEqual(identities.authenticate_calls, 0)
        self.assertNotIn(PASSWORD, response.get_data(as_text=True))

    def test_health_and_readiness_show_mode_and_no_scheduler(self):
        for mode, storage, health_mode in (
            ('demo', 'process-memory', 'offline-demo'),
            ('production', 'sqlite-local', 'configured'),
        ):
            server = self.app(self.settings(mode))
            client = server.test_client()
            self.assertEqual(client.get('/healthz').get_json(), {'status': 'ok', 'mode': health_mode})
            self.assertEqual(client.get('/readyz').get_json(),
                             {'status': 'ready', 'storage': storage, 'scheduler': 'not-started'})

    def test_production_ui_does_not_advertise_demo_credentials_or_simulation(self):
        server = self.app(self.settings('production'))
        client = server.test_client()
        login = self.page(client, '/login')
        self.assertEqual(login.status_code, 200)
        layout = client.get('/_dash-layout')
        self.assertEqual(layout.status_code, 200)
        for text in (login.get_data(as_text=True), layout.get_data(as_text=True)):
            for disclosure in ('demo-admin', 'demo-only', 'OFFLINE DEMO'):
                self.assertNotIn(disclosure, text)
        self.login(client)
        admin = self.page(client, '/admin')
        self.assertEqual(admin.status_code, 200)
        self.assertNotIn('adapter-run', admin.get_data(as_text=True))

    def test_report_page_uses_one_provider_snapshot_for_chart_and_table(self):
        reports = FixtureReports('one-snapshot')
        server = self.app(self.settings('production'), reports=reports)
        client = self.logged_in(server)
        self.assertEqual(reports.rows_calls, 0)
        response = self.page(client, '/page3')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(reports.rows_calls, 1)

        def components(node):
            if isinstance(node, list):
                for child in node:
                    yield from components(child)
            elif isinstance(node, dict) and 'props' in node:
                yield node
                yield from components(node['props'].get('children'))

        content = response.get_json()['response']['_pages_content']['children']
        by_id = {node['props']['id']: node['props'] for node in components(content)
                 if isinstance(node['props'].get('id'), str)}
        row = by_id['table']['data'][0]
        revenue = by_id['performance-chart']['figure']['data'][0]
        cost = by_id['performance-chart']['figure']['data'][1]
        self.assertEqual(row['department'], 'one-snapshot')
        self.assertEqual(revenue['x'], [row['period']])
        self.assertEqual(revenue['y'], [row['revenue']])
        self.assertEqual(cost['y'], [row['cost']])

    def test_broken_state_fails_readiness_without_exposing_error(self):
        server = self.app(self.settings('production'))
        runtime = server.extensions['workspace']
        with patch.object(runtime.state, 'list_uncertain_jobs', side_effect=RuntimeError(PROVIDER_DETAIL)):
            response = server.test_client().get('/readyz')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json(), {'status': 'unavailable'})
        self.assertNotIn(PROVIDER_DETAIL, response.get_data(as_text=True))

    def test_sqlite_manual_simulation_deduplicates_across_app_instances(self):
        settings = self.settings(state_path=str(Path(self.directory.name) / 'shared-demo.sqlite3'))
        first, second = self.app(settings), self.app(settings)
        client_a, client_b = self.logged_in(first), self.logged_in(second)
        responses = [self.callback(client_a, 'adapter-result', 'children', 'adapter-run'),
                     self.callback(client_b, 'adapter-result', 'children', 'adapter-run')]
        for response in responses:
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            self.assertIn('Lease released: True', response.get_data(as_text=True))
        self.assertIn('Fixed job executed: True', responses[0].get_data(as_text=True))
        self.assertIn('Fixed job executed: False', responses[1].get_data(as_text=True))
        runtime_a, runtime_b = first.extensions['workspace'], second.extensions['workspace']
        self.assertEqual(len(runtime_a.mail.messages) + len(runtime_b.mail.messages), 1)
        self.assertEqual(runtime_b.state.get_job('demo-mail', 'fixture-1').state, 'succeeded')
        self.assertEqual(runtime_b.state.list_uncertain_jobs(), [])

    def test_sqlite_failed_manual_job_stays_terminal_and_releases_lease(self):
        settings = self.settings(state_path=str(Path(self.directory.name) / 'failed-demo.sqlite3'))
        server = self.app(settings)
        runtime = server.extensions['workspace']
        client = self.logged_in(server)
        with patch.object(runtime.mail, 'send', side_effect=RuntimeError(PROVIDER_DETAIL)):
            response = self.callback(client, 'adapter-result', 'children', 'adapter-run')
        self.assert_ui_error(response, 'simulation.failed', 'adapter-result')
        job = runtime.state.get_job('demo-mail', 'fixture-1')
        self.assertEqual(job.state, 'failed')
        self.assertEqual(job.failure_code, 'execution_failed')
        self.assertEqual(runtime.state.list_uncertain_jobs(), [])
        response = self.callback(client, 'adapter-result', 'children', 'adapter-run')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Lease acquired: True', response.get_data(as_text=True))
        self.assertIn('Lease released: True', response.get_data(as_text=True))
        self.assertIn('Fixed job executed: False', response.get_data(as_text=True))
        self.assertEqual(runtime.mail.messages, [])

    def assert_completion_remains_uncertain(self, raises):
        settings = self.settings(state_path=str(Path(self.directory.name) / 'uncertain-demo.sqlite3'))
        server = self.app(settings)
        runtime = server.extensions['workspace']
        client = self.logged_in(server)
        failure = {'side_effect': RuntimeError(PROVIDER_DETAIL)} if raises else {'return_value': False}
        with patch.object(runtime.state, 'finish_job', **failure) as finish:
            response = self.callback(client, 'adapter-result', 'children', 'adapter-run')
        self.assert_ui_error(response, 'simulation.failed', 'adapter-result')
        self.assertNotIn('Fixed job executed: True', response.get_data(as_text=True))
        finish.assert_called_once()
        self.assertTrue(finish.call_args.kwargs['success'])
        self.assertEqual(len(runtime.mail.messages), 1)
        job = runtime.state.get_job('demo-mail', 'fixture-1')
        self.assertEqual(job.state, 'running')
        self.assertIsNone(job.finished_at)
        self.assertIsNone(job.failure_code)
        self.assertEqual(runtime.state.list_uncertain_jobs(), [job])
        events = [entry.event for entry in runtime.state.list_audit()]
        self.assertNotIn('job.failed', events)
        self.assertNotIn('job.succeeded', events)

        reopened = self.app(settings)
        client = self.logged_in(reopened)
        response = self.callback(client, 'adapter-result', 'children', 'adapter-run')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertIn('Lease acquired: True', response.get_data(as_text=True))
        self.assertIn('Lease released: True', response.get_data(as_text=True))
        self.assertIn('Fixed job executed: False', response.get_data(as_text=True))
        self.assertEqual(reopened.extensions['workspace'].mail.messages, [])
        self.assertEqual(len(runtime.mail.messages), 1)
        self.assertEqual(reopened.extensions['workspace'].state.list_uncertain_jobs(), [job])

    def test_successful_mail_then_completion_write_error_remains_uncertain_without_retry(self):
        self.assert_completion_remains_uncertain(raises=True)

    def test_successful_mail_then_rejected_completion_remains_uncertain_without_retry(self):
        self.assert_completion_remains_uncertain(raises=False)

    def test_import_and_factory_start_no_threads_jobs_mail_or_network(self):
        # A fresh interpreter detects import effects hidden by unittest discovery.
        script = r'''
import socket
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
with patch.object(threading.Thread, 'start', side_effect=AssertionError('unexpected thread')), \
     patch.object(socket.socket, 'connect', side_effect=AssertionError('unexpected network')):
    import reporting_workspace
    import reporting_workspace.application
    import reporting_workspace.web
    from reporting_workspace.application import create_app
    from reporting_workspace.config import Settings
    from demo_services import DemoScheduler, MailSink
    with tempfile.TemporaryDirectory() as directory, \
         patch.object(DemoScheduler, 'run_once', side_effect=AssertionError('unexpected job')), \
         patch.object(MailSink, 'send', side_effect=AssertionError('unexpected mail')):
        app = create_app(Settings(state_path=str(Path(directory) / 'state.sqlite3')))
        runtime = app.extensions['workspace']
        assert runtime.state.list_uncertain_jobs() == []
        assert runtime.state.get_job('demo-mail', 'fixture-1') is None
        assert runtime.mail.messages == []
        assert app.test_client().get('/readyz').status_code == 200
print('no-startup-effects')
'''
        result = subprocess.run([sys.executable, '-c', script], cwd=str(Path(__file__).resolve().parents[1]),
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('no-startup-effects', result.stdout)

    def test_new_modules_parse_as_python38(self):
        root = Path(__file__).resolve().parents[1]
        for path in list((root / 'reporting_workspace').rglob('*.py')) + [root / 'wsgi.py']:
            with self.subTest(path=path.name):
                ast.parse(path.read_text(encoding='utf-8'), feature_version=(3, 8))


if __name__ == '__main__':
    unittest.main()
