/* "Ask" chat screen: opens from any [data-ask-open] button, sends questions to
   /api/ask and shows the answer with the articles it came from. */
(function () {
  'use strict';
  var root = document.getElementById('ask');
  if (!root) return;

  var log = document.getElementById('ask-log');
  var form = document.getElementById('ask-form');
  var input = document.getElementById('ask-input');
  var sendBtn = document.getElementById('ask-send');
  var note = document.getElementById('ask-note');
  var chips = document.getElementById('ask-chips');
  var history = [];          // [{role, content}] sent along for follow-up questions
  var busy = false;
  var loadedStatus = false;
  var lastFocus = null;

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // Tiny, safe formatter: everything is escaped first, then **bold**, `-`
  // bullet lines and paragraphs are turned into HTML.
  function format(text) {
    var blocks = esc(text).split(/\n{2,}/);
    return blocks.map(function (block) {
      var lines = block.split('\n');
      var isList = lines.every(function (l) { return /^\s*[-•*]\s+/.test(l); });
      var inline = function (s) { return s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>'); };
      if (isList) {
        return '<ul>' + lines.map(function (l) { return '<li>' + inline(l.replace(/^\s*[-•*]\s+/, '')) + '</li>'; }).join('') + '</ul>';
      }
      return '<p>' + inline(lines.join('<br>')) + '</p>';
    }).join('');
  }

  function scrollDown() { log.scrollTop = log.scrollHeight; }

  function addUser(text) {
    var el = document.createElement('div');
    el.className = 'ask__msg ask__msg--user';
    el.innerHTML = '<div class="ask__bubble">' + esc(text) + '</div>';
    log.appendChild(el);
    scrollDown();
  }

  function addBot(html, sources, extraClass) {
    var el = document.createElement('div');
    el.className = 'ask__msg ask__msg--bot' + (extraClass ? ' ' + extraClass : '');
    var inner = '<div class="ask__bubble">' + html + '</div>';
    if (sources && sources.length) {
      inner += '<div class="ask__sources">' + sources.map(function (s) {
        return '<a class="ask__source" href="' + esc(s.url) + '">' +
          '<span class="ask__source-title">' + esc(s.title) + '</span>' +
          (s.pill ? '<span class="ask__source-pill">' + esc(s.pill) + '</span>' : '') + '</a>';
      }).join('') + '</div>';
    }
    el.innerHTML = inner;
    log.appendChild(el);
    scrollDown();
    return el;
  }

  function addTyping() {
    var el = document.createElement('div');
    el.className = 'ask__msg ask__msg--bot ask__msg--typing';
    el.innerHTML = '<div class="ask__bubble" aria-label="Thinking"><span></span><span></span><span></span></div>';
    log.appendChild(el);
    scrollDown();
    return el;
  }

  function setNote(text, isError) {
    note.textContent = text || '';
    note.classList.toggle('is-error', !!isError);
  }

  function remainingNote(remaining, limit) {
    if (remaining == null) return '';
    if (remaining === 0) return 'No questions left today.';
    return remaining + (remaining === 1 ? ' question' : ' questions') + ' left today';
  }

  function csrf() { return (window.READER && window.READER.csrf) || ''; }

  function loadStatus() {
    if (loadedStatus) return;
    loadedStatus = true;
    fetch('/api/ask/status', { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.enabled) {
          setNote('Search is not switched on yet.', true);
          return;
        }
        setNote(remainingNote(d.remaining, d.limit));
        if (d.suggestions && d.suggestions.length) {
          chips.innerHTML = d.suggestions.map(function (s) {
            return '<button type="button" class="ask__chip">' + esc(s) + '</button>';
          }).join('');
          chips.hidden = false;
        }
      })
      .catch(function () { loadedStatus = false; });
  }

  function ask(question) {
    question = question.trim();
    if (!question || busy) return;
    busy = true;
    sendBtn.disabled = true;
    chips.hidden = true;
    setNote('');
    addUser(question);
    var typing = addTyping();

    fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRF-Token': csrf() },
      credentials: 'same-origin',
      body: JSON.stringify({ question: question, history: history.slice(-6) })
    })
      .then(function (r) {
        return r.json().catch(function () { return { ok: false, error: 'Unexpected response from the server.' }; });
      })
      .then(function (d) {
        typing.remove();
        if (!d.ok) {
          addBot('<p>' + esc(d.error || 'Something went wrong. Please try again.') + '</p>', null, 'ask__msg--error');
          setNote(d.remaining != null ? remainingNote(d.remaining, d.limit) : '', false);
          return;
        }
        history.push({ role: 'user', content: question });
        if (d.matched) {
          history.push({ role: 'assistant', content: d.answer });
          addBot(format(d.answer), d.sources);
        } else {
          var msg = "I haven't written about that yet, so I can't answer it from the articles.";
          history.push({ role: 'assistant', content: msg });
          addBot('<p>' + esc(msg) + '</p>' + (d.sources && d.sources.length ? '<p>These might be close:</p>' : ''), d.sources);
        }
        setNote(remainingNote(d.remaining, d.limit));
      })
      .catch(function () {
        typing.remove();
        addBot("<p>Couldn't reach the server. Check your connection and try again.</p>", null, 'ask__msg--error');
      })
      .then(function () {
        busy = false;
        sendBtn.disabled = false;
        if (!window.matchMedia('(pointer: coarse)').matches) input.focus();
      });
  }

  // ── Opening and closing ────────────────────────────────────────────────
  // Full-page mode (larger screens only; the button is hidden on phones).
  var expandBtn = document.getElementById('ask-expand');
  function setWide(on, remember) {
    root.classList.toggle('is-wide', on);
    if (expandBtn) {
      expandBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
      var label = on ? 'Back to small window' : 'Full page';
      expandBtn.setAttribute('aria-label', label);
      expandBtn.setAttribute('title', label);
    }
    if (remember) { try { localStorage.setItem('sf-ask-wide', on ? '1' : '0'); } catch (e) {} }
  }
  try { if (localStorage.getItem('sf-ask-wide') === '1') setWide(true, false); } catch (e) {}

  function open() {
    if (root.classList.contains('is-open')) return;
    lastFocus = document.activeElement;
    root.removeAttribute('inert');
    root.setAttribute('aria-hidden', 'false');
    // One frame later so the browser has painted the closed state and the
    // slide-up actually animates.
    requestAnimationFrame(function () {
      root.classList.add('is-open');
      document.documentElement.classList.add('ask-open');
    });
    loadStatus();
    setTimeout(function () {
      if (!window.matchMedia('(pointer: coarse)').matches) input.focus();
    }, 380);
  }

  function close() {
    if (!root.classList.contains('is-open')) return;
    root.classList.remove('is-open');
    document.documentElement.classList.remove('ask-open');
    root.setAttribute('aria-hidden', 'true');
    root.setAttribute('inert', '');
    if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (e) {} }
  }

  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-ask-open]')) { e.preventDefault(); open(); }
    else if (e.target.closest('[data-ask-wide]')) { setWide(!root.classList.contains('is-wide'), true); }
    else if (e.target.closest('[data-ask-close]')) { close(); }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') close();
  });

  chips.addEventListener('click', function (e) {
    var chip = e.target.closest('.ask__chip');
    if (chip) ask(chip.textContent);
  });

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var q = input.value;
    input.value = '';
    input.style.height = '';
    ask(q);
  });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });
  input.addEventListener('input', function () {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 120) + 'px';
  });
})();
