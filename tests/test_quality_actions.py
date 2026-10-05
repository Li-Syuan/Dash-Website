"""Synthetic corrective-action contract and real Flask/Dash transport regressions.

These tests do not certify company data, business SLAs or browser rendering.
"""

import csv
from dataclasses import FrozenInstanceError
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from demo_services import AccessDenied
from reporting_workspace.application import create_app
from reporting_workspace.config import Settings
from reporting_workspace.errors import ProviderUnavailable
from reporting_workspace.lifecycle import dispose_app
from reporting_workspace.quality_actions import (ActionReport, COLUMNS, POLICY, Query,
                                                 QueryError, SyntheticActionRepository)
from reporting_workspace.quality_actions.contract import MAX_SOURCE_ROWS
from reporting_workspace.ui_pages.quality_actions import PREFIX, SPEC, make_spec
from reporting_workspace.ui_pages.report_template import SPEC as TEMPLATE_SPEC


USERS = {
    'owner-a': dict(id='owner-a', role='user', org='A'),
    'peer-a': dict(id='peer-a', role='user', org='A'),
    'admin-a': dict(id='admin-a', role='admin', org='A'),
    'owner-b': dict(id='owner-b', role='user', org='B'),
    'admin-b': dict(id='admin-b', role='admin', org='B'),
    'guest-a': dict(id='guest-a', role='guest', org='A'),
}
OWNER = USERS['owner-a']
AS_OF = '2026-10-05'
PUBLIC_COLUMNS = ('case_id', 'opened_date', 'due_date', 'closed_date', 'priority',
                  'finding', 'status', 'age_days', 'overdue_days')


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


def action(**changes):
    row = dict(org='A', case_id='action-0001', opened_date='2026-09-25',
               due_date='2026-10-01', closed_date='', priority='High',
               finding='Synthetic corrective finding')
    row.update(changes)
    return row


def components(value):
    """Walk actual serialized Dash component trees, without UI internals."""
    if isinstance(value, list):
        for child in value:
            yield from components(child)
    elif isinstance(value, dict):
        if 'props' in value:
            yield value
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from components(child)


