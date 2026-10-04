"""Synthetic pure-data tolerance cases; no benchmark or product execution.

Archived records supply the producer schema only. Generated timing/memory
samples below are unit fixtures, never calibration evidence.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import statistics
import unittest
from unittest.mock import patch

from tools.acceptance_tolerance import evaluate


ROOT = Path(__file__).resolve().parents[1]
POLICY = dict(schema=1, status='approved', wall_percent=10, rss_percent=5, approved_at=1800000002.5)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode('utf-8')).hexdigest()


def summarize(record, values, suffix):
    record['median_' + suffix] = statistics.median(values)
    record['p95_' + suffix] = sorted(values)[math.ceil(len(values) * .95) - 1]


def summaries(payload, family):
    for dataset in payload['datasets']:
        if family == 'xlsx':
            summarize(dataset, [item['wall_ms'] for item in dataset['samples']], 'wall_ms')
            for name in ('peak_rss_bytes', 'peak_commit_bytes'):
                if dataset['samples'][0]['memory_after'][name] is not None:
                    summarize(dataset, [item['memory_after'][name] for item in dataset['samples']], name)
        else:
            for record in dataset['measurements'].values():
                values = record['samples_ms']
                summarize(record, values, 'ms')
                record['min_ms'], record['max_ms'] = min(values), max(values)


def wrapper(payload, run_id):
    return dict(run_id=run_id, sha256=digest(payload), payload=payload,
                clock_valid=True, created_at=1800000000)


class ToleranceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = {}
        for family, name in (('xlsx', 'v11-candidate-large-xlsx.json'),
                             ('latency', 'candidate-v10.json')):
            cls.schemas[family] = json.loads((ROOT / 'benchmarks' / 'evidence' / name).read_text(encoding='utf-8'))

    def run_fixture(self, family, index, wall=100, rss=100000000, samples=None):
        data = deepcopy(self.schemas[family])
        data['label'] = 'synthetic-unit-fixture-' + str(index)
        data['samples'] = samples if samples is not None else (7 if family == 'xlsx' else 15)
        count = data['samples']
        # Distinct distributions per run have the exact same median unless a
        # test explicitly changes it. Independence is assumed only by fixtures.
        offsets = [(position - (count - 1) / 2) * (0.1 + index * .001) for position in range(count)]
        for dataset_index, dataset in enumerate(data['datasets']):
            multiplier = dataset_index + 1
            if family == 'xlsx':
                original = dataset['samples'][0]
                dataset['samples'] = []
                for position, offset in enumerate(offsets):
                    sample = deepcopy(original)
                    sample['wall_ms'] = (wall + offset) * multiplier
                    sample['xlsx_sha256'] = digest([index, position, dataset_index])
                    peak = int((rss + offset * 10000) * multiplier)
                    for phase, ratio in (('memory_before', .8), ('memory_after', 1)):
                        memory = sample[phase]
                        memory['peak_rss_bytes'] = int(peak * ratio)
                        if memory['rss_bytes'] is not None:
                            memory['rss_bytes'] = int(peak * ratio * .9)
                            memory['peak_commit_bytes'] = int(peak * ratio * 1.1)
                            memory['private_bytes'] = int(peak * ratio * .7)
                    dataset['samples'].append(sample)
            else:
                for scenario_index, record in enumerate(dataset['measurements'].values()):
                    record['samples_ms'] = [(wall + offset) * multiplier * (scenario_index + 1)
                                            for offset in offsets]
        summaries(data, family)
        result = wrapper(data, 'unit-' + str(index))
        result['created_at'] += index
        return result

    def inputs(self, family='xlsx', samples=None):
        return ([self.run_fixture(family, index, samples=samples) for index in range(3)],
                self.run_fixture(family, 3, samples=samples))

    def result(self, baselines, candidate, family='xlsx', policy=POLICY):
        return evaluate(baselines, candidate, family, deepcopy(policy))

    def test_stable_baseline_and_distinct_single_candidate_pass_both_families(self):
        for family in self.schemas:
            baselines, candidate = self.inputs(family)
            result = self.result(baselines, candidate, family)
            self.assertEqual(result['status'], 'PASSED', result)
            self.assertEqual(result['details']['baseline_runs'], 3)
            self.assertEqual(result['details']['candidate_runs'], 1)
            self.assertEqual(result['details']['baseline_samples_per_run'], [7 if family == 'xlsx' else 15] * 3)
            self.assertEqual(result['details']['candidate_repeatability'], 'not-established-single-run')
            self.assertFalse(result['details']['noise_widens_policy'])

    def test_within_run_outlier_does_not_replace_robust_median(self):
        for family in self.schemas:
            baselines, candidate = self.inputs(family)
            for run in baselines + [candidate]:
                for dataset in run['payload']['datasets']:
                    if family == 'xlsx':
                        sample = dataset['samples'][-1]
                        sample['wall_ms'] *= 1000
                        sample['memory_after']['peak_rss_bytes'] *= 1000
                    else:
                        for record in dataset['measurements'].values():
                            record['samples_ms'][-1] *= 1000
                summaries(run['payload'], family)
            self.assertEqual(self.result(baselines, candidate, family)['status'], 'PASSED')

    def test_clear_35_percent_wall_slowdown_fails_each_family(self):
        for family in self.schemas:
            baselines, _ = self.inputs(family)
            result = self.result(baselines, self.run_fixture(family, 3, wall=135), family)
            self.assertEqual(result['status'], 'FAILED', result)
            self.assertTrue(result['details']['breaches'])

    def test_rss_regression_has_its_own_explicit_limit(self):
        baselines, _ = self.inputs()
        result = self.result(baselines, self.run_fixture('xlsx', 3, rss=106000000))
        self.assertEqual(result['status'], 'FAILED', result)
        self.assertEqual({row['metric'] for row in result['details']['breaches']}, {'rss_percent'})

    def test_omitted_or_null_tolerance_remains_incomplete(self):
        baselines, candidate = self.inputs()
        for name in ('wall_percent', 'rss_percent'):
            for remove in (False, True):
                with self.subTest(name=name, omitted=remove):
                    policy = dict(POLICY)
                    if remove:
                        del policy[name]
                    else:
                        policy[name] = None
                    result = self.result(baselines, candidate, policy=policy)
                    self.assertEqual(result['status'], 'INCOMPLETE', result)
                    self.assertEqual(result['reason'], 'explicit-tolerance-value-missing')

    def test_noisy_wall_baseline_is_incomplete_even_if_candidate_clearly_slow(self):
        baselines, _ = self.inputs()
        baselines[2] = self.run_fixture('xlsx', 2, wall=106)
        result = self.result(baselines, self.run_fixture('xlsx', 3, wall=135))
        self.assertEqual(result['status'], 'INCOMPLETE', result)
        self.assertEqual(result['reason'], 'calibration-noise-exceeds-half-tolerance')
        self.assertTrue(result['details']['breaches'])
        self.assertEqual(result['details']['policy'], POLICY)

    def test_noisy_rss_baseline_is_incomplete(self):
        baselines, candidate = self.inputs()
        baselines[2] = self.run_fixture('xlsx', 2, rss=103000000)
        result = self.result(baselines, candidate)
        self.assertEqual(result['status'], 'INCOMPLETE', result)
        self.assertEqual({row['metric'] for row in result['details']['noisy_metrics']}, {'rss_percent'})

    def test_half_tolerance_noise_and_exact_tolerance_are_inclusive(self):
        baselines, _ = self.inputs()
        baselines[0] = self.run_fixture('xlsx', 0, wall=97.5, rss=98750000)
        baselines[2] = self.run_fixture('xlsx', 2, wall=102.5, rss=101250000)
        result = self.result(baselines, self.run_fixture('xlsx', 3, wall=110, rss=105000000))
        self.assertEqual(result['status'], 'PASSED', result)

    def test_no_baseline_no_candidate_and_fewer_than_three_runs_are_incomplete(self):
        baselines, candidate = self.inputs()
        for runs, observed in (([], candidate), (baselines, None), (baselines[:2], candidate)):
            self.assertEqual(self.result(runs, observed)['status'], 'INCOMPLETE')

    def test_missing_or_provisional_policy_never_passes(self):
        baselines, candidate = self.inputs()
        for policy in (None, dict(POLICY, status='provisional')):
            result = self.result(baselines, candidate, policy=policy)
            self.assertEqual(result['status'], 'INCOMPLETE', result)

    def test_approval_timestamp_missing_null_or_wrong_order_is_incomplete(self):
        baselines, candidate = self.inputs()
        missing = dict(POLICY)
        missing.pop('approved_at')
        for policy in (missing, dict(POLICY, approved_at=None),
                       dict(POLICY, approved_at=1800000001),
                       dict(POLICY, approved_at=1800000004)):
            result = self.result(baselines, candidate, policy=policy)
            self.assertEqual(result['status'], 'INCOMPLETE', result)

    def test_approval_may_equal_latest_baseline_or_candidate_time(self):
        baselines, candidate = self.inputs()
        for approved_at in (baselines[-1]['created_at'], candidate['created_at']):
            result = self.result(baselines, candidate, policy=dict(POLICY, approved_at=approved_at))
            self.assertEqual(result['status'], 'PASSED', result)

    def test_approval_timestamp_must_be_finite_numeric(self):
        baselines, candidate = self.inputs()
        for approved_at in (True, float('inf'), float('nan'), -1, '1800000002.5'):
            result = self.result(baselines, candidate, policy=dict(POLICY, approved_at=approved_at))
            self.assertEqual(result['status'], 'INVALID', result)

    def test_limits_are_required_finite_numeric_and_no_defaults_are_supplied(self):
        baselines, candidate = self.inputs()
        for field in ('wall_percent', 'rss_percent'):
            policy = dict(POLICY)
            policy.pop(field)
            self.assertEqual(self.result(baselines, candidate, policy=policy)['status'], 'INCOMPLETE')
            for value in (True, -1, float('nan'), float('inf'), '10'):
                policy[field] = value
                self.assertEqual(self.result(baselines, candidate, policy=policy)['status'], 'INVALID')

    def test_policy_and_measurement_schema_versions_fail_closed(self):
        baselines, candidate = self.inputs()
        self.assertEqual(self.result(baselines, candidate, policy=dict(POLICY, schema=2))['status'], 'INCOMPLETE')
        for value, status in ((2, 'INCOMPLETE'), (True, 'INVALID'), (None, 'INVALID')):
            changed = deepcopy(candidate)
            changed['payload']['schema'] = value
            self.assertEqual(self.result(baselines, changed)['status'], status)

    def test_three_samples_are_insufficient_even_with_three_runs(self):
        for family in self.schemas:
            baselines, candidate = self.inputs(family, samples=3)
            result = self.result(baselines, candidate, family)
            self.assertEqual(result['status'], 'INCOMPLETE', result)
            self.assertEqual(result['reason'], 'insufficient-samples-per-run')

    def test_exact_sample_counts_and_recomputed_aggregates_are_required(self):
        for family in self.schemas:
            for change in ('short', 'extra', 'aggregate'):
                baselines, candidate = self.inputs(family)
                dataset = candidate['payload']['datasets'][0]
                record = dataset if family == 'xlsx' else dataset['measurements']['service_query_page']
                values = record['samples' if family == 'xlsx' else 'samples_ms']
                if change == 'short':
                    values.pop()
                elif change == 'extra':
                    values.append(deepcopy(values[0]))
                else:
                    record['median_wall_ms' if family == 'xlsx' else 'median_ms'] += 1
                self.assertEqual(self.result(baselines, candidate, family)['status'], 'INVALID')

    def test_duplicate_run_ids_or_byte_hashes_are_invalid(self):
        for field in ('run_id', 'sha256'):
            baselines, candidate = self.inputs()
            candidate[field] = baselines[0][field]
            self.assertEqual(self.result(baselines, candidate)['status'], 'INVALID')

    def test_copied_raw_runs_cannot_be_resealed_renamed_or_reordered(self):
        for family in self.schemas:
            baselines, candidate = self.inputs(family)
            candidate['payload'] = deepcopy(baselines[0]['payload'])
            candidate['payload']['label'] = 'relabelled-copy'
            for dataset in candidate['payload']['datasets']:
                if family == 'xlsx':
                    dataset['samples'].reverse()
                    for sample in dataset['samples']:
                        sample['xlsx_sha256'] = 'e' * 64
                else:
                    for record in dataset['measurements'].values():
                        record['samples_ms'].reverse()
            candidate['sha256'] = digest(candidate['payload'])
            result = self.result(baselines, candidate, family)
            self.assertEqual(result['status'], 'INVALID', result)
            self.assertEqual(result['reason'], 'duplicate-raw-digest')

    def test_repeated_values_are_legitimate_observations_not_independence_proof(self):
        for family in self.schemas:
            baselines, candidate = self.inputs(family)
            dataset = candidate['payload']['datasets'][0]
            if family == 'xlsx':
                dataset['samples'][-1] = deepcopy(dataset['samples'][0])
                dataset['samples'][-1]['xlsx_sha256'] = 'e' * 64
            else:
                values = dataset['measurements']['service_query_page']['samples_ms']
                values[-1] = values[0]
            summaries(candidate['payload'], family)
            result = self.result(baselines, candidate, family)
            self.assertEqual(result['status'], 'PASSED', result)

    def test_numeric_representation_does_not_hide_complete_run_replay(self):
        baselines, candidate = self.inputs('latency')
        for dataset in baselines[0]['payload']['datasets']:
            for record in dataset['measurements'].values():
                record['samples_ms'] = [int(value) for value in record['samples_ms']]
        summaries(baselines[0]['payload'], 'latency')
        candidate['payload'] = deepcopy(baselines[0]['payload'])
        for dataset in candidate['payload']['datasets']:
            for record in dataset['measurements'].values():
                record['samples_ms'] = [float(value) for value in record['samples_ms']]
        summaries(candidate['payload'], 'latency')
        self.assertEqual(self.result(baselines, candidate, 'latency')['status'], 'INVALID')

    def test_descriptive_memory_metadata_cannot_hide_replayed_run(self):
        baselines, candidate = self.inputs()
        candidate['payload'] = deepcopy(baselines[0]['payload'])
        for dataset in candidate['payload']['datasets']:
            for sample in dataset['samples']:
                sample['memory_before']['description'] = 'changed metadata'
                sample['memory_after']['description'] = 'changed metadata'
        result = self.result(baselines, candidate)
        self.assertEqual(result['status'], 'INVALID', result)
        self.assertEqual(result['reason'], 'duplicate-raw-digest')

    def test_environment_harness_and_sqlite_mismatches_are_incomplete(self):
        for family in self.schemas:
            for field, value in (('python', '3.8.20'), ('platform', 'Windows other-build'),
                                 ('machine', 'other-architecture'), ('harness_sha256', 'e' * 64),
                                 ('sqlite', 'other-version')):
                baselines, candidate = self.inputs(family)
                if field == 'platform':
                    value = candidate['payload']['platform'].split(' ')[0] + ' other-build'
                candidate['payload'][field] = value
                self.assertEqual(self.result(baselines, candidate, family)['status'], 'INCOMPLETE', field)
            baselines, candidate = self.inputs(family)
            candidate['payload']['packages']['Flask'] = 'different-version'
            self.assertEqual(self.result(baselines, candidate, family)['status'], 'INCOMPLETE')

    def test_product_source_can_change_only_between_baseline_and_candidate(self):
        baselines, candidate = self.inputs()
        name = next(iter(candidate['payload']['source_hashes']))
        candidate['payload']['source_hashes'][name] = 'e' * 64
        self.assertEqual(self.result(baselines, candidate)['status'], 'PASSED')
        baselines[1]['payload']['source_hashes'][name] = 'e' * 64
        self.assertEqual(self.result(baselines, candidate)['status'], 'INCOMPLETE')

    def test_workload_fixture_and_dataset_plan_mismatch_is_incomplete(self):
        for family in self.schemas:
            baselines, candidate = self.inputs(family)
            candidate['payload']['datasets'].pop()
            self.assertEqual(self.result(baselines, candidate, family)['status'], 'INCOMPLETE')
            baselines, candidate = self.inputs(family)
            dataset = candidate['payload']['datasets'][0]
            if family == 'xlsx':
                dataset['setup']['normalized_fixture_sha256'] = 'e' * 64
                for sample in dataset['samples'] + dataset['warmups']:
                    sample['validation']['normalized_rows_sha256'] = 'e' * 64
            else:
                dataset['fixture_sha256'] = 'e' * 64
            self.assertEqual(self.result(baselines, candidate, family)['status'], 'INCOMPLETE')

    def test_valid_different_sampling_plan_is_incomplete(self):
        for family, count in (('xlsx', 9), ('latency', 17)):
            baselines, _ = self.inputs(family)
            self.assertEqual(self.result(baselines, self.run_fixture(family, 3, samples=count), family)['status'], 'INCOMPLETE')

    def test_missing_scenario_or_invalid_export_validation_is_invalid(self):
        baselines, candidate = self.inputs('latency')
        candidate['payload']['datasets'][0]['measurements'].pop('callback_export_csv')
        self.assertEqual(self.result(baselines, candidate, 'latency')['status'], 'INVALID')
        baselines, candidate = self.inputs()
        candidate['payload']['datasets'][0]['samples'][0]['validation']['rows'] = 1
        self.assertEqual(self.result(baselines, candidate)['status'], 'INVALID')

    def test_invalid_clock_wrapper_and_nonfinite_raw_measurements_are_invalid(self):
        for field, value in (('clock_valid', 1), ('created_at', -1),
                             ('created_at', float('inf')), ('sha256', 'wrong'), ('run_id', ' spaced ')):
            baselines, candidate = self.inputs()
            candidate[field] = value
            self.assertEqual(self.result(baselines, candidate)['status'], 'INVALID')
        for value in (float('nan'), float('inf'), -1, True):
            baselines, candidate = self.inputs()
            candidate['payload']['datasets'][0]['samples'][0]['wall_ms'] = value
            self.assertEqual(self.result(baselines, candidate)['status'], 'INVALID')

    def test_invalid_environment_clock_blocks_as_incomplete(self):
        baselines, candidate = self.inputs()
        candidate['clock_valid'] = False
        self.assertEqual(self.result(baselines, candidate)['status'], 'INCOMPLETE')

    def test_archived_three_sample_xlsx_record_is_valid_but_insufficient(self):
        baseline = wrapper(deepcopy(self.schemas['xlsx']), 'archived-baseline')
        result = self.result([baseline], None)
        self.assertEqual(result['status'], 'INCOMPLETE', result)
        self.assertNotEqual(result['reason'], 'malformed-or-inconsistent-measurement')

    def test_labels_and_descriptive_commands_are_inert_and_inputs_unchanged(self):
        baselines, candidate = self.inputs()
        candidate['payload']['label'] = 'arbitrary-label'
        candidate['payload']['command'] = ['never', 'execute', 'this']
        snapshot = deepcopy((baselines, candidate))
        with patch('subprocess.Popen', side_effect=AssertionError('must not execute')):
            result = self.result(baselines, candidate)
        self.assertEqual(result['status'], 'PASSED', result)
        self.assertEqual((baselines, candidate), snapshot)


if __name__ == '__main__':
    unittest.main()
