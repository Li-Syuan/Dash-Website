"""Pinned Dash component contracts and deterministic local-JS theme tests.

Node's VM supplies browser APIs; these are function tests, not visual browser QA.
No package download, live server, company data, or browser network is involved.
"""

import ast
from pathlib import Path
import shutil
import subprocess
import unittest

from dash import ClientsideFunction, Dash, Input, Output, State

from reporting_workspace.registry import AccessPolicy, CallbackRegistry, PageRegistry
from reporting_workspace.theme import (
    PROVIDER_ID, STORE_ID, TOGGLE_ID, register_theme, theme_store, theme_toggle,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'assets' / 'theme.js'
CSS = ROOT / 'assets' / 'theme.css'
NODE = shutil.which('node')


class ThemeComponentTests(unittest.TestCase):
    def test_store_is_fresh_memory_only_and_contains_no_identity(self):
        first, second = theme_store(), theme_store()
        self.assertIsNot(first, second)
        self.assertEqual(first.to_plotly_json()['props'], {
            'id': STORE_ID, 'storage_type': 'memory',
        })

    def test_toggle_is_native_keyboard_button_with_compact_accessible_state(self):
        props = theme_toggle().to_plotly_json()['props']
        self.assertEqual(props['id'], TOGGLE_ID)
        self.assertEqual(props['type'], 'button')
        self.assertEqual(props['n_clicks'], 0)
        self.assertEqual(props['aria-pressed'], 'false')
        self.assertEqual(props['title'], props['aria-label'])
        self.assertEqual(props['aria-label'], 'Switch to dark mode')
        self.assertEqual(props['className'], 'workspace-theme-toggle')

    def test_registry_contract_is_two_presentation_only_client_callbacks(self):
        class Registrar:
            def __init__(self):
                self.calls = []

            def clientside_callback(self, function, *dependencies, **kwargs):
                self.calls.append((function, dependencies, kwargs))

        registrar = Registrar()
        register_theme(registrar)
        self.assertEqual(len(registrar.calls), 2)
        sync, figure = registrar.calls
        self.assertIsInstance(sync[0], ClientsideFunction)
        self.assertEqual(sync[0].namespace, 'workspace_theme')
        self.assertEqual(sync[0].function_name, 'sync')
        self.assertEqual(figure[0].function_name, 'figure')
        self.assertEqual(sync[2], {'callback_id': 'shell.theme', 'policy': AccessPolicy.public()})
        self.assertEqual(figure[2], {'callback_id': 'shell.theme_chart', 'policy': AccessPolicy.public()})
        self.assertEqual([str(dep) for dep in sync[1] if isinstance(dep, Output)], [
            PROVIDER_ID + '.theme', STORE_ID + '.data', TOGGLE_ID + '.children',
            TOGGLE_ID + '.aria-label', TOGGLE_ID + '.aria-pressed', TOGGLE_ID + '.title',
        ])
        self.assertEqual([str(dep) for dep in sync[1] if isinstance(dep, Input)], [TOGGLE_ID + '.n_clicks'])
        self.assertEqual([str(dep) for dep in sync[1] if isinstance(dep, State)], [STORE_ID + '.data', PROVIDER_ID + '.theme'])
        self.assertEqual([str(dep) for dep in figure[1] if isinstance(dep, Input)], [STORE_ID + '.data', 'performance-chart.id'])
        self.assertEqual([str(dep) for dep in figure[1] if isinstance(dep, State)], ['performance-chart.figure'])
        self.assertEqual([str(dep) for dep in figure[1] if isinstance(dep, Output)], ['performance-chart.figure'])

    def test_real_dash_registration_remains_client_only_and_app_local(self):
        first = Dash(__name__)
        second = Dash(__name__)
        registries = [CallbackRegistry(app, PageRegistry(), lambda: None)
                      for app in (first, second)]
        for app, callbacks in zip((first, second), registries):
            register_theme(callbacks)
            callbacks.freeze()
            self.assertEqual(len(app.callback_map), 2)
            self.assertEqual({spec.callback_id for spec in callbacks.callbacks.values()},
                             {'shell.theme', 'shell.theme_chart'})
            for output_key, entry in app.callback_map.items():
                self.assertNotIn('callback', entry)
                self.assertIsNone(callbacks.policy_for(output_key))
            for descriptor in app._callback_list:
                self.assertEqual(descriptor['clientside_function']['namespace'], 'workspace_theme')
        self.assertIsNot(first.callback_map, second.callback_map)
        self.assertIsNot(registries[0].callbacks, registries[1].callbacks)

    def test_python38_syntax_and_theme_style_coverage(self):
        ast.parse((ROOT / 'reporting_workspace' / 'theme.py').read_text(), feature_version=(3, 8))
        css = CSS.read_text()
        self.assertEqual(css.count('{'), css.count('}'))
        for token in ('html[data-theme="light"]', 'html[data-theme="dark"]', '--chart-background',
                      '.workspace-theme-toggle:focus-visible', '.workspace-notification',
                      '#maintenance-table', '.dash-filter', '.form-control', '.form-select',
                      '.modal-content', '.popover', 'tr:nth-child(even)', '.cell--selected',
                      'prefers-reduced-motion', 'prefers-contrast', 'forced-colors', 'flex-wrap: wrap'):
            self.assertIn(token, css)
        for remote in ('@import', 'https://', 'http://', 'url('):
            self.assertNotIn(remote, css)
        script = SCRIPT.read_text()
        for prohibited in ('fetch(', 'XMLHttpRequest', 'MutationObserver', 'Proxy(', 'setInterval', 'setTimeout'):
            self.assertNotIn(prohibited, script)
        self.assertEqual(script.count('localStorage.setItem'), 1)


HARNESS = r'''
const assert = require('assert');
const vm = require('vm');
const sourceCode = require('fs').readFileSync(process.argv[1], 'utf8');
function browser(options = {}) {
    const attrs = {};
    const writes = [];
    const noUpdate = {sentinel: 'no-update'};
    const root = {
        setAttribute: (key, value) => { attrs[key] = value; },
        getAttribute: key => attrs[key]
    };
    const storage = Object.assign({}, options.storage || {});
    const window = {
        localStorage: {
            getItem: key => { if (options.readError) throw Error('disabled'); return storage[key] ?? null; },
            setItem: (key, value) => {
                if (options.writeError) throw Error('disabled');
                writes.push([key, value]); storage[key] = value;
            }
        },
        matchMedia: query => {
            assert.strictEqual(query, '(prefers-color-scheme: dark)');
            if (options.mediaError) throw Error('disabled');
            return {matches: Boolean(options.dark)};
        },
        getComputedStyle: () => ({getPropertyValue: key => (options.tokens || {})[key] || ''}),
        dash_clientside: {no_update: noUpdate, another_namespace: {preserved: true}}
    };
    if (options.noMedia) delete window.matchMedia;
    const context = {window, document: {documentElement: root}, console};
    vm.runInNewContext(sourceCode, context);
    return {api: window.dash_clientside.workspace_theme, attrs, writes, storage, window, noUpdate};
}
function plain(value) { return JSON.parse(JSON.stringify(value)); }
'''


@unittest.skipUnless(NODE, 'Node is not available for deterministic JavaScript function checks')
class ThemeJavaScriptTests(unittest.TestCase):
    def js(self, assertions):
        result = subprocess.run([NODE, '-e', HARNESS + assertions, str(SCRIPT)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_initial_mode_follows_os_without_persisting_it(self):
        self.js(r'''
for (const dark of [false, true]) {
    const b = browser({dark});
    const expected = dark ? 'dark' : 'light';
    assert.strictEqual(b.attrs['data-theme'], expected);
    assert.deepStrictEqual(b.writes, []);
    const out = b.api.sync(0, null, {primaryColor: 'blue', components: {Notification: {}}});
    assert.strictEqual(out[0].colorScheme, expected);
    assert.strictEqual(out[0].primaryColor, 'blue');
    assert.strictEqual(out[1].explicit, false);
    assert.strictEqual(out[1].clicks, 0);
    assert.strictEqual(out[4], dark ? 'true' : 'false');
    assert.deepStrictEqual(b.writes, []);
    assert.strictEqual(b.window.dash_clientside.another_namespace.preserved, true);
}
''')

    def test_only_valid_saved_modes_override_system(self):
        self.js(r'''
for (const saved of ['light', 'dark']) {
    const b = browser({dark: saved === 'light', storage: {'workspace.theme': saved}});
    assert.strictEqual(b.attrs['data-theme'], saved);
    const out = b.api.sync(0, null, {});
    assert.strictEqual(out[0].colorScheme, saved);
    assert.strictEqual(out[1].explicit, true);
    assert.deepStrictEqual(b.writes, []);
}
for (const saved of ['', 'DARK', 'system', '{}', '<script>', null]) {
    const b = browser({dark: true, storage: {'workspace.theme': saved}});
    assert.strictEqual(b.attrs['data-theme'], 'dark');
    assert.strictEqual(b.api.sync(0, null, {})[1].explicit, false);
    assert.deepStrictEqual(b.writes, []);
}
''')

    def test_toggle_repeated_calls_rapid_clicks_and_reload(self):
        self.js(r'''
const b = browser();
const baseTheme = {colorScheme: 'light', primaryColor: 'blue', respectReducedMotion: true,
                   components: {Notification: {defaultProps: {closeButtonProps: {'aria-label': 'Close'}}}}};
const original = JSON.stringify(baseTheme);
let out = b.api.sync(0, null, baseTheme);
out = b.api.sync(1, out[1], out[0]);
assert.strictEqual(out[0].colorScheme, 'dark');
assert.strictEqual(out[1].explicit, true);
assert.strictEqual(out[2], '\u2600');
assert.strictEqual(out[3], 'Switch to light mode');
assert.strictEqual(out[4], 'true');
assert.strictEqual(out[5], out[3]);
assert.deepStrictEqual(b.writes, [['workspace.theme', 'dark']]);
assert.strictEqual(JSON.stringify(baseTheme), original);
out = b.api.sync(1, out[1], out[0]);
assert.strictEqual(out[0].colorScheme, 'dark');
assert.strictEqual(b.writes.length, 1);
out = b.api.sync(3, out[1], out[0]);
assert.strictEqual(out[0].colorScheme, 'dark');
out = b.api.sync(4, out[1], out[0]);
assert.strictEqual(out[0].colorScheme, 'light');
assert.strictEqual(out[4], 'false');
assert.strictEqual(b.attrs['data-theme'], 'light');
const reload = browser({dark: true, storage: b.storage});
assert.strictEqual(reload.api.sync(0, null, {})[0].colorScheme, 'light');
assert.deepStrictEqual(reload.writes, []);
''')

    def test_unavailable_storage_and_os_apis_do_not_break_controls(self):
        self.js(r'''
for (const options of [{readError: true, writeError: true, dark: true},
                       {readError: true, writeError: true, noMedia: true},
                       {mediaError: true}]) {
    const b = browser(options);
    let out = b.api.sync(0, null, null);
    const initial = out[0].colorScheme;
    out = b.api.sync(1, out[1], out[0]);
    assert.notStrictEqual(out[0].colorScheme, initial);
    assert.strictEqual(b.attrs['data-theme'], out[0].colorScheme);
    assert.strictEqual(out[1].explicit, true);
}
''')

    def test_malformed_memory_state_is_ignored_and_never_persisted(self):
        self.js(r'''
for (const state of [null, [], 'dark', {}, {colorScheme: 'neon', explicit: true, clicks: 0},
                     {colorScheme: 'dark', explicit: 'yes', clicks: 0},
                     {colorScheme: 'dark', explicit: true, clicks: -1},
                     {colorScheme: 'dark', explicit: true, clicks: 0.5},
                     {colorScheme: 'dark', explicit: true, clicks: '0'}]) {
    const b = browser();
    const out = b.api.sync(0, state, null);
    assert.strictEqual(out[0].colorScheme, 'light');
    assert.deepStrictEqual(Object.keys(out[1]).sort(), ['clicks', 'colorScheme', 'explicit']);
    assert.deepStrictEqual(b.writes, []);
}
for (const clicks of [-1, 1.5, '1', null, Infinity, NaN]) {
    const b = browser();
    assert.strictEqual(b.api.sync(clicks, null, {})[0].colorScheme, 'light');
    assert.deepStrictEqual(b.writes, []);
}
''')

    def test_chart_preserves_traces_ranges_templates_and_original_figure(self):
        self.js(r'''
for (const colorScheme of ['light', 'dark']) {
    const b = browser({dark: colorScheme === 'dark'});
    const source = {data: [{type: 'bar', x: ['Jan', 'Feb'], y: [1, 2], marker: {color: '#315fe7'}}],
      layout: {height: 270, barmode: 'group', margin: {l: 50}, uirevision: 'report',
        font: {family: 'Arial'}, legend: {orientation: 'h', y: 1.15},
        xaxis: {type: 'category', showgrid: false, categoryarray: ['Jan', 'Feb']},
        yaxis: {title: {text: 'Report units', font: {size: 14}}, range: [0, 10]},
        template: {data: {bar: [{marker: {opacity: 0.8}}]}, layout: {hovermode: 'closest'}}},
      frames: []};
    const original = JSON.stringify(source);
    const themed = b.api.figure({colorScheme}, 'performance-chart', source);
    assert.notStrictEqual(themed, source);
    assert.strictEqual(themed.data, source.data);
    assert.strictEqual(themed.frames, source.frames);
    assert.strictEqual(JSON.stringify(source), original);
    assert.strictEqual(themed.layout.paper_bgcolor, colorScheme === 'dark' ? '#18283f' : '#ffffff');
    assert.strictEqual(themed.layout.plot_bgcolor, themed.layout.paper_bgcolor);
    assert.strictEqual(themed.layout.height, 270);
    assert.strictEqual(themed.layout.uirevision, 'report');
    assert.strictEqual(themed.layout.font.family, 'Arial');
    assert.strictEqual(themed.layout.legend.orientation, 'h');
    assert.strictEqual(themed.layout.xaxis.type, 'category');
    assert.strictEqual(themed.layout.xaxis.showgrid, false);
    assert.deepStrictEqual(themed.layout.yaxis.range, [0, 10]);
    assert.strictEqual(themed.layout.yaxis.title.text, 'Report units');
    assert.strictEqual(themed.layout.yaxis.title.font.size, 14);
    assert.strictEqual(themed.layout.template.layout.hovermode, 'closest');
    assert.strictEqual(themed.layout.template.data, source.layout.template.data);
    assert.strictEqual(themed.layout.template.layout.paper_bgcolor, themed.layout.paper_bgcolor);
}
''')

    def test_chart_dynamic_mount_empty_figure_tokens_and_missing_target(self):
        self.js(r'''
const b = browser({dark: true, tokens: {'--chart-background': '  #123456 ', '--chart-text': '#fedcba'}});
assert.strictEqual(b.api.figure(null, 'performance-chart', {data: []}).layout.paper_bgcolor, '#123456');
assert.strictEqual(b.api.figure({colorScheme: 'dark'}, 'performance-chart', {data: []}).layout.font.color, '#fedcba');
assert.strictEqual(b.api.figure({colorScheme: 'dark'}, 'other-chart', {data: []}), b.noUpdate);
assert.strictEqual(b.api.figure({colorScheme: 'dark'}, 'performance-chart', null), b.noUpdate);
assert.strictEqual(b.api.figure({colorScheme: 'dark'}, 'performance-chart', []), b.noUpdate);
// A remounted route can receive the Store before root synchronization.
assert.strictEqual(b.api.figure({colorScheme: 'light'}, 'performance-chart', {data: []}).layout.paper_bgcolor, '#ffffff');
''')


if __name__ == '__main__':
    unittest.main()
