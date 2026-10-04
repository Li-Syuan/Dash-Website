"""Catalog metadata, real Dash transport and local preference JavaScript contracts.

The 50-report fixtures exercise scale without inventing reports in the app.
Transport/Node checks are not browser-rendering or accessibility certification.
"""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

from dash import html
from plotly.utils import PlotlyJSONEncoder

from reporting_workspace.application import create_app
from reporting_workspace.lifecycle import dispose_app
from reporting_workspace.registry import AccessPolicy, PageRegistry, PageSpec
from reporting_workspace.ui_pages.overview import (
    MAX_PREFERENCES, MAX_QUERY, PAGE_SIZE, catalog_model, catalog_scope,
    clean_preferences, render_catalog,
)

try:
    from .test_application import AppTestCase, FixtureIdentity, FixtureReports, callback_output_spec, components
except ImportError:
    from test_application import AppTestCase, FixtureIdentity, FixtureReports, callback_output_spec, components


ADMIN_A = {'id': 'fixture-admin', 'role': 'admin', 'org': 'A'}
USER_A = {'id': 'fixture-user', 'role': 'user', 'org': 'A'}
ADMIN_B = {'id': 'admin-b', 'role': 'admin', 'org': 'B'}
USER_B = {'id': 'user-b', 'role': 'user', 'org': 'B'}


def synthetic_pages(calls=None):
    calls = calls if calls is not None else []
    pages = []
    for number in range(50):
        policy = (AccessPolicy.require() if number < 40 else AccessPolicy.require(roles=('admin',))
                  if number < 45 else AccessPolicy.require(roles=('admin',), org='A'))
        category = 'Operations' if number < 20 else 'Finance' if number < 40 else 'Restricted admin' if number < 45 else 'Restricted organization'
        def layout(runtime, number=number):
            calls.append(number)
            return html.P('Report payload {}'.format(number))
        pages.append(PageSpec(
            'synthetic-{:02d}'.format(number), '/synthetic-{:02d}'.format(number),
            'Synthetic report {:02d}'.format(number), layout, policy,
            nav_label='Report {:02d}'.format(number), nav_order=100 + number,
            catalog_category=category, catalog_description='Description marker {:02d}'.format(number),
            catalog_tags=('measure-{:02d}'.format(number), 'synthetic'),
        ))
    return tuple(pages)


def make_registry():
    registry = PageRegistry()
    for page in synthetic_pages():
        registry.register(page)
    return registry.freeze()


def serialized(value):
    return json.dumps(value, cls=PlotlyJSONEncoder)


