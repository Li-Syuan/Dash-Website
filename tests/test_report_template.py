"""Copyable synthetic report contract, real factory/router and Dash transport."""

import csv
import io
import unittest

from demo_services import AccessDenied
from reporting_workspace.application import create_app
from reporting_workspace.config import Settings
from reporting_workspace.errors import ProviderUnavailable
from reporting_workspace.lifecycle import dispose_app
from reporting_workspace.report_templates import (COLUMNS, InspectionReport,
                                                 QueryError, SyntheticInspectionRepository)
from reporting_workspace.report_templates.contract import MAX_SOURCE_ROWS
from reporting_workspace.ui_pages.report_template import PREFIX, SPEC, make_spec


USERS = {
    'owner-a': dict(id='owner-a', role='user', org='A'),
    'peer-a': dict(id='peer-a', role='user', org='A'),
    'admin-a': dict(id='admin-a', role='admin', org='A'),
    'owner-b': dict(id='owner-b', role='user', org='B'),
    'admin-b': dict(id='admin-b', role='admin', org='B'),
    'guest-a': dict(id='guest-a', role='guest', org='A'),
}
OWNER = USERS['owner-a']


class Identities:
    is_demo = False

    def __init__(self):
        self.users = {name: dict(value) for name, value in USERS.items()}

    def get_user(self, identifier):
        value = self.users.get(identifier)
        return dict(value) if value is not None else None

    def authenticate(self, username, password):
        return self.get_user(username) if password == 'synthetic-only' else None


class MainReport:
    is_demo = False
    columns = ['period', 'department', 'revenue', 'cost', 'profit']

    def rows(self, user):
        return []

    def export(self, user):
        return ','.join(self.columns) + '\n'


class Rows:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def rows_for_org(self, organization):
        self.calls.append(organization)
        return iter(self.rows)


def sample(**changes):
    result = dict(org='A', sample_id='sample-0001', inspection_date='2026-10-04',
                  department='Assembly', product='Synthetic product', inspected=200, rejected=3)
    result.update(changes)
    return result


