// dhrubo dashboard — SSE log consumer (vanilla JS).
// Subscribes to /jobs/{id}/events, appends each stdout/stderr line to
// the <pre id="log"> element, and renders a terminal chip on done/failed/cancelled.

(function () {
  'use strict';

  var log = document.getElementById('log');
  if (!log) { return; }
  var jobId = log.getAttribute('data-job-id');
  if (!jobId) { return; }

  var url = '/jobs/' + encodeURIComponent(jobId) + '/events';
  var stateChip = null;

  function findStateChip() {
    var headers = document.querySelectorAll('.card h2 .state');
    for (var i = 0; i < headers.length; i++) {
      var chip = headers[i];
      if (chip && chip.parentElement) { return chip; }
    }
    return null;
  }

  function append(prefix, text) {
    var span = document.createElement('span');
    if (prefix) {
      span.appendChild(document.createTextNode(prefix));
    }
    span.appendChild(document.createTextNode(text + '\n'));
    log.appendChild(span);
    // Auto-scroll to bottom.
    log.scrollTop = log.scrollHeight;
  }

  function setTerminal(state, code) {
    if (!stateChip) { stateChip = findStateChip(); }
    if (stateChip) {
      // Update class to match the new state.
      stateChip.className = 'state state-' + state;
      stateChip.textContent = state + (code != null ? ' (' + code + ')' : '');
    }
  }

  function open() {
    var source = new EventSource(url);
    source.addEventListener('stdout', function (e) {
      append('', e.data);
    });
    source.addEventListener('stderr', function (e) {
      append('', e.data);
    });
    source.addEventListener('done', function (e) {
      append('.term', '--- DONE ' + e.data + ' ---');
      setTerminal('done', e.data);
      source.close();
      // Disable the Cancel button when terminal.
      var btn = document.querySelector('form.inline button');
      if (btn) { btn.disabled = true; }
    });
    source.addEventListener('failed', function (e) {
      append('.err', '--- FAILED ' + e.data + ' ---');
      setTerminal('failed', e.data);
      source.close();
      var btn = document.querySelector('form.inline button');
      if (btn) { btn.disabled = true; }
    });
    source.addEventListener('cancelled', function (e) {
      append('.term', '--- CANCELLED ' + e.data + ' ---');
      setTerminal('cancelled', e.data);
      source.close();
      var btn = document.querySelector('form.inline button');
      if (btn) { btn.disabled = true; }
    });
    source.onerror = function () {
      // Browser will auto-reconnect; nothing else to do.
    };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', open);
  } else {
    open();
  }
})();
