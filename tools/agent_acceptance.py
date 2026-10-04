"""Run fixed local acceptance profiles; evidence is data, never instructions."""
import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import shutil
import sys
import time
import uuid

from acceptance_core import artifact_manifest, fingerprint, run_process, verify_artifacts, safe_output_path


TOOL_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 1
EXIT = {'PASSED': 0, 'FAILED': 1, 'BLOCKED': 2, 'INCOMPLETE': 3,
        'INVALID': 86, 'TIMED_OUT': 124}
REQUIRED = ('regression', 'authorization', 'coverage_default', 'coverage_template',
            'browser', 'latency_before', 'latency_after', 'latency_compare',
            'xlsx_before', 'xlsx_after', 'xlsx_compare')
PERFORMANCE = REQUIRED[5:]
PROFILES = {'full': REQUIRED, 'unit': ('regression',), 'self': ('self',),
            'authorization': REQUIRED[1:4], 'browser': ('browser',),
            'performance': PERFORMANCE, 'performance-smoke': REQUIRED[5:8],
            'performance-baseline': ('latency_before', 'xlsx_before'),
            'performance-calibrated': PERFORMANCE}
PACKAGES = ('dash', 'Flask', 'Werkzeug', 'Flask-Login', 'plotly', 'openpyxl',
            'dash-bootstrap-components', 'dash-mantine-components', 'setuptools')
TOOL_FILES = ('tools/agent_acceptance.py', 'tools/acceptance_worker.py',
              'tools/acceptance_core.py', 'tools/_acceptance_process.py',
              'tools/acceptance_binding.py', 'tools/acceptance_evidence.py',
              'tools/acceptance_tolerance.py', 'tools/acceptance_performance.py',
              'tools/acceptance_report.py',
              'tests/probes/clock_guard.py')


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + '\n').encode('utf-8')


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(json_bytes(value))


def strict_json(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('invalid-json-file')

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate-json-key')
            result[key] = value
        return result

    def constant(_):
        raise ValueError('non-finite-json-number')

    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs,
                      parse_constant=constant)


def toolchain():
    return {name: hashlib.sha256((TOOL_ROOT / name).read_bytes()).hexdigest()
            for name in TOOL_FILES}


def environment():
    packages = {}
    for name in PACKAGES:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return dict(python=platform.python_version(), implementation=platform.python_implementation(),
                platform=platform.system(), release=platform.release(), machine=platform.machine(),
                packages=packages)


def controlled_environment():
    # Explicit local tool paths are configuration from the operator, not from
    # an evidence file. No provider/module/command is read from a result JSON.
    retained = {name: os.environ[name] for name in ('QA_PLAYWRIGHT_MODULE', 'QA_BROWSER_CHANNEL')
                if name in os.environ}
    result = {key: value for key, value in os.environ.items()
              if not key.startswith(('REPORTING_', 'QA_')) and key not in ('PYTHONPATH', 'PYTHONHOME')}
    result.update(retained)
    result.update(PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1',
                  QA_PYTHON=sys.executable, QA_REPORT_TEMPLATE='1',
                  QA_STATIC_BRIDGE='0', QA_HTTP10='0', QA_HEADED='0')
    return result


def aggregate(steps, invalid=False):
    statuses = {item['status'] for item in steps}
    if invalid or 'INVALID' in statuses:
        return 'INVALID'
    for status in ('TIMED_OUT', 'FAILED', 'BLOCKED'):
        if status in statuses:
            return status
    indexed = {item['id']: item for item in steps}
    if any(indexed.get(name, {}).get('status') != 'PASSED' for name in REQUIRED):
        return 'INCOMPLETE'
    return 'PASSED'


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def runtime_log_errors(path):
    content = path.read_text(encoding='utf-8', errors='replace')
    return [marker for marker in ('Exception ignored in:', 'Exception in thread ', 'Fatal Python error:')
            if marker in content]


