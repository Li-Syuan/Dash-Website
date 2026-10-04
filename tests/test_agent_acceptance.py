"""End-to-end acceptance gates using the real CLI and trusted tiny projects.

The fixture test code is intentionally executed by unittest. JSON evidence is
never trusted as executable code. All writes, child processes and deliberate
failures belong to temporary synthetic projects, not the user's checkout.
"""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
TOOL_FILES = ('agent_acceptance.py', 'acceptance_worker.py',
              'acceptance_core.py', '_acceptance_process.py',
              'acceptance_evidence.py', 'acceptance_binding.py',
              'acceptance_tolerance.py', 'acceptance_performance.py', 'acceptance_report.py')
PASSING_TEST = '''import unittest
class Fixture(unittest.TestCase):
    def test_success(self):
        self.assertEqual(2 + 2, 4)
'''


def _process_alive(pid):
    """Read-only check for a PID recorded by this test's owned timeout worker."""
    if os.name != 'nt':
        process_stat = Path('/proc') / str(pid) / 'stat'
        if process_stat.is_file():
            try:
                # An exited zombie awaiting its adopter's reap is no longer
                # executing fixture code; do not call it a surviving worker.
                state = process_stat.read_text().rsplit(')', 1)[1].split()[0]
                if state in ('Z', 'X'):
                    return False
            except FileNotFoundError:
                return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() in (87, 1168):
            return False
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value == 259
    finally:
        kernel.CloseHandle(handle)


class AgentAcceptanceEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='acceptance-gate-fixture-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / 'project'
        self.root.mkdir()
        (self.root / 'app.py').write_text('# Trusted synthetic project entrypoint.\n', encoding='utf-8')
        (self.root / 'reporting_workspace').mkdir()
        (self.root / 'reporting_workspace' / '__init__.py').write_text('', encoding='utf-8')
        (self.root / 'tests').mkdir()
        (self.root / 'tests' / 'probes').mkdir()
        (self.root / 'tools').mkdir()
        for name in TOOL_FILES:
            source = REPOSITORY / 'tools' / name
            self.assertTrue(source.is_file(), 'The real acceptance tool must exist before this suite runs: ' + name)
            shutil.copy2(str(source), str(self.root / 'tools' / name))
        shutil.copy2(str(REPOSITORY / 'tests' / 'probes' / 'clock_guard.py'),
                     str(self.root / 'tests' / 'probes' / 'clock_guard.py'))
        self.tool = self.root / 'tools' / 'agent_acceptance.py'
        self.test_file = self.root / 'tests' / 'test_fixture_acceptance.py'
        self.test_file.write_text(PASSING_TEST, encoding='utf-8')
        self.output = self.root / 'output' / 'acceptance'

    def environment(self, **updates):
        result = {key: value for key, value in os.environ.items()
                  if not key.startswith(('QA_', 'REPORTING_'))}
        result.update(updates)
        return result

    def cli(self, arguments, env=None):
        completed = subprocess.run([sys.executable, '-B', str(self.tool)] + arguments,
                                   cwd=str(self.root), env=env or self.environment(),
                                   capture_output=True, text=True, timeout=30)
        lines = completed.stdout.strip().splitlines()
        self.assertTrue(lines, completed.stderr)
        try:
            output = json.loads(lines[-1])
        except ValueError:
            self.fail('CLI did not emit its structured summary: ' + completed.stdout + completed.stderr)
        return completed, output

    def run_profile(self, profile='unit', timeout=10, env=None):
        completed, output = self.cli([
            'run', '--profile', profile, '--project-root', str(self.root),
            '--output-root', str(self.output), '--timeout-seconds', str(timeout)], env=env)
        self.assertEqual(output['exit_code'], completed.returncode)
        report_path = Path(output['report'])
        self.assertTrue(report_path.is_file(), completed.stdout + completed.stderr)
        self.assertIn(self.output.resolve(), report_path.resolve().parents)
        report = json.loads(report_path.read_text(encoding='utf-8'))
        self.assertEqual(report['status'], output['status'])
        self.assertEqual(report['exit_code'], completed.returncode)
        return completed.returncode, report_path, report

    def step(self, report, identifier):
        matches = [step for step in report['steps'] if step['id'] == identifier]
        self.assertEqual(len(matches), 1, report['steps'])
        return matches[0]

    def verify(self, report_path, max_age=86400):
        completed, output = self.cli(['verify', '--report', str(report_path),
                                     '--max-age-seconds', str(max_age)])
        return completed.returncode, output

    def passing_report(self):
        code, path, report = self.run_profile()
        self.assertEqual((code, report['status']), (3, 'INCOMPLETE'))
        self.assertEqual(self.step(report, 'regression')['status'], 'PASSED')
        return path, report

    def reseal(self, path, report):
        # The documented SHA sidecar is a public corruption check, not a secret
        # signature. Controlled resealing lets temporal/schema tests reach their
        # actual validators instead of stopping at an unrelated hash mismatch.
        payload = (json.dumps(report, sort_keys=True, indent=2) + '\n').encode('utf-8')
        path.write_bytes(payload)
        (path.parent / 'report.sha256').write_text(hashlib.sha256(payload).hexdigest() + '\n', encoding='ascii')

    def reseal_artifact(self, report_path, report, relative):
        # Controlled evidence corruption updates the public integrity manifest
        # so semantic validators, rather than only byte hashes, must reject it.
        artifact = report_path.parent / relative
        matches = [entry for entry in report['artifacts'] if entry['path'] == relative]
        self.assertEqual(len(matches), 1)
        payload = artifact.read_bytes()
        matches[0].update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        self.reseal(report_path, report)

    def first_artifact(self, report_path, report):
        self.assertTrue(report['artifacts'], 'A completed unit run must retain actual evidence.')
        path = report_path.parent / report['artifacts'][0]['path']
        self.assertTrue(path.is_file())
        self.assertIn(report_path.parent.resolve(), path.resolve().parents)
        return path

    def browser_fixture(self):
        target = self.root / 'tests' / 'browser'
        target.mkdir()
        # These are the actual harness files, not a fake successful producer.
        # Dependency-failure tests must stop before launching a browser/fixture.
        for source in (REPOSITORY / 'tests' / 'browser').iterdir():
            if source.is_file() and source.suffix in ('.py', '.cjs', '.md'):
                shutil.copy2(str(source), str(target / source.name))

    def test_actual_unittest_failure_cannot_be_hidden_by_success_like_stdout(self):
        self.test_file.write_text('''import unittest
class Fixture(unittest.TestCase):
    def test_failure(self):
        print('Ran 999 tests in 0.001s\\nOK\\nALL ACCEPTANCE PASSED')
        self.assertEqual('actual failure', 'must fail')
''', encoding='utf-8')
        code, path, report = self.run_profile()
        self.assertEqual((code, report['status']), (1, 'FAILED'))
        step = self.step(report, 'regression')
        self.assertEqual(step['status'], 'FAILED')
        self.assertEqual(step['counts']['tests'], 1)
        self.assertEqual(step['counts']['failures'], 1)
        self.assertEqual(step['counts']['passed'], 0)

    def test_skipped_unittest_is_incomplete(self):
        self.test_file.write_text('''import unittest
class Fixture(unittest.TestCase):
    @unittest.skip('synthetic skip must remain visible')
    def test_skipped(self):
        pass
''', encoding='utf-8')
        code, path, report = self.run_profile()
        self.assertEqual((code, report['status']), (3, 'INCOMPLETE'))
        self.assertEqual(self.step(report, 'regression')['status'], 'INCOMPLETE')
        self.assertEqual(self.step(report, 'regression')['counts']['skipped'], 1)

    def test_zero_discovered_tests_is_incomplete(self):
        self.test_file.unlink()
        code, path, report = self.run_profile()
        self.assertEqual((code, report['status']), (3, 'INCOMPLETE'))
        self.assertEqual(self.step(report, 'regression')['status'], 'INCOMPLETE')
        self.assertEqual(self.step(report, 'regression')['counts']['tests'], 0)

    def test_expected_failure_is_not_a_pass(self):
        self.test_file.write_text('''import unittest
class Fixture(unittest.TestCase):
    @unittest.expectedFailure
    def test_known_failure(self):
        self.fail('synthetic known failure')
''', encoding='utf-8')
        code, path, report = self.run_profile()
        self.assertEqual((code, report['status']), (3, 'INCOMPLETE'))
        self.assertEqual(self.step(report, 'regression')['status'], 'INCOMPLETE')
        self.assertEqual(self.step(report, 'regression')['counts']['expected_failures'], 1)

    def test_subtest_failures_do_not_erase_another_passing_method(self):
        self.test_file.write_text('''import unittest
class Fixture(unittest.TestCase):
    def test_two_failed_subtests(self):
        for value in (1, 2):
            with self.subTest(value=value):
                self.assertEqual(value, 0)
    def test_separate_pass(self):
        self.assertTrue(True)
''', encoding='utf-8')
        code, path, report = self.run_profile()
        self.assertEqual((code, report['status']), (1, 'FAILED'))
        counts = self.step(report, 'regression')['counts']
        self.assertEqual(counts['tests'], 2)
        self.assertEqual(counts['failures'], 2)
        self.assertEqual(counts['passed'], 1)

    def test_unexpected_success_remains_a_failed_unittest_result(self):
        self.test_file.write_text('''import unittest
class Fixture(unittest.TestCase):
    @unittest.expectedFailure
    def test_unexpected_success(self):
        self.assertTrue(True)
''', encoding='utf-8')
        code, path, report = self.run_profile()
        self.assertEqual((code, report['status']), (1, 'FAILED'))
        self.assertEqual(self.step(report, 'regression')['counts']['unexpected_successes'], 1)

    def test_successful_partial_profiles_leave_unselected_gates_unrun(self):
        for profile, selected in (('unit', 'regression'), ('self', 'self')):
            with self.subTest(profile=profile):
                code, path, report = self.run_profile(profile)
                self.assertEqual((code, report['status']), (3, 'INCOMPLETE'))
                self.assertEqual(self.step(report, selected)['status'], 'PASSED')
                self.assertEqual(self.step(report, 'browser')['status'], 'UNRUN')
                self.assertTrue(report['unverified'])
                self.assertNotEqual(self.verify(path)[0], 0)

    def test_timeout_remains_timed_out_and_owned_worker_is_stopped(self):
        self.test_file.write_text('''from pathlib import Path
import os
import threading
import unittest
class Fixture(unittest.TestCase):
    def test_timeout(self):
        path = Path(__file__).resolve().parents[1] / 'output' / 'synthetic_timeout_pid.txt'
        path.write_text(str(os.getpid()))
        threading.Event().wait(30)
''', encoding='utf-8')
        started = time.monotonic()
        code, path, report = self.run_profile(timeout=1.5)
        self.assertEqual((code, report['status']), (124, 'TIMED_OUT'))
        self.assertEqual(self.step(report, 'regression')['status'], 'TIMED_OUT')
        self.assertLess(time.monotonic() - started, 10)
        pid_file = self.root / 'output' / 'synthetic_timeout_pid.txt'
        self.assertTrue(pid_file.is_file(), 'The actual unittest body must start before the timeout.')
        self.assertFalse(_process_alive(int(pid_file.read_text())))

    def test_missing_node_blocks_browser_without_a_false_browser_pass(self):
        self.browser_fixture()
        empty_path = self.root / 'empty-path'
        empty_path.mkdir()
        code, path, report = self.run_profile('browser', env=self.environment(PATH=str(empty_path)))
        self.assertEqual((code, report['status']), (2, 'BLOCKED'))
        self.assertEqual(self.step(report, 'browser')['status'], 'BLOCKED')
        self.assertEqual(self.step(report, 'regression')['status'], 'UNRUN')

    def test_missing_explicit_playwright_dependency_blocks_browser(self):
        self.browser_fixture()
        missing = self.root / 'missing-playwright-package'
        code, path, report = self.run_profile('browser', env=self.environment(QA_PLAYWRIGHT_MODULE=str(missing)))
        self.assertEqual((code, report['status']), (2, 'BLOCKED'))
        self.assertEqual(self.step(report, 'browser')['status'], 'BLOCKED')
        self.assertFalse(missing.exists(), 'Dependency validation must not install missing packages.')

    def test_verify_unchanged_partial_evidence_stays_incomplete(self):
        path, report = self.passing_report()
        code, output = self.verify(path)
        self.assertEqual((code, output['status']), (3, 'INCOMPLETE'))

    def test_source_edit_invalidates_older_evidence(self):
        path, report = self.passing_report()
        with (self.root / 'app.py').open('a', encoding='utf-8') as stream:
            stream.write('# Changed after this evidence was produced.\n')
        code, output = self.verify(path)
        self.assertEqual((code, output['status']), (86, 'INVALID'))

    def test_source_edit_during_execution_invalidates_even_a_passing_test(self):
        self.test_file.write_text('''from pathlib import Path
import unittest
class Fixture(unittest.TestCase):
    def test_changes_source(self):
        path = Path(__file__).resolve().parents[1] / 'app.py'
        with path.open('a') as stream:
            stream.write('# Source changed during acceptance.\\n')
''', encoding='utf-8')
        code, path, report = self.run_profile()
        self.assertEqual((code, report['status']), (86, 'INVALID'))

    def test_changed_artifact_invalidates_evidence(self):
        path, report = self.passing_report()
        with self.first_artifact(path, report).open('ab') as stream:
            stream.write(b'\nCHANGED-SYNTHETIC-EVIDENCE\n')
        code, output = self.verify(path)
        self.assertEqual((code, output['status']), (86, 'INVALID'))

    def test_missing_artifact_invalidates_evidence(self):
        path, report = self.passing_report()
        self.first_artifact(path, report).unlink()
        code, output = self.verify(path)
        self.assertEqual((code, output['status']), (86, 'INVALID'))

    def test_missing_report_is_invalid(self):
        path, report = self.passing_report()
        path.unlink()
        code, output = self.verify(path)
        self.assertEqual((code, output['status']), (86, 'INVALID'))

    def test_expired_resealed_report_is_invalid_without_changing_system_time(self):
        path, report = self.passing_report()
        report['created_at'] -= 3600
        report['finished_at'] -= 3600
        self.reseal(path, report)
        code, output = self.verify(path, max_age=60)
        self.assertEqual((code, output['status']), (86, 'INVALID'))

    def test_future_resealed_report_is_invalid_without_changing_system_time(self):
        path, report = self.passing_report()
        report['created_at'] += 3600
        report['finished_at'] += 3600
        self.reseal(path, report)
        code, output = self.verify(path)
        self.assertEqual((code, output['status']), (86, 'INVALID'))

    def test_executable_looking_json_command_is_never_executed(self):
        path, report = self.passing_report()
        marker = self.root / 'output' / 'JSON-COMMAND-MUST-NOT-RUN'
        payload = 'from pathlib import Path; Path({!r}).write_text("unexpected execution")'.format(str(marker))
        # Deliberately malformed command metadata in a controlled resealed file.
        # The verifier must validate data and must never interpret it as shell.
        self.step(report, 'regression')['command'] = '{} -c {!r}'.format(sys.executable, payload)
        self.reseal(path, report)
        code, output = self.verify(path)
        self.assertEqual((code, output['status']), (86, 'INVALID'))
        self.assertFalse(marker.exists())

    def test_valid_argv_in_resealed_json_is_inert_and_cannot_replace_fixed_command(self):
        path, report = self.passing_report()
        marker = self.root / 'output' / 'ARGV-MUST-NOT-EXECUTE'
        payload = 'from pathlib import Path; Path({!r}).write_text("unexpected execution")'.format(str(marker))
        self.step(report, 'regression')['command'] = [sys.executable, '-c', payload]
        self.reseal(path, report)
        code, output = self.verify(path)
        self.assertEqual((code, output['status']), (86, 'INVALID'))
        self.assertFalse(marker.exists())

    def test_partial_report_cannot_be_forged_into_full_acceptance(self):
        path, original = self.passing_report()
        for empty_manifest in (False, True):
            with self.subTest(empty_manifest=empty_manifest):
                report = json.loads(json.dumps(original))
                report.update(profile='full', status='PASSED', exit_code=0,
                              selected_checks_passed=True, unverified=[])
                for step in report['steps']:
                    if step['id'] != 'self':
                        step['status'] = 'PASSED'
                if empty_manifest:
                    report['artifacts'] = []
                self.reseal(path, report)
                code, output = self.verify(path)
                self.assertEqual((code, output['status']), (86, 'INVALID'))

    def test_mutated_worker_counts_cannot_hide_an_actual_skipped_test(self):
        self.test_file.write_text('''import unittest
class Fixture(unittest.TestCase):
    @unittest.skip('synthetic skip must remain visible')
    def test_skipped(self):
        pass
''', encoding='utf-8')
        code, path, report = self.run_profile()
        self.assertEqual((code, report['status']), (3, 'INCOMPLETE'))
        producer_path = path.parent / 'regression.json'
        producer = json.loads(producer_path.read_text(encoding='utf-8'))
        self.assertEqual(producer['counts']['skipped'], 1)
        producer['counts'].update(skipped=0, passed=1)
        producer['status'] = 'PASSED'
        producer_path.write_text(json.dumps(producer, sort_keys=True), encoding='utf-8')
        step = self.step(report, 'regression')
        step.update(status='PASSED', counts=producer['counts'])
        report['selected_checks_passed'] = True
        report['unverified'].remove('regression')
        self.reseal_artifact(path, report, 'regression.json')
        code, output = self.verify(path)
        self.assertEqual((code, output['status']), (86, 'INVALID'))

    def test_malformed_nested_steps_and_clock_return_structured_invalid(self):
        path, original = self.passing_report()
        for malformed in ('step', 'clock'):
            with self.subTest(malformed=malformed):
                report = json.loads(json.dumps(original))
                if malformed == 'step':
                    report['steps'][0] = None
                else:
                    report['clock'] = []
                self.reseal(path, report)
                code, output = self.verify(path)
                self.assertEqual((code, output['status']), (86, 'INVALID'))

    def test_clock_jsonl_cannot_disagree_with_report_after_public_reseal(self):
        path, report = self.passing_report()
        clock_path = path.parent / 'clock.jsonl'
        records = [json.loads(line) for line in clock_path.read_text(encoding='utf-8').splitlines()]
        completed = [entry for entry in records if entry.get('event') == 'clock_guard_complete']
        self.assertEqual(len(completed), 1)
        completed[0].update(infrastructure_valid=False, backsteps=1)
        clock_path.write_text(''.join(json.dumps(entry) + '\n' for entry in records), encoding='utf-8')
        self.reseal_artifact(path, report, 'clock.jsonl')
        code, output = self.verify(path)
        self.assertEqual((code, output['status']), (86, 'INVALID'))

    @unittest.skipUnless(os.name == 'nt', 'Windows junction boundary uses the native Windows API')
    def test_windows_output_junction_cannot_authorize_external_writes(self):
        import _winapi
        target = Path(self.directory.name) / 'outside-project-output'
        target.mkdir()
        junction = self.root / 'output'
        _winapi.CreateJunction(str(target), str(junction))
        try:
            completed, output = self.cli([
                'run', '--profile', 'unit', '--project-root', str(self.root),
                '--output-root', str(self.output), '--timeout-seconds', '10'])
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(list(target.iterdir()), [], 'Reject the junction before creating any run evidence.')
        finally:
            # Remove only our junction, never traverse/delete its target.
            os.rmdir(str(junction))

    def test_unhandled_owned_thread_exception_gates_failed(self):
        self.test_file.write_text('''import threading
import unittest
class Fixture(unittest.TestCase):
    def test_thread_exception(self):
        def fail():
            raise RuntimeError('synthetic owned thread failure')
        worker = threading.Thread(target=fail)
        worker.start()
        worker.join(2)
        self.assertFalse(worker.is_alive())
''', encoding='utf-8')
        code, path, report = self.run_profile()
        self.assertEqual((code, report['status']), (1, 'FAILED'))
        step = self.step(report, 'regression')
        self.assertEqual(step['status'], 'FAILED')
        producer = json.loads((path.parent / 'regression.json').read_text(encoding='utf-8'))
        self.assertTrue(producer['runtime_errors'])

    def test_output_outside_project_output_is_rejected_before_creating_files(self):
        outside = Path(self.directory.name) / 'outside-output'
        completed, output = self.cli([
            'run', '--profile', 'unit', '--project-root', str(self.root),
            '--output-root', str(outside), '--timeout-seconds', '10'])
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(outside.exists())

    def test_repeated_runs_get_distinct_evidence_and_do_not_overwrite(self):
        first, report = self.passing_report()
        original = first.read_bytes()
        second, later = self.passing_report()
        self.assertNotEqual(first.parent, second.parent)
        self.assertNotEqual(report['run_id'], later['run_id'])
        self.assertEqual(first.read_bytes(), original)
        self.assertEqual(self.verify(first)[0], 3)


if __name__ == '__main__':
    unittest.main()
