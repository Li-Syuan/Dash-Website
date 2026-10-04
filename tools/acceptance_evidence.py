"""Pure-data validators for the fixed local acceptance producers (Python 3.8).

No file reads, subprocesses, project imports, or evaluation of evidence occur.
The caller binds files, process receipts, timestamps and runtime/source identity.
These are consistency checks, not signatures or attestations of honest execution.
"""
import hashlib
import json
import math
import re
import statistics


LATENCY_SCENARIOS = (
    'service_query_page', 'service_query_filter', 'service_export_csv',
    'service_export_xlsx', 'callback_query_page', 'callback_export_csv',
    'callback_export_xlsx',
)
_PACKAGES = ('dash', 'Flask', 'Werkzeug', 'Flask-Login', 'plotly', 'openpyxl',
             'dash-bootstrap-components', 'dash-mantine-components', 'setuptools')
_COMMON = ('schema', 'python', 'platform', 'machine', 'packages', 'harness_sha256',
           'samples', 'warmups', 'timer', 'p95_method')
_METHODS = {
    'latency': {
        'timer': 'time.perf_counter_ns; monotonic elapsed, milliseconds',
        'p95_method': 'nearest rank; no interpolation',
        'measurement_scope': 'synchronous service calls and real Flask test-client Dash HTTP dispatch; not browser/network latency',
        'seed_method': 'authorized synthetic stage_rows + submit_stage; setup outside timings',
    },
    'xlsx': {
        'timer': 'perf_counter_ns; export call only; milliseconds',
        'memory_scope': 'native process-lifetime peak in fresh child; includes imports/service setup; measured before/after export; excludes fixture seed and validator children',
        'warmup_scope': 'separate fresh processes; warms filesystem/OS caches, not the timed Python heap',
        'p95_method': 'nearest rank; three samples => maximum; descriptive only',
        'fixture_method': 'authorized 500-row stage/import batches in separate seed child',
        'default_cap_unchanged': 50000,
    },
}
_MEMORY_WINDOWS = 'Windows GetProcessMemoryInfo; bytes; process-lifetime high-water marks'
_MEMORY_POSIX = 'getrusage ru_maxrss; bytes; process-lifetime high-water mark'


class _Invalid(ValueError):
    pass


def _require(condition, code):
    if not condition:
        raise _Invalid(code)


def _mapping(value):
    _require(type(value) is dict, 'invalid-object')
    return value


def _list(value, length=None):
    _require(type(value) is list and (length is None or len(value) == length), 'invalid-list-count')
    return value


def _integer(value, minimum=0):
    _require(type(value) is int and value >= minimum, 'invalid-integer')
    return value


def _number(value, positive=True):
    _require(type(value) in (int, float) and math.isfinite(value)
             and (value > 0 if positive else value >= 0), 'invalid-measurement')
    return value


def _hash(value):
    _require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None, 'invalid-digest')
    return value


def _text(value):
    _require(type(value) is str and 0 < len(value) <= 1024 and '\x00' not in value, 'invalid-text')
    return value


def _equal_number(actual, expected):
    _require(type(actual) in (int, float) and math.isfinite(actual)
             and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-9), 'inconsistent-derived-metric')


def _result(status, reason, **details):
    return dict(status=status, reason=reason, details=details)


def _failure(error):
    return _result('INVALID', str(error) if isinstance(error, _Invalid) else 'malformed-producer-evidence')


def _source(payload, files, harness):
    _mapping(files)
    expected = {name: digest for name, digest in files.items()
                if type(name) is str and name.startswith('reporting_workspace/') and name.endswith('.py')}
    _require(bool(expected), 'empty-product-source-inventory')
    for name, digest in expected.items():
        _require('\\' not in name and ':' not in name and
                 all(part not in ('', '.', '..') for part in name.split('/')), 'unsafe-source-name')
        _hash(digest)
    _require(payload.get('source_hashes') == expected, 'source-inventory-mismatch')
    _require(payload.get('harness_sha256') == _hash(harness), 'harness-mismatch')


