/*
 * Footprints: rate a place 1-5 footprints, "worth the walk?" (footprints.py is the server side).
 *
 *   MansfieldFootprints.load(['slug', ...])  -> Promise(data)   data = {signedIn, mine: {slug: n}, stats: {slug: {avg, n}}}
 *   MansfieldFootprints.rate(slug, steps, {visited, walk, name}) -> Promise   (steps 0 takes the rating back)
 *   MansfieldFootprints.row(slug, name, {walk, onRated})   a "Worth the walk?" row of 5 footprint buttons
 *   MansfieldFootprints.badgeEl(slug)                      "4.6 · 3 walkers" with a footprint, or null
 *   MansfieldFootprints.icon                                the footprint SVG (also used by the map's arrival card)
 */
(function () {
  'use strict';

  var READER = window.READER || { signedIn: false, csrf: '' };
  var ICON = '<svg viewBox="0 0 24 24" aria-hidden="true">' +
    '<ellipse cx="7.2" cy="10.4" rx="3" ry="4.3" transform="rotate(-10 7.2 10.4)"/><ellipse cx="7.9" cy="18.4" rx="2.2" ry="2.6" transform="rotate(-10 7.9 18.4)"/>' +
    '<ellipse cx="16.8" cy="6.2" rx="3" ry="4.3" transform="rotate(10 16.8 6.2)"/><ellipse cx="16.1" cy="14.2" rx="2.2" ry="2.6" transform="rotate(10 16.1 14.2)"/></svg>';
  var data = { signedIn: !!READER.signedIn, mine: {}, stats: {} };

  function merge(d) {
    if (!d) return data;
    if (typeof d.signedIn === 'boolean') data.signedIn = d.signedIn;
    Object.keys(d.mine || {}).forEach(function (k) { data.mine[k] = d.mine[k]; });
    Object.keys(d.stats || {}).forEach(function (k) { data.stats[k] = d.stats[k]; });
    return data;
  }
  function load(slugs) {
    var list = (slugs || []).filter(function (s) { return /^[A-Za-z0-9][A-Za-z0-9_-]{0,200}$/.test(String(s || '')); }).slice(0, 40);
    if (!list.length) return Promise.resolve(data);
    return fetch('/api/footprints?s=' + encodeURIComponent(list.join(',')), { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(merge)
      .catch(function () { return data; });
  }
  function signIn() { location.href = '/account/login?next=' + encodeURIComponent(location.pathname + location.search); }
  function rate(slug, steps, extra) {
    if (!READER.signedIn) { signIn(); return Promise.reject(new Error('sign_in')); }
    extra = extra || {};
    return fetch('/api/footprints', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRF-Token': READER.csrf || '' },
      body: JSON.stringify({ slug: slug, steps: steps, visited: !!extra.visited, walk: extra.walk || '', name: extra.name || '' })
    }).then(function (r) {
      if (r.status === 401) { signIn(); throw new Error('sign_in'); }
      if (!r.ok) throw new Error('failed');
      return r.json();
    }).then(function (d) {
      if (d.steps) data.mine[slug] = d.steps; else delete data.mine[slug];
      if (d.stats) data.stats[slug] = d.stats; else delete data.stats[slug];
      return d;
    });
  }

  function badgeText(slug) {
    var st = data.stats[slug];
    if (!st || !st.n) return '';
    return Number(st.avg).toFixed(1) + ' · ' + st.n + (st.n === 1 ? ' walker' : ' walkers');
  }
  function badgeEl(slug) {
    var text = badgeText(slug);
    if (!text) return null;
    var b = document.createElement('span');
    b.className = 'fpbadge';
    b.title = 'Worth the walk: ' + text;
    b.innerHTML = ICON;
    b.appendChild(document.createTextNode(text));
    return b;
  }

  function row(slug, name, opts) {
    opts = opts || {};
    var wrap = document.createElement('div');
    wrap.className = 'fprow';
    var label = document.createElement('span');
    label.className = 'fprow__label';
    label.textContent = 'Worth the walk?';
    var steps = document.createElement('div');
    steps.className = 'fprow__steps';
    steps.setAttribute('role', 'radiogroup');
    steps.setAttribute('aria-label', 'Worth the walk: 1 to 5 footprints');
    var buttons = [];
    function paint(n) {
      buttons.forEach(function (b, i) {
        b.classList.toggle('is-on', i < n);
        b.setAttribute('aria-checked', i + 1 === n ? 'true' : 'false');
      });
    }
    for (var i = 1; i <= 5; i++) {
      (function (n) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'fprow__step';
        b.setAttribute('role', 'radio');
        b.setAttribute('aria-label', n + (n === 1 ? ' footprint' : ' footprints'));
        b.innerHTML = ICON;
        b.addEventListener('click', function (e) {
          e.stopPropagation();
          var was = data.mine[slug] || 0;
          var next = was === n ? 0 : n;   // tap the same footprint again to take it back
          paint(next);
          rate(slug, next, { visited: opts.visited, walk: opts.walk, name: name }).then(function (d) {
            paint(d.steps || 0);
            if (opts.onRated) opts.onRated(d);
          }).catch(function (err) { if (err.message !== 'sign_in') paint(was); });
        });
        buttons.push(b);
        steps.appendChild(b);
      })(i);
    }
    paint(data.mine[slug] || 0);
    wrap.appendChild(label);
    wrap.appendChild(steps);
    return wrap;
  }

  window.MansfieldFootprints = { load: load, rate: rate, row: row, badgeEl: badgeEl, badgeText: badgeText, icon: ICON, data: data };
})();
