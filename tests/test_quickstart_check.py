"""Fail-closed, sanitized quickstart checks using isolated synthetic local state."""
import ast
from contextlib import closing, redirect_stdout
import hashlib
from importlib import metadata
import io
import json
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import quickstart_check as check


ROOT = Path(__file__).resolve().parents[1]
PRIVATE = 'PRIVATE-CONFIGURATION-VALUE-NEVER-PRINT-THIS'


def snapshot(root):
    return {str(path.relative_to(root)): (
        path.stat().st_size, path.stat().st_mtime_ns,
        hashlib.sha256(path.read_bytes()).hexdigest())
        for path in root.rglob('*') if path.is_file()}


class QuickstartFixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='quickstart-synthetic-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.state = self.root / 'workspace.sqlite'
        self.environ = {
            'REPORTING_MODE': 'demo',
            'REPORTING_STATE_PATH': str(self.state),
            'REPORTING_ENABLE_QUALITY_ACTIONS': '1',
        }

    def run_check(self, argv=('--new-demo',), changes=None, environ=None):
        source = dict(self.environ if environ is None else environ)
        if changes:
            source.update(changes)
        stream = io.StringIO()
        with patch.dict(os.environ, source, clear=True), redirect_stdout(stream):
            code = check.main(list(argv))
        result = json.loads(stream.getvalue())
        self.assertEqual(code, 0 if result['status'] == 'passed' else 2)
        self.assertNotIn(str(self.root), stream.getvalue())
        self.assertNotIn(PRIVATE, stream.getvalue())
        return result

    def assert_blocked(self, result, code):
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['code'], code)


