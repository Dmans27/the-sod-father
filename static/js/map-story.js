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
 *   - Route stories ("This story is a route" in the editor, data-route="1"):
 *     the story's places (place cards and links to a place's page) become
 *     numbered stops in the order they first appear. The story fills the
 *     screen (no map strip); a header at the top shows the numbers and the
 *     stop being read about, and tapping it opens the map there. A "Walk this
 *     route" card closes the story, and readers can save the route to their
 *     profile (/api/routes) from that card or from the map's bottom bar.
 *     /article/<slug>?map=route opens straight onto the map.
 *       page -> map  { type: 'mansfield:route', stops: [{listing, name}] }
 *       map  -> page { type: 'mansfield:route-ready', stops, legs, total }
 *       page -> map  { type: 'mansfield:route-step', index }      (-1: whole route)
 *       page -> map  { type: 'mansfield:route-view', view: 'strip'|'full', pad: {top, bottom} }
 *       map  -> page { type: 'mansfield:read-about', index }      ("Read about this")
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
  // The map runs under the phone's status bar; tell it how tall that is (an iframe can't see it).
  function safeTop() {
    if (document.documentElement.classList.contains('in-sheet')) return 0;   // inside the pop-up card: already below the status bar
    var probe = document.createElement('div');
    probe.style.cssText = 'position:fixed;top:0;left:0;width:1px;visibility:hidden;pointer-events:none;height:env(safe-area-inset-top, 0px)';
    document.body.appendChild(probe);
    var h = Math.round(probe.getBoundingClientRect().height) || 0;
    probe.remove();
    return h;
  }
  function load() {
    if (frame.getAttribute('src')) return;
    var url = src;
    try {
      var x = new URL(src);
      x.searchParams.set('safe', String(safeTop()));
      // Walking route stories use Danny's own walking style from Mapbox Studio (see STYLES in the directory app).
      if (layer.getAttribute('data-route') === '1') x.searchParams.set('style', 'mansfield');
      url = x.toString();
    } catch (e) {}
    frame.setAttribute('src', url);
  }
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
    // Sent a moment later, so a place link's focus-place (sent right after open()) is already on its way.
    if (routeOn) setTimeout(sendView, 0);
    tellCard(true);
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
    if (routeOn) sync(true);
    tellCard(false);
  }
  // Inside the home page's pop-up card (?in=sheet): tell it when the map is open, so it hides its close button.
  function tellCard(on) {
    if (window.parent === window || !document.documentElement.classList.contains('in-sheet')) return;
    try { window.parent.postMessage({ type: 'mansfield:sheet-map', open: on }, location.origin); } catch (e) {}
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
    if (!e.data) return;
    if (e.data.type === 'mansfield:focused' && asking) { clearInterval(asking); asking = null; }
    if (e.data.type === 'mansfield:route-ready') onRouteReady(e.data);
    if (e.data.type === 'mansfield:read-about') readAbout(e.data);
    if (e.data.type === 'mansfield:route-walk') onRouteWalk(e.data);
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
    focusPlace(m[1], a.getAttribute('data-place-name') || a.textContent.replace(/\s+/g, ' ').trim());
  }, true);

  // ── Route: numbered stops ─────────────────────────────────────────────
  var routeOn = false;
  var stops = [];          // { listing, name, color, lat, lng, link, section }
  var legs = [];
  var total = 0;
  var stepNow = -2;
  var headOn = false;
  var head = document.getElementById('route-head');
  var headNums = [];
  var article = document.querySelector('.article');
  var titleEl = document.querySelector('.article__header');
  var endCard = null;
  var storySlug = (document.getElementById('article-author-pop') || { getAttribute: function () { return ''; } }).getAttribute('data-slug') ||
                  (/^\/article\/([^/?#]+)/.exec(location.pathname) || [])[1] || '';
  var READER = window.READER || { signedIn: false, csrf: '' };

  function postFrame(msg) { try { frame.contentWindow.postMessage(msg, origin); } catch (e) {} }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  // Real walking times once the map has the real path (mansfield:route-walk), else an estimate.
  function legText(m, secs) {
    if (secs > 0 && secs <= 3 * 3600) return Math.max(1, Math.round(secs / 60)) + ' min walk';
    if (!(secs > 0) && m <= 2500) return Math.max(1, Math.round(m * 1.3 / 80)) + ' min walk';
    var km = m / 1000;
    return (km >= 10 ? Math.round(km) : km.toFixed(1)) + ' km';
  }
  var durations = null;   // seconds per leg, from the real walking path
  function walkMinutes() {
    return durations ? durations.reduce(function (a, s) { return a + s / 60; }, 0)
                     : legs.reduce(function (a, m) { return a + m * 1.3 / 80; }, 0);
  }
  function routeSub() {
    var mins = walkMinutes();
    var sub = stops.length + ' stops · ' + kmText(total);
    if (total <= 10000) sub += ' · about ' + (mins >= 60 ? Math.floor(mins / 60) + ' h ' + (Math.round(mins % 60) ? Math.round(mins % 60) + ' min' : '') : Math.max(1, Math.round(mins)) + ' min') + ' of walking';
    return sub.trim();
  }
  function kmText(m) { var km = m / 1000; return (km >= 10 ? Math.round(km) : km.toFixed(1)) + ' km'; }

  // The places in the story, first mention of each, in reading order: links in
  // the text to a place's page (…/listing/<name>) and place cards (one place,
  // or a swipe row of several) from the same directory.
  var SLUG = /^[A-Za-z0-9][A-Za-z0-9_-]{0,200}$/;
  function cardSlugs(frameEl) {
    var url;
    try { url = new URL(frameEl.getAttribute('src') || '', location.href); } catch (e) { return []; }
    if (url.origin !== origin) return [];
    var m = /^\/embed\/card\/([^/?#]+)\/?$/.exec(url.pathname);
    if (m) return SLUG.test(m[1]) ? [m[1]] : [];
    if (/^\/embed\/stack\/?$/.test(url.pathname)) {
      try {
        var d = (url.searchParams.get('d') || '').replace(/-/g, '+').replace(/_/g, '/');
        while (d.length % 4) d += '=';
        var bytes = atob(d), arr = new Uint8Array(bytes.length);
        for (var i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
        var items = JSON.parse(new TextDecoder('utf-8').decode(arr));
        return (Array.isArray(items) ? items : []).map(function (x) { return x && x.s; }).filter(function (x) { return typeof x === 'string' && SLUG.test(x); });
      } catch (e) { return []; }
    }
    return [];
  }
  function placeLinks() {
    var out = [], seen = {};
    var found = document.querySelectorAll('.article__content a[href], .article__content .article__place iframe');
    Array.prototype.forEach.call(found, function (node) {
      if (node.tagName === 'IFRAME') {
        var fig = node.closest('.article__place');
        var cap = fig && fig.querySelector('figcaption');
        cardSlugs(node).forEach(function (slug) {
          if (seen[slug]) return;
          seen[slug] = true;
          out.push({ listing: slug, name: cap && cardSlugs(node).length === 1 ? cap.textContent.trim() : '', link: fig, card: true });
        });
        return;
      }
      var a = node, url;
      try { url = new URL(a.href); } catch (e) { return; }
      if (url.origin !== origin) return;
      var m = /^\/listing\/([A-Za-z0-9][A-Za-z0-9_-]{0,200})\/?$/.exec(url.pathname);
      if (!m) return;
      var name = a.textContent.replace(/\s+/g, ' ').trim();
      a.setAttribute('data-place-name', name);
      if (seen[m[1]]) return;
      seen[m[1]] = true;
      out.push({ listing: m[1], name: name, link: a });
    });
    return out;
  }

  var wanted = layer.getAttribute('data-route') === '1' ? placeLinks() : [];
  var routeAsk = null;
  function askRoute() {
    if (routeOn || wanted.length < 2 || !phone.matches) return;
    if (routeAsk) clearInterval(routeAsk);
    var tries = 0;
    function send() {
      postFrame({ type: 'mansfield:route', stops: wanted.map(function (w) { return { listing: w.listing, name: w.name }; }) });
      if (++tries > 20 && routeAsk) { clearInterval(routeAsk); routeAsk = null; }   // the map is slow or failed: stay a plain map story
    }
    routeAsk = setInterval(send, 700);
    send();
  }
  if (wanted.length >= 2) frame.addEventListener('load', askRoute);

  function onRouteReady(d) {
    if (routeAsk) { clearInterval(routeAsk); routeAsk = null; }
    if (routeOn) return;
    var got = Array.isArray(d.stops) ? d.stops : [];
    var list = [];
    got.forEach(function (s) {
      var w = wanted.filter(function (x) { return s.listing && x.listing === s.listing; })[0] ||
              wanted.filter(function (x) { return x.name && x.name.toLowerCase() === String(s.name || '').toLowerCase(); })[0];
      if (!w || list.some(function (x) { return x.listing === w.listing; })) return;
      list.push({ listing: w.listing, name: String(s.name || w.name), category: String(s.category || ''),
                  color: /^#[0-9a-f]{3,8}$/i.test(s.color || '') ? s.color : '#e0864a',
                  lat: Number(s.lat), lng: Number(s.lng), link: w.link, card: !!w.card,
                  section: w.card ? w.link : (w.link.closest('p, li, h2, h3, blockquote, figure') || w.link) });
    });
    if (list.length < 2) return;
    stops = list;
    legs = (Array.isArray(d.legs) ? d.legs : []).map(Number);
    total = legs.reduce(function (a, b) { return a + (b || 0); }, 0);
    routeOn = true;
    document.body.classList.add('has-route');
    // For the List button on the map (static/js/map-list.js): just these stops, in order.
    window.MansfieldRoute = { stops: list.map(function (s) { return { listing: s.listing, name: s.name, color: s.color }; }), legs: legs.slice() };
    stops.forEach(function (s, i) {
      s.section.style.setProperty('--route-c', s.color);
      if (s.card) return;
      var b = el('span', 'route-num', String(i + 1));
      b.style.setProperty('--c', s.color);
      b.setAttribute('aria-hidden', 'true');
      s.link.insertBefore(b, s.link.firstChild);
    });
    // Place cards: a number in the corner (a range for a swipe row of several); tap it for the map.
    var cards = [];
    stops.forEach(function (s, i) { if (s.card && cards.indexOf(s.link) < 0) cards.push(s.link); });
    cards.forEach(function (fig) {
      var nums = [];
      stops.forEach(function (s, i) { if (s.link === fig) nums.push(i); });
      var first = stops[nums[0]];
      var b = el('button', 'route-num route-num--card', nums.length > 1 ? (nums[0] + 1) + '–' + (nums[nums.length - 1] + 1) : String(nums[0] + 1));
      b.type = 'button';
      b.style.setProperty('--c', first.color);
      b.setAttribute('aria-label', 'Stop ' + (nums[0] + 1) + ': ' + first.name + ', show on the map');
      b.addEventListener('click', function () { openAt(nums[0]); });
      fig.classList.add('has-route-num');
      fig.insertBefore(b, fig.firstChild);
    });
    buildHead();
    routeCard();
    paintSave();
    checkSaved();
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', function () { sync(true); });
    stepNow = -2;
    sync(true);
    if (openOnLoad) { openOnLoad = false; open(); }
  }

  // The header: a number for each stop (joined by a dotted line, like the
  // map), and under it the name of the stop being read about.
  function buildHead() {
    if (!head) return;
    var row = head.querySelector('.route-head__nums');
    stops.forEach(function (s, i) {
      if (i) row.appendChild(el('span', 'route-head__line'));
      var b = el('button', 'route-head__num', String(i + 1));
      b.type = 'button';
      b.style.setProperty('--c', s.color);
      b.setAttribute('aria-label', 'Stop ' + (i + 1) + ': ' + s.name + ', show on the map');
      b.addEventListener('click', function () { openAt(i); });
      row.appendChild(b);
      headNums.push(b);
    });
    head.querySelector('.route-head__name').addEventListener('click', function () {
      if (stepNow >= 0) openAt(stepNow); else open();
    });
    document.body.classList.add('has-route-head');
  }

  var ticking = false;
  function onScroll() {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(function () { ticking = false; sync(false); });
  }

  // Header or not (it shows once the title has scrolled away, until the
  // "Walk this route" card comes up), and which stop the reader is on: the
  // last stop whose paragraph or card has passed a line a third of the way
  // down the screen below the header.
  function sync(force) {
    if (!routeOn || isOpen) return;
    var h = window.innerHeight;
    var past = titleEl ? titleEl.getBoundingClientRect().bottom < 0 : window.scrollY > 300;
    var atEnd = endCard ? endCard.getBoundingClientRect().top < h * 0.55 : false;
    var on = !!head && past && !atEnd;
    if (on !== headOn || force) {
      headOn = on;
      document.body.classList.toggle('route-head-on', on);
      if (head) head.setAttribute('aria-hidden', on ? 'false' : 'true');
      if (force) sendView();
    }
    var top = head ? (head.offsetHeight || 92) : 0;
    var line = top + (h - top) * 0.34;
    var idx = -1;
    stops.forEach(function (s, i) { if (s.section.getBoundingClientRect().top < line) idx = i; });
    if (idx !== stepNow) setStep(idx);
  }

  // The map has the real walking path: use its distances and times everywhere.
  function onRouteWalk(d) {
    if (!routeOn || !Array.isArray(d.legs) || d.legs.length !== stops.length - 1) return;
    legs = d.legs.map(function (m) { return Math.max(0, Number(m) || 0); });
    durations = Array.isArray(d.durations) && d.durations.length === legs.length ? d.durations.map(function (s) { return Math.max(0, Number(s) || 0); }) : null;
    total = legs.reduce(function (a, b) { return a + b; }, 0);
    if (window.MansfieldRoute) { window.MansfieldRoute.legs = legs.slice(); window.MansfieldRoute.durations = durations ? durations.slice() : null; }
    if (endCard) {
      var sub = endCard.querySelector('.route-card__sub');
      if (sub) sub.textContent = routeSub();
      Array.prototype.forEach.call(endCard.querySelectorAll('.route-card__leg'), function (n, i) {
        if (i) n.textContent = legText(legs[i - 1] || 0, durations ? durations[i - 1] : 0);
      });
    }
    if (stepNow >= -1) setStep(stepNow);   // the header's route summary
  }

  function setStep(idx) {
    stepNow = idx;
    postFrame({ type: 'mansfield:route-step', index: idx });
    if (!head) return;
    headNums.forEach(function (b, i) {
      b.classList.toggle('is-now', i === idx);
      b.classList.toggle('is-past', i < idx);
      if (i === idx) b.setAttribute('aria-current', 'step'); else b.removeAttribute('aria-current');
    });
    var label = head.querySelector('.route-head__label');
    label.textContent = '';
    if (idx >= 0) {
      label.appendChild(document.createTextNode(stops[idx].name));
      head.querySelector('.route-head__name').setAttribute('aria-label', 'Show ' + stops[idx].name + ' on the map');
    } else {
      label.appendChild(document.createTextNode(total <= 10000 ? 'Walking route' : 'Route'));
      label.appendChild(el('small', '', ' · ' + stops.length + ' stops · ' + kmText(total)));
      head.querySelector('.route-head__name').setAttribute('aria-label', 'Show the route on the map');
    }
    // Keep the current number in view when there are more than fit.
    var cur = headNums[Math.max(0, idx)], row = head.querySelector('.route-head__nums');
    if (cur && row.scrollWidth > row.clientWidth) row.scrollTo({ left: Math.max(0, cur.offsetLeft - row.clientWidth / 2 + cur.offsetWidth / 2), behavior: 'smooth' });
  }

  // The map is out of sight while reading; it just keeps the current stop ready.
  function sendView() {
    if (!routeOn) return;
    // Full map: the map shows the reader's current stop, unless we just asked it for a particular place.
    if (isOpen) { postFrame({ type: 'mansfield:route-view', view: 'full', focus: !asking }); return; }
    postFrame({ type: 'mansfield:route-view', view: 'strip', pad: { top: Math.round(safeTop() + 60), bottom: 180 } });
  }

  function openAt(i) {
    var s = stops[i];
    if (!s) return;
    open();
    focusPlace(s.listing, s.name);
  }

  // "Read about this" on the map: back to the story, at that stop's paragraph.
  function readAbout(d) {
    var s = stops[Math.round(Number(d.index))];
    if (!s) return;
    close(false);
    setTimeout(function () {
      var y = s.section.getBoundingClientRect().top + (window.scrollY || window.pageYOffset) - (head ? head.offsetHeight || 92 : 0) - 18;
      window.scrollTo({ top: Math.max(0, y), behavior: 'smooth' });
      s.section.classList.remove('route-flash');
      void s.section.offsetWidth;
      s.section.classList.add('route-flash');
      setTimeout(function () { s.section.classList.remove('route-flash'); }, 2000);
    }, 480);
  }

  // A small drawing of the route (no map tiles needed) for the end card.
  function preview() {
    var W = 340, H = 150, P = 26, PB = 44;   // more room at the bottom for the "See it on the map" label
    var lat0 = stops.reduce(function (a, s) { return a + s.lat; }, 0) / stops.length;
    var k = Math.cos(lat0 * Math.PI / 180);
    var xs = stops.map(function (s) { return s.lng * k; }), ys = stops.map(function (s) { return -s.lat; });
    var minX = Math.min.apply(null, xs), maxX = Math.max.apply(null, xs), minY = Math.min.apply(null, ys), maxY = Math.max.apply(null, ys);
    var sc = Math.min((W - 2 * P) / ((maxX - minX) || 1e-9), (H - P - PB) / ((maxY - minY) || 1e-9));
    var ox = (W - (maxX - minX) * sc) / 2, oy = P + ((H - P - PB) - (maxY - minY) * sc) / 2;
    var pts = stops.map(function (s, i) { return [ox + (xs[i] - minX) * sc, oy + (ys[i] - minY) * sc]; });
    var NS = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    svg.setAttribute('aria-hidden', 'true');
    function add(tag, attrs, text) {
      var n = document.createElementNS(NS, tag);
      for (var a in attrs) n.setAttribute(a, attrs[a]);
      if (text) n.textContent = text;
      svg.appendChild(n);
      return n;
    }
    for (var g = 0; g < W; g += 34) add('path', { d: 'M' + g + ' 0V' + H, stroke: 'rgba(255,255,255,0.04)' });
    for (var g2 = 0; g2 < H; g2 += 34) add('path', { d: 'M0 ' + g2 + 'H' + W, stroke: 'rgba(255,255,255,0.04)' });
    var d = pts.map(function (p, i) { return (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1); }).join(' ');
    add('path', { d: d, fill: 'none', stroke: 'rgba(255,255,255,0.85)', 'stroke-width': '3', 'stroke-dasharray': '0 7', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' });
    pts.forEach(function (p, i) {
      add('circle', { cx: p[0], cy: p[1], r: 11, fill: stops[i].color, stroke: '#fff', 'stroke-width': 2 });
      add('text', { x: p[0], y: p[1] + 4.2, 'text-anchor': 'middle', 'font-size': 11.5, 'font-weight': 700, fill: '#fff', 'font-family': 'IBM Plex Sans, -apple-system, sans-serif' }, String(i + 1));
    });
    return svg;
  }

  function mapsLinks() {
    var walk = total <= 10000;
    var groups = [];
    for (var i = 0; i < stops.length - 1; i += 4) groups.push(stops.slice(i, Math.min(stops.length, i + 5)));   // phones take 3 stops between start and end
    return groups.map(function (g, gi) {
      var ll = function (s) { return s.lat + ',' + s.lng; };
      var u = 'https://www.google.com/maps/dir/?api=1&origin=' + encodeURIComponent(ll(g[0])) + '&destination=' + encodeURIComponent(ll(g[g.length - 1]));
      if (g.length > 2) u += '&waypoints=' + encodeURIComponent(g.slice(1, -1).map(ll).join('|'));
      if (walk) u += '&travelmode=walking';
      var first = stops.indexOf(g[0]) + 1, last = stops.indexOf(g[g.length - 1]) + 1;
      return { href: u, label: groups.length > 1 ? 'Stops ' + first + '–' + last + ' in Google Maps' : 'Open the route in Google Maps' };
    });
  }

  function routeCard() {
    var content = document.querySelector('.article__content');
    if (!content) return;
    var walk = total <= 10000;
    var card = el('section', 'route-card');
    card.setAttribute('aria-label', 'The route');
    var pic = el('button', 'route-card__map');
    pic.type = 'button';
    pic.setAttribute('data-mapstory-open', '');
    pic.setAttribute('aria-label', 'See the route on the map');
    pic.appendChild(preview());
    pic.appendChild(el('span', 'route-card__map-label', 'See it on the map'));
    card.appendChild(pic);
    var body = el('div', 'route-card__body');
    body.appendChild(el('h2', 'route-card__h', walk ? 'Walk this route' : 'Follow this route'));
    body.appendChild(el('p', 'route-card__sub', routeSub()));
    var ol = el('ol', 'route-card__list');
    stops.forEach(function (s, i) {
      var li = el('li');
      var b = el('button');
      b.type = 'button';
      b.setAttribute('aria-label', 'Stop ' + (i + 1) + ': ' + s.name + ', show on the map');
      var n = el('span', 'route-num', String(i + 1));
      n.style.setProperty('--c', s.color);
      b.appendChild(n);
      b.appendChild(el('b', '', s.name));
      b.appendChild(el('span', 'route-card__leg', i ? legText(legs[i - 1] || 0) : 'Start'));
      b.addEventListener('click', function () { openAt(i); });
      li.appendChild(b);
      ol.appendChild(li);
    });
    body.appendChild(ol);
    mapsLinks().forEach(function (l) {
      var a = el('a', 'route-card__go', l.label);
      a.href = l.href;
      a.target = '_blank';
      a.rel = 'noopener';
      body.appendChild(a);
    });
    var row = el('div', 'route-card__row');
    var save = el('button', 'route-card__save');
    save.type = 'button';
    save.setAttribute('data-route-save', '');
    save.setAttribute('aria-pressed', 'false');
    save.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.5 3.5h11v17l-5.5-4-5.5 4z"/></svg>';   // fixed icon only
    save.appendChild(el('span', 'route-card__save-label', 'Save route'));
    row.appendChild(save);
    var share = layer.querySelector('.mapstory__share');
    if (share) {
      var sb = el('button', 'map-share route-card__share', 'Share');
      sb.type = 'button';
      sb.setAttribute('data-map-share', share.getAttribute('data-map-share') || '');
      sb.setAttribute('data-title', share.getAttribute('data-title') || '');
      row.appendChild(sb);
    }
    body.appendChild(row);
    var plan = el('a', 'route-card__plan', 'Plan your own walk with AI \u2192');
    plan.href = '/walk';
    body.appendChild(plan);
    card.appendChild(body);
    content.parentNode.insertBefore(card, content.nextSibling);
    endCard = card;
  }

  // ── Saving the route to the reader's profile ──────────────────────────
  var routeSaved = false;
  var saving = false;
  function paintSave() {
    Array.prototype.forEach.call(document.querySelectorAll('[data-route-save]'), function (b) {
      b.hidden = false;
      b.classList.toggle('is-saved', routeSaved);
      b.setAttribute('aria-pressed', routeSaved ? 'true' : 'false');
      b.setAttribute('aria-label', routeSaved ? 'Route saved. Tap to remove it from your profile' : 'Save route');
      b.title = routeSaved ? 'Saved' : 'Save route';
      var label = b.querySelector('.route-card__save-label');
      if (label) label.textContent = routeSaved ? 'Saved' : 'Save route';
    });
  }
  function checkSaved() {
    if (!READER.signedIn || !storySlug) return;
    fetch('/api/routes?article=' + encodeURIComponent(storySlug), { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && typeof d.saved === 'boolean') { routeSaved = d.saved; paintSave(); } })
      .catch(function () {});
  }
  var toastEl = null, toastTimer = null;
  function toast(text, link) {
    if (!toastEl) {
      toastEl = el('div', 'place-toast');
      toastEl.setAttribute('role', 'status');
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = text;
    if (link) {
      var a = el('a', '', link.label);
      a.href = link.href;
      toastEl.appendChild(a);
    }
    toastEl.classList.add('is-on');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove('is-on'); }, 3200);
  }
  function toggleSave(btn) {
    if (!READER.signedIn) {
      if (window.MansfieldSignIn) window.MansfieldSignIn(btn, { title: 'Sign in to save', sub: 'Saving walking routes is for readers with an account.' });
      return;
    }
    if (saving || !storySlug) return;
    saving = true;
    var want = !routeSaved;
    fetch('/api/routes', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'X-CSRF-Token': READER.csrf || '', 'Accept': 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify({ article: storySlug, saved: want, stops: stops.map(function (s) {
        return { name: s.name, listing: s.listing, lat: s.lat, lng: s.lng, color: s.color, category: s.category };
      }) })
    }).then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        saving = false;
        if (!res.ok) { toast(res.d && res.d.error === 'too_many' ? 'You have saved the most routes you can. Remove one first.' : 'Couldn\u2019t save the route. Try again.'); return; }
        routeSaved = !!res.d.saved;
        paintSave();
        if (routeSaved) toast('Route saved to your profile.', { label: 'View', href: '/account#routes' });
        else toast('Route removed from your profile.');
      })
      .catch(function () { saving = false; toast('Couldn\u2019t reach the server. Check your connection and try again.'); });
  }
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-route-save]');
    if (!btn) return;
    e.preventDefault();
    toggleSave(btn);
  });

  // /article/<slug>?map=route (the "Open the map" link on a saved route): straight onto the map.
  var openOnLoad = /(?:^|[?&])map=route(?:&|$)/.test(location.search) && phone.matches;
  if (openOnLoad && wanted.length < 2) { openOnLoad = false; open(); }
  if (openOnLoad) setTimeout(function () { if (openOnLoad) { openOnLoad = false; open(); } }, 7000);   // the route never came: open the plain map

})();
