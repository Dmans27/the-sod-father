/*
 * Extras that only switch on inside the iOS app (the Capacitor shell in
 * capacitor-app/). On a normal website visit window.Capacitor doesn't exist,
 * so nothing here does anything and every call site falls back to the usual
 * web behaviour.
 *
 *   MansfieldNative.active        true inside the app
 *   MansfieldNative.share(u, t)   the real iOS share sheet; returns false if unavailable
 *   MansfieldNative.haptic(kind)  'light' | 'medium' | 'success'
 *
 * Also in the app:
 *   - small taps (haptics) on buttons, tabs and saving a story
 *   - pull down at the top of a page to refresh it
 *   - saved stories are copied to the phone (as plain text) so they can be read
 *     without a connection -- capacitor-app/www/offline.html shows them when
 *     the site can't be reached
 *
 * Needs these Capacitor plugins installed in the app: share, haptics, preferences.
 */
(function () {
  'use strict';

  var NATIVE = window.MansfieldNative = { active: false, share: function () { return false; }, haptic: function () {} };

  var C = window.Capacitor;
  var isNative = false;
  try { isNative = !!(C && C.isNativePlatform && C.isNativePlatform()); } catch (e) {}
  if (!isNative) return;

  NATIVE.active = true;
  document.documentElement.classList.add('is-native-app');

  var Plugins = C.Plugins || {};
  function plugin(name) { try { return Plugins[name] || null; } catch (e) { return null; } }
  function quiet(p) { if (p && p.catch) p.catch(function () {}); return p; }

  // ── Share sheet ──────────────────────────────────────────────────────
  NATIVE.share = function (url, title) {
    var Share = plugin('Share');
    if (!Share) return false;
    quiet(Share.share({ title: title, text: title, url: url, dialogTitle: 'Share this story' }));
    return true;
  };

  // ── Haptics ──────────────────────────────────────────────────────────
  NATIVE.haptic = function (kind) {
    var H = plugin('Haptics');
    if (!H) return;
    try {
      if (kind === 'success') quiet(H.notification({ type: 'SUCCESS' }));
      else quiet(H.impact({ style: kind === 'medium' ? 'MEDIUM' : 'LIGHT' }));
    } catch (e) {}
  };

  document.addEventListener('click', function (e) {
    if (e.target.closest && e.target.closest('.story-action, .bottom-nav__item, .comment__tool, .comment__reason, .saved-tab, .account-btn')) {
      NATIVE.haptic('light');
    }
  }, true);

  // ── Pull down to refresh ─────────────────────────────────────────────
  var THRESHOLD = 70, MAX_PULL = 110;
  var startY = null, dist = 0, armed = false, busy = false, bubble = null;

  function overlayOpen() {
    return !!document.querySelector('.csheet.is-open, .share-menu.is-open, .article-sheet-overlay.is-open, .ask.is-open');
  }

  function ensureBubble() {
    if (bubble) return bubble;
    bubble = document.createElement('div');
    bubble.setAttribute('aria-hidden', 'true');
    bubble.style.cssText =
      'position:fixed;left:50%;top:calc(env(safe-area-inset-top, 0px) + 6px);z-index:300;' +
      'padding:7px 14px;border-radius:999px;background:#1b4332;color:#faf6ec;' +
      'font:600 13px -apple-system,BlinkMacSystemFont,"IBM Plex Sans",sans-serif;' +
      'box-shadow:0 4px 14px rgba(0,0,0,.22);pointer-events:none;opacity:0;white-space:nowrap;' +
      'transform:translate(-50%,-60px);';
    document.body.appendChild(bubble);
    return bubble;
  }

  function paint(d) {
    var b = ensureBubble();
    var show = d > 8 || busy;
    b.style.transition = 'none';
    b.style.opacity = show ? '1' : '0';
    b.style.transform = 'translate(-50%,' + (Math.min(d, MAX_PULL) - 40) + 'px)';
    b.textContent = busy ? 'Refreshing…' : (d >= THRESHOLD ? 'Release to refresh' : 'Pull to refresh');
  }

  function reset() {
    startY = null; dist = 0; armed = false;
    if (bubble && !busy) {
      bubble.style.transition = 'opacity .2s ease, transform .2s ease';
      bubble.style.opacity = '0';
      bubble.style.transform = 'translate(-50%,-60px)';
    }
  }

  document.addEventListener('touchstart', function (e) {
    if (busy || e.touches.length !== 1 || window.scrollY > 0 || overlayOpen()) { startY = null; return; }
    if (e.target.closest && e.target.closest('input, textarea, select, [contenteditable], .article__gallery-track, .article__table-wrap, iframe')) { startY = null; return; }
    startY = e.touches[0].clientY; dist = 0; armed = false;
  }, { passive: true });

  document.addEventListener('touchmove', function (e) {
    if (startY === null) return;
    if (window.scrollY > 0) { reset(); return; }
    var dy = e.touches[0].clientY - startY;
    if (dy <= 0) { dist = 0; paint(0); return; }
    dist = Math.min(dy * 0.5, MAX_PULL);
    if (dist >= THRESHOLD && !armed) { armed = true; NATIVE.haptic('medium'); }
    if (dist < THRESHOLD) armed = false;
    paint(dist);
  }, { passive: true });

  function release() {
    if (startY === null) return;
    if (dist >= THRESHOLD && !busy) {
      busy = true;
      paint(THRESHOLD);
      setTimeout(function () { location.reload(); }, 150);
      setTimeout(function () { busy = false; reset(); }, 8000); // if the reload never happens
    } else {
      reset();
    }
    startY = null;
  }
  document.addEventListener('touchend', release, { passive: true });
  document.addEventListener('touchcancel', function () { reset(); }, { passive: true });

  // ── Offline reading of saved stories ─────────────────────────────────
  // Each saved story is stored in the app's own storage (Capacitor Preferences)
  // as plain text under "offline:<slug>", with a list under "offline:index".
  // Signing out clears it.
  var MAX_OFFLINE = 50;
  var chain = Promise.resolve();

  function pget(k) { return Plugins.Preferences.get({ key: k }).then(function (r) { return r && r.value; }); }
  function pset(k, v) { return Plugins.Preferences.set({ key: k, value: v }); }
  function prem(k) { return Plugins.Preferences.remove({ key: k }); }

  function readIndex() {
    return pget('offline:index').then(function (v) {
      try { var a = JSON.parse(v); return Array.isArray(a) ? a : []; } catch (e) { return []; }
    }).catch(function () { return []; });
  }

  function syncOffline() {
    if (!plugin('Preferences')) return;
    chain = chain.then(function () {
      var R = window.READER || { signedIn: false, saved: [] };
      return readIndex().then(function (idx) {
        if (!R.signedIn) {
          if (!idx.length) return;
          return Promise.all(idx.map(function (i) { return prem('offline:' + i.slug); }))
            .then(function () { return pset('offline:index', '[]'); });
        }
        var want = (R.saved || []).slice(0, MAX_OFFLINE);
        var keep = idx.filter(function (i) { return want.indexOf(i.slug) !== -1; });
        var have = {};
        keep.forEach(function (i) { have[i.slug] = true; });
        var drops = idx.filter(function (i) { return want.indexOf(i.slug) === -1; })
          .map(function (i) { return prem('offline:' + i.slug); });
        var missing = want.filter(function (s) { return !have[s]; });
        var next = keep.slice();
        var online = navigator.onLine !== false;
        var fetches = (online ? missing : []).map(function (slug) {
          return fetch('/api/offline/' + encodeURIComponent(slug), { credentials: 'same-origin' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
              if (!d || !d.slug) return;
              return pset('offline:' + d.slug, JSON.stringify(d)).then(function () {
                next.push({ slug: d.slug, title: d.title, author: d.author, pill: d.pill });
              });
            }).catch(function () {});
        });
        return Promise.all(drops.concat(fetches)).then(function () {
          // keep the same order as the saved list
          next.sort(function (a, b) { return want.indexOf(a.slug) - want.indexOf(b.slug); });
          return pset('offline:index', JSON.stringify(next));
        });
      });
    }).catch(function () {});
  }

  document.addEventListener('mansfield:saved', function (e) {
    NATIVE.haptic('success');
    syncOffline();
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', syncOffline);
  else syncOffline();
})();
