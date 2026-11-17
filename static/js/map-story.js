/*
 * Travel map story (phones) -- the story's map sits full screen behind the
 * story. The page is unchanged on computers.
 *
 *   - At the top of the story a strip of the map shows above the story card.
 *   - Tapping that strip, or the Map button in the bottom name bar, slides the
 *     story away and leaves the whole map to explore.
 *   - "Back to story" (or the phone's back gesture) slides the story back.
 *     The story is only moved out of sight, never closed, so the reader lands
 *     on exactly the line they left.
 *   - A link in the text to a place's directory page (…/listing/<name>) opens
 *     the map and asks it to fly to that place and open its card:
 *       page -> map  { type: 'mansfield:focus-place', listing, name }
 *       map  -> page { type: 'mansfield:focused', found }
 *
 * The map is an iframe from the directory app (an allowed map host only), set
 * to ?mode=story so it pans with one finger and leaves room for our button.
 */
(function () {
  'use strict';

  var layer = document.getElementById('mapstory');
  if (!layer) { document.body.classList.remove('has-mapstory'); return; }

  var HOSTS = (window.MAP_HOSTS || []).map(function (h) { return String(h).toLowerCase(); });
  var frame = layer.querySelector('.mapstory__frame');
  var back = layer.querySelector('.mapstory__back');
  var src = frame.getAttribute('data-src') || '';
  var origin = '';
  try {
    var u = new URL(src);
    if (HOSTS.indexOf(u.host.toLowerCase()) >= 0 && (u.protocol === 'https:' || /^(localhost|127\.0\.0\.1)(:\d+)?$/.test(u.host))) origin = u.origin;
  } catch (e) {}
  if (!origin) { document.body.classList.remove('has-mapstory'); return; }

  var phone = window.matchMedia('(max-width: 640px)');

  // The layer must not live inside the story: the story slides away, the map stays put.
  document.body.appendChild(layer);

  // Load the map once the story itself has loaded (phones only).
  function load() { if (!frame.getAttribute('src')) frame.setAttribute('src', src); }
  function loadSoon() {
    if (!phone.matches) return;
    if (document.readyState === 'complete') setTimeout(load, 250);
    else window.addEventListener('load', function () { setTimeout(load, 250); }, { once: true });
  }
  loadSoon();

  var isOpen = false;

  function open() {
    if (!phone.matches) return false;
    load();
    if (isOpen) return true;
    isOpen = true;
    document.body.classList.add('mapstory-open');
    layer.setAttribute('aria-hidden', 'false');
    frame.removeAttribute('tabindex');
    // So the phone's back gesture brings the story back instead of leaving the page.
    try { history.pushState({ mapstory: true }, ''); } catch (e) {}
    setTimeout(function () { try { back.focus({ preventScroll: true }); } catch (e) {} }, 380);
    return true;
  }

  function close(fromHistory) {
    if (!isOpen) return;
    isOpen = false;
    document.body.classList.remove('mapstory-open');
    layer.setAttribute('aria-hidden', 'true');
    frame.setAttribute('tabindex', '-1');
    if (!fromHistory && history.state && history.state.mapstory) {
      try { history.back(); } catch (e) {}
    }
  }

  window.addEventListener('popstate', function () { if (isOpen) close(true); });
  back.addEventListener('click', function () { close(false); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && isOpen && !document.querySelector('.mlist.is-open')) close(false);
  });
  if (phone.addEventListener) {
    phone.addEventListener('change', function (e) { if (e.matches) loadSoon(); else close(false); });
  }

  // ── Asking the map to show one place ──────────────────────────────────
  var asking = null;
  window.addEventListener('message', function (e) {
    if (e.source !== frame.contentWindow || e.origin !== origin) return;
    if (e.data && e.data.type === 'mansfield:focused' && asking) { clearInterval(asking); asking = null; }
  });
  function focusPlace(slug, name) {
    if (asking) clearInterval(asking);
    var tries = 0;
    function send() {
      try { frame.contentWindow.postMessage({ type: 'mansfield:focus-place', listing: slug, name: name }, origin); } catch (e) {}
      if (++tries > 16 && asking) { clearInterval(asking); asking = null; }   // the map may still be loading: keep asking for ~10s
    }
    asking = setInterval(send, 600);
    send();
  }

  document.addEventListener('click', function (e) {
    // The strip above the story and the Map button.
    if (e.target.closest('[data-mapstory-open]')) {
      if (open()) { e.preventDefault(); e.stopPropagation(); }
      return;
    }
    // A link in the story to a place's directory page.
    var a = e.target.closest('.article__content a[href]');
    if (!a || !phone.matches) return;
    var url;
    try { url = new URL(a.href); } catch (err) { return; }
    if (url.origin !== origin) return;
    var m = /^\/listing\/([A-Za-z0-9][A-Za-z0-9_-]{0,200})\/?$/.exec(url.pathname);
    if (!m) return;
    e.preventDefault();
    e.stopPropagation();
    open();
    focusPlace(m[1], a.textContent.replace(/\s+/g, ' ').trim());
  }, true);
})();
