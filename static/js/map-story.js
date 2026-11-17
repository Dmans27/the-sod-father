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
 *     the linked places become numbered stops in the order the story first
 *     mentions them. A strip of the map stays at the top while you read and
 *     follows along; a "Walk this route" card closes the story.
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
    try { var x = new URL(src); x.searchParams.set('safe', String(safeTop())); url = x.toString(); } catch (e) {}
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
    if (routeOn) {
      document.body.classList.add('mapstory-closing');   // the strip shrinks back while the story slides up
      clearTimeout(closingTimer);
      closingTimer = setTimeout(function () { document.body.classList.remove('mapstory-closing'); }, 460);
      sync(true);
    }
  }
  var closingTimer = null;

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
  var readingNow = false;
  var stripPx = 0;
  var stripEl = layer.querySelector('.mapstory__strip');
  var article = document.querySelector('.article');

  function postFrame(msg) { try { frame.contentWindow.postMessage(msg, origin); } catch (e) {} }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  function legText(m) {
    if (m <= 2500) return Math.max(1, Math.round(m * 1.3 / 80)) + ' min walk';
    var km = m / 1000;
    return (km >= 10 ? Math.round(km) : km.toFixed(1)) + ' km';
  }
  function kmText(m) { var km = m / 1000; return (km >= 10 ? Math.round(km) : km.toFixed(1)) + ' km'; }

  // The places the story links to, first mention of each, in reading order.
  function placeLinks() {
    var out = [], seen = {};
    Array.prototype.forEach.call(document.querySelectorAll('.article__content a[href]'), function (a) {
      var url;
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
              wanted.filter(function (x) { return x.name.toLowerCase() === String(s.name || '').toLowerCase(); })[0];
      if (!w || list.some(function (x) { return x.link === w.link; })) return;
      list.push({ listing: w.listing, name: String(s.name || w.name), category: String(s.category || ''),
                  color: /^#[0-9a-f]{3,8}$/i.test(s.color || '') ? s.color : '#2f9e7e',
                  lat: Number(s.lat), lng: Number(s.lng), link: w.link,
                  section: w.link.closest('p, li, h2, h3, blockquote, figure') || w.link });
    });
    if (list.length < 2) return;
    stops = list;
    legs = (Array.isArray(d.legs) ? d.legs : []).map(Number);
    total = legs.reduce(function (a, b) { return a + (b || 0); }, 0);
    routeOn = true;
    document.body.classList.add('has-route');
    stripPx = Math.round(safeTop() + 176);
    document.documentElement.style.setProperty('--route-strip', stripPx + 'px');
    stops.forEach(function (s, i) {
      var b = el('span', 'route-num', String(i + 1));
      b.style.setProperty('--c', s.color);
      b.setAttribute('aria-hidden', 'true');
      s.link.insertBefore(b, s.link.firstChild);
      s.section.style.setProperty('--route-c', s.color);
    });
    var prog = stripEl && stripEl.querySelector('.mapstory__prog');
    if (prog) stops.forEach(function () { prog.appendChild(el('i')); });
    var chip = document.querySelector('.mapstory-peek__chip');
    if (chip && chip.lastChild && chip.lastChild.nodeType === 3) chip.lastChild.nodeValue = 'See the route · ' + stops.length + ' stops';
    routeCard();
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', function () { sync(true); });
    stepNow = -2;
    sync(true);
  }

  var ticking = false;
  function onScroll() {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(function () { ticking = false; sync(false); });
  }

  // Strip or not, and which stop the reader is on: the last stop whose
  // paragraph has passed a line a bit above the middle of what's left of the screen.
  function sync(force) {
    if (!routeOn || isOpen) return;
    var cardTop = article ? article.getBoundingClientRect().top : 0;
    var reading = cardTop <= stripPx + 0.5;
    if (reading !== readingNow || force) {
      readingNow = reading;
      document.body.classList.toggle('route-reading', reading);
      sendView();
    }
    var idx = -1;
    if (reading) {
      var line = stripPx + (window.innerHeight - stripPx) * 0.38;
      stops.forEach(function (s, i) { if (s.section.getBoundingClientRect().top < line) idx = i; });
    }
    if (idx !== stepNow) setStep(idx);
  }

  function setStep(idx) {
    stepNow = idx;
    postFrame({ type: 'mansfield:route-step', index: idx });
    stops.forEach(function (s, i) { s.section.classList.toggle('route-now', i === idx); });
    if (!stripEl) return;
    Array.prototype.forEach.call(stripEl.querySelectorAll('.mapstory__prog i'), function (b, i) { b.classList.toggle('is-on', i <= idx); });
    var num = stripEl.querySelector('.mapstory__now .route-num');
    var name = stripEl.querySelector('.mapstory__now-name');
    var small = stripEl.querySelector('.mapstory__now small');
    if (idx >= 0) {
      num.hidden = false;
      num.textContent = String(idx + 1);
      num.style.setProperty('--c', stops[idx].color);
      name.textContent = stops[idx].name;
      small.textContent = '· ' + (idx + 1) + ' of ' + stops.length;
      stripEl.setAttribute('aria-label', 'Open the map at stop ' + (idx + 1) + ', ' + stops[idx].name);
    } else {
      num.hidden = true;
      name.textContent = total <= 10000 ? 'Walking route' : 'Route';
      small.textContent = '· ' + stops.length + ' stops · ' + kmText(total);
      stripEl.setAttribute('aria-label', 'Open the map');
    }
  }

  // Which part of the map the reader can see, so the map keeps the pins in it.
  function sendView() {
    if (!routeOn) return;
    // Full map: the map shows the reader's current stop, unless we just asked it for a particular place.
    if (isOpen) { postFrame({ type: 'mansfield:route-view', view: 'full', focus: !asking }); return; }
    var h = window.innerHeight, top, bottom;
    if (readingNow) {
      top = safeTop() + 12;
      bottom = h - (stripPx - 40);
    } else {
      var hd = document.querySelector('.site-header');
      top = Math.max(safeTop(), hd ? hd.getBoundingClientRect().bottom : 0);
      bottom = h - (article ? article.getBoundingClientRect().top : h * 0.4) + 52;   // + the "See the route" chip along the bottom
    }
    postFrame({ type: 'mansfield:route-view', view: 'strip', pad: { top: Math.round(top), bottom: Math.round(Math.max(0, bottom)) } });
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
      var y = s.section.getBoundingClientRect().top + (window.scrollY || window.pageYOffset) - stripPx - 18;
      window.scrollTo({ top: Math.max(0, y), behavior: 'smooth' });
      s.section.classList.remove('route-flash');
      void s.section.offsetWidth;
      s.section.classList.add('route-flash');
      setTimeout(function () { s.section.classList.remove('route-flash'); }, 2000);
    }, 480);
  }

  // A small drawing of the route (no map tiles needed) for the end card.
  function preview() {
    var W = 340, H = 150, P = 28;
    var lat0 = stops.reduce(function (a, s) { return a + s.lat; }, 0) / stops.length;
    var k = Math.cos(lat0 * Math.PI / 180);
    var xs = stops.map(function (s) { return s.lng * k; }), ys = stops.map(function (s) { return -s.lat; });
    var minX = Math.min.apply(null, xs), maxX = Math.max.apply(null, xs), minY = Math.min.apply(null, ys), maxY = Math.max.apply(null, ys);
    var sc = Math.min((W - 2 * P) / ((maxX - minX) || 1e-9), (H - 2 * P) / ((maxY - minY) || 1e-9));
    var ox = (W - (maxX - minX) * sc) / 2, oy = (H - (maxY - minY) * sc) / 2;
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
    card.appendChild(pic);
    var body = el('div', 'route-card__body');
    body.appendChild(el('h2', 'route-card__h', walk ? 'Walk this route' : 'Follow this route'));
    var mins = legs.reduce(function (a, m) { return a + m * 1.3 / 80; }, 0);
    var sub = stops.length + ' stops · ' + kmText(total);
    if (walk) sub += ' · about ' + (mins >= 60 ? Math.floor(mins / 60) + ' h ' + (Math.round(mins % 60) ? Math.round(mins % 60) + ' min' : '') : Math.max(1, Math.round(mins)) + ' min') + ' of walking';
    body.appendChild(el('p', 'route-card__sub', sub.trim()));
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
    var see = el('button', '', 'See it on the map');
    see.type = 'button';
    see.setAttribute('data-mapstory-open', '');
    row.appendChild(see);
    var share = layer.querySelector('.mapstory__share');
    if (share) {
      var sb = el('button', 'map-share route-card__share', 'Share');
      sb.type = 'button';
      sb.setAttribute('data-map-share', share.getAttribute('data-map-share') || '');
      sb.setAttribute('data-title', share.getAttribute('data-title') || '');
      row.appendChild(sb);
    }
    body.appendChild(row);
    card.appendChild(body);
    content.parentNode.insertBefore(card, content.nextSibling);
  }
})();
