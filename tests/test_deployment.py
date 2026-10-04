"""Offline deployment drills with real synthetic stores and owned subprocesses."""
from contextlib import closing
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from reporting_workspace.application import create_app
from reporting_workspace.config import Settings
from reporting_workspace.deployment import (
    DeploymentError, RESTORE_MARKER, _REQUIRED, _OPTIONAL, _layout,
    _logical_digest, _read_manifest, assert_restore_reviewed, backup_set,
    configuration_preflight, liveness_status, readiness_status, restore_set,
)
from reporting_workspace.lifecycle import dispose_app
from reporting_workspace.state import StateStore


ADMIN = dict(id='demo-admin', role='admin', org='A')
RELEASE = 'synthetic-compatible-release'


def _hold_writer(path, channel):
    with closing(sqlite3.connect(path, isolation_level=None, timeout=.2)) as connection:
        connection.execute('BEGIN IMMEDIATE')
        channel.send('locked')
        if channel.poll(15):
            channel.recv()
        connection.rollback()
    channel.close()


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='deployment-synthetic-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state_path = self.root / 'source.sqlite'
        self.server = create_app(Settings(state_path=str(self.state_path)))
        self.addCleanup(dispose_app, self.server)

    def backup(self, name='backup', **kwargs):
        return backup_set(self.state_path, self.root / name, RELEASE, quiesced=True, **kwargs)

    def rows(self, path, sql):
        with closing(sqlite3.connect(str(path))) as connection:
            return connection.execute(sql).fetchall()

    def digest(self, path):
        with closing(sqlite3.connect(str(path))) as connection:
            return _logical_digest(connection)

    def stop(self):
        dispose_app(self.server)

    def test_configuration_preflight_has_no_provider_calls_files_or_secret_disclosure(self):
        before = set(self.root.iterdir())
        result = configuration_preflight({})
        self.assertEqual(result['status'], 'valid')
        self.assertEqual(set(self.root.iterdir()), before)
        secret = 'PRIVATE-CONFIGURATION-VALUE'
        result = configuration_preflight({'REPORTING_MODE': secret, 'REPORTING_SECRET_KEY': secret})
        self.assertEqual(result['status'], 'invalid')
        self.assertNotIn(secret, json.dumps(result))
        class Identity:
            is_demo = False
            def authenticate(self, *args):
                raise AssertionError('No provider calls during preflight.')
            def get_user(self, *args):
                raise AssertionError('No provider calls during preflight.')
        class Reports:
            is_demo = False
            columns = ['period', 'department', 'revenue', 'cost', 'profit']
            def rows(self, *args):
                raise AssertionError('No provider calls during preflight.')
            def export(self, *args):
                raise AssertionError('No provider calls during preflight.')
        settings = {'REPORTING_MODE': 'production', 'REPORTING_SECRET_KEY': secret * 2,
                    'REPORTING_STATE_PATH': str(self.root / 'new-production.sqlite')}
        self.assertEqual(configuration_preflight(settings)['status'], 'invalid')
        self.assertEqual(configuration_preflight(settings, Identity(), Reports())['status'], 'valid')
        self.assertFalse((self.root / 'new-production.sqlite').exists())

    def test_liveness_and_readiness_preserve_healthy_contract_without_starting_workers(self):
        self.assertEqual(liveness_status(self.server), ({'status': 'ok', 'mode': 'offline-demo'}, 200))
        self.assertEqual(readiness_status(self.server),
                         ({'status': 'ready', 'storage': 'sqlite-local', 'scheduler': 'not-started'}, 200))
        for key in ('job_monitor', 'etl_dispatch'):
            self.assertIsNone(self.server.extensions[key]._thread)
        # Explicitly start each owned synthetic worker with its jobs disabled.
        # The helper must report either worker, without starting another job.
        for key in ('job_monitor', 'etl_dispatch'):
            worker = self.server.extensions[key]
            worker.start()
            self.assertEqual(readiness_status(self.server)[0]['scheduler'], 'running', key)
            self.assertTrue(worker.stop())
            self.assertEqual(readiness_status(self.server)[0]['scheduler'], 'not-started', key)
        self.stop()
        self.assertEqual(liveness_status(self.server), ({'status': 'unavailable'}, 503))
        self.assertEqual(readiness_status(self.server), ({'status': 'unavailable'}, 503))

    def test_readiness_checks_missing_and_incompatible_siblings_without_recreating_them(self):
        etl = Path(self.server.extensions['etl_dispatch'].path)
        etl.unlink()
        self.assertEqual(readiness_status(self.server), ({'status': 'unavailable'}, 503))
        self.assertFalse(etl.exists())
        self.assertEqual(liveness_status(self.server)[1], 200)

    def test_readiness_rejects_closed_persistent_qsl_connection(self):
        self.server.extensions['qa_demo_crud'].close()
        self.assertEqual(readiness_status(self.server), ({'status': 'unavailable'}, 503))
        self.assertEqual(liveness_status(self.server)[1], 200)

    def test_readiness_rejects_closed_persistent_builder_connection(self):
        self.server.extensions['qa_demo_builder'].close()
        self.assertEqual(readiness_status(self.server), ({'status': 'unavailable'}, 503))
        self.assertEqual(liveness_status(self.server)[1], 200)

    def test_persistent_connection_probe_is_bounded_and_recovers_after_lock_release(self):
        for key in ('qa_demo_crud', 'qa_demo_builder'):
            entered, release = threading.Event(), threading.Event()
            resource = self.server.extensions[key]
            def hold():
                with resource._lock:
                    entered.set()
                    release.wait(5)
            thread = threading.Thread(target=hold)
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                started = time.monotonic()
                self.assertEqual(readiness_status(self.server), ({'status': 'unavailable'}, 503))
                self.assertLess(time.monotonic() - started, 1)
            finally:
                release.set()
                thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(readiness_status(self.server)[1], 200)

    def test_readiness_rejects_corrupt_store_without_private_diagnostics(self):
        with closing(sqlite3.connect(str(self.state_path))) as connection:
            connection.execute('PRAGMA user_version=999')
        payload, code = readiness_status(self.server)
        self.assertEqual((payload, code), ({'status': 'unavailable'}, 503))
        self.assertNotIn(str(self.root), json.dumps(payload))

    def test_readiness_exclusive_lock_fails_bounded_and_then_recovers(self):
        with closing(sqlite3.connect(str(self.state_path), isolation_level=None)) as blocker:
            blocker.execute('BEGIN EXCLUSIVE')
            started = time.monotonic()
            self.assertEqual(readiness_status(self.server)[1], 503)
            self.assertLess(time.monotonic() - started, 2)
            blocker.rollback()
        self.assertEqual(readiness_status(self.server)[1], 200)

    def test_complete_backup_restore_preserves_all_stores_claims_fences_and_enabled_schedules(self):
        registry = self.server.extensions['maintenance_registry']
        for number in (1, 2, 3):
            registry.query(ADMIN, 'fixture-{:03d}'.format(number))
        state = self.server.extensions['workspace'].state
        state.claim_job('synthetic-job', 'unknown-outcome', 'synthetic-owner')
        lease = state.acquire('synthetic-lease', 'synthetic-owner', ttl=30)
        engine = self.server.extensions['etl_dispatch']
        engine.configure(ADMIN, 'synthetic-sales-daily', True, 60)
        before = {name: self.digest(path) for name, (path, kind) in _layout(self.state_path, 'demo').items()}
        self.stop()
        manifest = self.backup()
        self.assertEqual(len(manifest['members']), len(_REQUIRED) + len(_OPTIONAL))
        self.assertEqual(manifest['absent_optional'], [])
        self.assertNotIn(str(self.root), json.dumps(manifest))
        target = self.root / 'restored'
        result = restore_set(self.root / 'backup', target, RELEASE)
        self.assertEqual(result['status'], 'restored-offline-review-required')
        for member in manifest['members']:
            self.assertEqual(self.digest(target / member['name']), before[member['name']])
        restored_state = StateStore(target / 'workspace.sqlite')
        self.assertIsNone(restored_state.claim_job('synthetic-job', 'unknown-outcome', 'other-owner'))
        self.assertEqual(restored_state.list_uncertain_jobs()[0].state, 'running')
        self.assertEqual(self.rows(target / 'workspace.sqlite', 'SELECT fencing FROM leases'), [(lease.fencing,)])
        self.assertEqual(self.rows(target / 'workspace.sqlite.etl.sqlite',
                                  "SELECT enabled FROM etl_config WHERE job_id='synthetic-sales-daily'"), [(1,)])
        self.assertTrue((target / RESTORE_MARKER).is_file())
        self.assertFalse((target / 'INCOMPLETE.json').exists())
        with self.assertRaisesRegex(ValueError, 'offline reconciliation'):
            create_app(Settings(state_path=str(target / 'workspace.sqlite')))
        # Guard runs before factory code can mutate/create restored siblings.
        for member in manifest['members']:
            self.assertEqual(self.digest(target / member['name']), before[member['name']])

    def test_backup_requires_explicit_quiescence_and_never_overwrites(self):
        self.stop()
        with self.assertRaisesRegex(DeploymentError, 'explicit_quiescence_required'):
            backup_set(self.state_path, self.root / 'backup', RELEASE)
        self.assertFalse((self.root / 'backup').exists())
        self.backup()
        manifest = (self.root / 'backup' / 'manifest.json').read_bytes()
        with self.assertRaisesRegex(DeploymentError, 'destination_exists'):
            self.backup()
        self.assertEqual((self.root / 'backup' / 'manifest.json').read_bytes(), manifest)
        with self.assertRaisesRegex(DeploymentError, 'destination_exists'):
            restore_set(self.root / 'backup', self.root, RELEASE)
        with self.assertRaisesRegex(DeploymentError, 'destination_overlaps_backup'):
            restore_set(self.root / 'backup', self.root / 'backup' / 'nested', RELEASE)

    def test_missing_required_store_and_unknown_sibling_fail_closed(self):
        self.stop()
        required = Path(str(self.state_path) + '.etl.sqlite')
        required.unlink()
        with self.assertRaisesRegex(DeploymentError, 'required_store_missing'):
            self.backup()
        self.assertFalse((self.root / 'backup').exists())

    def test_unknown_sibling_is_not_silently_omitted(self):
        self.stop()
        unexpected = Path(str(self.state_path) + '.qa') / 'unregistered.sqlite'
        unexpected.write_bytes(b'synthetic-unknown-store')
        with self.assertRaisesRegex(DeploymentError, 'unexpected_sibling_entry'):
            self.backup()

    def test_wal_mode_and_changed_constraints_are_rejected(self):
        self.stop()
        qsl = Path(str(self.state_path) + '.qa') / 'qsl.sqlite'
        with closing(sqlite3.connect(str(qsl))) as connection:
            self.assertEqual(connection.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
        with self.assertRaisesRegex(DeploymentError, 'unsupported_journal_mode'):
            self.backup(name='wal-backup')
        with closing(sqlite3.connect(str(qsl))) as connection:
            connection.execute('PRAGMA journal_mode=DELETE')
            connection.execute('DROP TRIGGER legacy_qsl_revisions_no_delete')
        with self.assertRaisesRegex(DeploymentError, 'incompatible_legacy_schema'):
            self.backup(name='changed-constraints')

    def test_writer_process_blocks_capture_and_failed_capture_releases_prior_locks(self):
        self.stop()
        context = multiprocessing.get_context('spawn')
        parent, child = context.Pipe()
        etl = str(self.state_path) + '.etl.sqlite'
        process = context.Process(target=_hold_writer, args=(etl, child))
        process.start()
        child.close()
        try:
            self.assertTrue(parent.poll(10))
            self.assertEqual(parent.recv(), 'locked')
            started = time.monotonic()
            with self.assertRaises(DeploymentError):
                self.backup()
            self.assertLess(time.monotonic() - started, 3)
            self.assertFalse((self.root / 'backup' / 'manifest.json').exists())
            self.assertTrue((self.root / 'backup' / 'INCOMPLETE.json').exists())
            # Main was locked first. Failure must release it, not strand locks.
            with closing(sqlite3.connect(str(self.state_path), isolation_level=None, timeout=.2)) as connection:
                connection.execute('BEGIN IMMEDIATE')
                connection.rollback()
            parent.send('release')
            process.join(5)
            self.assertEqual(process.exitcode, 0)
            self.backup(name='after-lock-release')
        finally:
            if process.is_alive():
                process.terminate()
            process.join(5)
            parent.close()
            process.close()

    def test_all_store_writer_locks_are_held_during_each_sqlite_backup(self):
        self.stop()
        from reporting_workspace import deployment
        original = deployment._backup_one
        probes = []
        def observed(source, destination, deadline):
            for path, kind in _layout(self.state_path, 'demo').values():
                with closing(sqlite3.connect(str(path), isolation_level=None, timeout=0)) as writer:
                    with self.assertRaises(sqlite3.OperationalError):
                        writer.execute('BEGIN IMMEDIATE')
                probes.append(kind)
            return original(source, destination, deadline)
        with patch.object(deployment, '_backup_one', side_effect=observed):
            self.backup()
        self.assertEqual(len(probes), len(_REQUIRED) ** 2)

    def test_partial_backup_and_restore_are_never_marked_complete(self):
        self.stop()
        from reporting_workspace import deployment
        original = deployment._backup_one
        copies = []
        def fail_second(source, destination, deadline):
            copies.append(str(destination))
            if len(copies) == 2:
                raise OSError('PRIVATE-SYNTHETIC-DISK-FAILURE')
            return original(source, destination, deadline)
        with patch.object(deployment, '_backup_one', side_effect=fail_second):
            with self.assertRaisesRegex(DeploymentError, '^backup_unavailable_or_incompatible$'):
                self.backup(name='partial-backup')
        partial = self.root / 'partial-backup'
        self.assertTrue((partial / 'INCOMPLETE.json').exists())
        self.assertFalse((partial / 'manifest.json').exists())
        with self.assertRaisesRegex(DeploymentError, 'incomplete_or_missing_manifest'):
            restore_set(partial, self.root / 'reject-partial', RELEASE)
        self.backup(name='complete-backup')
        copies[:] = []
        with patch.object(deployment, '_backup_one', side_effect=fail_second):
            with self.assertRaisesRegex(DeploymentError, '^restore_unavailable_or_incompatible$'):
                restore_set(self.root / 'complete-backup', self.root / 'partial-restore', RELEASE)
        self.assertTrue((self.root / 'partial-restore' / 'INCOMPLETE.json').exists())
        self.assertFalse((self.root / 'partial-restore' / 'restore-manifest.json').exists())
        with self.assertRaisesRegex(ValueError, 'offline reconciliation'):
            create_app(Settings(state_path=str(self.root / 'partial-restore' / 'workspace.sqlite')))

    def test_corruption_incompatible_version_or_missing_member_prevent_restore(self):
        self.stop()
        self.backup()
        directory = self.root / 'backup'
        file = directory / 'workspace.sqlite'
        original = file.read_bytes()
        file.write_bytes(b'not-a-sqlite-database')
        with self.assertRaisesRegex(DeploymentError, 'backup_checksum_failed'):
            restore_set(directory, self.root / 'corrupt-restore', RELEASE)
        self.assertFalse((self.root / 'corrupt-restore').exists())
        file.write_bytes(original)
        with self.assertRaisesRegex(DeploymentError, 'backup_release_mismatch'):
            restore_set(directory, self.root / 'wrong-release', 'another-release')
        manifest_file = directory / 'manifest.json'
        manifest = json.loads(manifest_file.read_text())
        manifest['storage_contracts']['state.py'] = '0' * 64
        manifest_file.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(DeploymentError, 'incompatible_backup_contract'):
            restore_set(directory, self.root / 'wrong-contract', RELEASE)

    def test_manifest_path_traversal_and_omitted_required_members_are_rejected(self):
        self.stop()
        self.backup()
        directory = self.root / 'backup'
        manifest_file = directory / 'manifest.json'
        original = json.loads(manifest_file.read_text())
        for members in (original['members'][1:], [dict(original['members'][0], name='../outside.sqlite')] + original['members'][1:]):
            altered = dict(original, members=members)
            manifest_file.write_text(json.dumps(altered))
            with self.assertRaisesRegex(DeploymentError, 'backup_member_set_invalid'):
                restore_set(directory, self.root / 'rejected-restore', RELEASE)
            self.assertFalse((self.root / 'rejected-restore').exists())

    def test_safe_rollback_uses_before_image_never_downgrades_newer_state_in_place(self):
        self.stop()
        before = self.digest(self.state_path)
        self.backup()
        with closing(sqlite3.connect(str(self.state_path))) as connection:
            connection.execute('PRAGMA user_version=999')
        with self.assertRaisesRegex(DeploymentError, 'incompatible_state_version'):
            self.backup(name='future-version')
        target = self.root / 'rollback-drill'
        restore_set(self.root / 'backup', target, RELEASE)
        self.assertEqual(self.digest(target / 'workspace.sqlite'), before)
        self.assertEqual(self.rows(self.state_path, 'PRAGMA user_version'), [(999,)])
        self.assertEqual(self.rows(target / 'workspace.sqlite', 'PRAGMA user_version'), [(3,)])
        with self.assertRaisesRegex(ValueError, 'offline reconciliation'):
            assert_restore_reviewed(target / 'workspace.sqlite')

    def test_restore_marker_is_presence_based_and_not_walked_above_state_parent(self):
        assert_restore_reviewed(None)
        marker = self.root / RESTORE_MARKER
        marker.write_text('not-json')
        with self.assertRaisesRegex(ValueError, 'offline reconciliation'):
            assert_restore_reviewed(self.state_path)
        child = self.root / 'another-isolated-directory'
        child.mkdir()
        assert_restore_reviewed(child / 'separate.sqlite')

    def test_cli_preflight_and_backup_restore_drill_use_owned_processes(self):
        self.stop()
        tool = Path(__file__).resolve().parents[1] / 'tools' / 'deployment_check.py'
        config = self.root / 'config.json'
        config.write_text('{}')
        commands = [
            ['preflight', '--config-json', str(config)],
            ['backup', '--state', str(self.state_path), '--destination', str(self.root / 'cli-backup'),
             '--release', RELEASE, '--quiesced'],
            ['restore', '--backup', str(self.root / 'cli-backup'), '--destination', str(self.root / 'cli-restore'),
             '--expected-release', RELEASE],
        ]
        for arguments in commands:
            process = subprocess.run([sys.executable, '-B', str(tool)] + arguments,
                                     capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            self.assertNotIn(str(self.root), process.stdout)
            self.assertIn(json.loads(process.stdout)['status'],
                          ('valid', 'backup-complete', 'restored-offline-review-required'))
        self.assertTrue((self.root / 'cli-restore' / RESTORE_MARKER).exists())


if __name__ == '__main__':
    unittest.main()
