// dhrubo dashboard — Publish form XHR handler (vanilla JS).
//
// IMPORTANT: the token is held in this closure for the duration of the
// fetch only; the input field is cleared immediately when the call returns,
// and the local `token` variable goes out of scope at function exit.

(function () {
  'use strict';

  var form = document.getElementById('publish-form');
  if (!form) { return; }

  var resultCard = document.getElementById('result-card');
  var errorCard = document.getElementById('error-card');
  var resultEl = document.getElementById('result');
  var errorEl = document.getElementById('error');
  var tokenInput = document.getElementById('github_token');

  function show(node) { node.hidden = false; }
  function hide(node) { node.hidden = true; }

  function clearResultUI() {
    hide(resultCard);
    hide(errorCard);
    resultEl.textContent = '';
    errorEl.textContent = '';
  }

  function clearToken() {
    if (tokenInput) { tokenInput.value = ''; }
  }

  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    clearResultUI();

    var fd = new FormData(form);
    var body = new URLSearchParams();
    var token = null;
    fd.forEach(function (v, k) {
      if (typeof v !== 'string') { return; }
      if (k === 'github_token') {
        token = v;
        // Do NOT append to body if empty — let the server fall back
        // to the env var.
        if (v.length > 0) {
          body.append(k, v);
        }
      } else if (v.length > 0) {
        body.append(k, v);
      }
    });

    var submit = form.querySelector('button[type="submit"]');
    if (submit) { submit.disabled = true; }

    fetch('/api/publish', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    })
      .then(function (resp) {
        return resp.json().then(function (j) {
          return { ok: resp.ok, status: resp.status, payload: j };
        });
      })
      .then(function (out) {
        if (submit) { submit.disabled = false; }
        // Token out of scope effectively on next tick; clear input now.
        clearToken();
        if (out.ok && out.payload.ok) {
          show(resultCard);
          resultEl.textContent = JSON.stringify(out.payload, null, 2);
        } else {
          show(errorCard);
          errorEl.textContent =
            (out.status || '?') + ': ' +
            (out.payload && (out.payload.detail || out.payload.error)
              ? (out.payload.detail || out.payload.error)
              : JSON.stringify(out.payload));
        }
      })
      .catch(function (err) {
        if (submit) { submit.disabled = false; }
        clearToken();
        show(errorCard);
        errorEl.textContent = 'Network error: ' + (err && err.message ? err.message : err);
      });
  });
})();
