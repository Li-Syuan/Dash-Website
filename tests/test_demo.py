import ast
import csv
import io
import os
import runpy
import re
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from demo_services import AccessDenied, SyntheticReports, DemoLocks, DemoScheduler, MailSink, allowed
try:
    from .test_application import callback_output_spec
except ImportError:
    from test_application import callback_output_spec


def without_notification_events(content):
    """Ignore fresh event/draft identifiers; keep content, codes and audience exact."""
    if isinstance(content, list):
        return [without_notification_events(item) for item in content]
    if isinstance(content, dict):
        result = {key: without_notification_events(value) for key, value in content.items()}
        props = result.get('props', {})
        component_id = props.get('id')
        if (result.get('type') == 'Store' and isinstance(component_id, dict)
                and component_id.get('type') == 'workspace-notify'):
            event = props.get('data')
            if isinstance(event, dict):
                props['data'] = {key: value for key, value in event.items()
                                 if key not in ('event_id', 'request_id')}
        if result.get('type') == 'Store' and component_id in ('maintenance-draft', 'admin-draft', 'admin-schedule-draft'):
            if not isinstance(props.get('data'), str) or re.fullmatch(r'[0-9a-f]{32}', props['data']) is None:
                raise AssertionError('Maintenance draft must be a fresh validated idempotency key')
            props['data'] = '<fresh-draft-key>'
        return result
    return content


class ServiceTests(unittest.TestCase):
    def test_auth_matrix(self):
        for user, admin, group_a in [(None,False,False), (dict(role='admin',org='A'),True,True),
            (dict(role='user',org='A'),False,True), (dict(role='user',org='B'),False,False)]:
            self.assertEqual(allowed(user,['admin']), admin)
            self.assertEqual(allowed(user,['admin','user'],'A'), group_a)

    def test_report_authorization_and_csv(self):
        report = SyntheticReports()
        for user in [None, dict(role='user',org='A'), dict(role='user',org='B')]:
            with self.assertRaises(AccessDenied):
                report.rows(user)
            with self.assertRaises(AccessDenied):
                report.export(user)
        user = dict(role='admin',org='A')
        rows = list(csv.DictReader(io.StringIO(report.export(user))))
        self.assertEqual(len(rows), 12)
        self.assertEqual(list(rows[0]), report.columns)
        for row in rows:
            self.assertEqual(int(row['profit']), int(row['revenue'])-int(row['cost']))

    def test_lock_owner_and_expiry(self):
        now = [0]
        locks = DemoLocks(lambda: now[0])
        self.assertTrue(locks.acquire('r','a',10))
        self.assertFalse(locks.acquire('r','b'))
        self.assertFalse(locks.release('r','b'))
        now[0] = 10
        self.assertFalse(locks.release('r','a'))
        self.assertTrue(locks.acquire('r','b'))
        self.assertFalse(locks.release('r','a'))
        self.assertTrue(locks.release('r','b'))

    def test_job_dedup_and_sink(self):
        scheduler, mail = DemoScheduler(), MailSink()
        action = lambda: mail.send('Fixture','No SMTP',['test@example.invalid'])
        self.assertTrue(scheduler.run_once('job','run',action))
        self.assertFalse(scheduler.run_once('job','run',action))
        self.assertEqual(len(mail.messages),1)
        with self.assertRaises(ValueError):
            mail.send('x','x',['real@example.com'])

    def test_python38_syntax(self):
        for name in ['app.py', 'demo_services.py', 'wsgi.py']:
            ast.parse(Path(name).read_text(encoding='utf-8'), feature_version=(3,8))


