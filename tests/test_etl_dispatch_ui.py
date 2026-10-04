"""Offline ETL workbench layout, HTTP mutation boundaries and launcher lifecycle."""
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_unified_portal as helpers
from reporting_workspace.launcher import start_background_services, stop_background_services
from reporting_workspace.ui_pages.etl_dispatch import _Confirmations, _request, _today, _range
from reporting_workspace.crud import ValidationError


JOB = 'synthetic-sales-daily'
OTHER_JOB = 'synthetic-inventory-health'
DAY = '2020-01-02'


class ConfirmationTests(unittest.TestCase):
    def test_confirmation_binds_actor_job_dates_and_request(self):
        for index, replacement in [(0, 'other'), (1, OTHER_JOB), (2, '2020-01-01'),
                                   (3, '2020-01-03'), (4, 'different-request')]:
            store = _Confirmations()
            args = ['actor', JOB, DAY, DAY, 'request']
            proof = store.issue(*args)
            args[index] = replacement
            with self.assertRaises(ValidationError):
                store.consume(proof, *args)

    def test_cancel_supersede_expiry_and_consume_are_fail_closed(self):
        store = _Confirmations()
        args = ['actor', JOB, DAY, DAY, 'request']
        first = store.issue(*args)
        second = store.issue(*args)
        with self.assertRaises(ValidationError):
            store.consume(first, *args)
        store.revoke(second, 'actor')
        with self.assertRaises(ValidationError):
            store.consume(second, *args)
        with patch('reporting_workspace.ui_pages.etl_dispatch.time.monotonic', return_value=0):
            expired = store.issue(*args)
        with patch('reporting_workspace.ui_pages.etl_dispatch.time.monotonic', return_value=301):
            with self.assertRaises(ValidationError):
                store.consume(expired, *args)
        once = store.issue(*args)
        store.consume(once, *args)
        with self.assertRaises(ValidationError):
            store.consume(once, *args)

    def test_request_rotation_is_explicit_or_bound_input_change_only(self):
        initial = _request('run', [JOB, DAY])
        self.assertEqual(_request('run', [JOB, DAY], initial), initial)
        self.assertNotEqual(_request('run', [OTHER_JOB, DAY], initial)['request_id'], initial['request_id'])
        self.assertNotEqual(_request('run', [JOB, DAY], initial, True)['request_id'], initial['request_id'])

    def test_ui_date_bounds_are_inclusive_and_disallow_future(self):
        from datetime import date
        self.assertEqual(_range('2020-01-01', '2020-01-31'), 31)
        for start, end in [('2020-01-01', '2020-02-01'), ('2020-01-02', '2020-01-01'),
                           (DAY, (date.fromisoformat(_today()) + timedelta(days=1)).isoformat()),
                           ('2020-1-1', DAY), (None, DAY)]:
            with self.assertRaises(ValidationError):
                _range(start, end)


class LauncherLifecycleTests(unittest.TestCase):
    def worker(self, events, name, fail_start=False, fail_stop=False):
        def start():
            events.append(name + '.start')
            if fail_start:
                raise RuntimeError('synthetic start failure')
        def stop():
            events.append(name + '.stop')
            if fail_stop:
                raise RuntimeError('synthetic stop failure')
        return SimpleNamespace(start=start, stop=stop)

    def test_launcher_owns_both_workers_and_stops_in_reverse_order(self):
        events = []
        server = SimpleNamespace(extensions={key: self.worker(events, key) for key in ('job_monitor', 'etl_dispatch')})
        start_background_services(server)
        stop_background_services(server)
        self.assertEqual(events, ['job_monitor.start', 'etl_dispatch.start', 'etl_dispatch.stop', 'job_monitor.stop'])

    def test_partial_start_rolls_back_old_worker(self):
        events = []
        server = SimpleNamespace(extensions={'job_monitor': self.worker(events, 'old'),
            'etl_dispatch': self.worker(events, 'new', fail_start=True)})
        with self.assertRaises(RuntimeError):
            start_background_services(server)
        self.assertEqual(events, ['old.start', 'new.start', 'old.stop'])

    def test_failed_stop_does_not_skip_other_worker(self):
        events = []
        server = SimpleNamespace(extensions={'job_monitor': self.worker(events, 'old'),
            'etl_dispatch': self.worker(events, 'new', fail_stop=True)})
        with self.assertRaises(RuntimeError):
            stop_background_services(server)
        self.assertEqual(events, ['new.stop', 'old.stop'])


