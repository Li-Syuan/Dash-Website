"""Real local timer, safe ETL, durable fencing, identity, restart and failure tests."""
from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from demo_services import AccessDenied
from reporting_workspace.crud import Conflict, NotFound, StateUnavailable, ValidationError
from reporting_workspace.job_monitor import (DEFAULT_INTERVAL_SECONDS, JOB_ID, MAX_INTERVAL_SECONDS,
                                             MAX_LOGS, MAX_RUNS, MIN_INTERVAL_SECONDS, STEPS, JobMonitor, _DDL_V1)
from reporting_workspace.providers import DemoIdentityProvider


ADMIN = dict(id='admin-a', role='admin', org='A')
USER = dict(id='user-a', role='user', org='A')
FOREIGN_ADMIN = dict(id='admin-b', role='admin', org='B')
FOREIGN_USER = dict(id='user-b', role='user', org='B')


def identities():
    return DemoIdentityProvider({u['id']: dict(password='test-only', role=u['role'], org=u['org'])
                                 for u in (ADMIN, USER, FOREIGN_ADMIN, FOREIGN_USER)})


def dump(path):
    with sqlite3.connect(str(path)) as connection:
        return tuple(connection.iterdump())


def process_attempt(path, entered, release, queue):
    """A separate process holds the job lease while a sibling tries to run it."""
    monitor = JobMonitor(path, identities())
    source = monitor._source_snapshot
    def blocked_source():
        entered.set()
        if not release.wait(10):
            raise RuntimeError('Process test timed out.')
        return source()
    monitor._source_snapshot = blocked_source
    try:
        queue.put(monitor.run_now(ADMIN)['status'])
    except Exception as error:
        queue.put(type(error).__name__)


class JobMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'monitor.sqlite'
        self.identities = identities()
        self.monitor = JobMonitor(self.path, self.identities)
        self.addCleanup(self.monitor.stop)

    def small_monitor(self, **kwargs):
        monitor = JobMonitor(self.path, self.identities, min_interval_seconds=.05,
                             poll_interval_seconds=.01, **kwargs)
        self.addCleanup(monitor.stop)
        return monitor

    def wait_for(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(.01)
        self.fail('Expected real worker condition was not observed.')

    def test_empty_monitor_does_not_start_on_construction_or_enable(self):
        with patch('reporting_workspace.job_monitor.threading.Thread') as thread:
            second = JobMonitor(self.path, self.identities)
            initial = second.status(USER)
            self.assertFalse(initial['enabled'])
            self.assertFalse(initial['scheduler_running'])
            self.assertIsNone(initial['last_run'])
            self.assertIsNone(initial['last_success'])
            self.assertIsNone(initial['data_freshness_seconds'])
            self.assertEqual(initial['interval_seconds'], DEFAULT_INTERVAL_SECONDS)
            second.configure(ADMIN, True, 60)
            self.assertFalse(second.status(USER)['scheduler_running'])
            thread.assert_not_called()
        self.assertEqual(initial['timezone'], 'Asia/Taipei')
        self.assertEqual(initial['job_id'], JOB_ID)
        self.assertIsNone(initial['next_run'])
        self.assertIsNone(initial['published_summary'])

    def test_current_org_a_users_read_and_only_current_org_a_admin_controls(self):
        self.monitor.status(USER)
        for user in (FOREIGN_ADMIN, FOREIGN_USER):
            for action in (lambda:self.monitor.status(user),
                           lambda:self.monitor.configure(user, True, 60),
                           lambda:self.monitor.run_now(user)):
                with self.assertRaises(AccessDenied):
                    action()
        for action in (lambda:self.monitor.configure(USER, True, 60), lambda:self.monitor.run_now(USER)):
            with self.assertRaises(AccessDenied):
                action()
        for spoof in (dict(ADMIN, org='B'), dict(USER, role='admin'), dict(ADMIN, role='user')):
            with self.assertRaises(AccessDenied):
                self.monitor.status(spoof)
        self.identities.user_db[ADMIN['id']]['role'] = 'user'
        for action in (lambda:self.monitor.status(ADMIN), lambda:self.monitor.configure(ADMIN, True, 60),
                       lambda:self.monitor.run_now(ADMIN)):
            with self.assertRaises(AccessDenied):
                action()
        self.identities.user_db.pop(USER['id'])
        with self.assertRaises(AccessDenied):
            self.monitor.status(USER)

    def test_provider_errors_fail_closed_with_safe_errors(self):
        with patch.object(self.identities, 'get_user', side_effect=RuntimeError('SECRET TOKEN company-db.example')):
            for action in (lambda:self.monitor.configure(ADMIN, True, 60), lambda:self.monitor.run_now(ADMIN),
                           lambda:self.monitor.status(USER)):
                with self.assertRaises(AccessDenied) as raised:
                    action()
                self.assertNotIn('SECRET', str(raised.exception))
        self.assertEqual(self.monitor.status(USER)['runs'], [])

    def test_strict_interval_enable_and_pagination_bounds_are_nonmutating(self):
        before = dump(self.path)
        invalid = (True, False, None, '60', 0, -1, 59, MAX_INTERVAL_SECONDS+1,
                   float('nan'), float('inf'), float('-inf'), 10**400)
        for interval in invalid:
            with self.subTest(interval=str(interval)[:20]), self.assertRaises(ValidationError):
                self.monitor.configure(ADMIN, True, interval)
        for enabled in (0, 1, 'true', None, [], {}):
            with self.subTest(enabled=enabled), self.assertRaises(ValidationError):
                self.monitor.configure(ADMIN, enabled, 60)
        for limit in (0, MAX_RUNS+1, True, 1.0, '1', None):
            with self.assertRaises(ValidationError):
                self.monitor.status(USER, limit)
        self.assertEqual(dump(self.path), before)
        self.monitor.configure(ADMIN, True, MIN_INTERVAL_SECONDS)
        self.monitor.configure(ADMIN, True, MAX_INTERVAL_SECONDS)

    def test_trusted_test_interval_and_constructor_bounds(self):
        monitor = self.small_monitor()
        self.assertEqual(monitor.configure(ADMIN, True, .05)['interval_seconds'], .05)
        with self.assertRaises(ValidationError):
            self.monitor.configure(ADMIN, True, .05)
        for name, value in (('min_interval_seconds', 0), ('min_interval_seconds', 61),
                            ('poll_interval_seconds', 0), ('lease_ttl_seconds', .001),
                            ('lease_ttl_seconds', True)):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                JobMonitor(self.path, self.identities, **{name:value})

    def test_three_real_steps_and_atomic_summary_with_safe_status(self):
        run = self.monitor.run_now(ADMIN)
        self.assertEqual(run['status'], 'succeeded')
        self.assertEqual(run['trigger'], 'manual')
        self.assertEqual(run['row_count'], 4)
        self.assertEqual([step['name'] for step in run['steps']], list(STEPS))
        for step in run['steps']:
            self.assertEqual(step['status'], 'succeeded')
            self.assertEqual(step['row_count'], 4)
            self.assertGreaterEqual(step['duration_seconds'], 0)
            self.assertTrue(step['started_at'].endswith('+08:00'))
            self.assertTrue(step['finished_at'].endswith('+08:00'))
        status = self.monitor.status(USER)
        published = status['published_summary']
        self.assertEqual(published['run_id'], run['run_id'])
        self.assertEqual((published['revenue'], published['cost'], published['profit']), (420000, 247000, 173000))
        self.assertIsNotNone(status['last_success'])
        self.assertGreaterEqual(status['data_freshness_seconds'], 0)
        encoded = json.dumps(status)
        for forbidden in ('lease_token', 'lease_owner', 'fencing', self.monitor.path, 'test-only', ADMIN['id']):
            self.assertNotIn(forbidden, encoded)
        self.assertTrue(status['synthetic'])
        self.assertFalse(status['real_delivery'])
        self.assertFalse(status['enabled'])

    def test_upstream_failure_skips_downstream_and_preserves_last_success(self):
        success = self.monitor.run_now(ADMIN)
        good = self.monitor.status(USER)['published_summary']
        with patch.object(self.monitor, '_source_snapshot', side_effect=RuntimeError('SECRET SMTP password real-company database')):
            run = self.monitor.run_now(ADMIN)
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(run['error_code'], 'source_failed')
        self.assertEqual([step['status'] for step in run['steps']], ['failed','skipped','skipped'])
        self.assertEqual([step['error_code'] for step in run['steps']], ['source_failed','upstream_failed','upstream_failed'])
        self.assertEqual(self.monitor.status(USER)['published_summary'], good)
        self.assertEqual(self.monitor.status(USER)['last_success_at'], success['finished_at_epoch'])
        self.assertNotIn('SECRET', json.dumps(self.monitor.status(USER)))
        self.assertNotIn('SMTP password', json.dumps(self.monitor.status(USER)))

    def test_actual_validation_rejects_duplicate_synthetic_rows(self):
        rows = self.monitor._source_snapshot()
        rows[1]['record_id'] = rows[0]['record_id']
        with patch.object(self.monitor, '_source_snapshot', return_value=rows):
            run = self.monitor.run_now(ADMIN)
        self.assertEqual(run['error_code'], 'validation_failed')
        self.assertEqual([step['status'] for step in run['steps']], ['succeeded','failed','skipped'])
        self.assertIsNone(self.monitor.status(USER)['published_summary'])

    def test_failed_publish_rolls_back_summary_and_success_bookkeeping(self):
        self.monitor.run_now(ADMIN)
        good = self.monitor.status(USER)['published_summary']
        original = self.monitor._publish_summary
        def failing_publish(connection, context, rows, now):
            original(connection, context, rows, now)
            raise RuntimeError('SECRET after publication before commit')
        with patch.object(self.monitor, '_publish_summary', side_effect=failing_publish):
            run = self.monitor.run_now(ADMIN)
        self.assertEqual(run['error_code'], 'publish_failed')
        self.assertEqual([step['status'] for step in run['steps']], ['succeeded','succeeded','failed'])
        self.assertEqual(self.monitor.status(USER)['published_summary'], good)
        self.assertEqual(self.monitor.status(USER)['last_run']['status'], 'failed')
        self.assertNotIn('SECRET', json.dumps(self.monitor.status(USER)))

    def test_reconfiguration_during_execution_blocks_stale_publication(self):
        self.monitor.run_now(ADMIN)
        good = self.monitor.status(USER)['published_summary']
        source = self.monitor._source_snapshot
        def reconfigure_source():
            self.monitor.configure(ADMIN, True, 60)
            return source()
        with patch.object(self.monitor, '_source_snapshot', side_effect=reconfigure_source):
            run = self.monitor.run_now(ADMIN)
        self.assertEqual(run['error_code'], 'configuration_changed')
        self.assertEqual(self.monitor.status(USER)['published_summary'], good)
        self.assertEqual([step['status'] for step in run['steps']], ['failed','skipped','skipped'])

    def test_revoked_identity_during_execution_cannot_publish(self):
        self.monitor.run_now(ADMIN)
        good = self.monitor.status(USER)['published_summary']
        source = self.monitor._source_snapshot
        def revoke_source():
            self.identities.user_db[ADMIN['id']]['role'] = 'user'
            return source()
        with patch.object(self.monitor, '_source_snapshot', side_effect=revoke_source):
            run = self.monitor.run_now(ADMIN)
        self.assertEqual(run['error_code'], 'permission_revoked')
        self.assertEqual([step['status'] for step in run['steps']], ['succeeded','failed','skipped'])
        self.assertEqual(self.monitor.status(USER)['published_summary'], good)

    def test_two_instances_cannot_overlap_manual_attempts(self):
        second = JobMonitor(self.path, self.identities)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        source = self.monitor._source_snapshot
        def held_source():
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Test did not release the synthetic source.')
            return source()
        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(self.monitor, '_source_snapshot', side_effect=held_source):
            first = pool.submit(self.monitor.run_now, ADMIN)
            self.assertTrue(entered.wait(3))
            with self.assertRaises(Conflict):
                second.run_now(ADMIN)
            self.assertEqual(len(second.status(USER)['runs']), 1)
            self.assertEqual(second.status(USER)['last_run']['status'], 'running')
            release.set()
            self.assertEqual(first.result(3)['status'], 'succeeded')
        self.assertEqual(len(second.status(USER)['runs']), 1)

    def test_separate_processes_share_durable_lease(self):
        context = multiprocessing.get_context('spawn')
        entered, release, queue = context.Event(), context.Event(), context.Queue()
        process = context.Process(target=process_attempt, args=(str(self.path), entered, release, queue))
        process.start()
        try:
            self.assertTrue(entered.wait(10))
            with self.assertRaises(Conflict):
                self.monitor.run_now(ADMIN)
            self.assertEqual(len(self.monitor.status(USER)['runs']), 1)
            release.set()
            self.assertEqual(queue.get(timeout=10), 'succeeded')
            process.join(10)
            self.assertEqual(process.exitcode, 0)
        finally:
            release.set()
            if process.is_alive():
                process.terminate()
            process.join(10)
            queue.close()

    def test_restart_recovers_interrupted_claim_without_replaying_it(self):
        old_context = self.monitor._claim(ADMIN, 'manual')
        self.monitor._start_step(old_context, STEPS[0])
        # Simulate a process exiting after claiming: expire only its lease.
        with sqlite3.connect(self.monitor.lease_path) as connection:
            connection.execute('UPDATE leases SET expires_at=0')
        restarted = JobMonitor(self.path, self.identities)
        new = restarted.run_now(ADMIN)
        runs = restarted.status(USER)['runs']
        previous = next(run for run in runs if run['run_id'] == old_context['run_id'])
        self.assertEqual(previous['status'], 'failed')
        self.assertEqual(previous['error_code'], 'interrupted')
        self.assertEqual([step['status'] for step in previous['steps']], ['failed','skipped','skipped'])
        self.assertNotEqual(new['run_id'], previous['run_id'])
        self.assertEqual(restarted.status(USER)['published_summary']['run_id'], new['run_id'])
        # The expired old worker still cannot replace the new publication.
        old = self.monitor._execute(old_context)
        self.assertEqual(old['error_code'], 'interrupted')
        self.assertEqual(restarted.status(USER)['published_summary']['run_id'], new['run_id'])

    def test_expired_worker_is_fenced_before_publication(self):
        monitor = self.small_monitor(lease_ttl_seconds=.05)
        monitor.run_now(ADMIN)
        good = monitor.status(USER)['published_summary']
        source = monitor._source_snapshot
        def slow_source():
            time.sleep(.07)
            return source()
        with patch.object(monitor, '_source_snapshot', side_effect=slow_source):
            run = monitor.run_now(ADMIN)
        self.assertEqual(run['error_code'], 'lease_lost')
        self.assertEqual(monitor.status(USER)['published_summary'], good)

    def test_restored_old_fence_cannot_report_success_against_newer_publication(self):
        self.monitor.run_now(ADMIN)
        good = self.monitor.status(USER)['published_summary']
        # Simulate a restored lease backup lagging the protected publication.
        with sqlite3.connect(self.path) as connection:
            connection.execute('UPDATE monitor_publication SET fencing=100')
        run = self.monitor.run_now(ADMIN)
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(run['error_code'], 'lease_lost')
        self.assertEqual(self.monitor.status(USER)['published_summary'], good)
        self.assertEqual(run['steps'][-1]['status'], 'failed')

    def test_real_timer_runs_and_stop_joins_without_more_attempts(self):
        monitor = self.small_monitor()
        configured = monitor.configure(ADMIN, True, .05)
        self.assertTrue(configured['next_run'].endswith('+08:00'))
        self.assertTrue(monitor.start())
        self.assertFalse(monitor.start())
        result = self.wait_for(lambda: monitor.status(USER)['last_success'])
        self.assertIsNotNone(result)
        self.assertTrue(monitor.stop())
        status = monitor.status(USER)
        count = len(status['runs'])
        self.assertFalse(status['scheduler_running'])
        self.assertTrue(any(run['trigger']=='timer' and run['status']=='succeeded' for run in status['runs']))
        time.sleep(.1)
        self.assertEqual(len(monitor.status(USER)['runs']), count)
        self.assertTrue(monitor.start())
        self.wait_for(lambda: len(monitor.status(USER)['runs']) > count)
        self.assertTrue(monitor.stop())

    def test_two_real_workers_claim_each_due_tick_once(self):
        first, second = self.small_monitor(), self.small_monitor()
        first.configure(ADMIN, True, .08)
        first.start()
        second.start()
        self.wait_for(lambda: len(first.status(USER)['runs']) >= 3)
        self.assertTrue(first.stop())
        self.assertTrue(second.stop())
        runs = first.status(USER)['runs']
        timer_runs = [run for run in runs if run['trigger']=='timer']
        scheduled = [run['scheduled_for'] for run in timer_runs]
        self.assertEqual(len(scheduled), len(set(scheduled)))
        self.assertTrue(all(run['status']=='succeeded' for run in timer_runs))
        self.assertTrue(all(run['row_count']==4 for run in timer_runs))

    def test_disable_stops_future_ticks_and_restart_keeps_disabled_settings(self):
        monitor = self.small_monitor()
        monitor.configure(ADMIN, True, .05)
        monitor.start()
        self.wait_for(lambda: monitor.status(USER)['last_success'])
        disabled = monitor.configure(ADMIN, False, .05)
        self.assertIsNone(disabled['next_run'])
        self.assertIsNone(disabled['next_run_at'])
        count = len(disabled['runs'])
        time.sleep(.15)
        self.assertEqual(len(monitor.status(USER)['runs']), count)
        monitor.stop()
        restarted = self.small_monitor()
        self.assertFalse(restarted.status(USER)['enabled'])
        self.assertEqual(restarted.status(USER)['published_summary'], monitor.status(USER)['published_summary'])
        restarted.start()
        time.sleep(.1)
        self.assertEqual(len(restarted.status(USER)['runs']), count)

    def test_timer_rechecks_schedule_administrator_access(self):
        monitor = self.small_monitor()
        monitor.configure(ADMIN, True, .05)
        self.identities.user_db[ADMIN['id']]['org'] = 'B'
        monitor.start()
        self.wait_for(lambda: not monitor.status(USER)['enabled'])
        status = monitor.status(USER)
        self.assertEqual(status['runs'], [])
        self.assertIsNone(status['next_run'])
        self.assertEqual(status['logs'][0]['error_code'], 'permission_revoked')

    def test_missed_ticks_coalesce_to_one_attempt_and_preserve_anchor(self):
        monitor = self.small_monitor()
        from contextlib import closing
        now = time.time()
        # Verify coalescing/anchor math, independent of Windows disk speed.
        with patch('reporting_workspace.job_monitor.time.time', return_value=now):
            monitor.configure(ADMIN, True, .05)
            with closing(sqlite3.connect(self.path)) as connection, connection:
                due = now - 100
                connection.execute('UPDATE monitor_config SET next_run_at=?', (due,))
            monitor._tick()
            status = monitor.status(USER)
            self.assertEqual(len(status['runs']), 1)
            self.assertGreater(status['next_run_at'], now)
            self.assertLessEqual(status['next_run_at'] - now, .050001)
            intervals = (status['next_run_at'] - due) / .05
            self.assertAlmostEqual(intervals, round(intervals), places=4)
            monitor._tick()
            self.assertEqual(len(monitor.status(USER)['runs']), 1)

    def test_stop_reports_running_work_and_does_not_start_second_thread(self):
        monitor = self.small_monitor()
        monitor.configure(ADMIN, True, .05)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        source = monitor._source_snapshot
        def held_source():
            entered.set()
            release.wait(4)
            return source()
        with patch.object(monitor, '_source_snapshot', side_effect=held_source):
            monitor.start()
            self.assertTrue(entered.wait(3))
            self.assertFalse(monitor.stop(timeout=0))
            self.assertFalse(monitor.start())
            release.set()
            self.assertTrue(monitor.stop())
        self.assertFalse(monitor.status(USER)['scheduler_running'])

    def test_same_configuration_is_idempotent(self):
        configured = self.monitor.configure(ADMIN, True, 60)
        before = dump(self.path)
        repeated = self.monitor.configure(ADMIN, True, 60)
        self.assertEqual(configured['next_run_at'], repeated['next_run_at'])
        self.assertEqual(dump(self.path), before)

    def test_durable_storage_missing_or_incompatible_fails_closed(self):
        self.path.unlink()
        with self.assertRaises(StateUnavailable):
            self.monitor.run_now(ADMIN)
        self.assertFalse(self.path.exists())
        with sqlite3.connect(self.path) as connection:
            connection.execute('CREATE TABLE unrelated(secret TEXT)')
        before = dump(self.path)
        with self.assertRaises(StateUnavailable):
            JobMonitor(self.path, self.identities)
        self.assertEqual(dump(self.path), before)

    def test_wal_storage_is_rejected_for_atomic_fenced_publication(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute('PRAGMA journal_mode=WAL')
        with self.assertRaises(StateUnavailable):
            self.monitor.status(USER)

    def test_selected_run_diagnostics_are_safe_explicit_read_only_metadata(self):
        with patch.object(self.monitor, '_source_snapshot', side_effect=RuntimeError(
                'SECRET credential env OPENAI_API_KEY smtp://company.example traceback internal-path')):
            failed = self.monitor.run_now(ADMIN)
        succeeded = self.monitor.run_now(ADMIN)
        before = dump(self.path)
        diagnostics = self.monitor.export_diagnostics(USER, failed['run_id'])
        self.assertEqual(diagnostics['run']['run_id'], failed['run_id'])
        self.assertEqual(diagnostics['run']['status'], 'failed')
        self.assertEqual(diagnostics['run']['error_code'], 'source_failed')
        self.assertEqual(diagnostics['run']['config_version'], 1)
        self.assertEqual([step['status'] for step in diagnostics['run']['steps']], ['failed','skipped','skipped'])
        self.assertEqual(diagnostics['related_report_refs'],
                         [dict(report_id=JOB_ID, kind='local-synthetic-summary', synthetic=True)])
        self.assertEqual(self.monitor.export_diagnostics(ADMIN, succeeded['run_id'])['run']['row_count'], 4)
        self.assertEqual(dump(self.path), before)
        encoded = json.dumps(diagnostics)
        for forbidden in ('SECRET', 'credential', 'OPENAI_API_KEY', 'company.example', 'traceback', 'internal-path',
                          ADMIN['id'], 'lease_token', 'lease_owner', 'fencing', 'updated_by', 'revenue', 'cost',
                          'record_id', 'enabled', 'interval_seconds', self.monitor.path):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(set(diagnostics), {'format_version','job_id','timezone','synthetic','run','related_report_refs'})
        self.assertEqual(set(diagnostics['run']),
                         {'run_id','trigger','status','error_code','error','scheduled_for','started_at','finished_at',
                          'started_at_epoch','finished_at_epoch','duration_seconds','row_count','steps','config_version'})

    def test_diagnostics_reject_foreign_identity_unknown_or_unbounded_id(self):
        run = self.monitor.run_now(ADMIN)
        for user in (FOREIGN_ADMIN, FOREIGN_USER, dict(USER, role='admin')):
            with self.assertRaises(AccessDenied):
                self.monitor.export_diagnostics(user, run['run_id'])
        for identifier in (None, 1, True, '', 'a'*31, 'a'*33, 'F'*32, '../monitor.sqlite', "' OR 1=1 --"):
            with self.assertRaises(ValidationError):
                self.monitor.export_diagnostics(USER, identifier)
        with self.assertRaises(NotFound):
            self.monitor.export_diagnostics(USER, '0'*32)
        self.identities.user_db[USER['id']]['org'] = 'B'
        with self.assertRaises(AccessDenied):
            self.monitor.export_diagnostics(USER, run['run_id'])

    def test_mock_owner_configuration_is_bounded_admin_only_and_persistent(self):
        self.assertEqual(self.monitor.status(USER)['notification_config'], dict(enabled=False, owner_email=None))
        before = dump(self.path)
        for email in ('real@company.example', 'a@EXAMPLE.INVALID', 'a'*65+'@example.invalid',
                      'bad\\nheader@example.invalid', 'a@example.invalid,b@example.invalid', [], 123):
            with self.assertRaises(ValidationError):
                self.monitor.configure_notification(ADMIN, email, True)
        for enabled in (1, 0, 'true', None):
            with self.assertRaises(ValidationError):
                self.monitor.configure_notification(ADMIN, 'owner@example.invalid', enabled)
        for user in (USER, FOREIGN_ADMIN, FOREIGN_USER, dict(USER, role='admin')):
            with self.assertRaises(AccessDenied):
                self.monitor.configure_notification(user, 'owner@example.invalid', True)
        self.assertEqual(dump(self.path), before)
        config = self.monitor.configure_notification(ADMIN, 'owner@example.invalid', True)['notification_config']
        self.assertEqual(config, dict(enabled=True, owner_email='owner@example.invalid'))
        restarted = JobMonitor(self.path, self.identities)
        self.assertEqual(restarted.status(USER)['notification_config'], config)
        self.monitor.configure_notification(ADMIN, None, False)
        self.assertEqual(self.monitor.status(USER)['notification_config'], dict(enabled=False, owner_email=None))

    def test_failed_run_claims_only_one_safe_mock_notification(self):
        self.monitor.configure_notification(ADMIN, 'owner@example.invalid', True)
        with patch.object(self.monitor, '_source_snapshot', side_effect=RuntimeError('SECRET private rows SMTP token traceback')):
            failed = self.monitor.run_now(ADMIN)
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(len(self.monitor.mail.messages), 1)
        message = self.monitor.mail.messages[0]
        self.assertEqual(message['recipients'], ['owner@example.invalid'])
        self.assertEqual(len(message['body'].splitlines()), 5)
        self.assertIn('Failed step: source_snapshot', message['body'])
        self.assertIn('Code: source_failed', message['body'])
        self.assertIn('Monitor: /QA_portal/operations', message['body'])
        for forbidden in ('SECRET', ADMIN['id'], 'private rows', 'token', 'traceback', 'SMTP', self.monitor.path):
            self.assertNotIn(forbidden, message['body'])
        self.monitor._notify_failure(dict(run_id=failed['run_id'], user=ADMIN))
        self.assertEqual(len(self.monitor.mail.messages), 1)
        notification = self.monitor.status(USER)['notification_logs'][0]
        self.assertEqual(notification['run_id'], failed['run_id'])
        self.assertEqual(notification['status'], 'simulated')
        self.assertFalse(notification['real_delivery'])
        self.assertEqual(self.monitor.status(USER)['last_run']['status'], 'failed')
        encoded = json.dumps(self.monitor.export_diagnostics(USER, failed['run_id']))
        self.assertNotIn('owner@example.invalid', encoded)
        self.assertNotIn('notification', encoded)

    def test_consecutive_failures_coalesce_durably_until_success(self):
        self.monitor.configure_notification(ADMIN, 'owner@example.invalid', True)
        second = JobMonitor(self.path, self.identities)
        with patch.object(self.monitor, '_source_snapshot', side_effect=RuntimeError('first failure')):
            first = self.monitor.run_now(ADMIN)
        with patch.object(second, '_source_snapshot', side_effect=RuntimeError('second failure')):
            second_run = second.run_now(ADMIN)
        statuses = {row['run_id']:row['status'] for row in second.status(USER)['notification_logs']}
        self.assertEqual(statuses[first['run_id']], 'simulated')
        self.assertEqual(statuses[second_run['run_id']], 'coalesced')
        self.assertEqual(len(self.monitor.mail.messages), 1)
        self.assertEqual(second.mail.messages, [])
        second.run_now(ADMIN)
        with patch.object(second, '_source_snapshot', side_effect=RuntimeError('new failure episode')):
            third = second.run_now(ADMIN)
        self.assertEqual(second.status(USER)['notification_logs'][0]['run_id'], third['run_id'])
        self.assertEqual(second.status(USER)['notification_logs'][0]['status'], 'simulated')
        self.assertEqual(len(second.mail.messages), 1)

    def test_concurrent_duplicate_notification_claims_send_once(self):
        self.monitor.configure_notification(ADMIN, 'owner@example.invalid', True)
        with patch.object(self.monitor, '_notify_failure'), patch.object(self.monitor, '_source_snapshot', side_effect=ValueError('fail')):
            failed = self.monitor.run_now(ADMIN)
        second = JobMonitor(self.path, self.identities)
        context = dict(run_id=failed['run_id'], user=ADMIN)
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit((self.monitor if index%2 else second)._notify_failure, context) for index in range(8)]
            for future in futures:
                future.result(3)
        self.assertEqual(len(self.monitor.mail.messages)+len(second.mail.messages), 1)
        self.assertEqual(len(self.monitor.status(USER)['notification_logs']), 1)
        self.assertEqual(self.monitor.status(USER)['notification_logs'][0]['status'], 'simulated')

    def test_mock_mail_failure_keeps_job_failed_without_etl_retry(self):
        self.monitor.configure_notification(ADMIN, 'owner@example.invalid', True)
        with patch.object(self.monitor, '_source_snapshot', side_effect=RuntimeError('SECRET ETL failure')) as source, \
                patch.object(self.monitor.mail, 'send', side_effect=RuntimeError('SECRET SMTP credential')) as send:
            failed = self.monitor.run_now(ADMIN)
            self.monitor._notify_failure(dict(run_id=failed['run_id'], user=ADMIN))
            self.assertEqual(source.call_count, 1)
            self.assertEqual(send.call_count, 1)
        self.assertEqual(failed['error_code'], 'source_failed')
        self.assertEqual(self.monitor.status(USER)['last_run']['error_code'], 'source_failed')
        notification = self.monitor.status(USER)['notification_logs'][0]
        self.assertEqual(notification['status'], 'failed')
        self.assertEqual(notification['error_code'], 'mock_failed')
        self.assertNotIn('SECRET', json.dumps(self.monitor.status(USER)))
        with patch.object(self.monitor, '_source_snapshot', side_effect=ValueError('next consecutive failure')):
            self.monitor.run_now(ADMIN)
        self.assertEqual(self.monitor.status(USER)['notification_logs'][0]['status'], 'coalesced')
        self.assertEqual(self.monitor.mail.messages, [])

    def test_revoked_org_during_failed_job_blocks_mock_notification(self):
        self.monitor.configure_notification(ADMIN, 'owner@example.invalid', True)
        def revoke_and_fail():
            self.identities.user_db[ADMIN['id']]['org'] = 'B'
            raise RuntimeError('SECRET')
        with patch.object(self.monitor, '_source_snapshot', side_effect=revoke_and_fail):
            run = self.monitor.run_now(ADMIN)
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(self.monitor.mail.messages, [])
        self.assertEqual(self.monitor.status(USER)['notification_logs'][0]['status'], 'blocked')
        self.assertEqual(self.monitor.status(USER)['notification_logs'][0]['error_code'], 'permission_revoked')
        with self.assertRaises(AccessDenied):
            self.monitor.configure_notification(ADMIN, 'owner@example.invalid', False)

    def test_disabled_notification_and_notification_storage_failure_do_not_rerun_etl(self):
        with patch.object(self.monitor, '_source_snapshot', side_effect=ValueError('source')) as source:
            run = self.monitor.run_now(ADMIN)
        self.assertEqual(source.call_count, 1)
        self.assertEqual(self.monitor.status(USER)['notification_logs'][0]['status'], 'disabled')
        self.assertEqual(self.monitor.mail.messages, [])
        with patch.object(self.monitor, '_source_snapshot', side_effect=ValueError('source')) as source, \
                patch.object(self.monitor, '_notify_failure', side_effect=StateUnavailable('safe storage error')):
            second = self.monitor.run_now(ADMIN)
        self.assertEqual(source.call_count, 1)
        self.assertEqual(second['status'], 'failed')
        self.assertEqual(second['error_code'], 'source_failed')
        self.assertEqual(self.monitor.status(USER)['notification_error_code'], 'storage_unavailable')
        self.assertNotEqual(run['run_id'], second['run_id'])

    def test_notification_v1_schema_upgrade_retains_existing_jobs(self):
        path = Path(self.temp.name)/'old-monitor.sqlite'
        with sqlite3.connect(path) as connection:
            for ddl in _DDL_V1:
                connection.execute(ddl)
            connection.execute('INSERT INTO monitor_config VALUES(?,?,0,?,NULL,7,NULL,?)', (JOB_ID,'A',300,time.time()))
            connection.execute('PRAGMA user_version=1')
        upgraded = JobMonitor(path, self.identities)
        self.assertEqual(upgraded.status(USER)['interval_seconds'], 300)
        self.assertEqual(upgraded.status(USER)['notification_config'], dict(enabled=False, owner_email=None))
        with sqlite3.connect(path) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertEqual(connection.execute('SELECT version FROM monitor_config').fetchone()[0], 7)
        self.assertEqual(upgraded.run_now(ADMIN)['status'], 'succeeded')

    def test_logs_and_history_remain_bounded(self):
        for _ in range(MAX_RUNS+4):
            self.monitor.run_now(ADMIN)
        self.assertEqual(len(self.monitor.status(USER, MAX_RUNS)['runs']), MAX_RUNS)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM monitor_runs').fetchone()[0], MAX_RUNS)
            self.assertLessEqual(connection.execute('SELECT COUNT(*) FROM monitor_logs').fetchone()[0], MAX_LOGS)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM monitor_steps').fetchone()[0], MAX_RUNS*3)


if __name__ == '__main__':
    unittest.main()
