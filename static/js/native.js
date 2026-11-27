/*
 * Extras that only switch on inside the iOS app (the Capacitor shell in
 * capacitor-app/). On a normal website visit window.Capacitor doesn't exist,
 * so nothing here does anything and every call site falls back to the usual
 * web behaviour.
 *
 *   MansfieldNative.active        true inside the app
 *   MansfieldNative.share(u, t)   the real iOS share sheet; returns false if unavailable
 *   MansfieldNative.haptic(kind)  'light' | 'medium' | 'success'
 *   MansfieldNative.push          notifications: status() / enable() / disable() (see below)
 *
 * Also in the app:
 *   - small taps (haptics) on buttons, tabs and saving a story
 *   - saved stories are copied to the phone (as plain text) so they can be read
 *     without a connection -- capacitor-app/www/offline.html shows them when
 *     the site can't be reached
 *
 * Location inside the app comes from static/js/native-geo.js (loaded earlier, in <head>).
 *
 * Needs these Capacitor plugins installed in the app: share, haptics, preferences, geolocation,
 * push-notifications.
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

  // ── Notifications (push.py sends them) ───────────────────────────────
  // iOS is only asked when the reader chooses to: the Notifications row on the Account page, or the
  // one-time card on the Friends page. Once allowed, each launch re-sends this phone's token so the
  // server always has a fresh one. Tapping a notification opens its page. "Turn off" in the app
  // forgets the token on the server (iOS still lets it, but nothing is sent).
  var Push = plugin('PushNotifications');
  var pushToken = null, wantTest = false;
  NATIVE.push = { status: function () { return Promise.resolve('none'); }, enable: function () { return Promise.resolve('none'); },
                  disable: function () { return Promise.resolve('none'); } };

  function api(path, body) {
    var R = window.READER || {};
    return fetch(path, { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': R.csrf || '' }, body: JSON.stringify(body || {}) });
  }
  function prefGet(k) { return plugin('Preferences') ? pget(k).catch(function () { return null; }) : Promise.resolve(null); }
  function prefSet(k, v) { if (plugin('Preferences')) quiet(pset(k, v)); }

  if (Push) {
    quiet(Push.addListener('registration', function (t) {
      var tok = t && t.value;
      if (!tok) return;
      pushToken = tok;
      prefSet('push:token', tok);
      if ((window.READER || {}).signedIn) quiet(api('/api/push/register', { token: tok, test: wantTest }));
      wantTest = false;
    }));
    quiet(Push.addListener('registrationError', function () { wantTest = false; }));
    quiet(Push.addListener('pushNotificationActionPerformed', function (a) {
      var d = (a && a.notification && a.notification.data) || {};
      var u = String(d.url || '');
      if (u.charAt(0) === '/' && u.charAt(1) !== '/') location.href = u;
    }));

    var perm = function () {
      return Push.checkPermissions().then(function (r) { return (r && r.receive) || 'prompt'; }).catch(function () { return 'prompt'; });
    };
    NATIVE.push.status = function () {   // 'on' | 'off' (turned off here) | 'prompt' | 'denied'
      return Promise.all([perm(), prefGet('push:off')]).then(function (v) {
        if (v[0] === 'denied') return 'denied';
        if (v[0] !== 'granted') return 'prompt';
        return v[1] === '1' ? 'off' : 'on';
      });
    };
    NATIVE.push.enable = function () {
      prefSet('push:asked', '1');
      return Push.requestPermissions().then(function (r) {
        if (!r || r.receive !== 'granted') return 'denied';
        prefSet('push:off', '0');
        wantTest = true;
        return Push.register().then(function () { return 'on'; });
      }).catch(function () { return 'denied'; });
    };
    NATIVE.push.disable = function () {
      prefSet('push:off', '1');
      return prefGet('push:token').then(function (tok) {
        if (tok) return api('/api/push/unregister', { token: tok }).catch(function () {});
      }).then(function () { return 'off'; });
    };

    // Each launch, for a signed-in reader who allowed notifications: refresh the token.
    if ((window.READER || {}).signedIn) {
      NATIVE.push.status().then(function (s) { if (s === 'on') quiet(Push.register()); });
    }
    prefGet('push:token').then(function (tok) { if (tok && !pushToken) pushToken = tok; });

    // Signing out: tell the server to stop pinging this phone.
    document.addEventListener('submit', function (e) {
      var f = e.target;
      if (!f || !f.getAttribute || (f.getAttribute('action') || '') !== '/account/logout' || !pushToken) return;
      var i = document.createElement('input');
      i.type = 'hidden'; i.name = 'push_token'; i.value = pushToken;
      f.appendChild(i);
    }, true);
  }

  function pushRow() {   // Account page: "Notifications  Turn on / On · Turn off"
    var row = document.querySelector('[data-push-row]');
    if (!row) return;
    var dd = row.querySelector('dd');
    if (!Push) return;
    row.hidden = false;
    function paint(s) {
      dd.textContent = '';
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'push-row__btn';
      if (s === 'on') {
        var on = document.createElement('span'); on.className = 'push-row__on'; on.textContent = 'On';
        dd.appendChild(on);
        b.textContent = 'Turn off';
        b.addEventListener('click', function () { NATIVE.push.disable().then(paint); });
        dd.appendChild(b);
      } else if (s === 'denied') {
        dd.textContent = 'Off in iPhone Settings › LocalLane';
      } else {
        b.textContent = 'Turn on';
        b.addEventListener('click', function () { NATIVE.haptic('light'); NATIVE.push.enable().then(paint); });
        dd.appendChild(b);
      }
    }
    NATIVE.push.status().then(paint);
  }

  function pushCard() {   // Friends page, once: a gentle ask before iOS's own prompt
    if (!Push || !(window.READER || {}).signedIn || !/^\/account\/friends/.test(location.pathname)) return;
    Promise.all([NATIVE.push.status(), prefGet('push:asked')]).then(function (v) {
      if (v[0] !== 'prompt' || v[1] === '1') return;
      var card = document.createElement('div');
      card.className = 'push-card';
      card.setAttribute('role', 'dialog');
      card.setAttribute('aria-label', 'Notifications');
      card.innerHTML = '<p class="push-card__t">Know when friends add you</p>' +
        '<p class="push-card__s">Get a ping for friend requests and when a friend adds you to a map.</p>' +
        '<div class="push-card__row"><button type="button" class="push-card__no">Not now</button>' +
        '<button type="button" class="push-card__yes">Turn on</button></div>';
      document.body.appendChild(card);
      function close() { prefSet('push:asked', '1'); card.remove(); }
      card.querySelector('.push-card__no').addEventListener('click', close);
      card.querySelector('.push-card__yes').addEventListener('click', function () { close(); NATIVE.push.enable(); });
    });
  }

  function pushUi() { pushRow(); pushCard(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', pushUi);
  else pushUi();

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
