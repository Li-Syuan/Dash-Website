"""Real process/SQLite fault boundaries; synthetic data and no external effects.

Child processes pause inside real transactions, and the parent terminates them
without Python cleanup. Other cases use independent SQLite reader/writer locks.
Only the test subclass inserts observation barriers; SQLite commit/rollback and
the dispatcher state machine are the real implementation.
"""
from contextlib import closing, contextmanager
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from reporting_workspace.crud import Conflict, StateUnavailable
from reporting_workspace.etl_adapters import ETLRegistry, JobSpec, SnapshotAdapter, StepSpec
from reporting_workspace.etl_dispatch import ETLDispatch
from reporting_workspace.providers import DemoIdentityProvider


JOB = 'recovery-fixture'
DAY = '2000-01-01'
ADMIN = dict(id='demo-admin', role='admin', org='A')


def _registry(observe=None):
    def source(context, upstream):
        if observe:
            observe('source', context)
        return [dict(record_id='synthetic-one', value=7)]

    def publish(context, upstream):
        if observe:
            observe('publish', context)
        return [dict(row) for row in upstream['source']]

    return ETLRegistry((JobSpec(JOB, 'Recovery fixture', 'Isolated synthetic data.', 'A', (
        StepSpec('source', SnapshotAdapter('recovery-source', 'v1', source),
                 required_fields=('record_id', 'value'), unique_fields=('record_id',)),
        StepSpec('publish', SnapshotAdapter('recovery-publish', 'v1', publish), ('source',),
                 required_fields=('record_id', 'value'), count_matches='source'),
    ), 'publish'),))


class _ObservedDispatch(ETLDispatch):
    """Test-only barriers around real commits; never simulate transaction outcomes."""
    def __init__(self, *args, channel, phase=None, pause_day=DAY, **kwargs):
        self.channel = channel
        self.phase = phase
        self.pause_day = pause_day
        self.paused = False
        super().__init__(*args, **kwargs)

    def pause(self, phase):
        if self.phase == phase and not self.paused:
            self.paused = True
            self.channel.send(dict(kind='paused', phase=phase))
            if not self.channel.poll(20):
                raise AssertionError('Parent did not release the synthetic fault barrier.')
            self.channel.recv()

    @contextmanager
    def _transaction(self, initialize=False):
        try:
            with self._observed_transaction(initialize=initialize) as connection:
                yield connection
        except StateUnavailable:
            self.channel.send(dict(kind='storage_unavailable'))
            raise

    @contextmanager
    def _observed_transaction(self, initialize=False):
        published = False
        with super()._transaction(initialize=initialize) as connection:
            yield connection
            if not initialize and self.phase and not self.paused:
                run = connection.execute(
                    'SELECT * FROM etl_runs WHERE business_date=? ORDER BY started_at DESC LIMIT 1',
                    (self.pause_day,)).fetchone()
                if run is not None:
                    steps = [row[0] for row in connection.execute(
                        'SELECT status FROM etl_steps WHERE run_id=? ORDER BY ordinal', (run['run_id'],))]
                    if steps == ['pending', 'pending']:
                        self.pause('before_claim_commit')
                    if steps == ['succeeded', 'pending']:
                        self.pause('before_snapshot_commit')
                    if run['status'] == 'succeeded':
                        self.pause('before_publication_commit')
                        published = True
        if published:
            self.pause('after_publication_commit')


def _child(path, channel, options):
    """Spawn target: a fresh interpreter and identity provider on every restart."""
    def observe(step, context):
        channel.send(dict(kind='adapter', step=step, business_date=context['business_date']))
        if step == options.get('pause_step') and context['business_date'] == options.get('pause_day', DAY):
            dispatch.pause('adapter')

    try:
        dispatch = _ObservedDispatch(
            path, DemoIdentityProvider(), registry=_registry(observe), channel=channel,
            phase=options.get('phase'), pause_day=options.get('pause_day', DAY),
            lease_ttl_seconds=options.get('ttl', 2))
        channel.send(dict(kind='ready'))
        if not channel.poll(20):
            raise AssertionError('Parent did not start the synthetic operation.')
        channel.recv()
        operation = options.get('operation', 'run')
        if operation == 'backfill':
            result = dispatch.backfill(ADMIN, JOB, DAY, '2000-01-03', request_id='three-day-backfill')
        elif operation == 'retry':
            result = dispatch.retry(ADMIN, options['run_id'], request_id='unsafe-retry')
        else:
            result = dispatch.run_now(ADMIN, JOB, DAY, request_id=options.get('request', 'recovery-request'))
        channel.send(dict(kind='result', value=result))
    except (Conflict, StateUnavailable) as error:
        channel.send(dict(kind='error', error=type(error).__name__, message=str(error)))
    finally:
        channel.close()


class ETLRecoveryFaultTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='etl-fault-')
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'dispatch.sqlite'
        ETLDispatch(self.path, DemoIdentityProvider(), registry=_registry())
        self.context = multiprocessing.get_context('spawn')
        self.children = []
        self.addCleanup(self._close_children)

    def _close_children(self):
        for process, channel in self.children:
            if process.is_alive():
                process.terminate()
            process.join(5)
            channel.close()
            if process.is_alive():
                raise AssertionError('Synthetic ETL child did not stop.')
            process.close()

    def _start(self, **options):
        parent, child = self.context.Pipe()
        process = self.context.Process(target=_child, args=(str(self.path), child, options))
        process.start()
        child.close()
        self.children.append((process, parent))
        messages = self._until(parent, 'ready')
        return process, parent, messages

    def _until(self, channel, wanted):
        messages = []
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if not channel.poll(max(0, deadline - time.monotonic())):
                break
            try:
                message = channel.recv()
            except EOFError:
                self.fail('Synthetic ETL child exited before {}: {}'.format(wanted, messages))
            messages.append(message)
            if message['kind'] == wanted:
                return messages
            if message['kind'] == 'error':
                self.fail('Unexpected child result: {}'.format(message))
        self.fail('Synthetic ETL child did not reach {}: {}'.format(wanted, messages))

    def _run(self, **options):
        process, channel, messages = self._start(**options)
        channel.send('start')
        messages.extend(self._until(channel, 'result'))
        process.join(5)
        self.assertEqual(process.exitcode, 0)
        return messages[-1]['value'], [m for m in messages if m['kind'] == 'adapter']

    def _crash(self, **options):
        process, channel, messages = self._start(**options)
        channel.send('start')
        messages.extend(self._until(channel, 'paused'))
        process.terminate()
        process.join(5)
        self.assertFalse(process.is_alive())
        self.assertNotEqual(process.exitcode, 0)
        return [m for m in messages if m['kind'] == 'adapter']

    def _rows(self, sql, values=()):
        with closing(sqlite3.connect(str(self.path), timeout=1)) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(sql, values)]

    def _assert_integrity(self):
        self.assertEqual(self._rows('PRAGMA integrity_check'), [{'integrity_check': 'ok'}])
        self.assertEqual(self._rows('PRAGMA foreign_key_check'), [])

    def _expire_naturally(self):
        # Wait for the stored lease, not a guessed fixed sleep; never patch clocks
        # or rewrite durable lease values to manufacture recovery.
        expires = max(row['expires_at'] for row in self._rows('SELECT expires_at FROM etl_leases'))
        remaining = expires - time.time()
        self.assertLess(remaining, 5)
        if remaining > 0:
            time.sleep(remaining + .03)

    def test_kill_before_claim_commit_rolls_back_receipt_and_run(self):
        self.assertEqual(self._crash(phase='before_claim_commit'), [])
        for table in ('etl_runs', 'etl_requests', 'etl_leases', 'etl_steps', 'etl_publications'):
            self.assertEqual(self._rows('SELECT * FROM ' + table), [])
        result, calls = self._run()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual([call['step'] for call in calls], ['source', 'publish'])
        self.assertEqual(len(self._rows('SELECT * FROM etl_publications')), 1)
        self._assert_integrity()

    def test_kill_during_adapter_preserves_claim_and_denies_unsafe_retry(self):
        self.assertEqual(len(self._crash(phase='adapter', pause_step='source')), 1)
        self._expire_naturally()
        result, calls = self._run()
        self.assertEqual((result['status'], result['error_code']), ('failed', 'interrupted'))
        self.assertFalse(result['retry_allowed'])
        self.assertEqual(calls, [])
        self.assertEqual(len(self._rows('SELECT * FROM etl_runs')), 1)
        self.assertEqual(self._rows('SELECT * FROM etl_publications'), [])
        process, channel, _ = self._start(operation='retry', run_id=result['run_id'])
        channel.send('start')
        error = self._until(channel, 'error')[-1]
        self.assertEqual(error['error'], 'Conflict')
        process.join(5)
        self.assertEqual(process.exitcode, 0)
        fresh, calls = self._run(request='explicit-new-intent')
        self.assertEqual(fresh['status'], 'succeeded')
        self.assertNotEqual(fresh['run_id'], result['run_id'])
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(self._rows('SELECT * FROM etl_runs')), 2)
        self.assertEqual(self._rows('SELECT fencing FROM etl_publications'), [{'fencing': 2}])
        self._assert_integrity()

    def test_kill_before_snapshot_commit_keeps_no_partial_snapshot(self):
        self.assertEqual(len(self._crash(phase='before_snapshot_commit')), 1)
        source = self._rows("SELECT * FROM etl_steps WHERE name='source'")[0]
        self.assertEqual(source['status'], 'running')
        self.assertIsNone(source['snapshot'])
        self.assertIsNone(source['digest'])
        self._expire_naturally()
        result, calls = self._run()
        self.assertEqual(result['error_code'], 'interrupted')
        self.assertEqual(calls, [])
        self.assertEqual(self._rows('SELECT * FROM etl_publications'), [])
        self._assert_integrity()

    def test_kill_before_publication_commit_keeps_previous_publication(self):
        previous, _ = self._run(request='previous-good')
        before = self._rows('SELECT * FROM etl_publications')
        self.assertEqual(len(self._crash(phase='before_publication_commit')), 2)
        self.assertEqual(self._rows('SELECT * FROM etl_publications'), before)
        self._expire_naturally()
        result, calls = self._run()
        self.assertEqual(result['error_code'], 'interrupted')
        self.assertEqual([step['status'] for step in result['steps']], ['succeeded', 'succeeded'])
        self.assertFalse(result['retry_allowed'])
        self.assertEqual(calls, [])
        self.assertEqual(self._rows('SELECT * FROM etl_publications'), before)
        self.assertNotEqual(result['run_id'], previous['run_id'])
        self._assert_integrity()

    def test_kill_after_publication_commit_before_ack_replays_only_receipt(self):
        self.assertEqual(len(self._crash(phase='after_publication_commit')), 2)
        before = self._rows('SELECT * FROM etl_publications')
        self.assertEqual(len(before), 1)
        result, calls = self._run()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['run_id'], before[0]['run_id'])
        self.assertEqual(calls, [])
        self.assertEqual(self._rows('SELECT * FROM etl_publications'), before)
        self.assertEqual(len(self._rows('SELECT * FROM etl_runs')), 1)
        self._assert_integrity()

    def test_partial_backfill_restart_continues_only_unclaimed_dates(self):
        calls = self._crash(operation='backfill', phase='adapter', pause_step='source',
                            pause_day='2000-01-02')
        self.assertEqual([call['business_date'] for call in calls], [DAY, DAY, '2000-01-02'])
        first = self._rows('SELECT * FROM etl_publications')[0]
        self._expire_naturally()
        result, calls = self._run(operation='backfill')
        self.assertEqual([run['status'] for run in result['runs']], ['succeeded', 'failed', 'succeeded'])
        self.assertEqual(result['runs'][1]['error_code'], 'interrupted')
        self.assertEqual([call['business_date'] for call in calls], ['2000-01-03', '2000-01-03'])
        self.assertEqual(self._rows('SELECT * FROM etl_publications WHERE business_date=?', (DAY,)), [first])
        self.assertEqual(len(self._rows('SELECT * FROM etl_runs')), 3)
        repeated, calls = self._run(operation='backfill')
        self.assertEqual(calls, [])
        self.assertEqual([r['run_id'] for r in repeated['runs']], [r['run_id'] for r in result['runs']])
        self.assertEqual(len(self._rows('SELECT * FROM etl_publications')), 2)
        self._assert_integrity()

    def test_writer_lock_timeout_creates_no_claim_then_same_request_succeeds(self):
        process, channel, _ = self._start(ttl=30)
        with closing(sqlite3.connect(str(self.path), isolation_level=None)) as blocker:
            blocker.execute('BEGIN IMMEDIATE')
            channel.send('start')
            error = self._until(channel, 'error')[-1]
            self.assertEqual(error['error'], 'StateUnavailable')
            self.assertEqual(error['message'], 'Local durable ETL storage is unavailable.')
            self.assertEqual(self._rows('SELECT * FROM etl_requests'), [])
            self.assertEqual(self._rows('SELECT * FROM etl_runs'), [])
            blocker.rollback()
        process.join(5)
        self.assertEqual(process.exitcode, 0)
        result, calls = self._run()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(len(calls), 2)
        self._assert_integrity()

    def test_transient_writer_lock_waits_then_commits_once(self):
        process, channel, _ = self._start(ttl=30)
        with closing(sqlite3.connect(str(self.path), isolation_level=None)) as blocker:
            blocker.execute('BEGIN IMMEDIATE')
            channel.send('start')
            self.assertFalse(channel.poll(.2), 'Adapter ran before the durable claim could commit.')
            blocker.rollback()
        messages = self._until(channel, 'result')
        self.assertEqual(messages[-1]['value']['status'], 'succeeded')
        self.assertEqual(len([m for m in messages if m['kind'] == 'adapter']), 2)
        process.join(5)
        self.assertEqual(process.exitcode, 0)
        result, calls = self._run()
        self.assertEqual(result['run_id'], messages[-1]['value']['run_id'])
        self.assertEqual(calls, [])
        self.assertEqual(len(self._rows('SELECT * FROM etl_publications')), 1)
        self._assert_integrity()

    def test_writer_lock_after_adapter_keeps_prior_snapshot_without_replay(self):
        previous, _ = self._run(request='previous-good')
        publication = self._rows('SELECT * FROM etl_publications')
        process, channel, _ = self._start(phase='adapter', pause_step='publish', ttl=30)
        channel.send('start')
        messages = self._until(channel, 'paused')
        self.assertEqual(len([m for m in messages if m['kind'] == 'adapter']), 2)
        with closing(sqlite3.connect(str(self.path), isolation_level=None)) as blocker:
            blocker.execute('BEGIN IMMEDIATE')
            channel.send('continue')
            self._until(channel, 'storage_unavailable')
            blocker.rollback()
        failed = self._until(channel, 'result')[-1]['value']
        process.join(5)
        self.assertEqual(process.exitcode, 0)
        self.assertEqual((failed['status'], failed['error_code']), ('failed', 'storage_unavailable'))
        self.assertEqual([step['status'] for step in failed['steps']], ['succeeded', 'failed'])
        self.assertFalse(failed['retry_allowed'])
        self.assertEqual(self._rows('SELECT * FROM etl_publications'), publication)
        repeated, calls = self._run()
        self.assertEqual(repeated['run_id'], failed['run_id'])
        self.assertEqual(calls, [])
        self.assertNotEqual(repeated['run_id'], previous['run_id'])
        self._assert_integrity()

    def test_reader_lock_at_publication_commit_rolls_back_publication_and_success(self):
        self._run(request='previous-good')
        publication = self._rows('SELECT * FROM etl_publications')
        process, channel, _ = self._start(phase='before_publication_commit', ttl=30)
        channel.send('start')
        self._until(channel, 'paused')
        with closing(sqlite3.connect(str(self.path), isolation_level=None)) as blocker:
            blocker.execute('BEGIN')
            visible = blocker.execute('SELECT run_id FROM etl_publications').fetchone()[0]
            self.assertEqual(visible, publication[0]['run_id'])
            channel.send('continue')
            # A real SHARED reader prevents rollback-journal commit. Keep it
            # until SQLite itself reports timeout; do not mock OperationalError.
            self._until(channel, 'storage_unavailable')
            blocker.rollback()
        failed = self._until(channel, 'result')[-1]['value']
        process.join(5)
        self.assertEqual(process.exitcode, 0)
        self.assertEqual((failed['status'], failed['error_code']), ('failed', 'storage_unavailable'))
        self.assertEqual([step['status'] for step in failed['steps']], ['succeeded', 'succeeded'])
        self.assertFalse(failed['retry_allowed'])
        self.assertEqual(self._rows('SELECT * FROM etl_publications'), publication)
        repeated, calls = self._run()
        self.assertEqual(repeated['run_id'], failed['run_id'])
        self.assertEqual(calls, [])
        self._assert_integrity()


if __name__ == '__main__':
    unittest.main()
