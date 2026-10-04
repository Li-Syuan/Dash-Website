"""Independent ETL acceptance: local durable state and actual HTTP, not visual QA.

Each test uses synthetic fixtures and an isolated local SQLite database. No
network, company adapters, credentials or real message delivery are exercised.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
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
from reporting_workspace.providers import DemoIdentityProvider
from reporting_workspace.etl_dispatch import ETLDispatch


ADMIN = dict(id='acceptance-admin-a', role='admin', org='A')
USER = dict(id='acceptance-user-a', role='user', org='A')
FOREIGN = dict(id='acceptance-admin-b', role='admin', org='B')
SALES = 'synthetic-sales-daily'
INVENTORY = 'synthetic-inventory-health'


def identities():
    return DemoIdentityProvider({user['id']: dict(password='synthetic-test-only',
        role=user['role'], org=user['org']) for user in (ADMIN, USER, FOREIGN)})


def database_dump(path):
    with sqlite3.connect(str(path)) as connection:
        return tuple(connection.iterdump())


class ETLDispatchAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='etl-acceptance-')
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'dispatch.sqlite'
        self.identities = identities()
        self.dispatch = self.make_dispatch()
        self.yesterday = (date.today() - timedelta(days=1)).isoformat()

    def make_dispatch(self, **kwargs):
        dispatch = ETLDispatch(self.path, self.identities, **kwargs)
        self.addCleanup(dispatch.stop)
        return dispatch

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(.01)
        self.fail('The real worker did not reach the required observable state.')

    def test_registered_jobs_are_separate_and_start_disabled(self):
        jobs = self.dispatch.jobs(USER)
        self.assertEqual({item['job_id'] for item in jobs}, {SALES, INVENTORY})
        for item in jobs:
            status = self.dispatch.status(USER, item['job_id'])
            self.assertFalse(status['enabled'])
            self.assertFalse(status['worker_running'])
            self.assertEqual(status['runs'], [])
            self.assertIsNone(status['publication'])
            self.assertFalse(status['can_manage'])
        self.assertTrue(self.dispatch.status(ADMIN, SALES)['can_manage'])

    def test_repeat_request_survives_new_service_instance_without_new_run(self):
        first = self.dispatch.run_now(ADMIN, SALES, self.yesterday, request_id='accept-repeat')
        self.assertEqual(first['status'], 'succeeded')
        second = self.make_dispatch()
        duplicate = second.run_now(ADMIN, SALES, self.yesterday, request_id='accept-repeat')
        self.assertEqual(duplicate['run_id'], first['run_id'])
        self.assertEqual(len(second.status(USER, SALES)['runs']), 1)
        self.assertEqual(second.status(USER, INVENTORY)['runs'], [])

    def test_reader_mutations_and_current_identity_spoofs_are_denied(self):
        existing = self.dispatch.run_now(ADMIN, SALES, self.yesterday, request_id='reader-denial')
        for action in (
                lambda: self.dispatch.configure(USER, SALES, True, 60),
                lambda: self.dispatch.run_now(USER, SALES),
                lambda: self.dispatch.backfill(USER, SALES, self.yesterday, self.yesterday),
                lambda: self.dispatch.retry(USER, existing['run_id'])):
            with self.assertRaises(AccessDenied):
                action()
        for actor in (None, {}, dict(USER, role='admin'), dict(ADMIN, org='B'),
                      dict(ADMIN, id='missing'), dict(ADMIN, role='user')):
            with self.subTest(actor=actor), self.assertRaises(AccessDenied):
                self.dispatch.jobs(actor)
        self.identities.user_db.pop(USER['id'])
        with self.assertRaises(AccessDenied):
            self.dispatch.status(USER, SALES)

    def test_revoked_admin_cannot_repeat_prior_idempotent_action(self):
        first = self.dispatch.run_now(ADMIN, SALES, self.yesterday, request_id='accept-revoke')
        self.identities.user_db[ADMIN['id']]['role'] = 'user'
        with self.assertRaises(AccessDenied):
            self.dispatch.run_now(ADMIN, SALES, self.yesterday, request_id='accept-revoke')
        self.assertEqual(self.dispatch.status(USER, SALES)['publication']['run_id'], first['run_id'])

    def test_unknown_job_and_cross_tenant_read_do_not_reveal_run(self):
        first = self.dispatch.run_now(ADMIN, SALES, self.yesterday, request_id='accept-private')
        for action in (
                lambda: self.dispatch.status(USER, 'unregistered-job'),
                lambda: self.dispatch.run_now(ADMIN, 'unregistered-job'),
                lambda: self.dispatch.run_detail(USER, 'unregistered-run')):
            with self.assertRaises(NotFound):
                action()
        for action in (lambda: self.dispatch.status(FOREIGN, SALES),
                       lambda: self.dispatch.run_detail(FOREIGN, first['run_id']),
                       lambda: self.dispatch.run_now(FOREIGN, SALES)):
            with self.assertRaises((AccessDenied, NotFound)):
                action()

    def test_malformed_future_reversed_and_oversized_backfill_do_not_mutate(self):
        before = database_dump(self.path)
        for value in ('2026-1-2', '2026-02-30', 'not-a-date', '', 'x' * 10000,
                      '2026-01-01T00:00:00Z', 1, True, [], {},
                      (date.today() + timedelta(days=3)).isoformat()):
            with self.subTest(value=str(value)[:50]), self.assertRaises(ValidationError):
                self.dispatch.run_now(ADMIN, SALES, value, request_id='accept-bad-date')
        for start, end in ((self.yesterday, '1900-01-01'),
                           ('1900-01-01', self.yesterday),
                           (None, self.yesterday), (self.yesterday, None)):
            with self.subTest(start=start, end=end), self.assertRaises(ValidationError):
                self.dispatch.backfill(ADMIN, SALES, start, end, request_id='accept-bad-backfill')
        self.assertEqual(database_dump(self.path), before)

    def test_invalid_schedule_types_and_stale_version_are_nonmutating(self):
        initial = self.dispatch.status(ADMIN, SALES)
        before = database_dump(self.path)
        for interval in (None, '60', True, False, 0, -1, 59, 86401,
                         float('nan'), float('inf'), 10 ** 400):
            with self.subTest(interval=str(interval)[:40]), self.assertRaises(ValidationError):
                self.dispatch.configure(ADMIN, SALES, True, interval)
        for enabled in (0, 1, None, 'true', [], {}):
            with self.subTest(enabled=enabled), self.assertRaises(ValidationError):
                self.dispatch.configure(ADMIN, SALES, enabled, 60)
        self.assertEqual(database_dump(self.path), before)
        changed = self.dispatch.configure(ADMIN, SALES, True, 60,
                                          expected_version=initial['version'])
        with self.assertRaises(Conflict):
            self.dispatch.configure(ADMIN, SALES, False, 300,
                                    expected_version=initial['version'])
        self.assertEqual(self.dispatch.status(ADMIN, SALES)['version'], changed['version'])
        self.assertTrue(self.dispatch.status(ADMIN, SALES)['enabled'])

    def test_public_results_exclude_actor_leases_paths_and_provider_error(self):
        self.dispatch.run_now(ADMIN, SALES, self.yesterday, request_id='accept-safe')
        result = json.dumps(self.dispatch.status(USER, SALES), ensure_ascii=False)
        for private in (ADMIN['id'], 'synthetic-test-only', self.dispatch.path,
                        'lease_token', 'lease_owner'):
            self.assertNotIn(private, result)
        with patch.object(self.identities, 'get_user', side_effect=RuntimeError('PRIVATE ERROR WITH SECRET')):
            with self.assertRaises(AccessDenied) as raised:
                self.dispatch.jobs(USER)
            self.assertNotIn('PRIVATE', str(raised.exception))

    def test_real_worker_runs_enabled_job_and_leaves_other_job_untouched(self):
        dispatch = self.make_dispatch(min_interval_seconds=.05, poll_interval_seconds=.01)
        dispatch.configure(ADMIN, SALES, True, .05)
        self.assertFalse(dispatch.status(USER, SALES)['worker_running'])
        self.assertEqual(dispatch.status(USER, SALES)['runs'], [])
        dispatch.start()
        result = self.wait_for(lambda: next((run for run in dispatch.status(USER, SALES)['runs']
            if run['status'] == 'succeeded' and run['trigger'] == 'timer'), None))
        self.assertTrue(result['run_id'])
        dispatch.configure(ADMIN, SALES, False, .05)
        dispatch.stop()
        count = len(dispatch.status(USER, SALES)['runs'])
        time.sleep(.08)
        self.assertEqual(len(dispatch.status(USER, SALES)['runs']), count)
        self.assertEqual(dispatch.status(USER, INVENTORY)['runs'], [])
        self.assertFalse(dispatch.status(USER, SALES)['worker_running'])


from reporting_workspace.etl_adapters import ETLRegistry, JobSpec, SnapshotAdapter, StepSpec


def rows_source(context, upstream):
    return [dict(record_id='synthetic-one', value=1), dict(record_id='synthetic-two', value=2)]


def rows_copy(context, upstream):
    return [dict(row) for row in upstream[next(iter(upstream))]]


def pipeline(source=rows_source, validate=rows_copy, final=rows_copy,
             retry_safe=True, org='A', allowed=()):
    return JobSpec('acceptance-pipeline', 'Acceptance synthetic pipeline',
                   'Isolated synthetic adapter acceptance fixture.', org, (
        StepSpec('source', SnapshotAdapter('acceptance-source', 'v1', source, retry_safe),
                 required_fields=('record_id', 'value'), unique_fields=('record_id',)),
        StepSpec('validate', SnapshotAdapter('acceptance-validate', 'v1', validate), ('source',),
                 required_fields=('record_id', 'value'), unique_fields=('record_id',),
                 nonnegative_fields=('value',), count_matches='source'),
        StepSpec('publish', SnapshotAdapter('acceptance-publish', 'v1', final), ('validate',),
                 required_fields=('record_id', 'value'), unique_fields=('record_id',),
                 count_matches='validate')), 'publish', allowed_user_ids=allowed)


def hold_in_separate_process(path, entered):
    """Independent process deliberately leaves only local synthetic work pending."""
    def source(context, upstream):
        entered.set()
        time.sleep(30)
        return rows_source(context, upstream)
    service = ETLDispatch(path, identities(), registry=ETLRegistry((pipeline(source=source),)),
                          lease_ttl_seconds=.5)
    service.run_now(ADMIN, 'acceptance-pipeline',
        (date.today() - timedelta(days=1)).isoformat(), request_id='child-process')


class ETLAdapterBoundaryAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='etl-adversarial-')
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'dispatch.sqlite'
        self.identities = identities()
        self.yesterday = (date.today() - timedelta(days=1)).isoformat()

    def service(self, specification=None, **kwargs):
        service = ETLDispatch(self.path, self.identities,
            registry=ETLRegistry((specification or pipeline(),)), **kwargs)
        self.addCleanup(service.stop)
        return service

    def test_cycles_unknown_dependencies_and_unchecked_branch_rejected(self):
        adapter = SnapshotAdapter('acceptance-source', 'v1', rows_source)
        cases = [
            (StepSpec('a', adapter, ('b',)), StepSpec('b', adapter, ('a',))),
            (StepSpec('a', adapter, ('missing',)), StepSpec('b', adapter, ('a',))),
            (StepSpec('a', adapter), StepSpec('b', adapter)),
            (StepSpec('a', adapter, ('a',)), StepSpec('b', adapter, ('a',))),
        ]
        for steps in cases:
            with self.subTest(steps=steps), self.assertRaises(ValueError):
                JobSpec('bad-pipeline', 'Bad fixture', 'Must be rejected.', 'A', steps, 'b')
        self.assertFalse(self.path.exists())

    def test_topological_order_comes_from_dependencies_not_input_order(self):
        original = pipeline()
        reordered = JobSpec(original.job_id, original.name, original.description,
                            original.org, tuple(reversed(original.steps)), original.publish_step)
        self.assertEqual([step.name for step in reordered.steps], ['source', 'validate', 'publish'])
        run = self.service(reordered).run_now(ADMIN, original.job_id, self.yesterday,
                                            request_id='topological')
        self.assertEqual(run['status'], 'succeeded')
        self.assertEqual([step['name'] for step in run['steps']], ['source', 'validate', 'publish'])

    def test_count_loss_blocks_publication_and_skips_dependent_step(self):
        service = self.service(pipeline(validate=lambda context, upstream: upstream['source'][:1]))
        run = service.run_now(ADMIN, 'acceptance-pipeline', self.yesterday, request_id='count-loss')
        self.assertEqual(run['status'], 'failed')
        self.assertEqual([step['status'] for step in run['steps']], ['succeeded', 'failed', 'skipped'])
        self.assertIsNone(service.status(USER, 'acceptance-pipeline')['publication'])
        self.assertNotEqual(run['error_code'], None)

    def test_adapter_invalid_rows_and_counts_fail_closed_without_secret_text(self):
        invalid = (None, True, 1, 'bad', {}, [], [1],
                   [dict(record_id='x', value=float('nan'))],
                   [dict(record_id='x', value=float('inf'))],
                   [dict(record_id='x', value='PRIVATE' * 50000)],
                   [dict(record_id='x', value=1)] * 1001,
                   [dict(record_id='x', value=1), dict(record_id='x', value=2)])
        for index, rows in enumerate(invalid):
            path = self.path.with_name('invalid-{}.sqlite'.format(index))
            service = ETLDispatch(path, self.identities, registry=ETLRegistry((
                pipeline(source=lambda context, upstream, value=rows: value),)))
            self.addCleanup(service.stop)
            with self.subTest(index=index):
                run = service.run_now(ADMIN, 'acceptance-pipeline', self.yesterday,
                                      request_id='bad-rows-{}'.format(index))
                self.assertEqual(run['status'], 'failed')
                self.assertEqual([step['status'] for step in run['steps']], ['failed', 'skipped', 'skipped'])
                self.assertIsNone(service.status(USER, 'acceptance-pipeline')['publication'])
                self.assertNotIn('PRIVATE', json.dumps(run))

    def test_nonnegative_numeric_fields_reject_bool_strings_negative_and_overflow(self):
        for index, value in enumerate((True, False, '1', None, -1, 10 ** 400)):
            path = self.path.with_name('numeric-{}.sqlite'.format(index))
            def transform(context, upstream, number=value):
                return [dict(row, value=number) for row in upstream['source']]
            service = ETLDispatch(path, self.identities,
                                  registry=ETLRegistry((pipeline(validate=transform),)))
            self.addCleanup(service.stop)
            with self.subTest(value=str(value)[:40]):
                run = service.run_now(ADMIN, 'acceptance-pipeline', self.yesterday,
                                      request_id='numeric-{}'.format(index))
                self.assertEqual(run['status'], 'failed')
                self.assertEqual([step['status'] for step in run['steps']], ['succeeded', 'failed', 'skipped'])
                self.assertIsNone(service.status(USER, 'acceptance-pipeline')['publication'])

    def test_safe_retry_reuses_verified_successful_upstream_snapshot(self):
        calls = {'source': 0, 'validate': 0, 'publish': 0}
        def source(context, upstream):
            calls['source'] += 1
            return rows_source(context, upstream)
        def validate(context, upstream):
            calls['validate'] += 1
            if calls['validate'] == 1:
                raise RuntimeError('PRIVATE transient synthetic failure')
            return rows_copy(context, upstream)
        def final(context, upstream):
            calls['publish'] += 1
            return rows_copy(context, upstream)
        spec = pipeline(source=source, validate=validate, final=final)
        service = self.service(spec)
        failed = service.run_now(ADMIN, spec.job_id, self.yesterday, request_id='retry-root')
        self.assertEqual(failed['status'], 'failed')
        retried = service.retry(ADMIN, failed['run_id'], request_id='retry-once')
        self.assertEqual(retried['status'], 'succeeded')
        self.assertNotEqual(retried['run_id'], failed['run_id'])
        self.assertEqual(retried['parent_run_id'], failed['run_id'])
        self.assertEqual(retried['root_run_id'], failed['run_id'])
        self.assertEqual(retried['business_date'], failed['business_date'])
        self.assertEqual(calls, {'source': 1, 'validate': 2, 'publish': 1})
        duplicate = self.service(spec).retry(ADMIN, failed['run_id'], request_id='retry-once')
        self.assertEqual(duplicate['run_id'], retried['run_id'])
        self.assertEqual(calls, {'source': 1, 'validate': 2, 'publish': 1})
        self.assertEqual(service.run_detail(USER, failed['run_id'])['status'], 'failed')
        self.assertNotIn('PRIVATE', json.dumps(service.status(USER, spec.job_id)))

    def test_unsafe_failed_step_cannot_be_automatically_retried(self):
        calls = []
        def unsafe(context, upstream):
            calls.append(context['run_id'])
            raise RuntimeError('Synthetic uncertain adapter outcome')
        service = self.service(pipeline(source=unsafe, retry_safe=False))
        failed = service.run_now(ADMIN, 'acceptance-pipeline', self.yesterday,
                                 request_id='unsafe-root')
        self.assertEqual(failed['status'], 'failed')
        with self.assertRaises((Conflict, ValidationError)):
            service.retry(ADMIN, failed['run_id'], request_id='unsafe-repeat')
        self.assertEqual(len(calls), 1)

    def test_same_job_nonoverlap_uses_shared_database_not_instance_lock(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def held(context, upstream):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Synthetic test release timeout')
            return rows_source(context, upstream)
        spec = pipeline(source=held)
        first, second = self.service(spec), self.service(spec)
        with ThreadPoolExecutor(max_workers=2) as pool:
            running = pool.submit(first.run_now, ADMIN, spec.job_id, self.yesterday,
                                  request_id='held-first')
            self.assertTrue(entered.wait(3))
            with self.assertRaises(Conflict):
                second.run_now(ADMIN, spec.job_id, self.yesterday, request_id='held-second')
            self.assertEqual(len(second.status(USER, spec.job_id)['runs']), 1)
            release.set()
            self.assertEqual(running.result(3)['status'], 'succeeded')

    def test_expired_worker_is_fenced_after_successor_publishes(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        lock, calls = threading.Lock(), []
        def held_first(context, upstream):
            with lock:
                calls.append(context['run_id'])
                first_call = len(calls) == 1
            if first_call:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError('Synthetic test release timeout')
            return rows_source(context, upstream)
        spec = pipeline(source=held_first)
        first, successor = self.service(spec, lease_ttl_seconds=.05), self.service(spec)
        with ThreadPoolExecutor(max_workers=2) as pool:
            stale = pool.submit(first.run_now, ADMIN, spec.job_id, self.yesterday,
                                request_id='stale-root')
            self.assertTrue(entered.wait(3))
            time.sleep(.10)
            fresh = successor.run_now(ADMIN, spec.job_id, self.yesterday,
                                      request_id='fresh-root')
            self.assertEqual(fresh['status'], 'succeeded')
            release.set()
            self.assertNotEqual(stale.result(3)['status'], 'succeeded')
        self.assertEqual(successor.status(USER, spec.job_id)['publication']['run_id'], fresh['run_id'])

    def test_identity_revocation_inside_adapter_blocks_publication(self):
        def revoke(context, upstream):
            self.identities.user_db[ADMIN['id']]['role'] = 'user'
            return rows_source(context, upstream)
        service = self.service(pipeline(source=revoke))
        with self.assertRaises(AccessDenied):
            service.run_now(ADMIN, 'acceptance-pipeline', self.yesterday,
                            request_id='revoke-during-source')
        state = service.status(USER, 'acceptance-pipeline')
        self.assertEqual(state['runs'][0]['status'], 'failed')
        self.assertEqual(state['runs'][0]['error_code'], 'permission_revoked')
        self.assertIsNone(state['publication'])

    def test_job_user_allowlist_applies_before_snapshot_and_run_reads(self):
        service = self.service(pipeline(allowed=(ADMIN['id'],)))
        result = service.run_now(ADMIN, 'acceptance-pipeline', self.yesterday,
                                 request_id='private-allowlist')
        self.assertEqual(service.jobs(USER), [])
        for action in (lambda: service.status(USER, 'acceptance-pipeline'),
                       lambda: service.run_detail(USER, result['run_id'])):
            with self.assertRaises((NotFound, AccessDenied)):
                action()

    def test_failed_later_attempt_preserves_last_successful_publication(self):
        attempts = []
        def source(context, upstream):
            attempts.append(context['run_id'])
            if len(attempts) > 1:
                raise RuntimeError('PRIVATE failure after a good publication')
            return rows_source(context, upstream)
        service = self.service(pipeline(source=source))
        succeeded = service.run_now(ADMIN, 'acceptance-pipeline', self.yesterday,
                                    request_id='publication-good')
        publication = service.status(USER, 'acceptance-pipeline')['publication']
        failed = service.run_now(ADMIN, 'acceptance-pipeline', self.yesterday,
                                 request_id='publication-failed')
        self.assertEqual(succeeded['status'], 'succeeded')
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(service.status(USER, 'acceptance-pipeline')['publication'], publication)

    def test_registry_jobs_steps_and_quality_bounds_are_explicit(self):
        adapter = SnapshotAdapter('acceptance-adapter', 'v1', rows_source)
        for bounds in ((True, 2), (0, True), (-1, 1), (2, 1), (1, 1001), (1.0, 2)):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                StepSpec('source', adapter, min_rows=bounds[0], max_rows=bounds[1])
        with self.assertRaises(ValueError):
            StepSpec('validate', adapter, ('source',), count_matches='other')
        with self.assertRaises(ValueError):
            ETLRegistry((pipeline(), pipeline()))
        with self.assertRaises(ValueError):
            ETLRegistry().snapshot()
        too_many = tuple(StepSpec('step-{}'.format(index), adapter) for index in range(21))
        with self.assertRaises(ValueError):
            JobSpec('too-many', 'Too many', 'Invalid registry fixture.', 'A', too_many, 'step-20')

    def test_request_key_cannot_replay_with_a_different_business_date(self):
        service = self.service()
        service.run_now(ADMIN, 'acceptance-pipeline', self.yesterday, request_id='date-bound')
        before = database_dump(self.path)
        other_date = (date.today() - timedelta(days=2)).isoformat()
        with self.assertRaises((Conflict, ValidationError)):
            service.run_now(ADMIN, 'acceptance-pipeline', other_date, request_id='date-bound')
        self.assertEqual(database_dump(self.path), before)

    def test_backfill_is_inclusive_and_deduplicates_all_dates_after_restart(self):
        service = self.service()
        start = (date.today() - timedelta(days=3)).isoformat()
        first = service.backfill(ADMIN, 'acceptance-pipeline', start, self.yesterday,
                                 request_id='backfill-bounded')
        self.assertEqual(len(first['runs']), 3)
        self.assertEqual({run['business_date'] for run in first['runs']},
                         {(date.today() - timedelta(days=offset)).isoformat() for offset in (1, 2, 3)})
        self.assertTrue(all(run['status'] == 'succeeded' for run in first['runs']))
        second = self.service().backfill(ADMIN, 'acceptance-pipeline', start, self.yesterday,
                                        request_id='backfill-bounded')
        self.assertEqual([run['run_id'] for run in first['runs']],
                         [run['run_id'] for run in second['runs']])
        self.assertEqual(len(service.status(USER, 'acceptance-pipeline')['runs']), 3)

    def test_process_exit_recovers_as_interrupted_without_replaying_request(self):
        service = self.service()
        context = multiprocessing.get_context('spawn')
        entered = context.Event()
        process = context.Process(target=hold_in_separate_process,
                                  args=(str(self.path), entered))
        process.start()
        try:
            self.assertTrue(entered.wait(10))
            with self.assertRaises(Conflict):
                service.run_now(ADMIN, 'acceptance-pipeline', self.yesterday,
                                request_id='while-child-running')
            process.terminate()
            process.join(5)
            self.assertFalse(process.is_alive())
            time.sleep(.55)
            recovered = service.run_now(ADMIN, 'acceptance-pipeline', self.yesterday,
                                        request_id='child-process')
            self.assertNotEqual(recovered['status'], 'succeeded')
            self.assertIsNone(service.status(USER, 'acceptance-pipeline')['publication'])
            new_run = service.run_now(ADMIN, 'acceptance-pipeline', self.yesterday,
                                      request_id='explicit-after-interruption')
            self.assertEqual(new_run['status'], 'succeeded')
            self.assertNotEqual(recovered['run_id'], new_run['run_id'])
        finally:
            if process.is_alive():
                process.terminate()
            process.join(5)


    def test_retry_attempt_limit_is_three_and_does_not_replay_failed_ancestor(self):
        calls = []
        def failing(context, upstream):
            calls.append(context['run_id'])
            raise RuntimeError('Synthetic repeated transient failure')
        service = self.service(pipeline(validate=failing))
        first = service.run_now(ADMIN, 'acceptance-pipeline', self.yesterday, request_id='attempt-one')
        second = service.retry(ADMIN, first['run_id'], request_id='attempt-two')
        third = service.retry(ADMIN, second['run_id'], request_id='attempt-three')
        self.assertEqual([first['attempt'], second['attempt'], third['attempt']], [1, 2, 3])
        self.assertFalse(third['retry_allowed'])
        with self.assertRaises(Conflict):
            service.retry(ADMIN, third['run_id'], request_id='attempt-four')
        replay = service.retry(ADMIN, first['run_id'], request_id='ancestor-repeat')
        self.assertEqual(replay['run_id'], second['run_id'])
        self.assertEqual(len(calls), 3)

    def test_corrupt_successful_snapshot_metadata_cannot_be_used_for_retry(self):
        def failing(context, upstream):
            raise RuntimeError('Synthetic failure after upstream success')
        for column, value in (('row_count', 1), ('digest', 'f' * 64),
                              ('snapshot', '[]'), ('checks_json', '[]'),
                              ('reused_from_run_id', 'f' * 32)):
            path = self.path.with_name('corrupt-{}.sqlite'.format(column))
            service = ETLDispatch(path, self.identities, registry=ETLRegistry((pipeline(validate=failing),)))
            self.addCleanup(service.stop)
            failed = service.run_now(ADMIN, 'acceptance-pipeline', self.yesterday,
                                     request_id='corrupt-' + column)
            with sqlite3.connect(str(path)) as connection:
                connection.execute('UPDATE etl_steps SET ' + column + '=? WHERE run_id=? AND name=?',
                                   (value, failed['run_id'], 'source'))
            before = database_dump(path)
            with self.subTest(column=column):
                self.assertFalse(service.run_detail(ADMIN, failed['run_id'])['retry_allowed'])
                with self.assertRaises(Conflict):
                    service.retry(ADMIN, failed['run_id'], request_id='reject-' + column)
                self.assertEqual(database_dump(path), before)

    def test_current_registry_signature_change_refuses_old_storage(self):
        service = self.service()
        service.run_now(ADMIN, 'acceptance-pipeline', self.yesterday, request_id='registry-original')
        original = pipeline()
        steps = list(original.steps)
        steps[0] = StepSpec('source', SnapshotAdapter('acceptance-source', 'v2', rows_source),
                            required_fields=('record_id', 'value'), unique_fields=('record_id',))
        changed = JobSpec(original.job_id, original.name, original.description, original.org,
                          tuple(steps), original.publish_step)
        before = database_dump(self.path)
        with self.assertRaises(StateUnavailable):
            self.service(changed)
        self.assertEqual(database_dump(self.path), before)

    def test_config_change_inside_step_blocks_stale_publication(self):
        holder = {}
        def reconfigure(context, upstream):
            holder['service'].configure(ADMIN, 'acceptance-pipeline', True, 60)
            return rows_source(context, upstream)
        service = self.service(pipeline(source=reconfigure))
        holder['service'] = service
        result = service.run_now(ADMIN, 'acceptance-pipeline', self.yesterday,
                                 request_id='configuration-changed')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error_code'], 'configuration_changed')
        self.assertIsNone(service.status(USER, 'acceptance-pipeline')['publication'])
        self.assertFalse(result['retry_allowed'])

    def test_display_limit_retains_old_request_receipts_durably(self):
        service = self.service()
        oldest = service.run_now(ADMIN, 'acceptance-pipeline', self.yesterday,
                                 request_id='retained-oldest')
        for index in range(100):
            service.run_now(ADMIN, 'acceptance-pipeline', self.yesterday,
                            request_id='recent-{}'.format(index))
        visible = service.status(USER, 'acceptance-pipeline')['runs']
        self.assertEqual(len(visible), 100)
        self.assertNotIn(oldest['run_id'], {run['run_id'] for run in visible})
        duplicate = self.service().run_now(ADMIN, 'acceptance-pipeline', self.yesterday,
                                          request_id='retained-oldest')
        self.assertEqual(duplicate['run_id'], oldest['run_id'])
        with sqlite3.connect(str(self.path)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM etl_runs').fetchone()[0], 101)


    def test_reused_snapshot_origin_must_retain_valid_quality_counts(self):
        def failing(context, upstream):
            raise RuntimeError('Synthetic transient failure')
        service = self.service(pipeline(validate=failing))
        first = service.run_now(ADMIN, 'acceptance-pipeline', self.yesterday, request_id='origin-root')
        second = service.retry(ADMIN, first['run_id'], request_id='origin-second')
        self.assertEqual(second['steps'][0]['reused_from_run_id'], first['run_id'])
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute('UPDATE etl_steps SET row_count=1 WHERE run_id=? AND name=?',
                               (first['run_id'], 'source'))
        before = database_dump(self.path)
        self.assertFalse(service.run_detail(ADMIN, second['run_id'])['retry_allowed'])
        with self.assertRaises(Conflict):
            service.retry(ADMIN, second['run_id'], request_id='origin-reject')
        self.assertEqual(database_dump(self.path), before)

    def test_reused_snapshot_origin_must_be_an_ancestor_in_same_retry_chain(self):
        def failing(context, upstream):
            raise RuntimeError('Synthetic transient failure')
        service = self.service(pipeline(validate=failing))
        first = service.run_now(ADMIN, 'acceptance-pipeline', self.yesterday, request_id='lineage-root')
        unrelated = service.run_now(ADMIN, 'acceptance-pipeline', self.yesterday,
                                    request_id='lineage-unrelated')
        second = service.retry(ADMIN, first['run_id'], request_id='lineage-second')
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute('UPDATE etl_steps SET reused_from_run_id=? WHERE run_id=? AND name=?',
                               (unrelated['run_id'], second['run_id'], 'source'))
        before = database_dump(self.path)
        self.assertFalse(service.run_detail(ADMIN, second['run_id'])['retry_allowed'])
        with self.assertRaises(Conflict):
            service.retry(ADMIN, second['run_id'], request_id='lineage-reject')
        self.assertEqual(database_dump(self.path), before)


import test_unified_portal as http_helpers


class ETLHTTPAcceptanceTests(unittest.TestCase):
    setUp = http_helpers.UnifiedPortalTransportTests.setUp
    start_app = http_helpers.UnifiedPortalTransportTests.start_app
    close_app = http_helpers.UnifiedPortalTransportTests.close_app
    tearDown = http_helpers.UnifiedPortalTransportTests.tearDown
    call = http_helpers.UnifiedPortalTransportTests.call
    login = http_helpers.UnifiedPortalTransportTests.login
    page = http_helpers.UnifiedPortalTransportTests.page

    def form(self, job=SALES, business_date=None, start=None, end=None, run_id=None,
             previous=None, trigger='etl-job'):
        day = business_date or (date.today() - timedelta(days=1)).isoformat()
        values = {'etl-job': job, 'etl-business-date.date': day,
                  'etl-backfill-dates.start_date': start or day,
                  'etl-backfill-dates.end_date': end or day, 'etl-run-id': run_id}
        if previous:
            values.update({key: value['data'] for key, value in previous.items() if key.endswith('-request')})
        tokens = self.call('etl-run-request.data', trigger, values)
        values.update({key: value['data'] for key, value in tokens.items()})
        return values, tokens

    def engine(self):
        return self.server.extensions['etl_dispatch']

    def actor(self):
        return self.identities.get_user('demo-admin')

    def preview(self, values):
        return self.call('etl-backfill-modal.is_open', 'etl-backfill-preview', values)

    def test_page_navigation_layout_and_readonly_controls_share_main_login(self):
        self.login()
        self.assertEqual(self.client.get('/QA_portal/etl').status_code, 200)
        layout = self.page('/QA_portal/etl')['_pages_content']['children']
        controls = {node['props'].get('id'): node['props'] for node in http_helpers.components(layout)
                    if node['props'].get('id')}
        for identifier in ('etl-job', 'etl-business-date', 'etl-backfill-dates', 'etl-backfill-modal',
                           'etl-run-request', 'etl-run-id', 'etl-detail', 'etl-save', 'etl-run'):
            self.assertIn(identifier, controls)
        self.assertEqual(set(option['value'] for option in controls['etl-job']['options']),
                         {SALES, INVENTORY})
        ids = [node['props']['id'] for node in http_helpers.components(layout) if 'id' in node['props']]
        self.assertEqual(len(ids), len(set(ids)))
        home = self.page('/')['_pages_content']['children']
        self.assertIn('/QA_portal/etl', [node['props'].get('href') for node in http_helpers.components(home)])
        self.login('demo-user-a')
        layout = self.page('/QA_portal/etl')['_pages_content']['children']
        controls = {node['props'].get('id'): node['props'] for node in http_helpers.components(layout)
                    if node['props'].get('id')}
        for identifier in ('etl-save', 'etl-run', 'etl-backfill-preview', 'etl-backfill-confirm',
                           'etl-new-request', 'etl-retry', 'etl-business-date', 'etl-backfill-dates'):
            self.assertTrue(controls[identifier]['disabled'], identifier)

    def test_callback_transport_denies_anonymous_foreign_and_readonly_writes(self):
        self.call('etl-status.children', 'etl-refresh', {'etl-job': SALES}, expected=401)
        self.call('etl-run-result.children', 'etl-run', {'etl-job': SALES}, expected=401)
        self.login('demo-user-b')
        self.call('etl-status.children', 'etl-refresh', {'etl-job': SALES}, expected=403)
        self.login('demo-user-a')
        self.call('etl-status.children', 'etl-refresh', {'etl-job': SALES})
        for output, trigger in (('etl-run-result.children', 'etl-run'), ('etl-save-result.children', 'etl-save'),
                                ('etl-backfill-result.children', 'etl-backfill-confirm'),
                                ('etl-retry-result.children', 'etl-retry')):
            self.call(output, trigger, {'etl-job': SALES}, expected=403)
        self.assertEqual(self.engine().status(self.actor(), SALES)['runs'], [])

    def test_real_callback_repeat_request_is_durable_after_factory_restart(self):
        self.login()
        values, _ = self.form()
        first = self.call('etl-run-result.children', 'etl-run', values)
        second = self.call('etl-run-result.children', 'etl-run', values)
        self.assertEqual(first['etl-run-event']['data'], second['etl-run-event']['data'])
        self.assertEqual(len(self.engine().status(self.actor(), SALES)['runs']), 1)
        self.start_app()
        self.login()
        third = self.call('etl-run-result.children', 'etl-run', values)
        self.assertEqual(first['etl-run-event']['data'], third['etl-run-event']['data'])
        self.assertEqual(len(self.engine().status(self.actor(), SALES)['runs']), 1)

    def test_poll_updates_status_without_overwriting_selected_job_or_unsaved_form(self):
        self.login()
        response = self.call('etl-status.children', 'etl-poll', {'etl-job': INVENTORY, 'etl-poll': 1})
        self.assertIn(INVENTORY, json.dumps(response))
        self.assertNotIn('etl-job', response)
        self.assertNotIn('etl-enabled', response)
        self.assertNotIn('etl-interval', response)
        self.assertNotIn('etl-version', response)
        self.assertNotIn('etl-run-id', {key: value for key, value in response.items() if 'value' in value})

    def test_unrelated_form_change_retains_manual_request_but_reset_rotates(self):
        self.login()
        values, tokens = self.form()
        changed_start = (date.today() - timedelta(days=2)).isoformat()
        _, changed = self.form(start=changed_start, previous=tokens, trigger='etl-backfill-dates')
        self.assertEqual(changed['etl-run-request'], tokens['etl-run-request'])
        self.assertNotEqual(changed['etl-backfill-request'], tokens['etl-backfill-request'])
        _, reset = self.form(previous=changed, trigger='etl-new-request')
        self.assertNotEqual(reset['etl-run-request'], changed['etl-run-request'])

    def test_cancelled_preview_cannot_be_confirmed_from_stale_client_proof(self):
        self.login()
        values, _ = self.form()
        preview = self.preview(values)
        self.assertTrue(preview['etl-backfill-modal']['is_open'])
        proof = preview['etl-backfill-proof']['data']
        values['etl-backfill-proof'] = proof
        canceled = self.call('etl-backfill-modal.is_open', 'etl-backfill-cancel', values)
        self.assertFalse(canceled['etl-backfill-modal']['is_open'])
        self.assertIsNone(canceled['etl-backfill-proof']['data'])
        rejected = self.call('etl-backfill-result.children', 'etl-backfill-confirm', values)
        self.assertNotIn('job_id', rejected.get('etl-backfill-event', {}).get('data', {}))
        self.assertEqual(self.engine().status(self.actor(), SALES)['runs'], [])

    def test_changed_input_revokes_preview_and_changed_job_cannot_confirm(self):
        self.login()
        values, _ = self.form()
        preview = self.preview(values)
        values['etl-backfill-proof'] = preview['etl-backfill-proof']['data']
        changed = dict(values, **{'etl-job': INVENTORY})
        response = self.call('etl-backfill-modal.is_open', 'etl-job', changed)
        self.assertFalse(response['etl-backfill-modal']['is_open'])
        rejected = self.call('etl-backfill-result.children', 'etl-backfill-confirm', values)
        self.assertNotIn('job_id', rejected.get('etl-backfill-event', {}).get('data', {}))
        self.assertEqual(self.engine().status(self.actor(), SALES)['runs'], [])
        self.assertEqual(self.engine().status(self.actor(), INVENTORY)['runs'], [])

    def test_backfill_confirm_repeat_and_new_preview_are_durable_without_duplicate_dates(self):
        self.login()
        start = (date.today() - timedelta(days=2)).isoformat()
        values, _ = self.form(start=start)
        preview = self.preview(values)
        values['etl-backfill-proof'] = preview['etl-backfill-proof']['data']
        confirmed = self.call('etl-backfill-result.children', 'etl-backfill-confirm', values)
        self.assertIn('etl-backfill-event', confirmed)
        first = self.engine().status(self.actor(), SALES)['runs']
        self.assertEqual(len(first), 2)
        self.call('etl-backfill-result.children', 'etl-backfill-confirm', values)
        preview = self.preview(values)
        values['etl-backfill-proof'] = preview['etl-backfill-proof']['data']
        self.call('etl-backfill-result.children', 'etl-backfill-confirm', values)
        self.assertEqual([run['run_id'] for run in self.engine().status(self.actor(), SALES)['runs']],
                         [run['run_id'] for run in first])

    def test_cross_job_run_selection_cannot_detail_or_retry_different_job(self):
        self.login()
        values, _ = self.form()
        run = self.call('etl-run-result.children', 'etl-run', values)['etl-run-event']['data']['run_id']
        values, _ = self.form(job=INVENTORY, run_id=run)
        result = self.call('etl-detail.children', 'etl-run-id', values)
        self.assertTrue(result['etl-retry']['disabled'])
        self.assertNotIn('source', json.dumps(result))
        result = self.call('etl-retry-result.children', 'etl-retry', values)
        self.assertNotIn('etl-retry-event', result)
        self.assertEqual(self.engine().status(self.actor(), INVENTORY)['runs'], [])

    def test_malformed_or_future_dates_and_forged_tokens_create_no_runs(self):
        self.login()
        values, _ = self.form()
        for day in ('2026-02-30', '2026-1-1', 'x' * 10000,
                    (date.today() + timedelta(days=3)).isoformat()):
            changed = dict(values, **{'etl-business-date.date': day})
            result = self.call('etl-run-result.children', 'etl-run', changed)
            self.assertNotIn('etl-run-event', result)
        changed = dict(values, **{'etl-run-request': {'request_id': 'f' * 32}})
        result = self.call('etl-run-result.children', 'etl-run', changed)
        self.assertNotIn('etl-run-event', result)
        for start, end in (('1900-01-01', '2026-10-01'), ('2026-10-01', '1900-01-01')):
            changed = dict(values, **{'etl-backfill-dates.start_date': start,
                                      'etl-backfill-dates.end_date': end})
            result = self.preview(changed)
            self.assertFalse(result['etl-backfill-modal']['is_open'])
            self.assertIsNone(result['etl-backfill-proof']['data'])
        self.assertEqual(self.engine().status(self.actor(), SALES)['runs'], [])

    def test_cross_origin_and_revoked_session_fail_before_dispatch(self):
        self.login()
        values, _ = self.form()
        self.call('etl-run-result.children', 'etl-run', values,
                  headers={'Origin': 'https://untrusted.invalid'}, expected=403)
        self.identities.user_db.pop('demo-admin')
        self.call('etl-run-result.children', 'etl-run', values, expected=401)

    def test_factory_stays_idle_and_explicit_launcher_helpers_start_stop_real_workers(self):
        from reporting_workspace.launcher import start_background_services, stop_background_services
        service = self.engine()
        self.assertFalse(service.worker_running)
        service.configure(self.actor(), SALES, True, 60)
        self.assertFalse(service.worker_running)
        self.addCleanup(stop_background_services, self.server)
        start_background_services(self.server)
        self.assertTrue(service.worker_running)
        start_background_services(self.server)
        self.assertTrue(service.worker_running)
        stop_background_services(self.server)
        self.assertFalse(service.worker_running)
        stop_background_services(self.server)
        self.assertEqual(service.status(self.actor(), SALES)['runs'], [])

    def test_http_quality_failure_retry_preserves_parent_and_upstream_provenance(self):
        self.login()
        service = self.engine()
        original = service._snapshot
        injected = []
        def one_count_loss(rows, step, upstream):
            if step.name == 'validate' and not injected:
                injected.append(True)
                rows = rows[:-1]
            return original(rows, step, upstream)
        values, _ = self.form()
        with patch.object(service, '_snapshot', side_effect=one_count_loss):
            response = self.call('etl-run-result.children', 'etl-run', values)
            failed_id = response['etl-run-event']['data']['run_id']
            failed = service.run_detail(self.actor(), failed_id)
            self.assertEqual(failed['status'], 'failed')
            self.assertEqual(failed['error_code'], 'quality_failed')
            self.assertEqual([step['status'] for step in failed['steps']], ['succeeded', 'failed', 'skipped'])
            values, _ = self.form(run_id=failed_id)
            detail = self.call('etl-detail.children', 'etl-run-id', values)
            self.assertFalse(detail['etl-retry']['disabled'])
            self.assertIn('count_match', json.dumps(detail))
            response = self.call('etl-retry-result.children', 'etl-retry', values)
            recovered_id = response['etl-retry-event']['data']['run_id']
            recovered = service.run_detail(self.actor(), recovered_id)
            self.assertEqual(recovered['status'], 'succeeded')
            self.assertEqual(recovered['parent_run_id'], failed_id)
            self.assertEqual(recovered['root_run_id'], failed_id)
            self.assertEqual(recovered['steps'][0]['reused_from_run_id'], failed_id)
            duplicate = self.call('etl-retry-result.children', 'etl-retry', values)
            self.assertEqual(duplicate['etl-retry-event']['data']['run_id'], recovered_id)
        self.assertEqual(service.run_detail(self.actor(), failed_id)['status'], 'failed')
        self.assertEqual(service.status(self.actor(), SALES)['publication']['run_id'], recovered_id)
