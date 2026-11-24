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
    if (e.target.closest && e.target.closest('.story-action, .bottom-nav__item, .bottom-nav__ask, .comment__tool, .comment__reason, .saved-tab, .account-btn')) {
      NATIVE.haptic('light');
    }
  }, true);

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
