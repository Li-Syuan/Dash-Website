"""Offline integration acceptance tests; no external services required."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
import tempfile
import unittest
from unittest.mock import patch

from reporting_workspace.legacy_jobs import (
    LegacyJobAdapter, JobValidationError, JobAccessDenied, JobConflict,
    preview_schedule, validate_settings,
)


def config(**updates):
    result = dict(cadence='daily', time='10:00', timezone='Asia/Taipei',
                  weekday=None, recipients=['team@example.invalid'], enabled=True)
    result.update(updates)
    return result


class LegacyJobsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, 'jobs.sqlite')
        self.calls = []
        self.allowed = True

        def authorize(actor, action, job):
            self.calls.append((actor, action, job))
            return self.allowed and actor == 'admin'
        self.authorize = authorize
        self.adapter = LegacyJobAdapter(self.path, authorize)
        self.adapter.save_settings('admin', 'material_daily', config())

    def tearDown(self):
        self.temp.cleanup()

    def test_settings_durable_and_optimistic(self):
        other = LegacyJobAdapter(self.path, self.authorize)
        self.assertEqual(other.get_settings('admin', 'material_daily')['version'], 1)
        with self.assertRaises(JobConflict):
            other.save_settings('admin', 'material_daily', config())
        self.assertEqual(other.save_settings('admin', 'material_daily', config(enabled=False), 1)['version'], 2)
        self.assertEqual(other.preview('admin', 'material_daily', datetime.now(timezone.utc)), [])

    def test_disabled_schedule_allows_explicit_manual_mock(self):
        self.adapter.save_settings('admin', 'material_daily', config(enabled=False), 1)
        self.assertEqual(self.adapter.preview('admin', 'material_daily', datetime.now(timezone.utc)), [])
        result = self.adapter.run_mock('admin', 'material_daily', 'manual-while-disabled')
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(len(self.adapter.list_runs('admin', 'material_daily')), 1)

    def test_strong_settings_validation(self):
        bad_values = [dict(recipients=['person@example.com']), dict(recipients=['a@example.invalid\nBcc:x']),
                      dict(recipients=['a@example.invalid', 'A@example.invalid']), dict(time='25:00'),
                      dict(timezone=''), dict(enabled=1), dict(cadence='hourly'),
                      dict(cadence='weekly', weekday=True), dict(cadence='weekly', weekday=7),
                      dict(weekday=1), dict(command='rm -rf /')]
        for updates in bad_values:
            with self.subTest(updates=updates), self.assertRaises(JobValidationError):
                validate_settings(config(**updates))

    def test_previews_daily_weekly_quarterly(self):
        at = datetime(2026, 10, 1, 2, 0, tzinfo=timezone.utc)
        self.assertEqual(preview_schedule(config(), at, 1), ['2026-10-02T02:00:00+00:00'])
        self.assertEqual(preview_schedule(config(cadence='weekly', weekday=0), at, 1), ['2026-10-05T02:00:00+00:00'])
        self.assertEqual(preview_schedule(config(cadence='quarterly'), at, 1), ['2027-01-01T02:00:00+00:00'])
        with self.assertRaises(JobValidationError):
            preview_schedule(config(), datetime(2026, 1, 1))

    def test_source_and_report_failure_block_downstream(self):
        first = self.adapter.run_mock('admin', 'material_daily', 'day-1', source_ok=False)
        self.assertEqual(first['detail']['report'], 'blocked')
        self.assertEqual(first['detail']['send'], 'blocked')
        second = self.adapter.run_mock('admin', 'material_daily', 'day-2', report_ok=False)
        self.assertEqual(second['status'], 'report_failed')
        self.assertEqual(second['detail']['send'], 'blocked')
        self.assertEqual(len(self.adapter.audit_log('admin', 'material_daily')), 5)

    def test_transport_statuses_are_honest(self):
        for outcome in ('accepted', 'partial', 'uncertain', 'failure'):
            result = self.adapter.run_mock('admin', 'material_daily', outcome, delivery=outcome)
            self.assertEqual(result['detail']['send'], outcome)
            self.assertEqual(result['status'], 'send_failed' if outcome == 'failure' else outcome)
        runs = self.adapter.list_runs('admin', 'material_daily')
        self.assertEqual(len(runs), 4)
        accepted = next(r for r in runs if r['status'] == 'accepted')
        self.assertIn('not verified', accepted['detail']['summary'])
        self.assertEqual(accepted['settings']['recipients'], ['team@example.invalid'])

    def test_manual_retry_and_uncertain_duplicate_risk(self):
        result = self.adapter.run_mock('admin', 'material_daily', 'original', delivery='uncertain')
        with self.assertRaises(JobConflict):
            self.adapter.run_mock('admin', 'material_daily', 'retry', retry_of=result['run_id'])
        retry = self.adapter.run_mock('admin', 'material_daily', 'retry', retry_of=result['run_id'], acknowledge_duplicate_risk=True)
        self.assertEqual(retry['status'], 'accepted')
        with self.assertRaises(JobConflict):
            self.adapter.run_mock('admin', 'material_daily', 'retry-again', retry_of=result['run_id'], acknowledge_duplicate_risk=True)
        with self.assertRaises(JobConflict):
            self.adapter.run_mock('admin', 'material_daily', 'accepted-retry', retry_of=retry['run_id'])

    def test_concurrent_run_key_claim(self):
        def execute(_):
            try:
                return self.adapter.run_mock('admin', 'material_daily', 'same')['status']
            except JobConflict:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(execute, range(16)))
        self.assertEqual(results.count('accepted'), 1)
        self.assertEqual(results.count('conflict'), 15)
        self.assertEqual(len(self.adapter.list_runs('admin', 'material_daily')), 1)

    def test_each_action_reauthorizes_before_data_access(self):
        self.allowed = False
        operations = [lambda: self.adapter.save_settings('admin', 'material_daily', config(), 1),
                      lambda: self.adapter.get_settings('admin', 'material_daily'),
                      lambda: self.adapter.preview('admin', 'material_daily', datetime.now(timezone.utc)),
                      lambda: self.adapter.run_mock('admin', 'material_daily', 'run'),
                      lambda: self.adapter.list_runs('admin', 'material_daily'),
                      lambda: self.adapter.audit_log('admin', 'material_daily')]
        for operation in operations:
            with self.assertRaises(JobAccessDenied):
                operation()
        self.assertEqual(len(self.calls), 7)

    def test_no_network_and_no_automatic_runs(self):
        with patch('socket.socket', side_effect=AssertionError('network prohibited')):
            other = LegacyJobAdapter(self.path, self.authorize)
            self.assertEqual(other.list_runs('admin', 'material_daily'), [])
            other.run_mock('admin', 'material_daily', 'offline')


if __name__ == '__main__':
    unittest.main()