class ETLTransportTests(unittest.TestCase):
    setUp = helpers.UnifiedPortalTransportTests.setUp
    start_app = helpers.UnifiedPortalTransportTests.start_app
    close_app = helpers.UnifiedPortalTransportTests.close_app
    tearDown = helpers.UnifiedPortalTransportTests.tearDown
    call = helpers.UnifiedPortalTransportTests.call
    login = helpers.UnifiedPortalTransportTests.login
    page = helpers.UnifiedPortalTransportTests.page

    @property
    def service(self):
        return self.server.extensions['etl_dispatch']

    @property
    def admin(self):
        return self.identities.get_user('demo-admin')

    def fill_values(self, **overrides):
        values = {'etl-job': JOB, 'etl-backfill-dates.start_date': DAY,
                  'etl-backfill-dates.end_date': DAY,
                  'etl-backfill-request': _request('backfill', [JOB, DAY, DAY]),
                  'etl-backfill-proof': None}
        values.update(overrides)
        return values

    def preview(self, values):
        result = self.call('etl-backfill-modal.is_open', 'etl-backfill-preview', values)
        return result['etl-backfill-proof']['data']

    def test_page_has_jobs_readonly_controls_and_no_duplicate_component_ids(self):
        self.assertFalse(self.service.worker_running)
        self.login('demo-user-a')
        page = self.page('/QA_portal/etl')
        component_list = list(helpers.components(page['_pages_content']['children']))
        components = {node['props']['id']: node['props'] for node in component_list if isinstance(node['props'].get('id'), str)}
        ids = [node['props']['id'] for node in component_list if isinstance(node['props'].get('id'), str)]
        self.assertEqual(len(ids), len(set(ids)))
        for control in ['etl-save', 'etl-run', 'etl-retry', 'etl-backfill-preview', 'etl-backfill-confirm',
                        'etl-enabled', 'etl-interval', 'etl-business-date', 'etl-backfill-dates', 'etl-new-request']:
            self.assertTrue(components[control]['disabled'], control)
        self.assertIn(JOB, str(page))
        self.assertIn(OTHER_JOB, str(page))
        self.assertEqual(components['etl-backfill-dates']['max_date_allowed'], _today())
        response = self.client.get('/assets/etl_dispatch.css')
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertFalse(self.service.worker_running)

    def test_status_transport_anonymous_wrong_org_and_readonly(self):
        self.call('etl-status.children', 'etl-refresh', {'etl-job': JOB}, expected=401)
        self.login('demo-user-b')
        self.call('etl-status.children', 'etl-refresh', {'etl-job': JOB}, expected=403)
        self.assertNotIn('etl-job-cards', str(self.page('/QA_portal/etl')))
        self.login('demo-user-a')
        result = self.call('etl-status.children', 'etl-refresh', {'etl-job': JOB})
        self.assertIn('尚未啟動', str(result))
        self.assertIn('depends_on', str(self.service.status(self.admin, JOB)))

    def test_mutations_deny_readonly_on_transport_boundary(self):
        self.login('demo-user-a')
        for output, trigger in [('etl-run-result.children', 'etl-run'), ('etl-save-result.children', 'etl-save'),
                                ('etl-retry-result.children', 'etl-retry'), ('etl-backfill-result.children', 'etl-backfill-confirm')]:
            self.call(output, trigger, expected=403)
        values = self.fill_values()
        self.assertIsNone(self.preview(values))
        self.assertEqual(self.service.status(self.admin, JOB)['runs'], [])

    def test_repeat_manual_click_returns_original_run_until_explicit_reset(self):
        self.login()
        values = {'etl-job': JOB, 'etl-business-date': DAY, 'etl-run-request': _request('run', [JOB, DAY])}
        first = self.call('etl-run-result.children', 'etl-run', values)
        second = self.call('etl-run-result.children', 'etl-run', values)
        self.assertEqual(first['etl-run-event'], second['etl-run-event'])
        self.assertEqual(len(self.service.status(self.admin, JOB)['runs']), 1)
        values['etl-run-request'] = _request('run', [JOB, DAY], renew=True)
        third = self.call('etl-run-result.children', 'etl-run', values)
        self.assertNotEqual(first['etl-run-event'], third['etl-run-event'])
        self.assertEqual(len(self.service.status(self.admin, JOB)['runs']), 2)

    def test_mount_and_ambiguous_actions_never_execute(self):
        self.login()
        values = {'etl-job': JOB, 'etl-business-date': DAY, 'etl-run-request': _request('run', [JOB, DAY]), 'etl-run': 0}
        self.call('etl-run-result.children', 'etl-run', values, expected=204)
        values['etl-run'] = 1
        self.call('etl-run-result.children', 'etl-run', values, expected=204,
                  changed=['etl-run.n_clicks', 'etl-job.value'])
        fill = self.fill_values(**{'etl-backfill-preview': 0})
        self.assertIsNone(self.preview(fill))
        fill['etl-backfill-preview'] = 1
        result = self.call('etl-backfill-modal.is_open', 'etl-backfill-preview', fill,
                           changed=['etl-backfill-preview.n_clicks', 'etl-backfill-cancel.n_clicks'])
        self.assertFalse(result['etl-backfill-modal']['is_open'])
        self.call('etl-backfill-result.children', 'etl-backfill-confirm', fill, expected=204,
                  changed=['etl-backfill-confirm.n_clicks', 'etl-backfill-cancel.n_clicks'])
        self.assertEqual(self.service.status(self.admin, JOB)['runs'], [])

    def test_backfill_preview_cancel_replay_and_input_change_do_not_write(self):
        self.login()
        values = self.fill_values()
        proof = self.preview(values)
        self.assertTrue(proof)
        self.assertEqual(self.service.status(self.admin, JOB)['runs'], [])
        values['etl-backfill-proof'] = proof
        canceled = self.call('etl-backfill-modal.is_open', 'etl-backfill-cancel', values)
        self.assertFalse(canceled['etl-backfill-modal']['is_open'])
        result = self.call('etl-backfill-result.children', 'etl-backfill-confirm', values)
        self.assertIn('已取消或已使用', str(result))
        values['etl-backfill-proof'] = self.preview(values)
        changed = dict(values, **{'etl-job': OTHER_JOB})
        self.call('etl-backfill-modal.is_open', 'etl-job', changed)
        self.call('etl-backfill-result.children', 'etl-backfill-confirm', values)
        self.assertEqual(self.service.status(self.admin, JOB)['runs'], [])

    def test_valid_backfill_confirmation_runs_once_and_detail_is_allowlisted(self):
        self.login()
        values = self.fill_values()
        values['etl-backfill-proof'] = self.preview(values)
        self.call('etl-backfill-result.children', 'etl-backfill-confirm', values)
        self.call('etl-backfill-result.children', 'etl-backfill-confirm', values)
        runs = self.service.status(self.admin, JOB)['runs']
        self.assertEqual(len(runs), 1)
        result = self.call('etl-detail.children', 'etl-run-id', {'etl-job': JOB, 'etl-run-id': runs[0]['run_id']})
        self.assertIn('批次明細', str(result))
        self.assertIn('沿用來源批次', str(result))
        for secret in ['snapshot_json', 'source_rows', 'lease_token', 'adapter_kwargs', 'sqlite_path']:
            self.assertNotIn(secret, str(result))

    def test_schedule_version_conflict_and_poll_preserves_form(self):
        self.login()
        initial = self.call('etl-enabled.value', 'etl-job', {'etl-job': JOB})
        values = {'etl-job': JOB, 'etl-enabled': True, 'etl-interval': 360,
                  'etl-version': initial['etl-version']['data']}
        self.call('etl-save-result.children', 'etl-save', values)
        conflict = self.call('etl-save-result.children', 'etl-save', values)
        self.assertIn('狀態衝突', str(conflict))
        polled = self.call('etl-status.children', 'etl-poll', {'etl-job': JOB})
        self.assertNotIn('etl-enabled', polled)
        self.assertNotIn('etl-interval', polled)
        self.assertNotIn('etl-job', polled)
        self.assertEqual(self.service.status(self.admin, JOB)['interval_seconds'], 360)
        invalid = self.call('etl-enabled.value', 'etl-job', {'etl-job': 'missing-job'})
        self.assertIsNone(invalid['etl-version']['data'])

    def test_schedule_ui_never_drops_optimistic_version_or_accepts_fractional_seconds(self):
        self.login()
        initial = self.service.status(self.admin, JOB)
        for version, interval in [(None, 300), (True, 300), (0, 300), (initial['version'], 300.5)]:
            with self.subTest(version=version, interval=interval):
                values = {'etl-job': JOB, 'etl-enabled': True, 'etl-interval': interval,
                          'etl-version': {'job_id': JOB, 'version': version}}
                result = self.call('etl-save-result.children', 'etl-save', values)
                self.assertNotIn('etl-save-event', result)
                self.assertFalse(self.service.status(self.admin, JOB)['enabled'])
                self.assertEqual(self.service.status(self.admin, JOB)['version'], initial['version'])

    def test_other_job_run_selection_does_not_render_or_retry(self):
        self.login()
        run = self.service.run_now(self.admin, JOB, business_date=DAY)
        values = {'etl-job': OTHER_JOB, 'etl-run-id': run['run_id'],
                  'etl-retry-request': _request('retry', [OTHER_JOB, run['run_id']])}
        result = self.call('etl-detail.children', 'etl-run-id', values)
        self.assertIn('屬於其他工作', str(result))
        self.assertNotIn(run['run_id'], str(result))
        self.assertTrue(result['etl-retry']['disabled'])
        self.call('etl-retry-result.children', 'etl-retry', values)
        self.assertEqual(self.service.status(self.admin, OTHER_JOB)['runs'], [])

    def test_request_callback_preserves_unrelated_form_tokens(self):
        self.login()
        run = _request('run', [JOB, DAY])
        fill = _request('backfill', [JOB, DAY, DAY])
        retry = _request('retry', [JOB, None])
        values = {'etl-job': JOB, 'etl-business-date': DAY, 'etl-backfill-dates.start_date': DAY,
                  'etl-backfill-dates.end_date': '2020-01-03', 'etl-run-id': None,
                  'etl-run-request': run, 'etl-backfill-request': fill, 'etl-retry-request': retry}
        result = self.call('etl-run-request.data', 'etl-backfill-dates', values)
        self.assertEqual(result['etl-run-request']['data'], run)
        self.assertEqual(result['etl-retry-request']['data'], retry)
        self.assertNotEqual(result['etl-backfill-request']['data']['request_id'], fill['request_id'])

    def test_logout_current_revocation_and_cross_origin_deny_mutations(self):
        self.login()
        values = {'etl-job': JOB, 'etl-business-date': DAY, 'etl-run-request': _request('run', [JOB, DAY])}
        self.call('etl-run-result.children', 'etl-run', values, headers={'Origin': 'https://outside.invalid'}, expected=403)
        self.identities.user_db['demo-admin']['role'] = 'user'
        self.call('etl-run-result.children', 'etl-run', values, expected=403)
        self.identities.user_db.pop('demo-admin')
        self.call('etl-status.children', 'etl-refresh', {'etl-job': JOB}, expected=401)
        self.assertEqual(self.service.status(self.identities.get_user('demo-user-a'), JOB)['runs'], [])
