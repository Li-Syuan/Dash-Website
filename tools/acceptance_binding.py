"""Bind local acceptance decisions to fresh producer files. Never execute metadata."""
import hashlib
import json
import sys
from pathlib import Path

try:
    from .acceptance_evidence import validate_performance, validate_coverage, validate_browser_cases
except ImportError:
    from acceptance_evidence import validate_performance, validate_coverage, validate_browser_cases


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def performance_result(payload, family, action, profile, source, baseline,
                       directory, project, budgets, read_json):
    script = 'large_xlsx.py' if family == 'xlsx' else 'portal_latency.py'
    harness = hashlib.sha256((project / 'benchmarks' / script).read_bytes()).hexdigest()
    before = after = None
    if action == 'compare':
        require(baseline is not None, 'missing-comparison-baseline')
        before_path = directory / (family + '_before.json')
        after_path = directory / (family + '_after.json')
        before, after = read_json(before_path), read_json(after_path)
        for label, path in (('before', before_path), ('after', after_path)):
            require(payload.get(label + '_sha256') == hashlib.sha256(path.read_bytes()).hexdigest(),
                    'comparison-input-hash-mismatch')
        files = {'before': baseline['files'], 'after': source['files']}
    else:
        expected = baseline if action == 'before' else source
        require(expected is not None, 'missing-measurement-baseline')
        files = expected['files']
    result = validate_performance(payload, family, action, profile, files, harness,
                                  before=before, after=after, budgets=budgets)
    require(isinstance(result, dict) and result.get('status') != 'INVALID',
            'invalid-performance-producer:' + str(result.get('reason')))
    if action == 'compare' and result['status'] in ('PASSED', 'FAILED'):
        result = dict(status='INCOMPLETE', reason='independent-calibrated-performance-evidence-required',
                      details=dict(descriptive_comparison=result['details'],
                                   next_entrypoint='assess-performance',
                                   interpretation='Numeric CLI budgets alone do not establish baseline stability.'))
    return result


