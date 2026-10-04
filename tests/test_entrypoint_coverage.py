"""Exhaustive registered transport-denial matrix; separate from browser QA."""
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
import sys

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reporting_workspace.application import create_app
from reporting_workspace.config import Settings
from reporting_workspace.lifecycle import dispose_app


EVIDENCE = {'pages': [], 'callbacks': [], 'http_routes': [], 'checks': []}
ACTORS = (None, 'demo-admin', 'demo-user-a', 'demo-user-b', 'matrix-admin-b', 'matrix-guest-a')


class EntrypointCoverageTests(unittest.TestCase):
    enable_report_template = False

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='entry-coverage-')
        cls.server = create_app(Settings(state_path=str(Path(cls.directory.name) / 'state.sqlite'),
                                         enable_report_template=cls.enable_report_template))
        cls.server.config['TESTING'] = True
        cls.runtime = cls.server.extensions['workspace']
        cls.runtime.identities.user_db.update({
            'matrix-admin-b': dict(password='synthetic-only', role='admin', org='B'),
            'matrix-guest-a': dict(password='synthetic-only', role='guest', org='A'),
        })
        cls.pages = cls.server.extensions['page_registry'].pages
        cls.callbacks = cls.server.extensions['callback_registry'].callbacks
        EVIDENCE['pages'] = [dict(id=p.page_id, path=p.path, policy=asdict(p.policy)) for p in cls.pages]
        EVIDENCE['callbacks'] = [asdict(c) for c in cls.callbacks.values()]
        EVIDENCE['http_routes'] = [dict(path=r.rule, endpoint=r.endpoint, methods=sorted(r.methods))
                                   for r in cls.server.url_map.iter_rules()]

    @classmethod
    def tearDownClass(cls):
        dispose_app(cls.server)
        cls.directory.cleanup()

    def client(self, actor):
        client = self.server.test_client()
        if actor:
            with client.session_transaction() as session:
                session['_user_id'] = actor
                session['_fresh'] = True
        return client

    def record(self, kind, entry, actor, status, expected):
        EVIDENCE['checks'].append(dict(kind=kind, entry=entry, actor=actor or 'anonymous',
                                       status=status, expected=expected, passed=status == expected))
        self.assertEqual(status, expected, (kind, entry, actor))

    def test_every_protected_callback_rejects_every_disallowed_actor(self):
        for actor in ACTORS:
            current = self.runtime.identities.get_user(actor) if actor else None
            client = self.client(actor)
            for key, spec in self.callbacks.items():
                if spec.kind != 'server' or spec.policy.allows(current):
                    continue
                with self.subTest(actor=actor, callback=spec.callback_id):
                    response = client.post('/_dash-update-component', json={
                        'output': key, 'inputs': [], 'state': [], 'changedPropIds': []})
                    self.record('callback_denial', spec.callback_id, actor,
                                response.status_code, 401 if actor is None else 403)

    def test_clientside_and_unregistered_outputs_never_dispatch_on_server(self):
        outputs = [(key, spec.callback_id) for key, spec in self.callbacks.items() if spec.kind != 'server']
        outputs.append(('forged-server-output.children', 'unregistered-output'))
        for actor in ACTORS:
            client = self.client(actor)
            for key, name in outputs:
                with self.subTest(actor=actor, callback=name):
                    response = client.post('/_dash-update-component', json={'output': key})
                    self.record('nonserver_denial', name, actor, response.status_code, 403)

    def test_all_denied_page_layouts_fail_before_protected_render(self):
        key = next(k for k, c in self.callbacks.items() if c.callback_id == 'shell.route')
        for actor in ACTORS:
            current = self.runtime.identities.get_user(actor) if actor else None
            for page in self.pages:
                if page.policy.allows(current):
                    continue
                with self.subTest(actor=actor, page=page.path):
                    response = self.client(actor).post('/_dash-update-component', json={
                        'output': key,
                        'outputs': [{'id':'_pages_content','property':'children'}, {'id':'_pages_store','property':'data'}],
                        'inputs': [{'id':'_pages_location','property':'pathname','value':page.path},
                                   {'id':'_pages_location','property':'search','value':'?id=forged&org=A&role=admin'}],
                        'state': [], 'changedPropIds': ['_pages_location.pathname']})
                    self.assertEqual(response.status_code, 200)
                    content = json.dumps(response.get_json()['response']['_pages_content']['children'])
                    marker = 'redirect-unauthenticated-user-to-login' if actor is None else '403: Forbidden'
                    self.assertIn(marker, content)
                    EVIDENCE['checks'].append(dict(kind='rendered_page_denial', entry=page.path,
                        actor=actor or 'anonymous', passed=True, status='login redirect' if actor is None else 'forbidden view'))

    def test_api_and_export_routes_deny_outside_their_policy(self):
        paths = ('/api/reports/export.csv', '/demo-api/report.csv',
                 '/api/managed-reports/forged-id/export.csv',
                 '/QA_portal/source/forged-id/1/forged-record', '/QA_portal/feature-todo')
        for path in paths:
            response = self.client(None).get(path)
            self.record('api_denial', path, None, response.status_code, 401)
        for actor in ('demo-user-a', 'demo-user-b', 'matrix-guest-a'):
            for path in paths[:2]:
                self.record('export_denial', path, actor, self.client(actor).get(path).status_code, 403)
        for actor in ('demo-user-b', 'matrix-admin-b', 'matrix-guest-a'):
            for path in paths[3:]:
                self.record('tenant_api_denial', path, actor, self.client(actor).get(path).status_code, 403)

    def test_revoked_sessions_reject_all_protected_callbacks_on_next_request(self):
        actor = 'matrix-revoked'
        self.runtime.identities.user_db[actor] = dict(password='synthetic-only', role='admin', org='A')
        client = self.client(actor)
        self.assertEqual(client.get('/').status_code, 200)
        del self.runtime.identities.user_db[actor]
        for key, spec in self.callbacks.items():
            if spec.kind != 'server' or not spec.policy.authenticated:
                continue
            with self.subTest(callback=spec.callback_id):
                response = client.post('/_dash-update-component', json={'output':key})
                self.record('revoked_session_denial', spec.callback_id, actor, response.status_code, 401)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--write-coverage', type=Path)
    parser.add_argument('--report-template', action='store_true',
                        help='Include the explicitly enabled synthetic report template.')
    args = parser.parse_args()
    EntrypointCoverageTests.enable_report_template = args.report_template
    EVIDENCE['report_template_enabled'] = args.report_template
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(EntrypointCoverageTests))
    if args.write_coverage:
        args.write_coverage.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE['summary'] = dict(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                                   checks=len(EVIDENCE['checks']), passed=sum(c['passed'] for c in EVIDENCE['checks']),
                                   browser_test=False)
        args.write_coverage.write_text(json.dumps(EVIDENCE, ensure_ascii=False, indent=2), encoding='utf-8')
    raise SystemExit(0 if result.wasSuccessful() else 1)