def worker_status(payload, suite, run_id):
    if not isinstance(payload, dict) or payload.get('schema') != 1:
        raise ValueError('invalid-worker-schema')
    if payload.get('producer') != 'acceptance_worker' or payload.get('run_id') != run_id or payload.get('suite') != suite:
        raise ValueError('wrong-worker-invocation')
    counts = payload.get('counts', {})
    if not isinstance(counts, dict):
        raise ValueError('invalid-worker-counts')
    for key in ('tests', 'passed', 'failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes'):
        if type(counts.get(key)) is not int or counts[key] < 0:
            raise ValueError('invalid-worker-counts')
    if counts['passed'] > counts['tests'] or not isinstance(payload.get('runtime_errors'), list):
        raise ValueError('invalid-worker-counts')
    lists = ('started_test_ids', 'successful_test_ids', 'failed_test_ids', 'failed_parent_ids',
             'expected_failure_ids', 'unexpected_success_ids', 'skipped')
    if any(not isinstance(payload.get(key), list) for key in lists):
        raise ValueError('missing-worker-method-evidence')
    for key in lists[:-1]:
        if any(not isinstance(value, str) or not value for value in payload[key]):
            raise ValueError('invalid-worker-method-evidence')
    if any(not isinstance(item, dict) or not isinstance(item.get('test'), str) or
           not isinstance(item.get('reason'), str) for item in payload['skipped']):
        raise ValueError('invalid-worker-skip-evidence')
    expected_counts = {'tests': len(payload['started_test_ids']),
                       'passed': len(payload['successful_test_ids']),
                       'skipped': len(payload['skipped']),
                       'expected_failures': len(payload['expected_failure_ids']),
                       'unexpected_successes': len(payload['unexpected_success_ids']),
                       'failed_methods': len(set(payload['failed_parent_ids']))}
    if any(counts.get(key) != value for key, value in expected_counts.items()):
        raise ValueError('worker-counts-disagree-with-methods')
    if len(payload['failed_test_ids']) != counts['failures'] + counts['errors'] or len(payload['failed_parent_ids']) != len(payload['failed_test_ids']):
        raise ValueError('worker-failures-disagree-with-methods')
    failed = counts['failures'] + counts['errors'] + counts['unexpected_successes'] or payload['runtime_errors']
    incomplete = counts['tests'] == 0 or counts['skipped'] or counts['expected_failures']
    status = 'FAILED' if failed else 'INCOMPLETE' if incomplete else 'PASSED'
    if payload.get('status') != status:
        raise ValueError('inconsistent-worker-status')
    if status == 'PASSED' and counts['passed'] != counts['tests']:
        raise ValueError('inconsistent-worker-pass-count')
    return status


def coverage_status(payload, template):
    if not isinstance(payload, dict) or payload.get('report_template_enabled') is not template:
        raise ValueError('wrong-coverage-profile')
    summary = payload.get('summary', {})
    for key in ('tests', 'failures', 'errors', 'checks', 'passed'):
        if type(summary.get(key)) is not int or summary[key] < 0:
            raise ValueError('invalid-coverage-counts')
    if summary['failures'] or summary['errors']:
        return 'FAILED'
    if summary['tests'] == 0 or summary['checks'] == 0:
        return 'INCOMPLETE'
    checks = payload.get('checks')
    if not isinstance(checks, list) or len(checks) != summary['checks']:
        raise ValueError('inconsistent-coverage-checks')
    if summary['passed'] != summary['checks'] or any(item.get('passed') is not True for item in checks):
        return 'FAILED'
    if summary.get('browser_test') is not False:
        raise ValueError('http-is-not-browser-evidence')
    return 'PASSED'