def _plan(family, profile):
    _require(family in _METHODS and profile in ('full', 'performance', 'performance-smoke',
                                              'performance-baseline', 'performance-calibrated'), 'invalid-performance-profile')
    if family == 'xlsx':
        _require(profile != 'performance-smoke', 'xlsx-not-in-smoke-profile')
        return [100000, 200000], (7 if profile in ('performance-baseline', 'performance-calibrated') else 3), 1
    return ([1000], 3, 1) if profile == 'performance-smoke' else ([5000, 25000], 15, 3)


def _summary(record, values, median, p95):
    _equal_number(record.get(median), statistics.median(values))
    _equal_number(record.get(p95), sorted(values)[math.ceil(len(values) * .95) - 1])


def _memory(value, windows):
    value = _mapping(value)
    _require(value.get('method') == (_MEMORY_WINDOWS if windows else _MEMORY_POSIX), 'memory-method-mismatch')
    _integer(value.get('peak_rss_bytes'), 1)
    for name in ('rss_bytes', 'peak_commit_bytes', 'private_bytes'):
        if windows:
            _integer(value.get(name), 1)
        else:
            _require(name in value and value[name] is None, 'unexpected-memory-metric')
    if windows:
        _require(value['peak_rss_bytes'] >= value['rss_bytes'] and
                 value['peak_commit_bytes'] >= value['private_bytes'], 'inconsistent-memory-peak')
    return value


def _xlsx_dataset(dataset, size, samples, warmups, windows):
    _require(type(dataset.get('rows')) is int and dataset['rows'] == size, 'dataset-size-mismatch')
    setup = _mapping(dataset.get('setup'))
    _require(type(setup.get('rows')) is int and setup['rows'] == size, 'fixture-size-mismatch')
    expected_hash = _hash(setup.get('normalized_fixture_sha256'))
    _number(setup.get('setup_ms'))
    _integer(setup.get('database_bytes'), 1)
    cap = _mapping(dataset.get('default_cap_check'))
    _require(cap.get('status') == 'expected-rejection' and type(cap.get('default_cap')) is int
             and cap['default_cap'] == 50000, 'default-cap-not-verified')
    _number(cap.get('wall_ms'))
    measured = _list(dataset.get('samples'), samples)
    for sample in _list(dataset.get('warmups'), warmups) + measured:
        sample = _mapping(sample)
        _number(sample.get('wall_ms'))
        _integer(sample.get('xlsx_bytes'), 1)
        _hash(sample.get('xlsx_sha256'))
        _require(type(sample.get('configured_export_cap')) is int and sample['configured_export_cap'] == size,
                 'configured-cap-mismatch')
        before = _memory(sample.get('memory_before'), windows)
        after = _memory(sample.get('memory_after'), windows)
        for metric in ('peak_rss_bytes', 'peak_commit_bytes'):
            if before[metric] is not None:
                _require(after[metric] >= before[metric], 'memory-peak-decreased')
        validation = _mapping(sample.get('validation'))
        _require(type(validation.get('rows')) is int and validation['rows'] == size
                 and validation.get('normalized_rows_sha256') == expected_hash
                 and validation.get('sheet_names') == ['QSL']
                 and validation.get('cell_types') == ['n', 'n'] + ['s'] * 7
                 and validation.get('literal_formula_error_unicode_date_like_text') == 'passed',
                 'xlsx-semantics-not-verified')
    metrics = {'wall_ms': [item['wall_ms'] for item in measured],
               'peak_rss_bytes': [item['memory_after']['peak_rss_bytes'] for item in measured]}
    if windows:
        metrics['peak_commit_bytes'] = [item['memory_after']['peak_commit_bytes'] for item in measured]
    else:
        _require('median_peak_commit_bytes' not in dataset and 'p95_peak_commit_bytes' not in dataset,
                 'unsupported-commit-memory-summary')
    for metric, values in metrics.items():
        _summary(dataset, values, 'median_' + metric, 'p95_' + metric)