class CatalogModelTests(unittest.TestCase):
    def setUp(self):
        self.registry = make_registry()

    def test_fifty_metadata_cards_paginate_without_loading_any_report(self):
        model = catalog_model(self.registry, ADMIN_A)
        self.assertEqual(model['total'], 50)
        self.assertEqual(model['page_count'], 5)
        self.assertEqual(len(model['pages']), PAGE_SIZE)
        found = []
        for expected in (12, 12, 12, 12, 2):
            self.assertEqual(len(model['pages']), expected)
            found.extend(page.page_id for page in model['pages'])
            model = catalog_model(self.registry, ADMIN_A, page_state=model['page_state'], action='catalog-next')
        self.assertEqual(found, ['synthetic-{:02d}'.format(number) for number in range(50)])
        self.assertEqual(model['page_state']['index'], 4)
        model = catalog_model(self.registry, ADMIN_A, page_state=model['page_state'], action='catalog-prev')
        self.assertEqual(model['page_state']['index'], 3)

    def test_search_matches_title_navigation_description_tags_and_id(self):
        for query in ('Synthetic report 17', 'Report 17', 'Description marker 17', 'MEASURE-17', 'synthetic-17'):
            with self.subTest(query=query):
                model = catalog_model(self.registry, USER_A, query=query)
                self.assertEqual([page.page_id for page in model['pages']], ['synthetic-17'])
        model = catalog_model(self.registry, USER_A, query='synthetic marker', category='Finance')
        self.assertEqual(model['total'], 20)
        self.assertEqual(model['categories'], {'Operations': 20, 'Finance': 20})

    def test_authorized_subset_controls_metadata_categories_counts_and_search(self):
        for user, count in ((None, 0), (USER_A, 40), (USER_B, 40), (ADMIN_B, 45), (ADMIN_A, 50)):
            with self.subTest(user=user):
                model = catalog_model(self.registry, user)
                self.assertEqual(model['authorized_total'], count)
                self.assertEqual(len(model['allowed_ids']), count)
                self.assertEqual(sum(model['categories'].values()), count)
                hidden = catalog_model(self.registry, user, query='measure-49')
                self.assertEqual(hidden['total'], 1 if user == ADMIN_A else 0)
                if count < 50:
                    self.assertNotIn('Restricted organization', model['categories'])
                    self.assertNotIn('synthetic-49', serialized(render_catalog(model)))
                    self.assertNotIn('synthetic-49', model['allowed_ids'])
                if count < 45:
                    self.assertNotIn('Restricted admin', model['categories'])

    def test_forged_favorites_never_resolve_registry_or_expand_authorization(self):
        value = {'scope': catalog_scope(USER_A), 'favorites': ['synthetic-49', 'synthetic-17', '/page3', {}, 1],
                 'recent': ['synthetic-48', 'synthetic-02', 'https://example.invalid/']}
        with patch.object(self.registry, 'get_by_id', side_effect=AssertionError('Preferences must not resolve IDs')):
            model = catalog_model(self.registry, USER_A, preferences=value, view='favorites')
        self.assertEqual(model['total'], 1)
        self.assertEqual(model['pages'][0].page_id, 'synthetic-17')
        self.assertEqual(model['preferences']['recent'], ['synthetic-02'])
        self.assertNotIn('synthetic-49', serialized(render_catalog(model)))
        self.assertNotIn('https://', serialized(render_catalog(model)))

    def test_preferences_are_scoped_to_id_role_and_org_and_bounded(self):
        scopes = {catalog_scope(ADMIN_A), catalog_scope(USER_A), catalog_scope(ADMIN_B),
                  catalog_scope(dict(ADMIN_A, id='another-user'))}
        self.assertEqual(len(scopes), 4)
        allowed = {'id-{}'.format(n) for n in range(150)}
        value = {'scope': catalog_scope(USER_A), 'favorites': list(allowed), 'recent': ['id-1'] * 150,
                 'rows': ['must not survive']}
        cleaned = clean_preferences(value, catalog_scope(USER_A), allowed)
        self.assertEqual(len(cleaned['favorites']), MAX_PREFERENCES)
        self.assertEqual(cleaned['recent'], ['id-1'])
        self.assertEqual(set(cleaned), {'scope', 'favorites', 'recent'})
        self.assertEqual(clean_preferences(value, catalog_scope(ADMIN_A), allowed)['favorites'], [])
        for invalid in (None, [], 'text', 123):
            self.assertEqual(clean_preferences(invalid, catalog_scope(USER_A), allowed)['favorites'], [])
        self.assertIsNone(catalog_scope(None))
        for claim in ADMIN_A.values():
            self.assertNotIn(claim, catalog_scope(ADMIN_A))

    def test_defaults_have_bounded_shortcuts_and_recent_view_keeps_open_order(self):
        ids = ['synthetic-{:02d}'.format(n) for n in range(9, 0, -1)]
        prefs = {'scope': catalog_scope(USER_A), 'favorites': ids, 'recent': list(reversed(ids))}
        model = catalog_model(self.registry, USER_A, preferences=prefs)
        self.assertEqual({key: len(value) for key, value in model['shortcuts'].items()}, {'favorites': 4, 'recent': 4})
        tree = json.loads(serialized(render_catalog(model)))
        buttons = [node['props']['id'] for node in components(tree) if node['type'] == 'Button']
        self.assertEqual(len(buttons), PAGE_SIZE)
        self.assertEqual(len(set(serialized(item) for item in buttons)), PAGE_SIZE)
        model = catalog_model(self.registry, USER_A, preferences=prefs, view='recent')
        self.assertEqual([page.page_id for page in model['pages']], list(reversed(ids)))
        self.assertEqual(model['shortcuts'], {})

    def test_malformed_and_overlong_filters_and_page_state_are_bounded(self):
        base = catalog_model(self.registry, ADMIN_A)
        state = base['page_state']
        for value in (True, None, [], {}, -100, 1000001, 10 ** 100):
            with self.subTest(value=value):
                model = catalog_model(self.registry, ADMIN_A, page_state=dict(state, index=value))
                self.assertEqual(model['page_state']['index'], 0)
        model = catalog_model(self.registry, ADMIN_A, page_state=dict(state, index=999999))
        self.assertEqual(model['page_state']['index'], 4)
        filtered = catalog_model(self.registry, ADMIN_A, query='measure-01', page_state=model['page_state'], action='catalog-next')
        self.assertEqual(filtered['page_state']['index'], 0)
        self.assertEqual(filtered['total'], 1)
        for invalid in (None, [], {}, True, 1):
            model = catalog_model(self.registry, USER_A, query=invalid, category=invalid, view=invalid)
            self.assertEqual(model['total'], 40)
        model = catalog_model(self.registry, USER_A, query='x' * MAX_QUERY + 'measure-01')
        self.assertEqual(model['total'], 0)
        model = catalog_model(self.registry, USER_A, category='Restricted organization')
        self.assertEqual(model['categories'], {'Operations': 20, 'Finance': 20})

    def test_card_metadata_is_plain_react_text_and_favorite_is_native_button(self):
        registry = PageRegistry()
        page = PageSpec('safe-text', '/safe-text', '<script>title</script>', lambda runtime: None,
                        AccessPolicy.require(), catalog_category='Category',
                        catalog_description='<img src=x onerror=bad()>', catalog_tags=('<b>tag</b>',))
        registry.register(page)
        model = catalog_model(registry, USER_A)
        tree = json.loads(serialized(render_catalog(model)))
        nodes = list(components(tree))
        self.assertIn(page.title, [node['props'].get('children') for node in nodes if node['type'] == 'H3'])
        self.assertTrue(all('dangerouslySetInnerHTML' not in node['props'] for node in nodes))
        button = next(node for node in nodes if node['type'] == 'Button')
        self.assertEqual(button['props']['aria-pressed'], 'false')
        self.assertEqual(button['props']['type'], 'button')
        self.assertIn(page.title, button['props']['aria-label'])
        self.assertEqual([node['props']['href'] for node in nodes if node['type'] == 'A'], ['/safe-text'])


