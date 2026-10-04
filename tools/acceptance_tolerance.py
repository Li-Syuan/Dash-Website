"""Explicit-policy tolerance gate over comparable, independently bound evidence.

Python 3.8, pure data only. The caller verifies file bytes, trusted sources,
runtime receipts and freshness. Unique metadata and sample fingerprints reject
obvious reuse; they do not prove honest execution or statistical independence.
No business tolerance is inferred, widened for noise, or supplied by default.
"""
import hashlib
import json
import math
import statistics

try:
    from .acceptance_evidence import (
        LATENCY_SCENARIOS, _METHODS, _PACKAGES, _hash, _integer, _latency_dataset,
        _list, _mapping, _number, _source, _text, _xlsx_dataset,
    )
except ImportError:  # Fixed sibling import when the trusted CLI runs as a script.
    from acceptance_evidence import (
        LATENCY_SCENARIOS, _METHODS, _PACKAGES, _hash, _integer, _latency_dataset,
        _list, _mapping, _number, _source, _text, _xlsx_dataset,
    )


MIN_BASELINE_RUNS = 3
MIN_SAMPLES = {'latency': 15, 'xlsx': 7}


class _GateError(ValueError):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason
        super().__init__(reason)


def _require(condition, reason, status='INVALID'):
    if not condition:
        raise _GateError(status, reason)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True, allow_nan=False).encode('utf-8')).hexdigest()


def _policy(value):
    if value is None:
        return None
    _mapping(value)
    _require(type(value.get('schema')) is int, 'malformed-policy-schema')
    _require(value['schema'] == 1, 'unsupported-policy-schema', 'INCOMPLETE')
    _require(value.get('status') in ('provisional', 'approved'), 'invalid-policy-status')
    for name in ('wall_percent', 'rss_percent'):
        _require(value.get(name) is not None, 'explicit-tolerance-value-missing', 'INCOMPLETE')
        _number(value.get(name), positive=False)
    approved_at = value.get('approved_at')
    if approved_at is not None:
        _number(approved_at, positive=False)
    _require(value['status'] != 'approved' or approved_at is not None,
             'policy-approval-time-missing', 'INCOMPLETE')
    result = {name: value[name] for name in ('schema', 'status', 'wall_percent', 'rss_percent')}
    result['approved_at'] = approved_at
    return result


def _sample_fingerprint(values):
    # Equal readings may be legitimate observations. Preserve their counts;
    # reject complete replay between purported runs, not repeated scalar values.
    return sorted(_digest(value) for value in values)


def _memory_fingerprint(value):
    # Extra descriptive metadata must not conceal a replay of the same native
    # readings. The schema validator has already checked these fixed fields.
    return {name: value[name] for name in (
        'method', 'peak_rss_bytes', 'rss_bytes', 'peak_commit_bytes', 'private_bytes')}


