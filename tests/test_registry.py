"""Registry contracts using real Dash registration, with no external services."""

from dataclasses import FrozenInstanceError
import unittest

import dash
from dash import ClientsideFunction, Dash, Input, Output, dcc, html
from dash import _callback

from demo_services import AccessDenied
from reporting_workspace.registry import AccessPolicy, CallbackRegistry, PageRegistry, PageSpec


ADMIN_A = {'id': 'admin-a', 'role': 'admin', 'org': 'A'}
ADMIN_B = {'id': 'admin-b', 'role': 'admin', 'org': 'B'}
USER_A = {'id': 'user-a', 'role': 'user', 'org': 'A'}


def page(page_id='reports', path='/reports', policy=None, **kwargs):
    return PageSpec(page_id, path, 'Reports', lambda runtime: runtime,
                    AccessPolicy.require(['admin']) if policy is None else policy, **kwargs)


class AccessPolicyTests(unittest.TestCase):
    def test_authentication_intent_is_mandatory_and_boolean(self):
        with self.assertRaises(TypeError):
            AccessPolicy()
        for value in (None, '', 0, 1, 'false'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                AccessPolicy(value)

    def test_public_does_not_inspect_the_identity(self):
        for user in (None, {}, object(), {'role': None}):
            self.assertTrue(AccessPolicy.public().allows(user))

    def test_public_with_claim_restrictions_is_rejected(self):
        for kwargs in ({'roles': ['admin']}, {'org': 'A'}, {'roles': []}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                AccessPolicy(False, **kwargs)

    def test_roles_are_copied_bounded_and_immutable(self):
        roles = ['admin', 'auditor']
        policy = AccessPolicy.require(roles)
        roles.append('user')
        self.assertEqual(policy.roles, ('admin', 'auditor'))
        with self.assertRaises(FrozenInstanceError):
            policy.org = 'A'
        for invalid in ('admin', (), [], [''], [' '], ['a\n'], [1],
                        ['a' * 65], ['admin', 'admin'], ['r{}'.format(i) for i in range(65)]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                AccessPolicy.require(invalid)

    def test_organization_is_bounded(self):
        for invalid in ('', ' ', '\x00', 'o' * 129, 1, []):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                AccessPolicy.require(org=invalid)

    def test_role_and_organization_are_both_required(self):
        policy = AccessPolicy.require(['admin'], org='A')
        self.assertTrue(policy.allows(ADMIN_A))
        self.assertFalse(policy.allows(ADMIN_B))
        self.assertFalse(policy.allows(USER_A))
        self.assertFalse(policy.allows(None))

    def test_missing_or_invalid_identity_claims_fail_closed(self):
        for invalid in (None, {}, False, True, 'admin', {'role': 'admin', 'org': 'A'},
                        dict(ADMIN_A, role=None), dict(ADMIN_A, id=''),
                        dict(ADMIN_A, org=1), dict(ADMIN_A, role='a' * 65)):
            with self.subTest(invalid=invalid):
                self.assertFalse(AccessPolicy.require().allows(invalid))
        self.assertTrue(AccessPolicy.require().allows(USER_A))

    def test_policy_implication_covers_authentication_roles_and_org(self):
        public = AccessPolicy.public()
        authenticated = AccessPolicy.require()
        admins = AccessPolicy.require(['admin'])
        staff_a = AccessPolicy.require(['admin', 'auditor'], org='A')
        admins_a = AccessPolicy.require(['admin'], org='A')
        for stricter, broader in ((public, public), (authenticated, public),
                                  (admins, authenticated), (admins_a, admins),
                                  (admins_a, staff_a), (admins_a, admins_a)):
            self.assertTrue(stricter.at_least_as_restrictive_as(broader))
        for weaker, stronger in ((public, authenticated), (authenticated, admins),
                                 (admins, admins_a), (staff_a, admins_a),
                                 (AccessPolicy.require(['admin'], org='B'), admins_a)):
            self.assertFalse(weaker.at_least_as_restrictive_as(stronger))
        with self.assertRaises(ValueError):
            admins.at_least_as_restrictive_as(None)


class PageRegistryTests(unittest.TestCase):
    def test_policy_is_explicit(self):
        with self.assertRaises(TypeError):
            PageSpec('reports', '/reports', 'Reports', lambda runtime: runtime)
        with self.assertRaises(ValueError):
            PageSpec('reports', '/reports', 'Reports', lambda runtime: runtime, None)

    def test_duplicate_ids_and_paths_do_not_replace_metadata(self):
        registry = PageRegistry()
        original = registry.register(page())
        for duplicate in (page(path='/other'), page(page_id='other')):
            with self.assertRaises(ValueError):
                registry.register(duplicate)
        self.assertIs(registry.get('/reports'), original)
        self.assertIs(registry.get_by_id('reports'), original)
        self.assertEqual(registry.pages, (original,))

    def test_paths_must_be_canonical_and_local(self):
        for invalid in ('', 'reports', '//reports', '/reports/', '/a//b', '/a/../b',
                        '/.', '/..', '/a?x=1', '/a#part', '/a b', '/a\\b',
                        '/%2e%2e', 'https://example.invalid/', '/a\n', '/' + 'a' * 512, None):
            with self.subTest(path=invalid), self.assertRaises(ValueError):
                page(path=invalid)
        for valid in ('/', '/login', '/logout', '/page-1', '/nested/report.v2'):
            self.assertEqual(page(path=valid).path, valid)

    def test_metadata_and_callback_registrar_are_validated(self):
        for field, value in (('page_id', ''), ('page_id', 'x/y'), ('title', ''),
                             ('layout', 'module:layout'), ('nav_label', ''),
                             ('nav_order', True), ('nav_order', 1.5),
                             ('register_callbacks', 'module:register')):
            values = dict(page_id='reports', path='/reports', title='Reports',
                          layout=lambda runtime: runtime, policy=AccessPolicy.public())
            values[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                PageSpec(**values)

    def test_layout_and_registrar_are_not_invoked_by_registration(self):
        calls = []
        spec = PageSpec('test', '/test', 'Test', lambda runtime: calls.append('layout'),
                        AccessPolicy.public(), register_callbacks=lambda registry, runtime: calls.append('callbacks'))
        registry = PageRegistry()
        registry.register(spec)
        registry.freeze()
        self.assertEqual(calls, [])

    def test_navigation_uses_declared_policy_and_deterministic_order(self):
        registry = PageRegistry()
        specs = (
            page('z', '/z', AccessPolicy.public(), nav_label='Last', nav_order=10),
            page('b', '/b', AccessPolicy.public(), nav_label='B'),
            page('a', '/a', AccessPolicy.public(), nav_label='A'),
            page('private', '/private', AccessPolicy.require(['admin'], org='A'), nav_label='Private', nav_order=-1),
            page('hidden', '/hidden', AccessPolicy.public()),
        )
        for spec in specs:
            registry.register(spec)
        self.assertEqual([spec.page_id for spec in registry.navigation(None)], ['a', 'b', 'z'])
        self.assertEqual([spec.page_id for spec in registry.navigation(USER_A)], ['a', 'b', 'z'])
        self.assertEqual([spec.page_id for spec in registry.navigation(ADMIN_B)], ['a', 'b', 'z'])
        self.assertEqual([spec.page_id for spec in registry.navigation(ADMIN_A)], ['private', 'a', 'b', 'z'])

    def test_unknown_lookups_do_not_normalize_into_an_authorized_page(self):
        registry = PageRegistry()
        registry.register(page())
        for unknown in ('/missing', '/reports/', '/reports?x=1', None, []):
            self.assertIsNone(registry.get(unknown))
        for unknown in ('missing', None, []):
            self.assertIsNone(registry.get_by_id(unknown))

    def test_catalog_requires_paired_bounded_metadata(self):
        for kwargs in ({'catalog_category': 'Finance'}, {'catalog_description': 'Monthly report'},
                       {'catalog_category': '', 'catalog_description': 'Monthly report'},
                       {'catalog_category': 'Finance', 'catalog_description': ' '},
                       {'catalog_category': 'F' * 65, 'catalog_description': 'Monthly report'},
                       {'catalog_category': 'Finance', 'catalog_description': 'D' * 241},
                       {'catalog_category': 'Finance\n', 'catalog_description': 'Monthly report'},
                       {'catalog_category': 1, 'catalog_description': 'Monthly report'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                page(**kwargs)
        spec = page(catalog_category='F' * 64, catalog_description='D' * 240)
        self.assertEqual(len(spec.catalog_category), 64)
        self.assertEqual(len(spec.catalog_description), 240)

    def test_catalog_tags_are_bounded_distinct_copied_and_immutable(self):
        tags = ['monthly', 'finance']
        spec = page(catalog_category='Finance', catalog_description='Monthly report', catalog_tags=tags)
        tags.append('later')
        self.assertEqual(spec.catalog_tags, ('monthly', 'finance'))
        with self.assertRaises(FrozenInstanceError):
            spec.catalog_tags = ('other',)
        for invalid in (None, 'monthly', [''], [' '], ['a\n'], [1], ['t' * 33],
                        ['monthly', 'monthly'], ['tag{}'.format(i) for i in range(13)]):
            with self.subTest(tags=invalid), self.assertRaises(ValueError):
                page(catalog_category='Finance', catalog_description='Monthly report', catalog_tags=invalid)
        with self.assertRaises(ValueError):
            page(catalog_tags=['monthly'])
        self.assertEqual(page().catalog_tags, ())

    def test_catalog_excludes_unlisted_and_unauthorized_metadata(self):
        registry = PageRegistry()
        registry.register(page('unlisted', '/unlisted', AccessPolicy.public(), nav_label='Visible nav only'))
        registry.register(page('public', '/public', AccessPolicy.public(), catalog_category='General',
                               catalog_description='Public summary', catalog_tags=('public',), nav_order=10))
        registry.register(page('private', '/private', AccessPolicy.require(['admin'], org='A'),
                               catalog_category='Private finance', catalog_description='Private report',
                               catalog_tags=('confidential',), nav_order=-1))
        self.assertEqual([spec.page_id for spec in registry.catalog(None)], ['public'])
        self.assertEqual([spec.page_id for spec in registry.catalog(USER_A)], ['public'])
        self.assertEqual([spec.page_id for spec in registry.catalog(ADMIN_B)], ['public'])
        self.assertEqual([spec.page_id for spec in registry.catalog(ADMIN_A)], ['private', 'public'])
        self.assertNotIn('confidential', repr(registry.catalog(USER_A)))
        self.assertIsInstance(registry.catalog(ADMIN_A), tuple)

    def test_catalog_filters_and_sorts_fifty_synthetic_specs(self):
        registry = PageRegistry()
        for index in reversed(range(50)):
            policy = AccessPolicy.public() if index % 3 == 0 else AccessPolicy.require(
                ['admin'], org='A' if index % 3 == 1 else 'B')
            registry.register(page(
                'synthetic-{:02d}'.format(index), '/synthetic-{}'.format(index), policy,
                nav_order=index % 5, catalog_category='Synthetic category {}'.format(index % 4),
                catalog_description='Synthetic test fixture {}'.format(index),
                catalog_tags=('fixture', 'tag-{}'.format(index % 6)),
            ))
        registry.freeze()
        self.assertEqual(len(registry.pages), 50)
        for user in (None, USER_A, ADMIN_A, ADMIN_B):
            expected = tuple(sorted((spec for spec in registry.pages if spec.policy.allows(user)),
                                    key=lambda spec: (spec.nav_order, spec.page_id)))
            with self.subTest(user=user):
                self.assertEqual(registry.catalog(user), expected)
        self.assertEqual(len(registry.catalog(None)), 17)
        self.assertEqual(len(registry.catalog(ADMIN_A)), 34)
        self.assertEqual(len(registry.catalog(ADMIN_B)), 33)

    def test_freeze_and_metadata_exposure_are_immutable(self):
        registry = PageRegistry()
        spec = registry.register(page())
        self.assertIs(registry.freeze(), registry)
        self.assertTrue(registry.frozen)
        self.assertIs(registry.freeze(), registry)
        self.assertIsInstance(registry.pages, tuple)
        with self.assertRaises(FrozenInstanceError):
            spec.policy = AccessPolicy.public()
        with self.assertRaises(RuntimeError):
            registry.register(page('other', '/other'))
        with self.assertRaises(ValueError):
            PageRegistry().register({'page_id': 'test'})


class CallbackRegistryTests(unittest.TestCase):
    def setUp(self):
        self.app = Dash(__name__, server=False, use_pages=False)
        self.pages = PageRegistry()
        self.pages.register(page())
        self.identity = None
        self.lookups = 0

        def identity():
            self.lookups += 1
            return self.identity

        self.registry = CallbackRegistry(self.app, self.pages, identity)

    def callback(self, callback_id='refresh', output_id='result', policy=None, page_id=None):
        return self.registry.callback(Output(output_id, 'children'), Input('button', 'n_clicks'),
                                      callback_id=callback_id,
                                      policy=AccessPolicy.require(['admin']) if policy is None else policy,
                                      page_id=page_id)

    def test_explicit_policy_and_callback_id_are_mandatory(self):
        for kwargs in ({'callback_id': 'test'}, {'policy': AccessPolicy.public()}):
            with self.assertRaises(TypeError):
                self.registry.callback(Output('result', 'children'), Input('button', 'n_clicks'), **kwargs)
        for invalid in (None, True, {'authenticated': True}):
            with self.assertRaises(ValueError):
                self.registry.callback(Output('result', 'children'), Input('button', 'n_clicks'),
                                       callback_id='test', policy=invalid)
        self.assertEqual(self.app.callback_map, {})

    def test_public_guard_never_calls_identity_getter(self):
        def fail():
            raise AssertionError('public callback consulted current_user')
        registry = CallbackRegistry(self.app, self.pages, fail)
        callback = registry.callback(Output('public', 'children'), Input('button', 'n_clicks'),
                                     callback_id='public', policy=AccessPolicy.public())(lambda clicks: clicks)
        self.assertEqual(callback(3), 3)
        registry.freeze()
        self.assertEqual(registry.policy_for('public.children'), AccessPolicy.public())

    def test_direct_callback_calls_are_guarded_before_function_invocation(self):
        calls = []
        callback = self.callback(policy=AccessPolicy.require(['admin'], org='A'))(
            lambda clicks: calls.append(clicks) or 'ok')
        for identity in (None, USER_A, ADMIN_B):
            self.identity = identity
            with self.assertRaises(AccessDenied):
                callback(1)
        self.assertEqual(calls, [])
        self.identity = ADMIN_A
        self.assertEqual(callback(2), 'ok')
        self.assertEqual(calls, [2])
        self.assertEqual(self.lookups, 4)

    def test_guard_preserves_function_metadata(self):
        def named(clicks):
            """Original callback documentation."""
            return clicks
        callback = self.callback()(named)
        self.assertEqual(callback.__name__, 'named')
        self.assertEqual(callback.__doc__, named.__doc__)

    def test_duplicate_ids_are_rejected_without_registering_another_output(self):
        self.callback()(lambda clicks: clicks)
        with self.assertRaises(ValueError):
            self.callback(output_id='second')(lambda clicks: clicks)
        self.assertEqual(set(self.app.callback_map), {'result.children'})
        self.registry.freeze()

    def test_duplicate_outputs_restore_the_original_dash_callback(self):
        self.callback()(lambda clicks: 'original')
        original = self.app.callback_map['result.children']
        dependencies = list(self.app._callback_list)
        with self.assertRaises(ValueError):
            self.callback(callback_id='replacement')(lambda clicks: 'replacement')
        self.assertIs(self.app.callback_map['result.children'], original)
        self.assertEqual(self.app._callback_list, dependencies)
        self.assertEqual(len(self.registry.callbacks), 1)
        self.registry.freeze()

    def test_grouping_outputs_differently_cannot_duplicate_a_target(self):
        self.callback()(lambda clicks: clicks)
        with self.assertRaises(ValueError):
            self.registry.callback(Output('result', 'children'), Output('other', 'children'),
                                   Input('button', 'n_clicks'), callback_id='grouped',
                                   policy=AccessPolicy.public())(lambda clicks: (clicks, clicks))
        self.assertEqual(set(self.app.callback_map), {'result.children'})
        self.registry.freeze()

    def test_allow_duplicate_cannot_bypass_target_uniqueness(self):
        self.callback()(lambda clicks: clicks)
        with self.assertRaises(ValueError):
            self.registry.callback(Output('result', 'children', allow_duplicate=True),
                                   Input('other', 'n_clicks'), callback_id='duplicate',
                                   policy=AccessPolicy.public(), prevent_initial_call=True)(lambda clicks: clicks)
        self.assertEqual(set(self.app.callback_map), {'result.children'})
        self.registry.freeze()

    def test_duplicate_targets_inside_one_callback_are_rejected(self):
        with self.assertRaises(ValueError):
            self.registry.callback(Output('result', 'children'), Output('result', 'children'),
                                   Input('button', 'n_clicks'), callback_id='duplicate',
                                   policy=AccessPolicy.public())(lambda clicks: (clicks, clicks))
        self.assertEqual(self.app.callback_map, {})
        self.assertEqual(self.app._callback_list, [])

    def test_page_bound_callback_cannot_weaken_the_page_policy(self):
        self.pages.register(page('org-a', '/org-a', AccessPolicy.require(['admin'], org='A')))
        for policy in (AccessPolicy.public(), AccessPolicy.require(),
                       AccessPolicy.require(['admin', 'user']), AccessPolicy.require(org='A'),
                       AccessPolicy.require(['admin']), AccessPolicy.require(['admin'], org='B')):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                self.callback(page_id='org-a', policy=policy)(lambda clicks: clicks)
        self.callback(page_id='org-a', policy=AccessPolicy.require(['admin'], org='A'))(lambda clicks: clicks)
        self.registry.freeze()

    def test_page_bound_callback_can_narrow_roles_and_organization(self):
        self.pages.register(page('staff', '/staff', AccessPolicy.require(['admin', 'auditor'])))
        self.callback(page_id='staff', policy=AccessPolicy.require(['admin'], org='A'))(lambda clicks: clicks)
        self.assertEqual(self.registry.callbacks['result.children'].page_id, 'staff')

    def test_unknown_page_is_rejected(self):
        with self.assertRaises(ValueError):
            self.callback(page_id='missing')(lambda clicks: clicks)
        self.assertEqual(self.app.callback_map, {})

    def test_bound_registrar_automatically_supplies_its_page_id(self):
        bound = self.registry.for_page('reports')
        callback = bound.callback(
            Output('bound', 'children'), Input('button', 'n_clicks'),
            callback_id='bound', policy=AccessPolicy.require(['admin']),
        )(lambda clicks: clicks)
        self.assertEqual(self.registry.callbacks['bound.children'].page_id, 'reports')
        with self.assertRaises(AccessDenied):
            callback(1)
        self.identity = ADMIN_A
        self.assertEqual(callback(2), 2)
        self.registry.freeze()

    def test_bound_registrar_accepts_an_explicit_matching_page_id(self):
        self.registry.for_page('reports').callback(
            Output('bound', 'children'), Input('button', 'n_clicks'),
            callback_id='bound', policy=AccessPolicy.require(['admin']), page_id='reports',
        )(lambda clicks: clicks)
        self.assertEqual(self.registry.callbacks['bound.children'].page_id, 'reports')
        self.registry.freeze()

    def test_bound_registrar_cannot_weaken_policy_by_omitting_page_id(self):
        self.pages.register(page('org-a', '/org-a', AccessPolicy.require(['admin'], org='A')))
        bound = self.registry.for_page('org-a')
        for policy in (AccessPolicy.public(), AccessPolicy.require(),
                       AccessPolicy.require(['admin']), AccessPolicy.require(['admin', 'user'], org='A'),
                       AccessPolicy.require(['admin'], org='B')):
            with self.subTest(policy=policy), self.assertRaisesRegex(ValueError, 'weaker'):
                bound.callback(Output('bound', 'children'), Input('button', 'n_clicks'),
                               callback_id='bound', policy=policy)(lambda clicks: clicks)
        self.assertEqual(self.app.callback_map, {})
        self.assertEqual(self.app._callback_list, [])

    def test_bound_registrar_cannot_change_or_remove_page_scope(self):
        self.pages.register(page('public', '/public', AccessPolicy.public()))
        bound = self.registry.for_page('reports')
        for page_id in (None, 'public', 'missing', '', [], {}):
            with self.subTest(page_id=page_id), self.assertRaisesRegex(ValueError, 'page_id'):
                bound.callback(Output('bound', 'children'), Input('button', 'n_clicks'),
                               callback_id='bound', policy=AccessPolicy.public(), page_id=page_id)(lambda clicks: clicks)
        self.assertEqual(self.app.callback_map, {})
        self.assertEqual(self.app._callback_list, [])

    def test_bound_registrar_still_requires_an_explicit_policy(self):
        bound = self.registry.for_page('reports')
        with self.assertRaises(TypeError):
            bound.callback(Output('bound', 'children'), Input('button', 'n_clicks'), callback_id='bound')
        with self.assertRaises(ValueError):
            bound.callback(Output('bound', 'children'), Input('button', 'n_clicks'),
                           callback_id='bound', policy=None)
        self.assertEqual(self.app.callback_map, {})

    def test_bound_registrar_requires_known_page_and_cannot_outlive_freeze(self):
        for unknown in ('missing', None, [], {}):
            with self.subTest(page_id=unknown), self.assertRaises(ValueError):
                self.registry.for_page(unknown)
        bound = self.registry.for_page('reports')
        pending = bound.callback(Output('bound', 'children'), Input('button', 'n_clicks'),
                                 callback_id='bound', policy=AccessPolicy.require(['admin']))
        self.registry.freeze()
        with self.assertRaises(RuntimeError):
            self.registry.for_page('reports')
        with self.assertRaises(RuntimeError):
            bound.callback(Output('later', 'children'), Input('button', 'n_clicks'),
                           callback_id='later', policy=AccessPolicy.require(['admin']))
        with self.assertRaises(RuntimeError):
            pending(lambda clicks: clicks)

    def test_actual_dash_keys_for_multi_and_pattern_outputs_are_used(self):
        policy = AccessPolicy.public()
        self.registry.callback(Output('a-b', 'children'), Output({'type': 'sample', 'index': 1}, 'children'),
                               Input('button', 'n_clicks'), callback_id='unusual-outputs',
                               policy=policy)(lambda clicks: (clicks, clicks))
        self.assertEqual(len(self.app.callback_map), 1)
        actual_key = next(iter(self.app.callback_map))
        self.assertEqual(set(self.registry.callbacks), {actual_key})
        self.assertEqual(self.registry.callbacks[actual_key].output_key, actual_key)
        self.assertIs(self.registry.policy_for(actual_key), policy)
        self.registry.freeze()

    def test_dash_grouped_keyword_dependencies_keep_the_actual_key(self):
        self.registry.callback(
            output={'left': Output('left', 'children'), 'right': Output('right', 'children')},
            inputs={'clicks': Input('button', 'n_clicks')},
            callback_id='grouped-keywords', policy=AccessPolicy.public(),
        )(lambda clicks: {'left': clicks, 'right': clicks})
        actual_key = next(iter(self.app.callback_map))
        self.assertEqual(self.registry.policy_for(actual_key), AccessPolicy.public())
        self.registry.freeze()

    def test_unknown_or_malformed_output_lookup_denies(self):
        self.callback()(lambda clicks: clicks)
        for output in ('missing.children', '..result.children..', '', None, [], {}):
            self.assertIsNone(self.registry.policy_for(output))

    def test_direct_dash_callback_bypassing_registry_is_detected(self):
        self.callback()(lambda clicks: clicks)
        self.app.callback(Output('unregistered', 'children'), Input('button', 'n_clicks'))(lambda clicks: clicks)
        self.assertIsNone(self.registry.policy_for('unregistered.children'))
        with self.assertRaisesRegex(ValueError, 'declared policy'):
            self.registry.freeze()
        self.assertFalse(self.registry.frozen)

    def test_direct_replacement_of_declared_callback_is_detected_and_denied(self):
        self.callback()(lambda clicks: clicks)
        self.app.callback(Output('result', 'children'), Input('other', 'n_clicks'))(lambda clicks: clicks)
        self.assertIsNone(self.registry.policy_for('result.children'))
        with self.assertRaises(ValueError):
            self.registry.freeze()

    def test_callbacks_existing_before_registry_construction_are_detected(self):
        app = Dash(__name__, server=False, use_pages=False)
        app.callback(Output('unknown', 'children'), Input('button', 'n_clicks'))(lambda clicks: clicks)
        registry = CallbackRegistry(app, PageRegistry(), lambda: None)
        with self.assertRaises(ValueError):
            registry.freeze()

    def test_callback_dependency_list_drift_is_detected(self):
        self.callback()(lambda clicks: clicks)
        self.app._callback_list.append(dict(self.app._callback_list[0]))
        with self.assertRaises(ValueError):
            self.registry.freeze()

    def test_freeze_closes_callback_and_page_registrations(self):
        pending = self.callback(callback_id='pending', output_id='pending')
        self.callback()(lambda clicks: clicks)
        self.assertIs(self.registry.freeze(), self.registry)
        self.assertTrue(self.registry.frozen)
        self.assertTrue(self.pages.frozen)
        self.assertIs(self.registry.freeze(), self.registry)
        with self.assertRaises(RuntimeError):
            self.callback(callback_id='new', output_id='new')
        with self.assertRaises(RuntimeError):
            pending(lambda clicks: clicks)
        with self.assertRaises(RuntimeError):
            self.pages.register(page('later', '/later'))

    def test_callback_metadata_is_read_only(self):
        self.callback()(lambda clicks: clicks)
        with self.assertRaises(TypeError):
            self.registry.callbacks['result.children'] = None
        with self.assertRaises(FrozenInstanceError):
            self.registry.callbacks['result.children'].policy = AccessPolicy.public()

    def test_post_freeze_callback_map_tampering_fails_closed(self):
        self.callback()(lambda clicks: clicks)
        self.registry.freeze()
        self.app.callback_map['result.children']['callback'] = lambda: 'unprotected'
        self.assertIsNone(self.registry.policy_for('result.children'))
        with self.assertRaises(ValueError):
            self.registry.freeze()

    def test_separate_factories_do_not_share_metadata_or_dash_globals(self):
        global_pages = dict(dash.page_registry)
        global_callbacks = dict(_callback.GLOBAL_CALLBACK_MAP)
        global_list = list(_callback.GLOBAL_CALLBACK_LIST)

        def build(policy):
            app = Dash(__name__, server=False, use_pages=False)
            pages = PageRegistry()
            pages.register(page(policy=policy))
            registry = CallbackRegistry(app, pages, lambda: ADMIN_A)
            registry.callback(Output('same', 'children'), Input('button', 'n_clicks'),
                              callback_id='same', policy=policy, page_id='reports')(lambda clicks: clicks)
            registry.freeze()
            return app, pages, registry

        first_app, first_pages, first_registry = build(AccessPolicy.public())
        second_app, second_pages, second_registry = build(AccessPolicy.require(['admin']))
        self.assertIsNot(first_pages, second_pages)
        self.assertIsNot(first_registry, second_registry)
        self.assertIsNot(first_app.callback_map['same.children'], second_app.callback_map['same.children'])
        self.assertEqual(first_registry.policy_for('same.children'), AccessPolicy.public())
        self.assertEqual(second_registry.policy_for('same.children'), AccessPolicy.require(['admin']))
        self.assertEqual(dict(dash.page_registry), global_pages)
        self.assertEqual(dict(_callback.GLOBAL_CALLBACK_MAP), global_callbacks)
        self.assertEqual(_callback.GLOBAL_CALLBACK_LIST, global_list)


class ClientsideRegistryTests(unittest.TestCase):
    def setUp(self):
        self.app = Dash(__name__, server=False, use_pages=False)
        self.pages = PageRegistry()
        self.pages.register(page())
        self.pages.register(page('public', '/public', AccessPolicy.public()))

        def no_identity_lookup():
            raise AssertionError('clientside registration consulted identity')

        self.registry = CallbackRegistry(self.app, self.pages, no_identity_lookup)

    def register(self, callback_id='theme', output_id='theme', **kwargs):
        return self.registry.clientside_callback(
            ClientsideFunction(namespace='workspace_theme', function_name='apply'),
            Output(output_id, 'data'), Input('theme-toggle', 'n_clicks'),
            callback_id=callback_id, policy=AccessPolicy.public(), **kwargs
        )

    def test_client_registration_records_actual_metadata_but_no_server_policy(self):
        spec = self.register()
        actual_key = next(iter(self.app.callback_map))
        self.assertIs(spec, self.registry.callbacks[actual_key])
        self.assertEqual(spec.kind, 'client')
        self.assertEqual(spec.output_key, actual_key)
        self.assertEqual(spec.policy, AccessPolicy.public())
        self.assertNotIn('callback', self.app.callback_map[actual_key])
        self.assertEqual(self.app._callback_list[0]['clientside_function'],
                         {'namespace': 'workspace_theme', 'function_name': 'apply'})
        self.assertIsNone(self.registry.policy_for(actual_key))
        self.assertIs(self.registry.freeze(), self.registry)

    def test_client_policy_is_mandatory_and_public_only(self):
        function = ClientsideFunction(namespace='theme', function_name='apply')
        with self.assertRaises(TypeError):
            self.registry.clientside_callback(function, Output('theme', 'data'),
                                              Input('toggle', 'n_clicks'), callback_id='theme')
        for policy in (None, True, AccessPolicy.require(), AccessPolicy.require(['admin'])):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                self.registry.clientside_callback(function, Output('theme', 'data'),
                                                  Input('toggle', 'n_clicks'), callback_id='theme', policy=policy)
        self.assertEqual(self.app.callback_map, {})
        self.assertEqual(self.app._callback_list, [])

    def test_only_named_bounded_clientside_functions_are_accepted(self):
        for function in ('function () { return null; }', {}, lambda: None,
                         ClientsideFunction(namespace='bad/path', function_name='apply'),
                         ClientsideFunction(namespace='theme', function_name=None)):
            with self.subTest(function=function), self.assertRaises(ValueError):
                self.registry.clientside_callback(function, Output('theme', 'data'),
                                                  Input('toggle', 'n_clicks'), callback_id='theme',
                                                  policy=AccessPolicy.public())
        self.assertEqual(self.app.callback_map, {})
        self.assertEqual(self.app._callback_list, [])

    def test_mixed_server_and_client_callbacks_freeze_together(self):
        self.register()
        public = AccessPolicy.public()
        self.registry.callback(Output('server', 'children'), Input('button', 'n_clicks'),
                               callback_id='server', policy=public)(lambda clicks: clicks)
        self.registry.freeze()
        self.assertEqual(self.registry.callbacks['server.children'].kind, 'server')
        self.assertIs(self.registry.policy_for('server.children'), public)
        self.assertIsNone(self.registry.policy_for('theme.data'))

    def test_client_duplicate_output_restores_original_map_and_list(self):
        self.register()
        original = self.app.callback_map['theme.data']
        records = list(self.app._callback_list)
        with self.assertRaises(ValueError):
            self.register(callback_id='duplicate')
        self.assertIs(self.app.callback_map['theme.data'], original)
        self.assertEqual(self.app._callback_list, records)
        self.assertEqual(len(self.registry.callbacks), 1)
        self.registry.freeze()

    def test_server_cannot_reuse_client_callback_id_or_output(self):
        self.register()
        for callback_id, output in (('theme', 'other'), ('server', 'theme')):
            with self.subTest(callback_id=callback_id), self.assertRaises(ValueError):
                self.registry.callback(Output(output, 'data'), Input('button', 'n_clicks'),
                                       callback_id=callback_id, policy=AccessPolicy.public())(lambda clicks: clicks)
        self.assertEqual(set(self.app.callback_map), {'theme.data'})
        self.assertNotIn('callback', self.app.callback_map['theme.data'])
        self.registry.freeze()

    def test_client_cannot_reuse_server_callback_id_or_output(self):
        self.registry.callback(Output('server', 'data'), Input('button', 'n_clicks'),
                               callback_id='server', policy=AccessPolicy.public())(lambda clicks: clicks)
        original = self.app.callback_map['server.data']
        for callback_id, output in (('server', 'other'), ('theme', 'server')):
            with self.subTest(callback_id=callback_id), self.assertRaises(ValueError):
                self.register(callback_id=callback_id, output_id=output)
        self.assertIs(self.app.callback_map['server.data'], original)
        self.assertTrue(callable(self.app.callback_map['server.data']['callback']))
        self.registry.freeze()

    def test_client_multi_output_uses_actual_dash_key(self):
        spec = self.registry.clientside_callback(
            ClientsideFunction(namespace='workspace_theme', function_name='apply'),
            Output('theme', 'data'), Output('label', 'children'), Input('toggle', 'n_clicks'),
            callback_id='theme', policy=AccessPolicy.public(), prevent_initial_call=True,
        )
        actual_key = next(iter(self.app.callback_map))
        self.assertEqual(spec.output_key, actual_key)
        self.assertIsNone(self.registry.policy_for(actual_key))
        self.registry.freeze()

    def test_client_grouping_cannot_hide_duplicate_target(self):
        self.register()
        with self.assertRaises(ValueError):
            self.registry.clientside_callback(
                ClientsideFunction(namespace='workspace_theme', function_name='apply'),
                Output('theme', 'data'), Output('label', 'children'), Input('toggle', 'n_clicks'),
                callback_id='duplicate', policy=AccessPolicy.public(),
            )
        self.assertEqual(set(self.app.callback_map), {'theme.data'})
        self.registry.freeze()

    def test_undeclared_direct_clientside_callback_is_detected(self):
        self.app.clientside_callback(
            ClientsideFunction(namespace='workspace_theme', function_name='apply'),
            Output('theme', 'data'), Input('toggle', 'n_clicks'),
        )
        self.assertIsNone(self.registry.policy_for('theme.data'))
        with self.assertRaisesRegex(ValueError, 'declared policy'):
            self.registry.freeze()

    def test_client_freeze_detects_map_list_and_descriptor_changes(self):
        for change in ('map-replaced', 'list-replaced', 'descriptor-replaced',
                       'descriptor-changed', 'server-handler-added'):
            app = Dash(__name__, server=False, use_pages=False)
            registry = CallbackRegistry(app, PageRegistry(), lambda: None)
            registry.clientside_callback(
                ClientsideFunction(namespace='workspace_theme', function_name='apply'),
                Output('theme', 'data'), Input('toggle', 'n_clicks'),
                callback_id='theme', policy=AccessPolicy.public(),
            )
            if change == 'map-replaced':
                app.callback_map['theme.data'] = dict(app.callback_map['theme.data'])
            elif change == 'list-replaced':
                app._callback_list[0] = dict(app._callback_list[0])
            elif change == 'descriptor-replaced':
                app._callback_list[0]['clientside_function'] = dict(app._callback_list[0]['clientside_function'])
            elif change == 'descriptor-changed':
                app._callback_list[0]['clientside_function']['function_name'] = 'other'
            else:
                app.callback_map['theme.data']['callback'] = lambda: 'unsafe'
            with self.subTest(change=change), self.assertRaises(ValueError):
                registry.freeze()
            self.assertIsNone(registry.policy_for('theme.data'))

    def test_client_registration_obeys_page_binding_and_freeze(self):
        bound = self.registry.for_page('public')
        function = ClientsideFunction(namespace='workspace_theme', function_name='apply')
        spec = bound.clientside_callback(function, Output('theme', 'data'), Input('toggle', 'n_clicks'),
                                         callback_id='theme', policy=AccessPolicy.public())
        self.assertEqual(spec.page_id, 'public')
        for page_id in (None, 'reports'):
            with self.subTest(page_id=page_id), self.assertRaises(ValueError):
                bound.clientside_callback(function, Output('other', 'data'), Input('toggle', 'n_clicks'),
                                          callback_id='other', policy=AccessPolicy.public(), page_id=page_id)
        private_spec = self.registry.for_page('reports').clientside_callback(
            function, Output('private', 'data'), Input('toggle', 'n_clicks'),
            callback_id='private', policy=AccessPolicy.public(),
        )
        self.assertEqual(private_spec.page_id, 'reports')
        self.assertEqual(private_spec.policy, AccessPolicy.public())
        # Ownership by a protected page grants no HTTP/server-data permission.
        self.assertIsNone(self.registry.policy_for('private.data'))
        self.registry.freeze()
        with self.assertRaises(RuntimeError):
            self.register(callback_id='later', output_id='later')

    def test_client_registration_does_not_mutate_dash_globals(self):
        pages_before = dict(dash.page_registry)
        callbacks_before = dict(_callback.GLOBAL_CALLBACK_MAP)
        list_before = list(_callback.GLOBAL_CALLBACK_LIST)
        self.register()
        self.registry.freeze()
        self.assertEqual(dict(dash.page_registry), pages_before)
        self.assertEqual(dict(_callback.GLOBAL_CALLBACK_MAP), callbacks_before)
        self.assertEqual(_callback.GLOBAL_CALLBACK_LIST, list_before)

    def test_actual_application_denies_http_dispatch_to_client_only_output(self):
        from reporting_workspace.application import create_app
        from reporting_workspace.config import Settings

        def register(callbacks, runtime):
            callbacks.clientside_callback(
                ClientsideFunction(namespace='registry_probe', function_name='apply'),
                Output('registry-client-probe', 'data'), Input('registry-probe-toggle', 'n_clicks'),
                callback_id='registry-client-probe', policy=AccessPolicy.public(),
            )

        spec = PageSpec(
            'registry-client-probe', '/registry-client-probe', 'Client probe',
            lambda runtime: html.Div([dcc.Store(id='registry-client-probe'),
                                     html.Button('Toggle', id='registry-probe-toggle')]),
            AccessPolicy.public(), register_callbacks=register,
        )
        server = create_app(Settings(), extra_pages=(spec,))
        response = server.test_client().post('/_dash-update-component', json={
            'output': 'registry-client-probe.data',
            'outputs': {'id': 'registry-client-probe', 'property': 'data'},
            'inputs': [{'id': 'registry-probe-toggle', 'property': 'n_clicks', 'value': 1}],
            'state': [], 'changedPropIds': ['registry-probe-toggle.n_clicks'],
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['error'], 'Unregistered callback')


if __name__ == '__main__':
    unittest.main()
