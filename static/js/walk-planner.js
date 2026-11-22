/*
 * "Plan a walk" (templates/walk.html, walk_planner.py).
 *
 * The reader types or speaks a request ("3 highly rated coffee shops near
 * me"). We ask for their location once (only used for that search, never
 * stored), send the request to /api/walk-plan, and show the walk on the
 * directory app's map (/embed/route-map) in its walking-route mode:
 * numbered pins, the street path, Start walk. The stops, with a short line
 * on why each one fits, are in the list sheet, with Save route, Google Maps
 * and Share. Follow-ups ("make it 5 stops", "swap the second one") change
 * the current walk.
 *
 * Voice: a short recording (MediaRecorder) goes to /api/walk-plan/voice,
 * which turns it into text; the text lands in the box and is sent.
 *
 * Talking to the map uses the same messages as travel route stories
 * (static/js/map-story.js): mansfield:route -> mansfield:route-ready,
 * mansfield:route-view, mansfield:route-walk, mansfield:read-about.
 *
 * Before there's a walk the reader sees an explore map (/embed/explore) they
 * can move around. The map tells us what's on screen (mansfield:view), so
 * "food in this area" searches there; "Draw area" lets them draw a circle
 * (mansfield:draw-circle -> mansfield:circle) and the next walk stays inside
 * it. "New walk" clears the route and goes back to the explore map.
 */
