"""Read-only assessment of bound local benchmark records; never replay commands."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

from acceptance_core import artifact_manifest, fingerprint, safe_output_path, verify_artifacts
from acceptance_binding import clock_record
from acceptance_evidence import _plan
from acceptance_tolerance import evaluate


class Incomplete(ValueError):
    pass


def require(value, reason):
    if not value:
        raise ValueError(reason)


def measurement(reference, family, expected_source, project, current_environment, max_age, api, baseline_capture=False):
    require(isinstance(reference, dict) and set(reference) == {'report', 'step'}, 'invalid-measurement-reference')
    report_path = safe_output_path(reference['report'], directory=False)
    if not report_path.is_file():
        raise Incomplete('baseline-or-candidate-report-unavailable')
    report = api.strict_json(report_path)
    require(isinstance(report, dict), 'invalid-measurement-report')
    seal = safe_output_path(report_path.with_suffix('.sha256'), directory=False)
    require(seal.read_text(encoding='ascii').strip() == hashlib.sha256(report_path.read_bytes()).hexdigest(), 'report-digest-mismatch')
    created = report.get('created_at')
    finished = report.get('finished_at')
    require(api.finite(created) and api.finite(finished) and finished >= created, 'invalid-evidence-time')
    now = time.time()
    if created > now or finished > now or now - created > max_age:
        raise Incomplete('future-or-expired-measurement')
    if report.get('environment') != current_environment or report.get('environment_after') != current_environment:
        raise Incomplete('measurement-environment-mismatch')
    issues = verify_artifacts(report_path.parent, report.get('artifacts'))
    require(not issues, 'measurement-artifact-mismatch:' + str(issues[:1]))
    names = {item['path'] for item in report['artifacts']}
    require('clock.jsonl' in names, 'clock-not-bound')
    clock = clock_record(report_path.parent / 'clock.jsonl', api.strict_json)
    require(clock == report.get('clock'), 'clock-report-mismatch')
    if not clock['valid']:
        raise Incomplete('clock-invalid-measurement')
    step_name = reference['step']
    require(step_name in (family + '_before', family + '_after'), 'unsupported-measurement-step')
    steps = report.get('steps')
    require(isinstance(steps, list), 'missing-measurement-steps')
    selected = [item for item in steps if isinstance(item, dict) and item.get('id') == step_name]
    require(len(selected) == 1, 'duplicate-or-missing-measurement-step')
    step = selected[0]
    if step.get('status') != 'PASSED':
        raise Incomplete('measurement-did-not-pass')
    producer_name, receipt_name, log_name = step_name + '.json', step_name + '.process.json', step_name + '.log'
    require({producer_name, receipt_name, log_name} <= names and
            {producer_name, receipt_name, log_name} <= set(step.get('evidence', [])), 'measurement-receipt-not-bound')
    receipt = api.strict_json(report_path.parent / receipt_name)
    process = receipt.get('process', {})
    require(receipt.get('run_id') == report.get('run_id') and receipt.get('step') == step_name and
            receipt.get('argv') == step.get('command') and process == step.get('process') and
            process.get('returncode') == 0 and process.get('timed_out') is False and
            process.get('cleanup_confirmed') is True and process.get('launch_error') is None,
            'measurement-process-mismatch')
    payload_path = report_path.parent / producer_name
    payload = api.strict_json(payload_path)
    require(isinstance(payload, dict), 'invalid-measurement-payload')
    expected_packages = ('openpyxl', 'Flask', 'Werkzeug', 'dash') if family == 'xlsx' else api.PACKAGES
    if (payload.get('python') != current_environment['python'] or
            payload.get('platform') != current_environment['platform'] + ' ' + current_environment['release'] or
            payload.get('machine') != current_environment['machine'] or
            payload.get('packages') != {name: current_environment['packages'][name] for name in expected_packages} or
            (family == 'latency' and payload.get('sqlite') != sqlite3.sqlite_version)):
        raise Incomplete('producer-runtime-mismatch')
    try:
        planned_rows, planned_samples, planned_warmups = _plan(family, report.get('profile'))
    except ValueError:
        raise Incomplete('unsupported-measurement-profile')
    script = 'large_xlsx.py' if family == 'xlsx' else 'portal_latency.py'
    harness_hash = hashlib.sha256((project / 'benchmarks' / script).read_bytes()).hexdigest()
    if payload.get('harness_sha256') != harness_hash:
        raise Incomplete('measurement-harness-mismatch')
    command = step.get('command')
    require(isinstance(command, list) and len(command) >= 15 and all(isinstance(value, str) for value in command),
            'invalid-measurement-command')
    require(command[0] == sys.executable and command[1] == '-B' and Path(command[2]).name == script,
            'non-registry-measurement-command')
    harness_path = safe_output_path(command[2], directory=False)
    if not harness_path.is_file() or hashlib.sha256(harness_path.read_bytes()).hexdigest() != harness_hash:
        raise Incomplete('recorded-harness-unavailable-or-changed')
    rows = [str(item.get('rows' if family == 'xlsx' else 'active_rows')) for item in payload.get('datasets', [])]
    require(rows == [str(value) for value in planned_rows] and payload.get('samples') == planned_samples and
            payload.get('warmups') == planned_warmups, 'measurement-fixed-plan-mismatch')
    expected_tail = ['--source-root', command[4], '--output', str(payload_path), '--rows'] + rows + [
        '--samples', str(payload.get('samples')), '--warmups', str(payload.get('warmups')),
        '--label', 'before' if step_name.endswith('_before') else 'after']
    require(command[3:] == expected_tail and Path(command[4]).is_absolute() and
            receipt.get('cwd') == step.get('cwd'), 'measurement-command-plan-mismatch')
    expected = {name: digest for name, digest in expected_source['files'].items()
                if name.startswith('reporting_workspace/') and name.endswith('.py')}
    if not expected or payload.get('source_hashes') != expected:
        raise Incomplete('measurement-product-source-mismatch')
    recorded_source = report.get('baseline') if step_name.endswith('_before') else report.get('source')
    require(isinstance(recorded_source, dict) and all(recorded_source.get('files', {}).get(name) == digest
                                                    for name, digest in expected.items()), 'measurement-source-receipt-mismatch')
    require(not verify_artifacts(report_path.parent, report['artifacts']), 'measurement-changed-during-read')
    require(api.strict_json(report_path) == report and
            seal.read_text(encoding='ascii').strip() == hashlib.sha256(report_path.read_bytes()).hexdigest(),
            'report-changed-during-read')
    return dict(run_id=report['run_id'] + ':' + step_name, sha256=hashlib.sha256(payload_path.read_bytes()).hexdigest(),
                payload=payload, clock_valid=True, created_at=finished if baseline_capture else created,
                capture_started_at=created, capture_finished_at=finished,
                report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest())


def assess(args, api):
    project = safe_output_path(args.project_root, directory=True)
    initial = fingerprint(project)
    output = safe_output_path(args.output)
    allowed = safe_output_path(project / 'output')
    require(allowed in output.parents and not output.exists(), 'assessment-output-must-be-new-under-project-output')
    status, reason, details = 'INCOMPLETE', 'configuration-unavailable', {}
    inputs = []
    try:
        if args.policy is None or args.calibration is None or args.candidate_report is None or args.baseline is None:
            raise Incomplete('explicit-policy-baseline-calibration-and-candidate-required')
        policy_path = safe_output_path(args.policy, directory=False)
        manifest_path = safe_output_path(args.calibration, directory=False)
        if not policy_path.is_file() or not manifest_path.is_file():
            raise Incomplete('policy-or-calibration-unavailable')
        policy = api.strict_json(policy_path)
        manifest = api.strict_json(manifest_path)
        require(isinstance(manifest, dict) and manifest.get('schema') == 1 and isinstance(manifest.get('records'), list),
                'invalid-calibration-manifest')
        require(manifest.get('family') == args.family, 'wrong-calibration-family')
        inputs = [dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                  for path in (policy_path, manifest_path)]
        baseline = fingerprint(safe_output_path(args.baseline, directory=True))
        env = api.environment()
        records = []
        for reference in manifest['records']:
            require(isinstance(reference, dict), 'invalid-calibration-reference')
            reference = dict(reference)
            supplied = Path(reference.get('report', ''))
            reference['report'] = str(supplied if supplied.is_absolute() else manifest_path.parent / supplied)
            records.append(measurement(reference, args.family, baseline, project, env, args.max_age_seconds, api,
                                       baseline_capture=True))
        candidate = measurement(dict(report=str(args.candidate_report), step=args.family + '_after'), args.family,
                                initial, project, env, args.max_age_seconds, api)
        verdict = evaluate(records, candidate, args.family, policy)
        status, reason, details = verdict['status'], verdict['reason'], verdict['details']
        details['records'] = [dict(run_id=item['run_id'], sha256=item['sha256'], report_sha256=item['report_sha256'])
                              for item in records]
        details['candidate'] = dict(run_id=candidate['run_id'], sha256=candidate['sha256'], report_sha256=candidate['report_sha256'])
        if fingerprint(project) != initial or fingerprint(safe_output_path(args.baseline, directory=True)) != baseline or api.environment() != env:
            raise ValueError('assessment-source-or-runtime-changed')
        if any(hashlib.sha256(Path(item['path']).read_bytes()).hexdigest() != item['sha256'] for item in inputs):
            raise ValueError('assessment-input-changed')
    except Incomplete as error:
        status, reason = 'INCOMPLETE', str(error)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        status, reason = 'INVALID', str(error) if isinstance(error, ValueError) else type(error).__name__
    result = dict(schema=1, producer='acceptance_performance', family=args.family, created_at=time.time(),
                  status=status, reason=reason, exit_code=api.EXIT[status], details=details, inputs=inputs,
                  source=initial, whole_project_verified=False, command_replayed=False,
                  interpretation='Local unsigned benchmark consistency and tolerance assessment only; no whole-project certification.')
    api.save(output, result)
    output.with_suffix('.sha256').write_text(hashlib.sha256(output.read_bytes()).hexdigest() + '\n', encoding='ascii')
    output.with_suffix('.md').write_text('# Performance assessment: {}\n\nFamily: {}. Exit: {}.\n\n{}\n\nNo benchmark or metadata command was executed.\n'.format(
        status, args.family, result['exit_code'], reason), encoding='utf-8')
    return dict(status=status, exit_code=result['exit_code'], reason=reason, report=str(output), whole_project_verified=False)