def browser_status(payload, project, directory):
    if payload.get('mode') != 'full acceptance' or payload.get('staticTransport') != 'native browser':
        raise ValueError('wrong-browser-mode')
    cases = payload.get('results')
    if not isinstance(cases, list) or len({item.get('name') for item in cases}) != len(cases):
        raise ValueError('invalid-browser-cases')
    passed = sum(item.get('status') == 'passed' for item in cases)
    failed = sum(item.get('status') == 'failed' for item in cases)
    if type(payload.get('passed')) is not int or type(payload.get('failed')) is not int:
        raise ValueError('invalid-browser-counts')
    if (passed, failed) != (payload['passed'], payload['failed']):
        raise ValueError('inconsistent-browser-counts')
    if failed or payload.get('pageErrors') or payload.get('blockedExternalOrigins'):
        return 'FAILED'
    if any(item.get('error') != 'net::ERR_ABORTED' for item in payload.get('networkFailures', [])):
        return 'FAILED'
    if any(item.get('expectedAuthorizationDenial') is not True for item in payload.get('consoleErrors', [])):
        return 'FAILED'
    template = payload.get('reportTemplate', {})
    if (passed != 40 or len(cases) != 40 or payload.get('unrunCount') != 0 or
            template.get('enabled') is not True or template.get('unrunScenarios') != []):
        return 'INCOMPLETE'
    fixture = payload.get('fixture', {})
    if (payload.get('serverStopped') is not True or payload.get('syntheticStateRemoved') is not True or
            fixture.get('schedulerStarted') is not False or fixture.get('syntheticOnly') is not True or
            fixture.get('importedEntry') != 'app.py' or not payload.get('browser')):
        return 'BLOCKED'
    for filename in ('source-hashes.json', 'harness-hashes.json'):
        hashes = strict_json(directory / filename)
        if not isinstance(hashes, dict) or not hashes:
            raise ValueError('missing-browser-source-hashes')
        for name, digest in hashes.items():
            relative = Path(name)
            if relative.is_absolute() or '..' in relative.parts or (project / relative).is_symlink():
                raise ValueError('unsafe-browser-source-path')
            path = (project / relative).resolve()
            if project not in path.parents or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError('stale-browser-source')
    return 'PASSED'


def source_changes(before, after):
    old, new = before['files'], after['files']
    return [dict(path=name, before=old.get(name), after=new.get(name))
            for name in sorted(set(old) | set(new)) if old.get(name) != new.get(name)]