def _latency_dataset(dataset, size, samples):
    _require(type(dataset.get('active_rows')) is int and dataset['active_rows'] == size, 'dataset-size-mismatch')
    _require(type(dataset.get('deleted_seed_rows')) is int and dataset['deleted_seed_rows'] == 2,
             'fixture-deleted-count-mismatch')
    _require(type(dataset.get('filtered_rows')) is int and dataset['filtered_rows'] == size // 100,
             'fixture-filter-count-mismatch')
    for name in ('fixture_sha256', 'normalized_rows_sha256'):
        _hash(dataset.get(name))
    _integer(dataset.get('database_bytes'), 1)
    setup = _mapping(dataset.get('setup_import'))
    _require(type(setup.get('batch_rows')) is int and setup['batch_rows'] == 500 and
             setup.get('scope') == 'one growing dataset setup, not steady-state write throughput',
             'fixture-setup-method-mismatch')
    times = [_number(value) for value in _list(setup.get('samples_ms'), math.ceil(size / 500))]
    _equal_number(setup.get('total_ms'), sum(times))
    _equal_number(setup.get('median_batch_ms'), statistics.median(times))
    _require(bool(_list(dataset.get('query_plan'))), 'missing-query-plan')
    for line in dataset['query_plan']:
        _text(line)
    measurements = _mapping(dataset.get('measurements'))
    _require(set(measurements) == set(LATENCY_SCENARIOS), 'scenario-set-mismatch')
    for scenario, record in measurements.items():
        record = _mapping(record)
        times = [_number(value) for value in _list(record.get('samples_ms'), samples)]
        _summary(record, times, 'median_ms', 'p95_ms')
        _equal_number(record.get('min_ms'), min(times))
        _equal_number(record.get('max_ms'), max(times))
        if scenario.startswith('callback_') or scenario.startswith('service_export_'):
            _integer(record.get('response_bytes'), 1)
        _require(bool(_list(record.get('profile'))), 'missing-profile')
        for item in record['profile']:
            _mapping(item)
            _text(item.get('file'))
            _text(item.get('function'))
            _integer(item.get('line'))
            _integer(item.get('calls'), 1)
            _number(item.get('self_ms'), positive=False)
            _number(item.get('cumulative_ms'), positive=False)


def _measurement(payload, family, action, profile, files, harness):
    payload = _mapping(payload)
    sizes, samples, warmups = _plan(family, profile)
    _require(type(payload.get('schema')) is int and payload['schema'] == 1
             and payload.get('label') == action, 'measurement-schema-or-label-mismatch')
    _source(payload, files, harness)
    _require(type(payload.get('samples')) is int and payload['samples'] == samples
             and type(payload.get('warmups')) is int and payload['warmups'] == warmups, 'sampling-plan-mismatch')
    for name, value in _METHODS[family].items():
        _require(type(payload.get(name)) is type(value) and payload[name] == value, 'measurement-method-mismatch')
    _require(re.fullmatch('[0-9]+\.[0-9]+\.[0-9]+', _text(payload.get('python'))) is not None,
             'invalid-python-version')
    _text(payload.get('platform'))
    _text(payload.get('machine'))
    packages = _mapping(payload.get('packages'))
    expected_packages = ('openpyxl', 'Flask', 'Werkzeug', 'dash') if family == 'xlsx' else _PACKAGES
    _require(set(packages) == set(expected_packages), 'package-set-mismatch')
    for value in packages.values():
        _text(value)
    if family == 'latency':
        _text(payload.get('sqlite'))
    datasets = _list(payload.get('datasets'), len(sizes))
    for dataset, size in zip(datasets, sizes):
        dataset = _mapping(dataset)
        if family == 'xlsx':
            _xlsx_dataset(dataset, size, samples, warmups, payload['platform'].startswith('Windows '))
        else:
            _latency_dataset(dataset, size, samples)
    return sizes


