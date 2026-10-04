"""Independent-process maintenance writes, failure recovery and atomic audit.

Only temporary synthetic SQLite state is used. A barrier releases writers after
each process has read the same version; no application lock serializes the test.
Fault hooks live entirely in worker fixtures, never in the product repository.
"""

from contextlib import closing
import hashlib
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from reporting_workspace.crud import CrudError, ReportDefinitions
from reporting_workspace.state import BUSY_TIMEOUT_MS, StateStore


OWNER = dict(id='synthetic-owner', role='user', org='A')
ADMIN = dict(id='synthetic-admin', role='admin', org='A')
PEER = dict(id='synthetic-peer', role='user', org='A')
FOREIGN_OWNER = dict(OWNER, org='B')
FOREIGN_ADMIN = dict(ADMIN, org='B')


class _SyntheticIdentity:
    def __init__(self, actor, revoked=None):
        self.actor = dict(actor)
        self.revoked = revoked

    def get_user(self, identifier):
        if self.revoked is not None and self.revoked.is_set():
            return None
        return dict(self.actor) if identifier == self.actor['id'] else None


def _writer(path, job, ready, barrier, results, audit_ready=None,
            audit_release=None, revoked=None, transaction_attempted=None):
    """Spawn-safe worker: own store, provider, service and SQLite connections."""
    result = {'request_id': job['request_id'], 'pid': os.getpid()}
    try:
        store = StateStore(path)
        service = ReportDefinitions(store, _SyntheticIdentity(job['actor'], revoked))
        try:
            row = service.get(job['actor'], job['identifier'])
            result.update(read_status='ok', read_version=row['version'])
        except CrudError as error:
            result['read_status'] = type(error).__name__

        if transaction_attempted is not None:
            original_connect = store._connect

            def traced_connect():
                connection = original_connect()
                connection.set_trace_callback(
                    lambda sql: transaction_attempted.set() if sql == 'BEGIN IMMEDIATE' else None)
                return connection

            store._connect = traced_connect

        original_audit = store._audit
        if job.get('fault') in ('pause', 'audit_constraint'):
            def fault_audit(connection, *args, **kwargs):
                if job['fault'] == 'audit_constraint':
                    # SQLite itself rejects the unsupported outcome after the
                    # row UPDATE. No altered schema or synthetic exception.
                    broken = list(args)
                    broken[3] = 'invalid-outcome'
                    try:
                        return original_audit(connection, *broken, **kwargs)
                    except sqlite3.IntegrityError as error:
                        result['audit_error_type'] = type(error).__name__
                        raise
                value = original_audit(connection, *args, **kwargs)
                audit_ready.set()
                if not audit_release.wait(30):
                    raise RuntimeError('synthetic audit release timed out')
                return value

            store._audit = fault_audit

        ready.set()
        barrier.wait(timeout=30)
        started = time.perf_counter()
        try:
            operation = getattr(service, job['operation'])
            args = [job['actor'], job['identifier'], job['version']]
            if job['operation'] == 'update':
                args.append({'name': job['name']})
            row = operation(*args, request_id=job['request_id'])
            result.update(status='ok', row=row)
        except CrudError as error:
            result.update(status=type(error).__name__, message=str(error))
        result['elapsed_seconds'] = time.perf_counter() - started
    except BaseException as error:
        # Only type names are needed for fixture failures; do not emit state.
        result.update(status='worker_error', error=type(error).__name__)
        ready.set()
        try:
            barrier.abort()
        except Exception:
            pass
    results.put(result)


class ConcurrencyConsistencyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'synthetic-maintenance.sqlite'
        self.store = StateStore(self.path)
        self.service = ReportDefinitions(self.store, _SyntheticIdentity(OWNER))
        self.seed = self.service.create(OWNER, {'name': 'Original synthetic record'},
                                        request_id='seed-request')
        self.context = multiprocessing.get_context('spawn')

    def job(self, index, actor=OWNER, operation='update', version=1, **extra):
        result = dict(actor=dict(actor), operation=operation,
                      identifier=self.seed['id'], version=version,
                      request_id='writer-{}'.format(index),
                      name='Synthetic revision {}'.format(index))
        result.update(extra)
        return result

    def start_writers(self, jobs, **controls):
        barrier = self.context.Barrier(len(jobs) + 1)
        results = self.context.Queue()
        ready = [self.context.Event() for _ in jobs]
        processes = [self.context.Process(
            target=_writer, args=(str(self.path), job, signal, barrier, results),
            kwargs=controls) for job, signal in zip(jobs, ready)]

        def cleanup():
            for process in processes:
                if process.is_alive():
                    process.terminate()
                if process.pid is not None:
                    process.join(10)
            results.close()
            results.join_thread()

        self.addCleanup(cleanup)
        for process in processes:
            process.start()
        for signal in ready:
            self.assertTrue(signal.wait(30), 'writer failed to reach the version-read barrier')
        return processes, barrier, results

    def collect(self, processes, results):
        values = [results.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(10)
            self.assertEqual(process.exitcode, 0)
        self.assertFalse([value for value in values if value['status'] == 'worker_error'], values)
        self.assertEqual(len({value['pid'] for value in values}), len(processes))
        self.assertNotIn(os.getpid(), {value['pid'] for value in values})
        return values

    def race(self, jobs):
        processes, barrier, results = self.start_writers(jobs)
        barrier.wait(timeout=30)
        return self.collect(processes, results)

    def snapshot(self):
        """Read row + audit using one independent, consistent SQLite snapshot."""
        with closing(sqlite3.connect(str(self.path))) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute('BEGIN')
            row = dict(connection.execute(
                'SELECT * FROM report_definitions WHERE id = ?', (self.seed['id'],)).fetchone())
            events = [dict(item) for item in connection.execute(
                'SELECT * FROM audit_events ORDER BY event_id')]
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            connection.rollback()
        return row, events

    def assert_winner_audit(self, jobs, values, previous_events=1):
        winners = [value for value in values if value['status'] == 'ok']
        self.assertEqual(len(winners), 1, values)
        winner = winners[0]
        job = next(job for job in jobs if job['request_id'] == winner['request_id'])
        row, events = self.snapshot()
        self.assertEqual(len(events), previous_events + 1)
        self.assertEqual(row['version'], job['version'] + 1)
        for field in ('org', 'owner_id', 'description', 'cadence', 'created_at'):
            self.assertEqual(row[field], self.seed[field])
        self.assertEqual(row['enabled'], int(self.seed['enabled']))
        event = events[-1]
        self.assertEqual(event['request_id'], winner['request_id'])
        self.assertEqual(event['actor_id'], hashlib.sha256(job['actor']['id'].encode('utf-8')).hexdigest())
        self.assertEqual(event['resource_id'], self.seed['id'])
        self.assertEqual(event['outcome'], 'success')
        expected_event = {'update': 'crud.updated', 'soft_delete': 'crud.deleted',
                          'restore': 'crud.restored'}[job['operation']]
        self.assertEqual(event['event'], expected_event)
        self.assertEqual(row['name'], winner['row']['name'])
        self.assertEqual(row['deleted_at'], winner['row']['deleted_at'])
        self.assertNotIn('Synthetic revision', repr(events))
        return winner

    def test_six_owner_admin_writers_one_wins_then_explicit_reload_allows_retry(self):
        jobs = [self.job(index, actor=OWNER if index % 2 else ADMIN) for index in range(6)]
        values = self.race(jobs)
        self.assertEqual([value['read_version'] for value in values], [1] * 6)
        self.assertEqual(sorted(value['status'] for value in values), ['Conflict'] * 5 + ['ok'])
        winner = self.assert_winner_audit(jobs, values)
        # Failed writes do not automatically retry or silently overwrite the
        # winner. A fresh read/version is explicitly required for each retry.
        for job in jobs:
            if job['request_id'] == winner['request_id']:
                continue
            service = ReportDefinitions(StateStore(self.path), _SyntheticIdentity(job['actor']))
            current = service.get(job['actor'], self.seed['id'])
            updated = service.update(job['actor'], current['id'], current['version'],
                                     {'name': job['name']}, request_id=job['request_id'] + '-retry')
            self.assertEqual(updated['version'], current['version'] + 1)
        row, events = self.snapshot()
        self.assertEqual((row['version'], len(events)), (7, 7))
        self.assertEqual(len({event['request_id'] for event in events}), 7)

    def test_update_archive_and_restore_races_preserve_lifecycle_and_audit(self):
        jobs = [self.job(1), self.job(2, actor=ADMIN, operation='soft_delete'),
                self.job(3, operation='soft_delete'), self.job(4, actor=ADMIN)]
        values = self.race(jobs)
        self.assertEqual(sorted(value['status'] for value in values), ['Conflict'] * 3 + ['ok'])
        self.assert_winner_audit(jobs, values)
        current = self.service.get(OWNER, self.seed['id'])
        if current['deleted_at'] is None:
            current = self.service.soft_delete(OWNER, current['id'], current['version'],
                                               request_id='prepare-restore')
        before = len(self.store.list_audit())
        jobs = [self.job('restore-{}'.format(index), actor=OWNER if index % 2 else ADMIN,
                         operation='restore', version=current['version']) for index in range(4)]
        values = self.race(jobs)
        self.assertEqual(sorted(value['status'] for value in values), ['Conflict'] * 3 + ['ok'])
        self.assert_winner_audit(jobs, values, previous_events=before)
        self.assertIsNone(self.service.get(OWNER, self.seed['id'])['deleted_at'])

    def test_unauthorized_writers_keep_same_org_read_and_tenant_opacity_during_race(self):
        actors = [OWNER, ADMIN, PEER, FOREIGN_OWNER, FOREIGN_ADMIN]
        jobs = [self.job(index, actor=actor) for index, actor in enumerate(actors)]
        values = self.race(jobs)
        by_request = {value['request_id']: value for value in values}
        self.assertEqual(sorted(by_request['writer-{}'.format(index)]['status'] for index in (0, 1)),
                         ['Conflict', 'ok'])
        self.assertEqual(by_request['writer-2']['read_status'], 'ok')
        self.assertEqual(by_request['writer-2']['status'], 'AccessDenied')
        for index in (3, 4):
            value = by_request['writer-{}'.format(index)]
            self.assertEqual((value['read_status'], value['status']), ('NotFound', 'NotFound'))
            self.assertEqual(value['message'], 'Report definition not found.')
        self.assert_winner_audit(jobs, values)

    def test_real_write_lock_times_out_without_row_or_audit_then_explicit_retry_succeeds(self):
        jobs = [self.job('locked')]
        processes, barrier, results = self.start_writers(jobs)
        before = self.snapshot()
        with closing(sqlite3.connect(str(self.path), isolation_level=None)) as lock:
            lock.execute('BEGIN IMMEDIATE')
            barrier.wait(timeout=30)
            value = self.collect(processes, results)[0]
            self.assertEqual(value['status'], 'StateUnavailable')
            self.assertEqual(value['message'], 'Durable state is unavailable.')
            self.assertGreaterEqual(value['elapsed_seconds'], BUSY_TIMEOUT_MS / 1000.0 * 0.8)
            self.assertEqual(self.snapshot(), before)
            lock.rollback()
        updated = self.service.update(OWNER, self.seed['id'], 1, {'name': 'Explicit retry'},
                                      request_id='locked-retry')
        self.assertEqual(updated['version'], 2)
        self.assertEqual([event.request_id for event in self.store.list_audit()],
                         ['seed-request', 'locked-retry'])

    def test_independent_reader_never_sees_row_without_matching_committed_audit(self):
        audit_ready, audit_release = self.context.Event(), self.context.Event()
        jobs = [self.job('paused', fault='pause')]
        before = self.snapshot()
        processes, barrier, results = self.start_writers(
            jobs, audit_ready=audit_ready, audit_release=audit_release)
        barrier.wait(timeout=30)
        self.assertTrue(audit_ready.wait(20), 'writer did not reach the uncommitted audit')
        try:
            self.assertEqual(self.snapshot(), before)
        finally:
            audit_release.set()
        values = self.collect(processes, results)
        self.assert_winner_audit(jobs, values)

    def test_real_audit_constraint_failure_rolls_back_row_and_keeps_version_available(self):
        before = self.snapshot()
        values = self.race([self.job('constraint', fault='audit_constraint')])
        self.assertEqual(values[0]['status'], 'StateUnavailable')
        self.assertEqual(values[0]['audit_error_type'], 'IntegrityError')
        self.assertEqual(values[0]['message'], 'Durable state is unavailable.')
        self.assertEqual(self.snapshot(), before)
        updated = self.service.update(OWNER, self.seed['id'], 1, {'name': 'Recovered'},
                                      request_id='constraint-retry')
        self.assertEqual(updated['version'], 2)
        self.assertEqual([event.request_id for event in self.store.list_audit()],
                         ['seed-request', 'constraint-retry'])

    def test_process_termination_after_row_and_audit_rolls_back_both_and_releases_lock(self):
        audit_ready, audit_release = self.context.Event(), self.context.Event()
        before = self.snapshot()
        processes, barrier, results = self.start_writers(
            [self.job('terminated', fault='pause')],
            audit_ready=audit_ready, audit_release=audit_release)
        barrier.wait(timeout=30)
        self.assertTrue(audit_ready.wait(20), 'writer did not reach the uncommitted audit')
        self.assertEqual(self.snapshot(), before)
        processes[0].terminate()
        processes[0].join(10)
        self.assertFalse(processes[0].is_alive())
        self.assertNotEqual(processes[0].exitcode, 0)
        reopened = ReportDefinitions(StateStore(self.path), _SyntheticIdentity(OWNER))
        self.assertEqual(reopened.get(OWNER, self.seed['id']), self.seed)
        self.assertEqual(self.snapshot(), before)
        updated = reopened.update(OWNER, self.seed['id'], 1, {'name': 'After restart'},
                                  request_id='restart-retry')
        self.assertEqual(updated['version'], 2)
        self.assertEqual([event.request_id for event in self.store.list_audit()],
                         ['seed-request', 'restart-retry'])

    def test_identity_revoked_while_waiting_for_sqlite_is_rechecked_before_write(self):
        revoked, attempted = self.context.Event(), self.context.Event()
        before = self.snapshot()
        processes, barrier, results = self.start_writers(
            [self.job('revoked')], revoked=revoked, transaction_attempted=attempted)
        with closing(sqlite3.connect(str(self.path), isolation_level=None)) as lock:
            lock.execute('BEGIN IMMEDIATE')
            barrier.wait(timeout=30)
            self.assertTrue(attempted.wait(20), 'writer did not attempt BEGIN IMMEDIATE')
            revoked.set()
            lock.rollback()
        value = self.collect(processes, results)[0]
        self.assertEqual((value['read_status'], value['status']), ('ok', 'AccessDenied'))
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
