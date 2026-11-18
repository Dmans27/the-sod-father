/*
 * Map list (phones) -- the List button on a story's map, under the share
 * button. It shows the map's places as a scrolling list of cards (picture
 * on the left; name, address and rating on the right). Tapping a card opens
 * the place's full listing in a second card with a "Back to listing" button.
 *
 * The places live in the map, which is an iframe from the directory app (another
 * site), so we ask it for them:
 *
 *   page -> map   { type: 'mansfield:list-places' }
 *   map  -> page  { type: 'mansfield:places', title, places: [...] }
 *
 * Only a map iframe on this page, from an allowed map host (window.MAP_HOSTS),
 * is asked or listened to, and everything it sends is treated as plain text:
 * cards are built with textContent, and links are checked before use.
 *
 * On a shared link (the map opened on its own) the map draws the same list
 * itself -- see the directory app's templates/embed_map.html.
 */
(function () {
  'use strict';

  var HOSTS = (window.MAP_HOSTS || []).map(function (h) { return String(h).toLowerCase(); });
  var SLUG = /^[A-Za-z0-9][A-Za-z0-9_-]{0,200}$/;
  var COLORS = { food: '#E2574C', coffee: '#C6935A', bars: '#B368F7', wine: '#B4436C', rooftops: '#5AC8E8', shops: '#F5B301',
                 hotels: '#5B7CFA', sights: '#E8833A', other: '#2f9e7e' };

  // Same groups as the map's pins, so a card without a picture gets its pin colour.
  function categoryColor(category) {
    var c = (category || '').toLowerCase();
    if (c.indexOf('coffee') >= 0 || c.indexOf('cafe') >= 0 || c.indexOf('café') >= 0) return COLORS.coffee;
    if (c.indexOf('bar') >= 0 || c.indexOf('cocktail') >= 0) return COLORS.bars;
    if (c.indexOf('wine') >= 0) return COLORS.wine;
    if (c.indexOf('rooftop') >= 0) return COLORS.rooftops;
    if (/hotel|hostel|lodging|guest ?house|apartment|resort|\binn\b/.test(c)) return COLORS.hotels;
    if (/museum|gallery|sight|attraction|landmark|monument|palace|church|cathedral|castle|park|tourist/.test(c)) return COLORS.sights;
    if (c.indexOf('shop') >= 0) return COLORS.shops;
    if (c.indexOf('restaurant') >= 0 || c.indexOf('food') >= 0) return COLORS.food;
    return COLORS.other;
  }

  function frameOrigin(frame) {
    try {
      var u = new URL(frame.src);
      if (HOSTS.indexOf(u.host.toLowerCase()) < 0 || u.username) return null;
      if (u.protocol !== 'https:' && !/^(localhost|127\.0\.0\.1)(:\d+)?$/.test(u.host)) return null;
      return u.origin;
    } catch (e) { return null; }
  }

  // A link from the map is used only if it is a plain web address (pictures: on the map's own site).
  function safeUrl(raw, origin, sameSite) {
    try {
      var u = new URL(String(raw || ''));
      if (u.protocol !== 'https:' && u.protocol !== 'http:') return '';
      if (sameSite && u.origin !== origin) return '';
      return u.href;
    } catch (e) { return ''; }
  }

  function text(tag, cls, value) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    el.textContent = value;
    return el;
  }

  // ── Asking the map for its places ─────────────────────────────────────
  var waiting = null;
  window.addEventListener('message', function (e) {
    if (!waiting || e.source !== waiting.frame.contentWindow || e.origin !== waiting.origin) return;
    if (!e.data || e.data.type !== 'mansfield:places' || !Array.isArray(e.data.places)) return;
    var w = waiting;
    waiting = null;
    clearInterval(w.retry);
    clearTimeout(w.timer);
    w.done(e.data);
  });

  function askMap(frame, origin, done, fail) {
    if (waiting) { clearInterval(waiting.retry); clearTimeout(waiting.timer); }
    // The map may still be loading, so keep asking for a few seconds.
    function send() { try { frame.contentWindow.postMessage({ type: 'mansfield:list-places' }, origin); } catch (e) {} }
    waiting = { frame: frame, origin: origin, done: done, retry: setInterval(send, 700),
                timer: setTimeout(function () { if (waiting) { clearInterval(waiting.retry); waiting = null; } fail(); }, 9000) };
    send();
  }

  // ── The sheet ─────────────────────────────────────────────────────────
  var root, listEl, titleEl, statusEl, detail, detailBody, origin = '';

  function build() {
    root = document.createElement('div');
    root.className = 'mlist';
    root.setAttribute('aria-hidden', 'true');
    root.innerHTML =
      '<div class="mlist__scrim" data-mlist-close></div>' +
      '<section class="mlist__panel" role="dialog" aria-modal="true" aria-labelledby="mlist-title">' +
        '<div class="mlist__head"><h2 class="mlist__title" id="mlist-title"></h2>' +
        '<button type="button" class="mlist__close" data-mlist-close aria-label="Close list">&times;</button></div>' +
        '<p class="mlist__status" role="status"></p>' +
        '<div class="mlist__list"></div>' +
      '</section>' +
      '<section class="mlist__detail" role="dialog" aria-modal="true" aria-label="Place details" hidden>' +
        '<div class="mlist__bar"><button type="button" class="mlist__back">← Back to listing</button></div>' +
        '<div class="mlist__detail-body"></div>' +
      '</section>';
    document.body.appendChild(root);
    listEl = root.querySelector('.mlist__list');
    titleEl = root.querySelector('.mlist__title');
    statusEl = root.querySelector('.mlist__status');
    detail = root.querySelector('.mlist__detail');
    detailBody = root.querySelector('.mlist__detail-body');
    root.addEventListener('click', function (e) { if (e.target.closest('[data-mlist-close]')) close(); });
    root.querySelector('.mlist__back').addEventListener('click', closeDetail);
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape' || !root.classList.contains('is-open')) return;
      if (!detail.hidden) closeDetail(); else close();
    });
  }

  function isDark() {
    return document.documentElement.classList.contains('is-dark') ||
      !!document.querySelector('body.article-page.is-dark, .article-sheet.is-dark');
  }

  function open(title) {
    if (!root) build();
    root.classList.toggle('is-dark', isDark());
    titleEl.textContent = title || 'Places';
    listEl.innerHTML = '';
    statusEl.textContent = 'Loading places…';
    statusEl.hidden = false;
    detail.hidden = true;
    detailBody.innerHTML = '';
    root.setAttribute('aria-hidden', 'false');
    document.documentElement.classList.add('mlist-open');
    void root.offsetHeight;
    root.classList.add('is-open');
  }

  function close() {
    if (!root) return;
    if (waiting) { clearInterval(waiting.retry); clearTimeout(waiting.timer); waiting = null; }
    root.classList.remove('is-open', 'is-detail');
    root.setAttribute('aria-hidden', 'true');
    document.documentElement.classList.remove('mlist-open');
    setTimeout(function () { if (!root.classList.contains('is-open')) { detail.hidden = true; detailBody.innerHTML = ''; } }, 350);
  }

  function rating(place) {
    var r = Number(place.rating);
    return place.rating !== null && place.rating !== undefined && r > 0 ? r : null;
  }

  function legText(m) {
    if (!(m > 0)) return '';
    if (m <= 2500) return Math.max(1, Math.round(m * 1.3 / 80)) + ' min walk';
    var km = m / 1000;
    return (km >= 10 ? Math.round(km) : km.toFixed(1)) + ' km';
  }

  // stop: { n, of, leg, color } for a walking route story's stops, else undefined.
  function card(place, stop) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'mlist__card' + (stop ? ' mlist__card--stop' : '');
    var photo = safeUrl(place.photo, origin, true);
    var ph = document.createElement('span');
    ph.className = 'mlist__ph';
    ph.style.background = categoryColor(place.category);
    if (photo) {
      var img = document.createElement('img');
      img.className = 'mlist__img';
      img.alt = '';
      img.loading = 'lazy';
      img.decoding = 'async';
      img.addEventListener('error', function () { img.replaceWith(ph); }, { once: true });
      img.src = photo;
      btn.appendChild(img);
    } else {
      btn.appendChild(ph);
    }
    var info = document.createElement('span');
    info.className = 'mlist__info';
    if (stop) {
      var num = text('span', 'route-num mlist__num', String(stop.n));
      num.style.setProperty('--c', stop.color);
      num.setAttribute('aria-hidden', 'true');
      btn.appendChild(num);
      var step = text('span', 'mlist__step', 'Stop ' + stop.n + ' of ' + stop.of + (stop.leg ? ' · ' + stop.leg : ''));
      step.style.setProperty('--c', stop.color);
      info.appendChild(step);
    }
    info.appendChild(text('span', 'mlist__name', place.name || 'Place'));
    if (place.address) info.appendChild(text('span', 'mlist__addr', place.address));
    var meta = document.createElement('span');
    meta.className = 'mlist__meta';
    var r = rating(place);
    if (r) {
      meta.appendChild(text('span', 'mlist__rating', '★ ' + r.toFixed(1)));
      if (Number(place.ratingCount) > 0) meta.appendChild(text('span', '', '(' + Number(place.ratingCount).toLocaleString() + ')'));
    }
    if (place.category) meta.appendChild(text('span', 'mlist__cat', String(place.category).replace(/_/g, ' ')));
    if (meta.children.length) info.appendChild(meta);
    btn.appendChild(info);
    btn.addEventListener('click', function () { openDetail(place); });
    return btn;
  }

  // ── The second card: the place's full listing ─────────────────────────
  function openDetail(place) {
    detailBody.innerHTML = '';
    var slug = String(place.listing || '');
    if (SLUG.test(slug)) {
      // The directory's own full place page: pictures, hours, reviews, buttons.
      var frame = document.createElement('iframe');
      frame.className = 'mlist__frame';
      frame.title = place.name || 'Place details';
      frame.src = origin + '/embed/card/' + encodeURIComponent(slug) + '?style=full';
      frame.setAttribute('referrerpolicy', 'strict-origin-when-cross-origin');
      frame.setAttribute('allow', 'fullscreen');
      frame.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox');
      detailBody.appendChild(frame);
    } else {
      detailBody.appendChild(simpleDetail(place));
    }
    detail.hidden = false;
    void detail.offsetHeight;
    root.classList.add('is-detail');
    root.querySelector('.mlist__back').focus({ preventScroll: true });
  }

  // A place that isn't in the directory: what the map knows about it.
  function simpleDetail(place) {
    var box = document.createElement('div');
    box.className = 'mlist__simple';
    var photo = safeUrl(place.photo, origin, true);
    if (photo) {
      var img = document.createElement('img');
      img.className = 'mlist__hero';
      img.alt = '';
      img.addEventListener('error', function () { img.remove(); }, { once: true });
      img.src = photo;
      box.appendChild(img);
    }
    var body = document.createElement('div');
    body.className = 'mlist__simple-body';
    body.appendChild(text('h3', 'mlist__simple-name', place.name || 'Place'));
    var meta = document.createElement('div');
    meta.className = 'mlist__simple-meta';
    var r = rating(place);
    if (r) meta.appendChild(text('span', 'mlist__rating', '★ ' + r.toFixed(1)));
    if (place.category) meta.appendChild(text('span', 'mlist__cat', String(place.category).replace(/_/g, ' ')));
    if (meta.children.length) body.appendChild(meta);
    if (place.address) body.appendChild(text('p', 'mlist__simple-addr', place.address));
    if (place.notes) body.appendChild(text('p', 'mlist__simple-note', place.notes));
    var actions = document.createElement('div');
    actions.className = 'mlist__actions';
    var lat = Number(place.lat), lng = Number(place.lng);
    if (isFinite(lat) && isFinite(lng)) {
      var dir = text('a', 'is-primary', 'Directions');
      dir.href = 'https://www.google.com/maps/dir/?api=1&destination=' + lat + ',' + lng;
      dir.target = '_blank'; dir.rel = 'noopener noreferrer';
      actions.appendChild(dir);
    }
    var site = safeUrl(place.website, origin, false);
    if (site) {
      var a = text('a', '', 'Website');
      a.href = site; a.target = '_blank'; a.rel = 'noopener noreferrer';
      actions.appendChild(a);
    }
    if (actions.children.length) body.appendChild(actions);
    box.appendChild(body);
    return box;
  }

  function closeDetail() {
    root.classList.remove('is-detail');
    setTimeout(function () { if (!root.classList.contains('is-detail')) { detail.hidden = true; detailBody.innerHTML = ''; } }, 330);
  }

  // ── The List button ───────────────────────────────────────────────────
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('.map-list');
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    var fig = btn.closest('.article__map, .mapstory');   // a story's map, or the map behind a travel map story
    var frame = fig && fig.querySelector('.article__map-frame iframe, .mapstory__frame');
    var from = frame && frameOrigin(frame);
    if (!from) return;
    origin = from;
    var caption = fig.querySelector('figcaption');
    var given = '';
    if (caption && caption.firstChild && caption.firstChild.nodeType === 3) given = caption.firstChild.textContent.replace(/[\s·]+$/, '').trim();
    open(given || 'Places');
    askMap(frame, from, function (data) {
      statusEl.hidden = true;
      if (!given && data.title) titleEl.textContent = String(data.title);
      var places = data.places.filter(function (p) { return p && typeof p === 'object' && p.name; });
      // A walking route story: only the route's stops, in the order of the walk.
      var route = fig.classList.contains('mapstory') && window.MansfieldRoute;
      if (route && Array.isArray(route.stops) && route.stops.length) {
        var stops = [];
        route.stops.forEach(function (s, i) {
          var p = places.filter(function (x) { return s.listing && x.listing === s.listing; })[0] ||
                  places.filter(function (x) { return (x.name || '').toLowerCase() === String(s.name || '').toLowerCase(); })[0];
          if (p) stops.push({ place: p, stop: { n: i + 1, of: route.stops.length, leg: i ? legText((route.legs || [])[i - 1]) : 'Start', color: s.color || '#2f9e7e' } });
        });
        if (stops.length) {
          titleEl.textContent = 'Walking route';
          titleEl.appendChild(text('span', 'mlist__count', String(stops.length) + ' stops'));
          stops.forEach(function (x) { listEl.appendChild(card(x.place, x.stop)); });
          return;
        }
      }
      titleEl.appendChild(text('span', 'mlist__count', String(places.length)));
      if (!places.length) { statusEl.textContent = 'No places on this map yet.'; statusEl.hidden = false; return; }
      places.forEach(function (p) { listEl.appendChild(card(p)); });
    }, function () {
      statusEl.textContent = 'Couldn’t load the places. Wait for the map to finish loading, then try again.';
    });
  }, true);
})();