def _comparison(payload, family, profile, sources, harness, before, after, budgets):
    _mapping(payload)
    _mapping(sources)
    _require(set(sources) == {'before', 'after'}, 'comparison-requires-both-source-inventories')
    sizes = _measurement(before, family, 'before', profile, sources['before'], harness)
    _measurement(after, family, 'after', profile, sources['after'], harness)
    for field in set(_COMMON) | set(_METHODS[family]) | ({'sqlite'} if family == 'latency' else set()):
        _require(before[field] == after[field], 'incomparable-measurement-conditions')
    _require(type(payload.get('schema')) is int and payload['schema'] == 1
             and payload.get('conditions_match') is True, 'invalid-comparison-schema')
    for action in ('before', 'after'):
        _hash(payload.get(action + '_sha256'))  # The caller binds actual file bytes.
        _require(payload.get(action + '_file') == family + '_' + action + '.json', 'comparison-file-mismatch')
    changes = [name for name in sorted(set(before['source_hashes']) | set(after['source_hashes']))
               if before['source_hashes'].get(name) != after['source_hashes'].get(name)]
    _require(payload.get('source_changes') == changes, 'comparison-source-change-mismatch')
    expected, storage = [], []
    for old, new, size in zip(before['datasets'], after['datasets'], sizes):
        if family == 'xlsx':
            _require(old['setup']['normalized_fixture_sha256'] == new['setup']['normalized_fixture_sha256'],
                     'incomparable-fixture')
            row = {'rows': size}
            metrics = ['median_wall_ms', 'p95_wall_ms', 'median_peak_rss_bytes', 'p95_peak_rss_bytes']
            if before['platform'].startswith('Windows '):
                metrics += ['median_peak_commit_bytes', 'p95_peak_commit_bytes']
            for metric in metrics:
                row.update({'before_' + metric: old[metric], 'after_' + metric: new[metric],
                            metric + '_reduction_percent': 100 * (1 - new[metric] / old[metric])})
            expected.append(row)
        else:
            for field in ('active_rows', 'deleted_seed_rows', 'fixture_sha256', 'normalized_rows_sha256', 'filtered_rows'):
                _require(old[field] == new[field], 'incomparable-fixture')
            storage.append(dict(active_rows=size, before_database_bytes=old['database_bytes'],
                                after_database_bytes=new['database_bytes'],
                                before_import_setup_total_ms=old['setup_import']['total_ms'],
                                after_import_setup_total_ms=new['setup_import']['total_ms'],
                                scope=old['setup_import']['scope']))
            for scenario in LATENCY_SCENARIOS:
                row = dict(active_rows=size, scenario=scenario)
                for metric in ('median_ms', 'p95_ms'):
                    previous, current = old['measurements'][scenario][metric], new['measurements'][scenario][metric]
                    row.update({'before_' + metric: previous, 'after_' + metric: current,
                                metric + '_reduction_percent': 100 * (1 - current / previous)})
                expected.append(row)
    def identity(row):
        return (row.get('rows'),) if family == 'xlsx' else (row.get('active_rows'), row.get('scenario'))
    actual = _list(payload.get('comparisons'), len(expected))
    indexed = {}
    for row in actual:
        _mapping(row)
        key = identity(row)
        _require(key not in indexed, 'duplicate-comparison')
        indexed[key] = row
    for row in expected:
        actual_row = indexed.get(identity(row))
        _require(type(actual_row) is dict and set(actual_row) == set(row), 'comparison-set-mismatch')
        for key, value in row.items():
            if key in ('rows', 'active_rows', 'scenario'):
                _require(type(actual_row[key]) is type(value) and actual_row[key] == value, 'comparison-set-mismatch')
            else:
                _equal_number(actual_row[key], value)
    if family == 'latency':
        _require(payload.get('storage_and_setup') == storage, 'comparison-setup-mismatch')
    budgets = {} if budgets is None else _mapping(budgets)
    required = ('max_wall_regression_percent', 'max_rss_regression_percent') if family == 'xlsx' else ('max_wall_regression_percent',)
    for key in required:
        if budgets.get(key) is not None:
            _number(budgets[key], positive=False)
            _require(budgets[key] <= 1000, 'invalid-performance-budget')
    if any(budgets.get(key) is None for key in required):
        return _result('INCOMPLETE', 'performance-budgets-not-supplied', comparisons=expected)
    breaches = []
    for row in expected:
        pairs = [('median_wall_ms', required[0]), ('median_peak_rss_bytes', required[1])] if family == 'xlsx' else [('median_ms', required[0])]
        for metric, key in pairs:
            regression = -row[metric + '_reduction_percent']
            if regression > budgets[key]:
                breaches.append(dict(dataset=identity(row), metric=metric, regression_percent=regression, permitted=budgets[key]))
    return _result('FAILED' if breaches else 'PASSED',
                   'explicit-performance-budget-exceeded' if breaches else 'validated-performance-comparison',
                   comparisons=expected, budget_breaches=breaches)


