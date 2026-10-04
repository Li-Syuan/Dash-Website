/* Dash 2.9.1 reads rejected callback response text twice. Keep its HTTP error. */
(function () {
  'use strict';

  var originalFetch = window.fetch;
  if (!originalFetch || originalFetch.__workspaceDashErrorResponse) return;

  function isCallbackPost(input, init) {
    try {
      var isRequest = window.Request && input instanceof window.Request;
      var method = init && init.method !== undefined ? init.method :
        (isRequest ? input.method : 'GET');
      var url = new URL(isRequest ? input.url : input, window.location.href);
      return String(method).toUpperCase() === 'POST' &&
        url.origin === window.location.origin && url.pathname === '/_dash-update-component';
    } catch (error) {
      // Leave malformed input and native fetch rejections to the caller.
      return false;
    }
  }

  function callbackFetch(input, init) {
    var pending = originalFetch.apply(this, arguments);
    if (!isCallbackPost(input, init)) return pending;
    return pending.then(function (response) {
      if (response.status === 400 || response.status === 401) {
        var originalText = response.text;
        var textPromise;
        response.text = function () {
          if (!textPromise) textPromise = originalText.call(response);
          return textPromise;
        };
      }
      return response;
    });
  }

  callbackFetch.__workspaceDashErrorResponse = true;
  window.fetch = callbackFetch;
}());