def clock_record(path, read_json):
    # Use the same duplicate-key/nonfinite parser as ordinary producer files.
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, 'duplicate-clock-key')
            value[key] = item
        return value
    require(path.is_file() and not path.is_symlink() and path.stat().st_size < 16 * 1024 * 1024,
            'missing-clock-events')
    records = [json.loads(line, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite-clock')))
               for line in path.read_text(encoding='utf-8').splitlines()]
    require(len(records) >= 2 and all(isinstance(item, dict) for item in records), 'invalid-clock-events')
    require(records[0].get('event') == 'clock_guard_start' and
            records[-1].get('event') == 'clock_guard_complete', 'incomplete-clock-events')
    middle = records[1:-1]
    require(all(item.get('event') in ('clock_backstep', 'clock_monitor_error') for item in middle),
            'unexpected-clock-events')
    backsteps = [item for item in middle if item['event'] == 'clock_backstep']
    errors = [item.get('error_type') for item in middle if item['event'] == 'clock_monitor_error']
    require(all(isinstance(value, str) and value for value in errors), 'invalid-clock-errors')
    tolerance = records[0].get('tolerance_ns')
    require(type(tolerance) is int and tolerance == 1000000, 'unexpected-clock-tolerance')
    def sample(value):
        keys = ('realtime_ns', 'monotonic_before_ns', 'monotonic_after_ns', 'offset_low_ns', 'offset_high_ns')
        require(isinstance(value, dict) and all(type(value.get(key)) is int for key in keys), 'invalid-clock-sample')
        require(value['monotonic_after_ns'] >= value['monotonic_before_ns'] and
                value['offset_high_ns'] >= value['offset_low_ns'], 'invalid-clock-bounds')
        return value
    sample(records[0].get('sample'))
    sample(records[-1].get('sample'))
    for item in backsteps:
        previous, current = sample(item.get('previous')), sample(item.get('current'))
        upper = current['offset_high_ns'] - previous['offset_low_ns']
        wall = current['realtime_ns'] - previous['realtime_ns']
        require((wall < 0 or upper < -tolerance) and item.get('wall_delta_ns') == wall and
                item.get('offset_step_upper_bound_ns') == upper, 'unsubstantiated-clock-backstep')
    value = dict(valid=not (backsteps or errors), backsteps=len(backsteps), monitor_errors=errors)
    finish = records[-1]
    require(type(finish.get('infrastructure_valid')) is bool and
            finish['infrastructure_valid'] == value['valid'] and
            finish.get('backsteps') == value['backsteps'] and finish.get('monitor_errors') == errors,
            'clock-summary-disagrees-with-events')
    return value


def expected_command(name, report, directory, project, baseline, tool_root):
    py = [sys.executable, '-B']
    if name in ('self', 'regression', 'authorization'):
        suite = {'self': 'self', 'regression': 'full', 'authorization': 'authorization'}[name]
        return py + [str(tool_root / 'tools/acceptance_worker.py'), '--suite', suite,
                     '--project-root', str(project), '--output', str(directory / (name + '.json')),
                     '--run-id', report['run_id']]
    if name.startswith('coverage_'):
        command = py + [str(project / 'tests/test_entrypoint_coverage.py'), '--write-coverage',
                        str(directory / (name + '.json'))]
        return command + (['--report-template'] if name == 'coverage_template' else [])
    if name == 'browser':
        return None  # Node location is operator-controlled; only its fixed script is allowed.
    family, action = name.split('_', 1)
    if action == 'compare':
        script = 'compare_large_xlsx.py' if family == 'xlsx' else 'compare_latency.py'
        return py + [str(project / 'benchmarks' / script), str(directory / (family + '_before.json')),
                     str(directory / (family + '_after.json')), '--output', str(directory / (name + '.json'))]
    require(baseline is not None, 'explicit-baseline-path-required')
    script = 'large_xlsx.py' if family == 'xlsx' else 'portal_latency.py'
    rows = [100000, 200000] if family == 'xlsx' else ([1000] if report['profile'] == 'performance-smoke' else [5000, 25000])
    samples, warmups = (3, 1) if family == 'xlsx' or report['profile'] == 'performance-smoke' else (15, 3)
    if family == 'xlsx' and report['profile'] in ('performance-baseline', 'performance-calibrated'):
        samples = 7
    return py + [str(project / 'benchmarks' / script), '--source-root', str(baseline if action == 'before' else project),
                 '--output', str(directory / (name + '.json')), '--rows'] + [str(row) for row in rows] + [
                     '--samples', str(samples), '--warmups', str(warmups), '--label', action]


def validate_report_evidence(report, directory, project, baseline, api):
    manifest = report['artifacts']
    require(isinstance(manifest, list) and manifest, 'empty-artifact-manifest')
    names = {item['path'] for item in manifest}
    require('clock.jsonl' in names, 'clock-artifact-not-bound')
    require(report.get('clock') == clock_record(directory / 'clock.jsonl', api.strict_json),
            'clock-record-disagrees-with-events')
    require(report.get('environment_after') == report.get('environment'), 'runtime-changed-during-run')
    for step in report['steps']:
        name, status = step['id'], step['status']
        evidence = step.get('evidence')
        require(isinstance(evidence, list) and len(evidence) == len(set(evidence)) and
                all(isinstance(value, str) and value in names for value in evidence), 'unbound-step-evidence')
        process = step.get('process')
        if status == 'UNRUN':
            require(process is None and step.get('counts') is None and step['command'] == [] and not evidence,
                    'unrun-step-has-execution-claims')
            continue
        if process is None:
            require(status in ('BLOCKED', 'INVALID') and not step['command'] and not evidence and
                    step.get('counts') is None, 'missing-step-process')
            continue
        receipt_name, log_name = name + '.process.json', name + '.log'
        require(receipt_name in evidence and log_name in evidence, 'missing-process-receipt-or-log')
        receipt = api.strict_json(directory / receipt_name)
        require(isinstance(receipt, dict) and receipt.get('schema') == 1 and receipt.get('run_id') == report['run_id'] and
                receipt.get('step') == name and receipt.get('process') == process and
                receipt.get('argv') == step['command'] and receipt.get('cwd') == str(project) == step.get('cwd'),
                'process-receipt-mismatch')
        require(isinstance(process, dict) and type(process.get('timed_out')) is bool and
                type(process.get('cleanup_confirmed')) is bool and
                api.finite(process.get('elapsed_seconds')) and process['elapsed_seconds'] >= 0 and
                (type(process.get('returncode')) is int or process.get('returncode') is None) and
                (isinstance(process.get('launch_error'), str) or process.get('launch_error') is None), 'invalid-process-receipt')
        expected = expected_command(name, report, directory, project, baseline, api.TOOL_ROOT)
        if name == 'browser':
            require(len(step['command']) == 2 and Path(step['command'][0]).name.lower() in ('node', 'node.exe') and
                    step['command'][1] == str(project / 'tests/browser/acceptance.cjs'), 'non-registry-browser-command')
        else:
            require(step['command'] == expected, 'non-registry-command')
        derived = ('BLOCKED' if not process['cleanup_confirmed'] else 'TIMED_OUT' if process['timed_out']
                   else 'BLOCKED' if process['launch_error'] else None)
        if derived:
            require(status == derived, 'process-outcome-disagrees-with-gate')
            continue
        if status == 'INVALID':
            continue  # Invalid evidence is never promoted by verification.
        producer_name = 'browser/results.json' if name == 'browser' else name + '.json'
        if name in api.PERFORMANCE and process['returncode'] != 0:
            require(status == 'FAILED', 'failed-benchmark-promoted')
            continue
        if name == 'browser' and producer_name not in names:
            require(status == 'BLOCKED', 'missing-browser-result-promoted')
            continue
        require(producer_name in names and producer_name in evidence, 'producer-artifact-not-bound')
        payload = api.strict_json(directory / producer_name)
        require(isinstance(payload, dict), 'invalid-producer-object')
        if name in ('self', 'regression', 'authorization'):
            suite = {'self': 'self', 'regression': 'full', 'authorization': 'authorization'}[name]
            derived = api.worker_status(payload, suite, report['run_id'])
            require(step.get('counts') == payload['counts'] and process['returncode'] == api.EXIT[derived],
                    'worker-receipt-counts-or-exit-mismatch')
            runtime_errors = api.runtime_log_errors(directory / log_name)
            require(step.get('runtime_log_errors') == runtime_errors, 'worker-runtime-log-mismatch')
            if runtime_errors:
                derived = 'FAILED'
        elif name.startswith('coverage_'):
            verdict = validate_coverage(payload, name == 'coverage_template')
            derived = verdict['status']
            require(step.get('counts') == payload['summary'] and
                    process['returncode'] == (0 if derived == 'PASSED' else 1), 'coverage-receipt-mismatch')
        elif name == 'browser':
            require({'browser/source-hashes.json', 'browser/harness-hashes.json'} <= names, 'browser-source-artifacts-not-bound')
            derived = api.browser_status(payload, project, directory / 'browser')
            catalog = validate_browser_cases(payload)
            if catalog['status'] != 'PASSED':
                derived = catalog['status']
            require(step.get('counts') == dict(passed=payload.get('passed'), failed=payload.get('failed'),
                                              unrun=payload.get('unrunCount')), 'browser-count-mismatch')
            require(derived != 'PASSED' or process['returncode'] == 0, 'browser-exit-mismatch')
        else:
            family, action = name.split('_', 1)
            if action != 'compare':
                recorded = report['environment']
                require(payload.get('python') == recorded['python'] and
                        payload.get('machine') == recorded['machine'] and
                        payload.get('platform') == recorded['platform'] + ' ' + recorded['release'] and
                        isinstance(payload.get('packages'), dict) and bool(payload['packages']) and
                        all(recorded['packages'].get(key) == value for key, value in payload['packages'].items()),
                        'benchmark-runtime-mismatch')
            verdict = performance_result(payload, family, action, report['profile'], report['source'],
                                         report['baseline'], directory, project, report['performance_budgets'], api.strict_json)
            derived = verdict['status']
        require(status == derived, 'step-status-disagrees-with-producer:' + name)
