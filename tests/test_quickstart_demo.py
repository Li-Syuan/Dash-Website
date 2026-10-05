"""User-facing offline drill boundaries; no live databases or network listeners."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tools.quickstart_demo import DrillError, main, run_drill


class QuickstartDemoTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='quickstart-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.target = self.root / 'new-drill'

    def test_drill_restores_all_members_and_keeps_source_later_data_and_quarantine(self):
        with patch.dict(os.environ, {'REPORTING_MODE': 'production',
                                     'REPORTING_STATE_PATH': str(self.root / 'DO-NOT-TOUCH.sqlite')}, clear=False):
            result = run_drill(self.target)
        self.assertEqual(result['status'], 'passed-synthetic-offline-drill')
        self.assertEqual((result['report_rows'], result['members']), (36, 8))
        self.assertEqual(result['report_summary'], {'open': 25, 'closed': 11, 'overdue': 25})
        self.assertEqual(result['restore_marker'], 'retained')
        self.assertTrue(result['all_restored_logical_digests_equal'])
        self.assertFalse((self.root / 'DO-NOT-TOUCH.sqlite').exists())
        self.assertFalse((self.target / 'INCOMPLETE.json').exists())
        self.assertTrue((self.target / 'restored/RESTORE_REVIEW_REQUIRED.json').is_file())
        self.assertTrue((self.target / 'SYNTHETIC_DRILL_DO_NOT_DEPLOY.json').is_file())
        self.assertNotIn(str(self.root), json.dumps(result))
        with sqlite3.connect(str(self.target / 'source/workspace.sqlite')) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 999)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM job_runs').fetchone()[0], 2)

    def test_explicit_date_changes_snapshot_and_export(self):
        result = run_drill(self.target, '2026-08-01')
        self.assertEqual(result['as_of'], '2026-08-01')
        self.assertEqual(result['report_rows'], 0)
        self.assertEqual(result['report_summary'], {'open': 0, 'closed': 0, 'overdue': 0})
        lines = (self.target / 'synthetic-corrective-actions.csv').read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 1)

    def test_existing_destination_is_never_overwritten(self):
        self.target.mkdir()
        sentinel = self.target / 'sentinel'
        sentinel.write_text('keep', encoding='utf-8')
        with self.assertRaisesRegex(DrillError, 'destination_exists'):
            run_drill(self.target)
        self.assertEqual(list(self.target.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_invalid_date_and_relative_path_fail_before_writes(self):
        for path, date in ((self.target, '2026-02-30'), (Path('relative-drill'), '2026-10-05')):
            with self.subTest(path=path), self.assertRaises(Exception):
                run_drill(path, date)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_dependency_or_private_failure_is_sanitized(self):
        private = 'private-error-password=must-not-appear'
        output = io.StringIO()
        with patch('tools.quickstart_demo.run_drill', side_effect=RuntimeError(private)), redirect_stdout(output):
            code = main(['--destination', str(self.target)])
        self.assertEqual(code, 2)
        self.assertNotIn(private, output.getvalue())
        self.assertEqual(json.loads(output.getvalue())['code'], 'synthetic_drill_failed')

    def test_malformed_cli_never_echoes_supplied_secret_or_path(self):
        output, errors = io.StringIO(), io.StringIO()
        canary = 'private-cli-secret-never-print'
        with redirect_stdout(output), redirect_stderr(errors):
            code = main(['--destination', str(self.target), '--secret=' + canary])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue()),
                         {'status': 'failed', 'code': 'arguments_invalid'})
        self.assertEqual(errors.getvalue(), '')
        self.assertNotIn(canary, output.getvalue())
        self.assertFalse(self.target.exists())

    def test_failure_retains_owned_incomplete_marker_and_refuses_retry(self):
        with patch('reporting_workspace.deployment.backup_set', side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                run_drill(self.target)
        self.assertTrue((self.target / 'INCOMPLETE.json').is_file())
        self.assertFalse((self.target / 'evidence.json').exists())
        with self.assertRaisesRegex(DrillError, 'destination_exists'):
            run_drill(self.target)

    def test_symlink_destination_or_parent_is_rejected(self):
        link = self.root / 'linked'
        try:
            link.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('Symlink creation not available to this test user.')
        with self.assertRaises(Exception):
            run_drill(link / 'new-drill')
        self.assertFalse(self.target.exists())


if __name__ == '__main__':
    unittest.main()
