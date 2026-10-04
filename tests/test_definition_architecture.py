"""Request-scoped definition authority over real SQLite and Flask transports.

The old fixture helpers are reused explicitly, without inheriting their test
classes (which would silently run the pre-existing test suites twice).
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from flask import g, jsonify
from flask.globals import request_ctx

import test_crud_service as crud_fixtures
import test_maintenance as maintenance_fixtures
from reporting_workspace.application import create_app
from reporting_workspace.lifecycle import dispose_app
from reporting_workspace.config import Settings
from reporting_workspace.crud import (
    AccessDenied, Conflict, NotFound, StateUnavailable,
    ValidationError,
)
from reporting_workspace.definition_policy import DEFINITION_ACCESS, DefinitionPrincipal
from reporting_workspace.notifications import NotifyService
from reporting_workspace.ui_pages import maintenance


def actor_id(user):
    return hashlib.sha256(user['id'].encode('utf-8')).hexdigest()


class MutableIdentities(maintenance_fixtures.Identities):
    """App-owned synthetic claims that can be revoked between HTTP requests."""

    def __init__(self):
        self.users = {key: dict(value) for key, value in maintenance_fixtures.USERS.items()}
        self.users['user-b'] = dict(id='user-b', role='user', org='B')

    def get_user(self, user_id):
        return self.users.get(user_id)

    def authenticate(self, username, password):
        return self.get_user(username) if password == 'fixture-only' else None


class DefinitionPrincipalTests(unittest.TestCase):
    def test_principal_copies_only_authoritative_claims_and_is_frozen(self):
        source = dict(crud_fixtures.USER, credential='synthetic-private-marker', extra=[])
        principal = DefinitionPrincipal.from_user(source)
        expected = (source['id'], source['org'], source['role'], actor_id(source))
        source.update(id='changed', org='B', role='admin')
        self.assertEqual((principal.id, principal.org, principal.role, principal.actor_id), expected)
        self.assertNotIn('synthetic-private-marker', repr(principal))
        self.assertFalse(hasattr(principal, 'credential'))
        for name, value in (('id', 'changed'), ('org', 'B'), ('role', 'admin'), ('actor_id', 'forged')):
            with self.subTest(field=name), self.assertRaises((FrozenInstanceError, AttributeError)):
                setattr(principal, name, value)

    def test_exact_identity_and_tenant_strings_are_not_trimmed_or_merged(self):
        source = dict(id=' owner@example.test ', org=' A ', role='user')
        principal = DefinitionPrincipal.from_user(source)
        self.assertEqual((principal.id, principal.org), (source['id'], source['org']))
        self.assertEqual(principal.actor_id, actor_id(source))
        self.assertNotEqual(principal.actor_id, actor_id(crud_fixtures.USER))
        self.assertFalse(principal.can_change(dict(org='A', owner_id=principal.actor_id)))
        self.assertTrue(principal.can_change(dict(org=' A ', owner_id=principal.actor_id)))

    def test_policy_and_principal_reject_the_same_invalid_claim_matrix(self):
        invalid = [None, [], '', {}, dict(crud_fixtures.USER, role='guest')]
        for field, limit in (('id', 255), ('org', 128), ('role', 64)):
            for value in (None, True, 3, [], '', ' ', 'x' * (limit + 1), 'bad\x00claim', '\ud800'):
                invalid.append(dict(crud_fixtures.USER, **{field: value}))
        for source in invalid:
            with self.subTest(source=repr(source)):
                self.assertFalse(DEFINITION_ACCESS.allows(source))
                with self.assertRaises(AccessDenied):
                    DefinitionPrincipal.from_user(source)
        for source in (crud_fixtures.USER, crud_fixtures.PEER, crud_fixtures.ADMIN,
                       crud_fixtures.OTHER_USER, crud_fixtures.OTHER_ADMIN):
            self.assertTrue(DEFINITION_ACCESS.allows(source))
            self.assertEqual(DefinitionPrincipal.from_user(source).actor_id, actor_id(source))

    def test_can_change_requires_tenant_even_for_matching_owner_or_admin(self):
        sources = (crud_fixtures.USER, crud_fixtures.PEER, crud_fixtures.ADMIN,
                   crud_fixtures.OTHER_USER, crud_fixtures.OTHER_ADMIN)
        for source in sources:
            principal = DefinitionPrincipal.from_user(source)
            for org in ('A', 'B'):
                for owner in (actor_id(crud_fixtures.USER), actor_id(crud_fixtures.PEER)):
                    with self.subTest(source=source, org=org, owner=owner):
                        expected = source['org'] == org and (
                            source['role'] == 'admin' or actor_id(source) == owner)
                        self.assertEqual(principal.can_change(dict(org=org, owner_id=owner)), expected)

    def test_page_and_callbacks_use_the_shared_definition_policy(self):
        self.assertIs(maintenance.POLICY, DEFINITION_ACCESS)
        self.assertIs(maintenance.SPEC.policy, DEFINITION_ACCESS)
        self.assertEqual(DEFINITION_ACCESS.roles, ('admin', 'user'))
        self.assertTrue(DEFINITION_ACCESS.authenticated)
        self.assertIsNone(DEFINITION_ACCESS.org)


class BoundDefinitionTests(unittest.TestCase):
    setUp = crud_fixtures.CrudServiceTests.setUp

    def test_mutating_source_identity_after_bind_cannot_change_any_authority(self):
        source = dict(crud_fixtures.USER)
        bound = self.service.bind(source, request_id='original-request')
        peer = self.service.create(crud_fixtures.PEER, {'name': 'Peer owned'})
        foreign = self.service.create(crud_fixtures.OTHER_USER, {'name': 'Other tenant'})
        source.update(id=crud_fixtures.PEER['id'], org='B', role='admin')
        row = bound.create({'name': 'Original owner'})
        self.assertEqual((row['org'], row['owner_id']), ('A', actor_id(crud_fixtures.USER)))
        self.assertEqual(bound.principal, DefinitionPrincipal.from_user(crud_fixtures.USER))
        self.assertFalse(bound.can_change(peer))
        self.assertFalse(bound.can_change(foreign))
        with self.assertRaises(AccessDenied):
            bound.update(peer['id'], 1, {'name': 'Escalated'})
        with self.assertRaises(NotFound):
            bound.get(foreign['id'])
        self.assertEqual({item['id'] for item in bound.list()['items']}, {row['id'], peer['id']})
        event = self.store.list_audit()[-1]
        self.assertEqual((event.actor_id, event.request_id), (actor_id(crud_fixtures.USER), 'original-request'))

    def test_bound_crud_preserves_lifecycle_idempotency_versions_and_audit_context(self):
        bound = self.service.bind(crud_fixtures.USER, request_id='bound-lifecycle')
        row = bound.create({'name': '  Initial  '}, request_key=crud_fixtures.CREATE_KEY)
        self.assertEqual(bound.create({'name': 'Initial'}, request_key=crud_fixtures.CREATE_KEY), row)
        self.assertEqual(bound.get(row['id']), row)
        changed = bound.update(row['id'], 1, {'name': 'Changed'})
        self.assertEqual(changed['version'], 2)
        with self.assertRaises(Conflict):
            bound.update(row['id'], 1, {'name': 'Stale'})
        archived = bound.soft_delete(row['id'], 2)
        self.assertEqual(archived['version'], 3)
        self.assertEqual(bound.list()['total'], 0)
        self.assertEqual(bound.list(include_deleted=True)['items'], [archived])
        restored = bound.restore(row['id'], 3)
        self.assertEqual(restored['version'], 4)
        self.assertEqual(bound.list(q='Changed', limit=1, offset=0)['items'], [restored])
        with self.assertRaises(Conflict):
            bound.create({'name': 'Initial'}, request_key=crud_fixtures.CREATE_KEY)
        events = self.store.list_audit()
        self.assertEqual([event.event for event in events],
                         ['crud.created', 'crud.updated', 'crud.deleted', 'crud.restored'])
        self.assertEqual({event.request_id for event in events}, {'bound-lifecycle'})
        self.assertEqual({event.actor_id for event in events}, {actor_id(crud_fixtures.USER)})

    def test_bound_facade_forwards_to_compatible_public_service_methods(self):
        bound = self.service.bind(crud_fixtures.USER, request_id='forwarded-request')
        operations = (
            ('list', (), dict(q='search', limit=2, offset=3, include_deleted=True)),
            ('get', ('a' * 32,), {}),
            ('create', ({'name': 'Forwarded'},), dict(request_key=crud_fixtures.CREATE_KEY)),
            ('update', ('a' * 32, 7, {'enabled': False}), {}),
            ('soft_delete', ('a' * 32, 7), {}),
            ('restore', ('a' * 32, 7), {}),
        )
        for name, args, kwargs in operations:
            result = object()
            with self.subTest(operation=name), patch.object(self.service, name, return_value=result) as method:
                self.assertIs(getattr(bound, name)(*args, **kwargs), result)
                method.assert_called_once()
                actual_args, actual_kwargs = method.call_args
                self.assertEqual(actual_args[1:], args)
                forwarded = actual_args[0]
                if isinstance(forwarded, DefinitionPrincipal):
                    self.assertEqual(forwarded, bound.principal)
                else:
                    self.assertEqual(dict(forwarded), crud_fixtures.USER)
                expected_kwargs = dict(kwargs)
                if name in ('create', 'update', 'soft_delete', 'restore'):
                    expected_kwargs['request_id'] = 'forwarded-request'
                self.assertEqual(actual_kwargs, expected_kwargs)

    def test_bound_methods_reject_user_and_request_id_overrides(self):
        bound = self.service.bind(crud_fixtures.USER, request_id='fixed-request')
        row = bound.create({'name': 'Original'})
        operations = (
            ('list', ()), ('get', (row['id'],)), ('create', ({'name': 'Injected'},)),
            ('update', (row['id'], 1, {'name': 'Injected'})),
            ('soft_delete', (row['id'], 1)), ('restore', (row['id'], 1)),
            ('can_change', (row,)), ('identity', ()),
        )
        for name, args in operations:
            for override in ({'user': crud_fixtures.OTHER_ADMIN}, {'request_id': 'injected-request'}):
                with self.subTest(operation=name, override=override), self.assertRaises(TypeError):
                    getattr(bound, name)(*args, **override)
        self.assertEqual(bound.get(row['id']), row)
        self.assertEqual(len(self.store.list_audit()), 1)

    def test_bound_identity_returns_fresh_copies_without_changing_authority(self):
        bound = self.service.bind(crud_fixtures.USER)
        first, second = bound.identity(), bound.identity()
        self.assertIsNot(first, second)
        self.assertEqual(first, crud_fixtures.USER)
        first.update(id='different-owner', org='B', role='admin')
        self.assertEqual(bound.identity(), crud_fixtures.USER)
        row = bound.create({'name': 'Original identity'})
        self.assertEqual((row['org'], row['owner_id']), ('A', actor_id(crud_fixtures.USER)))

    def test_active_guard_is_checked_for_every_operation_before_delegation(self):
        guard = Mock(return_value=True)
        bound = self.service.bind(crud_fixtures.USER, request_id='guarded', is_active=guard)
        row = bound.create({'name': 'Allowed while active'})
        operations = (
            ('list', ()), ('get', (row['id'],)), ('create', ({'name': 'Inactive'},)),
            ('update', (row['id'], 1, {'name': 'Inactive'})),
            ('soft_delete', (row['id'], 1)), ('restore', (row['id'], 1)),
            ('can_change', (row,)), ('identity', ()),
        )
        guard.return_value = False
        for name, args in operations:
            guard.reset_mock()
            with self.subTest(operation=name), self.assertRaises(AccessDenied):
                getattr(bound, name)(*args)
            guard.assert_called_once_with()
        self.assertEqual(self.service.get(crud_fixtures.USER, row['id']), row)
        self.assertEqual(len(self.store.list_audit()), 1)

    def test_same_identity_across_tenants_never_reuses_keys_or_crosses_record_scope(self):
        owner_a = self.service.bind(crud_fixtures.USER, request_id='tenant-a')
        owner_b = self.service.bind(crud_fixtures.OTHER_USER, request_id='tenant-b')
        row_a = owner_a.create({'name': 'Tenant A'}, request_key=crud_fixtures.CREATE_KEY)
        row_b = owner_b.create({'name': 'Tenant B'}, request_key=crud_fixtures.CREATE_KEY)
        self.assertEqual(row_a['owner_id'], row_b['owner_id'])
        self.assertNotEqual(row_a['id'], row_b['id'])
        for source, own, foreign in (
                (crud_fixtures.USER, row_a, row_b),
                (crud_fixtures.ADMIN, row_a, row_b),
                (crud_fixtures.OTHER_USER, row_b, row_a),
                (crud_fixtures.OTHER_ADMIN, row_b, row_a)):
            bound = self.service.bind(source)
            self.assertEqual(bound.list()['items'], [own])
            self.assertFalse(bound.can_change(foreign))
            operations = (
                ('get', (foreign['id'],)),
                ('update', (foreign['id'], 1, {'name': 'Cross-tenant'})),
                ('soft_delete', (foreign['id'], 1)), ('restore', (foreign['id'], 1)),
            )
            for name, args in operations:
                with self.subTest(source=source, operation=name), self.assertRaises(NotFound):
                    getattr(bound, name)(*args)
        self.assertEqual(len(self.store.list_audit()), 2)

    def test_bound_peer_and_admin_write_decisions_match_principal(self):
        for role, may_change in (('user', False), ('admin', True)):
            row = self.service.create(crud_fixtures.USER, {'name': 'Owner record'})
            source = dict(crud_fixtures.PEER, role=role)
            bound = self.service.bind(source)
            self.assertEqual(bound.can_change(row), may_change)
            self.assertEqual(bound.get(row['id']), row)
            if may_change:
                self.assertEqual(bound.update(row['id'], 1, {'name': 'Admin edit'})['version'], 2)
            else:
                with self.assertRaises(AccessDenied):
                    bound.update(row['id'], 1, {'name': 'Peer edit'})

    def test_bound_failure_rolls_back_both_mutation_and_idempotency_reservation(self):
        bound = self.service.bind(crud_fixtures.USER, request_id='atomic-request')
        with patch.object(self.store, '_audit', side_effect=sqlite3.OperationalError('synthetic failure')):
            with self.assertRaises(StateUnavailable):
                bound.create({'name': 'Retry'}, request_key=crud_fixtures.CREATE_KEY)
        self.assertEqual(bound.list()['total'], 0)
        self.assertEqual(self.store.list_audit(), [])
        row = bound.create({'name': 'Retry'}, request_key=crud_fixtures.CREATE_KEY)
        self.assertEqual(bound.create({'name': 'Retry'}, request_key=crud_fixtures.CREATE_KEY), row)
        self.assertEqual(len(self.store.list_audit()), 1)

    def test_bound_payload_rejects_authority_fields_without_audit_or_write(self):
        bound = self.service.bind(crud_fixtures.USER)
        for field, value in (('org', 'B'), ('owner_id', actor_id(crud_fixtures.ADMIN)),
                             ('role', 'admin'), ('request_id', 'forged'), ('version', 999)):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                bound.create(dict(name='Forged', **{field: value}))
        self.assertEqual(bound.list()['total'], 0)
        self.assertEqual(self.store.list_audit(), [])

    def test_shared_service_has_independent_concurrent_principal_and_audit_scopes(self):
        sources = (crud_fixtures.USER, crud_fixtures.PEER, crud_fixtures.OTHER_USER)
        barrier = threading.Barrier(len(sources))

        def operate(index, source):
            bound = self.service.bind(dict(source), request_id='thread-{}'.format(index))
            barrier.wait(timeout=10)
            row = bound.create({'name': 'Thread {}'.format(index)}, request_key=crud_fixtures.CREATE_KEY)
            row = bound.update(row['id'], 1, {'description': 'Synthetic edit'})
            row = bound.soft_delete(row['id'], 2)
            row = bound.restore(row['id'], 3)
            return index, row

        with ThreadPoolExecutor(max_workers=len(sources)) as pool:
            futures = [pool.submit(operate, index, source) for index, source in enumerate(sources)]
            results = [future.result(timeout=30) for future in futures]
        events = self.store.list_audit()
        self.assertEqual(len(events), 12)
        for index, row in results:
            source = sources[index]
            self.assertEqual((row['org'], row['owner_id'], row['version']), (source['org'], actor_id(source), 4))
            resource_events = [event for event in events if event.resource_id == row['id']]
            self.assertEqual(len(resource_events), 4)
            self.assertEqual({event.actor_id for event in resource_events}, {actor_id(source)})
            self.assertEqual({event.request_id for event in resource_events}, {'thread-{}'.format(index)})
        self.assertEqual(self.service.list(crud_fixtures.USER)['total'], 2)
        self.assertEqual(self.service.list(crud_fixtures.OTHER_USER)['total'], 1)


class DefinitionRequestTransportTests(unittest.TestCase):
    authenticated = staticmethod(maintenance_fixtures.MaintenanceTests.authenticated)
    defaults = staticmethod(maintenance_fixtures.MaintenanceTests.defaults)
    call = maintenance_fixtures.MaintenanceTests.call
    assert_ok = maintenance_fixtures.MaintenanceTests.assert_ok
    assert_notice = maintenance_fixtures.MaintenanceTests.assert_notice

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.identities = MutableIdentities()
        self.server = self.make_app('first')
        self.runtime = self.server.extensions['workspace']
        self.service = self.runtime.definitions
        self.client = self.authenticated(self.server, 'user-a')
        self.values = self.defaults()

    def make_app(self, name):
        settings = Settings(secret_key='definition-scope-test-secret-429871',
                            state_path=str(Path(self.directory.name) / (name + '.sqlite3')))
        server = create_app(settings, self.identities, maintenance_fixtures.Reports())
        self.addCleanup(dispose_app, server)
        server.config['TESTING'] = True
        return server

    def test_runtime_requires_an_active_request_of_its_own_app(self):
        with self.assertRaises(AccessDenied):
            self.runtime.definition_request()
        with self.server.app_context():
            with self.assertRaises(AccessDenied):
                self.runtime.definition_request()
        other = self.make_app('other')

        @other.route('/_fixture/wrong-runtime')
        def wrong_runtime():
            with self.assertRaises(AccessDenied):
                self.runtime.definition_request()
            return jsonify(denied=True)

        response = self.authenticated(other, 'user-a').get('/_fixture/wrong-runtime')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['denied'])

    def test_runtime_snapshots_identity_once_and_audits_server_request_id(self):
        captured = []

        @self.server.route('/_fixture/snapshot')
        def snapshot():
            original = dict(self.identities.users['user-a'])
            mutable = dict(original)
            with patch.object(self.runtime, 'identity', return_value=mutable) as identity:
                bound = self.runtime.definition_request()
                mutable.update(id='forged', role='admin', org='B')
                row = bound.create({'name': 'Server bound'})
                self.assertEqual(bound.get(row['id']), row)
                self.assertEqual(bound.list()['items'], [row])
                self.assertTrue(bound.can_change(row))
                identity.assert_called_once_with()
            captured.append(bound)
            return jsonify(row=row)

        response = self.client.get('/_fixture/snapshot', headers={'X-Request-ID': 'browser-forged'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        row = response.get_json()['row']
        self.assertEqual((row['org'], row['owner_id']), ('A', actor_id(self.identities.users['user-a'])))
        self.assertNotEqual(response.headers['X-Request-ID'], 'browser-forged')
        event = self.runtime.state.list_audit()[0]
        self.assertEqual(event.request_id, response.headers['X-Request-ID'])
        self.assertEqual(event.actor_id, actor_id(self.identities.users['user-a']))
        with self.assertRaises(AccessDenied):
            captured[0].list()

    def test_retained_scope_denies_after_request_same_request_id_and_other_app(self):
        captured = {}
        other = self.make_app('other')

        @self.server.route('/_fixture/capture')
        def capture():
            bound = self.runtime.definition_request()
            row = bound.create({'name': 'Lifetime boundary'})
            captured.update(bound=bound, row=row, request_id=g.request_id)
            return jsonify(ok=True)

        def assert_retained_denied():
            # Correlation IDs are not capabilities, including in the same app.
            g.request_id = captured['request_id']
            bound, row = captured['bound'], captured['row']
            for name, args in (
                    ('list', ()), ('get', (row['id'],)), ('create', ({'name': 'Leaked'},)),
                    ('update', (row['id'], 1, {'name': 'Leaked'})),
                    ('soft_delete', (row['id'], 1)), ('restore', (row['id'], 1)),
                    ('can_change', (row,)), ('identity', ())):
                with self.subTest(operation=name), self.assertRaises(AccessDenied):
                    getattr(bound, name)(*args)
            return jsonify(denied=True)

        self.server.add_url_rule('/_fixture/reuse', 'reuse', assert_retained_denied)
        other.add_url_rule('/_fixture/reuse', 'reuse', assert_retained_denied)
        self.assertEqual(self.client.get('/_fixture/capture').status_code, 200)
        with self.assertRaises(AccessDenied):
            captured['bound'].list()
        for client in (self.client, self.authenticated(other, 'user-a')):
            response = client.get('/_fixture/reuse')
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            self.assertTrue(response.get_json()['denied'])
        self.assertEqual(len(self.runtime.state.list_audit()), 1)

    def test_copied_or_reentered_flask_context_cannot_reanimate_completed_request(self):
        captured = {}

        @self.server.route('/_fixture/copy-context')
        def capture_context():
            bound = self.runtime.definition_request()
            original = request_ctx._get_current_object()
            captured.update(bound=bound, original=original, copied=original.copy())
            self.assertEqual(bound.list()['total'], 0)
            return jsonify(ok=True)

        # Preserve the application context as well as the actual Request object.
        # A guard based only on has_request_context/request/app identities would
        # allow this replay after teardown; the request lifetime must be closed.
        with self.server.app_context():
            self.assertEqual(self.client.get('/_fixture/copy-context').status_code, 200)
            for name in ('copied', 'original'):
                with self.subTest(context=name), captured[name]:
                    with self.assertRaises(AccessDenied):
                        captured['bound'].list()
                    with self.assertRaises(AccessDenied):
                        captured['bound'].create({'name': 'Reanimated'})
                    with self.assertRaises(AccessDenied):
                        self.runtime.definition_request()
        self.assertEqual(self.service.list(self.identities.users['user-a'])['total'], 0)
        self.assertEqual(self.runtime.state.list_audit(), [])

    def test_ambiguous_write_triggers_never_choose_an_action_by_order(self):
        source = self.identities.users['user-a']
        row = self.service.create(source, {'name': 'Unchanged'})
        self.values[('maintenance-name', 'value')] = 'Ambiguous replacement'
        self.values[('maintenance-record', 'data')] = dict(id=row['id'], version=1)
        actions = ('maintenance-save.n_clicks', 'maintenance-archive.n_clicks',
                   'maintenance-restore.n_clicks')
        for first in actions:
            for second in actions:
                if first == second:
                    continue
                with self.subTest(triggers=(first, second)):
                    response = self.call('maintenance.mutate', (first, second))
                    self.assertEqual(response.status_code, 204, response.get_data(as_text=True))
        self.assertEqual(self.call('maintenance.mutate', actions).status_code, 204)
        # The new-record flow must not turn a combined save/archive into create.
        self.values[('maintenance-record', 'data')] = None
        self.assertEqual(self.call('maintenance.mutate', actions[:2]).status_code, 204)
        self.assertEqual(self.service.get(source, row['id']), row)
        self.assertEqual(self.service.list(source)['total'], 1)
        self.assertEqual(len(self.runtime.state.list_audit()), 1)

    def test_forged_trigger_ids_or_properties_are_not_write_intent(self):
        self.values[('maintenance-name', 'value')] = 'Forged trigger'
        for changed in ('{"type":"forged","index":1}.n_clicks',
                        'maintenance-save.value', 'maintenance-archive.value',
                        'maintenance-restore.value', 'maintenance-name.value'):
            with self.subTest(changed=changed):
                response = self.call('maintenance.mutate', changed)
                self.assertEqual(response.status_code, 204, response.get_data(as_text=True))
        self.assertEqual(self.service.list(self.identities.users['user-a'])['total'], 0)
        self.assertEqual(self.runtime.state.list_audit(), [])

    def test_success_and_error_notifications_keep_the_bound_identity_audience(self):
        source = dict(self.identities.users['user-a'])
        expected_audience = NotifyService(lambda: source).success('maintenance.created')['audience']
        original_create = self.service.create
        for fails in (False, True):
            claims = dict(source)
            values = self.defaults()
            values[('maintenance-name', 'value')] = 'Stable callback scope'

            def change_later_claims(*args, **kwargs):
                claims.update(id='later-identity', role='admin', org='B')
                if fails:
                    raise StateUnavailable('synthetic unavailable')
                return original_create(*args, **kwargs)

            with self.subTest(fails=fails), patch.object(self.runtime, 'identity', return_value=claims), \
                    patch.object(self.service, 'create', side_effect=change_later_claims):
                response = self.call('maintenance.mutate', 'maintenance-save.n_clicks', values=values)
            body = self.assert_notice(response, 'maintenance.unavailable' if fails else 'maintenance.created',
                                      'error' if fails else 'success')
            notices = [item['data'] for key, item in body.items() if key.startswith('{')]
            self.assertEqual(len(notices), 1)
            self.assertEqual(notices[0]['audience'], expected_audience)
        rows = self.service.list(source)['items']
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['org'], rows[0]['owner_id']), ('A', actor_id(source)))
        self.assertEqual(len(self.runtime.state.list_audit()), 1)

    def test_threaded_actual_callbacks_share_service_without_identity_or_audit_leakage(self):
        usernames = ('user-a', 'other-a', 'user-b')
        clients = [self.authenticated(self.server, name) for name in usernames]
        # Finish Dash's lazy first-request setup before deliberately overlapping
        # application callbacks. Each worker still has its own real Flask client.
        self.assertEqual(self.client.get('/login').status_code, 200)
        barrier = threading.Barrier(len(clients))
        original_create = self.service.create

        def interleaved_create(*args, **kwargs):
            barrier.wait(timeout=10)
            return original_create(*args, **kwargs)

        def save(index, client):
            values = self.defaults()
            values[('maintenance-name', 'value')] = 'Callback {}'.format(index)
            # The same draft token must remain scoped to the actual actor/org.
            values[('maintenance-draft', 'data')] = crud_fixtures.CREATE_KEY
            response = self.call('maintenance.mutate', 'maintenance-save.n_clicks', client=client, values=values)
            return index, response

        with patch.object(self.service, 'create', side_effect=interleaved_create):
            with ThreadPoolExecutor(max_workers=len(clients)) as pool:
                futures = [pool.submit(save, index, client) for index, client in enumerate(clients)]
                results = [future.result(timeout=30) for future in futures]
        events = self.runtime.state.list_audit()
        self.assertEqual(len(events), len(clients))
        request_ids = set()
        for index, response in results:
            body = self.assert_notice(response, 'maintenance.created', 'success')
            identifier = body['maintenance-mutation']['data']['id']
            source = self.identities.users[usernames[index]]
            row = self.service.get(source, identifier)
            self.assertEqual((row['org'], row['owner_id']), (source['org'], actor_id(source)))
            event = next(event for event in events if event.resource_id == identifier)
            self.assertEqual((event.actor_id, event.request_id), (actor_id(source), response.headers['X-Request-ID']))
            request_ids.add(event.request_id)
        self.assertEqual(len(request_ids), len(clients))
        self.assertEqual(self.service.list(self.identities.users['user-a'])['total'], 2)
        self.assertEqual(self.service.list(self.identities.users['user-b'])['total'], 1)

    def test_admin_claim_revocation_between_callbacks_removes_peer_write_authority(self):
        row = self.service.create(self.identities.users['other-a'], {'name': 'Peer definition'})
        admin = self.authenticated(self.server, 'admin-a')
        self.values[('maintenance-record', 'data')] = dict(id=row['id'], version=1)
        body = self.assert_ok(self.call('maintenance.select', 'maintenance-reload.n_clicks', client=admin))
        self.assertFalse(body['maintenance-save']['disabled'])
        self.identities.users['admin-a']['role'] = 'user'
        self.values[('maintenance-name', 'value')] = 'Stale admin edit'
        self.assert_notice(self.call('maintenance.mutate', 'maintenance-save.n_clicks', client=admin),
                           'maintenance.denied', 'error')
        body = self.assert_ok(self.call('maintenance.select', 'maintenance-reload.n_clicks', client=admin))
        self.assertTrue(body['maintenance-save']['disabled'])
        self.assertEqual(self.service.get(self.identities.users['other-a'], row['id']), row)
        self.assertEqual(len(self.runtime.state.list_audit()), 1)

    def test_changed_tenant_between_callbacks_cannot_reuse_selected_reference(self):
        original = dict(self.identities.users['user-a'])
        row = self.service.create(original, {'name': 'Original tenant marker'})
        self.values[('maintenance-record', 'data')] = dict(id=row['id'], version=1)
        self.assert_ok(self.call('maintenance.select', 'maintenance-reload.n_clicks'))
        self.identities.users['user-a']['org'] = 'B'
        response = self.call('maintenance.select', 'maintenance-reload.n_clicks')
        self.assert_notice(response, 'maintenance.denied', 'error')
        self.assertNotIn(row['name'], response.get_data(as_text=True))
        self.values[('maintenance-name', 'value')] = 'Tenant changed'
        self.assert_notice(self.call('maintenance.mutate', 'maintenance-save.n_clicks'),
                           'maintenance.denied', 'error')
        body = self.assert_ok(self.call('maintenance.list', 'maintenance-refresh.n_clicks'))
        self.assertEqual(body['maintenance-table']['data'], [])
        with self.assertRaises(AccessDenied):
            self.service.get(original, row['id'])
        self.assertEqual(self.service.get(self.identities.users['other-a'], row['id']), row)
        self.assertEqual(len(self.runtime.state.list_audit()), 1)

    def test_removed_identity_or_role_is_denied_before_next_callback_service_call(self):
        self.assert_ok(self.call('maintenance.list', 'maintenance-refresh.n_clicks'))
        source = dict(self.identities.users['user-a'])
        for replacement, status in ((dict(source, role='guest'), 403), (None, 401)):
            self.identities.users['user-a'] = replacement
            with patch.object(self.service, 'list') as listing, patch.object(self.service, 'create') as creation:
                for callback, trigger in (
                        ('maintenance.list', 'maintenance-refresh.n_clicks'),
                        ('maintenance.mutate', 'maintenance-save.n_clicks')):
                    with self.subTest(status=status, callback=callback):
                        self.assertEqual(self.call(callback, trigger).status_code, status)
                listing.assert_not_called()
                creation.assert_not_called()

        with self.assertRaises(AccessDenied):
            self.service.list(source)
        self.assertEqual(self.service.list(self.identities.users['other-a'])['total'], 0)

    def test_midrequest_revocation_rolls_back_and_returns_no_sensitive_update(self):
        original_audit = self.runtime.state._audit

        def revoke_after_audit(*args, **kwargs):
            original_audit(*args, **kwargs)
            self.identities.users['user-a'] = None

        self.values[('maintenance-name', 'value')] = 'Must be rolled back'
        with patch.object(self.runtime.state, '_audit', side_effect=revoke_after_audit):
            response = self.call('maintenance.mutate', 'maintenance-save.n_clicks')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()['response']
        self.assertNotIn('maintenance-mutation', body)
        self.assertNotIn('Must be rolled back', response.get_data(as_text=True))
        self.assertEqual(self.service.list(self.identities.users['other-a'])['total'], 0)
        self.assertEqual(self.runtime.state.list_audit(), [])

    def test_real_ui_access_labels_and_service_share_owner_admin_policy(self):
        owner = self.identities.users['user-a']
        peer = self.identities.users['other-a']
        rows = [self.service.create(source, {'name': 'Owned {}'.format(index)})
                for index, source in enumerate((owner, peer))]
        for username in ('user-a', 'other-a', 'admin-a', 'user-b'):
            source = self.identities.users[username]
            client = self.authenticated(self.server, username)
            values = self.defaults()
            response = self.call('maintenance.list', 'maintenance-refresh.n_clicks', client=client, values=values)
            body = self.assert_ok(response)
            by_id = {item['id']: item for item in body['maintenance-table']['data']}
            bound = self.service.bind(source)
            for row in rows:
                if source['org'] != row['org']:
                    self.assertNotIn(row['id'], by_id)
                    self.assertFalse(bound.can_change(row))
                else:
                    expected = 'Editable' if bound.can_change(row) else 'Read only'
                    self.assertEqual(by_id[row['id']]['access'], expected)
        specs = self.server.extensions['callback_registry'].callbacks.values()
        for spec in specs:
            if spec.page_id == 'maintenance':
                self.assertIs(spec.policy, DEFINITION_ACCESS)


if __name__ == '__main__':
    unittest.main()