class MainEntrypointTests(unittest.TestCase):
    def execute(self, run_name, configured_limit=None):
        fake_dash = SimpleNamespace(run_server=Mock())
        fake_runtime = SimpleNamespace(identities=SimpleNamespace(user_db={}))
        fake_server = SimpleNamespace(extensions={
            'dash_app': fake_dash, 'workspace': fake_runtime,
        })
        environment = {} if configured_limit is None else {
            'REPORTING_MAX_CONTENT_LENGTH': configured_limit,
        }
        with patch.dict(os.environ, environment, clear=True):
            with patch('reporting_workspace.application.create_app', return_value=fake_server) as factory:
                with patch('reporting_workspace.launcher.configure_demo_storage') as storage:
                    exports = runpy.run_path('app.py', run_name=run_name)
                    limit = os.environ.get('REPORTING_MAX_CONTENT_LENGTH')
        factory.assert_called_once_with()
        self.assertIs(exports['app'], fake_dash)
        self.assertIs(exports['server'], fake_server)
        self.assertIs(exports['runtime'], fake_runtime)
        self.assertIs(exports['user_db'], fake_runtime.identities.user_db)
        return fake_dash, storage, limit

    def test_direct_main_configures_storage_and_runs_loopback(self):
        dash, storage, limit = self.execute('__main__')
        storage.assert_called_once_with()
        self.assertEqual(limit, str(15 * 1024 * 1024))
        dash.run_server.assert_called_once_with(host='127.0.0.1', port=8050, debug=False)

    def test_direct_main_preserves_explicit_request_limit(self):
        _, _, limit = self.execute('__main__', configured_limit='2097152')
        self.assertEqual(limit, '2097152')

    def test_import_does_not_configure_storage_or_run_server(self):
        dash, storage, limit = self.execute('app_import_check')
        storage.assert_not_called()
        dash.run_server.assert_not_called()
        self.assertIsNone(limit)