class QuickstartChecks(QuickstartFixture):
    def test_allowlist_matches_central_settings_not_an_independent_value_parser(self):
        tree = ast.parse((ROOT / 'reporting_workspace/config.py').read_text(encoding='utf-8'))
        keys = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                and isinstance(node.value, str) and re.fullmatch(r'REPORTING_[A-Z_]+', node.value)}
        self.assertEqual(keys, check._KNOWN_REPORTING_KEYS)

    def test_all_existing_pins_checked_including_packaging_and_xlsx(self):
        dependencies = check._dependency_checks()
        self.assertEqual(len(dependencies), 9)
        self.assertTrue(all(item['status'] == 'passed' for item in dependencies))
        self.assertEqual({item['distribution']: item['expected'] for item in dependencies}, check._pins())

    def test_missing_dependencies_stop_before_any_product_import(self):
        script = (
            'import sys; from tools import quickstart_check; '
            'code = quickstart_check.main(["--new-demo"]); '
            'print([name for name in sys.modules if name.startswith("reporting_workspace")]); '
            'sys.exit(code)'
        )
        completed = subprocess.run([sys.executable, '-B', '-S', '-c', script],
                                   cwd=str(ROOT), capture_output=True, text=True, timeout=20)
        self.assertEqual(completed.returncode, 2)
        lines = completed.stdout.splitlines()
        self.assertEqual(json.loads(lines[0])['code'], 'dependencies_unavailable_or_mismatched')
        self.assertEqual(lines[1], '[]')
        self.assertEqual(completed.stderr, '')

    def test_version_mismatch_never_echoes_metadata(self):
        real_version = metadata.version
        with patch('importlib.metadata.version', side_effect=lambda name: PRIVATE if name == 'dash' else real_version(name)):
            result = self.run_check()
        self.assert_blocked(result, 'dependencies_unavailable_or_mismatched')
        self.assertEqual(next(item['status'] for item in result['checks']['dependencies']
                              if item['distribution'] == 'dash'), 'version_mismatch')

    def test_distribution_without_module_is_blocked(self):
        with patch('importlib.util.find_spec', return_value=None):
            result = self.run_check()
        self.assert_blocked(result, 'dependencies_unavailable_or_mismatched')
        self.assertTrue(all(item['status'] == 'module_missing' for item in result['checks']['dependencies']))

    def test_unreviewed_python_minor_does_not_claim_compatibility(self):
        with patch.object(sys, 'version_info', (3, 11, 1)):
            self.assert_blocked(self.run_check(), 'supported_python_minor_required')

    def test_new_demo_passes_without_creation_or_environment_changes(self):
        before = snapshot(self.root)
        source = dict(self.environ, REPORTING_SECRET_KEY=PRIVATE)
        from reporting_workspace import deployment
        with patch.object(deployment, 'configuration_preflight', wraps=deployment.configuration_preflight) as preflight:
            result = self.run_check(environ=source)
        preflight.assert_called_once_with(source)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['scope'], 'local-demo-preflight-only')
        self.assertEqual(result['checks']['state']['stores_created'], 0)
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(self.state.exists())

    def test_explicit_mode_and_persistent_path_required(self):
        for key in ('REPORTING_MODE', 'REPORTING_STATE_PATH'):
            source = dict(self.environ)
            del source[key]
            with self.subTest(key=key):
                self.assert_blocked(self.run_check(environ=source), 'explicit_mode_and_state_path_required')

    def test_quality_actions_required_and_uses_settings_boolean_contract(self):
        for value in ('0', 'false'):
            self.assert_blocked(self.run_check(changes={'REPORTING_ENABLE_QUALITY_ACTIONS': value}),
                                'quality_actions_flag_required')
        for value in ('yes', ' 1', PRIVATE):
            self.assert_blocked(self.run_check(changes={'REPORTING_ENABLE_QUALITY_ACTIONS': value}), 'settings_invalid')
        self.assertEqual(self.run_check(changes={'REPORTING_ENABLE_QUALITY_ACTIONS': 'TRUE'})['status'], 'passed')

    def test_invalid_numeric_secret_and_path_values_are_sanitized(self):
        for key, value in (
                ('REPORTING_MAX_CONTENT_LENGTH', '-1'),
                ('REPORTING_SESSION_LIFETIME_SECONDS', '0'),
                ('REPORTING_SESSION_COOKIE_SECURE', PRIVATE),
                ('REPORTING_STATE_PATH', 'relative-' + PRIVATE),
                ('REPORTING_STATE_PATH', '//server/' + PRIVATE),
                ('REPORTING_SECRET_KEY', '')):
            self.assert_blocked(self.run_check(changes={key: value}), 'settings_invalid')

    def test_unknown_reporting_key_rejected_but_unrelated_env_ignored(self):
        self.assert_blocked(self.run_check(changes={'REPORTING_ENABLE_QUALITY_ACTION': PRIVATE}),
                            'unknown_reporting_setting')
        self.assertEqual(self.run_check(changes={'UNRELATED_SETTING': PRIVATE})['status'], 'passed')

    def test_json_replaces_environment_and_never_applies_it(self):
        config = self.root / 'settings.json'
        config.write_text(json.dumps(self.environ), encoding='utf-8')
        result = self.run_check(('--config-json', str(config), '--new-demo'),
                                environ={'REPORTING_MODE': 'production', 'REPORTING_SECRET_KEY': PRIVATE})
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(json.loads(config.read_text(encoding='utf-8')), self.environ)
        self.assertFalse(self.state.exists())

    def test_malformed_duplicate_nonstring_and_unknown_json_rejected(self):
        config = self.root / 'settings.json'
        samples = [
            ('[]', 'configuration_file_invalid'),
            ('not JSON ' + PRIVATE, 'configuration_file_invalid'),
            ('{"REPORTING_MODE":"demo","REPORTING_MODE":"production"}', 'configuration_file_invalid'),
            ('{"unrelated":"' + PRIVATE + '"}', 'configuration_file_invalid'),
            ('{"REPORTING_MODE":1}', 'configuration_values_must_be_text'),
            ('{"REPORTING_ENABLE_QUALITY_ACTION":"1"}', 'unknown_reporting_setting'),
            (' ' * (1024 * 1024 + 1), 'configuration_file_invalid'),
        ]
        for text, expected in samples:
            config.write_text(text, encoding='utf-8')
            self.assert_blocked(self.run_check(('--config-json', str(config), '--new-demo')), expected)

    def test_missing_json_and_invalid_argv_do_not_disclose_paths_or_values(self):
        self.assert_blocked(self.run_check(('--config-json', str(self.root / PRIVATE), '--new-demo')),
                            'configuration_file_invalid')
        for argv in (('--profile', PRIVATE), ('--unknown-' + PRIVATE,), ('--new-demo', '--quiesced')):
            self.assert_blocked(self.run_check(argv), 'arguments_invalid')

    def test_missing_parent_is_not_created(self):
        missing = self.root / 'absent' / 'workspace.sqlite'
        self.assert_blocked(self.run_check(changes={'REPORTING_STATE_PATH': str(missing)}),
                            'configuration_or_restore_review_invalid')
        self.assertFalse(missing.parent.exists())

    def test_existing_main_sibling_and_dangling_symlink_block_new_demo(self):
        for suffix in ('', '.qa', '.operations', '.etl.sqlite'):
            path = Path(str(self.state) + suffix)
            path.write_text(PRIVATE, encoding='utf-8')
            self.assert_blocked(self.run_check(), 'new_demo_destination_exists')
            self.assertEqual(path.read_text(encoding='utf-8'), PRIVATE)
            path.unlink()
        link = Path(str(self.state) + '.qa')
        try:
            link.symlink_to(self.root / 'absent')
        except (OSError, NotImplementedError):
            self.skipTest('Symlinks unavailable on this platform.')
        self.assert_blocked(self.run_check(), 'new_demo_destination_exists')
        self.assertTrue(link.is_symlink())

    def test_restore_marker_presence_blocks_without_reading_or_removing(self):
        for name in ('RESTORE_REVIEW_REQUIRED.json', 'INCOMPLETE.json'):
            marker = self.root / name
            marker.write_text(PRIVATE, encoding='utf-8')
            before = snapshot(self.root)
            self.assert_blocked(self.run_check(), 'configuration_or_restore_review_invalid')
            self.assertEqual(snapshot(self.root), before)
            marker.unlink()

    def test_readonly_access_failure_never_attempts_write_probe(self):
        with patch.object(check.os, 'access', return_value=False):
            self.assert_blocked(self.run_check(), 'state_permissions_insufficient')
        self.assertEqual(snapshot(self.root), {})

    @unittest.skipUnless(os.name == 'posix', 'POSIX permission mode checks.')
    def test_parent_permission_modes_fail_closed_even_when_privileged(self):
        try:
            for mode, code in ((0o500, 'state_permissions_insufficient'), (0o755, 'state_permissions_not_private')):
                self.root.chmod(mode)
                self.assert_blocked(self.run_check(), code)
        finally:
            self.root.chmod(0o700)
        self.assertFalse(self.state.exists())

    def test_default_existing_check_requires_quiescence_and_never_creates(self):
        self.assert_blocked(self.run_check(()), 'existing_state_requires_quiesced')
        self.assert_blocked(self.run_check(('--quiesced',)), 'existing_state_missing_use_explicit_new_demo')
        self.assertFalse(self.state.exists())

    def test_company_profile_never_claims_readiness_or_falls_back_to_demo(self):
        source = dict(REPORTING_MODE='production', REPORTING_STATE_PATH=str(self.state),
                      REPORTING_SECRET_KEY=PRIVATE * 2)
        self.assert_blocked(self.run_check(('--profile', 'company'), environ=source),
                            'reviewed_company_providers_required')
        self.assert_blocked(self.run_check(('--profile', 'demo'), environ=source), 'profile_mode_mismatch')
        self.assert_blocked(self.run_check(('--profile', 'company')), 'profile_mode_mismatch')
        self.assert_blocked(self.run_check(('--profile', 'company', '--new-demo'), environ=source), 'arguments_invalid')
        self.assertFalse(self.state.exists())


