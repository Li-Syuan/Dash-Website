"""Deterministic local assistant DOM/event checks, not visual browser certification.

Node's VM supplies the small DOM surface used by the local script. Tests use no
browser packages, network, model backend, company data or live Dash server.
"""

from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'assets' / 'assistant.js'
NODE = shutil.which('node')


HARNESS = r'''
const assert = require('assert');
const vm = require('vm');
const source = require('fs').readFileSync(process.argv[1], 'utf8');
function browser(options = {}) {
    const observers = [];
    const documentListeners = {};
    const windowListeners = {};
    const mediaListeners = [];
    const focusLog = [];
    let document;
    function changed(target, type, attributeName) {
        observers.forEach(observer => {
            const entry = observer.entry;
            if (!entry || !(entry.target === target || (entry.options.subtree && entry.target.contains(target)))) return;
            if (type === 'childList' && !entry.options.childList) return;
            if (type === 'attributes' && (!entry.options.attributes ||
                !entry.options.attributeFilter.includes(attributeName))) return;
            observer.records.push({target, type, attributeName});
        });
    }
    function descendants(element) {
        return element.children.flatMap(child => [child, ...descendants(child)]);
    }
    function event(listeners, name, target, details = {}) {
        const value = Object.assign({target, defaultPrevented: false, stopped: false,
            preventDefault() { this.defaultPrevented = true; },
            stopPropagation() { this.stopped = true; }}, details);
        (listeners[name] || []).slice().forEach(listener => listener(value));
        return value;
    }
    class Element {
        constructor(tag, id, attributes = {}) {
            this.tagName = tag.toUpperCase();
            this.id = id || '';
            this.attributes = Object.assign({}, attributes);
            this.children = [];
            this.parentElement = null;
            this.style = {display: '', visibility: ''};
            this.disabled = false;
            this.clicks = 0;
        }
        get isConnected() { return Boolean(document && document.body.contains(this)); }
        get tabIndex() {
            if (this.attributes.tabindex !== undefined) return Number(this.attributes.tabindex);
            if (/^(BUTTON|INPUT|SELECT|TEXTAREA|IFRAME|OBJECT|EMBED)$/.test(this.tagName) ||
                (this.tagName === 'A' && this.getAttribute('href') !== null) ||
                this.getAttribute('contenteditable') === 'true') return 0;
            return -1;
        }
        getAttribute(name) { return Object.hasOwn(this.attributes, name) ? this.attributes[name] : null; }
        setAttribute(name, value) {
            this.attributes[name] = String(value);
            changed(this, 'attributes', name);
        }
        removeAttribute(name) {
            if (!Object.hasOwn(this.attributes, name)) return;
            delete this.attributes[name];
            changed(this, 'attributes', name);
        }
        append(...elements) {
            elements.forEach(element => {
                if (element.parentElement) element.remove();
                this.children.push(element);
                element.parentElement = this;
            });
            changed(this, 'childList');
        }
        remove() {
            const parent = this.parentElement;
            if (!parent) return;
            parent.children = parent.children.filter(child => child !== this);
            this.parentElement = null;
            if (this.contains(document.activeElement)) document.activeElement = document.body;
            changed(parent, 'childList');
        }
        contains(other) { return other === this || this.children.some(child => child.contains(other)); }
        closest(selector) {
            const selectors = selector.split(',').map(value => value.trim());
            for (let current = this; current; current = current.parentElement) {
                if (selectors.some(value => value === '[hidden]' ? current.getAttribute('hidden') !== null :
                    value === '[inert]' ? current.getAttribute('inert') !== null :
                    value === '[aria-hidden="true"]' && current.getAttribute('aria-hidden') === 'true')) return current;
            }
            return null;
        }
        matches(selector) {
            assert.strictEqual(selector, ':disabled');
            for (let current = this; current; current = current.parentElement) {
                if (current.disabled && (current === this || current.tagName === 'FIELDSET')) return true;
            }
            return false;
        }
        getClientRects() {
            if (!this.isConnected || (this.tagName === 'INPUT' && this.getAttribute('type') === 'hidden')) return [];
            for (let current = this; current; current = current.parentElement) {
                if (current.style.display === 'none' || current.getAttribute('hidden') !== null) return [];
            }
            return [{}];
        }
        querySelectorAll() {
            return descendants(this).filter(element =>
                /^(BUTTON|INPUT|SELECT|TEXTAREA|IFRAME|OBJECT|EMBED)$/.test(element.tagName) ||
                /^(A|AREA)$/.test(element.tagName) && element.getAttribute('href') !== null ||
                element.getAttribute('contenteditable') === 'true' || element.getAttribute('tabindex') !== null);
        }
        focus() {
            if (!this.isConnected || this.disabled || !this.getClientRects().length) return;
            if (options.nativeInert && this.closest('[inert]')) return;
            if (this.tabIndex < 0 && this.getAttribute('tabindex') === null && this !== document.body) return;
            document.activeElement = this;
            focusLog.push(this);
            event(documentListeners, 'focusin', this);
        }
        click() {
            if (this.disabled) return;
            const click = event(documentListeners, 'click', this);
            if (!click.defaultPrevented && !click.stopped) {
                this.clicks += 1;
                if (this.onClick) this.onClick();
            }
        }
    }
    const body = new Element('body');
    document = {
        body, activeElement: body, readyState: options.loading ? 'loading' : 'complete',
        addEventListener(name, fn) { (documentListeners[name] ||= []).push(fn); },
        getElementById(id) { return [body, ...descendants(body)].find(element => element.id === id) || null; }
    };
    const window = {
        innerWidth: options.width ?? 390,
        addEventListener(name, fn) { (windowListeners[name] ||= []).push(fn); },
        getComputedStyle(element) {
            for (let current = element; current; current = current.parentElement) {
                if (current.style.visibility) return {visibility: current.style.visibility};
            }
            return {visibility: 'visible'};
        },
        matchMedia(query) {
            assert.strictEqual(query, '(max-width: 650px)');
            const media = {get matches() { return window.innerWidth <= 650; }};
            if (options.legacyMedia) media.addListener = fn => mediaListeners.push(fn);
            else media.addEventListener = (name, fn) => {
                assert.strictEqual(name, 'change'); mediaListeners.push(fn);
            };
            return media;
        }
    };
    if (options.noMedia) delete window.matchMedia;
    class MutationObserver {
        constructor(callback) { this.callback = callback; this.entry = null; this.records = []; observers.push(this); }
        observe(target, opts) { this.entry = {target, options: opts}; }
    }
    function flush() {
        let rounds = 0;
        while (observers.some(observer => observer.records.length)) {
            assert.ok(++rounds < 30, 'Mutation observer must settle without a self-triggering loop');
            observers.forEach(observer => {
                if (!observer.records.length) return;
                const records = observer.records.splice(0);
                observer.callback(records);
            });
        }
    }
    function mount(open = false) {
        const root = new Element('div', 'react-entry-point');
        const wrapper = new Element('div', 'provider-wrapper');
        const header = new Element('header');
        const opener = new Element('button', 'btn_sidebar', {'aria-expanded': String(open)});
        const main = new Element('main', 'page-content');
        const link = new Element('a', 'report-link', {href: '/page1'});
        const panel = new Element('aside', 'sidebar', {'aria-hidden': String(!open), 'aria-label': 'Assistant panel'});
        panel.style.display = open ? 'block' : 'none';
        const close = new Element('button', 'assistant-close');
        const input = new Element('textarea', 'assistant-message');
        input.disabled = true;
        const send = new Element('button', 'assistant-send');
        send.disabled = true;
        const footer = new Element('footer');
        header.append(opener);
        main.append(link);
        panel.append(close, input, send);
        wrapper.append(header, main, panel, footer);
        root.append(wrapper);
        body.append(root);
        return {root, wrapper, header, opener, main, link, panel, close, input, send, footer};
    }
    let shell = options.lateMount ? null : mount(Boolean(options.open));
    vm.runInNewContext(source, {document, window, MutationObserver});
    flush();
    return {document, window, body, Element, flush, mount, shell, focusLog,
        key(key, shiftKey = false) { return event(documentListeners, 'keydown', document.activeElement, {key, shiftKey}); },
        pointer(target) { return event(documentListeners, 'pointerdown', target); },
        ready() { document.readyState = 'complete'; event(documentListeners, 'DOMContentLoaded', document); flush(); },
        page(name) { event(windowListeners, name, window); flush(); },
        resize(width, mediaOnly = false) {
            window.innerWidth = width;
            if (mediaOnly) mediaListeners.forEach(fn => fn());
            else event(windowListeners, 'resize', window);
            flush();
        },
        setOpen(open, target = shell) {
            target.panel.style.display = open ? 'block' : 'none';
            changed(target.panel, 'attributes', 'style');
            target.panel.setAttribute('aria-hidden', String(!open));
            target.opener.setAttribute('aria-expanded', String(open));
            flush();
        }
    };
}
function excluded(element) {
    assert.strictEqual(element.getAttribute('inert'), '');
    assert.strictEqual(element.getAttribute('aria-hidden'), 'true');
}
function restored(element) {
    assert.strictEqual(element.getAttribute('inert'), null);
    assert.strictEqual(element.getAttribute('aria-hidden'), null);
}
'''