def validate_performance(payload, family, action, profile, source_files, harness_sha256,
                         before=None, after=None, budgets=None):
    """Validate fixed measurement/compare evidence without executing producers.

    For a measurement, source_files is the complete trusted source fingerprint's
    files mapping. For comparison pass {'before': baseline_files, 'after': files}
    and both parsed measurements. harness_sha256 is always the measuring script's
    digest; comparison file-byte hashes are verified separately by the caller.
    """
    try:
        _require(action in ('before', 'after', 'compare'), 'invalid-performance-action')
        if action == 'compare':
            return _comparison(payload, family, profile, source_files, harness_sha256, before, after, budgets)
        sizes = _measurement(payload, family, action, profile, source_files, harness_sha256)
        return _result('PASSED', 'validated-measurement', datasets=sizes,
                       warmup_evidence='individual-validated-samples' if family == 'xlsx' else 'declared-count-only-producer-does-not-record-warmups')
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, ZeroDivisionError) as error:
        return _failure(error)


# Fixed v11 product catalogs; only acceptance tooling changes in this round.
# Canonical hashes cover full page/callback/HTTP route declarations and the
# complete (kind, entry, actor) matrix, not self-reported successful counts.
_COVERAGE = {
    False: (263, '44c5626a90e44e447f086a60f94759f7e7a3cd4cbd9972062c6104fab09e6db1',
            'e52d65988142fa224f77f5abbea56bd660157d0399920ceab79d4cfeb96f3e10'),
    True: (271, '04260ae0526ac1b32ddb0b069e9207c84f3e022f6bc0dda3a4320ae626980107',
           'b0082f6f2a02f1a89d56bf5795cd90ee07fcd53786063cca8078b13af55bc61b'),
}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True, allow_nan=False).encode('utf-8')).hexdigest()


def validate_coverage(payload, template):
    try:
        _mapping(payload)
        _require(type(template) is bool and payload.get('report_template_enabled') is template, 'wrong-coverage-profile')
        summary = _mapping(payload.get('summary'))
        for key in ('tests', 'failures', 'errors', 'checks', 'passed'):
            _integer(summary.get(key))
        _require(summary.get('browser_test') is False, 'http-is-not-browser-evidence')
        for key in ('skipped', 'expected_failures', 'unexpected_successes'):
            if key in summary:
                _integer(summary[key])
        if summary['failures'] or summary['errors']:
            return _result('FAILED', 'coverage-tests-failed')
        if summary.get('unexpected_successes', 0):
            return _result('FAILED', 'coverage-unexpected-success')
        if summary.get('skipped', 0) or summary.get('expected_failures', 0):
            return _result('INCOMPLETE', 'coverage-tests-unverified')
        _require(summary['tests'] == 5, 'coverage-method-count-mismatch')
        count, catalog_hash, matrix_hash = _COVERAGE[template]
        catalog = {key: sorted(_list(payload.get(key)), key=lambda item: json.dumps(item, sort_keys=True))
                   for key in ('pages', 'callbacks', 'http_routes')}
        _require(_digest(catalog) == catalog_hash, 'coverage-registry-mismatch')
        checks = _list(payload.get('checks'), count)
        keys, passed = [], 0
        for item in checks:
            item = _mapping(item)
            kind, entry, actor = (_text(item.get(key)) for key in ('kind', 'entry', 'actor'))
            keys.append((kind, entry, actor))
            if kind == 'rendered_page_denial':
                expected = 'login redirect' if actor == 'anonymous' else 'forbidden view'
            else:
                expected = 401 if kind in ('api_denial', 'revoked_session_denial') or (
                    kind == 'callback_denial' and actor == 'anonymous') else 403
                _require(type(item.get('expected')) is int and item['expected'] == expected, 'coverage-expected-status-mismatch')
            actual_pass = type(item.get('status')) is type(expected) and item['status'] == expected
            _require(item.get('passed') is actual_pass, 'coverage-hidden-failure')
            passed += int(actual_pass)
        _require(len(set(keys)) == count and _digest(sorted(keys)) == matrix_hash, 'coverage-matrix-incomplete')
        _require(summary['checks'] == count and summary['passed'] == passed, 'coverage-summary-mismatch')
        return _result('PASSED' if passed == count else 'FAILED', 'validated-coverage-matrix', checks=count)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
        return _failure(error)


