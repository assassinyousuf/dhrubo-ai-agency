// dhrubo dashboard — Diff form XHR handler (vanilla JS).

(function () {
  'use strict';

  var form = document.getElementById('diff-form');
  if (!form) { return; }

  var resultCard = document.getElementById('result-card');
  var errorCard = document.getElementById('error-card');
  var resultEl = document.getElementById('result');
  var errorEl = document.getElementById('error');
  var summaryEl = document.getElementById('result-summary');

  function show(node) { node.hidden = false; }
  function hide(node) { node.hidden = true; }
  function clearAll() {
    hide(resultCard);
    hide(errorCard);
    resultEl.textContent = '';
    errorEl.textContent = '';
    summaryEl.textContent = '';
  }

  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    clearAll();

    var fd = new FormData(form);
    var body = new URLSearchParams();
    fd.forEach(function (v, k) {
      if (typeof v === 'string' && v.length > 0) {
        body.append(k, v);
      }
    });

    var submit = form.querySelector('button[type="submit"]');
    if (submit) { submit.disabled = true; }

    fetch('/api/diff', {
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
        if (out.ok) {
          show(resultCard);
          resultEl.textContent = JSON.stringify(out.payload, null, 2);
          summaryEl.textContent =
            ' [' + (out.payload.run_id_a || '?') +
            ' -> ' + (out.payload.run_id_b || '?') + ']';
        } else {
          show(errorCard);
          errorEl.textContent =
            (out.status || '?') + ': ' +
            (out.payload && out.payload.detail
              ? out.payload.detail
              : JSON.stringify(out.payload));
        }
      })
      .catch(function (err) {
        if (submit) { submit.disabled = false; }
        show(errorCard);
        errorEl.textContent = 'Network error: ' + (err && err.message ? err.message : err);
      });
  });
})();