class QualityActionsServiceTests(unittest.TestCase):
    def setUp(self):
        self.identities = Identities()
        self.repository = SyntheticActionRepository()
        self.service = ActionReport(self.identities, self.repository)

    def test_public_schema_defaults_policy_and_immutable_query(self):
        self.assertEqual(COLUMNS, PUBLIC_COLUMNS)
        self.assertEqual(MAX_SOURCE_ROWS, 1000)
        query = Query.parse({})
        self.assertEqual((query.search, query.status, query.priority, query.as_of,
                          query.order_by, query.direction, query.limit, query.offset),
                         ('', '', '', AS_OF, 'due_date', 'asc', 20, 0))
        with self.assertRaises(FrozenInstanceError):
            query.as_of = '2026-10-06'
        for name in ('owner-a', 'peer-a', 'admin-a', 'owner-b', 'admin-b'):
            self.assertTrue(POLICY.allows(USERS[name]))
        self.assertFalse(POLICY.allows(USERS['guest-a']))
        self.assertFalse(POLICY.allows(None))

    def test_constructor_requires_explicit_identity_and_repository_contracts(self):
        for identities, repository in ((None, self.repository), ({}, self.repository),
                                       (self.identities, None), (self.identities, {})):
            with self.subTest(identities=identities, repository=repository), self.assertRaises(TypeError):
                ActionReport(identities, repository)

    def test_same_org_peers_share_rows_and_admin_has_no_cross_tenant_override(self):
        owner = self.service.query(OWNER, {})
        self.assertEqual(owner['total'], 36)
        self.assertEqual((owner['limit'], owner['offset'], len(owner['rows'])), (20, 0, 20))
        for name in ('peer-a', 'admin-a'):
            self.assertEqual(self.service.query(USERS[name], {}), owner)
        for name in ('owner-b', 'admin-b'):
            other = self.service.query(USERS[name], {})
            self.assertEqual(other['total'], 36)
            self.assertEqual([row['case_id'] for row in other['rows']],
                             [row['case_id'] for row in owner['rows']])
            self.assertTrue(all(row['finding'].startswith('B ') for row in other['rows']))
            self.assertNotIn('A synthetic', repr(other))
        self.assertTrue(all(set(row) == set(PUBLIC_COLUMNS) for row in owner['rows']))
        self.assertEqual(self.service.query(OWNER, {}), owner)
        self.assertEqual(list(self.repository.rows_for_org('unknown')), [])

    def test_anonymous_guest_and_forged_claims_fail_before_adapter_access(self):
        repository = Rows([action()])
        service = ActionReport(self.identities, repository)
        for actor in (None, {}, USERS['guest-a'], dict(OWNER, role='admin'),
                      dict(OWNER, org='B'), dict(OWNER, id='missing'),
                      dict(OWNER, role=''), dict(OWNER, org=True)):
            for method in (service.query, service.export_csv):
                with self.subTest(actor=actor, method=method.__name__), self.assertRaises(AccessDenied):
                    method(actor, {})
        self.assertEqual(repository.calls, [])

    def test_revoked_moved_or_promoted_identity_cannot_reuse_old_claims(self):
        self.service.query(OWNER, {})
        for changed in (None, dict(OWNER, org='B'), dict(OWNER, role='guest'),
                        dict(OWNER, role='admin')):
            self.identities.users['owner-a'] = changed
            for method in (self.service.query, self.service.export_csv):
                with self.subTest(changed=changed, method=method.__name__), self.assertRaises(AccessDenied):
                    method(OWNER, {})

    def test_revocation_during_lazy_iteration_blocks_rows_and_export_and_closes_source(self):
        identities = self.identities
        closed = []

        class RevokingRows:
            def rows_for_org(self, organization):
                try:
                    yield action()
                    identities.users.pop('owner-a', None)
                finally:
                    closed.append(True)

        service = ActionReport(identities, RevokingRows())
        for method in (service.query, service.export_csv):
            identities.users['owner-a'] = dict(OWNER)
            with self.assertRaises(AccessDenied):
                method(OWNER, {})
        self.assertEqual(closed, [True, True])

    def test_authoritative_identity_is_rechecked_after_fetch_and_before_publication(self):
        class ChangingIdentities(Identities):
            def __init__(self, change_at):
                super().__init__()
                self.calls = 0
                self.change_at = change_at

            def get_user(self, identifier):
                self.calls += 1
                if self.calls == self.change_at:
                    self.users[identifier] = dict(OWNER, org='B')
                return super().get_user(identifier)

        for change_at in (2, 3):
            for name in ('query', 'export_csv'):
                identities = ChangingIdentities(change_at)
                repository = Rows([action()])
                service = ActionReport(identities, repository)
                with self.subTest(change_at=change_at, method=name), self.assertRaises(AccessDenied):
                    getattr(service, name)(OWNER, {})
                self.assertEqual(repository.calls, ['A'])

    def test_revocation_during_last_csv_cell_blocks_already_serialized_output(self):
        from reporting_workspace.quality_actions import service as service_module

        original = service_module.csv_cell
        visited = []

        def revoking_cell(value):
            visited.append(value)
            if len(visited) == len(PUBLIC_COLUMNS):
                self.identities.users.pop('owner-a', None)
            return original(value)

        service = ActionReport(self.identities, Rows([action()]))
        with patch.object(service_module, 'csv_cell', side_effect=revoking_cell):
            with self.assertRaises(AccessDenied):
                service.export_csv(OWNER, {})
        self.assertEqual(len(visited), len(PUBLIC_COLUMNS))

    def test_identity_substitution_and_backend_failure_are_opaque_and_never_fetch(self):
        repository = Rows([action()])

        class InvalidIdentity:
            def __init__(self, fail):
                self.fail = fail

            def get_user(self, identifier):
                if self.fail:
                    raise RuntimeError('PRIVATE-IDENTITY-BACKEND-MARKER')
                return dict(USERS['admin-b'])

        for fail in (False, True):
            service = ActionReport(InvalidIdentity(fail), repository)
            for method in (service.query, service.export_csv):
                with self.subTest(fail=fail, method=method.__name__), self.assertRaises(ProviderUnavailable) as caught:
                    method(OWNER, {})
                self.assertNotIn('PRIVATE-IDENTITY-BACKEND-MARKER', str(caught.exception))
        self.assertEqual(repository.calls, [])

    def test_unknown_authority_and_invalid_query_inputs_never_reach_adapter(self):
        repository = Rows([action()])
        service = ActionReport(self.identities, repository)
        invalid = [None, [], '', {'org': 'B'}, {'actor': USERS['admin-b']}, {'role': 'admin'},
                   {'sql': 'select *'}, {'columns': ['org']}, {'rows': [action()]},
                   {'search': 'x' * 81}, {'search': 'bad\x00filter'}, {'search': '\ud800'},
                   {'search': True}, {'status': 'open'}, {'status': []}, {'priority': 'Critical'},
                   {'priority': None}, {'order_by': 'overdue_days DESC; DROP TABLE x'},
                   {'order_by': []}, {'order_by': 'finding'}, {'direction': 'DESC'}]
        invalid.extend({'as_of': value} for value in ('2026-02-30', '20261005', '2026-1-05',
                       '1999-12-31', '2101-01-01', '2026-10-05T00:00:00', '', None, True))
        invalid.extend({field: value} for field, values in (
            ('limit', (0, 101, True, 1.5, '20')),
            ('offset', (-1, 10001, False, 2.5, '0'))) for value in values)
        for values in invalid:
            for method in (service.query, service.export_csv):
                with self.subTest(values=repr(values), method=method.__name__), self.assertRaises(QueryError):
                    method(OWNER, values)
        self.assertEqual(repository.calls, [])

    def test_calendar_snapshot_boundaries_and_future_closure_are_historical(self):
        rows = [action(case_id='action-0001', opened_date=AS_OF, due_date=AS_OF),
                action(case_id='action-0002', closed_date=AS_OF),
                action(case_id='action-0003', closed_date='2026-10-06'),
                action(case_id='action-0004', closed_date='2026-09-30'),
                action(case_id='action-0005', opened_date='2026-10-06', due_date='2026-10-07')]
        service = ActionReport(self.identities, Rows(rows))
        result = service.query(OWNER, dict(order_by='case_id', limit=100))
        projected = [(row['case_id'], row['closed_date'], row['status'],
                      row['age_days'], row['overdue_days']) for row in result['rows']]
        self.assertEqual(projected, [('action-0001', '', 'Open', 0, 0),
                                     ('action-0002', AS_OF, 'Closed', 10, 4),
                                     ('action-0003', '', 'Open', 10, 4),
                                     ('action-0004', '2026-09-30', 'Closed', 5, 0)])
        self.assertEqual(result['summary'], dict(open=2, closed=2, overdue=1))
        self.assertEqual(result['total'], 4)
        self.assertEqual(rows[2]['closed_date'], '2026-10-06')
        future = service.query(OWNER, dict(as_of='2026-10-06', search='action-0003'))
        self.assertEqual((future['rows'][0]['status'], future['rows'][0]['age_days'],
                          future['rows'][0]['overdue_days']), ('Closed', 11, 5))

    def test_calendar_age_crosses_leap_day_and_year_boundary_without_business_day_rules(self):
        for opened, due, as_of, age, overdue in (
                ('2024-02-28', '2024-02-29', '2024-03-01', 2, 1),
                ('2025-12-31', '2026-01-01', '2026-01-02', 2, 1),
                ('2026-10-02', '2026-10-05', '2026-10-05', 3, 0)):
            service = ActionReport(self.identities, Rows([action(opened_date=opened, due_date=due)]))
            result = service.query(OWNER, {'as_of': as_of})['rows'][0]
            with self.subTest(as_of=as_of):
                self.assertEqual((result['age_days'], result['overdue_days']), (age, overdue))
                self.assertIs(type(result['age_days']), int)
                self.assertIs(type(result['overdue_days']), int)

    def test_status_priority_search_filters_and_summary_cover_all_matches_before_paging(self):
        service = ActionReport(self.identities, Rows([
            action(case_id='action-0001', finding='Seal defect'),
            action(case_id='action-0002', finding='SEAL repair', priority='Medium'),
            action(case_id='action-0003', finding='Seal defect', closed_date='2026-10-04'),
            action(case_id='action-0004', finding='Seal inspection', due_date='2026-10-10'),
            action(case_id='action-0005', finding='Other finding')]))
        values = dict(search='sEaL', priority='High', limit=1, order_by='case_id')
        first = service.query(OWNER, values)
        second = service.query(OWNER, dict(values, offset=1))
        self.assertEqual((first['total'], second['total']), (3, 3))
        self.assertEqual(first['summary'], dict(open=2, closed=1, overdue=1))
        self.assertEqual(second['summary'], first['summary'])
        self.assertEqual(first['rows'][0]['case_id'], 'action-0001')
        self.assertEqual(second['rows'][0]['case_id'], 'action-0003')
        closed = service.query(OWNER, dict(values, status='Closed'))
        self.assertEqual(closed['summary'], dict(open=0, closed=1, overdue=0))
        opened = service.query(OWNER, dict(values, status='Open', limit=100))
        self.assertEqual(opened['total'], 2)
        self.assertTrue(all(row['status'] == 'Open' for row in opened['rows']))
        self.assertEqual(service.query(OWNER, {'search': 'ACTION-0002'})['total'], 1)
        self.assertEqual(service.query(OWNER, {'search': '2026-09-25'})['total'], 0)
        self.assertEqual(service.query(OWNER, {'search': "' OR 1=1 --"})['summary'],
                         dict(open=0, closed=0, overdue=0))
        beyond = service.query(OWNER, dict(values, offset=10000))
        self.assertEqual(beyond['rows'], [])
        self.assertEqual(beyond['summary'], first['summary'])

    def test_numeric_sort_and_case_id_ties_are_deterministic_in_both_directions(self):
        service = ActionReport(self.identities, Rows([
            action(case_id='action-0003', opened_date='2026-09-20', due_date='2026-09-23'),
            action(case_id='action-0001', opened_date='2026-10-01', due_date='2026-10-03'),
            action(case_id='action-0002', opened_date='2026-09-20', due_date='2026-09-23')]))
        for order in ('overdue_days', 'age_days'):
            descending = service.query(OWNER, dict(order_by=order, direction='desc', limit=2))
            ascending = service.query(OWNER, dict(order_by=order, direction='asc', limit=100))
            self.assertEqual([row['case_id'] for row in descending['rows']], ['action-0002', 'action-0003'])
            self.assertEqual([row['case_id'] for row in ascending['rows']],
                             ['action-0001', 'action-0002', 'action-0003'])
        default = service.query(OWNER, {})
        self.assertEqual([row['case_id'] for row in default['rows']],
                         ['action-0002', 'action-0003', 'action-0001'])

    def test_foreign_duplicate_and_future_malformed_rows_invalidate_whole_result(self):
        for rows in ([action(), action(org='B', case_id='action-0002', finding='PRIVATE-TENANT-MARKER')],
                     [action(), action()],
                     [action(), action(case_id='action-0002', opened_date='2026-12-01',
                                       due_date='2026-11-01')]):
            service = ActionReport(self.identities, Rows(rows))
            for method in (service.query, service.export_csv):
                with self.subTest(method=method.__name__), self.assertRaises(ProviderUnavailable) as caught:
                    method(OWNER, {'search': 'no match'})
                self.assertNotIn('PRIVATE-TENANT-MARKER', str(caught.exception))

    def test_provider_schema_date_sequence_and_text_types_are_strict(self):
        invalid = [dict(opened_date=value) for value in ('2026-02-30', '20260925', '1999-12-31',
                   '2101-01-01', '2026-09-25T00:00:00', None, True)]
        invalid += [dict(due_date=value) for value in ('2026-09-24', '2026-02-30', '', None)]
        invalid += [dict(closed_date=value) for value in ('2026-09-24', '2026-02-30', None, False)]
        invalid += [dict(finding=value) for value in ('', '   ', '\t=FORMULA()', 'x\x00y',
                                                     '\ud800', 'x' * 201, None, 1)]
        invalid += [dict(case_id=value) for value in ('../../private', 'action-1', 'action-０００１', None)]
        invalid += [dict(priority='Urgent'), dict(priority=[]), dict(extra='unexpected'),
                    dict(status='Closed'), dict(age_days=999), dict(overdue_days=999)]
        for fields in invalid:
            service = ActionReport(self.identities, Rows([action(**fields)]))
            with self.subTest(fields=repr(fields)), self.assertRaises(ProviderUnavailable):
                service.query(OWNER, {})
        incomplete = action()
        del incomplete['closed_date']
        for row in (None, [], 'PRIVATE-ROW-MARKER', incomplete):
            with self.subTest(row=row), self.assertRaises(ProviderUnavailable):
                ActionReport(self.identities, Rows([row])).query(OWNER, {})

    def test_bad_source_partial_iteration_and_close_failure_are_opaque(self):
        class Source:
            def __init__(self, value):
                self.value = value

            def rows_for_org(self, organization):
                return self.value

        for value in (None, 'PRIVATE-SOURCE-MARKER', b'private', action(), 1):
            service = ActionReport(self.identities, Source(value))
            with self.subTest(value=value), self.assertRaises(ProviderUnavailable):
                service.query(OWNER, {})

        observed = []

        class BrokenRows:
            def rows_for_org(self, organization):
                try:
                    yield action()
                    raise RuntimeError('PRIVATE-PARTIAL-MARKER')
                finally:
                    observed.append('closed')

        for name in ('query', 'export_csv'):
            with self.assertRaises(ProviderUnavailable) as caught:
                getattr(ActionReport(self.identities, BrokenRows()), name)(OWNER, {})
            self.assertNotIn('PRIVATE-PARTIAL-MARKER', str(caught.exception))
        self.assertEqual(observed, ['closed', 'closed'])

        # A self-iterating adapter whose close fails cannot publish its result.
        class CloseFailure:
            def __iter__(self):
                return self

            def __next__(self):
                raise StopIteration

            def close(self):
                raise RuntimeError('PRIVATE-CLOSE-MARKER')

        with self.assertRaises(ProviderUnavailable) as caught:
            ActionReport(self.identities, Source(CloseFailure())).query(OWNER, {})
        self.assertNotIn('PRIVATE-CLOSE-MARKER', str(caught.exception))

    def test_lazy_source_is_bounded_closed_and_not_silently_truncated(self):
        for name in ('query', 'export_csv'):
            observed = {'count': 0, 'closed': False}

            class TooManyRows:
                def rows_for_org(self, organization):
                    try:
                        for number in range(100000):
                            observed['count'] += 1
                            yield action(case_id='action-{:04d}'.format(number))
                    finally:
                        observed['closed'] = True

            with self.subTest(method=name), self.assertRaises(ProviderUnavailable):
                getattr(ActionReport(self.identities, TooManyRows()), name)(OWNER, {})
            self.assertEqual(observed, {'count': MAX_SOURCE_ROWS + 1, 'closed': True})

    def test_exact_source_bound_is_accepted_and_all_rows_exported(self):
        repository = Rows([action(case_id='action-{:04d}'.format(index))
                           for index in range(MAX_SOURCE_ROWS)])
        service = ActionReport(self.identities, repository)
        result = service.query(OWNER, {'limit': 100})
        self.assertEqual((result['total'], len(result['rows'])), (1000, 100))
        content = service.export_csv(OWNER, {'limit': 1, 'offset': 10000})
        self.assertEqual(len(list(csv.DictReader(io.StringIO(content)))), 1000)

    def test_successful_lazy_source_is_closed_and_query_results_do_not_cache_rows(self):
        closed = []
        source = [action()]

        class ChangingRows:
            def rows_for_org(self, organization):
                try:
                    yield from source
                finally:
                    closed.append(True)

        service = ActionReport(self.identities, ChangingRows())
        result = service.query(OWNER, {})
        result['rows'][0]['finding'] = 'CLIENT-EDITED-MARKER'
        source[0]['finding'] = 'Fresh authoritative finding'
        fresh = service.query(OWNER, {})
        self.assertEqual(fresh['rows'][0]['finding'], 'Fresh authoritative finding')
        self.assertNotIn('CLIENT-EDITED-MARKER', service.export_csv(OWNER, {}))
        self.assertEqual(closed, [True, True, True])

    def test_csv_exports_all_filtered_rows_with_exact_schema_dates_numbers_and_safe_text(self):
        findings = ['=SUM(1,2)', '+cmd', '-cmd', '@SUM(A1)', '  =formula',
                    'ordinary, "quoted"', '合成矯正措施']
        rows = [action(case_id='action-{:04d}'.format(index), finding=finding)
                for index, finding in enumerate(findings)]
        service = ActionReport(self.identities, Rows(rows))
        values = dict(limit=1, offset=4, order_by='case_id', direction='asc')
        content = service.export_csv(OWNER, values)
        parsed = list(csv.DictReader(io.StringIO(content)))
        self.assertEqual(tuple(parsed[0]), PUBLIC_COLUMNS)
        self.assertEqual(len(parsed), len(findings))
        for index, row in enumerate(parsed):
            self.assertEqual(row['finding'], ("'" if index < 5 else '') + findings[index])
            self.assertEqual((row['opened_date'], row['due_date'], row['closed_date'],
                              row['status'], row['age_days'], row['overdue_days']),
                             ('2026-09-25', '2026-10-01', '', 'Open', '10', '4'))
        self.assertNotIn('org,', content)
        self.assertEqual(service.query(OWNER, values)['rows'][0]['finding'], findings[4])
        empty = service.export_csv(OWNER, {'search': 'no matching finding'})
        self.assertEqual(list(csv.reader(io.StringIO(empty))), [list(PUBLIC_COLUMNS)])

    def test_csv_honors_as_of_filters_and_does_not_reveal_future_closure(self):
        service = ActionReport(self.identities, Rows([
            action(case_id='action-0001', closed_date='2026-10-06'),
            action(case_id='action-0002', closed_date=AS_OF),
            action(case_id='action-0003', priority='Low'),
            action(case_id='action-0004', opened_date='2026-10-06', due_date='2026-10-07')]))
        content = service.export_csv(OWNER, dict(as_of=AS_OF, status='Open', priority='High'))
        parsed = list(csv.DictReader(io.StringIO(content)))
        self.assertEqual([row['case_id'] for row in parsed], ['action-0001'])
        self.assertEqual(parsed[0]['closed_date'], '')
        self.assertNotIn('2026-10-06', content)


