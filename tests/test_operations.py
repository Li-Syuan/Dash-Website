"""Offline acceptance tests for five durable reporting-operations features."""
from concurrent.futures import ThreadPoolExecutor
import copy
import csv
import io
from pathlib import Path
import sqlite3
from contextlib import closing
import tempfile
import unittest
from unittest.mock import patch

from demo_services import AccessDenied
from reporting_workspace.crud import Conflict, NotFound, StateUnavailable, ValidationError
from reporting_workspace.operations import (DEMO_REPORT_ID, MAX_ROWS, OperationsService,
                                             USAGE_EVENTS)
from reporting_workspace.providers import DemoIdentityProvider


ADMIN = dict(id='operations-admin-a', role='admin', org='A')
USER = dict(id='operations-user-a', role='user', org='A')
FOREIGN = dict(id='operations-admin-b', role='admin', org='B')


def identities():
    return DemoIdentityProvider({user['id']: dict(password='synthetic-only', role=user['role'], org=user['org'])
                                 for user in (ADMIN, USER, FOREIGN)})


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'operations.sqlite'
        self.identities = identities()
        self.service = OperationsService(self.path, self.identities)
        self.seed = self.service.seed_demo(ADMIN)

    def rows(self, version=2):
        return self.service.get_version(ADMIN, DEMO_REPORT_ID, version)['rows']

    def test_explicit_seed_is_idempotent_and_clearly_synthetic(self):
        self.assertTrue(self.seed['synthetic'])
        self.assertEqual(self.seed['report_id'], DEMO_REPORT_ID)
        before = self.service.list_reports(ADMIN)
        self.service.seed_demo(ADMIN)
        self.assertEqual(before, self.service.list_reports(ADMIN))
        self.assertEqual(len(before), 4)
        qsl = next(report for report in before if report['id'] == 'qsl-workspace')
        self.assertEqual(qsl['latest_version'], 0)
        self.assertEqual(qsl['row_count'], 0)
        self.assertEqual(sum(self.service.usage_summary(ADMIN)['totals'].values()), 0)
        self.assertEqual(self.service.get_version(ADMIN, DEMO_REPORT_ID, 1)['definition']['provenance'],
                         'synthetic-fixture')

    def test_no_constructor_fixtures_or_production_seed(self):
        separate = Path(self.temporary.name) / 'blank.sqlite'
        service = OperationsService(separate, self.identities)
        self.assertEqual(service.list_reports(ADMIN), [])
        self.identities.is_demo = False
        with self.assertRaises(AccessDenied):
            service.seed_demo(ADMIN)
        self.assertEqual(service.list_reports(ADMIN), [])
        with self.assertRaises(AccessDenied):
            self.service.quality_fixture(ADMIN, DEMO_REPORT_ID)

    def test_all_public_actions_require_current_valid_identity(self):
        operations = [
            lambda user: self.service.list_reports(user),
            lambda user: self.service.register_report(user, dict(name='New', source_key='synthetic', columns=['value'])),
            lambda user: self.service.save_version(user, DEMO_REPORT_ID, {}, []),
            lambda user: self.service.get_version(user, DEMO_REPORT_ID, 1),
            lambda user: self.service.source_record(user, DEMO_REPORT_ID, 1, 'monthly-row-001'),
            lambda user: self.service.version_diff(user, DEMO_REPORT_ID, 1, 2),
            lambda user: self.service.list_dependencies(user),
            lambda user: self.service.impact(user, 'source', 'synthetic-monthly'),
            lambda user: self.service.export_impact(user, 'source', 'synthetic-monthly'),
            lambda user: self.service.search_catalog(user, 'monthly'),
            lambda user: self.service.check_quality(user, DEMO_REPORT_ID, []),
            lambda user: self.service.simulate_send(user, DEMO_REPORT_ID, [], ['test@example.invalid']),
            lambda user: self.service.list_simulations(user, DEMO_REPORT_ID),
            lambda user: self.service.record_usage(user, DEMO_REPORT_ID, 'report_view'),
            lambda user: self.service.usage_summary(user),
            lambda user: self.service.quality_fixture(user, DEMO_REPORT_ID),
            lambda user: self.service.seed_demo(user),
        ]
        for operation in operations:
            for invalid in (None, {}, dict(ADMIN, org='B'), dict(ADMIN, id='unknown'),
                            dict(ADMIN, role='administrator'), dict(ADMIN, id='x\x00')):
                with self.subTest(operation=operation, invalid=invalid):
                    with self.assertRaises(AccessDenied):
                        operation(invalid)

    def test_role_claims_and_revocation_are_reloaded_on_every_action(self):
        self.assertEqual(len(self.service.list_reports(USER)), 4)
        with self.assertRaises(AccessDenied):
            self.service.list_reports(dict(USER, role='admin'))
        self.identities.user_db[ADMIN['id']]['role'] = 'user'
        with self.assertRaises(AccessDenied):
            self.service.seed_demo(ADMIN)
        current = self.identities.get_user(ADMIN['id'])
        with self.assertRaises(AccessDenied):
            self.service.seed_demo(current)
        del self.identities.user_db[USER['id']]
        with self.assertRaises(AccessDenied):
            self.service.list_reports(USER)

    def test_users_cannot_mutate_catalog_versions_or_mock_mail(self):
        for action in (
            lambda: self.service.register_report(USER, dict(name='New', source_key='synthetic', columns=['value'])),
            lambda: self.service.save_version(USER, DEMO_REPORT_ID, {}, []),
            lambda: self.service.simulate_send(USER, DEMO_REPORT_ID, [], ['test@example.invalid']),
            lambda: self.service.seed_demo(USER),
        ):
            with self.assertRaises(AccessDenied):
                action()

    def test_cross_organization_report_lineage_quality_and_counters_are_hidden(self):
        self.assertEqual(self.service.list_reports(FOREIGN), [])
        self.assertEqual(self.service.search_catalog(FOREIGN, 'monthly')['matches'], [])
        self.assertEqual(self.service.list_dependencies(FOREIGN), dict(nodes=[], edges=[]))
        for action in (
            lambda: self.service.get_version(FOREIGN, DEMO_REPORT_ID, 1),
            lambda: self.service.save_version(FOREIGN, DEMO_REPORT_ID, {}, []),
            lambda: self.service.version_diff(FOREIGN, DEMO_REPORT_ID, 1, 2),
            lambda: self.service.source_record(FOREIGN, DEMO_REPORT_ID, 1, 'monthly-row-001'),
            lambda: self.service.impact(FOREIGN, 'source', 'synthetic-monthly'),
            lambda: self.service.impact(FOREIGN, 'report', DEMO_REPORT_ID),
            lambda: self.service.check_quality(FOREIGN, DEMO_REPORT_ID, []),
            lambda: self.service.simulate_send(FOREIGN, DEMO_REPORT_ID, [], ['test@example.invalid']),
            lambda: self.service.list_simulations(FOREIGN, DEMO_REPORT_ID),
            lambda: self.service.record_usage(FOREIGN, DEMO_REPORT_ID, 'report_view'),
            lambda: self.service.quality_fixture(FOREIGN, DEMO_REPORT_ID),
        ):
            with self.assertRaises(NotFound):
                action()
        self.service.record_usage(USER, DEMO_REPORT_ID, 'report_view')
        self.assertEqual(self.service.usage_summary(FOREIGN)['totals']['report_view'], 0)
        self.service.seed_demo(FOREIGN)
        self.service.record_usage(FOREIGN, DEMO_REPORT_ID, 'report_view')
        self.assertEqual(self.service.usage_summary(USER)['totals']['report_view'], 1)
        self.assertEqual(self.service.usage_summary(FOREIGN)['totals']['report_view'], 1)

    def test_dependency_graph_lists_source_field_report_export_api_mail(self):
        graph = self.service.list_dependencies(USER)
        kinds = {node['kind'] for node in graph['nodes']}
        self.assertEqual(kinds, {'source', 'field', 'report', 'export', 'api', 'mail'})
        self.assertIn(dict(source='report:monthly-performance', target='report:executive-summary'), graph['edges'])
        self.assertIn(dict(source='field:synthetic-monthly:profit', target='report:monthly-performance'), graph['edges'])
        self.assertEqual(graph, self.service.dependency_graph(USER))

    def test_source_and_field_impact_are_transitive_and_preview_only(self):
        result = self.service.impact(USER, 'source', 'synthetic-monthly')
        self.assertEqual(result['counts'], dict(report=3, export=3, api=2, mail=2))
        self.assertTrue(result['preview_only'])
        field = self.service.impact(USER, 'source', 'synthetic-monthly', field='profit')
        self.assertEqual(field['counts'], dict(report=2, export=2, api=1, mail=2))
        self.assertEqual(field, self.service.impact(USER, 'field', 'synthetic-monthly', field='profit'))
        report = self.service.impact(USER, 'report', DEMO_REPORT_ID)
        self.assertEqual(report['counts'], dict(report=1, export=2, api=1, mail=2))
        self.assertEqual(self.service.impact(USER, 'mail', DEMO_REPORT_ID)['impacted'], [])
        self.assertEqual(self.service.list_simulations(USER, DEMO_REPORT_ID), [])
        self.assertEqual(self.service.mail.messages, [])

    def test_catalog_registration_requires_existing_same_org_dependencies(self):
        payload = dict(id='derived-example', name='Derived', source_key='synthetic-custom',
                       columns=['revenue'], depends_on=[DEMO_REPORT_ID])
        with self.assertRaises(NotFound):
            self.service.register_report(FOREIGN, payload)
        report = self.service.register_report(ADMIN, payload)
        self.assertEqual(report['latest_version'], 0)
        self.assertIn('derived-example', [node['resource_id'] for node in
                                         self.service.impact(USER, 'report', DEMO_REPORT_ID)['groups']['report']])
        with self.assertRaises(Conflict):
            self.service.register_report(ADMIN, payload)

    def test_impact_csv_is_bounded_and_formula_safe(self):
        self.service.register_report(ADMIN, dict(id='csv-report', name='=SUM(A1:A2)', source_key='csv-source',
                                                columns=['value'], depends_on=[DEMO_REPORT_ID]))
        rows = list(csv.DictReader(io.StringIO(self.service.export_impact(USER, 'report', DEMO_REPORT_ID))))
        row = next(row for row in rows if row['resource_id'] == 'csv-report' and row['kind'] == 'report')
        self.assertEqual(row['label'], "'=SUM(A1:A2)")
        self.assertEqual(set(row), {'kind', 'resource_id', 'label', 'depth'})

    def test_versions_diff_values_configuration_and_source_record_ids(self):
        result = self.service.version_diff(USER, DEMO_REPORT_ID, 1, 2)
        self.assertEqual(result['summary'], dict(added=1, removed=1, changed=1, definition_changes=1))
        self.assertEqual(result['source_record_ids'], ['monthly-row-002', 'monthly-row-003', 'monthly-row-007'])
        self.assertEqual(result['added'][0]['record_id'], 'monthly-row-007')
        self.assertEqual(result['removed'][0]['record_id'], 'monthly-row-003')
        changed = result['changed'][0]
        self.assertEqual(changed['changed_fields'], ['profit', 'revenue'])
        self.assertEqual(changed['after']['revenue'] - changed['before']['revenue'], 2500)
        self.assertEqual(result['definition_changes'], [dict(field='limit', before=100, after=200)])
        record = self.service.source_record(USER, DEMO_REPORT_ID, 2, changed['record_id'])
        self.assertEqual(record['row'], changed['after'])
        with self.assertRaises(NotFound):
            self.service.source_record(USER, DEMO_REPORT_ID, 2, 'monthly-row-003')
        self.assertEqual(self.service.source_record(USER, DEMO_REPORT_ID, 1, 'monthly-row-003')['record_id'],
                         'monthly-row-003')

    def test_reversed_and_same_version_diffs_are_consistent(self):
        reverse = self.service.version_diff(USER, DEMO_REPORT_ID, 2, 1)
        self.assertEqual(reverse['added'][0]['record_id'], 'monthly-row-003')
        self.assertEqual(reverse['removed'][0]['record_id'], 'monthly-row-007')
        same = self.service.version_diff(USER, DEMO_REPORT_ID, 1, 1)
        self.assertEqual(same['source_record_ids'], [])
        self.assertEqual(same['summary'], dict(added=0, removed=0, changed=0, definition_changes=0))

    def test_version_diff_numeric_totals_and_department_contributions_are_exact(self):
        diff = self.service.version_diff(USER, DEMO_REPORT_ID, 1, 2)
        totals = {item['field']: item for item in diff['numeric_totals']}
        self.assertEqual(totals['revenue']['before'], 621000)
        self.assertEqual(totals['revenue']['after'], 627500)
        self.assertEqual(totals['revenue']['delta'], 6500)
        self.assertEqual(totals['cost']['delta'], 2000)
        self.assertEqual(totals['profit']['delta'], 4500)
        departments = {item['value']: item for item in diff['group_contributions'] if item['group_by'] == 'department'}
        sales = {item['field']: item for item in departments['Demo Sales']['numeric_changes']}
        operations = {item['field']: item for item in departments['Demo Operations']['numeric_changes']}
        self.assertEqual(sales['revenue']['delta'], 4000)
        self.assertEqual(operations['revenue']['delta'], 2500)
        self.assertEqual(departments['Demo Sales']['source_record_ids'], ['monthly-row-003', 'monthly-row-007'])
        self.assertEqual(departments['Demo Operations']['source_record_ids'], ['monthly-row-002'])
        self.assertTrue(diff['arithmetic_only'])
        self.assertTrue(all(not item['truncated'] for item in diff['group_limits']))

    def test_supplier_material_contributions_trace_exact_record_deltas(self):
        identifier = 'supplier-material-report'
        self.service.register_report(ADMIN, dict(id=identifier, name='Supplier material', source_key='synthetic-material',
                                                columns=['Supplier', 'Material', 'amount'], key_fields=['Supplier', 'Material']))
        before = [dict(record_id='s1', Supplier='Supplier A', Material='Steel', amount=10),
                  dict(record_id='s2', Supplier='Supplier B', Material='Plastic', amount=20)]
        after = [dict(before[0], amount=15), before[1],
                 dict(record_id='s3', Supplier='Supplier A', Material='Plastic', amount=7)]
        self.service.save_version(ADMIN, identifier, {}, before, expected_version=0)
        self.service.save_version(ADMIN, identifier, {}, after, expected_version=1)
        result = self.service.version_diff(USER, identifier, 1, 2)
        self.assertEqual(result['numeric_totals'][0]['delta'], 12)
        groups = {(item['group_by'], item['value']): item for item in result['group_contributions']}
        self.assertEqual(groups['Supplier', 'Supplier A']['numeric_changes'][0]['delta'], 12)
        self.assertEqual(groups['Material', 'Steel']['numeric_changes'][0]['delta'], 5)
        self.assertEqual(groups['Material', 'Plastic']['numeric_changes'][0]['delta'], 7)
        self.assertEqual(groups['Supplier', 'Supplier A']['source_record_ids'], ['s1', 's3'])

    def test_numeric_diff_retains_decimal_arithmetic_and_coverage_counts(self):
        identifier = 'decimal-report'
        self.service.register_report(ADMIN, dict(id=identifier, name='Decimal', source_key='synthetic-decimal',
                                                columns=['category', 'amount'], key_fields=['category']))
        self.service.save_version(ADMIN, identifier, {}, [dict(record_id='d1', category='A', amount=.1),
                                                           dict(record_id='d2', category='B', amount=True)])
        self.service.save_version(ADMIN, identifier, {}, [dict(record_id='d1', category='A', amount=.3),
                                                           dict(record_id='d2', category='B')])
        change = self.service.version_diff(USER, identifier, 1, 2)['numeric_totals'][0]
        self.assertEqual(change['delta'], .2)
        self.assertEqual(change['delta_exact'], '0.2')
        self.assertEqual(change['before_numeric_count'], 1)
        self.assertEqual(change['before_nonnumeric_count'], 1)
        self.assertEqual(change['after_missing_count'], 1)

    def test_group_contribution_output_is_bounded_and_discloses_truncation(self):
        identifier = 'grouped-report'
        self.service.register_report(ADMIN, dict(id=identifier, name='Many groups', source_key='synthetic-groups',
                                                columns=['category', 'amount'], key_fields=['category']))
        rows = [dict(record_id='g{:03d}'.format(index), category='Category {:03d}'.format(index), amount=index)
                for index in range(70)]
        self.service.save_version(ADMIN, identifier, {}, [])
        self.service.save_version(ADMIN, identifier, {}, rows)
        result = self.service.version_diff(USER, identifier, 1, 2)
        self.assertEqual(len(result['group_contributions']), 50)
        self.assertEqual(result['group_limits'], [dict(field='category', groups_total=70, groups_shown=50, truncated=True)])
        self.assertEqual(result['group_contributions'][0]['value'], 'Category 069')

    def test_source_snapshot_preserves_whitespace_for_true_value_diffs(self):
        identifier = 'whitespace-report'
        self.service.register_report(ADMIN, dict(id=identifier, name='Whitespace', source_key='synthetic-space', columns=['value']))
        self.service.save_version(ADMIN, identifier, {}, [dict(record_id='space1', value='  source value  ')])
        self.service.save_version(ADMIN, identifier, {}, [dict(record_id='space1', value='source value')])
        change = self.service.version_diff(USER, identifier, 1, 2)['changed'][0]
        self.assertEqual(change['before']['value'], '  source value  ')
        self.assertEqual(change['after']['value'], 'source value')

    def test_versions_are_immutable_detached_and_optimistic(self):
        first = self.service.get_version(USER, DEMO_REPORT_ID, 1)
        first['rows'][0]['revenue'] = -1
        self.assertNotEqual(self.service.get_version(USER, DEMO_REPORT_ID, 1)['rows'][0]['revenue'], -1)
        third = self.service.save_version(ADMIN, DEMO_REPORT_ID, dict(limit=10), self.rows(), expected_version=2)
        self.assertEqual(third['version'], 3)
        with self.assertRaises(Conflict):
            self.service.save_version(ADMIN, DEMO_REPORT_ID, {}, [], expected_version=2)
        self.assertEqual(self.service.get_version(USER, DEMO_REPORT_ID, 1)['definition']['limit'], 100)

    def test_concurrent_version_writers_have_one_winner(self):
        rows = self.rows()
        def save(_):
            try:
                return self.service.save_version(ADMIN, DEMO_REPORT_ID, {}, rows, expected_version=2)['version']
            except Conflict:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(save, range(2)))
        self.assertCountEqual(outcomes, [3, 'conflict'])

    def test_duplicate_source_record_ids_cannot_enter_version_lineage(self):
        rows = self.rows()
        rows[1]['record_id'] = rows[0]['record_id']
        with self.assertRaises(ValidationError):
            self.service.save_version(ADMIN, DEMO_REPORT_ID, {}, rows, expected_version=2)
        self.assertEqual(next(report for report in self.service.list_reports(USER) if report['id'] == DEMO_REPORT_ID)['latest_version'], 2)

    def test_catalog_reuse_filter_clone_suggestions_are_deterministic(self):
        request = dict(text='Monthly performance', source_key='synthetic-monthly',
                       columns=['period', 'department', 'revenue', 'cost', 'profit'])
        first = self.service.search_catalog(USER, request)
        self.assertEqual(first, self.service.search_catalog(USER, request))
        primary = next(item for item in first['matches'] if item['report_id'] == DEMO_REPORT_ID)
        self.assertEqual(primary['suggestion'], 'reuse')
        self.assertEqual(first['algorithm'], 'deterministic-overlap-v1')
        narrower = dict(request, columns=['period', 'revenue'],
                        filters=[dict(field='department', op='eq', value='Demo Sales')])
        primary = next(item for item in self.service.search_catalog(USER, narrower)['matches'] if item['report_id'] == DEMO_REPORT_ID)
        self.assertEqual(primary['suggestion'], 'filter')
        primary = next(item for item in self.service.search_catalog(USER, dict(request, columns=['new_field']))['matches']
                       if item['report_id'] == DEMO_REPORT_ID)
        self.assertEqual(primary['suggestion'], 'clone')
        primary = next(item for item in self.service.search_catalog(USER, dict(request, filters=[dict(field='unknown', op='eq', value='x')]))['matches']
                       if item['report_id'] == DEMO_REPORT_ID)
        self.assertEqual(primary['suggestion'], 'clone')
        chinese = self.service.search_catalog(USER, '供應商 材料')
        self.assertEqual(chinese['matches'][0]['report_id'], 'qsl-workspace')

    def test_similarity_does_not_suggest_removing_existing_filters_as_filter(self):
        self.service.save_version(ADMIN, DEMO_REPORT_ID,
                                  dict(filters=[dict(field='department', op='eq', value='Demo Sales')]), self.rows())
        result = self.service.search_catalog(USER, dict(source_key='synthetic-monthly', filters=[]))
        primary = next(item for item in result['matches'] if item['report_id'] == DEMO_REPORT_ID)
        self.assertEqual(primary['suggestion'], 'clone')

    def test_catalog_requests_are_not_saved_or_executed(self):
        sentinel = 'unretained-request-unique-987654'
        self.service.search_catalog(USER, sentinel)
        self.assertNotIn(sentinel.encode(), self.path.read_bytes())
        with closing(sqlite3.connect(str(self.path))) as connection, connection:
            columns = [row[1] for row in connection.execute('PRAGMA table_info(operations_usage)')]
            self.assertEqual(columns, ['org', 'report_id', 'event', 'count', 'last_event_at'])

    def test_quality_clean_passes_without_mutating_baseline(self):
        fixture = self.service.quality_fixture(USER, DEMO_REPORT_ID, 'clean')
        self.assertFalse(fixture['update_failed'])
        result = self.service.check_quality(USER, DEMO_REPORT_ID, fixture['rows'])
        self.assertTrue(result['passed'])
        self.assertEqual(result['issues'], [])
        self.assertEqual((result['row_count'], result['baseline_count']), (6, 6))
        fixture['rows'][0]['revenue'] = -1
        self.assertNotEqual(self.service.quality_fixture(USER, DEMO_REPORT_ID, 'clean')['rows'][0]['revenue'], -1)

    def test_bad_fixture_blocks_all_four_quality_conditions_and_mock_mail(self):
        fixture = self.service.quality_fixture(USER, DEMO_REPORT_ID, 'bad')
        result = self.service.check_quality(USER, DEMO_REPORT_ID, fixture['rows'], fixture['update_failed'])
        self.assertFalse(result['passed'])
        self.assertEqual([issue['code'] for issue in result['issues']],
                         ['update_failed', 'missing_columns', 'count_drop', 'duplicate_keys'])
        sent = self.service.simulate_send(ADMIN, DEMO_REPORT_ID, fixture['rows'], ['test@example.invalid'], fixture['update_failed'])
        self.assertEqual(sent['status'], 'blocked')
        self.assertFalse(sent['real_delivery'])
        self.assertEqual(self.service.mail.messages, [])
        self.assertEqual(self.service.list_simulations(USER, DEMO_REPORT_ID)[0]['status'], 'blocked')
        self.assertEqual(self.service.usage_summary(USER)['totals']['quality_block'], 1)

    def test_each_quality_failure_independently_blocks_mock_send(self):
        clean = self.rows()
        missing = copy.deepcopy(clean)
        missing[0].pop('profit')
        duplicate = copy.deepcopy(clean)
        for field in ('period', 'department'):
            duplicate[1][field] = duplicate[0][field]
        cases = [(clean, True, 'update_failed'), (missing, False, 'missing_columns'),
                 (clean[:2], False, 'count_drop'), (duplicate, False, 'duplicate_keys')]
        for rows, failed, code in cases:
            result = self.service.simulate_send(ADMIN, DEMO_REPORT_ID, rows, ['test@example.invalid'], failed)
            self.assertEqual(result['status'], 'blocked')
            self.assertIn(code, [issue['code'] for issue in result['quality']['issues']])
        self.assertEqual(self.service.mail.messages, [])

    def test_gate_pass_sends_only_to_in_memory_sink_and_persists_safe_metadata(self):
        result = self.service.simulate_send(ADMIN, DEMO_REPORT_ID, self.rows(), ['test@example.invalid'])
        self.assertEqual(result['status'], 'simulated')
        self.assertFalse(result['real_delivery'])
        self.assertEqual(len(self.service.mail.messages), 1)
        self.assertEqual(self.service.mail.messages[0]['recipients'], ['test@example.invalid'])
        self.assertEqual(self.service.usage_summary(USER)['totals']['mock_send'], 1)
        restarted = OperationsService(self.path, self.identities)
        self.assertEqual(restarted.mail.messages, [])
        self.assertEqual(restarted.list_simulations(USER, DEMO_REPORT_ID)[0]['status'], 'simulated')
        self.assertNotIn(b'test@example.invalid', self.path.read_bytes())

    def test_mock_mail_channel_must_be_configured(self):
        with self.assertRaises(AccessDenied):
            self.service.simulate_send(ADMIN, 'department-cost', [], ['test@example.invalid'])

    def test_count_drop_threshold_boundary_is_inclusive(self):
        rows = self.rows()[:4]
        self.service.save_version(ADMIN, DEMO_REPORT_ID, {}, rows)
        self.assertTrue(self.service.check_quality(USER, DEMO_REPORT_ID, rows[:3])['passed'])
        self.assertFalse(self.service.check_quality(USER, DEMO_REPORT_ID, rows[:2])['passed'])
        self.assertFalse(self.service.check_quality(USER, DEMO_REPORT_ID, [])['passed'])

    def test_aggregate_usage_counts_actual_success_and_no_individuals(self):
        self.service.get_version(USER, DEMO_REPORT_ID, 2)
        self.service.version_diff(USER, DEMO_REPORT_ID, 1, 2)
        self.service.source_record(USER, DEMO_REPORT_ID, 2, 'monthly-row-002')
        self.service.record_usage(USER, 'qsl-workspace', 'selfservice_save')
        self.service.search_catalog(USER, 'monthly')
        totals = self.service.usage_summary(USER)['totals']
        for event in ('report_view', 'version_diff', 'source_drilldown', 'selfservice_save', 'catalog_search', 'self_service'):
            self.assertEqual(totals[event], 2 if event == 'report_view' else 1)
        for action in (
            lambda: self.service.get_version(USER, DEMO_REPORT_ID, 99),
            lambda: self.service.version_diff(USER, DEMO_REPORT_ID, 1, 99),
            lambda: self.service.source_record(USER, DEMO_REPORT_ID, 2, 'missing'),
        ):
            with self.assertRaises(NotFound):
                action()
        self.assertEqual(self.service.usage_summary(USER)['totals'], totals)
        contents = self.path.read_bytes()
        self.assertNotIn(ADMIN['id'].encode(), contents)
        self.assertNotIn(USER['id'].encode(), contents)
        self.assertNotIn('synthetic-only'.encode(), contents)
        self.assertIn('no individual tracking', self.service.usage_summary(USER)['privacy'].lower())

    def test_usage_is_atomic_under_concurrent_views(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.service.record_usage(USER, DEMO_REPORT_ID, 'report_view'), range(20)))
        self.assertEqual(self.service.usage_summary(USER)['totals']['report_view'], 20)

    def test_last_view_is_aggregate_and_no_events_mean_unobserved(self):
        summary = self.service.usage_summary(USER)
        report = next(item for item in summary['reports'] if item['report_id'] == DEMO_REPORT_ID)
        self.assertIsNone(report['last_view_at'])
        self.assertEqual(report['view_observation'], 'unobserved')
        self.assertEqual(report['view_observation_label'], '未觀測')
        with patch('reporting_workspace.operations.time.time', return_value=1790960000.0):
            self.service.record_usage(USER, DEMO_REPORT_ID, 'report_view')
        with patch('reporting_workspace.operations.time.time', return_value=1790959999.0):
            self.service.record_usage(USER, DEMO_REPORT_ID, 'report_view')
        report = next(item for item in self.service.usage_summary(USER)['reports'] if item['report_id'] == DEMO_REPORT_ID)
        self.assertEqual(report['last_view_at'], 1790960000.0)
        self.assertEqual(report['last_event_at']['report_view'], 1790960000.0)
        self.assertEqual(report['view_observation'], 'observed')
        with closing(sqlite3.connect(str(self.path))) as connection, connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM operations_usage WHERE event=?', ('report_view',)).fetchone()[0], 1)

    def test_only_fixed_usage_events_are_accepted(self):
        for invalid in ('login', USER['id'], 'SQL SELECT *', None, [], {}, 'quality_block'):
            with self.assertRaises(ValidationError):
                self.service.record_usage(USER, DEMO_REPORT_ID, invalid)
        for event in USAGE_EVENTS:
            self.service.record_usage(USER, DEMO_REPORT_ID, event)

    def test_malformed_catalog_definition_and_rows_are_rejected(self):
        base = dict(id='new-report', name='New', source_key='synthetic-custom', columns=['value'])
        for changes in (
            dict(source_key='https://example.com'), dict(sql='DROP TABLE operations_reports'),
            dict(columns=['value', 'value']), dict(columns=['record_id']), dict(channels=['smtp']),
            dict(key_fields=['unknown']), dict(max_count_drop=True), dict(max_count_drop=float('nan')),
            dict(max_count_drop=10 ** 1000), dict(tags=['x', 'X']), dict(depends_on=['new-report']),
            dict(name='x\x00'), dict(name='x' * 121), dict(columns=[[]]),
        ):
            with self.subTest(changes=changes):
                with self.assertRaises((ValidationError, NotFound)):
                    self.service.register_report(ADMIN, dict(base, **changes))
        invalid_definitions = [dict(sql='select *'), dict(source='https://example.com'), dict(columns=['unknown']),
                               dict(limit=True), dict(limit=MAX_ROWS + 1), dict(aggregate='execute'),
                               dict(filters=[dict(field='profit', op='python', value='eval')]),
                               dict(filters=[dict(field='profit', op='contains', value=1)])]
        for definition in invalid_definitions:
            with self.assertRaises(ValidationError):
                self.service.save_version(ADMIN, DEMO_REPORT_ID, definition, [])
        invalid_rows = [None, {}, [{}], [{'record_id': '../../etc'}],
                        [{'record_id': 'safe', 'value': {'nested': 'no'}}],
                        [{'record_id': 'safe', 'value': float('inf')}],
                        [{'record_id': 'safe', 'value': 10 ** 1000}],
                        [{'record_id': 'safe', 'value': 'x' * 501}],
                        [{'record_id': 'safe'}] * (MAX_ROWS + 1)]
        for rows in invalid_rows:
            with self.assertRaises(ValidationError):
                self.service.check_quality(USER, DEMO_REPORT_ID, rows)

    def test_malformed_lookup_impact_request_and_delivery_inputs_are_rejected(self):
        for identifier in ('../report', "' OR 1=1--", 'https://example.com', '', None, []):
            with self.assertRaises(NotFound):
                self.service.get_version(USER, identifier, 1)
            with self.assertRaises(ValidationError):
                self.service.impact(USER, 'source', identifier)
            with self.assertRaises(NotFound):
                self.service.source_record(USER, DEMO_REPORT_ID, 1, identifier)
        for request in (dict(sql='select *'), dict(text='x' * 1001), dict(columns=['x', 'x']),
                        dict(source_key='https://example.com'), dict(filters=[{}]), []):
            with self.assertRaises(ValidationError):
                self.service.search_catalog(USER, request)
        for recipients in (['real@example.com'], ['test@example.invalid', 'TEST@example.invalid'],
                           ['x@example.invalid\nBcc:someone'], [], 'test@example.invalid', [None]):
            with self.assertRaises(ValidationError):
                self.service.simulate_send(ADMIN, DEMO_REPORT_ID, [], recipients)
        with self.assertRaises(ValidationError):
            self.service.check_quality(USER, DEMO_REPORT_ID, [], update_failed='false')
        with self.assertRaises(ValidationError):
            self.service.impact(USER, 'sql', 'synthetic-monthly')
        with self.assertRaises(ValidationError):
            self.service.list_simulations(USER, DEMO_REPORT_ID, limit=True)
        for version in (0, True, 1.5, '1', 101, None):
            with self.assertRaises(ValidationError):
                self.service.get_version(USER, DEMO_REPORT_ID, version)
        with self.assertRaises(ValidationError):
            self.service.quality_fixture(USER, DEMO_REPORT_ID, 'unknown')
        with self.assertRaises(AccessDenied):
            self.service.quality_fixture(USER, 'qsl-workspace')

    def test_durable_restart_keeps_versions_usage_and_separate_storage(self):
        self.service.record_usage(USER, DEMO_REPORT_ID, 'report_view')
        separate = Path(self.temporary.name) / 'state.sqlite'
        separate.write_bytes(b'untouched-primary-state-file')
        restarted = OperationsService(self.path, self.identities)
        self.assertEqual(restarted.version_diff(USER, DEMO_REPORT_ID, 1, 2)['summary']['changed'], 1)
        self.assertEqual(restarted.usage_summary(USER)['totals']['report_view'], 2)
        self.assertEqual(separate.read_bytes(), b'untouched-primary-state-file')

    def test_storage_schema_fail_closed_and_missing_database_not_recreated(self):
        with closing(sqlite3.connect(str(self.path))) as connection, connection:
            connection.execute('PRAGMA user_version=2')
        with self.assertRaises(StateUnavailable):
            OperationsService(self.path, self.identities)
        with self.assertRaises(StateUnavailable):
            self.service.list_reports(USER)
        with closing(sqlite3.connect(str(self.path))) as connection, connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 2)
        self.path.unlink()
        with self.assertRaises(StateUnavailable):
            self.service.list_reports(USER)
        self.assertFalse(self.path.exists())

    def test_incompatible_unversioned_database_is_preserved(self):
        path = Path(self.temporary.name) / 'unrecognized.sqlite'
        with closing(sqlite3.connect(str(path))) as connection, connection:
            connection.execute('CREATE TABLE existing(value TEXT)')
            connection.execute("INSERT INTO existing VALUES('keep')")
        with self.assertRaises(StateUnavailable):
            OperationsService(path, self.identities)
        with closing(sqlite3.connect(str(path))) as connection, connection:
            self.assertEqual(connection.execute('SELECT value FROM existing').fetchone()[0], 'keep')
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
