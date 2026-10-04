"""Real acceptance files and owned subprocess trees; no product application."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from tools.acceptance_core import (artifact_manifest, fingerprint, run_process,
                                   verify_artifacts, write_json)


class AcceptanceFilesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for name in ('reporting_workspace', 'tests', 'assets', 'tools', 'benchmarks', '.github/workflows'):
            (self.root / name).mkdir(parents=True)
        self.write('app.py', b'print("synthetic")\n')
        self.write('demo_services.py', b'OFFLINE = True\n')
        self.write('reporting_workspace/example.py', b'VALUE = 1\n')
        self.write('tests/test_example.py', b'# synthetic fixture\n')
        self.write('assets/example.js', b'// synthetic\n')
        self.write('tools/example.py', b'# tool\n')
        self.write('benchmarks/example.py', b'# benchmark\n')
        self.write('.github/workflows/test.yml', b'name: Synthetic\n')
        self.write('requirements-demo.txt', b'# approved pins unchanged\n')

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_source_fingerprint_is_canonical_relative_and_requires_no_git(self):
        result = fingerprint(self.root)
        self.assertEqual(result['schema'], 1)
        self.assertEqual(len(result['files']), 9)
        self.assertEqual(result['files']['app.py'], hashlib.sha256((self.root / 'app.py').read_bytes()).hexdigest())
        encoded = json.dumps({'schema': 1, 'files': result['files']}, sort_keys=True,
                             separators=(',', ':'), ensure_ascii=True).encode('utf-8')
        self.assertEqual(result['digest'], hashlib.sha256(encoded).hexdigest())
        self.assertEqual(result, fingerprint(self.root))
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_source_addition_change_deletion_make_a_saved_fingerprint_stale(self):
        original = fingerprint(self.root)
        self.write('tests/added.py', b'# new validation\n')
        added = fingerprint(self.root)
        self.assertNotEqual(original['digest'], added['digest'])
        self.assertIn('tests/added.py', added['files'])
        self.write('tests/added.py', b'# changed bytes\n')
        changed = fingerprint(self.root)
        self.assertNotEqual(added['digest'], changed['digest'])
        (self.root / 'tests/added.py').unlink()
        self.assertEqual(fingerprint(self.root), original)
        (self.root / 'assets/example.js').unlink()
        self.assertNotEqual(fingerprint(self.root)['digest'], original['digest'])

    def test_output_evidence_database_and_cache_are_excluded(self):
        before = fingerprint(self.root)
        for relative in ('output/huge.zip', 'tests/output/result.json', 'tests/evidence/screenshot.png',
                         'tools/__pycache__/example.pyc', 'reporting_workspace/state.sqlite',
                         'reporting_workspace/state.sqlite-wal', 'assets/cache/data.bin',
                         'benchmarks/evidence/before.json'):
            self.write(relative, b'not executable source')
        self.assertEqual(fingerprint(self.root), before)

    def test_missing_or_wrong_root_layout_is_refused(self):
        (self.root / 'app.py').unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            fingerprint(self.root)
        (self.root / 'app.py').mkdir()
        with self.assertRaises(ValueError):
            fingerprint(self.root)

    def test_real_file_and_directory_links_are_refused(self):
        target = self.write('outside.txt', b'synthetic outside payload')
        link = self.root / 'assets/link.txt'
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError) as error:
            self.skipTest('Host does not permit creation of test symlinks: ' + type(error).__name__)
        with self.assertRaises(ValueError):
            fingerprint(self.root)
        with self.assertRaises(ValueError):
            artifact_manifest(self.root, [link])
        link.unlink()
        link.symlink_to(self.root / 'reporting_workspace', target_is_directory=True)
        with self.assertRaises(ValueError):
            fingerprint(self.root)
        link.unlink()

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'FIFO fixtures require POSIX')
    def test_nonregular_fifo_is_rejected_without_blocking_or_reading(self):
        os.mkfifo(str(self.root / 'tests/not-a-file'))
        with self.assertRaises(ValueError):
            fingerprint(self.root)
        with self.assertRaises(ValueError):
            artifact_manifest(self.root, [Path('tests/not-a-file')])

    @unittest.skipUnless(os.name == 'nt', 'Windows directory junction fixture')
    def test_real_windows_junction_cannot_escape_artifact_or_source_checks(self):
        import _winapi
        if not hasattr(_winapi, 'CreateJunction'):
            self.skipTest('Interpreter does not expose its stdlib junction fixture helper')
        destination = self.root / 'assets/junction'
        _winapi.CreateJunction(str(self.root / 'reporting_workspace'), str(destination))
        try:
            with self.assertRaises(ValueError):
                fingerprint(self.root)
            with self.assertRaises(ValueError):
                artifact_manifest(self.root, [destination / 'example.py'])
            entry = {'path': 'assets/junction/example.py', 'bytes': 10, 'sha256': '0' * 64}
            self.assertEqual(verify_artifacts(self.root, [entry]), ['artifact[0]:unsafe_or_unreadable'])
        finally:
            # Remove only this junction entry, never its target directory.
            destination.rmdir()

    def test_manifest_round_trip_and_content_size_missing_tampering(self):
        path = self.write('output/raw.bin', b'\x00\xfforiginal\r\n')
        manifest = artifact_manifest(self.root, [path])
        self.assertEqual(manifest[0]['path'], 'output/raw.bin')
        self.assertEqual(verify_artifacts(self.root, manifest), [])
        path.write_bytes(b'X' * manifest[0]['bytes'])
        self.assertEqual(verify_artifacts(self.root, manifest), ['artifact[0]:hash_mismatch'])
        path.write_bytes(b'longer changed size')
        errors = verify_artifacts(self.root, manifest)
        self.assertIn('artifact[0]:hash_mismatch', errors)
        self.assertIn('artifact[0]:size_mismatch', errors)
        path.unlink()
        self.assertEqual(verify_artifacts(self.root, manifest), ['artifact[0]:missing'])

    def test_manifest_metadata_cannot_traverse_escape_duplicate_or_hide_invalid_types(self):
        good = artifact_manifest(self.root, [Path('app.py')])[0]
        for relative in ('../app.py', '/app.py', 'C:/app.py', 'a\\b', './app.py',
                         'a/../app.py', 'a//b', '', 'file:stream', 123):
            with self.subTest(path=relative):
                self.assertEqual(verify_artifacts(self.root, [dict(good, path=relative)]),
                                 ['artifact[0]:unsafe_path'])
        for value in (True, False, -1, 1.0, '1', None):
            self.assertEqual(verify_artifacts(self.root, [dict(good, bytes=value)]), ['artifact[0]:invalid_size'])
        for value in (None, '0' * 63, 'z' * 64, 'A' * 64):
            self.assertEqual(verify_artifacts(self.root, [dict(good, sha256=value)]), ['artifact[0]:invalid_hash'])
        self.assertEqual(verify_artifacts(self.root, [good, dict(good, path='APP.PY')]),
                         ['artifact[1]:duplicate_path'])
        self.assertEqual(verify_artifacts(self.root, [dict(good, command='must never execute')]),
                         ['artifact[0]:invalid_entry'])
        for invalid in ({}, '[]', None, (good,)):
            self.assertEqual(verify_artifacts(self.root, invalid), ['invalid_manifest'])
        with self.assertRaises(ValueError):
            artifact_manifest(self.root, [Path('app.py'), self.root / 'app.py'])
        with self.assertRaises(ValueError):
            artifact_manifest(self.root, 'app.py')
        with self.assertRaises(ValueError):
            artifact_manifest(self.root, [self.root.parent / 'outside.txt'])

    def test_atomic_json_preserves_existing_evidence_unless_explicitly_overwriting(self):
        path = self.root / 'result.json'
        write_json(path, {'stage': 1})
        before = path.read_bytes()
        with self.assertRaises(FileExistsError):
            write_json(path, {'stage': 2})
        self.assertEqual(path.read_bytes(), before)
        write_json(path, {'stage': 2}, overwrite=True)
        self.assertEqual(json.loads(path.read_text()), {'stage': 2})
        with self.assertRaises(ValueError):
            write_json(path, {'value': float('nan')}, overwrite=True)
        self.assertEqual(json.loads(path.read_text()), {'stage': 2})
        self.assertEqual(list(self.root.glob('.acceptance-json-*')), [])


def _process_is_running(pid):
    if os.name == 'nt':
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE, never terminate rights.
        if not handle:
            if ctypes.get_last_error() == 87:
                return False
            raise OSError('Unable to verify owned child exit.')
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class AcceptanceProcessTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.counter = 0

    def run_child(self, code=None, argv=None, timeout=10, **changes):
        self.counter += 1
        log = self.root / ('child-{}.log'.format(self.counter))
        arguments = dict(argv=argv if argv is not None else [sys.executable, '-I', '-S', '-c', code],
                         cwd=self.root, env=dict(os.environ), log_path=log, timeout_seconds=timeout)
        arguments.update(changes)
        return run_process(**arguments), log

    def test_raw_output_and_deliberate_failure_are_preserved_without_retry(self):
        result, log = self.run_child("import os;os.write(1,b'OUT\\x00\\xff\\r\\n');os.write(2,b'ERR\\n');raise SystemExit(7)")
        self.assertEqual(result['returncode'], 7)
        self.assertFalse(result['timed_out'])
        self.assertIsNone(result['launch_error'])
        self.assertTrue(result['cleanup_confirmed'])
        self.assertEqual(log.read_bytes(), b'OUT\x00\xff\r\nERR\n')
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_success_has_closed_stdin_and_no_internal_handshake_bytes(self):
        result, log = self.run_child("import sys;print(repr(sys.stdin.buffer.read()))")
        self.assertEqual(result['returncode'], 0)
        self.assertTrue(result['cleanup_confirmed'])
        self.assertEqual(log.read_text().strip(), "b''")
        self.assertEqual(list(self.root.glob('.acceptance-host-*')), [])

    def test_missing_executable_is_structured_and_sanitized(self):
        result, log = self.run_child(argv=[str(self.root / 'private-missing-executable')])
        self.assertIsNone(result['returncode'])
        self.assertEqual(result['launch_error'], 'FileNotFoundError')
        self.assertTrue(result['cleanup_confirmed'])
        self.assertNotIn('private-missing-executable', json.dumps(result))
        self.assertEqual(log.read_bytes(), b'')

    def test_shell_text_and_bad_arguments_are_rejected_before_launch(self):
        marker = self.root / 'must-not-exist'
        for argv in ('echo bad > ' + str(marker), [], [1], ['bad\x00exe'], (), ''):
            result, log = self.run_child(argv=argv)
            self.assertEqual(result['launch_error'], 'ValueError')
            self.assertIsNone(result['returncode'])
            self.assertTrue(result['cleanup_confirmed'])
            self.assertFalse(log.exists())
        self.assertFalse(marker.exists())
        for timeout in (0, -1, True, float('inf'), float('nan'), '5'):
            result, _ = self.run_child('raise SystemExit(0)', timeout=timeout)
            self.assertEqual(result['launch_error'], 'ValueError')

    def test_existing_raw_log_is_never_overwritten(self):
        path = self.root / 'existing.log'
        path.write_bytes(b'prior evidence')
        result, _ = self.run_child('raise SystemExit(0)', log_path=path)
        self.assertEqual(result['launch_error'], 'FileExistsError')
        self.assertEqual(path.read_bytes(), b'prior evidence')

    def test_timeout_terminates_owned_child_and_grandchild(self):
        marker = self.root / 'grandchild.pid'
        code = (
            "import os,pathlib,signal,subprocess,sys,time\n"
            "child=subprocess.Popen([sys.executable,'-I','-S','-c','import time;time.sleep(60)'])\n"
            "pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n"
            "def stop(*args):\n"
            "    child.terminate()\n"
            "    child.wait(timeout=3)\n"
            "    raise SystemExit(0)\n"
            "signal.signal(signal.SIGTERM,stop)\n"
            "print('ready',flush=True)\n"
            "time.sleep(60)\n")
        result, log = self.run_child(argv=[sys.executable, '-I', '-S', '-c', code, str(marker)], timeout=3)
        self.assertTrue(marker.is_file(), log.read_bytes())
        pid = int(marker.read_text())
        self.assertTrue(result['timed_out'], result)
        self.assertIsNone(result['launch_error'], result)
        self.assertTrue(result['cleanup_confirmed'], result)
        self.assertFalse(_process_is_running(pid), 'Owned grandchild survived timeout cleanup')
        self.assertIn(b'ready', log.read_bytes())

    @unittest.skipUnless(os.name == 'nt', 'Windows Job Object assignment contract')
    def test_windows_assignment_failure_never_launches_target(self):
        marker = self.root / 'must-not-launch'
        with patch('tools.acceptance_core._WindowsJob.assign', side_effect=OSError('private assignment details')):
            result, _ = self.run_child("import pathlib;pathlib.Path('must-not-launch').write_text('bad')")
        self.assertEqual(result['launch_error'], 'OSError')
        self.assertTrue(result['cleanup_confirmed'])
        self.assertFalse(marker.exists())
        self.assertNotIn('private assignment details', json.dumps(result))

    def test_unconfirmed_cleanup_is_reported_even_when_target_returns_zero(self):
        cleanup = ('tools.acceptance_core._WindowsJob.cleanup' if os.name == 'nt'
                   else 'tools.acceptance_core._cleanup_posix')
        with patch(cleanup, return_value=False):
            result, _ = self.run_child('raise SystemExit(0)')
        self.assertEqual(result['returncode'], 0)
        self.assertFalse(result['cleanup_confirmed'])

    @unittest.skipUnless(os.name == 'nt', 'Windows Job Object descendant accounting')
    def test_windows_parent_exit_does_not_leave_owned_descendant_running(self):
        marker = self.root / 'detached-from-parent.pid'
        code = ("import pathlib,subprocess,sys;"
                "child=subprocess.Popen([sys.executable,'-I','-S','-c','import time;time.sleep(60)']);"
                "pathlib.Path(sys.argv[1]).write_text(str(child.pid))")
        result, _ = self.run_child(argv=[sys.executable, '-I', '-S', '-c', code, str(marker)])
        self.assertEqual(result['returncode'], 0)
        self.assertTrue(result['cleanup_confirmed'], result)
        self.assertFalse(result['timed_out'])
        self.assertFalse(_process_is_running(int(marker.read_text())))


if __name__ == '__main__':
    unittest.main()