class CatalogTransportTests(AppTestCase):
    def catalog_app(self):
        identities = FixtureIdentity()
        identities.users.update({'admin-b': ADMIN_B, 'user-b': USER_B})
        provider, calls = FixtureReports(), []
        server = create_app(self.settings(), identities, provider, extra_pages=synthetic_pages(calls))
        self.addCleanup(dispose_app, server)
        server.config['TESTING'] = True
        return server, provider, calls

    def render(self, client, query='', category='', view='all', prefs=None, state=None, action='catalog-query.value'):
        key, outputs = callback_output_spec(client, 'catalog-results', 'children')
        return client.post('/_dash-update-component', json={
            'output': key, 'outputs': outputs,
            'inputs': [
                {'id': 'catalog-query', 'property': 'value', 'value': query},
                {'id': 'catalog-category', 'property': 'value', 'value': category},
                {'id': 'catalog-view', 'property': 'value', 'value': view},
                {'id': 'catalog-preferences', 'property': 'data', 'value': prefs},
                {'id': 'catalog-prev', 'property': 'n_clicks', 'value': 1},
                {'id': 'catalog-next', 'property': 'n_clicks', 'value': 1},
            ], 'state': [{'id': 'catalog-page', 'property': 'data', 'value': state}],
            'changedPropIds': [action],
        })

    def test_layout_and_real_callback_only_emit_authorized_metadata_without_data_calls(self):
        server, provider, calls = self.catalog_app()
        for username, hidden_id, hidden_category in (('fixture-user', 'synthetic-40', 'Restricted admin'),
                                                      ('admin-b', 'synthetic-49', 'Restricted organization')):
            client = self.logged_in(server, username)
            layout = self.page(client, '/')
            self.assertEqual(layout.status_code, 200, layout.get_data(as_text=True))
            self.assertIn('Report catalog', layout.get_data(as_text=True))
            self.assertNotIn(hidden_id, layout.get_data(as_text=True))
            self.assertNotIn(hidden_category, layout.get_data(as_text=True))
            self.assertNotIn('Report payload', layout.get_data(as_text=True))
            content = layout.get_json()['response']['_pages_content']['children']
            stores = {node['props']['id']: node['props'] for node in components(content) if node['type'] == 'Store'}
            self.assertEqual(set(stores['catalog-scope']['data']), {'scope', 'allowed_ids'})
            for store in stores.values():
                self.assertEqual(store['storage_type'], 'memory')
            user = USER_A if username == 'fixture-user' else ADMIN_B
            prefs = {'scope': catalog_scope(user), 'favorites': [hidden_id], 'recent': [hidden_id]}
            response = self.render(client, query=hidden_id, prefs=prefs)
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            self.assertEqual(response.get_json()['response']['catalog-summary']['children'], '0 reports')
            self.assertNotIn(hidden_id, response.get_data(as_text=True))
            self.assertNotIn(hidden_category, response.get_data(as_text=True))
        self.assertEqual(provider.rows_calls, 0)
        self.assertEqual(provider.export_calls, 0)
        self.assertEqual(calls, [])

    def test_actual_dispatcher_paginates_fifty_search_results_and_resets_on_filter(self):
        server, _, _ = self.catalog_app()
        client = self.logged_in(server)
        first = self.render(client, query='measure-')
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))
        body = first.get_json()['response']
        self.assertEqual(body['catalog-summary']['children'], '1–12 of 50 reports')
        self.assertEqual(body['catalog-page-label']['children'], 'Page 1 of 5')
        self.assertTrue(body['catalog-prev']['disabled'])
        second = self.render(client, query='measure-', state=body['catalog-page']['data'], action='catalog-next.n_clicks')
        self.assertEqual(second.status_code, 200, second.get_data(as_text=True))
        body = second.get_json()['response']
        self.assertEqual(body['catalog-summary']['children'], '13–24 of 50 reports')
        self.assertFalse(body['catalog-prev']['disabled'])
        filtered = self.render(client, query='measure-49', state=body['catalog-page']['data'])
        body = filtered.get_json()['response']
        self.assertEqual(body['catalog-summary']['children'], '1–1 of 1 reports')
        self.assertEqual(body['catalog-page-label']['children'], 'Page 1 of 1')
        self.assertTrue(body['catalog-next']['disabled'])

    def test_anonymous_server_callback_and_client_only_http_output_fail_closed(self):
        server, provider, calls = self.catalog_app()
        client = server.test_client()
        denied = self.render(client)
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.get_json(), {'error': 'Login required'})
        key, outputs = callback_output_spec(client, 'catalog-preferences', 'data')
        for visitor in (client, self.logged_in(server)):
            response = visitor.post('/_dash-update-component', json={
                'output': key, 'outputs': outputs, 'inputs': [], 'state': [], 'changedPropIds': [],
            })
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.get_json(), {'error': 'Unregistered callback'})
        self.assertEqual(provider.rows_calls, 0)
        self.assertEqual(calls, [])
        declaration = server.extensions['callback_registry'].callbacks[key]
        self.assertEqual(declaration.kind, 'client')
        self.assertEqual(declaration.page_id, 'overview')
        self.assertEqual(declaration.policy, AccessPolicy.public())


class CatalogJavaScriptTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is required for the local preference JavaScript contract')
    def test_ids_only_storage_scope_isolation_click_capture_and_safe_fallback(self):
        source = Path(__file__).resolve().parents[1] / 'assets' / 'catalog.js'
        script = r'''
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const saved = new Map();
let blocked = false;
let listener;
const context = {
  window: {
    dash_clientside: {no_update: {noUpdate: true}, callback_context: {triggered: []}},
    localStorage: {
      getItem(key) { if (blocked) throw Error('blocked'); return saved.get(key) || null; },
      setItem(key, value) { if (blocked) throw Error('blocked'); saved.set(key, value); }
    }
  },
  document: {addEventListener(event, fn, capture) {assert.equal(event, 'click'); assert.equal(capture, true); listener = fn;}}
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const client = context.window.dash_clientside;
const preferences = client.workspace_catalog.preferences;
const a = 'a'.repeat(64), b = 'b'.repeat(64);
const scopeA = {scope: a, allowed_ids: ['report-one', 'report-two']};
function trigger(id, value) {client.callback_context = {triggered: [{prop_id: JSON.stringify({type: 'catalog-favorite', report: id}) + '.n_clicks', value: value}]};}
let current = preferences([], scopeA, null);
assert.equal(current.scope, a);
assert.equal(current.favorites.length, 0);
assert.equal(preferences([], scopeA, current), client.no_update);
trigger('report-one', 1);
current = preferences([1], scopeA, current);
assert.equal(current.favorites[0], 'report-one');
let persisted = JSON.parse(saved.get('workspace.catalog.' + a));
assert.deepEqual(Object.keys(persisted).sort(), ['favorites', 'recent']);
assert.equal(persisted.favorites[0], 'report-one');
trigger('report-one', 0);
assert.equal(preferences([0], scopeA, current), client.no_update);
trigger('report-one', null);
assert.equal(preferences([null], scopeA, current), client.no_update);
trigger('hidden-report', 1);
assert.equal(preferences([1], scopeA, current), client.no_update);
function click(id, scope) { listener({target: {closest() {return {getAttribute(name) { return name === 'data-report-id' ? id : scope; }};}}}); }
click('report-two', a);
assert.equal(JSON.parse(saved.get('workspace.catalog.' + a)).recent[0], 'report-two');
click('hidden-report', a);
click('report-one', b);
assert.deepEqual(JSON.parse(saved.get('workspace.catalog.' + a)).recent, ['report-two']);
client.callback_context = {triggered: []};
let other = preferences([], {scope: b, allowed_ids: ['report-one']}, current);
assert.equal(other.favorites.length, 0);
assert.equal(other.recent.length, 0);
saved.set('workspace.catalog.' + b, JSON.stringify({favorites: ['hidden-report', {}, 'report-one', 'report-one'], recent: ['hidden-report'], rows: ['private']}));
other = preferences([], {scope: b, allowed_ids: ['report-one']}, other);
assert.equal(other.favorites.length, 1);
assert.equal(other.favorites[0], 'report-one');
assert.equal(other.recent.length, 0);
assert.equal(saved.get('workspace.catalog.' + b).includes('private'), false);
saved.set('workspace.catalog.' + b, '{broken');
assert.equal(preferences([], {scope: b, allowed_ids: ['report-one']}, other), client.no_update);
blocked = true;
trigger('report-one', 1);
other = preferences([1], {scope: b, allowed_ids: ['report-one']}, other);
assert.equal(other.favorites.length, 0);
client.callback_context = {triggered: []};
assert.equal(preferences([], {scope: b, allowed_ids: ['report-one']}, other), client.no_update);
assert.equal(preferences([], {scope: 'invalid', allowed_ids: ['report-one']}, other), client.no_update);
blocked = false;
const ids = Array.from({length: 120}, (_, i) => 'report-' + i);
saved.set('workspace.catalog.' + a, JSON.stringify({favorites: ids, recent: ids}));
current = preferences([], {scope: a, allowed_ids: ids}, null);
assert.equal(current.favorites.length, 100);
assert.equal(current.recent.length, 100);
console.log('catalog preference contracts passed');
'''
        result = subprocess.run([shutil.which('node'), '-e', script, str(source)],
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('catalog preference contracts passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
