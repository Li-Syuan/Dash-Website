"""Lease-wait counterfactuals using real isolated SQLite and injected clocks.

Only the test module's clock reference changes. Product lease policy, durable
rows, operating-system clocks, and acceptance clock invalidation are untouched.
"""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import test_etl_recovery_faults as recovery
from reporting_workspace import etl_dispatch


START = 1000000.0
TABLES = ('etl_config', 'etl_leases', 'etl_requests', 'etl_runs', 'etl_steps', 'etl_publications')


class _Clock:
    def __init__(self, wall=START + .5, rollback=0, monotonic_jump=0, stuck_wall=False):
        self.wall = wall
        self.elapsed = 0
        self.rollback = rollback
        self.monotonic_jump = monotonic_jump
        self.stuck_wall = stuck_wall
        self.sleeps = []
        self.wall_reads = 0

    def time(self):
        self.wall_reads += 1
        return self.wall

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        # No actual time.sleep, system-clock changes or operation retries.
        if len(self.sleeps) >= 102:
            raise AssertionError('Lease helper exceeded its bounded poll count.')
        if seconds <= 0:
            raise AssertionError('Lease helper requested a nonpositive sleep.')
        self.sleeps.append(seconds)
        self.elapsed += seconds + self.monotonic_jump
        if not self.stuck_wall:
            self.wall += seconds - self.rollback
        self.rollback = 0
        self.monotonic_jump = 0


class ETLLeaseWaitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='etl-lease-wait-')
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'lease.sqlite'
        # A real product claim creates the lease and receipt; no hand-written
        # INSERT/UPDATE or lease-expiry manipulation is used by these fixtures.
        clock = _Clock(wall=START)
        with patch.object(etl_dispatch, 'time', clock):
            dispatcher = etl_dispatch.ETLDispatch(
                self.path, recovery.DemoIdentityProvider(), registry=recovery._registry(),
                lease_ttl_seconds=2)
            dispatcher._claim(recovery.ADMIN, dispatcher.registry[recovery.JOB], recovery.DAY,
                              'manual', 'manual:lease-wait', request_id='lease-wait')
        self.before = self._snapshot()
        self.expiry = self.before['etl_leases'][0]['expires_at']
        self.assertEqual(self.expiry, START + 2)
        self.helper = recovery.ETLRecoveryFaultTests()
        self.helper.path = self.path
        # Runs before TemporaryDirectory cleanup, even when an assertion fails.
        self.addCleanup(self._assert_durable_unchanged)

    def _snapshot(self):
        with closing(sqlite3.connect(str(self.path))) as connection:
            connection.row_factory = sqlite3.Row
            return {name: [dict(row) for row in connection.execute(
                'SELECT * FROM ' + name + ' ORDER BY rowid')] for name in TABLES}

    def _assert_durable_unchanged(self):
        self.assertEqual(self._snapshot(), self.before,
                         'Waiting must never alter leases, receipts, runs, steps or publications.')

    def _wait(self, clock):
        with patch.object(recovery, 'time', clock):
            self.helper._expire_naturally()

    def _assert_bounded_polls(self, clock):
        self.assertTrue(all(0 < seconds <= .05 for seconds in clock.sleeps))
        self.assertLessEqual(sum(clock.sleeps), 5.00000001)
        self.assertLessEqual(len(clock.sleeps), 101)

    def test_stable_progress_rechecks_real_expiry_with_bounded_polls(self):
        clock = _Clock()
        self._wait(clock)
        self.assertGreaterEqual(clock.wall, self.expiry)
        self.assertGreater(clock.wall_reads, 1)
        self.assertLess(clock.elapsed, 5)
        self._assert_bounded_polls(clock)

    def test_point_eight_rollback_breaks_old_single_sleep_but_new_wait_reaches_expiry(self):
        old = _Clock(rollback=.8)
        # Preserve the earlier helper's one-read/one-sleep arithmetic as the
        # counterexample. Its exact original method is also exercised by the
        # separate retained ETL clock-counterfactual diagnostic.
        remaining = self.expiry - old.time()
        self.assertLess(remaining, 5)
        if remaining > 0:
            old.sleep(remaining + .03)
        self.assertEqual(old.wall_reads, 1)
        self.assertLess(old.wall, self.expiry)
        self.assertAlmostEqual(self.expiry - old.wall, .77)
        fixed = _Clock(rollback=.8)
        self._wait(fixed)
        self.assertGreaterEqual(fixed.wall, self.expiry)
        self.assertGreater(fixed.elapsed, sum(old.sleeps))
        self.assertLess(fixed.elapsed, 5)
        self._assert_bounded_polls(fixed)

    def test_monotonic_forward_jump_cannot_masquerade_as_wall_expiry(self):
        clock = _Clock(monotonic_jump=10)
        with self.assertRaisesRegex(AssertionError, '5-second monotonic wait'):
            self._wait(clock)
        self.assertLess(clock.wall, self.expiry)
        self.assertEqual(len(clock.sleeps), 1)
        self._assert_bounded_polls(clock)

    def test_stuck_wall_fails_at_the_monotonic_budget(self):
        clock = _Clock(stuck_wall=True)
        with self.assertRaisesRegex(AssertionError, '5-second monotonic wait'):
            self._wait(clock)
        self.assertEqual(clock.wall, START + .5)
        self.assertAlmostEqual(clock.elapsed, 5)
        self._assert_bounded_polls(clock)

    def test_large_wall_rollback_fails_bounded_instead_of_extending_wait_forever(self):
        clock = _Clock(rollback=10)
        with self.assertRaisesRegex(AssertionError, '5-second monotonic wait'):
            self._wait(clock)
        self.assertLess(clock.wall, self.expiry)
        self.assertAlmostEqual(clock.elapsed, 5)
        self._assert_bounded_polls(clock)

    def test_already_expired_lease_returns_without_sleep(self):
        clock = _Clock(wall=self.expiry)
        self._wait(clock)
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(clock.wall_reads, 1)

    def test_wall_expiry_is_checked_before_terminal_deadline_failure(self):
        clock = _Clock(wall=self.expiry - .04, monotonic_jump=5)
        self._wait(clock)
        self.assertGreaterEqual(clock.wall, self.expiry)
        self.assertGreater(clock.elapsed, 5)
        self.assertEqual(len(clock.sleeps), 1)
        self._assert_bounded_polls(clock)

    def test_initial_five_second_bound_is_preserved(self):
        clock = _Clock(wall=self.expiry - 5)
        with self.assertRaises(AssertionError):
            self._wait(clock)
        self.assertEqual(clock.sleeps, [])


if __name__ == '__main__':
    unittest.main()