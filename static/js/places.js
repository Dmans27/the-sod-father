/*
 * Places to try -- lets a signed-in reader save a restaurant / place from a
 * map pin inside a story.
 *
 * The map is an iframe from the ai-app (another site), so it can't reach
 * this account. It only sends messages up to this page:
 *
 *   map  -> page   { type: 'mansfield:hello' }                 "can I save places here?"
 *   page -> map    { type: 'mansfield:saved-state', signedIn, keys: [...] }
 *   map  -> page   { type: 'mansfield:save-place', place: {...} }   "toggle this one"
 *   card -> page   { type: 'mansfield:resize', height, expand }       "make my frame this tall"
 *                  (Place cards only -- they size themselves to the card inside.)
 *
 * Before doing anything we check the message really came from one of the map
 * iframes on this page AND from an allowed map host (window.MAP_HOSTS). The
 * save itself is an ordinary signed-in, CSRF-protected call to /api/places.
 *
 * A signed-out reader who taps Save is sent to sign in; the place is held in
 * sessionStorage for 15 minutes and saved when they land back on the site.
 */
(function () {
  var READER = window.READER || { signedIn: false, csrf: '' };
  var HOSTS = (window.MAP_HOSTS || []).map(function (h) { return String(h).toLowerCase(); });
  var PENDING = 'sf-pending-place';
  var keys = null; // place keys this reader has saved (null until loaded)

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function originAllowed(origin) {
    var m = /^(https?):\/\/([^\/]+)$/.exec(origin || '');
    if (!m || HOSTS.indexOf(m[2].toLowerCase()) < 0) return false;
    return m[1] === 'https' || /^(localhost|127\.0\.0\.1)(:\d+)?$/.test(m[2]);
  }

  function frames() {
    return Array.prototype.slice.call(document.querySelectorAll('.article__map-frame iframe, .article__place iframe'));
  }

  function fromMapFrame(e) {
    if (!originAllowed(e.origin)) return false;
    return frames().some(function (f) { return f.contentWindow === e.source; });
  }

  function api(method, url, body) {
    var opts = { method: method, credentials: 'same-origin',
      headers: { 'X-CSRF-Token': READER.csrf, 'Accept': 'application/json', 'Content-Type': 'application/json' } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) { return { ok: r.ok, status: r.status, data: data }; });
    });
  }

  function loadKeys() {
    if (keys) return Promise.resolve(keys);
    if (!READER.signedIn) { keys = []; return Promise.resolve(keys); }
    return api('GET', '/api/places').then(function (res) {
      keys = (res.ok && res.data.keys) || [];
      return keys;
    }).catch(function () { keys = []; return keys; });
  }

  function tell(target, origin) {
    if (!target || !originAllowed(origin)) return;
    try { target.postMessage({ type: 'mansfield:saved-state', signedIn: !!READER.signedIn, keys: keys || [] }, origin); } catch (e) {}
  }

  function tellAll() {
    frames().forEach(function (f) {
      HOSTS.forEach(function (h) {
        // We don't know which scheme the frame uses; its src does.
        try {
          var u = new URL(f.src);
          if (u.host.toLowerCase() === h) tell(f.contentWindow, u.origin);
        } catch (e) {}
      });
    });
  }

  // ── Little confirmation toast ────────────────────────────────────────
  var toastEl = null, toastTimer = null;
  function toast(html) {
    if (!toastEl) {
      toastEl = document.createElement('div');
      toastEl.className = 'place-toast';
      toastEl.setAttribute('role', 'status');
      document.body.appendChild(toastEl);
    }
    toastEl.innerHTML = html;
    void toastEl.offsetHeight;
    toastEl.classList.add('is-on');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove('is-on'); }, 3800);
  }

  function articleSlug(frame) {
    var m = /^\/article\/([^\/?#]+)/.exec(location.pathname);
    if (m) return decodeURIComponent(m[1]);
    // The homepage opens stories in a sheet; the open story's link is marked current.
    var cur = document.querySelector('a[data-slug].is-current');
    return cur ? cur.getAttribute('data-slug') : '';
  }

  function save(place, slug, want) {
    return api('POST', '/api/places', { place: place, article: slug, saved: want }).then(function (res) {
      if (res.ok) {
        keys = res.data.keys || [];
        tellAll();
        if (res.data.saved) toast('Saved <b>' + esc(place.name) + '</b> &middot; <a href="/account#places">View your places</a>');
        else toast('Removed <b>' + esc(place.name) + '</b>');
      } else if (res.status === 401) {
        goSignIn(place, slug);
      } else if (res.data && res.data.error === 'too_many') {
        toast('You\'ve reached the limit for saved places. Remove a few in your account first.');
      } else {
        toast('Sorry, that couldn\'t be saved. Please try again.');
      }
    }).catch(function () { toast('Sorry, that couldn\'t be saved. Please try again.'); });
  }

  function goSignIn(place, slug) {
    try { sessionStorage.setItem(PENDING, JSON.stringify({ place: place, slug: slug, t: Date.now() })); } catch (e) {}
    location.href = '/account/login?next=' + encodeURIComponent(location.pathname + location.search);
  }

  window.addEventListener('message', function (e) {
    var d = e.data;
    if (!d || typeof d !== 'object' || !fromMapFrame(e)) return;
    if (d.type === 'mansfield:resize') {
      var box = frames().filter(function (f) { return f.contentWindow === e.source; })[0];
      var h = Number(d.height);
      if (!box || !box.closest('.article__place') || !isFinite(h)) return;
      box.style.height = Math.max(120, Math.min(1400, Math.round(h))) + 'px';
      if (d.expand) {
        // Opening details/photos makes the card taller: keep it in view.
        setTimeout(function () { box.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }, 320);
      }
      return;
    }
    if (d.type === 'mansfield:hello') {
      loadKeys().then(function () { tell(e.source, e.origin); });
    } else if (d.type === 'mansfield:save-place' && d.place) {
      var frame = frames().filter(function (f) { return f.contentWindow === e.source; })[0];
      var slug = articleSlug(frame);
      if (!READER.signedIn) { goSignIn(d.place, slug); return; }
      loadKeys().then(function () {
        save(d.place, slug, keys.indexOf(String(d.place.key || '')) < 0);
      });
    }
  });

  // Came back from signing in with a place waiting: save it now.
  function applyPending() {
    var raw = null;
    try { raw = sessionStorage.getItem(PENDING); } catch (e) {}
    if (!raw || !READER.signedIn) return;
    try { sessionStorage.removeItem(PENDING); } catch (e) {}
    var p;
    try { p = JSON.parse(raw); } catch (e) { return; }
    if (!p || !p.place || Date.now() - (p.t || 0) > 15 * 60 * 1000) return;
    save(p.place, p.slug || '', true);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', applyPending);
  else applyPending();

  // ── Account page: remove a saved place ───────────────────────────────
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-remove-place]');
    if (!btn) return;
    btn.disabled = true;
    api('DELETE', '/api/places/' + encodeURIComponent(btn.getAttribute('data-remove-place'))).then(function (res) {
      if (!res.ok) { btn.disabled = false; return; }
      var card = btn.closest('.place-card');
      if (card) card.remove();
      var list = document.getElementById('places-list');
      var empty = document.getElementById('places-empty');
      if (list && empty && !list.children.length) empty.hidden = false;
      var count = document.getElementById('places-count');
      if (count) { if (list && list.children.length) count.textContent = list.children.length; else count.remove(); }
    }).catch(function () { btn.disabled = false; });
  });
})();