class ExistingDemoChecks(QuickstartFixture):
    def setUp(self):
        super().setUp()
        from reporting_workspace.application import create_app
        from reporting_workspace.config import Settings
        from reporting_workspace.lifecycle import dispose_app
        self.server = create_app(Settings(state_path=str(self.state), enable_quality_actions=True))
        dispose_app(self.server)

    def test_complete_existing_set_is_checked_without_writes_or_worker_start(self):
        before = snapshot(self.root)
        with patch('reporting_workspace.application.create_app', side_effect=AssertionError('No app construction')):
            result = self.run_check(('--quiesced',))
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['checks']['state']['stores_checked'], 8)
        self.assertEqual(result['checks']['state']['quiescence'], 'operator-asserted')
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(any(path.name.endswith(check._SIDECARS) for path in self.root.rglob('*')))

    def test_missing_required_sibling_is_not_recreated(self):
        etl = Path(str(self.state) + '.etl.sqlite')
        etl.unlink()
        before = snapshot(self.root)
        self.assert_blocked(self.run_check(('--quiesced',)), 'existing_demo_layout_invalid')
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(etl.exists())

    def test_unknown_sibling_is_rejected_without_disclosing_payload(self):
        unknown = Path(str(self.state) + '.qa') / (PRIVATE + '.sqlite')
        unknown.write_text(PRIVATE, encoding='utf-8')
        before = snapshot(self.root)
        self.assert_blocked(self.run_check(('--quiesced',)), 'existing_demo_layout_invalid')
        self.assertEqual(snapshot(self.root), before)

    def test_schema_drift_and_corrupt_header_are_rejected_without_repair(self):
        with closing(sqlite3.connect(str(self.state))) as connection:
            connection.execute('PRAGMA user_version=999')
        before = snapshot(self.root)
        self.assert_blocked(self.run_check(('--quiesced',)), 'existing_demo_store_invalid')
        self.assertEqual(snapshot(self.root), before)
        self.state.write_bytes(PRIVATE.encode('ascii'))
        before = snapshot(self.root)
        self.assert_blocked(self.run_check(('--quiesced',)), 'existing_demo_store_invalid')
        self.assertEqual(snapshot(self.root), before)

    def test_sidecars_block_before_open_including_optional_orphan_sidecar(self):
        for path in (Path(str(self.state) + '-journal'),
                     Path(str(self.state) + '.operations/fixture-sqlite-a.sqlite-wal')):
            path.write_text(PRIVATE, encoding='utf-8')
            before = snapshot(self.root)
            with patch('sqlite3.connect', side_effect=AssertionError('Do not open SQLite with sidecars')):
                self.assert_blocked(self.run_check(('--quiesced',)), 'state_journal_requires_offline_review')
            self.assertEqual(snapshot(self.root), before)
            path.unlink()

    def test_wal_header_rejected_without_creating_shm(self):
        with closing(sqlite3.connect(str(self.state))) as connection:
            connection.execute('PRAGMA journal_mode=WAL')
        self.assertFalse(any(path.name.endswith(check._SIDECARS) for path in self.root.rglob('*')))
        self.assertEqual(self.state.read_bytes()[18:20], b'\x02\x02')
        before = snapshot(self.root)
        with patch('sqlite3.connect', side_effect=AssertionError('Do not open WAL')):
            self.assert_blocked(self.run_check(('--quiesced',)), 'state_journal_requires_offline_review')
        self.assertEqual(snapshot(self.root), before)

    def test_sqlite_connections_are_immutable_readonly_or_inmemory_reference(self):
        connect = sqlite3.connect
        calls = []
        def guarded(database, *args, **kwargs):
            calls.append(database)
            self.assertTrue(database == ':memory:' or database.endswith('?mode=ro&immutable=1'))
            return connect(database, *args, **kwargs)
        with patch('sqlite3.connect', side_effect=guarded):
            result = self.run_check(('--quiesced',))
        self.assertEqual(result['status'], 'passed')
        self.assertGreaterEqual(len(calls), 8)

    def test_optional_fixture_store_is_validated_without_initializing_other_fixtures(self):
        from reporting_workspace.maintenance_registry import build_fixture_registry
        registry = build_fixture_registry(str(self.state) + '.operations')
        try:
            registry.query(dict(id='demo-admin', role='admin', org='A'), 'fixture-001')
        finally:
            registry.close()
        before = snapshot(self.root)
        result = self.run_check(('--quiesced',))
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['checks']['state']['stores_checked'], 9)
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse((Path(str(self.state) + '.operations') / 'fixture-sqlite-b.sqlite').exists())

    @unittest.skipUnless(os.name == 'posix', 'POSIX permission mode checks.')
    def test_existing_store_permissions_fail_closed_without_chmod(self):
        for mode, code in ((0o400, 'state_permissions_insufficient'), (0o622, 'state_permissions_not_private')):
            self.state.chmod(mode)
            try:
                self.assert_blocked(self.run_check(('--quiesced',)), code)
                self.assertEqual(self.state.stat().st_mode & 0o777, mode)
            finally:
                self.state.chmod(0o600)

    def test_symlink_state_path_is_rejected_before_opening_it(self):
        target = self.root / 'preserved.sqlite'
        self.state.rename(target)
        try:
            self.state.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest('Symlinks unavailable on this platform.')
        before = snapshot(self.root)
        with patch('sqlite3.connect', side_effect=AssertionError('Do not follow symlink')):
            self.assert_blocked(self.run_check(('--quiesced',)), 'configuration_or_restore_review_invalid')
        self.assertEqual(snapshot(self.root), before)
        self.assertTrue(self.state.is_symlink())

    def test_old_sqlite_without_immutable_support_is_blocked(self):
        with patch.object(sqlite3, 'sqlite_version_info', (3, 21, 0)):
            self.assert_blocked(self.run_check(('--quiesced',)), 'sqlite_immutable_support_required')

    def test_changed_metadata_during_check_is_blocked(self):
        signature = check._signature
        counts = {}
        def changed(path):
            counts[str(path)] = counts.get(str(path), 0) + 1
            values = signature(path)
            return values[:-1] + (values[-1] + counts[str(path)],)
        with patch.object(check, '_signature', side_effect=changed):
            self.assert_blocked(self.run_check(('--quiesced',)), 'state_changed_during_check')


if __name__ == '__main__':
    unittest.main()