class Session:
    def __init__(self, args):
        self.args = args
        self.project = safe_output_path(args.project_root)
        self.initial = fingerprint(self.project)
        self.tool_initial = toolchain()
        self.environment_initial = environment()
        self.env = controlled_environment()
        self.run_id = uuid.uuid4().hex
        output = safe_output_path(args.output_root or (self.project / 'output/agent-acceptance'))
        allowed = safe_output_path(self.project / 'output')
        if output != allowed and allowed not in output.parents:
            raise ValueError('output-must-be-below-project-output')
        output.mkdir(parents=True, exist_ok=True)
        self.directory = output / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + self.run_id[:12])
        self.directory.mkdir()
        self.started = time.time()
        self.baseline = safe_output_path(args.baseline) if args.baseline else None
        self.baseline_source = fingerprint(self.baseline) if self.baseline else None
        self.selected = PROFILES[args.profile]
        self.steps = [dict(id=name, status='UNRUN', reason='not-selected', command=[],
                           counts=None, process=None, evidence=[]) for name in ('self',) + REQUIRED]
        self.invalid_reasons = []

    def step(self, name):
        return next(item for item in self.steps if item['id'] == name)

    def unchanged(self):
        return environment() == self.environment_initial and fingerprint(self.project) == self.initial and toolchain() == self.tool_initial and (
            self.baseline is None or fingerprint(self.baseline) == self.baseline_source)

    def command(self, step, argv):
        step['command'] = list(argv)
        step['cwd'] = str(self.project)
        log = self.directory / (step['id'] + '.log')
        step['evidence'].append(log.name)
        result = run_process(argv, self.project, self.env, log, self.args.timeout_seconds)
        step['process'] = result
        receipt = self.directory / (step['id'] + '.process.json')
        save(receipt, dict(schema=1, run_id=self.run_id, step=step['id'],
                           argv=list(argv), cwd=str(self.project), process=result,
                           timeout_seconds=self.args.timeout_seconds))
        step['evidence'].append(receipt.name)
        if not result['cleanup_confirmed']:
            step.update(status='BLOCKED', reason='owned-process-cleanup-unconfirmed')
            return False
        if result['timed_out']:
            step.update(status='TIMED_OUT', reason='step-deadline-exceeded')
            return False
        if result['launch_error']:
            step.update(status='BLOCKED', reason='tool-launch-failed')
            return False
        return True

    def python(self, script, *arguments):
        return [sys.executable, '-B', str(script)] + [str(value) for value in arguments]

    def unit(self, step, suite):
        output = self.directory / (step['id'] + '.json')
        command = self.python(TOOL_ROOT / 'tools/acceptance_worker.py', '--suite', suite,
                              '--project-root', self.project, '--output', output, '--run-id', self.run_id)
        if not self.command(step, command):
            return
        payload = strict_json(output)
        status = worker_status(payload, suite, self.run_id)
        if step['process']['returncode'] != EXIT[status]:
            raise ValueError('worker-exit-result-mismatch')
        step['runtime_log_errors'] = runtime_log_errors(self.directory / (step['id'] + '.log'))
        if step['runtime_log_errors']:
            status = 'FAILED'
        step.update(status=status, reason='unittest-result', counts=payload['counts'])
        step['evidence'].append(output.name)

    def coverage(self, step, template):
        output = self.directory / (step['id'] + '.json')
        command = self.python(self.project / 'tests/test_entrypoint_coverage.py', '--write-coverage', output)
        if template:
            command.append('--report-template')
        if not self.command(step, command):
            return
        payload = strict_json(output)
        from acceptance_evidence import validate_coverage
        status = validate_coverage(payload, template)['status']
        if status == 'INVALID':
            raise ValueError('invalid-coverage-matrix')
        if step['process']['returncode'] != (0 if status == 'PASSED' else 1):
            raise ValueError('coverage-exit-result-mismatch')
        step.update(status=status, reason='registered-http-policy-matrix', counts=payload['summary'])
        step['evidence'].append(output.name)

    def browser(self, step):
        if os.name != 'nt':
            step.update(status='BLOCKED', reason='native-browser-owned-cleanup-supported-on-windows-only')
            return
        node = shutil.which('node', path=self.env.get('PATH'))
        module = self.env.get('QA_PLAYWRIGHT_MODULE')
        channel = self.env.get('QA_BROWSER_CHANNEL', 'chrome')
        if not node or (module is not None and not Path(module).is_dir()) or channel not in ('chrome', 'msedge', 'chromium'):
            step.update(status='BLOCKED', reason='browser-tool-configuration-unavailable')
            return
        parent = self.project / 'output/playwright/v11'
        previous = set(parent.iterdir()) if parent.exists() else set()
        ran = self.command(step, [node, str(self.project / 'tests/browser/acceptance.cjs')])
        created = [path for path in parent.iterdir() if path.is_dir() and path not in previous] if parent.exists() else []
        if len(created) != 1:
            if ran:
                step.update(status='BLOCKED', reason='browser-created-no-unique-run')
            return
        directory = created[0]
        destination = self.directory / 'browser'
        destination.mkdir()
        allowed = {'.json', '.log', '.txt', '.png', '.csv', '.xlsx'}
        for path in sorted(directory.rglob('*')):
            if path.is_file() and path.suffix in allowed and 'state' not in path.relative_to(directory).parts:
                # Check for symlinks and escape before copying producer evidence.
                artifact_manifest(directory, [path])
                target = destination / path.relative_to(directory)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        step['browser_original_directory'] = directory.relative_to(self.project).as_posix()
        if not ran:
            return
        if not (destination / 'results.json').is_file():
            step.update(status='BLOCKED', reason='browser-result-missing')
            return
        payload = strict_json(destination / 'results.json')
        status = browser_status(payload, self.project, destination)
        from acceptance_evidence import validate_browser_cases
        catalog = validate_browser_cases(payload)
        if catalog['status'] != 'PASSED':
            status = catalog['status']
        if status == 'PASSED' and step['process']['returncode'] != 0:
            raise ValueError('browser-exit-result-mismatch')
        step.update(status=status, reason='actual-native-browser', counts=dict(
            passed=payload.get('passed'), failed=payload.get('failed'), unrun=payload.get('unrunCount')),
            browser_version=payload.get('browser'), browser_python=payload.get('python'), browser_node=payload.get('node'))
        step['evidence'].append('browser/results.json')

    def performance(self, step):
        name = step['id']
        if self.baseline is None:
            step.update(status='BLOCKED', reason='explicit-trusted-baseline-required')
            return
        family, action = name.split('_', 1)
        script = 'large_xlsx.py' if family == 'xlsx' else 'portal_latency.py'
        output = self.directory / (name + '.json')
        if action == 'compare':
            before_step, after_step = self.step(family + '_before'), self.step(family + '_after')
            if before_step['status'] != 'PASSED' or after_step['status'] != 'PASSED':
                step.update(status='BLOCKED', reason='current-measurements-unavailable')
                return
            before, after = [self.directory / (family + '_' + label + '.json') for label in ('before', 'after')]
            comparer = 'compare_large_xlsx.py' if family == 'xlsx' else 'compare_latency.py'
            command = self.python(self.project / 'benchmarks' / comparer, before, after, '--output', output)
        else:
            source = self.baseline if action == 'before' else self.project
            rows = (100000, 200000) if family == 'xlsx' else ((1000,) if self.args.profile == 'performance-smoke' else (5000, 25000))
            samples, warmups = (3, 1) if family == 'xlsx' or self.args.profile == 'performance-smoke' else (15, 3)
            if family == 'xlsx' and self.args.profile in ('performance-baseline', 'performance-calibrated'):
                samples = 7
            command = self.python(self.project / 'benchmarks' / script, '--source-root', source,
                                  '--output', output, '--rows', *rows, '--samples', samples,
                                  '--warmups', warmups, '--label', action)
        if not self.command(step, command):
            return
        if step['process']['returncode'] != 0:
            step.update(status='FAILED', reason='benchmark-process-failed')
            return
        payload = strict_json(output)
        step['evidence'].append(output.name)
        from acceptance_binding import performance_result
        verdict = performance_result(payload, family, action, self.args.profile,
                                     self.initial, self.baseline_source, self.directory,
                                     self.project, dict(max_wall_regression_percent=self.args.max_wall_regression_percent,
                                                        max_rss_regression_percent=self.args.max_rss_regression_percent), strict_json)
        step.update(status=verdict['status'], reason=verdict['reason'],
                    measurement=verdict['details'])

    def execute(self):
        guard_path = TOOL_ROOT / 'tests/probes/clock_guard.py'
        spec = importlib.util.spec_from_file_location('acceptance_readonly_clock_guard', str(guard_path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with (self.directory / 'clock.jsonl').open('x', encoding='utf-8') as clock_log:
            guard = module.ClockGuard(clock_log).start()
            try:
                for name in self.selected:
                    step = self.step(name)
                    step['reason'] = 'selected-not-started'
                    if not self.unchanged():
                        self.invalid_reasons.append('source-changed-during-run')
                        break
                    print(json.dumps(dict(event='step-start', id=name, run_id=self.run_id)), flush=True)
                    try:
                        if name in ('self', 'regression', 'authorization'):
                            self.unit(step, {'self': 'self', 'regression': 'full', 'authorization': 'authorization'}[name])
                        elif name.startswith('coverage_'):
                            self.coverage(step, name.endswith('template'))
                        elif name == 'browser':
                            self.browser(step)
                        else:
                            self.performance(step)
                    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                        step.update(status='INVALID', reason='invalid-or-missing-producer-evidence',
                                    error_type=type(error).__name__)
                    print(json.dumps(dict(event='step-complete', id=name, status=step['status'])), flush=True)
                    if guard.invalid:
                        self.invalid_reasons.append('clock-environment-invalid')
                        break
            finally:
                valid = guard.finish()
        if not valid:
            self.invalid_reasons.append('clock-environment-invalid')
        if not self.unchanged():
            self.invalid_reasons.append('source-changed-during-run')
        return self.finish(dict(valid=valid, backsteps=len(guard.backsteps), monitor_errors=guard.monitor_errors))

    def finish(self, clock):
        status = aggregate(self.steps, bool(self.invalid_reasons))
        selected_passed = not self.invalid_reasons and all(self.step(name)['status'] == 'PASSED' for name in self.selected)
        files = [path for path in self.directory.rglob('*') if path.is_file()]
        report = dict(schema=SCHEMA, run_id=self.run_id, profile=self.args.profile,
                      created_at=self.started, finished_at=time.time(),
                      source=self.initial, toolchain=self.tool_initial, environment=self.environment_initial,
                      environment_after=environment(),
                      baseline=self.baseline_source,
                      source_changes=source_changes(self.baseline_source, self.initial) if self.baseline_source else [],
                      steps=self.steps, artifacts=artifact_manifest(self.directory, files),
                      status=status, exit_code=EXIT[status], selected_checks_passed=selected_passed,
                      unverified=[name for name in REQUIRED if self.step(name)['status'] != 'PASSED'],
                      invalid_reasons=sorted(set(self.invalid_reasons)), clock=clock,
                      performance_budgets=dict(max_wall_regression_percent=self.args.max_wall_regression_percent,
                                               max_rss_regression_percent=self.args.max_rss_regression_percent),
                      retry_count=0, side_effects=dict(company_services=False, external_api=False, push=False, deploy=False),
                      interpretation='Local trusted-source consistency evidence, not signed attestation or company deployment certification.')
        path = self.directory / 'report.json'
        from acceptance_binding import validate_report_evidence
        try:
            validate_report_evidence(report, self.directory, self.project, self.baseline, sys.modules[__name__])
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            report['invalid_reasons'].append('evidence-binding:' + str(error))
            status = 'INVALID'
            selected_passed = False
            report.update(status=status, exit_code=86, selected_checks_passed=False)
        save(path, report)
        (self.directory / 'report.sha256').write_text(hashlib.sha256(path.read_bytes()).hexdigest() + '\n', encoding='ascii')
        lines = ['# Agent acceptance: ' + status, '', 'Profile: `' + self.args.profile + '`; exit code: `' + str(EXIT[status]) + '`.',
                 'Selected checks passed: ' + str(selected_passed) + '. Whole-project acceptance: ' + ('PASSED' if status == 'PASSED' else 'NOT VERIFIED') + '.',
                 '', '| Check | Result | Reason |', '| --- | --- | --- |']
        lines += ['| {} | {} | {} |'.format(item['id'], item['status'], item['reason']) for item in self.steps]
        lines += ['', 'Runtime: Python {} on {} {}; {} seconds total.'.format(
            report['environment']['python'], report['environment']['platform'], report['environment']['machine'],
            round(report['finished_at'] - report['created_at'], 3)),
            'Source digest: `' + self.initial['digest'] + '`.',
            'Changed program files against baseline: {}.'.format(len(report['source_changes']))]
        for item in self.steps:
            if item.get('counts'):
                lines += ['', '`{}` counts: `{}`.'.format(item['id'], json.dumps(item['counts'], sort_keys=True))]
        for family in ('latency', 'xlsx'):
            comparison = self.directory / (family + '_compare.json')
            if comparison.is_file() and self.step(family + '_compare').get('measurement') is not None:
                payload = strict_json(comparison)
                rows = payload.get('comparisons', [])
                if family == 'xlsx':
                    lines += ['', '| XLSX rows | Before ms | After ms | Before peak RSS MiB | After peak RSS MiB |',
                              '| --- | --- | --- | --- | --- |']
                    for row in rows:
                        lines.append('| {} | {:.3f} | {:.3f} | {:.3f} | {:.3f} |'.format(
                            row['rows'], row['before_median_wall_ms'], row['after_median_wall_ms'],
                            row['before_median_peak_rss_bytes'] / (1024 * 1024),
                            row['after_median_peak_rss_bytes'] / (1024 * 1024)))
                else:
                    lines += ['', '| Active rows | Scenario | Before median ms | After median ms |',
                              '| --- | --- | --- | --- |']
                    for row in rows:
                        lines.append('| {} | {} | {} | {} |'.format(row['active_rows'], row['scenario'],
                            row['before_median_ms'], row['after_median_ms']))
        lines += ['',
                  'Every command, count, environment, raw artifact hash and performance comparison is in report.json.',
                  'UNRUN, skipped tests, expected failures, absent performance budgets and invalid clocks never mean all passed.',
                  'No automatic retry, dependency installation, company service, external model/API, push or deployment.']
        if report['invalid_reasons']:
            lines += ['', 'Invalid evidence: ' + ', '.join(sorted(set(report['invalid_reasons']))) + '.']
        (self.directory / 'SUMMARY.md').write_bytes(('\n'.join(lines) + '\n').encode('utf-8'))
        return path, report


def verify(args):
    path = args.report.absolute()
    try:
        report = strict_json(path)
        seal = path.with_suffix('.sha256')
        if seal.is_symlink() or seal.read_text(encoding='ascii').strip() != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError('report-digest-mismatch')
        if not isinstance(report, dict) or report.get('schema') != SCHEMA or report.get('profile') not in PROFILES:
            raise ValueError('unsupported-report-schema-or-profile')
        now = time.time()
        created, finished = report.get('created_at'), report.get('finished_at')
        if not finite(created) or not finite(finished) or finished < created or created > now or finished > now:
            raise ValueError('invalid-or-future-evidence-time')
        if now - created > args.max_age_seconds:
            raise ValueError('expired-evidence')
        if report.get('source') != fingerprint(args.project_root.resolve()):
            raise ValueError('source-changed-since-evidence')
        if report.get('toolchain') != toolchain() or report.get('environment') != environment():
            raise ValueError('tool-or-runtime-changed-since-evidence')
        if report.get('baseline') is not None:
            if args.baseline is None or fingerprint(args.baseline.resolve()) != report['baseline']:
                raise ValueError('explicit-current-baseline-required-or-changed')
        failures = verify_artifacts(path.parent, report.get('artifacts'))
        if failures:
            raise ValueError('artifact-check-failed:' + failures[0])
        steps = report.get('steps')
        if not isinstance(steps, list) or any(not isinstance(item, dict) for item in steps) or [item.get('id') for item in steps] != list(('self',) + REQUIRED):
            raise ValueError('invalid-step-registry')
        selected = PROFILES[report['profile']]
        for item in steps:
            if item.get('status') not in tuple(EXIT) + ('UNRUN',):
                raise ValueError('invalid-step-status')
            if item['id'] not in selected and item['status'] != 'UNRUN':
                raise ValueError('unselected-step-cannot-be-passed')
            if not isinstance(item.get('command'), list) or any(not isinstance(value, str) for value in item['command']):
                raise ValueError('commands-are-inert-argv-data-only')
        clock = report.get('clock', {})
        if (not isinstance(clock, dict) or type(clock.get('valid')) is not bool or type(clock.get('backsteps')) is not int or
                not isinstance(clock.get('monitor_errors'), list)):
            raise ValueError('invalid-clock-record')
        if clock['valid'] and (clock['backsteps'] or clock['monitor_errors']):
            raise ValueError('inconsistent-clock-record')
        invalid = bool(report.get('invalid_reasons')) or not clock['valid']
        status = aggregate(steps, invalid)
        if report.get('status') != status or type(report.get('exit_code')) is not int or report['exit_code'] != EXIT[status]:
            raise ValueError('inconsistent-acceptance-gate')
        unverified = [name for name in REQUIRED if next(item for item in steps if item['id'] == name)['status'] != 'PASSED']
        if report.get('unverified') != unverified:
            raise ValueError('hidden-unverified-checks')
        if not isinstance(report.get('invalid_reasons'), list) or any(not isinstance(value, str) for value in report['invalid_reasons']):
            raise ValueError('invalid-reasons-type')
        expected_selected = all(item['status'] == 'PASSED' for item in steps if item['id'] in selected) and not invalid
        if report.get('selected_checks_passed') is not expected_selected:
            raise ValueError('inconsistent-selected-checks')
        from acceptance_binding import validate_report_evidence
        validate_report_evidence(report, path.parent.resolve(), args.project_root.resolve(),
                                 args.baseline.resolve() if args.baseline else None, sys.modules[__name__])
        return dict(status=status, exit_code=EXIT[status], evidence_valid=True,
                    whole_project_verified=status == 'PASSED', report=str(path))
    except (OSError, ValueError, TypeError, KeyError, StopIteration, AttributeError) as error:
        return dict(status='INVALID', exit_code=86, evidence_valid=False,
                    reason=str(error) if isinstance(error, ValueError) else type(error).__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    run = commands.add_parser('run', help='Run a fixed profile with new evidence; never reuse old outputs')
    run.add_argument('--profile', choices=tuple(PROFILES), required=True)
    run.add_argument('--project-root', type=Path, default=TOOL_ROOT)
    run.add_argument('--output-root', type=Path)
    run.add_argument('--baseline', type=Path)
    run.add_argument('--timeout-seconds', type=float, default=1200)
    run.add_argument('--max-wall-regression-percent', type=float)
    run.add_argument('--max-rss-regression-percent', type=float)
    check = commands.add_parser('verify', help='Read-only verification; never execute report commands')
    check.add_argument('--report', type=Path, required=True)
    check.add_argument('--project-root', type=Path, default=TOOL_ROOT)
    check.add_argument('--baseline', type=Path)
    check.add_argument('--max-age-seconds', type=float, default=86400)
    performance = commands.add_parser('assess-performance', help='Read-only calibrated performance gate; never replay commands')
    performance.add_argument('--family', choices=('latency', 'xlsx'), required=True)
    performance.add_argument('--project-root', type=Path, default=TOOL_ROOT)
    performance.add_argument('--baseline', type=Path)
    performance.add_argument('--policy', type=Path)
    performance.add_argument('--calibration', type=Path)
    performance.add_argument('--candidate-report', type=Path)
    performance.add_argument('--output', type=Path, required=True)
    performance.add_argument('--max-age-seconds', type=float, default=86400)
    display = commands.add_parser('render-report', help='Create a local script-free HTML view; never execute evidence commands')
    display.add_argument('--report', type=Path)
    display.add_argument('--project-root', type=Path, default=TOOL_ROOT)
    display.add_argument('--baseline', type=Path)
    display.add_argument('--history-report', type=Path, action='append', default=[])
    display.add_argument('--output', type=Path, required=True)
    display.add_argument('--max-age-seconds', type=float, default=86400)
    args = parser.parse_args()
    if args.action in ('verify', 'assess-performance', 'render-report'):
        if not finite(args.max_age_seconds) or not 1 <= args.max_age_seconds <= 604800:
            parser.error('max-age-seconds must be 1..604800')
        if args.action == 'verify':
            result = verify(args)
        elif args.action == 'render-report':
            from acceptance_report import generate
            try:
                result = generate(args, sys.modules[__name__])
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
                result = dict(status='INVALID', exit_code=86, generated=False,
                              reason=str(error) if isinstance(error, ValueError) else type(error).__name__)
        else:
            from acceptance_performance import assess
            try:
                result = assess(args, sys.modules[__name__])
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
                result = dict(status='INVALID', exit_code=86, reason=str(error) if isinstance(error, ValueError) else type(error).__name__)
    else:
        if not finite(args.timeout_seconds) or not .05 <= args.timeout_seconds <= 3600:
            parser.error('timeout-seconds must be .05..3600 per step')
        for budget in (args.max_wall_regression_percent, args.max_rss_regression_percent):
            if budget is not None and (not finite(budget) or not 0 <= budget <= 1000):
                parser.error('Explicit regression budgets must be finite 0..1000 percent')
        try:
            session = Session(args)
            path, report = session.execute()
            result = dict(report=str(path), status=report['status'], exit_code=report['exit_code'])
        except (OSError, ValueError) as error:
            result = dict(status='BLOCKED', exit_code=2, reason=type(error).__name__)
    print(json.dumps(result, sort_keys=True), flush=True)
    return result['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
