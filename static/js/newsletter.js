/* Newsletter: sends every [data-newsletter-form] with fetch (so the page
   doesn't reload) and runs the timed pop-up (#nl-popup), which exists only on
   the homepage and article pages.

   Browser storage (wrapped in try/catch, the page works without it):
     sf-nl-subscribed  "1" once someone has signed up -- pop-up never shows again
     sf-nl-dismissed   timestamp of the last time they closed it -- quiet for 14 days */
(function () {
  'use strict';

  var SHOW_AFTER_MS = 25000;                    // time on the page before the pop-up appears
  var QUIET_DAYS = 14;                          // after closing it, wait this long
  var KEY_SUBSCRIBED = 'sf-nl-subscribed';
  var KEY_DISMISSED = 'sf-nl-dismissed';

  function store(key, value) {
    try {
      if (value === undefined) return localStorage.getItem(key);
      localStorage.setItem(key, value);
    } catch (e) { return null; }
  }

  // ── Forms (footer, /newsletter page, pop-up) ────────────────────────────
  function csrf() {
    return (window.READER && window.READER.csrf) || '';
  }

  function wireForm(form) {
    var status = form.querySelector('.nl-status');
    var button = form.querySelector('button[type="submit"]');

    function say(text, isError) {
      if (!status) return;
      status.hidden = !text;
      status.textContent = text || '';
      status.classList.toggle('is-error', !!isError);
    }

    form.addEventListener('submit', function (event) {
      if (!window.fetch || !window.FormData) return;     // fall back to a normal post
      event.preventDefault();
      say('');
      if (button) button.disabled = true;

      fetch(form.action, {
        method: 'POST',
        body: new FormData(form),
        headers: { 'Accept': 'application/json', 'X-CSRF-Token': csrf() },
        credentials: 'same-origin'
      })
        .then(function (res) {
          return res.json().catch(function () { return {}; }).then(function (data) {
            return { ok: res.ok && data.ok, message: data.message };
          });
        })
        .then(function (result) {
          if (result.ok) {
            store(KEY_SUBSCRIBED, '1');
            form.dispatchEvent(new CustomEvent('newsletter:done', { bubbles: true, detail: result }));
            if (!form.closest('#nl-popup')) {
              say(result.message || "You're on the list. Thanks for signing up!", false);
              form.reset();
            }
          } else {
            say(result.message || 'Something went wrong. Please try again.', true);
          }
        })
        .catch(function () {
          say("Couldn't reach the server. Check your connection and try again.", true);
        })
        .then(function () { if (button) button.disabled = false; });
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll('[data-newsletter-form]'), wireForm);

  // ── Timed pop-up ────────────────────────────────────────────────────────
  var popup = document.getElementById('nl-popup');
  if (!popup) return;

  var steps = {};
  Array.prototype.forEach.call(popup.querySelectorAll('[data-nl-step]'), function (el) {
    steps[el.getAttribute('data-nl-step')] = el;
  });
  var lastFocus = null;

  function showStep(name) {
    Object.keys(steps).forEach(function (key) { steps[key].hidden = key !== name; });
  }

  function recentlyDismissed() {
    var at = parseInt(store(KEY_DISMISSED) || '0', 10);
    return at && Date.now() - at < QUIET_DAYS * 86400000;
  }

  function openPopup() {
    if (!popup.hidden) return;
    lastFocus = document.activeElement;
    showStep('intro');
    popup.hidden = false;
    document.documentElement.classList.add('nl-open');
    var cta = popup.querySelector('[data-nl-open]');
    if (cta) cta.focus();
  }

  function closePopup() {
    if (popup.hidden) return;
    popup.hidden = true;
    document.documentElement.classList.remove('nl-open');
    if (store(KEY_SUBSCRIBED) !== '1') store(KEY_DISMISSED, String(Date.now()));
    if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (e) {} }
  }

  popup.addEventListener('click', function (event) {
    if (event.target.closest('[data-nl-close]')) closePopup();
    else if (event.target.closest('[data-nl-open]')) {
      showStep('form');
      var first = popup.querySelector('input[name="name"]');
      if (first) first.focus();
    }
  });
  popup.addEventListener('newsletter:done', function () {
    showStep('done');
    var btn = popup.querySelector('[data-nl-step="done"] button');
    if (btn) btn.focus();
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closePopup();
  });

  if (store(KEY_SUBSCRIBED) === '1' || recentlyDismissed()) return;

  // Count time only while the tab is visible, so a page left open in the
  // background doesn't spring the pop-up the moment someone comes back.
  var shown = false;
  var visibleMs = 0;
  var lastTick = Date.now();
  setInterval(function () {
    var now = Date.now();
    if (!document.hidden) visibleMs += now - lastTick;
    lastTick = now;
    if (!shown && visibleMs >= SHOW_AFTER_MS) {
      shown = true;
      if (store(KEY_SUBSCRIBED) !== '1') openPopup();
    }
  }, 1000);
})();
