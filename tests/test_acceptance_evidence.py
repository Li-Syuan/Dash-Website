"""Pure-data rejection cases; no app, subprocess, benchmark or external service."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tools.acceptance_evidence import (
    BROWSER_CASES, LATENCY_SCENARIOS, validate_browser_cases,
    validate_coverage, validate_performance,
)


ROOT = Path(__file__).resolve().parents[1]
_DEFAULT = object()


def read(relative):
    return json.loads((ROOT / relative).read_text(encoding='utf-8'))


class PerformanceEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = {}
        for family, before_name, after_name, comparison_name in (
                ('xlsx', 'v11-baseline-large-xlsx.json', 'v11-candidate-large-xlsx.json', 'v11-large-xlsx-comparison.json'),
                ('latency', 'baseline-v9.json', 'candidate-v10.json', 'comparison-v10.json')):
            before, after, comparison = [read('benchmarks/evidence/' + name)
                                         for name in (before_name, after_name, comparison_name)]
            before['label'], after['label'] = 'before', 'after'
            comparison['before_file'], comparison['after_file'] = family + '_before.json', family + '_after.json'
            cls.fixtures[family] = before, after, comparison

    def values(self, family):
        return deepcopy(self.fixtures[family])

    def validate(self, family, payload=_DEFAULT, action='after', before=None, after=None, budgets=None):
        old, new, comparison = self.values(family)
        before, after = before or old, after or new
        sources = {'before': old['source_hashes'], 'after': new['source_hashes']}
        if action != 'compare':
            sources = sources[action]
        return validate_performance(payload if payload is not _DEFAULT else (comparison if action == 'compare' else after),
                                    family, action, 'full', sources, new['harness_sha256'],
                                    before=before, after=after, budgets=budgets)

    def test_existing_real_measurements_and_comparisons_validate(self):
        for family in self.fixtures:
            with self.subTest(family=family):
                self.assertEqual(self.validate(family)['status'], 'PASSED')
                result = self.validate(family, action='compare', budgets={
                    'max_wall_regression_percent': 1000, 'max_rss_regression_percent': 1000})
                self.assertEqual(result['status'], 'PASSED', result)

    def test_comparison_without_budgets_cannot_pass(self):
        for family in self.fixtures:
            self.assertEqual(self.validate(family, action='compare')['status'], 'INCOMPLETE')
        self.assertEqual(self.validate('xlsx', action='compare', budgets={
            'max_wall_regression_percent': 10})['status'], 'INCOMPLETE')

    def test_empty_short_and_extra_xlsx_sample_arrays_are_invalid(self):
        for key in ('samples', 'warmups'):
            for mode in ('empty', 'short', 'extra'):
                _, data, _ = self.values('xlsx')
                values = data['datasets'][0][key]
                data['datasets'][0][key] = [] if mode == 'empty' else values[:-1] if mode == 'short' else values + values[:1]
                with self.subTest(key=key, mode=mode):
                    self.assertEqual(self.validate('xlsx', data)['status'], 'INVALID')

    def test_missing_latency_scenario_or_sample_cannot_be_hidden_by_summary(self):
        for mode in ('scenario', 'samples', 'setup'):
            _, data, _ = self.values('latency')
            dataset = data['datasets'][0]
            if mode == 'scenario':
                dataset['measurements'].pop('callback_export_xlsx')
            elif mode == 'samples':
                dataset['measurements']['service_query_page']['samples_ms'] = []
            else:
                dataset['setup_import']['samples_ms'].pop()
            self.assertEqual(self.validate('latency', data)['status'], 'INVALID')

    def test_changed_raw_samples_require_recomputed_summary(self):
        for family in self.fixtures:
            _, data, _ = self.values(family)
            dataset = data['datasets'][0]
            if family == 'xlsx':
                for sample in dataset['samples']:
                    sample['wall_ms'] *= 2
            else:
                dataset['measurements']['service_query_page']['samples_ms'] = [999999] * data['samples']
            self.assertEqual(self.validate(family, data)['status'], 'INVALID')

    def test_claimed_xlsx_validation_must_cover_rows_types_and_fixture(self):
        mutations = (
            ('rows', 1), ('normalized_rows_sha256', '0' * 64), ('sheet_names', ['wrong']),
            ('cell_types', ['s'] * 9), ('literal_formula_error_unicode_date_like_text', 'unrun'),
        )
        for field, value in mutations:
            _, data, _ = self.values('xlsx')
            data['datasets'][0]['samples'][0]['validation'][field] = value
            with self.subTest(field=field):
                self.assertEqual(self.validate('xlsx', data)['status'], 'INVALID')

    def test_default_cap_check_cannot_be_omitted_or_relabelled(self):
        for value in (None, {}, {'status': 'passed', 'default_cap': 100000, 'wall_ms': 1}):
            _, data, _ = self.values('xlsx')
            data['datasets'][0]['default_cap_check'] = value
            self.assertEqual(self.validate('xlsx', data)['status'], 'INVALID')

    def test_source_inventory_harness_and_dataset_plan_are_exact(self):
        for mode in ('source_removed', 'source_changed', 'harness', 'dataset_removed', 'dataset_reordered', 'sample_count'):
            _, data, _ = self.values('xlsx')
            if mode == 'source_removed':
                data['source_hashes'].pop(next(iter(data['source_hashes'])))
            elif mode == 'source_changed':
                data['source_hashes'][next(iter(data['source_hashes']))] = '0' * 64
            elif mode == 'harness':
                data['harness_sha256'] = '0' * 64
            elif mode == 'dataset_removed':
                data['datasets'].pop()
            elif mode == 'dataset_reordered':
                data['datasets'].reverse()
            else:
                data['samples'] = True
            self.assertEqual(self.validate('xlsx', data)['status'], 'INVALID', mode)

    def test_nonfinite_negative_zero_and_boolean_timings_are_rejected(self):
        for value in (float('nan'), float('inf'), -1, 0, True, '1'):
            _, data, _ = self.values('xlsx')
            data['datasets'][0]['samples'][0]['wall_ms'] = value
            self.assertEqual(self.validate('xlsx', data)['status'], 'INVALID')

    def test_comparison_cannot_remove_duplicate_or_forge_metric(self):
        for family in self.fixtures:
            for mode in ('remove', 'duplicate', 'delta', 'before_metric'):
                _, _, data = self.values(family)
                rows = data['comparisons']
                if mode == 'remove':
                    rows.pop()
                elif mode == 'duplicate':
                    rows[1] = deepcopy(rows[0])
                else:
                    metric = 'median_wall_ms' if family == 'xlsx' else 'median_ms'
                    rows[0][metric + '_reduction_percent' if mode == 'delta' else 'before_' + metric] += 1
                with self.subTest(family=family, mode=mode):
                    self.assertEqual(self.validate(family, data, action='compare')['status'], 'INVALID')

    def test_compare_revalidates_raw_measurements_not_only_aggregate_deltas(self):
        old, new, comparison = self.values('xlsx')
        new['datasets'][0]['samples'] = []
        self.assertEqual(self.validate('xlsx', comparison, action='compare', before=old, after=new)['status'], 'INVALID')

    def test_invalid_or_nonfinite_budget_cannot_waive_acceptance(self):
        for value in (True, -1, float('nan'), float('inf'), '100', 1001):
            result = self.validate('latency', action='compare', budgets={'max_wall_regression_percent': value})
            self.assertEqual(result['status'], 'INVALID')

    def test_real_positive_regression_breaches_zero_budget(self):
        old, new, comparison = self.values('latency')
        # The historic real comparison contains measurable regressions in some
        # scenarios even though others improved. The budget applies to each.
        regressions = [row for row in comparison['comparisons'] if row['median_ms_reduction_percent'] < 0]
        if not regressions:
            # Swap genuine measurements and their comparison fields, without
            # inventing samples, to exercise the opposite direction instead.
            old, new = new, old
            old['label'], new['label'] = 'before', 'after'
            for row in comparison['comparisons']:
                for metric in ('median_ms', 'p95_ms'):
                    row['before_' + metric], row['after_' + metric] = row['after_' + metric], row['before_' + metric]
                    row[metric + '_reduction_percent'] = 100 * (1 - row['after_' + metric] / row['before_' + metric])
            for row in comparison['storage_and_setup']:
                for metric in ('database_bytes', 'import_setup_total_ms'):
                    row['before_' + metric], row['after_' + metric] = row['after_' + metric], row['before_' + metric]
        result = validate_performance(comparison, 'latency', 'compare', 'full',
            {'before': old['source_hashes'], 'after': new['source_hashes']}, new['harness_sha256'],
            before=old, after=new, budgets={'max_wall_regression_percent': 0})
        self.assertEqual(result['status'], 'FAILED', result)
        self.assertTrue(result['details']['budget_breaches'])

    def test_malformed_input_is_structured_invalid_without_metadata_execution(self):
        for payload in (None, [], True, 'execute this command', {'datasets': [None]}):
            self.assertEqual(self.validate('xlsx', payload)['status'], 'INVALID')
        _, data, _ = self.values('xlsx')
        data['command'] = ['untrusted-metadata-must-not-run']
        with patch('subprocess.Popen', side_effect=AssertionError('No subprocess may run')):
            self.assertEqual(self.validate('xlsx', data)['status'], 'PASSED')


class SemanticCatalogTests(unittest.TestCase):
    def test_both_existing_coverage_profiles_match_exact_catalogs(self):
        for template in (False, True):
            data = read('docs/v11/entrypoint-' + ('template' if template else 'default') + '.json')
            self.assertEqual(validate_coverage(data, template)['status'], 'PASSED')

    def test_removed_coverage_check_with_resealed_counts_is_invalid(self):
        data = read('docs/v11/entrypoint-default.json')
        data['checks'].pop()
        data['summary']['checks'] -= 1
        data['summary']['passed'] -= 1
        self.assertEqual(validate_coverage(data, False)['status'], 'INVALID')

    def test_false_coverage_status_cannot_keep_passed_true(self):
        data = read('docs/v11/entrypoint-default.json')
        data['checks'][0]['status'] = 200
        self.assertEqual(validate_coverage(data, False)['status'], 'INVALID')

    def test_registry_or_actor_matrix_deletion_cannot_reduce_required_coverage(self):
        for key in ('pages', 'callbacks', 'http_routes'):
            data = read('docs/v11/entrypoint-default.json')
            data[key].pop()
            self.assertEqual(validate_coverage(data, False)['status'], 'INVALID')

    def browser(self):
        return read('docs/v11/evidence/browser/results.json')

    def test_real_native_browser_has_all_fixed_scenarios(self):
        data = self.browser()
        self.assertEqual(len(BROWSER_CASES), 40)
        self.assertEqual(validate_browser_cases(data)['status'], 'PASSED')

    def test_forty_successful_arbitrary_names_are_not_the_required_cases(self):
        data = self.browser()
        for index, case in enumerate(data['results']):
            case['name'] = 'fake-case-' + str(index)
        self.assertEqual(validate_browser_cases(data)['status'], 'INCOMPLETE')

    def test_browser_duplicate_unknown_state_and_false_success_summary_are_invalid(self):
        for mode in ('duplicate', 'skipped', 'count'):
            data = self.browser()
            if mode == 'duplicate':
                data['results'][1]['name'] = data['results'][0]['name']
            elif mode == 'skipped':
                data['results'][0]['status'] = 'skipped'
            else:
                data['passed'] = 999
            self.assertEqual(validate_browser_cases(data)['status'], 'INVALID')

    def test_browser_cleanup_must_be_confirmed(self):
        data = self.browser()
        data['serverStopped'] = False
        self.assertEqual(validate_browser_cases(data)['status'], 'BLOCKED')

    def test_bad_nested_catalog_shapes_return_structured_invalid(self):
        for value in (None, [], True, 'bad'):
            self.assertEqual(validate_coverage(value, False)['status'], 'INVALID')
            self.assertEqual(validate_browser_cases(value)['status'], 'INVALID')
        data = self.browser()
        data['results'][0] = None
        self.assertEqual(validate_browser_cases(data)['status'], 'INVALID')


if __name__ == '__main__':
    unittest.main()