(function () {
  'use strict';

  var root = document.getElementById('walkplan');
  if (!root) return;
  var READER = window.READER || { signedIn: false, csrf: '' };
  var directory = root.getAttribute('data-directory') || '';
  var origin = '';
  try { origin = new URL(directory).origin; } catch (e) {}

  var frame = document.getElementById('walkplan-map');
  var intro = document.getElementById('walkplan-intro');
  var form = document.getElementById('walkplan-ask');
  var input = document.getElementById('walkplan-input');
  var goBtn = document.getElementById('walkplan-go');
  var micBtn = document.getElementById('walkplan-mic');
  var listBtn = document.getElementById('walkplan-list');
  var msg = document.getElementById('walkplan-msg');
  var msgText = msg.querySelector('.walkplan__msg-text');
  var msgStops = msg.querySelector('.walkplan__msg-stops');
  var sheet = document.getElementById('wsheet');
  var sheetTitle = document.getElementById('wsheet-title');
  var sheetSub = document.getElementById('wsheet-sub');
  var sheetList = document.getElementById('wsheet-list');
  var saveBtn = document.getElementById('wsheet-save');
  var mapsLink = document.getElementById('wsheet-maps');
  var shareBtn = document.getElementById('wsheet-share');
  var drawBtn = document.getElementById('walkplan-draw');
  var clearBtn = document.getElementById('walkplan-clear');
  var areaChip = document.getElementById('walkplan-area');
  var areaText = areaChip.querySelector('.walkplan__area-text');

  var plan = null;          // the walk on screen: {query, title, intro, note, stops, key, map, open}
  var legs = null, durations = null;
  var busy = false;
  var here = null;          // {lat, lng} from this visit, or false if refused
  var saved = false;
  var view = null;          // what the map shows: {center: [lng, lat], radius, zoom}
  var circle = null;        // the area the reader drew: {center: [lng, lat], radius, polygon: [[lng, lat], ...]}
  var START = { center: [16.3713, 48.2082], zoom: 13.5 };   // Vienna, until the reader moves the map

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  // ── Messages from the assistant ───────────────────────────────────────
  var msgTimer = null;
  function say(text, withStops, stay) {
    msgText.textContent = text;
    msgStops.hidden = !withStops;
    msg.hidden = false;
    msg.classList.toggle('is-busy', false);
    clearTimeout(msgTimer);
    if (!stay) msgTimer = setTimeout(function () { msg.hidden = true; }, 12000);
  }
  function thinking(text) {
    msgText.textContent = text;
    msgStops.hidden = true;
    msg.hidden = false;
    msg.classList.add('is-busy');
    clearTimeout(msgTimer);
  }
  msg.querySelector('.walkplan__msg-close').addEventListener('click', function () { msg.hidden = true; });
  msgStops.addEventListener('click', function () { msg.hidden = true; openSheet(); });

  // ── Location (asked once per visit, only when planning) ──────────────
  function getLocation() {
    return new Promise(function (resolve) {
      if (here !== null) return resolve(here || null);
      if (!('geolocation' in navigator)) { here = false; return resolve(null); }
      var done = false;
      var timer = setTimeout(function () { if (!done) { done = true; resolve(null); } }, 9000);
      navigator.geolocation.getCurrentPosition(function (pos) {
        if (done) return;
        done = true;
        clearTimeout(timer);
        here = { lat: pos.coords.latitude, lng: pos.coords.longitude };
        resolve(here);
      }, function () {
        if (done) return;
        done = true;
        clearTimeout(timer);
        here = false;
        resolve(null);
      }, { enableHighAccuracy: false, maximumAge: 120000, timeout: 8000 });
    });
  }

  function needsSignIn(btn) {
    if (READER.signedIn) return false;
    if (window.MansfieldSignIn) window.MansfieldSignIn(btn, { title: 'Sign in to plan a walk', sub: 'Planning walks with AI is for readers with an account.' });
    else location.href = '/account/login?next=/walk';
    return true;
  }

  // ── Asking ────────────────────────────────────────────────────────────
  function ask(query) {
    query = (query || '').replace(/\s+/g, ' ').trim();
    if (busy || query.length < 3) return;
    if (needsSignIn(goBtn)) return;
    busy = true;
    form.classList.add('is-busy');
    input.blur();
    thinking('Planning your walk…');
    getLocation().then(function (where) {
      var body = { query: query };
      if (where) { body.lat = where.lat; body.lng = where.lng; }
      if (view) body.view = { lng: view.center[0], lat: view.center[1], radius: view.radius };
      if (circle) {
        body.circle = { lng: circle.center[0], lat: circle.center[1], radius: circle.radius };
        if (circle.polygon) body.polygon = circle.polygon.map(function (p) { return [+p[0].toFixed(6), +p[1].toFixed(6)]; });
      }
      if (plan) body.previous = { query: plan.query, stops: plan.stops.map(function (s) {
        return { slug: s.slug, name: s.name, category: s.category, group: s.group, lat: s.lat, lng: s.lng, address: s.address };
      }) };
      return fetch('/api/walk-plan', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRF-Token': READER.csrf || '' },
        body: JSON.stringify(body)
      }).then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { return { status: r.status, d: d }; }); });
    }).then(function (res) {
      busy = false;
      form.classList.remove('is-busy');
      var d = res.d || {};
      if (res.status === 401 || d.error === 'sign_in') { msg.hidden = true; READER.signedIn = false; needsSignIn(goBtn); return; }
      if (!d.ok) { say(d.error || 'Something went wrong. Please try again.', false, true); return; }
      if (d.clarify) { say(d.clarify, false, true); input.focus(); return; }
      input.value = '';
      showPlan(d);
    }).catch(function () {
      busy = false;
      form.classList.remove('is-busy');
      say('Couldn’t reach the server. Check your connection and try again.', false, true);
    });
  }

  form.addEventListener('submit', function (e) { e.preventDefault(); ask(input.value); });
  Array.prototype.forEach.call(root.querySelectorAll('.walkplan__idea'), function (b) {
    b.addEventListener('click', function () { input.value = b.textContent; ask(b.textContent); });
  });

  // ── Showing a walk ────────────────────────────────────────────────────
  function showPlan(d) {
    plan = { query: d.query || '', title: d.title || 'Your walk', intro: d.intro || '', note: d.note || '',
             stops: d.stops || [], key: d.key || '', map: d.map || '', open: d.open || '' };
    legs = durations = null;
    saved = false;
    try { history.replaceState(null, '', plan.open || '/walk'); } catch (e) {}
    root.classList.add('has-walk');
    intro.hidden = true;
    listBtn.hidden = false;
    clearBtn.hidden = false;
    input.placeholder = 'Change it: "make it 5 stops", "swap stop 2"…';
    loadMap(plan.map, 'walk');
    renderSheet();
    checkSaved();
    say((plan.intro || plan.title) + (plan.note ? ' ' + plan.note : ''), true);
  }

  // A walk opened from a link (/walk?s=slug,slug&t=Title), e.g. a saved route.
  function showLinked() {
    var q = new URLSearchParams(location.search || '');
    var s = (q.get('s') || '').split(',').filter(function (x) { return /^[A-Za-z0-9][A-Za-z0-9_-]{0,200}$/.test(x); }).slice(0, 25);
    if (s.length < 2 || !origin) return false;
    var title = (q.get('t') || 'Your walk').slice(0, 120);
    plan = { query: title, title: title, intro: '', note: '', key: '', open: location.pathname + location.search,
             stops: s.map(function (slug) { return { slug: slug, name: '', category: '', group: 'other', lat: null, lng: null, why: '' }; }),
             map: directory + '/embed/route-map?' + new URLSearchParams({ s: s.join(','), t: title, style: 'mansfield', mode: 'story' }).toString() };
    root.classList.add('has-walk');
    intro.hidden = true;
    listBtn.hidden = false;
    clearBtn.hidden = false;
    input.placeholder = 'Change it: "make it 5 stops", "swap stop 2"…';
    loadMap(plan.map, 'walk');
    return true;
  }

  // ── Clearing the walk ─────────────────────────────────────────────────
  // Back to the explore map, at the same spot, ready for a new question.
  function clearWalk() {
    plan = null;
    legs = durations = null;
    saved = false;
    closeSheet();
    root.classList.remove('has-walk', 'is-walking', 'has-panel');
    listBtn.hidden = true;
    clearBtn.hidden = true;
    input.value = '';
    input.placeholder = 'e.g. food in this area';
    try { history.replaceState(null, '', '/walk'); } catch (e) {}
    loadMap(exploreUrl(), 'explore');
    say(circle ? 'Walk cleared. Your circle is still there: ask for a new walk inside it.' : 'Walk cleared. Ask for a new walk below.', false);
  }
  clearBtn.addEventListener('click', clearWalk);
  intro.querySelector('.walkplan__intro-close').addEventListener('click', function () { intro.hidden = true; });

  // ── The map ───────────────────────────────────────────────────────────
  var asking = null;
  function post(m) { try { frame.contentWindow.postMessage(m, origin); } catch (e) {} }
  function safeTop() {
    var probe = el('div');
    probe.style.cssText = 'position:fixed;top:0;left:0;width:1px;visibility:hidden;pointer-events:none;height:env(safe-area-inset-top, 0px)';
    document.body.appendChild(probe);
    var h = Math.round(probe.getBoundingClientRect().height) || 0;
    probe.remove();
    return h;
  }
  function exploreUrl() {
    var at = view || START;
    return directory + '/embed/explore?' + new URLSearchParams({
      lng: at.center[0].toFixed(5), lat: at.center[1].toFixed(5), z: (at.zoom || START.zoom).toFixed(2),
      style: 'mansfield', mode: 'story' }).toString();
  }
  function loadMap(url, kind) {
    if (!url || !origin) return;
    try {
      var u = new URL(url);
      if (u.origin !== origin) return;
      u.searchParams.set('safe', String(safeTop()));
      url = u.toString();
    } catch (e) { return; }
    frame.hidden = false;
    root.classList.toggle('is-exploring', kind === 'explore');
    root.classList.remove('has-panel');
    setDrawBtn(false);
    frame.onload = function () {
      if (asking) clearInterval(asking);
      asking = null;
      post({ type: 'mansfield:watch-view' });              // tell us what's on screen as the reader moves
      sendInsets();
      if (circle && circle.polygon) post({ type: 'mansfield:area-set', polygon: circle.polygon });
      else if (circle) post({ type: 'mansfield:circle-set', center: circle.center, radius: circle.radius });
      if (kind !== 'walk' || !plan) return;
      var tries = 0;
      var stops = plan.stops.map(function (s) { return { listing: s.slug, name: s.name }; });
      function send() {
        post({ type: 'mansfield:route', stops: stops });
        if (++tries > 20 && asking) { clearInterval(asking); asking = null; }
      }
      asking = setInterval(send, 700);
      send();
    };
    frame.src = url;
  }

  // Tell the map how much of its top our buttons cover, so a place card it opens isn't hidden behind them.
  var toolsEl = root.querySelector('.walkplan__tools');
  function sendInsets() {
    requestAnimationFrame(function () {
      var shown = toolsEl && getComputedStyle(toolsEl).display !== 'none';
      post({ type: 'mansfield:insets', top: shown ? Math.round(toolsEl.getBoundingClientRect().bottom + 6) : 0 });
    });
  }
  window.addEventListener('resize', sendInsets);

  // ── The search area (map view or a drawn circle) ──────────────────────
  function setDrawBtn(on) {
    drawBtn.classList.toggle('is-on', on);
    drawBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    drawBtn.querySelector('span').textContent = on ? 'Drag on the map' : (circle ? 'Redraw area' : 'Draw area');
  }
  function paintArea() {
    areaChip.hidden = !circle;
    if (circle) {
      var across = 2 * circle.radius;
      areaText.textContent = 'Your area · ' + (across >= 1000 ? (across / 1000).toFixed(1) + ' km' : across + ' m');
      areaChip.title = 'The walk only uses places inside the area you drew';
    }
    setDrawBtn(drawBtn.classList.contains('is-on'));
    sendInsets();
  }
  // The map answers mansfield:draw {on}. If it doesn't, it's an older map that can't draw yet.
  var drawAck = null, areaAt = 0;
  drawBtn.addEventListener('click', function () {
    var on = !drawBtn.classList.contains('is-on');
    post({ type: 'mansfield:draw-circle', on: on });
    setDrawBtn(on);
    intro.hidden = true;
    clearTimeout(drawAck);
    if (!on) { msg.hidden = true; return; }
    say('The map is locked. Draw round the area with your finger, like a pencil, and lift to finish. (A single tap makes a 500 m circle.)', false, true);
    drawAck = setTimeout(function () {
      setDrawBtn(false);
      say('Drawing isn’t available on this map yet. Please try again in a few minutes.', false);
    }, 2500);
  });
  areaChip.querySelector('.walkplan__area-clear').addEventListener('click', function () {
    circle = null;
    post({ type: 'mansfield:circle-clear' });
    paintArea();
    say('Area removed. Walks will use what’s on the map again.', false);
  });

  window.addEventListener('message', function (e) {
    if (e.source !== frame.contentWindow || e.origin !== origin || !e.data) return;
    var t = e.data.type;
    if (t === 'mansfield:view') {
      var c = e.data.center, r = Number(e.data.radius), z = Number(e.data.zoom);
      if (Array.isArray(c) && c.length === 2 && isFinite(c[0]) && isFinite(c[1]) && r > 0) {
        view = { center: [Number(c[0]), Number(c[1])], radius: Math.round(r), zoom: isFinite(z) ? z : START.zoom };
      }
      if (e.data.byUser && !plan) intro.hidden = true;   // they're looking around: get the welcome out of the way
      return;
    }
    if (t === 'mansfield:area' || t === 'mansfield:circle') {
      // The map sends "area" (the drawn shape) and, for older pages, "circle" right after it: keep the shape.
      if (t === 'mansfield:circle' && circle && circle.polygon && Date.now() - areaAt < 2000) return;
      var cc = e.data.center, cr = Number(e.data.radius), poly = Array.isArray(e.data.polygon) ? e.data.polygon : null;
      circle = (Array.isArray(cc) && cc.length === 2 && cr > 0) ? { center: [Number(cc[0]), Number(cc[1])], radius: Math.round(cr) } : null;
      if (circle && poly && poly.length >= 4 && poly.length <= 400) circle.polygon = poly.map(function (p) { return [Number(p[0]), Number(p[1])]; });
      if (t === 'mansfield:area') areaAt = Date.now();
      paintArea();
      if (circle) { say('Area set. Now ask, e.g. “Search this area for food”. The walk stays inside what you drew.', false); input.focus(); }
      return;
    }
    if (t === 'mansfield:draw') { clearTimeout(drawAck); setDrawBtn(!!e.data.on); return; }
    if (t === 'mansfield:panel') {   // a full-screen listing is open on the map: get our buttons out of its way
      root.classList.toggle('has-panel', !!e.data.open);
      if (e.data.open) msg.hidden = true;
      return;
    }
    if (!plan) return;
    if (t === 'mansfield:route-ready') {
      if (asking) { clearInterval(asking); asking = null; }
      // Fill in what a linked walk didn't know yet (names, places) from the map.
      (e.data.stops || []).forEach(function (s) {
        var mine = plan.stops.filter(function (x) { return x.slug === s.listing; })[0];
        if (!mine) return;
        if (!mine.name) mine.name = String(s.name || '');
        if (!mine.category) mine.category = String(s.category || '').replace(/_/g, ' ');
        if (mine.lat === null || mine.lat === undefined) { mine.lat = Number(s.lat); mine.lng = Number(s.lng); }
        if (!mine.color && /^#[0-9a-f]{3,8}$/i.test(s.color || '')) mine.color = s.color;
      });
      legs = Array.isArray(e.data.legs) ? e.data.legs.map(Number) : null;
      post({ type: 'mansfield:route-view', view: 'full' });
      renderSheet();
      if (!plan.key) checkSaved();
    } else if (t === 'mansfield:route-walk') {
      legs = Array.isArray(e.data.legs) ? e.data.legs.map(Number) : legs;
      durations = Array.isArray(e.data.durations) ? e.data.durations.map(Number) : null;
      renderSheet();
    } else if (t === 'mansfield:read-about') {
      openSheet(Math.round(Number(e.data.index)));
    } else if (t === 'mansfield:walk') {
      root.classList.toggle('is-walking', !!e.data.on);
      sendInsets();   // Start walk is on: the map's walk banner takes the top
      if (e.data.on) msg.hidden = true;
    }
  });

  // ── The list of stops ─────────────────────────────────────────────────
  function legText(i) {
    var m = legs && legs[i - 1], s = durations && durations[i - 1];
    if (s > 0) return Math.max(1, Math.round(s / 60)) + ' min walk';
    if (m > 0) return m <= 2500 ? Math.max(1, Math.round(m * 1.3 / 80)) + ' min walk' : (m / 1000).toFixed(1) + ' km';
    return '';
  }
  function mapsLinks() {
    var pts = plan.stops.filter(function (s) { return s.lat !== null && s.lat !== undefined && !isNaN(s.lat); });
    if (pts.length < 2) return '';
    var part = pts.slice(0, 5);   // phones take 3 stops between the start and the end
    var ll = function (s) { return s.lat + ',' + s.lng; };
    var u = 'https://www.google.com/maps/dir/?api=1&origin=' + encodeURIComponent(ll(part[0])) + '&destination=' + encodeURIComponent(ll(part[part.length - 1]));
    if (part.length > 2) u += '&waypoints=' + encodeURIComponent(part.slice(1, -1).map(ll).join('|'));
    return u + '&travelmode=walking';
  }
  function renderSheet() {
    if (!plan) return;
    sheetTitle.textContent = plan.title;
    sheetSub.textContent = plan.intro || (plan.stops.length + ' stops');
    sheetList.innerHTML = '';
    plan.stops.forEach(function (s, i) {
      var li = el('li', 'wsheet__stop');
      var num = el('span', 'route-num wsheet__num', String(i + 1));
      num.style.setProperty('--c', s.color || '#2f9e7e');
      li.appendChild(num);
      var body = el('div', 'wsheet__body');
      var top = el('div', 'wsheet__top');
      top.appendChild(el('b', 'wsheet__name', s.name || 'Stop ' + (i + 1)));
      var leg = i ? legText(i) : 'Start';
      if (leg) top.appendChild(el('span', 'wsheet__leg', leg));
      body.appendChild(top);
      var meta = [];
      if (s.rating) meta.push('★ ' + Number(s.rating).toFixed(1));
      if (s.category) meta.push(s.category);
      if (meta.length) body.appendChild(el('div', 'wsheet__meta', meta.join(' · ')));
      if (s.why) body.appendChild(el('p', 'wsheet__why', s.why));
      var show = el('button', 'wsheet__show', 'Show on map');
      show.type = 'button';
      show.addEventListener('click', function () {
        closeSheet();
        post({ type: 'mansfield:focus-place', listing: s.slug, name: s.name });
      });
      body.appendChild(show);
      li.appendChild(body);
      sheetList.appendChild(li);
    });
    var gm = mapsLinks();
    mapsLink.hidden = !gm;
    if (gm) mapsLink.href = gm;
    paintSave();
  }
  function openSheet(highlight) {
    if (!plan) return;
    renderSheet();
    sheet.classList.add('is-open');
    sheet.setAttribute('aria-hidden', 'false');
    var items = sheetList.children;
    Array.prototype.forEach.call(items, function (li, i) { li.classList.toggle('is-hl', i === highlight); });
    if (highlight >= 0 && items[highlight]) setTimeout(function () { items[highlight].scrollIntoView({ block: 'center' }); }, 250);
  }
  function closeSheet() {
    sheet.classList.remove('is-open');
    sheet.setAttribute('aria-hidden', 'true');
  }
  sheet.addEventListener('click', function (e) { if (e.target.closest('[data-wsheet-close]')) closeSheet(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeSheet(); });
  listBtn.addEventListener('click', function () { openSheet(-1); });

  // ── Save and share ────────────────────────────────────────────────────
  function paintSave() {
    saveBtn.classList.toggle('is-saved', saved);
    saveBtn.setAttribute('aria-pressed', saved ? 'true' : 'false');
    saveBtn.querySelector('span').textContent = saved ? 'Saved' : 'Save route';
  }
  function keyFor(slugs) {
    // Same key the server makes (walk: + the first 16 hex of SHA-1 of "a,b,c"), so a linked walk can be saved too.
    if (!window.crypto || !crypto.subtle || !window.TextEncoder) return Promise.resolve('');
    return crypto.subtle.digest('SHA-1', new TextEncoder().encode(slugs)).then(function (buf) {
      return 'walk:' + Array.prototype.map.call(new Uint8Array(buf), function (b) { return ('0' + b.toString(16)).slice(-2); }).join('').slice(0, 16);
    }).catch(function () { return ''; });
  }
  function ensureKey() {
    if (plan.key) return Promise.resolve(plan.key);
    return keyFor(plan.stops.map(function (s) { return s.slug; }).join(',')).then(function (k) { plan.key = k; return k; });
  }
  function checkSaved() {
    if (!READER.signedIn || !plan) return;
    ensureKey().then(function (key) {
      if (!key) return;
      fetch('/api/routes?article=' + encodeURIComponent(key), { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d && typeof d.saved === 'boolean') { saved = d.saved; paintSave(); } })
        .catch(function () {});
    });
  }
  var saving = false;
  saveBtn.addEventListener('click', function () {
    if (!plan || saving) return;
    if (needsSignIn(saveBtn)) return;
    var ready = plan.stops.filter(function (s) { return s.lat !== null && s.lat !== undefined && s.name; });
    if (ready.length < 2) { say('One moment, the map is still loading the stops.', false); return; }
    saving = true;
    ensureKey().then(function (key) {
      return fetch('/api/routes', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRF-Token': READER.csrf || '' },
        body: JSON.stringify({ article: key, title: plan.title, saved: !saved, stops: ready.map(function (s) {
          return { name: s.name, listing: s.slug, lat: s.lat, lng: s.lng, color: s.color || '#2f9e7e', category: s.category || '' };
        }) })
      });
    }).then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        saving = false;
        if (!res.ok) { say('Couldn’t save the route. Please try again.', false); return; }
        saved = !!res.d.saved;
        paintSave();
        say(saved ? 'Saved to your profile, under Routes.' : 'Removed from your profile.', false);
      })
      .catch(function () { saving = false; say('Couldn’t reach the server. Please try again.', false); });
  });
  shareBtn.addEventListener('click', function () {
    if (!plan) return;
    var url = window.location.origin + (plan.open || '/walk');
    if (window.MansfieldNative && window.MansfieldNative.share && window.MansfieldNative.share(url, plan.title)) return;
    if (navigator.share) { navigator.share({ title: plan.title, url: url }).catch(function () {}); return; }
    try { navigator.clipboard.writeText(url); say('Link copied.', false); } catch (e) { say(url, false, true); }
  });

  // ── Speaking ──────────────────────────────────────────────────────────
  var rec = null, chunks = [], recTimer = null, stream = null;
  var canRecord = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
  micBtn.hidden = !canRecord;
  function pickType() {
    var types = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'];
    for (var i = 0; i < types.length; i++) { try { if (MediaRecorder.isTypeSupported(types[i])) return types[i]; } catch (e) {} }
    return '';
  }
  function stopTracks() { if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; } }
  function stopRecording() { clearTimeout(recTimer); if (rec && rec.state !== 'inactive') rec.stop(); }
  micBtn.addEventListener('click', function () {
    if (busy) return;
    if (rec && rec.state === 'recording') { stopRecording(); return; }
    if (needsSignIn(micBtn)) return;
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (s) {
      stream = s;
      var type = pickType();
      rec = type ? new MediaRecorder(s, { mimeType: type }) : new MediaRecorder(s);
      chunks = [];
      rec.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
      rec.onstop = function () {
        stopTracks();
        micBtn.classList.remove('is-recording');
        micBtn.setAttribute('aria-label', 'Speak your walk');
        var blob = new Blob(chunks, { type: (rec.mimeType || type || 'audio/webm').split(';')[0] });
        rec = null;
        if (blob.size < 800) { say('I didn’t catch that. Tap the mic, speak, then tap it again.', false); return; }
        sendVoice(blob);
      };
      rec.start();
      micBtn.classList.add('is-recording');
      micBtn.setAttribute('aria-label', 'Stop recording');
      say('Listening… tap the mic again when you’re done.', false, true);
      recTimer = setTimeout(stopRecording, 20000);   // keep it short
    }).catch(function () {
      say('The microphone is off for this page. Allow it in your settings, or type instead.', false, true);
    });
  });
  function sendVoice(blob) {
    busy = true;
    form.classList.add('is-busy');
    thinking('Getting that down…');
    var fd = new FormData();
    var ext = /mp4/.test(blob.type) ? 'm4a' : /ogg/.test(blob.type) ? 'ogg' : 'webm';
    fd.append('audio', blob, 'speech.' + ext);
    fetch('/api/walk-plan/voice', { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': READER.csrf || '' }, body: fd })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { return { status: r.status, d: d }; }); })
      .then(function (res) {
        busy = false;
        form.classList.remove('is-busy');
        if (!res.d.ok) { say(res.d.error || 'Couldn’t understand that. Please try again.', false, true); return; }
        input.value = res.d.text;
        ask(res.d.text);
      })
      .catch(function () {
        busy = false;
        form.classList.remove('is-busy');
        say('Couldn’t reach the server. Please try again or type instead.', false, true);
      });
  }

  // Opened with a walk in the link (a saved route)? If not, start on the explore map.
  if (!showLinked()) loadMap(exploreUrl(), 'explore');
})();
