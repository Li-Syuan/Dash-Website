"""Read-only benchmark provenance and missing-prerequisite gates."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import agent_acceptance as api
from acceptance_core import artifact_manifest, fingerprint
from acceptance_performance import Incomplete, assess, measurement


class PerformanceBindingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='performance-provenance-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'reporting_workspace').mkdir()
        (self.root / 'tests').mkdir()
        (self.root / 'benchmarks').mkdir()
        (self.root / 'app.py').write_text('# synthetic\n', encoding='utf-8')
        (self.root / 'reporting_workspace/__init__.py').write_text('', encoding='utf-8')
        self.harness = self.root / 'benchmarks/portal_latency.py'
        self.harness.write_text('# This fixture is evidence only and must never execute.\n', encoding='utf-8')
        self.source = fingerprint(self.root)
        self.env = api.environment()
        self.directory = self.root / 'evidence'
        self.directory.mkdir()
        self.path = self.directory / 'report.json'
        self.producer = self.directory / 'latency_after.json'
        self.payload = dict(schema=1, harness_sha256=hashlib.sha256(self.harness.read_bytes()).hexdigest(),
                            source_hashes={'reporting_workspace/__init__.py': hashlib.sha256(b'').hexdigest()},
                            datasets=[{'active_rows': 5000}, {'active_rows': 25000}], samples=15, warmups=3)
        self.payload.update(python=self.env['python'], platform=self.env['platform'] + ' ' + self.env['release'],
                            machine=self.env['machine'], packages=self.env['packages'], sqlite=sqlite3.sqlite_version)
        command = [sys.executable, '-B', str(self.harness), '--source-root', str(self.root),
                   '--output', str(self.producer), '--rows', '5000', '25000', '--samples', '15',
                   '--warmups', '3', '--label', 'after']
        process = dict(returncode=0, timed_out=False, cleanup_confirmed=True, launch_error=None, elapsed_seconds=1.0)
        self.receipt = dict(schema=1, run_id='fixture-unique', step='latency_after', process=process,
                            argv=command, cwd=str(self.root))
        self.record = dict(schema=1, profile='performance', run_id='fixture-unique', created_at=time.time(), source=self.source,
                           environment=self.env, environment_after=self.env,
                           clock=dict(valid=True, backsteps=0, monitor_errors=[]),
                           steps=[dict(id='latency_after', status='PASSED', process=process,
                                       command=command, cwd=str(self.root),
                                       evidence=['latency_after.json', 'latency_after.process.json', 'latency_after.log'])])
        self.record['finished_at'] = time.time()
        wall, mono = time.time_ns(), time.monotonic_ns()
        sample = dict(realtime_ns=wall, monotonic_before_ns=mono, monotonic_after_ns=mono,
                      offset_low_ns=wall-mono, offset_high_ns=wall-mono)
        self.clock = [dict(event='clock_guard_start', tolerance_ns=1000000, sample=sample),
                      dict(event='clock_guard_complete', sample=sample, infrastructure_valid=True,
                           backsteps=0, monitor_errors=[])]
        self.write_evidence()

    def write_evidence(self):
        self.producer.write_text(json.dumps(self.payload), encoding='utf-8')
        (self.directory / 'latency_after.process.json').write_text(json.dumps(self.receipt), encoding='utf-8')
        (self.directory / 'latency_after.log').write_text('Fixture metadata only.\n', encoding='utf-8')
        (self.directory / 'clock.jsonl').write_text('\n'.join(json.dumps(row) for row in self.clock) + '\n', encoding='utf-8')
        self.record['artifacts'] = artifact_manifest(self.directory, [self.producer,
            self.directory / 'latency_after.process.json', self.directory / 'latency_after.log', self.directory / 'clock.jsonl'])
        self.path.write_text(json.dumps(self.record), encoding='utf-8')
        self.path.with_suffix('.sha256').write_text(hashlib.sha256(self.path.read_bytes()).hexdigest(), encoding='ascii')

    def load(self):
        return measurement(dict(report=str(self.path), step='latency_after'), 'latency',
                           self.source, self.root, self.env, 86400, api)

    def test_bound_record_is_read_without_running_its_program(self):
        value = self.load()
        self.assertEqual(value['sha256'], hashlib.sha256(self.producer.read_bytes()).hexdigest())
        self.assertEqual(value['run_id'], 'fixture-unique:latency_after')
        self.assertTrue(value['clock_valid'])

    def test_metadata_command_cannot_execute_or_replace_fixed_benchmark(self):
        marker = self.root / 'must-not-exist'
        malicious = [sys.executable, '-c', 'open({!r},"w").write("ran")'.format(str(marker))]
        self.receipt['argv'] = malicious
        self.record['steps'][0]['command'] = malicious
        self.write_evidence()
        with self.assertRaises(ValueError):
            self.load()
        self.assertFalse(marker.exists())

    def test_resealed_mismatched_process_receipt_is_rejected(self):
        self.receipt['process'] = dict(self.receipt['process'], returncode=1)
        self.write_evidence()
        with self.assertRaisesRegex(ValueError, 'measurement-process-mismatch'):
            self.load()

    def test_changed_raw_producer_without_resealed_manifest_is_rejected(self):
        self.producer.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'measurement-artifact-mismatch'):
            self.load()

    def test_expired_record_is_incomplete(self):
        self.record['created_at'] = time.time() - 86401
        self.write_evidence()
        with self.assertRaisesRegex(Incomplete, 'expired'):
            self.load()

    def test_changed_runtime_is_incomplete(self):
        self.record['environment'] = dict(self.env, python='3.8.999')
        self.write_evidence()
        with self.assertRaisesRegex(Incomplete, 'environment-mismatch'):
            self.load()

    def test_changed_product_source_is_incomplete(self):
        self.payload['source_hashes']['reporting_workspace/__init__.py'] = 'a' * 64
        self.write_evidence()
        with self.assertRaisesRegex(Incomplete, 'product-source-mismatch'):
            self.load()

    def test_producer_runtime_must_match_process_environment(self):
        self.payload['python'] = '3.8.999'
        self.write_evidence()
        with self.assertRaisesRegex(Incomplete, 'producer-runtime-mismatch'):
            self.load()

    def test_actual_cli_composes_bound_records_with_noise_and_regression_gate(self):
        # Complete manufactured unit fixtures, never performance measurements.
        import test_acceptance_tolerance as fixtures
        fixtures.ToleranceTests.setUpClass()
        builder = fixtures.ToleranceTests()
        records = []
        captured = time.time() - 30
        candidate_path = None
        for index in range(4):
            directory = self.root / ('synthetic-capture-' + str(index))
            directory.mkdir()
            step = 'latency_before' if index < 3 else 'latency_after'
            producer = directory / (step + '.json')
            payload = builder.run_fixture('latency', index, wall=100 if index < 3 else 135)['payload']
            for key in ('harness_sha256', 'source_hashes', 'python', 'platform', 'machine', 'packages', 'sqlite'):
                payload[key] = deepcopy(self.payload[key])
            producer.write_text(json.dumps(payload), encoding='utf-8')
            record = deepcopy(self.record)
            record.update(profile='performance-calibrated', run_id='synthetic-' + str(index),
                          created_at=captured + index * 5, finished_at=captured + index * 5 + 1,
                          baseline=self.source)
            entry = record['steps'][0]
            command = list(entry['command'])
            command[6], command[-1] = str(producer), 'before' if index < 3 else 'after'
            entry.update(id=step, command=command, evidence=[step + '.json', step + '.process.json', step + '.log'])
            receipt = dict(self.receipt, run_id=record['run_id'], step=step, argv=command)
            receipt_path = directory / (step + '.process.json')
            receipt_path.write_text(json.dumps(receipt), encoding='utf-8')
            log_path = directory / (step + '.log')
            log_path.write_text('Manufactured unit fixture; NO benchmark ran.\n', encoding='utf-8')
            clock_path = directory / 'clock.jsonl'
            clock_path.write_text((self.directory / 'clock.jsonl').read_text(encoding='utf-8'), encoding='utf-8')
            record['artifacts'] = artifact_manifest(directory, [producer, receipt_path, log_path, clock_path])
            path = directory / 'report.json'
            path.write_text(json.dumps(record), encoding='utf-8')
            path.with_suffix('.sha256').write_text(hashlib.sha256(path.read_bytes()).hexdigest(), encoding='ascii')
            if index < 3:
                records.append(dict(report=str(path), step=step))
            else:
                candidate_path = path
        policy_path, manifest_path = self.root / 'policy.json', self.root / 'calibration.json'
        policy_path.write_text(json.dumps(dict(fixtures.POLICY, approved_at=captured + 12)), encoding='utf-8')
        manifest_path.write_text(json.dumps(dict(schema=1, family='latency', records=records)), encoding='utf-8')
        output = self.root / 'output/assessment-cli.json'
        child = subprocess.run([sys.executable, '-B', str(ROOT / 'tools/agent_acceptance.py'),
            'assess-performance', '--family', 'latency', '--project-root', str(self.root),
            '--baseline', str(self.root), '--policy', str(policy_path), '--calibration', str(manifest_path),
            '--candidate-report', str(candidate_path), '--output', str(output)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
        self.assertEqual(child.returncode, 1, child.stdout)
        result = json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(result['reason'], 'approved-tolerance-exceeded')
        self.assertEqual(result['details']['baseline_runs'], 3)
        self.assertFalse(result['whole_project_verified'])
        self.assertFalse(result['command_replayed'])

    def test_invalid_clock_is_incomplete_without_relabeling(self):
        self.clock.insert(1, dict(event='clock_monitor_error', error_type='SyntheticClockError'))
        self.clock[-1].update(infrastructure_valid=False, monitor_errors=['SyntheticClockError'])
        self.record['clock'] = dict(valid=False, backsteps=0, monitor_errors=['SyntheticClockError'])
        self.write_evidence()
        with self.assertRaisesRegex(Incomplete, 'clock-invalid'):
            self.load()

    def test_missing_configuration_creates_nonpassing_machine_result(self):
        output = self.root / 'output/assessment.json'
        args = SimpleNamespace(project_root=self.root, output=output, family='latency', policy=None,
                               calibration=None, candidate_report=None, baseline=None, max_age_seconds=86400)
        result = assess(args, api)
        self.assertEqual((result['status'], result['exit_code']), ('INCOMPLETE', 3))
        self.assertFalse(json.loads(output.read_text())['whole_project_verified'])
        self.assertTrue(output.with_suffix('.sha256').is_file())

    def test_assessment_cannot_overwrite_or_escape_output(self):
        for output in (self.root / 'outside.json', self.root / 'app.py'):
            with self.subTest(output=output):
                args = SimpleNamespace(project_root=self.root, output=output)
                with self.assertRaises(ValueError):
                    assess(args, api)


if __name__ == '__main__':
    unittest.main()