class ReportTemplateServiceTests(unittest.TestCase):
    def setUp(self):
        self.identities = Identities()
        self.repository = SyntheticInspectionRepository()
        self.service = InspectionReport(self.identities, self.repository)

    def test_same_org_peers_share_rows_admin_has_no_cross_tenant_override(self):
        owner = self.service.query(OWNER, {})
        for name in ('peer-a', 'admin-a'):
            self.assertEqual(self.service.query(USERS[name], {}), owner)
        self.assertEqual(owner['total'], 32)
        for name in ('owner-b', 'admin-b'):
            foreign = self.service.query(USERS[name], {})
            self.assertEqual(foreign['total'], 32)
            self.assertEqual([row['sample_id'] for row in foreign['rows']],
                             [row['sample_id'] for row in owner['rows']])
            self.assertTrue(all(row['product'].startswith('B ') for row in foreign['rows']))
            self.assertNotIn('A synthetic', repr(foreign))
        self.assertTrue(all(set(row) == set(COLUMNS) for row in owner['rows']))

    def test_anonymous_wrong_role_and_forged_claims_fail_before_adapter_access(self):
        repository = Rows([sample()])
        service = InspectionReport(self.identities, repository)
        for actor in (None, {}, USERS['guest-a'], dict(OWNER, role='admin'),
                      dict(OWNER, org='B'), dict(OWNER, id='missing')):
            for method in (service.query, service.export_csv):
                with self.subTest(actor=actor, method=method.__name__), self.assertRaises(AccessDenied):
                    method(actor, {})
        self.assertEqual(repository.calls, [])

    def test_revoked_or_moved_identity_cannot_reuse_previously_rendered_rows(self):
        self.service.query(OWNER, {})
        for changed in (None, dict(OWNER, org='B'), dict(OWNER, role='guest')):
            self.identities.users['owner-a'] = changed
            for method in (self.service.query, self.service.export_csv):
                with self.subTest(changed=changed), self.assertRaises(AccessDenied):
                    method(OWNER, {})

    def test_revocation_during_lazy_provider_iteration_blocks_rows_and_export(self):
        identities = self.identities

        class RevokingRows:
            def rows_for_org(self, organization):
                yield sample()
                identities.users.pop('owner-a', None)

        service = InspectionReport(identities, RevokingRows())
        for method in (service.query, service.export_csv):
            identities.users['owner-a'] = dict(OWNER)
            with self.assertRaises(AccessDenied):
                method(OWNER, {})

    def test_identity_provider_substitution_and_failure_are_opaque_and_do_not_fetch(self):
        repository = Rows([sample()])

        class InvalidIdentity:
            def __init__(self, fail):
                self.fail = fail

            def get_user(self, identifier):
                if self.fail:
                    raise RuntimeError('PRIVATE-IDENTITY-BACKEND-MARKER')
                return dict(USERS['admin-b'])

        for fail in (False, True):
            service = InspectionReport(InvalidIdentity(fail), repository)
            with self.assertRaises(ProviderUnavailable) as caught:
                service.query(OWNER, {})
            self.assertNotIn('PRIVATE-IDENTITY-BACKEND-MARKER', str(caught.exception))
        self.assertEqual(repository.calls, [])

    def test_invalid_unknown_authority_and_unbounded_query_inputs_never_reach_adapter(self):
        repository = Rows([sample()])
        service = InspectionReport(self.identities, repository)
        invalid = [None, [], '', {'org': 'B'}, {'actor': USERS['admin-b']}, {'role': 'admin'},
                   {'sql': 'select *'}, {'columns': ['org']}, {'search': 'x' * 81},
                   {'search': 'bad\x00filter'}, {'search': '\ud800'}, {'search': True},
                   {'department': 'Other'}, {'order_by': 'rejected DESC; DROP TABLE x'},
                   {'order_by': []}, {'direction': 'DESC'}]
        invalid.extend({field: value} for field, values in (
            ('limit', (0, 101, True, 1.5, '20')),
            ('offset', (-1, 10001, False, 2.5, '0'))) for value in values)
        for values in invalid:
            with self.subTest(values=repr(values)), self.assertRaises(QueryError):
                service.query(OWNER, values)
        self.assertEqual(repository.calls, [])

    def test_filters_numeric_order_pagination_and_empty_results_are_deterministic(self):
        repository = Rows([sample(sample_id='sample-0003', rejected=12),
                           sample(sample_id='sample-0001', rejected=2),
                           sample(sample_id='sample-0002', rejected=12),
                           sample(sample_id='sample-0004', department='Laboratory', rejected=100)])
        service = InspectionReport(self.identities, repository)
        values = dict(department='Assembly', search='SYNTHETIC', order_by='rejected', direction='desc', limit=2)
        first = service.query(OWNER, values)
        second = service.query(OWNER, dict(values, offset=2))
        self.assertEqual([row['sample_id'] for row in first['rows']], ['sample-0002', 'sample-0003'])
        self.assertEqual([row['rejected'] for row in second['rows']], [2])
        self.assertEqual((first['total'], second['total']), (3, 3))
        self.assertEqual(service.query(OWNER, {'search': "' OR 1=1 --"})['total'], 0)
        self.assertEqual(service.query(OWNER, {'offset': 10000})['rows'], [])

    def test_foreign_adapter_row_or_duplicate_id_invalidates_the_whole_result(self):
        for rows in ([sample(), sample(org='B', sample_id='sample-0002', product='PRIVATE-TENANT-MARKER')],
                     [sample(), sample()]):
            service = InspectionReport(self.identities, Rows(rows))
            for method in (service.query, service.export_csv):
                with self.subTest(method=method.__name__), self.assertRaises(ProviderUnavailable) as caught:
                    method(OWNER, {})
                self.assertNotIn('PRIVATE-TENANT-MARKER', str(caught.exception))

    def test_provider_date_text_and_numeric_contract_rejects_unsafe_types(self):
        invalid = [dict(inspection_date=value) for value in ('2026-02-30', '20261004', '1999-12-31',
                                                           '2101-01-01', '2026-10-04T00:00:00', None)]
        invalid += [dict(inspected=value) for value in (True, -1, 1000000001, 2.5, '200', float('nan'))]
        invalid += [dict(rejected=201), dict(product='\t=FORMULA()'), dict(product='x\x00y'),
                    dict(product='\ud800'), dict(product='x' * 121), dict(product=None),
                    dict(sample_id='../../private'), dict(extra='unexpected'), dict(department='Unknown')]
        for fields in invalid:
            service = InspectionReport(self.identities, Rows([sample(**fields)]))
            with self.subTest(fields=repr(fields)), self.assertRaises(ProviderUnavailable):
                service.query(OWNER, {})

    def test_lazy_source_is_bounded_closed_and_never_silently_truncated(self):
        observed = {'count': 0, 'closed': False}

        class TooManyRows:
            def rows_for_org(self, organization):
                try:
                    for number in range(100000):
                        observed['count'] += 1
                        yield sample(sample_id='sample-{:04d}'.format(number))
                finally:
                    observed['closed'] = True

        service = InspectionReport(self.identities, TooManyRows())
        with self.assertRaises(ProviderUnavailable):
            service.query(OWNER, {})
        self.assertEqual(observed, {'count': MAX_SOURCE_ROWS + 1, 'closed': True})

    def test_csv_exports_all_filtered_rows_with_exact_schema_dates_numbers_and_safe_text(self):
        products = ['=SUM(1,2)', '+cmd', '-cmd', '@SUM(A1)', '  =formula', 'ordinary, "quoted"', '合成產品']
        rows = [sample(sample_id='sample-{:04d}'.format(index), product=product)
                for index, product in enumerate(products)]
        service = InspectionReport(self.identities, Rows(rows))
        values = dict(limit=1, offset=4, order_by='sample_id', direction='asc')
        content = service.export_csv(OWNER, values)
        parsed = list(csv.DictReader(io.StringIO(content)))
        self.assertEqual(tuple(parsed[0]), COLUMNS)
        self.assertEqual(len(parsed), len(products))
        for index, row in enumerate(parsed):
            self.assertEqual(row['product'], ("'" if index < 5 else '') + products[index])
            self.assertEqual((row['inspection_date'], row['inspected'], row['rejected']),
                             ('2026-10-04', '200', '3'))
        self.assertNotIn('org,', content)
        self.assertEqual(service.query(OWNER, values)['rows'][0]['product'], products[4])