class QualityActionsConfigurationTests(unittest.TestCase):
    def test_opt_in_is_default_off_and_uses_strict_boolean_configuration(self):
        self.assertFalse(Settings.from_env({}).enable_quality_actions)
        for raw in ('true', 'TRUE', '1'):
            self.assertTrue(Settings.from_env({'REPORTING_ENABLE_QUALITY_ACTIONS': raw}).enable_quality_actions)
        for raw in ('false', 'FALSE', '0'):
            self.assertFalse(Settings.from_env({'REPORTING_ENABLE_QUALITY_ACTIONS': raw}).enable_quality_actions)
        for raw in ('', 'yes', ' true', '1 ', 'quality_actions.SPEC', '2'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                Settings.from_env({'REPORTING_ENABLE_QUALITY_ACTIONS': raw})
        for value in (1, 0, 'true', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Settings(enable_quality_actions=value)

    def test_production_refuses_the_synthetic_flag_and_explicit_spec(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'not-created.sqlite'
            with self.assertRaises(ValueError):
                Settings(mode='production', secret_key='synthetic-production-test-' * 3,
                         state_path=str(path), enable_quality_actions=True)
            self.assertFalse(path.exists())
            settings = Settings(mode='production', secret_key='synthetic-production-test-' * 3,
                                state_path=str(path))
            with self.assertRaisesRegex(ValueError, 'demo'):
                create_app(settings, Identities(), MainReport(), extra_pages=(SPEC,))

    def test_default_enabled_and_original_template_coexistence_are_app_scoped(self):
        servers = []
        for values in ({}, {'enable_quality_actions': True},
                       {'enable_quality_actions': True, 'enable_report_template': True}):
            server = create_app(Settings(**values), Identities(), MainReport())
            self.addCleanup(dispose_app, server)
            servers.append(server)
        disabled, enabled, both = servers
        self.assertIsNone(disabled.extensions['page_registry'].get(SPEC.path))
        self.assertIs(enabled.extensions['page_registry'].get(SPEC.path), SPEC)
        self.assertIsNone(enabled.extensions['page_registry'].get(TEMPLATE_SPEC.path))
        self.assertIs(both.extensions['page_registry'].get(SPEC.path), SPEC)
        self.assertIs(both.extensions['page_registry'].get(TEMPLATE_SPEC.path), TEMPLATE_SPEC)
        for server in servers:
            client = server.test_client()
            self.assertEqual(client.get('/healthz').status_code, 200)
            ready = client.get('/readyz').get_json()
            self.assertEqual((ready['status'], ready['scheduler']), ('ready', 'not-started'))


class QualityActionsHttpTests(unittest.TestCase):
    def setUp(self):
        self.identities = Identities()
        self.server = self.make_app()
        self.client = self.login(self.server, 'owner-a')
        self.values = {(PREFIX + '-search', 'value'): '', (PREFIX + '-status-filter', 'value'): '',
                       (PREFIX + '-priority', 'value'): '', (PREFIX + '-as-of', 'value'): AS_OF,
                       (PREFIX + '-order', 'value'): 'due_date', (PREFIX + '-direction', 'value'): 'asc',
                       (PREFIX + '-limit', 'value'): 20, (PREFIX + '-offset', 'data'): 0,
                       (PREFIX + '-refresh', 'n_clicks'): 1, (PREFIX + '-prev', 'n_clicks'): 1,
                       (PREFIX + '-next', 'n_clicks'): 1, (PREFIX + '-export', 'n_clicks'): 1}

    def make_app(self, spec=SPEC, identities=None):
        server = create_app(Settings(secret_key='synthetic-quality-actions-test-secret'),
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

    def callback(self, name, client=None):
        client = client or self.client
        registry = client.application.extensions['callback_registry'].callbacks.values()
        spec = next(item for item in registry if item.callback_id == 'quality_actions.' + name)
        entry = client.application.extensions['dash_app'].callback_map[spec.output_key]
        return spec, entry

    def call(self, name, client=None, changed='refresh'):
        client = client or self.client
        spec, entry = self.callback(name, client)
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

    def test_explicit_page_and_callbacks_have_distinct_identity_and_shared_policy(self):
        self.assertEqual((PREFIX, SPEC.path), ('quality-actions', '/QA_portal/quality-actions'))
        self.assertIs(self.server.extensions['page_registry'].get(SPEC.path), SPEC)
        specs = self.server.extensions['callback_registry'].callbacks.values()
        owned = [item for item in specs if item.page_id == 'quality_actions']
        self.assertEqual({item.callback_id for item in owned}, {'quality_actions.query', 'quality_actions.export'})
        self.assertTrue(all(item.policy == POLICY == SPEC.policy for item in owned))
        for name in ('query', 'export'):
            _, entry = self.callback(name)
            states = {(item['id'], item['property']) for item in entry['state']}
            for field in ('search', 'status-filter', 'priority', 'as-of', 'order', 'direction', 'limit'):
                self.assertIn((PREFIX + '-' + field, 'value'), states)
            self.assertNotIn((PREFIX + '-table', 'data'), states)

    def test_router_renders_read_only_numeric_table_all_controls_and_summary(self):
        response = self.route()
        self.assertEqual(response.status_code, 200)
        nodes = {node['props']['id']: node for node in components(response.get_json())
                 if isinstance(node.get('props', {}).get('id'), str)}
        for field in ('search', 'status-filter', 'priority', 'as-of', 'order', 'direction', 'limit',
                      'offset', 'refresh', 'prev', 'next', 'export', 'table', 'status', 'summary', 'download'):
            self.assertIn(PREFIX + '-' + field, nodes)
        table = nodes[PREFIX + '-table']['props']
        self.assertEqual(tuple(column['id'] for column in table['columns']), PUBLIC_COLUMNS)
        self.assertEqual([column['id'] for column in table['columns'] if column.get('type') == 'numeric'],
                         ['age_days', 'overdue_days'])
        self.assertFalse(table['editable'])
        self.assertEqual((table['page_action'], table['sort_action'], table['filter_action']),
                         ('none', 'none', 'none'))
        self.assertEqual(nodes[PREFIX + '-as-of']['props']['value'], AS_OF)
        self.assertIn('36', str(nodes[PREFIX + '-status']['props']['children']))

    def test_router_query_string_cannot_change_actor_provider_or_filters(self):
        response = self.route(search='?org=B&role=admin&source=private&as_of=2100-01-01')
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('A synthetic finding', body)
        self.assertNotIn('B synthetic finding', body)
        anonymous = self.route(self.server.test_client())
        self.assertEqual(anonymous.status_code, 200)
        self.assertNotIn('synthetic finding', anonymous.get_data(as_text=True))
        self.assertIn('/login', anonymous.get_data(as_text=True))
        guest = self.route(self.login(self.server, 'guest-a'))
        self.assertEqual(guest.status_code, 200)
        self.assertNotIn('synthetic finding', guest.get_data(as_text=True))

    def test_reusing_spec_preserves_each_apps_current_identity_and_tenant_rows(self):
        identities = Identities()
        identities.users['owner-a']['org'] = 'B'
        second = self.make_app(identities=identities)
        other = self.login(second, 'owner-a')
        for client, present, absent in ((other, 'B synthetic', 'A synthetic'),
                                       (self.client, 'A synthetic', 'B synthetic')):
            response = self.call('query', client)
            self.assertEqual(response.status_code, 200)
            self.assertIn(present, response.get_data(as_text=True))
            self.assertNotIn(absent, response.get_data(as_text=True))

    def test_pagination_and_filter_refresh_reset_offset_using_server_totals(self):
        self.assertEqual(self.call('query').status_code, 200)
        self.assertEqual(len(self.values[(PREFIX + '-table', 'data')]), 20)
        self.assertEqual(self.call('query', changed='next').status_code, 200)
        self.assertEqual(len(self.values[(PREFIX + '-table', 'data')]), 16)
        self.assertTrue(self.values[(PREFIX + '-next', 'disabled')])
        self.assertEqual(self.values[(PREFIX + '-offset', 'data')], 20)
        self.assertEqual(self.call('query', changed='prev').status_code, 200)
        self.assertEqual(self.values[(PREFIX + '-offset', 'data')], 0)
        self.assertTrue(self.values[(PREFIX + '-prev', 'disabled')])
        self.call('query', changed='next')
        self.values[(PREFIX + '-search', 'value')] = 'action-0001'
        self.call('query')
        self.assertEqual(self.values[(PREFIX + '-offset', 'data')], 0)
        self.assertEqual([row['case_id'] for row in self.values[(PREFIX + '-table', 'data')]], ['action-0001'])
        self.assertTrue(self.values[(PREFIX + '-next', 'disabled')])

    def test_filter_order_summary_and_csv_use_server_rows_not_client_table(self):
        self.values[(PREFIX + '-status-filter', 'value')] = 'Open'
        self.values[(PREFIX + '-priority', 'value')] = 'High'
        self.values[(PREFIX + '-order', 'value')] = 'overdue_days'
        self.values[(PREFIX + '-direction', 'value')] = 'desc'
        self.values[(PREFIX + '-limit', 'value')] = 1
        expected = ActionReport(self.identities, SyntheticActionRepository()).query(
            OWNER, dict(status='Open', priority='High', order_by='overdue_days', direction='desc', limit=1))
        response = self.call('query')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.values[(PREFIX + '-table', 'data')], expected['rows'])
        self.assertIn(PREFIX + '-summary', response.get_json()['response'])
        self.values[(PREFIX + '-table', 'data')] = [{'finding': 'FORGED-BROWSER-ROW', 'org': 'B'}]
        response = self.call('export', changed='export')
        self.assertEqual(response.status_code, 200)
        download = response.get_json()['response'][PREFIX + '-download']['data']
        self.assertEqual(download['filename'], 'synthetic-corrective-actions.csv')
        self.assertNotIn('FORGED-BROWSER-ROW', download['content'])
        self.assertEqual(len(list(csv.DictReader(io.StringIO(download['content'])))), expected['total'])
        self.assertEqual(next(csv.reader(io.StringIO(download['content']))), list(PUBLIC_COLUMNS))

    def test_as_of_control_changes_snapshot_and_export_consistently(self):
        server = self.make_app(make_spec(Rows([
            action(case_id='action-0001', closed_date='2026-10-06'),
            action(case_id='action-0002', opened_date='2026-10-06', due_date='2026-10-07')])))
        client = self.login(server, 'owner-a')
        self.call('query', client)
        self.assertEqual(len(self.values[(PREFIX + '-table', 'data')]), 1)
        self.assertEqual(self.values[(PREFIX + '-table', 'data')][0]['status'], 'Open')
        self.values[(PREFIX + '-as-of', 'value')] = '2026-10-06'
        self.values[(PREFIX + '-status-filter', 'value')] = 'Closed'
        self.call('query', client)
        row = self.values[(PREFIX + '-table', 'data')][0]
        self.assertEqual((row['status'], row['closed_date'], row['age_days']), ('Closed', '2026-10-06', 11))
        response = self.call('export', client, changed='export')
        content = response.get_json()['response'][PREFIX + '-download']['data']['content']
        self.assertEqual([record['case_id'] for record in csv.DictReader(io.StringIO(content))], ['action-0001'])

    def test_anonymous_guest_and_revoked_sessions_deny_query_and_download(self):
        for client, status in ((self.server.test_client(), 401),
                               (self.login(self.server, 'guest-a'), 403)):
            for name in ('query', 'export'):
                response = self.call(name, client, changed='export' if name == 'export' else 'refresh')
                self.assertEqual(response.status_code, status)
        self.call('query')
        del self.identities.users['owner-a']
        for name in ('query', 'export'):
            self.assertEqual(self.call(name).status_code, 401)

    def test_foreign_session_cannot_replay_another_tenants_query_or_download(self):
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

    def test_forged_paging_sort_and_dates_fail_safely_without_download(self):
        self.values[(PREFIX + '-offset', 'data')] = True
        response = self.call('query', changed='next')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['response'][PREFIX + '-table']['data'], [])
        for field, invalid in (('order', 'PRIVATE-RAW-SQL-MARKER'), ('as-of', 'PRIVATE-DATE-MARKER')):
            self.values[(PREFIX + '-offset', 'data')] = 0
            self.values[(PREFIX + '-order', 'value')] = 'due_date'
            self.values[(PREFIX + '-as-of', 'value')] = AS_OF
            self.values[(PREFIX + '-' + field, 'value')] = invalid
            response = self.call('query')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()['response'][PREFIX + '-table']['data'], [])
            self.assertNotIn(invalid, response.get_data(as_text=True))
            response = self.call('export', changed='export')
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(PREFIX + '-download', response.get_json()['response'])
            self.assertNotIn(invalid, response.get_data(as_text=True))

    def test_provider_failure_discards_partial_rows_and_sanitizes_callbacks(self):
        class FailingRows:
            def rows_for_org(self, organization):
                yield action(finding='PRIVATE-PARTIAL-MARKER')
                raise RuntimeError('PRIVATE-ADAPTER-MARKER')

        server = self.make_app(make_spec(FailingRows()))
        client = self.login(server, 'owner-a')
        query = self.call('query', client)
        self.assertEqual(query.status_code, 200)
        self.assertEqual(query.get_json()['response'][PREFIX + '-table']['data'], [])
        exported = self.call('export', client, changed='export')
        self.assertEqual(exported.status_code, 200)
        self.assertNotIn(PREFIX + '-download', exported.get_json()['response'])
        for response in (query, exported):
            self.assertNotIn('PRIVATE-PARTIAL-MARKER', response.get_data(as_text=True))
            self.assertNotIn('PRIVATE-ADAPTER-MARKER', response.get_data(as_text=True))

    def test_revocation_during_callback_fetch_denies_publication(self):
        identities = Identities()

        class RevokingRows:
            def rows_for_org(self, organization):
                identities.users.pop('owner-a', None)
                yield action(finding='UNPUBLISHED-SYNTHETIC-MARKER')

        server = self.make_app(make_spec(RevokingRows()), identities)
        client = self.login(server, 'owner-a')
        for name in ('query', 'export'):
            identities.users['owner-a'] = dict(OWNER)
            response = self.call(name, client, changed='export' if name == 'export' else 'refresh')
            self.assertEqual(response.status_code, 403)
            self.assertNotIn('UNPUBLISHED-SYNTHETIC-MARKER', response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
