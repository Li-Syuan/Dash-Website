/* Local drawer accessibility only. No assistant backend or outbound transport. */
(function () {
  'use strict';

  var opener = null;
  var panel = null;
  var open = false;
  var modal = false;
  var modalAttributes = null;
  var background = [];
  var syncing = false;
  var suspended = false;
  var media = window.matchMedia ? window.matchMedia('(max-width: 650px)') : null;
  var focusSelector = 'a[href], area[href], button, input, select, textarea, iframe, ' +
    'object, embed, [contenteditable="true"], [tabindex]';

  function visible(element) {
    if (!element || !element.isConnected || !element.getClientRects().length ||
        element.closest('[hidden], [inert], [aria-hidden="true"]')) return false;
    var visibility = window.getComputedStyle(element).visibility;
    return visibility !== 'hidden' && visibility !== 'collapse';
  }

  function focusable(element) {
    return visible(element) && !element.disabled && !element.matches(':disabled') && element.tabIndex >= 0;
  }

  function controls() {
    return Array.prototype.slice.call(panel.querySelectorAll(focusSelector)).filter(focusable)
      .sort(function (a, b) {
        // Positive tabindex values precede the ordinary DOM-order tab sequence.
        return (a.tabIndex || Infinity) - (b.tabIndex || Infinity);
      });
  }

  function focus(element) {
    if (visible(element)) element.focus({preventScroll: true});
  }

  function focusInside() {
    var close = document.getElementById('assistant-close');
    var first = close && panel.contains(close) && focusable(close) ? close : controls()[0];
    if (first) focus(first);
    else if (modal) focus(panel); // A temporarily empty/disabled drawer still contains focus.
  }

  function restoreAttribute(element, name, original, applied) {
    // Do not overwrite a newer change made by another component.
    if (element.getAttribute(name) !== applied) return;
    if (original === null) element.removeAttribute(name);
    else element.setAttribute(name, original);
  }

  function restoreBackground(entry) {
    restoreAttribute(entry.element, 'inert', entry.inert, '');
    restoreAttribute(entry.element, 'aria-hidden', entry.hidden, 'true');
  }

  function releaseModal() {
    modal = false; // Release the focus guard before restoring the opener's branch.
    background.forEach(restoreBackground);
    background = [];
    if (modalAttributes) {
      restoreAttribute(panel, 'role', modalAttributes.role, 'dialog');
      restoreAttribute(panel, 'aria-modal', modalAttributes.modal, 'true');
      restoreAttribute(panel, 'tabindex', modalAttributes.tabindex, '-1');
      modalAttributes = null;
    }
  }

  function excludeBackground() {
    var siblings = [];
    // Exclude sibling branches all the way to body, including portal siblings,
    // without ever hiding/inerting an ancestor of the drawer itself.
    for (var branch = panel; branch && branch !== document.body; branch = branch.parentElement) {
      if (!branch.parentElement) break;
      Array.prototype.forEach.call(branch.parentElement.children, function (sibling) {
        if (sibling !== branch && !/^(SCRIPT|STYLE|LINK)$/.test(sibling.tagName)) siblings.push(sibling);
      });
    }
    background = background.filter(function (entry) {
      if (siblings.indexOf(entry.element) !== -1) return true;
      restoreBackground(entry);
      return false;
    });
    siblings.forEach(function (element) {
      if (background.some(function (entry) { return entry.element === element; })) return;
      background.push({element: element, inert: element.getAttribute('inert'), hidden: element.getAttribute('aria-hidden')});
      element.setAttribute('inert', '');
      element.setAttribute('aria-hidden', 'true');
    });
  }

  function sync() {
    if (syncing || suspended || !document.body) return;
    syncing = true;
    try {
      var nextOpener = document.getElementById('btn_sidebar');
      var nextPanel = document.getElementById('sidebar');
      if (opener !== nextOpener || panel !== nextPanel) {
        releaseModal();
        opener = nextOpener;
        panel = nextPanel;
        open = false; // Never restore focus to an opener from a removed shell.
      }
      var nextOpen = Boolean(opener && panel && opener.getAttribute('aria-expanded') === 'true' && visible(panel));
      if (!nextOpen) {
        var wasOpen = open;
        open = false;
        releaseModal();
        // Closing must not steal focus from a newer route or a desktop control.
        if (wasOpen && (panel.contains(document.activeElement) || document.activeElement === document.body)) focus(opener);
        return;
      }
      var justOpened = !open;
      open = true;
      if (media ? media.matches : window.innerWidth <= 650) {
        if (!modal) {
          modalAttributes = {role: panel.getAttribute('role'), modal: panel.getAttribute('aria-modal'),
            tabindex: panel.getAttribute('tabindex')};
          modal = true;
          panel.setAttribute('role', 'dialog');
          panel.setAttribute('aria-modal', 'true');
          panel.setAttribute('tabindex', '-1');
        }
        // Move focus before hiding the branch containing the opener from AT.
        if (justOpened || !panel.contains(document.activeElement) || !visible(document.activeElement)) focusInside();
        excludeBackground();
      } else {
        releaseModal();
        if (justOpened) focusInside();
      }
    } finally {
      syncing = false;
    }
  }

  document.addEventListener('keydown', function (event) {
    sync();
    if (!open) return;
    if (event.key === 'Escape') {
      var close = document.getElementById('assistant-close');
      if (close && panel.contains(close)) {
        event.preventDefault();
        close.click(); // Dedicated idempotent server close callback, never a toggle.
      }
    } else if (modal && event.key === 'Tab') {
      var items = controls();
      var index = items.indexOf(document.activeElement);
      if (!items.length || index === -1 || (!event.shiftKey && index === items.length - 1) ||
          (event.shiftKey && index === 0)) {
        event.preventDefault();
        focus(items.length ? items[event.shiftKey ? items.length - 1 : 0] : panel);
      }
    }
  }, true);

  document.addEventListener('focusin', function (event) {
    if (!syncing && modal && panel.isConnected && !panel.contains(event.target)) focusInside();
  }, true);

  // Native inert handles background interaction; these guards also protect
  // browsers without inert support, without adding a dependency/polyfill.
  function guardBackground(event) {
    if (modal && panel.isConnected && !panel.contains(event.target)) {
      event.preventDefault();
      event.stopPropagation();
      focusInside();
    }
  }
  document.addEventListener('pointerdown', guardBackground, true);
  document.addEventListener('click', guardBackground, true);

  var observer = new MutationObserver(function (records) {
    if (records.some(function (record) {
      return record.type === 'childList' || record.target === opener || record.target === panel ||
        (panel && panel.contains(record.target));
    })) sync();
  });
  function start() {
    observer.observe(document.body, {childList: true, subtree: true, attributes: true,
      attributeFilter: ['aria-expanded', 'aria-hidden', 'style', 'hidden', 'disabled', 'tabindex']});
    sync(); // Assets may run either before or after Dash mounts the shell.
  }
  window.addEventListener('resize', sync);
  if (media && media.addEventListener) media.addEventListener('change', sync);
  else if (media && media.addListener) media.addListener(sync);
  window.addEventListener('pagehide', function () { suspended = true; open = false; releaseModal(); });
  window.addEventListener('pageshow', function () { suspended = false; sync(); });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
}());