BROWSER_CASES = (
    '01-login-button-only', '02-maintenance-pagination', '03-maintenance-create-double-click',
    '04-maintenance-replay-supplementary', '05-same-organization-peer-readonly',
    '06-owner-update-version', '07-version-conflict-retains-draft', '08-archive-restore',
    '09-new-clears-unsaved-form', '10-tenant-b-isolation', '11-tampered-cross-tenant-id-ui-submit',
    '12-tampered-owner-ui-submit', '13-identity-revocation-ui-submit', '14-qsl-read-pagination',
    '15-cancel-create', '15-cancel-update', '15-cancel-delete', '15-cancel-upload',
    '16-qsl-create-double-click', '17-qsl-query-update-id-tamper', '18-qsl-delete-query-and-submit',
    '19-qsl-upload-submit', '20-qsl-wizard-next-previous', '21-qsl-export-csv-xlsx',
    '22-report-refresh-export', '23-etl-and-operations-render', '24-tenant-denied-qsl-etl-operations',
    '25-readonly-qsl-controls-and-etl-denial', '26-browser-history-navigation', '27-mobile-maintenance',
    '28-logout-replay-denied', '29-no-external-effects-or-page-errors',
    '30-template-login-and-query', '31-template-search-department', '32-template-pagination-and-sort',
    '33-template-filtered-csv-download', '34-template-same-organization-peer',
    '35-template-foreign-tenant-own-rows', '36-template-tampered-browser-state', '37-template-revocation-query-export',
)


def validate_browser_cases(payload):
    """Check the fixed native-browser scenario set; caller verifies source files."""
    try:
        _mapping(payload)
        _require(payload.get('mode') == 'full acceptance' and payload.get('staticTransport') == 'native browser',
                 'wrong-browser-mode')
        cases = _list(payload.get('results'))
        names, passed, failed = [], 0, 0
        for case in cases:
            case = _mapping(case)
            names.append(_text(case.get('name')))
            _require(case.get('status') in ('passed', 'failed'), 'invalid-browser-case-status')
            if case['status'] == 'passed':
                _number(case.get('milliseconds'), positive=False)
                passed += 1
            else:
                failed += 1
        _require(len(set(names)) == len(names), 'duplicate-browser-case')
        _require(type(payload.get('passed')) is int and payload['passed'] == passed
                 and type(payload.get('failed')) is int and payload['failed'] == failed, 'browser-summary-mismatch')
        if failed:
            return _result('FAILED', 'browser-scenario-failed')
        template = _mapping(payload.get('reportTemplate'))
        _require(template.get('enabled') is True and type(template.get('plannedScenarios')) is int
                 and template['plannedScenarios'] == 8, 'wrong-browser-template-profile')
        _integer(payload.get('unrunCount'))
        if set(names) != set(BROWSER_CASES) or payload.get('unrunCount') != 0 or template.get('unrunScenarios') != []:
            return _result('INCOMPLETE', 'browser-scenarios-unverified')
        for key in ('pageErrors', 'blockedExternalOrigins', 'networkFailures', 'consoleErrors'):
            _list(payload.get(key))
        if payload['pageErrors'] or payload['blockedExternalOrigins']:
            return _result('FAILED', 'browser-runtime-error')
        if any(_mapping(item).get('error') != 'net::ERR_ABORTED' for item in payload['networkFailures']):
            return _result('FAILED', 'browser-network-error')
        if any(_mapping(item).get('expectedAuthorizationDenial') is not True for item in payload['consoleErrors']):
            return _result('FAILED', 'browser-console-error')
        fixture = _mapping(payload.get('fixture'))
        if (payload.get('serverStopped') is not True or payload.get('syntheticStateRemoved') is not True
                or fixture.get('schedulerStarted') is not False or fixture.get('syntheticOnly') is not True
                or fixture.get('importedEntry') != 'app.py'):
            return _result('BLOCKED', 'browser-fixture-cleanup-unconfirmed')
        for key in ('browser', 'python', 'node'):
            _text(payload.get(key))
        return _result('PASSED', 'validated-native-browser-cases', cases=len(cases))
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
        return _failure(error)