class AssistantAssetTests(unittest.TestCase):
    def test_accessibility_asset_is_local_and_preserves_callback_boundary(self):
        source = SCRIPT.read_text()
        for prohibited in ('fetch(', 'XMLHttpRequest', 'WebSocket', 'localStorage',
                           'setInterval', 'setTimeout', 'dash_clientside'):
            self.assertNotIn(prohibited, source)
        self.assertIn('close.click()', source)
        self.assertNotIn('opener.click()', source)
        self.assertIn('(max-width: 650px)', source)
        self.assertIn('@media(max-width:650px)', (ROOT / 'assets' / 'z_layout.css').read_text())


@unittest.skipUnless(NODE, 'Node is not available for deterministic JavaScript DOM checks')
class AssistantJavaScriptTests(unittest.TestCase):
    def js(self, assertions):
        result = subprocess.run([NODE, '-e', HARNESS + assertions, str(SCRIPT)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_closed_shell_has_no_modal_or_focus_side_effects(self):
        self.js(r'''
const b = browser();
const s = b.shell;
assert.strictEqual(s.panel.getAttribute('role'), null);
assert.strictEqual(s.panel.getAttribute('aria-modal'), null);
[s.header, s.main, s.footer, s.root].forEach(restored);
assert.strictEqual(b.focusLog.length, 0);
assert.strictEqual(b.key('Tab').defaultPrevented, false);
assert.strictEqual(b.key('Escape').defaultPrevented, false);
assert.strictEqual(s.close.clicks, 0);
''')

    def test_initial_mount_and_late_loading_shell_both_initialize(self):
        self.js(r'''
for (const options of [{open: true}, {open: true, loading: true}, {lateMount: true},
                       {lateMount: true, loading: true}]) {
    const b = browser(options);
    const s = b.shell || b.mount(true);
    if (options.loading) b.ready();
    else b.flush();
    assert.strictEqual(s.panel.getAttribute('role'), 'dialog');
    assert.strictEqual(s.panel.getAttribute('aria-modal'), 'true');
    assert.strictEqual(b.document.activeElement, s.close);
    [s.header, s.main, s.footer].forEach(excluded);
    restored(s.root);
    restored(s.wrapper);
}
''')

    def test_mobile_modal_wraps_the_only_enabled_control_in_both_directions(self):
        self.js(r'''
for (const width of [320, 390, 650]) {
    const b = browser({width});
    const s = b.shell;
    s.opener.focus();
    b.setOpen(true);
    for (const shift of [false, true, false, false]) {
        assert.strictEqual(b.key('Tab', shift).defaultPrevented, true);
        assert.strictEqual(b.document.activeElement, s.close);
    }
    assert.strictEqual(s.input.disabled, true);
    assert.strictEqual(s.send.disabled, true);
    assert.strictEqual(s.panel.getAttribute('aria-modal'), 'true');
}
''')

    def test_focus_order_filters_hidden_disabled_and_negative_controls(self):
        self.js(r'''
const b = browser({open: true});
const s = b.shell;
const later = new b.Element('button', 'later');
const positive = new b.Element('button', 'positive', {tabindex: '2'});
const hidden = new b.Element('button', 'hidden', {hidden: ''});
const disabled = new b.Element('button', 'disabled'); disabled.disabled = true;
const negative = new b.Element('button', 'negative', {tabindex: '-1'});
const invisible = new b.Element('button', 'invisible'); invisible.style.visibility = 'hidden';
const offscreen = new b.Element('button', 'display-none'); offscreen.style.display = 'none';
const fieldset = new b.Element('fieldset'); fieldset.disabled = true;
fieldset.append(new b.Element('button', 'fieldset-control'));
const ariaHidden = new b.Element('div', '', {'aria-hidden': 'true'});
ariaHidden.append(new b.Element('button', 'aria-hidden-control'));
s.panel.append(later, hidden, disabled, negative, invisible, offscreen, fieldset, ariaHidden, positive);
b.flush();
s.close.focus();
assert.strictEqual(b.key('Tab').defaultPrevented, false, 'Interior forward Tab remains native');
assert.strictEqual(b.key('Tab', true).defaultPrevented, false, 'Positive tabindex precedes Close');
positive.focus();
assert.strictEqual(b.key('Tab', true).defaultPrevented, true);
assert.strictEqual(b.document.activeElement, later);
assert.strictEqual(b.key('Tab').defaultPrevented, true);
assert.strictEqual(b.document.activeElement, positive);
''')

    def test_empty_or_temporarily_disabled_drawer_uses_panel_focus(self):
        self.js(r'''
const b = browser({open: true});
const s = b.shell;
s.close.remove();
b.flush();
assert.strictEqual(b.document.activeElement, s.panel);
for (const shift of [false, true]) {
    assert.strictEqual(b.key('Tab', shift).defaultPrevented, true);
    assert.strictEqual(b.document.activeElement, s.panel);
}
s.panel.append(s.close);
b.flush();
assert.strictEqual(b.key('Tab').defaultPrevented, true);
assert.strictEqual(b.document.activeElement, s.close);
b.setOpen(false);
assert.strictEqual(s.panel.getAttribute('tabindex'), null);
assert.strictEqual(b.document.activeElement, s.opener);
''')

    def test_background_focus_pointer_and_click_are_guarded_without_native_inert(self):
        self.js(r'''
const b = browser({open: true, nativeInert: false});
const s = b.shell;
s.link.focus();
assert.strictEqual(b.document.activeElement, s.close);
const pointer = b.pointer(s.link);
assert.strictEqual(pointer.defaultPrevented, true);
assert.strictEqual(pointer.stopped, true);
s.link.click();
assert.strictEqual(s.link.clicks, 0);
s.close.click();
assert.strictEqual(s.close.clicks, 1);
assert.strictEqual(b.pointer(s.close).defaultPrevented, false);
''')

    def test_escape_uses_only_idempotent_close_and_focus_returns_after_close(self):
        self.js(r'''
const b = browser();
const s = b.shell;
s.opener.focus();
b.setOpen(true);
for (let count = 1; count <= 3; count += 1) {
    assert.strictEqual(b.key('Escape').defaultPrevented, true);
    assert.strictEqual(s.close.clicks, count);
    assert.strictEqual(s.opener.clicks, 0);
}
b.setOpen(false);
assert.strictEqual(b.document.activeElement, s.opener);
[s.header, s.main, s.footer].forEach(restored);
assert.strictEqual(s.panel.getAttribute('role'), null);
assert.strictEqual(s.panel.getAttribute('aria-modal'), null);
const focused = b.focusLog.length;
b.setOpen(false);
b.key('Escape');
assert.strictEqual(s.close.clicks, 3);
assert.strictEqual(b.focusLog.length, focused, 'Repeated close does not refocus');
b.setOpen(true);
assert.strictEqual(b.document.activeElement, s.close);
''')

    def test_desktop_stays_nonmodal_and_does_not_steal_newer_focus(self):
        self.js(r'''
for (const width of [651, 1280]) {
    const b = browser({width, open: true});
    const s = b.shell;
    assert.strictEqual(b.document.activeElement, s.close);
    assert.strictEqual(s.panel.getAttribute('role'), null);
    assert.strictEqual(s.panel.getAttribute('aria-modal'), null);
    [s.header, s.main, s.footer].forEach(restored);
    assert.strictEqual(b.key('Tab').defaultPrevented, false);
    assert.strictEqual(b.key('Tab', true).defaultPrevented, false);
    s.link.focus();
    s.link.click();
    assert.strictEqual(b.document.activeElement, s.link);
    assert.strictEqual(s.link.clicks, 1);
    assert.strictEqual(b.pointer(s.link).defaultPrevented, false);
    b.setOpen(false);
    assert.strictEqual(b.document.activeElement, s.link);
}
''')

    def test_resize_and_media_changes_apply_and_release_modal_without_closing(self):
        self.js(r'''
for (const options of [{}, {legacyMedia: true}, {noMedia: true}]) {
    const b = browser(Object.assign({width: 1200, open: true}, options));
    const s = b.shell;
    s.link.focus();
    b.resize(650, !options.noMedia);
    assert.strictEqual(b.document.activeElement, s.close);
    assert.strictEqual(s.panel.getAttribute('aria-modal'), 'true');
    excluded(s.main);
    b.resize(651);
    assert.strictEqual(s.panel.getAttribute('aria-modal'), null);
    restored(s.main);
    assert.strictEqual(b.document.activeElement, s.close);
    assert.strictEqual(b.key('Tab').defaultPrevented, false);
    assert.strictEqual(s.close.clicks, 0);
    assert.strictEqual(s.opener.getAttribute('aria-expanded'), 'true');
}
''')

    def test_original_background_and_panel_attributes_restore_exactly(self):
        self.js(r'''
const b = browser();
const s = b.shell;
s.header.setAttribute('aria-hidden', 'false');
s.main.setAttribute('inert', 'already-inert');
s.main.setAttribute('aria-hidden', 'true');
s.panel.setAttribute('role', 'complementary');
s.panel.setAttribute('aria-modal', 'false');
s.panel.setAttribute('tabindex', '0');
b.setOpen(true);
excluded(s.header);
excluded(s.main);
b.setOpen(false);
assert.strictEqual(s.header.getAttribute('aria-hidden'), 'false');
assert.strictEqual(s.main.getAttribute('inert'), 'already-inert');
assert.strictEqual(s.main.getAttribute('aria-hidden'), 'true');
assert.strictEqual(s.panel.getAttribute('role'), 'complementary');
assert.strictEqual(s.panel.getAttribute('aria-modal'), 'false');
assert.strictEqual(s.panel.getAttribute('tabindex'), '0');
''')

    def test_new_portals_are_excluded_and_newer_external_attributes_are_preserved(self):
        self.js(r'''
const b = browser({open: true});
const s = b.shell;
const portal = new b.Element('section', 'new-portal');
b.body.append(portal);
b.flush();
excluded(portal);
s.footer.setAttribute('aria-hidden', 'false');
s.footer.setAttribute('inert', 'new-owner');
b.flush();
portal.remove();
b.flush();
restored(portal);
b.setOpen(false);
assert.strictEqual(s.footer.getAttribute('aria-hidden'), 'false');
assert.strictEqual(s.footer.getAttribute('inert'), 'new-owner');
''')

    def test_shell_removal_and_remount_clear_inert_without_focusing_detached_opener(self):
        self.js(r'''
const b = browser({open: true});
const old = b.shell;
const portal = new b.Element('section'); b.body.append(portal); b.flush();
const lastFocus = b.focusLog.length;
old.root.remove();
b.flush();
[old.header, old.main, old.footer, portal].forEach(restored);
assert.strictEqual(old.panel.getAttribute('aria-modal'), null);
assert.strictEqual(b.focusLog.length, lastFocus);
assert.strictEqual(b.key('Tab').defaultPrevented, false);
const fresh = b.mount(true);
b.flush();
assert.strictEqual(b.document.activeElement, fresh.close);
assert.strictEqual(fresh.panel.getAttribute('aria-modal'), 'true');
b.setOpen(false, fresh);
assert.strictEqual(b.document.activeElement, fresh.opener);
assert.strictEqual(old.opener.clicks, 0);
''')

    def test_panel_replacement_and_new_route_content_keep_current_background_scope(self):
        self.js(r'''
const b = browser({open: true});
const s = b.shell;
const previous = s.panel;
previous.remove();
const replacement = new b.Element('aside', 'sidebar', {'aria-hidden': 'false'});
const close = new b.Element('button', 'assistant-close');
replacement.append(close);
s.wrapper.append(replacement);
b.flush();
assert.strictEqual(previous.getAttribute('aria-modal'), null);
assert.strictEqual(replacement.getAttribute('aria-modal'), 'true');
assert.strictEqual(b.document.activeElement, close);
const oldMain = s.main;
oldMain.remove();
const newMain = new b.Element('main', 'page-content');
s.wrapper.append(newMain);
b.flush();
restored(oldMain);
excluded(newMain);
assert.strictEqual(b.document.activeElement, close);
''')

    def test_pagehide_releases_and_bfcache_pageshow_reestablishes_current_state(self):
        self.js(r'''
const b = browser({open: true});
const s = b.shell;
b.page('pagehide');
[s.header, s.main, s.footer].forEach(restored);
assert.strictEqual(s.panel.getAttribute('aria-modal'), null);
assert.strictEqual(b.key('Tab').defaultPrevented, false);
b.page('pageshow');
assert.strictEqual(s.panel.getAttribute('aria-modal'), 'true');
assert.strictEqual(b.document.activeElement, s.close);
excluded(s.main);
''')

    def test_async_dash_open_waits_for_visible_and_aria_enabled_panel(self):
        self.js(r'''
const b = browser();
const s = b.shell;
s.opener.focus();
s.opener.setAttribute('aria-expanded', 'true');
b.flush();
assert.strictEqual(s.panel.getAttribute('aria-modal'), null);
assert.strictEqual(b.document.activeElement, s.opener);
s.panel.style.display = 'block';
s.panel.setAttribute('aria-hidden', 'false');
b.flush();
assert.strictEqual(s.panel.getAttribute('aria-modal'), 'true');
assert.strictEqual(b.document.activeElement, s.close);
s.panel.setAttribute('aria-hidden', 'true');
b.flush();
assert.strictEqual(s.panel.getAttribute('aria-modal'), null);
assert.strictEqual(b.document.activeElement, s.opener);
restored(s.main);
''')


if __name__ == '__main__':
    unittest.main()