class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import app, server
        cls.app, cls.server = app, server
        cls.server.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        from reporting_workspace.lifecycle import dispose_app
        dispose_app(cls.server)

    def setUp(self):
        self.client = self.server.test_client()
        self.client.get('/')

    def login(self, username, password='demo-only', changed='login-box.n_clicks'):
        return self.client.post('/_dash-update-component', json={
            'output':'..redirectHome.pathname...login-alert.is_open..',
            'outputs':[{'id':'redirectHome','property':'pathname'}, {'id':'login-alert','property':'is_open'}],
            'inputs':[{'id':'username-box','property':'value','value':username},
                {'id':'password-box','property':'value','value':password}, {'id':'login-box','property':'n_clicks','value':1}],
            'state':[], 'changedPropIds':[changed]})

    def call(self, component, prop, button):
        output, outputs = callback_output_spec(self.client, component, prop)
        return self.client.post('/_dash-update-component', json={
            'output':output, 'outputs':outputs,
            'inputs':[{'id':button,'property':'n_clicks','value':1}], 'state':[], 'changedPropIds':[button+'.n_clicks']})

    def page(self, pathname, search=''):
        return self.client.post('/_dash-update-component', json={
            'output': '.._pages_content.children..._pages_store.data..',
            'outputs': [{'id': '_pages_content', 'property': 'children'},
                        {'id': '_pages_store', 'property': 'data'}],
            'inputs': [{'id': '_pages_location', 'property': 'pathname', 'value': pathname},
                       {'id': '_pages_location', 'property': 'search', 'value': search}],
            'state': [], 'changedPropIds': ['_pages_location.pathname']})

    def page_content(self, pathname, search=''):
        response = self.page(pathname, search)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()['response']['_pages_content']['children']

    @staticmethod
    def components(content):
        if isinstance(content, list):
            for item in content:
                yield from TransportTests.components(item)
        elif isinstance(content, dict):
            if 'props' in content:
                yield content
                yield from TransportTests.components(content['props'].get('children'))

    def assert_redirect(self, content, path):
        locations = [c for c in self.components(content) if c.get('type') == 'Location']
        self.assertEqual(len(locations), 1, content)
        self.assertEqual(locations[0]['props']['pathname'], path)

    def assert_forbidden(self, content):
        self.assertEqual(content.get('type'), 'Alert', content)
        self.assertIn('403: Forbidden', content['props']['children'])
        self.assertNotIn('Demo Sales', str(content))

    def assert_heading(self, content, text):
        headings = [c['props']['children'] for c in self.components(content)
                    if c.get('type') == 'H1']
        self.assertIn(text, headings)

    def test_anonymous_callback_and_export_denied(self):
        for component, prop, button in [('table', 'data', 'report-refresh'),
                                        ('report-download', 'data', 'report-export'),
                                        ('adapter-result', 'children', 'adapter-run')]:
            with self.subTest(component=component):
                self.assertEqual(self.call(component, prop, button).status_code, 401)
        self.assertEqual(self.client.get('/demo-api/report.csv').status_code,401)

    def test_users_callback_and_export_denied(self):
        for name in ['demo-user-a','demo-user-b']:
            self.assertEqual(self.login(name).status_code,200)
            self.assertEqual(self.call('table','data','report-refresh').status_code,403)
            self.assertEqual(self.call('report-download','data','report-export').status_code,403)
            self.assertEqual(self.call('adapter-result','children','adapter-run').status_code,403)
            self.assertEqual(self.client.get('/demo-api/report.csv').status_code,403)

    def test_admin_report_and_download(self):
        self.assertEqual(self.login('demo-admin').status_code,200)
        response = self.call('table','data','report-refresh')
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(response.get_json()['response']['table']['data']),12)
        download = self.call('report-download','data','report-export')
        self.assertEqual(download.status_code,200)
        data = download.get_json()['response']['report-download']['data']
        self.assertEqual(data['filename'], 'demo-report.csv')
        self.assertEqual(data['type'], 'text/csv')
        self.assertEqual(len(list(csv.DictReader(io.StringIO(data['content'])))), 12)
        api = self.client.get('/demo-api/report.csv')
        self.assertEqual(api.status_code,200)
        self.assertEqual(api.get_data(as_text=True), data['content'])
        self.assertEqual(api.headers['Cache-Control'], 'no-store')
        self.assertIn('attachment;', api.headers['Content-Disposition'])

    def test_pages_authorization_matrix(self):
        protected = {'/': 'Report catalog', '/admin': 'Administration',
                     '/page1': 'Page 1', '/page2': 'Page 2', '/page3': 'Monthly performance',
                     '/maintenance': 'Report definitions'}
        for name in [None, 'demo-admin', 'demo-user-a', 'demo-user-b']:
            self.client = self.server.test_client()
            if name:
                self.assertEqual(self.login(name).status_code, 200)
            for path, title in protected.items():
                with self.subTest(user=name, path=path):
                    content = self.page_content(path)
                    permitted = (path in ('/', '/maintenance') or name == 'demo-admin' or
                                 (path == '/page2' and name == 'demo-user-a'))
                    if not name:
                        self.assert_redirect(content, '/login')
                    elif permitted:
                        self.assert_heading(content, title)
                        if path == '/page3':
                            tables = [c for c in self.components(content)
                                      if c['props'].get('id') == 'table']
                            self.assertEqual(len(tables), 1)
                            self.assertEqual(len(tables[0]['props']['data']), 12)
                    else:
                        self.assert_forbidden(content)
            login = self.page_content('/login')
            if name:
                self.assert_redirect(login, '/')
            else:
                self.assert_heading(login, 'Welcome back')

    def test_pages_accept_ignored_query_parameters(self):
        query = '?next=%2Fpage3&utm_source=demo'
        self.assert_heading(self.page_content('/login', query), 'Welcome back')
        for name in ['demo-admin', 'demo-user-a', 'demo-user-b']:
            self.client = self.server.test_client()
            self.login(name)
            for path in ['/', '/login', '/admin', '/page1', '/page2', '/page3', '/maintenance', '/missing']:
                with self.subTest(user=name, path=path):
                    self.assertEqual(without_notification_events(self.page_content(path, query)),
                                     without_notification_events(self.page_content(path)))

    def test_pages_role_and_org_are_independent_requirements(self):
        from app import user_db
        fixtures = {
            'test-admin-b': dict(password='demo-only', role='admin', org='B'),
            'test-other-a': dict(password='demo-only', role='other', org='A'),
        }
        with patch.dict(user_db, fixtures):
            for name in fixtures:
                with self.subTest(user=name):
                    self.client = self.server.test_client()
                    self.assertEqual(self.login(name).status_code, 200)
                    self.assert_forbidden(self.page_content('/page2'))
                    self.assert_heading(self.page_content('/'), 'Report catalog')
            self.login('test-admin-b')
            self.assert_heading(self.page_content('/admin'), 'Administration')
            self.assert_heading(self.page_content('/page1'), 'Page 1')
            self.assert_heading(self.page_content('/page3'), 'Monthly performance')

    def test_logout_revokes_session_and_callback_access(self):
        for search in ['', '?source=demo']:
            with self.subTest(search=search):
                self.login('demo-admin')
                self.assertEqual(self.call('table', 'data', 'report-refresh').status_code, 200)
                self.assert_redirect(self.page_content('/logout', search), '/login')
                with self.client.session_transaction() as session:
                    self.assertNotIn('_user_id', session)
                self.test_anonymous_callback_and_export_denied()
                self.assert_redirect(self.page_content('/page3'), '/login')
                self.assert_redirect(self.page_content('/logout', search), '/login')

    def test_invalid_credential_types_and_unicode_are_rejected(self):
        values = [([], 'demo-only'), ({}, 'demo-only'), (1, 'demo-only'),
                  (None, 'demo-only'), ('demo-admin', '\u00e9'), ('demo-admin', []),
                  ('demo-admin', {}), ('demo-admin', 123), ('demo-admin', None)]
        for username, password in values:
            with self.subTest(username=username, password=password):
                response = self.login(username, password)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.get_json()['response']['login-alert']['is_open'])
                self.assertEqual(self.client.get('/demo-api/report.csv').status_code, 401)

    def test_login_requires_button_trigger(self):
        for changed in ['username-box.value', 'password-box.value']:
            with self.subTest(changed=changed):
                self.assertEqual(self.login('demo-admin', changed=changed).status_code, 204)
                self.assertEqual(self.client.get('/demo-api/report.csv').status_code, 401)

    def test_malformed_callback_envelopes_are_rejected(self):
        for payload in [[1], 'not-an-object', 1, None, {}, {'output': []}, {'output': None}]:
            with self.subTest(payload=payload):
                response = self.client.post('/_dash-update-component', json=payload)
                self.assertEqual(response.status_code, 400)

    def test_anonymous_public_ui_callbacks(self):
        popover = self.client.post('/_dash-update-component', json={
            'output': 'popover.is_open', 'outputs': {'id': 'popover', 'property': 'is_open'},
            'inputs': [{'id': 'popover-target', 'property': 'n_clicks', 'value': 1}],
            'state': [{'id': 'popover', 'property': 'is_open', 'value': False}],
            'changedPropIds': ['popover-target.n_clicks']})
        self.assertEqual(popover.status_code, 200)
        self.assertTrue(popover.get_json()['response']['popover']['is_open'])
        sidebar = self.client.post('/_dash-update-component', json={
            'output': '..sidebar.style...page-content.style...side_click.data..',
            'outputs': [{'id': 'sidebar', 'property': 'style'},
                        {'id': 'page-content', 'property': 'style'},
                        {'id': 'side_click', 'property': 'data'}],
            'inputs': [{'id': 'btn_sidebar', 'property': 'n_clicks', 'value': 1},
                       {'id': 'assistant-close', 'property': 'n_clicks', 'value': 0}],
            'state': [{'id': 'side_click', 'property': 'data', 'value': 'SHOW'}],
            'changedPropIds': ['btn_sidebar.n_clicks']})
        self.assertEqual(sidebar.status_code, 200)
        self.assertEqual(sidebar.get_json()['response']['side_click']['data'], 'HIDDEN')
        self.assertEqual(sidebar.get_json()['response']['sidebar']['style'], {'display': 'none'})

    def test_admin_simulation_is_once_only(self):
        self.login('demo-admin')
        with patch('app.runtime.locks', DemoLocks()), patch('app.runtime.scheduler', DemoScheduler()), \
                patch('app.runtime.mail', MailSink()):
            first = self.call('adapter-result', 'children', 'adapter-run')
            second = self.call('adapter-result', 'children', 'adapter-run')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        first_text = first.get_json()['response']['adapter-result']['children']
        second_text = second.get_json()['response']['adapter-result']['children']
        self.assertIn('Fixed job executed: True', first_text)
        self.assertIn('Fixed job executed: False', second_text)
        self.assertIn('Messages captured: 1', first_text)
        self.assertIn('Messages captured: 1', second_text)

    def test_bad_login_and_unknown_session(self):
        self.assertTrue(self.login('demo-admin','wrong').get_json()['response']['login-alert']['is_open'])
        with self.client.session_transaction() as session:
            session['_user_id'] = 'deleted-user'
        self.assertEqual(self.client.get('/demo-api/report.csv').status_code,401)

    def test_all_routes_and_offline_assets(self):
        for path in ['/','/login','/logout','/admin','/page1','/page2','/page3','/maintenance','/missing']:
            self.assertEqual(self.client.get(path).status_code,200)
        page = self.client.get('/').get_data(as_text=True)
        self.assertNotIn('https://',page)
        with self.client.get('/assets/workspace.css') as asset:
            self.assertEqual(asset.status_code,200)


if __name__ == '__main__':
    unittest.main()
