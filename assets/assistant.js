/* Local focus/Escape support only. No assistant backend or outbound transport. */
(function () {
  'use strict';
  document.addEventListener('keydown', function (event) {
    var opener = document.getElementById('btn_sidebar');
    var close = document.getElementById('assistant-close');
    if (event.key === 'Escape' && opener && close && opener.getAttribute('aria-expanded') === 'true') {
      close.click(); // Dedicated idempotent close callback, never a toggle race.
    }
  });
  var observed = null;
  var observer = new MutationObserver(function () {
    var opener = document.getElementById('btn_sidebar');
    if (!opener || opener === observed) return;
    observed = opener;
    var wasOpen = false;
    new MutationObserver(function () {
      var open = opener.getAttribute('aria-expanded') === 'true';
      if (open) {
        var close = document.getElementById('assistant-close');
        if (close) close.focus();
      } else if (wasOpen) opener.focus();
      wasOpen = open;
    }).observe(opener, {attributes: true, attributeFilter: ['aria-expanded']});
  });
  function start() { observer.observe(document.body, {childList: true, subtree: true}); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