def _measurement(payload, family):
    payload = _mapping(payload)
    _require(type(payload.get('schema')) is int, 'malformed-measurement-schema')
    _require(payload['schema'] == 1, 'unsupported-measurement-schema', 'INCOMPLETE')
    samples, warmups = _integer(payload.get('samples'), 1), _integer(payload.get('warmups'), 1)
    _source(payload, payload.get('source_hashes'), payload.get('harness_sha256'))
    for name in ('python', 'platform', 'machine'):
        _text(payload.get(name))
    packages = _mapping(payload.get('packages'))
    expected_packages = ('openpyxl', 'Flask', 'Werkzeug', 'dash') if family == 'xlsx' else _PACKAGES
    _require(set(packages) == set(expected_packages), 'invalid-package-inventory')
    for version in packages.values():
        _text(version)
    conditions = {name: payload[name] for name in (
        'schema', 'harness_sha256', 'python', 'platform', 'machine', 'packages', 'samples', 'warmups')}
    for name, expected in _METHODS[family].items():
        actual = payload.get(name)
        _require(type(actual) is type(expected), 'malformed-measurement-method')
        # The trusted producer labels p95 with its historical three-sample
        # description even when --samples is higher. Its numerical rule remains
        # nearest-rank; raw samples below revalidate that rule for the real n.
        _require(actual == expected, 'unsupported-measurement-method', 'INCOMPLETE')
        conditions[name] = actual
    if family == 'latency' or 'sqlite' in payload:
        conditions['sqlite'] = _text(payload.get('sqlite'))
    datasets = _list(payload.get('datasets'))
    _require(bool(datasets), 'missing-workload', 'INCOMPLETE')
    metrics, workload, raw, seen_sizes = {}, [], [], set()
    for dataset in datasets:
        dataset = _mapping(dataset)
        key = 'rows' if family == 'xlsx' else 'active_rows'
        size = _integer(dataset.get(key), 1)
        _require(size not in seen_sizes, 'duplicate-dataset')
        seen_sizes.add(size)
        if family == 'xlsx':
            _require(50000 < size <= 500000, 'unsupported-workload', 'INCOMPLETE')
            windows = payload['platform'].startswith('Windows ')
            _xlsx_dataset(dataset, size, samples, warmups, windows)
            source = dataset['samples']
            observed = [dict(wall_ms=float(item['wall_ms']), memory_before=_memory_fingerprint(item['memory_before']),
                             memory_after=_memory_fingerprint(item['memory_after'])) for item in source]
            raw.append((size, _sample_fingerprint(observed)))
            metrics[(size, 'export_xlsx', 'wall_percent')] = statistics.median(item['wall_ms'] for item in source)
            metrics[(size, 'export_xlsx', 'rss_percent')] = statistics.median(
                item['memory_after']['peak_rss_bytes'] for item in source)
            workload.append(dict(rows=size, normalized_fixture_sha256=dataset['setup']['normalized_fixture_sha256'],
                                 default_cap=dataset['default_cap_check']['default_cap']))
        else:
            _require(1000 <= size <= 50000, 'unsupported-workload', 'INCOMPLETE')
            # Reuse the strict producer schema. The fixture's literal filter
            # matches indices congruent to 7 modulo 100, including a final partial
            # group; the fixed-profile validator assumes sizes divisible by 100.
            expected_filtered = (size + 92) // 100
            _require(type(dataset.get('filtered_rows')) is int and dataset['filtered_rows'] == expected_filtered,
                     'inconsistent-filtered-fixture')
            validation_copy = dict(dataset, filtered_rows=size // 100)
            _latency_dataset(validation_copy, size, samples)
            for scenario in LATENCY_SCENARIOS:
                values = dataset['measurements'][scenario]['samples_ms']
                raw.append((size, scenario, _sample_fingerprint([float(value) for value in values])))
                metrics[(size, scenario, 'wall_percent')] = statistics.median(values)
            workload.append({name: dataset[name] for name in (
                'active_rows', 'deleted_seed_rows', 'fixture_sha256', 'normalized_rows_sha256', 'filtered_rows')})
            workload[-1]['setup_batch_rows'] = dataset['setup_import']['batch_rows']
            workload[-1]['setup_scope'] = dataset['setup_import']['scope']
    # Dataset ordering is part of a benchmark run's plan and can affect caches.
    conditions['workload'] = workload
    return dict(conditions=conditions, source_hashes=payload['source_hashes'], metrics=metrics,
                raw_digest=_digest(raw), samples=samples, warmups=warmups)


def _wrapper(value, family):
    value = _mapping(value)
    identifier = _text(value.get('run_id'))
    _require(len(identifier) <= 128 and identifier.strip() == identifier, 'invalid-run-id')
    digest = _hash(value.get('sha256'))
    _require(type(value.get('clock_valid')) is bool, 'malformed-clock-state')
    _require(value['clock_valid'], 'invalid-clock-environment', 'INCOMPLETE')
    _number(value.get('created_at'), positive=False)
    result = _measurement(value.get('payload'), family)
    result.update(run_id=identifier, sha256=digest, created_at=value['created_at'])
    return result


def evaluate(calibrations, candidate, family, policy):
    """Evaluate one candidate run against >=3 independently bound baseline runs.

    Each wrapper contains run_id, sha256, payload, clock_valid and created_at.
    Explicit schema-1 policy contains status ('provisional' or 'approved'),
    wall_percent, rss_percent and finite approved_at (UTC epoch seconds when
    approved). Baselines must predate approval; the candidate cannot predate it.
    Extra descriptive metadata is inert. The caller binds timestamp provenance.
    Baselines need >=15 latency or >=7 XLSX samples/run and matching plans.
    Candidate uses the same minimum and plan, but its product source may differ.

    A metric's reference is median(baseline run medians). Baseline noise is
    100*(maximum run median-minimum run median)/reference. Noise above half the
    approved metric limit makes the result INCOMPLETE; it never raises the limit.
    One candidate run establishes only this run's comparison, not repeatability.
    """
    details = dict(minimum_baseline_runs=MIN_BASELINE_RUNS,
                   candidate_runs=0 if candidate is None else 1,
                   candidate_repeatability='not-established-single-run',
                   calibration_scope='measured-workloads-and-runtime-only-not-general-performance-certification',
                   warmup_evidence='xlsx-raw-warmup-records-latency-declared-warmup-count',
                   independence='unique-run-metadata-and-full-run-fingerprints-only-caller-binds-execution',
                   repeated_values='allowed-observation-values-do-not-prove-or-disprove-independent-capture',
                   artifact_source_runtime_and_freshness='caller-must-verify',
                   policy_limits_inferred=False, noise_widens_policy=False)
    try:
        _require(family in MIN_SAMPLES, 'unsupported-family')
        details.update(family=family, minimum_samples_per_run=MIN_SAMPLES[family])
        approved = _policy(policy)
        baselines = [_wrapper(value, family) for value in _list(calibrations)]
        observed = _wrapper(candidate, family) if candidate is not None else None
        all_runs = baselines + ([] if observed is None else [observed])
        for field in ('run_id', 'sha256', 'raw_digest'):
            values = [run[field] for run in all_runs]
            _require(len(set(values)) == len(values), 'duplicate-' + field.replace('_', '-'))
        details['baseline_runs'] = len(baselines)
        details['baseline_samples_per_run'] = [run['samples'] for run in baselines]
        details['candidate_samples'] = None if observed is None else observed['samples']
        details['policy'] = approved
        if not baselines:
            return dict(status='INCOMPLETE', reason='calibration-baseline-missing', details=details)
        reference = baselines[0]
        for run in baselines[1:]:
            _require(run['source_hashes'] == reference['source_hashes'], 'baseline-source-mismatch', 'INCOMPLETE')
        for run in all_runs[1:]:
            _require(run['conditions'] == reference['conditions'], 'environment-workload-or-plan-mismatch', 'INCOMPLETE')
        if observed is None:
            return dict(status='INCOMPLETE', reason='candidate-missing', details=details)
        if len(baselines) < MIN_BASELINE_RUNS:
            return dict(status='INCOMPLETE', reason='insufficient-independent-baseline-runs', details=details)
        if any(run['samples'] < MIN_SAMPLES[family] for run in all_runs):
            return dict(status='INCOMPLETE', reason='insufficient-samples-per-run', details=details)
        if approved is None:
            return dict(status='INCOMPLETE', reason='explicit-tolerance-policy-missing', details=details)
        if approved['status'] != 'approved':
            return dict(status='INCOMPLETE', reason='tolerance-policy-not-approved', details=details)
        latest_baseline = max(run['created_at'] for run in baselines)
        details['approval_order'] = dict(latest_baseline_created_at=latest_baseline,
                                         approved_at=approved['approved_at'],
                                         candidate_created_at=observed['created_at'])
        if not latest_baseline <= approved['approved_at'] <= observed['created_at']:
            return dict(status='INCOMPLETE', reason='policy-approval-order-mismatch', details=details)
        comparisons, noisy, breaches = [], [], []
        for key in reference['metrics']:
            medians = [run['metrics'][key] for run in baselines]
            baseline = statistics.median(medians)
            current = observed['metrics'][key]
            spread = 100 * ((max(medians) - min(medians)) / baseline)
            regression = 100 * ((current - baseline) / baseline)
            _require(math.isfinite(spread) and math.isfinite(regression), 'nonfinite-comparison')
            limit = approved[key[2]]
            row = dict(rows=key[0], scenario=key[1], metric=key[2], baseline_run_medians=medians,
                       baseline_median=baseline, candidate_median=current,
                       baseline_span_percent=spread, noise_limit_percent=limit / 2,
                       approved_limit_percent=limit, regression_percent=regression)
            comparisons.append(row)
            if spread > limit / 2:
                noisy.append(row)
            if regression > limit:
                breaches.append(row)
        details.update(comparisons=comparisons, noisy_metrics=noisy, breaches=breaches)
        if noisy:
            return dict(status='INCOMPLETE', reason='calibration-noise-exceeds-half-tolerance', details=details)
        if breaches:
            return dict(status='FAILED', reason='approved-tolerance-exceeded', details=details)
        return dict(status='PASSED', reason='within-approved-tolerance', details=details)
    except _GateError as error:
        return dict(status=error.status, reason=error.reason, details=details)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, ZeroDivisionError):
        return dict(status='INVALID', reason='malformed-or-inconsistent-measurement', details=details)
