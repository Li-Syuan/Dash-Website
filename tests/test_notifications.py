"""Notification adapter contracts against the approved DMC 0.12.0 runtime."""

from dataclasses import FrozenInstanceError
from importlib import metadata
import json
from pathlib import Path
import unittest

from dash import dcc, html
import dash_mantine_components as dmc
from plotly.utils import PlotlyJSONEncoder

from reporting_workspace.notifications import (
    CATALOG, MAX_EVENTS, MAX_SEEN, MAX_VISIBLE, NotifyService,
    notification_store_id, render_events, wrap_notifications,
)


ADMIN_A = {'id': 'private-admin-a', 'role': 'admin', 'org': 'private-org-a'}
ADMIN_B = {'id': 'private-admin-b', 'role': 'admin', 'org': 'private-org-b'}


def make_event(code='report.loaded', identity=None, request_id=None):
    service = NotifyService(lambda: ADMIN_A if identity is None else identity)
    return getattr(service, CATALOG[code].kind)(code, request_id=request_id)


class NotifyServiceTests(unittest.TestCase):
    def test_catalog_has_only_the_approved_codes_and_is_immutable(self):
        self.assertEqual(set(CATALOG), {
            'report.loaded', 'report.export.ready', 'report.failed',
            'report.export.failed', 'simulation.done', 'simulation.skipped',
            'simulation.busy', 'simulation.failed',
            'maintenance.created', 'maintenance.updated', 'maintenance.archived',
            'maintenance.restored', 'maintenance.invalid', 'maintenance.conflict',
            'maintenance.denied', 'maintenance.failed', 'maintenance.unavailable',
        })
        with self.assertRaises(TypeError):
            CATALOG['custom'] = CATALOG['report.loaded']
        with self.assertRaises(FrozenInstanceError):
            CATALOG['report.loaded'].message = 'untrusted text'

    def test_maintenance_codes_have_fixed_severity_and_no_payload_templates(self):
        expected = {
            'created': 'success', 'updated': 'success', 'archived': 'success',
            'restored': 'success', 'invalid': 'warning', 'conflict': 'warning',
            'denied': 'error', 'failed': 'error', 'unavailable': 'error',
        }
        for suffix, kind in expected.items():
            with self.subTest(code=suffix):
                spec = CATALOG['maintenance.' + suffix]
                self.assertEqual(spec.kind, kind)
                self.assertIsInstance(spec.title, str)
                self.assertIsInstance(spec.message, str)
                for template_marker in ('{', '}', '<', '>'):
                    self.assertNotIn(template_marker, spec.title + spec.message)
        self.assertIn('Reload the selected record before editing',
                      CATALOG['maintenance.conflict'].message)

    def test_all_catalog_entries_emit_safe_typed_events(self):
        for code, spec in CATALOG.items():
            with self.subTest(code=code):
                event = make_event(code)
                self.assertEqual(event['code'], code)
                self.assertEqual(event['kind'], spec.kind)
                self.assertEqual(set(event), {'event_id', 'code', 'kind', 'audience', 'request_id'})
                self.assertRegex(event['event_id'], r'^[0-9a-f]{32}$')
                self.assertRegex(event['audience'], r'^[0-9a-f]{64}$')
                self.assertIsNone(event['request_id'])

    def test_events_are_unique_and_service_has_no_queue(self):
        service = NotifyService(lambda: ADMIN_A)
        first, second = service.success('report.loaded'), service.success('report.loaded')
        self.assertNotEqual(first['event_id'], second['event_id'])
        self.assertEqual(first['audience'], second['audience'])
        self.assertEqual(set(vars(service)), {'_identity_getter'})

    def test_raw_identity_and_provider_extras_never_enter_events(self):
        identity = dict(ADMIN_A, password='private-password', email='private@example.invalid')
        serialized = json.dumps(make_event(identity=identity))
        for value in identity.values():
            self.assertNotIn(value, serialized)

    def test_identity_is_reloaded_for_every_action(self):
        current = [ADMIN_A]
        service = NotifyService(lambda: current[0])
        first = service.success('report.loaded')
        current[0] = ADMIN_B
        second = service.success('report.loaded')
        current[0] = None
        self.assertIsNone(service.success('report.loaded'))
        self.assertNotEqual(first['audience'], second['audience'])

    def test_missing_and_malformed_identities_emit_nothing(self):
        for identity in (None, {}, True, 'admin', dict(ADMIN_A, id=''),
                         dict(ADMIN_A, org=None), dict(ADMIN_A, role=''),
                         dict(ADMIN_A, id='bad\nidentity')):
            with self.subTest(identity=identity):
                self.assertIsNone(NotifyService(lambda: identity).success('report.loaded'))

    def test_getter_must_be_callable_and_provider_errors_do_not_become_text(self):
        with self.assertRaises(ValueError):
            NotifyService(None)

        def unavailable():
            raise RuntimeError('private backend detail')

        with self.assertRaises(RuntimeError):
            NotifyService(unavailable).error('report.failed')

    def test_unknown_or_wrong_severity_codes_are_rejected(self):
        service = NotifyService(lambda: ADMIN_A)
        for code in (None, [], {}, '', 'report.unknown', '<script>alert(1)</script>', 'report.failed'):
            with self.subTest(code=code), self.assertRaises(ValueError):
                service.success(code)
        with self.assertRaises(ValueError):
            service.info('simulation.busy')

    def test_request_ids_are_bounded_hex_and_normalized(self):
        service = NotifyService(lambda: ADMIN_A)
        self.assertEqual(service.error('report.failed', 'A0123BCD')['request_id'], 'a0123bcd')
        self.assertEqual(service.error('report.failed', 'a' * 64)['request_id'], 'a' * 64)
        for value in ('', 'a' * 7, 'a' * 65, 'request-123', '<img onerror=alert(1)>',
                      'abcd1234\n', 123, [], {}, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                service.error('report.failed', value)

    def test_store_ids_match_the_per_action_pattern_and_are_fresh(self):
        first = notification_store_id('reports.refresh')
        second = notification_store_id('reports.refresh')
        self.assertEqual(first, {'type': 'workspace-notify', 'action': '7265706f7274732e72656672657368'})
        self.assertIsNot(first, second)
        self.assertNotEqual(first, notification_store_id('reports.export'))
        self.assertEqual(dcc.Store(id=first, storage_type='memory').to_plotly_json()['props']['id'], first)
        for value in (None, {}, [], '', 'a' * 129, 'callback with spaces', 'x/y', 'x\n'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                notification_store_id(value)

    def test_store_encoding_is_dot_free_reversible_and_collision_free(self):
        actions = ['reports.refresh', 'reports-refresh', 'reports_refresh',
                   'reports...refresh', 'reports..refresh', 'reportsrefresh',
                   '7265706f7274732e72656672657368', 'a', 'A', 'a' * 128]
        encoded = [notification_store_id(action)['action'] for action in actions]
        self.assertEqual(len(set(encoded)), len(actions))
        for action, value in zip(actions, encoded):
            self.assertRegex(value, r'^[0-9a-f]+$')
            self.assertNotIn('.', value)
            self.assertEqual(bytes.fromhex(value).decode('ascii'), action)


class RenderEventsTests(unittest.TestCase):
    def test_all_catalog_messages_render_as_fixed_plain_strings(self):
        for code, spec in CATALOG.items():
            with self.subTest(code=code):
                event = make_event(code)
                rendered, seen = render_events([event], [], ADMIN_A)
                self.assertEqual(seen, [event['event_id']])
                self.assertEqual(len(rendered), 1)
                self.assertIsInstance(rendered[0], dmc.Notification)
                props = rendered[0].to_plotly_json()['props']
                self.assertEqual(props['title'], spec.title)
                self.assertEqual(props['message'], spec.message)
                self.assertIsInstance(props['message'], str)
                self.assertEqual(props['action'], 'show')
                self.assertEqual(props['className'], 'workspace-notification')
                self.assertFalse(props['disallowClose'])
                self.assertGreater(props['autoClose'], 0)

    def test_error_request_id_is_the_only_dynamic_message_content(self):
        request_id = '0abc1234def56789abcd1234'
        rendered, _ = render_events([make_event('report.failed', request_id=request_id)], [], ADMIN_A)
        self.assertEqual(rendered[0].message,
                         CATALOG['report.failed'].message + ' Request ID: ' + request_id + '.')
        rendered, _ = render_events([make_event(request_id=request_id)], [], ADMIN_A)
        self.assertEqual(rendered[0].message, CATALOG['report.loaded'].message)

    def test_raw_messages_html_exceptions_and_unknown_extras_are_rejected(self):
        for key, payload in (('message', '<img src=x onerror=alert(1)>'),
                             ('title', '<script>alert(1)</script>'),
                             ('children', {'dangerouslySetInnerHTML': {'__html': '<script/>'}}),
                             ('exception', 'secret database connection string'),
                             ('styles', {'root': {'display': 'none'}}),
                             ('created', 0)):
            with self.subTest(key=key):
                event = dict(make_event(), **{key: payload})
                self.assertEqual(render_events([event], [], ADMIN_A), ([], []))

    def test_malformed_event_fields_are_rejected_without_rendering(self):
        for field, value in (('event_id', 'not-hex'), ('event_id', 'a' * 33),
                             ('event_id', None), ('event_id', []),
                             ('code', []), ('code', {}), ('code', 'unknown'),
                             ('code', '<script/>'), ('kind', 'error'), ('kind', []),
                             ('audience', 'invalid'), ('audience', []),
                             ('request_id', '<script/>'), ('request_id', 'a' * 65),
                             ('request_id', 123), ('request_id', 'abcd1234\n')):
            with self.subTest(field=field, value=value):
                event = dict(make_event(), **{field: value})
                self.assertEqual(render_events([event], [], ADMIN_A), ([], []))
        for event in (None, '', 3, [], {}, True):
            with self.subTest(event=event):
                self.assertEqual(render_events([event], [], ADMIN_A), ([], []))

    def test_required_fields_cannot_be_missing_but_request_id_is_optional(self):
        for key in ('event_id', 'code', 'kind', 'audience'):
            event = make_event()
            del event[key]
            self.assertEqual(render_events([event], [], ADMIN_A), ([], []))
        event = make_event()
        del event['request_id']
        self.assertEqual(len(render_events([event], [], ADMIN_A)[0]), 1)

    def test_anonymous_identity_denies_and_clears_seen_ids(self):
        event = make_event()
        for identity in (None, {}, True, dict(ADMIN_A, role='')):
            with self.subTest(identity=identity):
                self.assertEqual(render_events([event], [event['event_id']], identity), ([], []))

    def test_other_users_organizations_and_changed_roles_cannot_replay_events(self):
        event = make_event()
        for identity in (ADMIN_B, dict(ADMIN_A, org='another-org'),
                         dict(ADMIN_A, role='user'), dict(ADMIN_A, id='someone-else')):
            with self.subTest(identity=identity):
                self.assertEqual(render_events([event], [], identity), ([], []))
        own = make_event(identity=ADMIN_B)
        rendered, seen = render_events([event, own], [], ADMIN_B)
        self.assertEqual(len(rendered), 1)
        self.assertEqual(seen, [own['event_id']])

    def test_duplicate_event_ids_and_replay_are_ignored(self):
        event = make_event()
        rendered, seen = render_events([event, dict(event), event], [], ADMIN_A)
        self.assertEqual(len(rendered), 1)
        self.assertEqual(seen, [event['event_id']])
        self.assertEqual(render_events([event], seen, ADMIN_A), ([], seen))

    def test_identical_codes_in_one_batch_coalesce_but_are_all_acknowledged(self):
        events = [make_event() for _ in range(20)]
        rendered, seen = render_events(events, [], ADMIN_A)
        self.assertEqual(len(rendered), 1)
        self.assertEqual(seen, [event['event_id'] for event in events])
        self.assertEqual(render_events(events, seen, ADMIN_A), ([], seen))

    def test_stable_toast_ids_prevent_stacking_repeated_clicks(self):
        first, second = make_event(), make_event()
        first_toast = render_events([first], [], ADMIN_A)[0][0]
        second_toast = render_events([second], [], ADMIN_A)[0][0]
        self.assertEqual(first_toast.id, second_toast.id)
        self.assertNotIn(first['event_id'], first_toast.id)
        other_toast = render_events([make_event(identity=ADMIN_B)], [], ADMIN_B)[0][0]
        self.assertNotEqual(first_toast.id, other_toast.id)

    def test_batch_visible_cap_drops_backlog_and_keeps_bounded_seen_ids(self):
        events = [make_event(code) for code in CATALOG]
        old_seen = ['{:032x}'.format(i) for i in range(100)]
        rendered, seen = render_events(events, old_seen, ADMIN_A)
        self.assertEqual(len(rendered), MAX_VISIBLE)
        self.assertEqual(len(seen), MAX_SEEN)
        self.assertEqual(seen[-len(events):], [event['event_id'] for event in events])
        self.assertEqual(render_events(events, seen, ADMIN_A), ([], seen))

    def test_oversized_batches_are_processed_with_a_fixed_bound(self):
        events = [make_event() for _ in range(MAX_EVENTS + 10)]
        rendered, seen = render_events(events, [], ADMIN_A)
        self.assertEqual(len(rendered), 1)
        self.assertEqual(len(seen), MAX_SEEN)
        self.assertEqual(seen[-1], events[MAX_EVENTS - 1]['event_id'])

    def test_untrusted_seen_values_are_filtered_without_echoing_them(self):
        valid = 'a' * 32
        seen = [None, [], {}, '<script/>', valid, valid, 'x' * 32, 'b' * 33]
        self.assertEqual(render_events([], seen, ADMIN_A), ([], [valid]))
        for malformed in (None, {}, 'not a list', 3):
            with self.subTest(malformed=malformed):
                self.assertEqual(render_events([], malformed, ADMIN_A), ([], []))

    def test_non_sequence_batches_do_not_iterate_or_render(self):
        for events in (None, make_event(), 'anything', 2, iter([make_event()])):
            with self.subTest(events=events):
                self.assertEqual(render_events(events, [], ADMIN_A), ([], []))

    def test_input_events_seen_and_identity_are_not_mutated(self):
        event, seen, identity = make_event(), [], dict(ADMIN_A)
        before = json.dumps([event, seen, identity], sort_keys=True)
        render_events([event], seen, identity)
        self.assertEqual(json.dumps([event, seen, identity], sort_keys=True), before)


class Dmc012ProviderTests(unittest.TestCase):
    def test_exact_approved_runtime_and_serializable_supported_props(self):
        self.assertEqual(metadata.version('dash-mantine-components'), '0.12.0')
        wrapped = wrap_notifications(html.Div('application'))
        self.assertIsInstance(wrapped, dmc.MantineProvider)
        self.assertIsInstance(wrapped.children, dmc.NotificationsProvider)
        notification = render_events([make_event()], [], ADMIN_A)[0][0]
        for component in (wrapped, wrapped.children, notification):
            self.assertLessEqual(set(component.to_plotly_json()['props']), set(component._prop_names))
            json.dumps(component, cls=PlotlyJSONEncoder)
        self.assertNotIn('closeButtonProps', notification._prop_names)

    def test_wrapping_keeps_bootstrap_styles_and_responsive_limit(self):
        child = html.Div(id='unchanged-child')
        wrapped = wrap_notifications(child)
        self.assertIs(wrapped.children.children, child)
        self.assertFalse(wrapped.withNormalizeCSS)
        self.assertFalse(wrapped.withGlobalStyles)
        self.assertFalse(wrapped.withCSSVariables)
        self.assertTrue(wrapped.theme['respectReducedMotion'])
        provider = wrapped.children
        self.assertEqual(provider.limit, MAX_VISIBLE)
        self.assertEqual(provider.containerWidth, 420)
        self.assertEqual(provider.position, 'top-right')

    def test_close_button_has_an_accessible_name_via_supported_theme_defaults(self):
        wrapped = wrap_notifications(None)
        close = wrapped.theme['components']['Notification']['defaultProps']['closeButtonProps']
        self.assertEqual(close['aria-label'], 'Dismiss notification')
        notification = render_events([make_event()], [], ADMIN_A)[0][0]
        self.assertFalse(notification.disallowClose)
        self.assertIn('&:focus-visible', notification.styles['closeButton'])
        self.assertEqual(notification.styles['root']['maxWidth'], '100%')
        self.assertEqual(notification.styles['description']['overflowWrap'], 'anywhere')

    def test_pinned_bundle_supplies_alert_role_theme_defaults_and_viewport_width(self):
        # Check the installed implementation rather than assuming a newer DMC
        # closeButtonProps/Notifications API exists. Browser QA additionally
        # verifies focus/Enter dismissal and the resulting accessible DOM.
        bundle = (Path(dmc.__file__).parent / 'dash_mantine_components.js').read_text()
        self.assertIn('role:"alert"', bundle)
        self.assertIn('closeButtonProps', bundle)
        self.assertIn('o.components[e])?void 0:n.defaultProps', bundle)
        self.assertIn('width:`calc(100% - ${2*e.spacing.md}px)`', bundle)


if __name__ == '__main__':
    unittest.main()