class ReportTemplateHttpTests(unittest.TestCase):
    def setUp(self):
        self.identities = Identities()
        self.server = self.make_app()
        self.client = self.login(self.server, 'owner-a')
        self.values = {(PREFIX + '-search', 'value'): '', (PREFIX + '-department', 'value'): '',
                       (PREFIX + '-order', 'value'): 'inspection_date', (PREFIX + '-direction', 'value'): 'asc',
                       (PREFIX + '-limit', 'value'): 20, (PREFIX + '-offset', 'data'): 0,
                       (PREFIX + '-refresh', 'n_clicks'): 1, (PREFIX + '-prev', 'n_clicks'): 1,
                       (PREFIX + '-next', 'n_clicks'): 1, (PREFIX + '-export', 'n_clicks'): 1}

    def make_app(self, spec=SPEC, identities=None):
        server = create_app(Settings(secret_key='synthetic-report-template-test-secret'),
                            identities or self.identities, MainReport(), extra_pages=(spec,) if spec else ())
        server.config['TESTING'] = True
        self.addCleanup(dispose_app, server)
        return server

    @staticmethod
    def login(server, name):
        client = server.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = name
            session['_fresh'] = True
        return client

    def call(self, name, client=None, changed='refresh'):
        client = client or self.client
        specs = client.application.extensions['callback_registry'].callbacks.values()
        spec = next(item for item in specs if item.callback_id == 'report_template.' + name)
        entry = client.application.extensions['dash_app'].callback_map[spec.output_key]
        outputs = entry['output']
        response = client.post('/_dash-update-component', json={
            'output': spec.output_key,
            'outputs': [{'id': item.component_id, 'property': item.component_property} for item in outputs],
            'inputs': [dict(item, value=self.values[(item['id'], item['property'])]) for item in entry['inputs']],
            'state': [dict(item, value=self.values[(item['id'], item['property'])]) for item in entry['state']],
            'changedPropIds': [PREFIX + '-' + changed + '.n_clicks']})
        if response.status_code == 200:
            for identifier, properties in response.get_json()['response'].items():
                for prop, value in properties.items():
                    self.values[(identifier, prop)] = value
        return response

    def route(self, client=None, search=''):
        return (client or self.client).post('/_dash-update-component', json={
            'output': '.._pages_content.children..._pages_store.data..',
            'outputs': [{'id': '_pages_content', 'property': 'children'},
                        {'id': '_pages_store', 'property': 'data'}],
            'inputs': [{'id': '_pages_location', 'property': 'pathname', 'value': SPEC.path},
                       {'id': '_pages_location', 'property': 'search', 'value': search}],
            'state': [], 'changedPropIds': ['_pages_location.pathname']})

    def test_default_factory_has_no_template_and_explicit_spec_is_app_local(self):
        default = self.make_app(spec=None)
        self.assertIsNone(default.extensions['page_registry'].get(SPEC.path))
        self.assertIs(self.server.extensions['page_registry'].get(SPEC.path), SPEC)
        specs = self.server.extensions['callback_registry'].callbacks.values()
        owned = [item for item in specs if item.page_id == 'report_template']
        self.assertEqual({item.callback_id for item in owned}, {'report_template.query', 'report_template.export'})
        self.assertTrue(all(item.policy == SPEC.policy for item in owned))

    def test_reusing_spec_keeps_each_apps_authoritative_identity_and_results_isolated(self):
        identities = Identities()
        identities.users['owner-a']['org'] = 'B'
        second = self.make_app(identities=identities)
        other_client = self.login(second, 'owner-a')
        for client, present, absent in ((other_client, 'B synthetic', 'A synthetic'),
                                       (self.client, 'A synthetic', 'B synthetic')):
            response = self.call('query', client)
            self.assertEqual(response.status_code, 200)
            self.assertIn(present, response.get_data(as_text=True))
            self.assertNotIn(absent, response.get_data(as_text=True))

    def test_router_loads_tenant_rows_and_query_string_cannot_change_actor_or_provider(self):
        response = self.route(search='?org=B&role=admin&source=private&sample_id=sample-0001')
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('A synthetic product', body)
        self.assertNotIn('B synthetic product', body)
        anonymous = self.route(self.server.test_client())
        self.assertEqual(anonymous.status_code, 200)
        self.assertNotIn('synthetic product', anonymous.get_data(as_text=True))
        self.assertIn('/login', anonymous.get_data(as_text=True))

    def test_table_pagination_filter_order_and_csv_use_server_owned_rows(self):
        self.assertEqual(self.call('query').status_code, 200)
        self.assertEqual(len(self.values[(PREFIX + '-table', 'data')]), 20)
        self.assertEqual(self.call('query', changed='next').status_code, 200)
        self.assertEqual(len(self.values[(PREFIX + '-table', 'data')]), 12)
        self.assertEqual(self.call('query', changed='prev').status_code, 200)
        self.assertEqual(self.values[(PREFIX + '-offset', 'data')], 0)
        self.values[(PREFIX + '-department', 'value')] = 'Assembly'
        self.values[(PREFIX + '-order', 'value')] = 'rejected'
        self.values[(PREFIX + '-direction', 'value')] = 'desc'
        self.call('query')
        rows = self.values[(PREFIX + '-table', 'data')]
        self.assertEqual(len(rows), 16)
        self.assertEqual([row['rejected'] for row in rows], sorted((row['rejected'] for row in rows), reverse=True))
        self.values[(PREFIX + '-table', 'data')] = [{'product': 'FORGED-BROWSER-ROW', 'org': 'B'}]
        response = self.call('export', changed='export')
        self.assertEqual(response.status_code, 200)
        download = response.get_json()['response'][PREFIX + '-download']['data']
        self.assertEqual(download['filename'], 'synthetic-quality-inspections.csv')
        self.assertNotIn('FORGED-BROWSER-ROW', download['content'])
        self.assertEqual(len(list(csv.DictReader(io.StringIO(download['content'])))), 16)

    def test_unauthenticated_wrong_role_and_revoked_transport_deny_query_and_download(self):
        for client, status in ((self.server.test_client(), 401),
                               (self.login(self.server, 'guest-a'), 403)):
            for name in ('query', 'export'):
                self.assertEqual(self.call(name, client, changed='export' if name == 'export' else 'refresh').status_code,
                                 status)
        self.call('query')
        del self.identities.users['owner-a']
        for name in ('query', 'export'):
            self.assertEqual(self.call(name).status_code, 401)

    def test_foreign_tenant_session_cannot_replay_another_tenants_query_or_download(self):
        foreign = self.login(self.server, 'admin-b')
        self.values[(PREFIX + '-search', 'value')] = 'A synthetic'
        response = self.call('query', foreign)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['response'][PREFIX + '-table']['data'], [])
        self.values[(PREFIX + '-search', 'value')] = ''
        response = self.call('export', foreign, changed='export')
        content = response.get_json()['response'][PREFIX + '-download']['data']['content']
        self.assertIn('B synthetic', content)
        self.assertNotIn('A synthetic', content)

    def test_forged_paging_and_sort_produce_safe_empty_result_and_no_export(self):
        self.values[(PREFIX + '-offset', 'data')] = True
        response = self.call('query', changed='next')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['response'][PREFIX + '-table']['data'], [])
        self.values[(PREFIX + '-order', 'value')] = 'PRIVATE-RAW-SQL-MARKER'
        response = self.call('export', changed='export')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(PREFIX + '-download', response.get_json()['response'])
        self.assertNotIn('PRIVATE-RAW-SQL-MARKER', response.get_data(as_text=True))

    def test_revoked_during_callback_provider_fetch_denies_publication(self):
        identities = Identities()

        class RevokingRepository:
            def rows_for_org(self, organization):
                identities.users.pop('owner-a', None)
                yield sample(product='UNPUBLISHED-SYNTHETIC-MARKER')

        server = self.make_app(make_spec(RevokingRepository()), identities)
        client = self.login(server, 'owner-a')
        for name in ('query', 'export'):
            identities.users['owner-a'] = dict(OWNER)
            response = self.call(name, client, changed='export' if name == 'export' else 'refresh')
            self.assertEqual(response.status_code, 403)
            self.assertNotIn('UNPUBLISHED-SYNTHETIC-MARKER', response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
