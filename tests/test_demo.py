import ast
import csv
import io
from pathlib import Path
import unittest
from demo_services import AccessDenied, SyntheticReports, DemoLocks, DemoScheduler, MailSink, allowed


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
        for name in ['app.py','demo_app.py','demo_server.py','demo_services.py']:
            ast.parse(Path(name).read_text(encoding='utf-8'), feature_version=(3,8))


class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from demo_app import app
        from demo_server import server
        cls.app, cls.server = app, server
        cls.server.config['TESTING'] = True

    def setUp(self):
        self.client = self.server.test_client()
        self.client.get('/')

    def login(self, username, password='demo-only'):
        return self.client.post('/_dash-update-component', json={
            'output':'..redirectHome.pathname...login-alert.is_open..',
            'outputs':[{'id':'redirectHome','property':'pathname'}, {'id':'login-alert','property':'is_open'}],
            'inputs':[{'id':'username-box','property':'value','value':username},
                {'id':'password-box','property':'value','value':password}, {'id':'login-box','property':'n_clicks','value':1}],
            'state':[], 'changedPropIds':['login-box.n_clicks']})

    def call(self, component, prop, button):
        return self.client.post('/_dash-update-component', json={
            'output':component+'.'+prop, 'outputs':{'id':component,'property':prop},
            'inputs':[{'id':button,'property':'n_clicks','value':1}], 'state':[], 'changedPropIds':[button+'.n_clicks']})

    def test_anonymous_callback_and_export_denied(self):
        self.assertEqual(self.call('table','data','report-refresh').status_code,401)
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
        self.assertEqual(self.call('report-download','data','report-export').status_code,200)
        self.assertEqual(self.client.get('/demo-api/report.csv').status_code,200)

    def test_bad_login_and_unknown_session(self):
        self.assertTrue(self.login('demo-admin','wrong').get_json()['response']['login-alert']['is_open'])
        with self.client.session_transaction() as session:
            session['_user_id'] = 'deleted-user'
        self.assertEqual(self.client.get('/demo-api/report.csv').status_code,401)

    def test_all_routes_and_offline_assets(self):
        for path in ['/','/login','/logout','/admin','/page1','/page2','/page3','/missing']:
            self.assertEqual(self.client.get(path).status_code,200)
        page = self.client.get('/').get_data(as_text=True)
        self.assertNotIn('https://',page)
        self.assertEqual(self.client.get('/assets/workspace.css').status_code,200)


if __name__ == '__main__':
    unittest.main()
