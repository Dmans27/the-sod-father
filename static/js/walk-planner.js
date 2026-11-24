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
  var msgSave = document.getElementById('walkplan-msg-save');
  var msgShare = document.getElementById('walkplan-msg-share');
  var msgPost = document.getElementById('walkplan-msg-post');   // "Post to feed" after a finished walk (community.js)
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
    if (msgPost) msgPost.hidden = true;
    msgStops.hidden = !withStops;
    if (msgSave) msgSave.hidden = msgShare.hidden = !(withStops && plan);   // a fresh walk: Save and Share right here
    msg.hidden = false;
    msg.classList.toggle('is-busy', false);
    clearTimeout(msgTimer);
    if (!stay) msgTimer = setTimeout(function () { msg.hidden = true; }, 12000);
  }
  function thinking(text) {
    msgText.textContent = text;
    msgStops.hidden = true;
    if (msgSave) msgSave.hidden = msgShare.hidden = true;
    msg.hidden = false;
    msg.classList.add('is-busy');
    clearTimeout(msgTimer);
  }
  msg.querySelector('.walkplan__msg-close').addEventListener('click', function () { msg.hidden = true; });
  msgStops.addEventListener('click', function () { msg.hidden = true; openSheet(); });
  if (msgSave) {
    msgSave.addEventListener('click', function () { saveBtn.click(); });
    msgShare.addEventListener('click', function () { shareBtn.click(); });
  }
  if (msgPost) msgPost.addEventListener('click', function () { postToFeed(plan, 'walk'); });

  // ── Community feed: post a walk for other walkers in the city (static/js/community.js) ──
  function postToFeed(walk, source) {
    if (!walk || !window.MansfieldCommunity) return;
    var stops = (walk.stops || []).filter(function (x) { return x && x.slug; })
      .map(function (x, i) { return { slug: x.slug, name: x.name || ('Stop ' + (i + 1)) }; });
    if (stops.length < 2) return;
    window.MansfieldCommunity.compose({ title: walk.title || '', stops: stops, source: source,
      onPosted: function () { msg.hidden = true; } });
  }
  // A walk that ended with every stop reached (or at least 2 of them) goes on the walker's
  // profile for their friends to see (profiles.py). Once per walk on screen.
  function logWalk(finished, reached) {
    if (!READER.signedIn || !plan || !plan.stops || plan.stops.length < 2 || plan.navigate) return;
    if (!(finished || reached >= 2) || plan.logged === (finished ? 2 : 1) || plan.logged === 2) return;
    plan.logged = finished ? 2 : 1;
    fetch('/api/walk-history', { method: 'POST', credentials: 'same-origin', keepalive: true,
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRF-Token': READER.csrf || '' },
      body: JSON.stringify({ title: plan.title || plan.query || 'A walk', walk: plan.key || '', finished: finished, reached: reached,
                             stops: plan.stops.map(function (x) { return { slug: x.slug, name: x.name }; }) }) }).catch(function () {});
  }
  function offerPost() {
    if (!plan || !plan.stops || plan.stops.length < 2 || !window.MansfieldCommunity) return;
    say('You made it: all ' + plan.stops.length + ' stops! Share this walk with walkers in ' + (window.FEED_CITY || 'your city') + '?', false, true);
    if (msgPost) msgPost.hidden = false;
  }

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
    setDrawer(false);
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
    b.addEventListener('click', function () { setDrawer(false); input.value = b.textContent; ask(b.textContent); });
  });

  // ── Showing a walk ────────────────────────────────────────────────────
  function showPlan(d) {
    plan = { query: d.query || '', title: d.title || 'Your walk', intro: d.intro || '', note: d.note || '',
             stops: d.stops || [], key: d.key || '', map: d.map || '', open: d.open || '' };
    legs = durations = null;
    saved = false;
    try { history.replaceState(null, '', plan.open || '/walk'); } catch (e) {}
    root.classList.add('has-walk');
    showTab();   // a new walk: the This week tab comes back
    syncIdle();
    intro.hidden = true;
    listBtn.hidden = false;
    clearBtn.hidden = false;
    input.placeholder = 'Change it: "make it 5 stops", "swap stop 2"…';
    loadMap(plan.map, 'walk');
    renderSheet();
    checkSaved();
    say((plan.intro || plan.title) + (plan.note ? ' ' + plan.note : ''), true);
  }

  // A drawn area from a shared link: its middle, and how far its farthest corner is (metres).
  function areaFrom(poly) {
    var pts = poly.map(function (p) { return [Number(p[0]), Number(p[1])]; }).filter(function (p) { return isFinite(p[0]) && isFinite(p[1]); });
    var lng = 0, lat = 0;
    pts.forEach(function (p) { lng += p[0]; lat += p[1]; });
    lng /= pts.length; lat /= pts.length;
    var far = 0;
    pts.forEach(function (p) {
      var dx = (p[0] - lng) * 111320 * Math.cos(lat * Math.PI / 180), dy = (p[1] - lat) * 110540;
      far = Math.max(far, Math.sqrt(dx * dx + dy * dy));
    });
    return { center: [lng, lat], radius: Math.max(50, Math.round(far)), polygon: pts };
  }

  // A walk opened from a link (/walk?s=slug,slug&t=Title), e.g. a saved route.
  // A shared walk (/w/<code>) comes in window.SHARED_WALK, with its names and the area it was drawn in.
  // /walk?go=<listing>&t=<name> is "walk me there": one stop, and the map starts
  // its own navigation from the walker right away (My places' Directions uses it).
  function showLinked(qOverride) {
    var q = qOverride || new URLSearchParams(location.search || '');
    var shared = qOverride ? null : (window.SHARED_WALK || null);
    var go = q.get('go') || '';
    var names = {};
    var s;
    if (go) {
      s = [go];
      names[go] = String(q.get('t') || '');
    } else if (shared && Array.isArray(shared.stops)) {
      s = shared.stops.map(function (x) { names[x.slug] = x.name || ''; return String(x.slug || ''); });
    } else {
      s = (q.get('s') || '').split(',');
    }
    s = s.filter(function (x) { return /^[A-Za-z0-9][A-Za-z0-9_-]{0,200}$/.test(x); }).slice(0, 25);
    if (s.length < (go ? 1 : 2) || !origin) return false;
    var title = go ? 'Walk to ' + (names[go] || 'this place') : String((shared ? shared.title : q.get('t')) || 'Your walk');
    title = title.slice(0, 120);
    if (shared && Array.isArray(shared.area) && shared.area.length >= 4) {
      circle = areaFrom(shared.area);
      paintArea();
    }
    plan = { query: title, title: title, intro: '', note: '', key: '', navigate: !!go,
             open: qOverride ? '/walk?' + q.toString() : location.pathname + location.search,
             share: shared ? '/w/' + shared.code : '',
             stops: s.map(function (slug) { return { slug: slug, name: names[slug] || '', category: '', group: 'other', lat: null, lng: null, why: '' }; }),
             map: directory + '/embed/route-map?' + new URLSearchParams({ s: s.join(','), t: title, style: 'mansfield', mode: 'story' }).toString() };
    if (shared) say(title + ' · ' + s.length + ' stops. Ask to change it, or plan your own.', true);
    root.classList.add('has-walk');
    showTab();   // a new walk: the This week tab comes back
    syncIdle();
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
    showTab();   // a fresh start: the "This week" tab is back
    plan = null;
    syncIdle();
    sendInsets();
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
  intro.querySelector('.walkplan__got-it').addEventListener('click', function () { intro.hidden = true; input.focus(); });
  // The welcome card shows once, on someone's very first visit (remembered on this device).
  function welcome() {
    var KEY = 'mf-walk-welcomed';
    try {
      if (localStorage.getItem(KEY)) return;
      localStorage.setItem(KEY, '1');
    } catch (e) { return; }   // no storage (private mode): skip it rather than show it every time
    intro.hidden = false;
  }

  // ── "My places" (right edge): the reader's saved places in this city ──
  // Opens with the tab (tap it, or pull it to the left); only the × closes it.
  // Fresh from /api/my-places every time it opens (they may have just saved one
  // from the map). From 3 places it's a ranking: arrows reorder it, and the top
  // ones can be shared as a link that opens them on the map in that order.
  var minePull = document.getElementById('walkplan-mine-pull');
  var mine = document.getElementById('walkplan-mine');
  var mineList = document.getElementById('walkplan-mine-list');
  var mineNote = document.getElementById('walkplan-mine-note');
  var mineFoot = document.getElementById('walkplan-mine-foot');
  var mineTitle = document.getElementById('walkplan-mine-title');
  var mineData = null, mineSaveTimer = null;
  var mineView = 'places';   // 'places' (the ranking) or 'routes' (routes they built)
  var myRoutes = null;       // [{id, title, stops: [{slug, name}]}] once loaded
  var building = null;       // the route being built or edited: {id, title, picks: [{slug, name}]}
  var mineTabs = document.getElementById('walkplan-mine-tabs');
  function setMine(open) {
    if (!mine) return;
    if (open) setDrawer(false);
    root.classList.toggle('mine-open', open);
    mine.setAttribute('aria-hidden', open ? 'false' : 'true');
    minePull.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) loadMine();
    else if (typeof stopGroupPoll === 'function') stopGroupPoll();
  }
  function loadMine() {
    var c = (view && view.center) || START.center;
    mineNote.textContent = 'Loading your places…';
    fetch('/api/my-places?lat=' + c[1].toFixed(5) + '&lng=' + c[0].toFixed(5), { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.json(); })
      .then(function (d) { mineData = d; paintMineView(); })
      .catch(function () { mineNote.textContent = 'Couldn’t load your places. Check your connection and try again.'; });
  }
  // Which view the panel shows: the ranking, the reader's routes, or the route builder.
  function paintMineView() {
    if (mineTabs) {
      mineTabs.hidden = !!(building || group || groupNew);
      Array.prototype.forEach.call(mineTabs.querySelectorAll('[data-mine-view]'), function (b) {
        b.setAttribute('aria-selected', b.getAttribute('data-mine-view') === mineView ? 'true' : 'false');
      });
    }
    stopGroupPoll();
    if (building) paintBuilder();
    else if (groupNew) paintGroupNew();
    else if (group) paintGroup();
    else if (mineView === 'routes') paintRoutes();
    else if (mineView === 'groups') paintGroups();
    else paintMine();
  }
  function routablePlaces() { return ((mineData && mineData.places) || []).filter(function (p) { return p.slug; }); }
  function buildButton(label) {
    var b = el('button', 'walkplan__acct-btn walkplan__acct-btn--ghost', label || '+ Build a route');
    b.type = 'button';
    b.addEventListener('click', function () { building = { id: '', title: '', picks: [] }; paintMineView(); });
    return b;
  }
  function paintMine() {
    var d = mineData || {}, places = d.places || [], from = d.rankFrom || 3;
    mineTitle.textContent = d.city ? 'My ' + d.city : 'My places here';
    mineList.innerHTML = '';
    mineFoot.innerHTML = '';
    if (!d.signedIn) {
      mineNote.innerHTML = 'Save places from the map and build <b>your own ranking</b> to share with friends.';
      var a = el('a', 'walkplan__acct-btn', 'Sign in to start');
      a.href = '/account/login?next=/walk';
      mineFoot.appendChild(a);
      return;
    }
    if (!places.length) {
      mineNote.textContent = '';
      mineList.appendChild(el('li', 'walkplan__mine-empty',
        'Tap a pin on the map and press “+ Save”. Once you’ve saved ' + from + ' places here, your ranking starts and you can share it.'));
      mineFoot.appendChild(buildButton());
      return;
    }
    var ranked = !!d.ranked;
    if (ranked) mineNote.innerHTML = 'Your ranking. Use the arrows to put <b>your favourite</b> first.';
    else {
      var left = from - places.length;
      mineNote.innerHTML = 'Save <b>' + left + ' more place' + (left === 1 ? '' : 's') + '</b> here to start your ranking.';
    }
    places.forEach(function (p, i) {
      var li = el('li', 'walkplan__mine-item');
      li.appendChild(el('span', 'walkplan__mine-rank' + (ranked ? '' : ' is-dot'), ranked ? String(i + 1) : '•'));
      var th = el('span', 'walkplan__mine-thumb');
      if (p.photo) th.style.backgroundImage = 'url("' + String(p.photo).replace(/["\\]/g, '') + '")';
      li.appendChild(th);
      var body = el('span', 'walkplan__mine-body');
      body.appendChild(el('span', 'walkplan__mine-name', p.name));
      var meta = el('span', 'walkplan__mine-meta', (p.category ? p.category + ' · ' : ''));
      if (p.slug) {   // our own walking navigation, from where you are to this place
        var dir = el('a', '', 'Directions');
        dir.href = '/walk?' + new URLSearchParams({ go: p.slug, t: p.name }).toString();
        dir.addEventListener('click', function (e) { e.preventDefault(); walkTo(p); });
        meta.appendChild(dir);
      }
      body.appendChild(meta);
      li.appendChild(body);
      if (ranked) {
        var mv = el('span', 'walkplan__mine-move');
        var up = el('button', '', '▲'), down = el('button', '', '▼');
        up.type = down.type = 'button';
        up.setAttribute('aria-label', 'Move ' + p.name + ' up');
        down.setAttribute('aria-label', 'Move ' + p.name + ' down');
        up.disabled = i === 0;
        down.disabled = i === places.length - 1;
        up.addEventListener('click', function () { moveMine(i, -1); });
        down.addEventListener('click', function () { moveMine(i, 1); });
        mv.appendChild(up); mv.appendChild(down);
        li.appendChild(mv);
      }
      mineList.appendChild(li);
    });
    if (ranked) {
      var shareable = places.filter(function (p) { return p.slug; }).slice(0, 10);
      if (shareable.length >= 2) {
        var b = el('button', 'walkplan__acct-btn');
        b.type = 'button';
        b.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-share"/></svg>';
        b.appendChild(document.createTextNode('Share my top ' + shareable.length));
        b.addEventListener('click', function () { shareMine(shareable, b); });
        mineFoot.appendChild(b);
      }
    }
    mineFoot.appendChild(buildButton());   // saved places or any place from the directory search
  }

  // ── My routes: the ones they built ("Date night") ─────────────────────
  function routeApi(method, url, body) {
    return fetch(url, { method: method, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRF-Token': READER.csrf || '' },
      body: body ? JSON.stringify(body) : undefined })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, status: r.status, d: d }; }); });
  }
  function paintRoutes() {
    var d = mineData || {};
    mineTitle.textContent = 'My routes';
    mineList.innerHTML = '';
    mineFoot.innerHTML = '';
    if (!d.signedIn) {
      mineNote.innerHTML = 'Build your own routes from places you save: a <b>date night</b>, your favourite coffee loop, a tour for friends.';
      var a = el('a', 'walkplan__acct-btn', 'Sign in to start');
      a.href = '/account/login?next=/walk';
      mineFoot.appendChild(a);
      return;
    }
    if (myRoutes === null) {
      mineNote.textContent = 'Loading your routes…';
      fetch('/api/my-routes', { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
        .then(function (r) { return r.json(); })
        .then(function (res) { myRoutes = res.routes || []; if (mineView === 'routes' && !building) paintRoutes(); })
        .catch(function () { mineNote.textContent = 'Couldn’t load your routes. Check your connection and try again.'; });
      return;
    }
    var canBuild = true;   // saved places or any place from the directory search
    if (!myRoutes.length) {
      mineNote.innerHTML = 'Make your own: a <b>date night</b>, your favourite coffee loop, a tour for friends.';
    } else {
      mineNote.textContent = '';
    }
    myRoutes.forEach(function (rt) {
      var li = el('li', 'walkplan__route');
      li.appendChild(el('p', 'walkplan__route-title', rt.title));
      li.appendChild(el('p', 'walkplan__route-stops', rt.stops.length + ' stops · ' + rt.stops.map(function (x) { return x.name; }).join(' → ')));
      var acts = el('div', 'walkplan__route-actions');
      function act(label, cls, fn) {
        var b = el('button', cls || '', label);
        b.type = 'button';
        b.addEventListener('click', function () { fn(b); });
        acts.appendChild(b);
      }
      act('Open on map', 'is-primary', function () { openRoute(rt); });
      act('Share', '', function (b) { shareList(rt.title, rt.stops, b); });
      act('Post to feed', 'is-post', function () { postToFeed(rt, 'route'); });
      act('Plan with friends', '', function () {   // a group map that starts with this route's stops
        groupNew = { title: rt.title, picks: [], stops: rt.stops.map(function (x) { return { slug: x.slug, name: x.name }; }) };
        mineView = 'groups';
        paintMineView();
      });
      act('Edit', '', function () { building = { id: rt.id, title: rt.title, picks: rt.stops.map(function (x) { return { slug: x.slug, name: x.name }; }) }; paintMineView(); });
      act('Delete', 'is-danger', function () {
        if (!window.confirm('Delete “' + rt.title + '”?')) return;
        routeApi('DELETE', '/api/my-routes/' + encodeURIComponent(rt.id)).then(function () {
          myRoutes = myRoutes.filter(function (x) { return x.id !== rt.id; });
          paintRoutes();
        });
      });
      li.appendChild(acts);
      mineList.appendChild(li);
    });
    if (canBuild) mineFoot.appendChild(buildButton());
  }
  function openRoute(rt) {
    setMine(false);
    msg.hidden = true;
    var q = new URLSearchParams({ s: rt.stops.map(function (x) { return x.slug; }).join(','), t: rt.title });
    if (showLinked(q)) {
      try { history.replaceState(null, '', '/walk?' + q.toString()); } catch (e) {}
      say(rt.title + ' · ' + rt.stops.length + ' stops. Tap Start to walk it.', true);
    }
  }

  // A search box for adding places: the directory first, Google for anything it doesn't have
  // (those are imported as listings by /api/place-import, then added like any other place).
  // opts.picked(p): already in?  opts.full(): no room left?  opts.add({slug, name, category}): a promise of true when added.
  function placeSearch(opts) {
    var findLi = el('li', 'walkplan__build-find');
    var find = el('input', 'walkplan__build-search');
    find.type = 'search';
    find.placeholder = 'Search any place to add it';
    find.setAttribute('aria-label', 'Search the directory');
    find.autocomplete = 'off';
    var found = el('ul', 'walkplan__build-results');
    findLi.appendChild(find);
    findLi.appendChild(found);
    var findTimer = null, findSeq = 0;
    find.addEventListener('input', function () {
      clearTimeout(findTimer);
      var q = find.value.trim();
      if (q.length < 2) { found.innerHTML = ''; return; }
      findTimer = setTimeout(function () {
        var seq = ++findSeq, c = (view && view.center) || START.center;
        found.innerHTML = '';
        found.appendChild(el('li', 'walkplan__build-hint', 'Searching…'));
        fetch('/api/place-search?' + new URLSearchParams({ q: q, lat: c[1].toFixed(5), lng: c[0].toFixed(5) }).toString(),
              { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (seq !== findSeq) return;   // a newer search is on its way
            found.innerHTML = '';
            var list = (d && d.places) || [];
            if (!list.length) { found.appendChild(el('li', 'walkplan__build-hint', 'No places found for “' + q + '”.')); return; }
            list.forEach(function (p) {
              var picked = opts.picked(p);
              var li = el('li', 'walkplan__build-hit');
              var txt = el('span', 'walkplan__mine-body');
              var nm = el('span', 'walkplan__mine-name', p.name);
              if (p.google) nm.appendChild(el('span', 'walkplan__build-new', 'New'));   // from Google: becomes a listing when added
              txt.appendChild(nm);
              txt.appendChild(el('span', 'walkplan__mine-meta', [p.category, p.address].filter(Boolean).join(' · ')));
              li.appendChild(txt);
              var add = el('button', '', picked ? 'Added' : '+ Add');
              add.type = 'button';
              add.disabled = picked || opts.full();
              function failed(text) { add.disabled = false; add.textContent = '+ Add'; mineNote.textContent = text; }
              add.addEventListener('click', function () {
                add.disabled = true;
                add.textContent = 'Adding…';
                if (p.slug) {
                  opts.add({ slug: p.slug, name: p.name, category: p.category || '' }).then(function (ok) { if (!ok) failed('Couldn’t add that place. Try again.'); });
                  return;
                }
                fetch('/api/place-import', { method: 'POST', credentials: 'same-origin',
                  headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRF-Token': READER.csrf || '' },
                  body: JSON.stringify({ placeId: p.placeId }) })
                  .then(function (r) { return r.json().catch(function () { return {}; }); })
                  .then(function (d) {
                    if (!d.ok || !d.place) { failed(d.error || 'Couldn’t add that place. Try again.'); return; }
                    opts.add({ slug: d.place.slug, name: d.place.name || p.name, category: d.place.category || p.category || '' })
                      .then(function (ok) { if (!ok) failed('Couldn’t add that place. Try again.'); });
                  })
                  .catch(function () { failed('Couldn’t reach the server. Try again.'); });
              });
              li.appendChild(add);
              found.appendChild(li);
            });
          })
          .catch(function () { if (seq === findSeq) { found.innerHTML = ''; found.appendChild(el('li', 'walkplan__build-hint', 'Couldn’t search right now.')); } });
      }, 300);
    });
    return findLi;
  }

  // ── The route builder: name it, tap places in the order to visit them ──
  function paintBuilder() {
    var places = routablePlaces();
    // Editing a route with stops that aren't in this city's list: keep them in.
    // Places added from the search (or kept from an edited route) that aren't saved favourites.
    building.picks.forEach(function (pk) {
      if (!places.some(function (p) { return p.slug === pk.slug; })) places.push({ slug: pk.slug, name: pk.name, category: pk.category || '', photo: '' });
    });
    mineTitle.textContent = building.id ? 'Edit route' : 'Build a route';
    mineNote.innerHTML = 'Tap places <b>in the order you want to visit them</b>, or search for any place, anywhere. Tap again to take one out.';
    mineList.innerHTML = '';
    mineFoot.innerHTML = '';
    var name = el('input', 'walkplan__build-name');
    name.type = 'text';
    name.maxLength = 60;
    name.placeholder = 'Name it, e.g. Date night';
    name.value = building.title;
    name.setAttribute('aria-label', 'Route name');
    var save = el('button', 'walkplan__acct-btn', building.id ? 'Save changes' : 'Save route');
    save.type = 'button';
    function ready() { save.disabled = !(name.value.trim() && building.picks.length >= 2); }
    name.addEventListener('input', function () { building.title = name.value; ready(); });
    var nameLi = el('li', '');
    nameLi.appendChild(name);
    mineList.appendChild(nameLi);
    // Search the whole directory (and Google, via the directory) to add a place that isn't saved yet.
    mineList.appendChild(placeSearch({
      picked: function (p) { return building.picks.some(function (x) { return (p.slug && x.slug === p.slug) || (p.placeId && x.placeId === p.placeId); }); },
      full: function () { return building.picks.length >= 15; },
      add: function (place) {
        if (!building.picks.some(function (x) { return x.slug === place.slug; })) building.picks.push(place);
        paintBuilder();
        return Promise.resolve(true);
      }
    }));
    places.forEach(function (p) {
      var at = building.picks.map(function (x) { return x.slug; }).indexOf(p.slug);
      var li = el('li', 'walkplan__mine-item is-pick' + (at >= 0 ? ' is-picked' : ''));
      li.setAttribute('role', 'button');
      li.tabIndex = 0;
      li.appendChild(el('span', 'walkplan__mine-rank' + (at >= 0 ? '' : ' is-dot'), at >= 0 ? String(at + 1) : '+'));
      var th = el('span', 'walkplan__mine-thumb');
      if (p.photo) th.style.backgroundImage = 'url("' + String(p.photo).replace(/["\\]/g, '') + '")';
      li.appendChild(th);
      var body = el('span', 'walkplan__mine-body');
      body.appendChild(el('span', 'walkplan__mine-name', p.name));
      if (p.category) body.appendChild(el('span', 'walkplan__mine-meta', p.category));
      li.appendChild(body);
      function toggle() {
        var i = building.picks.map(function (x) { return x.slug; }).indexOf(p.slug);
        if (i >= 0) building.picks.splice(i, 1);
        else if (building.picks.length < 15) building.picks.push({ slug: p.slug, name: p.name });
        paintBuilder();
      }
      li.addEventListener('click', toggle);
      li.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });
      mineList.appendChild(li);
    });

    save.addEventListener('click', function () {
      save.disabled = true;
      routeApi('POST', '/api/my-routes', { id: building.id || undefined, title: name.value, stops: building.picks }).then(function (res) {
        if (!res.ok) { mineNote.textContent = (res.d && res.d.error) || 'Couldn’t save the route. Please try again.'; ready(); return; }
        building = null;
        myRoutes = null;
        mineView = 'routes';
        paintMineView();
      }, function () { mineNote.textContent = 'Couldn’t reach the server. Please try again.'; ready(); });
    });
    var cancel = el('button', 'walkplan__acct-btn walkplan__acct-btn--ghost', 'Cancel');
    cancel.type = 'button';
    cancel.addEventListener('click', function () { building = null; paintMineView(); });
    mineFoot.appendChild(save);
    mineFoot.appendChild(cancel);
    ready();
  }
  // ── With friends: group maps planned together (group_maps.py) ─────────
  // A list of your maps; a form to start one and pick who's coming; and the map itself,
  // where everyone on it can rename it, add friends, and add, remove and reorder stops.
  // Every change saves straight away; while a map is open the page checks for the others'
  // changes every few seconds.
  var groupMaps = null;      // [{id, title, rev, owner, members, stops}] once loaded
  var group = null;          // the open map
  var groupNew = null;       // the "start a map" form: {title, picks: [handles], stops: [{slug, name}]}
  var groupFriends = null;   // your friends (for picking who's coming), once loaded
  var groupPicker = false;   // the open map's "add friends" list is showing
  var groupPoll = null;
  var AV_COLORS = ['#9fe0bd', '#f6c89f', '#b9c8f7', '#f3b2c4', '#c9e59b', '#f1dd8c', '#a9e1e8', '#d6bff2'];
  function avatar(p, cls) {
    var n = 0, h = String(p.handle || p.name || '');
    for (var i = 0; i < h.length; i++) n = (n * 31 + h.charCodeAt(i)) >>> 0;
    var a = el('span', 'walkplan__gm-av' + (cls ? ' ' + cls : ''), p.initial || '?');
    a.style.background = AV_COLORS[n % AV_COLORS.length];
    return a;
  }
  function firstName(p) { return p.me ? 'You' : String(p.name || '').split(' ')[0]; }
  function whoLine(members) {
    var names = members.map(firstName);
    if (names.length <= 3) return names.join(', ').replace(/, ([^,]*)$/, ' and $1');
    return names.slice(0, 2).join(', ') + ' and ' + (names.length - 2) + ' more';
  }
  function loadFriends(then) {
    if (groupFriends) { then(); return; }
    fetch('/api/friends', { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.json(); })
      .then(function (d) { groupFriends = (d && d.friends) || []; then(); })
      .catch(function () { groupFriends = []; then(); });
  }
  function signInFirst(text) {
    mineNote.innerHTML = text;
    var a = el('a', 'walkplan__acct-btn', 'Sign in to start');
    a.href = '/account/login?next=' + encodeURIComponent('/walk?group=new');
    mineFoot.appendChild(a);
  }
  function stopGroupPoll() { clearInterval(groupPoll); groupPoll = null; }
  function startGroupPoll() {
    stopGroupPoll();
    groupPoll = setInterval(function () {
      if (!group || !root.classList.contains('mine-open')) { stopGroupPoll(); return; }
      var a = document.activeElement;
      if (a && mine.contains(a) && a.tagName === 'INPUT') return;   // typing: don't redraw under them
      var id = group.id;
      fetch('/api/group-maps/' + encodeURIComponent(id) + '?rev=' + group.rev, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
        .then(function (r) { return r.json().then(function (d) { return { status: r.status, d: d }; }); })
        .then(function (res) {
          if (!group || group.id !== id) return;
          if (res.status === 404) { group = null; groupMaps = null; paintMineView(); mineNote.textContent = 'That map was deleted, or you were taken off it.'; return; }
          if (res.d && res.d.map) { group = res.d.map; paintGroup(); }
        })
        .catch(function () {});
    }, 6000);
  }
  // One change to the open map; the reply is the map as it now stands.
  function groupChange(method, path, body) {
    var id = group.id;
    return routeApi(method, '/api/group-maps/' + encodeURIComponent(id) + path, body).then(function (res) {
      if (res.d && res.d.verify) return false;   // the verify-your-email card is showing (base.html)
      if (res.status === 404 || (!res.ok && res.d && /isn’t there any more/.test(res.d.error || ''))) {
        group = null; groupMaps = null; paintMineView();
        mineNote.textContent = (res.d && res.d.error) || 'That map isn’t there any more.';
        return false;
      }
      if (!res.ok) { mineNote.textContent = (res.d && res.d.error) || 'Couldn’t save that. Try again.'; return false; }
      if (res.d.map && group && group.id === id) { group = res.d.map; paintGroup(); }
      if (res.d.warning) mineNote.textContent = res.d.warning;
      return true;
    }, function () { mineNote.textContent = 'Couldn’t reach the server. Try again.'; return false; });
  }
  function openGroup(id) {
    group = null;
    groupNew = null;
    mineView = 'groups';
    mineNote.textContent = 'Opening the map…';
    fetch('/api/group-maps/' + encodeURIComponent(id), { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.map) { groupMaps = null; paintMineView(); mineNote.textContent = (d && d.error) || 'Couldn’t open that map.'; return; }
        group = d.map; groupPicker = false; paintMineView();
      })
      .catch(function () { mineNote.textContent = 'Couldn’t reach the server. Try again.'; });
  }
  function showGroupOnMap(g) {
    openRoute({ title: g.title, stops: g.stops });
  }

  // The list of your group maps
  function paintGroups() {
    mineTitle.textContent = 'With friends';
    mineList.innerHTML = '';
    mineFoot.innerHTML = '';
    if (!READER.signedIn) { signInFirst('Plan an outing <b>together</b>: add friends to a map and everyone can add stops.'); return; }
    if (groupMaps === null) {
      mineNote.textContent = 'Loading your group maps…';
      fetch('/api/group-maps', { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
        .then(function (r) { return r.json(); })
        .then(function (d) { groupMaps = (d && d.maps) || []; if (mineView === 'groups' && !group && !groupNew && !building) paintGroups(); })
        .catch(function () { mineNote.textContent = 'Couldn’t load your group maps. Check your connection and try again.'; });
      return;
    }
    mineNote.innerHTML = groupMaps.length ? '' : 'Plan an outing <b>together</b>: start a map, add friends, and everyone can add, remove and reorder stops.';
    groupMaps.forEach(function (g) {
      var li = el('li', 'walkplan__route');
      li.appendChild(el('p', 'walkplan__route-title', g.title));
      var who = el('div', 'walkplan__gm-who');
      var stack = el('span', 'walkplan__gm-stack');
      g.members.slice(0, 5).forEach(function (p) { stack.appendChild(avatar(p, 'is-small')); });
      who.appendChild(stack);
      who.appendChild(el('span', '', whoLine(g.members)));
      li.appendChild(who);
      li.appendChild(el('p', 'walkplan__route-stops', g.stops.length ? g.stops.length + ' stop' + (g.stops.length === 1 ? '' : 's') + ' · ' +
        g.stops.map(function (x) { return x.name; }).join(' → ') : 'No stops yet'));
      var acts = el('div', 'walkplan__route-actions');
      var open = el('button', 'is-primary', 'Open');
      open.type = 'button';
      open.addEventListener('click', function () { group = g; groupPicker = false; paintMineView(); });
      acts.appendChild(open);
      if (g.stops.length >= 2) {
        var show = el('button', '', 'Show on map');
        show.type = 'button';
        show.addEventListener('click', function () { showGroupOnMap(g); });
        acts.appendChild(show);
      }
      li.appendChild(acts);
      mineList.appendChild(li);
    });
    var start = el('button', 'walkplan__acct-btn', '+ Plan with friends');
    start.type = 'button';
    start.addEventListener('click', function () { groupNew = { title: '', picks: [], stops: [] }; paintMineView(); });
    mineFoot.appendChild(start);
    var find = el('a', 'walkplan__acct-btn walkplan__acct-btn--ghost', 'Find friends');
    find.href = '/account/friends';
    mineFoot.appendChild(find);
  }

  // Starting a map: name it and pick who's coming
  function paintGroupNew() {
    mineTitle.textContent = 'Plan with friends';
    mineList.innerHTML = '';
    mineFoot.innerHTML = '';
    if (!READER.signedIn) { signInFirst('Plan an outing <b>together</b>: add friends to a map and everyone can add stops.'); return; }
    mineNote.innerHTML = 'Name your outing and pick <b>who’s coming</b>. Everyone you add can add and change stops.';
    var name = el('input', 'walkplan__build-name');
    name.type = 'text';
    name.maxLength = 60;
    name.placeholder = 'Name it, e.g. Saturday with the crew';
    name.value = groupNew.title;
    name.setAttribute('aria-label', 'Map name');
    var nameLi = el('li', '');
    nameLi.appendChild(name);
    mineList.appendChild(nameLi);
    if (groupNew.stops.length) {
      mineList.appendChild(el('li', 'walkplan__build-hint', 'Starts with ' + groupNew.stops.length + ' stops: ' +
        groupNew.stops.map(function (x) { return x.name; }).join(' → ')));
    }
    var go = el('button', 'walkplan__acct-btn', 'Start the map');
    go.type = 'button';
    function ready() { go.disabled = !name.value.trim(); }
    name.addEventListener('input', function () { groupNew.title = name.value; ready(); });
    var whoLi = el('li', 'walkplan__gm-pick');
    whoLi.appendChild(el('p', 'walkplan__gm-label', 'Who’s coming?'));
    var chips = el('div', 'walkplan__gm-chips');
    whoLi.appendChild(chips);
    mineList.appendChild(whoLi);
    loadFriends(function () {
      if (!groupNew) return;
      chips.innerHTML = '';
      if (!groupFriends.length) {
        var none = el('p', 'walkplan__build-hint', 'No friends yet. Start the map now and add them later, or ');
        var a = el('a', '', 'find friends');
        a.href = '/account/friends';
        none.appendChild(a);
        none.appendChild(document.createTextNode(' first.'));
        chips.appendChild(none);
        return;
      }
      groupFriends.forEach(function (p) {
        var on = groupNew.picks.indexOf(p.handle) >= 0;
        var b = el('button', 'walkplan__gm-chip');
        b.type = 'button';
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
        b.appendChild(avatar(p, 'is-small'));
        b.appendChild(document.createTextNode(p.name));
        b.addEventListener('click', function () {
          var i = groupNew.picks.indexOf(p.handle);
          if (i >= 0) groupNew.picks.splice(i, 1); else groupNew.picks.push(p.handle);
          b.setAttribute('aria-pressed', i >= 0 ? 'false' : 'true');
        });
        chips.appendChild(b);
      });
    });
    go.addEventListener('click', function () {
      go.disabled = true;
      routeApi('POST', '/api/group-maps', { title: name.value, members: groupNew.picks, stops: groupNew.stops }).then(function (res) {
        if (res.d && res.d.verify) { ready(); return; }
        if (!res.ok || !res.d.map) { mineNote.textContent = (res.d && res.d.error) || 'Couldn’t start the map. Try again.'; ready(); return; }
        groupNew = null;
        groupMaps = null;
        group = res.d.map;
        groupPicker = false;
        paintMineView();
        if (res.d.warning) mineNote.textContent = res.d.warning;
      }, function () { mineNote.textContent = 'Couldn’t reach the server. Try again.'; ready(); });
    });
    var cancel = el('button', 'walkplan__acct-btn walkplan__acct-btn--ghost', 'Cancel');
    cancel.type = 'button';
    cancel.addEventListener('click', function () { groupNew = null; paintMineView(); });
    mineFoot.appendChild(go);
    mineFoot.appendChild(cancel);
    ready();
  }

  // One open map: everyone on it can change it
  function paintGroup() {
    var g = group;
    mineTitle.textContent = 'With friends';
    mineList.innerHTML = '';
    mineFoot.innerHTML = '';
    mineNote.innerHTML = 'Everyone on this map can add, remove and reorder stops. <b>Changes save for everyone.</b>';
    // Name: saves when you leave the box
    var name = el('input', 'walkplan__build-name');
    name.type = 'text';
    name.maxLength = 60;
    name.value = g.title;
    name.setAttribute('aria-label', 'Map name');
    name.addEventListener('change', function () {
      var t = name.value.trim();
      if (!t) { name.value = g.title; return; }
      if (t !== g.title) groupChange('POST', '', { title: t });
    });
    name.addEventListener('keydown', function (e) { if (e.key === 'Enter') name.blur(); });
    var nameLi = el('li', '');
    nameLi.appendChild(name);
    mineList.appendChild(nameLi);
    // Who's on it
    var peopleLi = el('li', 'walkplan__gm-pick');
    var chips = el('div', 'walkplan__gm-chips');
    g.members.forEach(function (p) {
      var c = el('span', 'walkplan__gm-chip is-member');
      c.appendChild(avatar(p, 'is-small'));
      c.appendChild(document.createTextNode(firstName(p) + (p.owner ? ' · started it' : '')));
      if (g.owner && !p.me) {
        var x = el('button', 'walkplan__gm-x', '×');
        x.type = 'button';
        x.setAttribute('aria-label', 'Take ' + p.name + ' off this map');
        x.addEventListener('click', function () {
          if (!window.confirm('Take ' + p.name + ' off “' + g.title + '”?')) return;
          groupChange('DELETE', '/members/' + encodeURIComponent(p.handle));
        });
        c.appendChild(x);
      }
      chips.appendChild(c);
    });
    var addPeople = el('button', 'walkplan__gm-chip is-add', groupPicker ? 'Done' : '+ Add friends');
    addPeople.type = 'button';
    addPeople.addEventListener('click', function () { groupPicker = !groupPicker; loadFriends(paintGroup); });
    chips.appendChild(addPeople);
    peopleLi.appendChild(chips);
    if (groupPicker && groupFriends) {
      var onMap = g.members.map(function (p) { return p.handle; });
      var left = groupFriends.filter(function (p) { return onMap.indexOf(p.handle) < 0; });
      var pick = el('div', 'walkplan__gm-chips walkplan__gm-more');
      if (!left.length) {
        var none = el('p', 'walkplan__build-hint', groupFriends.length ? 'All your friends are on this map. ' : 'No friends yet. ');
        var a = el('a', '', 'Find friends');
        a.href = '/account/friends';
        none.appendChild(a);
        pick.appendChild(none);
      }
      left.forEach(function (p) {
        var b = el('button', 'walkplan__gm-chip');
        b.type = 'button';
        b.appendChild(avatar(p, 'is-small'));
        b.appendChild(document.createTextNode('+ ' + p.name));
        b.addEventListener('click', function () { b.disabled = true; groupChange('POST', '/members', { handles: [p.handle] }); });
        pick.appendChild(b);
      });
      peopleLi.appendChild(pick);
    }
    mineList.appendChild(peopleLi);
    // Adding stops
    mineList.appendChild(placeSearch({
      picked: function (p) { return g.stops.some(function (x) { return p.slug && x.slug === p.slug; }); },
      full: function () { return g.stops.length >= 15; },
      add: function (place) { return groupChange('POST', '/stops', place); }
    }));
    // The stops, in order
    if (!g.stops.length) mineList.appendChild(el('li', 'walkplan__mine-empty', 'No stops yet. Search above to add the first place.'));
    g.stops.forEach(function (st, i) {
      var li = el('li', 'walkplan__mine-item');
      li.appendChild(el('span', 'walkplan__mine-rank', String(i + 1)));
      var body = el('span', 'walkplan__mine-body');
      body.appendChild(el('span', 'walkplan__mine-name', st.name));
      body.appendChild(el('span', 'walkplan__mine-meta', (st.category ? st.category + ' · ' : '') + 'Added by ' + st.by));
      li.appendChild(body);
      var mv = el('span', 'walkplan__mine-move');
      var up = el('button', '', '▲'), down = el('button', '', '▼');
      up.type = down.type = 'button';
      up.setAttribute('aria-label', 'Move ' + st.name + ' up');
      down.setAttribute('aria-label', 'Move ' + st.name + ' down');
      up.disabled = i === 0;
      down.disabled = i === g.stops.length - 1;
      function move(step) {
        var slugs = g.stops.map(function (x) { return x.slug; }), j = i + step;
        var t = slugs[i]; slugs[i] = slugs[j]; slugs[j] = t;
        var s2 = g.stops.slice(); var t2 = s2[i]; s2[i] = s2[j]; s2[j] = t2;
        g.stops = s2; paintGroup();   // move it now; the reply has the order as everyone sees it
        groupChange('POST', '/order', { slugs: slugs });
      }
      up.addEventListener('click', function () { move(-1); });
      down.addEventListener('click', function () { move(1); });
      mv.appendChild(up); mv.appendChild(down);
      li.appendChild(mv);
      var rm = el('button', 'walkplan__gm-rm', '×');
      rm.type = 'button';
      rm.setAttribute('aria-label', 'Take ' + st.name + ' out');
      rm.addEventListener('click', function () { rm.disabled = true; groupChange('DELETE', '/stops/' + encodeURIComponent(st.slug)); });
      li.appendChild(rm);
      mineList.appendChild(li);
    });
    // Actions
    if (g.stops.length >= 2) {
      var show = el('button', 'walkplan__acct-btn', 'Show on map');
      show.type = 'button';
      show.addEventListener('click', function () { showGroupOnMap(g); });
      mineFoot.appendChild(show);
      var share = el('button', 'walkplan__acct-btn walkplan__acct-btn--ghost', 'Share as a link');
      share.type = 'button';
      share.addEventListener('click', function () { shareList(g.title, g.stops, share); });
      mineFoot.appendChild(share);
    }
    var row = el('div', 'walkplan__gm-row');
    var back = el('button', 'walkplan__acct-btn walkplan__acct-btn--ghost', '← All maps');
    back.type = 'button';
    back.addEventListener('click', function () { group = null; groupMaps = null; paintMineView(); });
    row.appendChild(back);
    var bye = el('button', 'walkplan__acct-btn walkplan__acct-btn--out', g.owner ? 'Delete' : 'Leave');
    bye.type = 'button';
    bye.addEventListener('click', function () {
      var ask = g.owner ? 'Delete “' + g.title + '” for everyone on it?' : 'Leave “' + g.title + '”? You can be added again later.';
      if (!window.confirm(ask)) return;
      var me = g.members.filter(function (p) { return p.me; })[0];
      var req = g.owner ? routeApi('DELETE', '/api/group-maps/' + encodeURIComponent(g.id))
                        : routeApi('DELETE', '/api/group-maps/' + encodeURIComponent(g.id) + '/members/' + encodeURIComponent(me ? me.handle : ''));
      req.then(function (res) {
        if (!res.ok) { mineNote.textContent = (res.d && res.d.error) || 'Couldn’t do that. Try again.'; return; }
        group = null; groupMaps = null; paintMineView();
      });
    });
    row.appendChild(bye);
    mineFoot.appendChild(row);
    startGroupPoll();
  }

  function walkTo(p) {
    setMine(false);
    msg.hidden = true;
    var q = new URLSearchParams({ go: p.slug, t: p.name });
    if (showLinked(q)) { try { history.replaceState(null, '', '/walk?' + q.toString()); } catch (e) {} }
  }
  function moveMine(i, step) {
    var places = mineData.places, j = i + step;
    if (j < 0 || j >= places.length) return;
    var t = places[i]; places[i] = places[j]; places[j] = t;
    paintMine();
    clearTimeout(mineSaveTimer);
    mineSaveTimer = setTimeout(function () {
      fetch('/api/my-places/order', { method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRF-Token': READER.csrf || '' },
        body: JSON.stringify({ keys: places.map(function (p) { return p.key; }) }) }).catch(function () {});
    }, 400);
  }
  function shareMine(list, btn) {
    var who = (mineData.name || 'My').split(' ')[0];
    shareList((who === 'My' ? 'My' : who + '’s') + ' top ' + list.length + (mineData.city ? ' in ' + mineData.city : ''), list, btn);
  }
  // A short link (/w/...) that opens these places on the map in this order, then the share sheet.
  function shareList(title, list, btn) {
    btn.disabled = true;
    fetch('/api/walk-share', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRF-Token': READER.csrf || '' },
      body: JSON.stringify({ title: title, stops: list.map(function (p) { return { slug: p.slug, name: p.name }; }) }) })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        btn.disabled = false;
        if (!d || !d.ok || !d.url) { mineNote.textContent = 'Couldn’t make a link right now. Please try again.'; return; }
        var url = window.location.origin + d.url;
        if (window.MansfieldNative && window.MansfieldNative.share && window.MansfieldNative.share(url, title)) return;
        if (navigator.share) { navigator.share({ title: title, url: url }).catch(function () {}); return; }
        try {
          navigator.clipboard.writeText(url).then(function () { mineNote.innerHTML = '<b>Link copied.</b> Paste it anywhere to share your list.'; },
                                                  function () { mineNote.textContent = url; });
        } catch (e) { mineNote.textContent = url; }
      })
      .catch(function () { btn.disabled = false; mineNote.textContent = 'Couldn’t make a link right now. Please try again.'; });
  }
  if (mine) {
    var minePullX = null;
    minePull.addEventListener('click', function () { setMine(true); });
    minePull.addEventListener('touchstart', function (e) { minePullX = e.touches[0].clientX; }, { passive: true });
    minePull.addEventListener('touchmove', function (e) {
      if (minePullX !== null && minePullX - e.touches[0].clientX > 24) { minePullX = null; setMine(true); }
    }, { passive: true });
    mine.querySelector('.walkplan__drawer-close').addEventListener('click', function () { setMine(false); });
    // "Build a route" in the drawer's From walkers slide: open My places on My routes.
    Array.prototype.forEach.call(document.querySelectorAll('[data-open-routes]'), function (b) {
      b.addEventListener('click', function () { setDrawer(false); mineView = 'routes'; setMine(true); });
    });
    if (mineTabs) Array.prototype.forEach.call(mineTabs.querySelectorAll('[data-mine-view]'), function (b) {
      b.addEventListener('click', function () { mineView = b.getAttribute('data-mine-view'); if (mineView === 'groups') groupMaps = null; paintMineView(); });
    });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') setMine(false); });
  }

  // ── Account panel (top-right button) ─────────────────────────────────
  // Slides in from the right: the whole screen on phones, half of it on iPad.
  var acct = document.getElementById('walkplan-acct');
  if (acct) {
    var meBtn = document.querySelector('.walkplan__me-btn');
    var setAcct = function (open) {
      root.classList.toggle('acct-open', open);
      acct.setAttribute('aria-hidden', open ? 'false' : 'true');
      meBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) { setDrawer(false); setMine(false); }
    };
    meBtn.addEventListener('click', function () { setAcct(true); });
    acct.querySelector('.walkplan__acct-close').addEventListener('click', function () { setAcct(false); });
    document.getElementById('walkplan-acct-back').addEventListener('click', function () { setAcct(false); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') setAcct(false); });
    // Invite friends: the phone's share sheet with your invite page (/u/<handle>, friends.py)
    // when you're signed in, or a link to the walk map when you're not.
    acct.querySelector('[data-invite]').addEventListener('click', function () {
      var handle = this.getAttribute('data-invite') || '';
      var url = window.location.origin + (handle ? '/u/' + encodeURIComponent(handle) : '/walk');
      var text = handle ? 'Add me on \\\\Mansfield and let’s plan a walk together' : 'Plan walks around Vienna with me on \\\\Mansfield';
      if (window.MansfieldNative && window.MansfieldNative.share && window.MansfieldNative.share(url, text)) return;
      if (navigator.share) { navigator.share({ title: text, text: text, url: url }).catch(function () {}); return; }
      try {
        navigator.clipboard.writeText(text + ' ' + url).then(function () { setAcct(false); say('Invite link copied. Paste it to a friend.', false); },
                                                            function () { setAcct(false); say(url, false, true); });
      } catch (e) { setAcct(false); say(url, false, true); }
    });
  }

  // ── "This week" drawer: a tab on the left edge with the week's walks and stories ──
  // Tap the tab (or pull it to the right) to open; only the × closes it (swiping the
  // idea pills or tapping beside it doesn't).
  // The tab stays on the map until "Draw area" is tapped, and comes back with the
  // next walk, "New walk", or when the drawn area is removed.
  var pull = document.getElementById('walkplan-pull');
  var drawer = document.getElementById('walkplan-drawer');
  function setDrawer(open) {
    if (!drawer) return;
    if (open && typeof setMine === 'function') setMine(false);
    root.classList.toggle('drawer-open', open);
    drawer.setAttribute('aria-hidden', open ? 'false' : 'true');
    pull.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  function hideTab() {
    root.classList.add('map-used');
    setDrawer(false);
    setMine(false);
    sendInsets();
  }
  function showTab() { root.classList.remove('map-used'); sendInsets(); }
  if (drawer) {
    var pullX = null;
    pull.addEventListener('click', function () { setDrawer(true); });
    pull.addEventListener('touchstart', function (e) { pullX = e.touches[0].clientX; }, { passive: true });
    pull.addEventListener('touchmove', function (e) {
      if (pullX !== null && e.touches[0].clientX - pullX > 24) { pullX = null; setDrawer(true); }
    }, { passive: true });
    drawer.querySelector('.walkplan__drawer-close').addEventListener('click', function () { setDrawer(false); });
    drawer.querySelector('[data-drawer-close]').addEventListener('click', function () { setDrawer(false); input.focus(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') setDrawer(false); });
  }

  // Walks to try / Good reads: swipe sideways (or tap a dot) to switch. The row takes the height of the section showing.
  var slideBox = document.getElementById('walkplan-slides');
  if (slideBox && slideBox.classList.contains('is-multi')) {
    var slideTrack = slideBox.querySelector('.walkplan__slides-track');
    var slidePages = slideTrack.children;
    var slideDots = slideBox.querySelectorAll('.walkplan__dot');
    var slideAt = 0, slideWait = null;
    var fitSlide = function () { if (slidePages[slideAt]) slideTrack.style.height = slidePages[slideAt].offsetHeight + 'px'; };
    var markSlide = function (i) {
      slideAt = Math.max(0, Math.min(slidePages.length - 1, i));
      for (var k = 0; k < slideDots.length; k++) {
        slideDots[k].classList.toggle('is-on', k === slideAt);
        slideDots[k].setAttribute('aria-selected', k === slideAt ? 'true' : 'false');
      }
      fitSlide();
    };
    slideTrack.addEventListener('scroll', function () {
      clearTimeout(slideWait);
      slideWait = setTimeout(function () {
        var i = Math.round(slideTrack.scrollLeft / Math.max(1, slideTrack.clientWidth));
        if (i !== slideAt) markSlide(i);
      }, 80);
    }, { passive: true });
    Array.prototype.forEach.call(slideDots, function (d, k) {
      d.addEventListener('click', function () {
        markSlide(k);
        slideTrack.scrollTo({ left: k * slideTrack.clientWidth, behavior: 'smooth' });
      });
    });
    window.addEventListener('resize', function () { slideTrack.scrollLeft = slideAt * slideTrack.clientWidth; fitSlide(); });
    if (window.ResizeObserver) {
      var slideWatch = new ResizeObserver(fitSlide);
      Array.prototype.forEach.call(slidePages, function (pg) { slideWatch.observe(pg); });
    }
    fitSlide();
  }

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
        post({ type: 'mansfield:route', stops: stops, navigate: !!plan.navigate });
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
      // side: on phones the account button sits in the map's top-right corner, so the
      // map keeps its route label that far in from both edges.
      // left: on iPad the This week tab sits in the middle of the left edge; while it's
      // showing, the map moves its stop list over so the tab doesn't cover it.
      var tab = document.getElementById('walkplan-pull');
      var tabOn = !!tab && window.innerWidth > 640 && !root.classList.contains('map-used') && !root.classList.contains('is-walking');
      post({ type: 'mansfield:insets', top: shown ? Math.round(toolsEl.getBoundingClientRect().bottom + 6) : 0,
             side: window.innerWidth <= 640 ? 52 : 0, left: tabOn ? 34 : 0 });
    });
  }
  window.addEventListener('resize', function () { if (typeof paintArea === 'function') paintArea(); else sendInsets(); });

  // ── The search area (map view or a drawn circle) ──────────────────────
  function setDrawBtn(on) {
    drawBtn.classList.toggle('is-on', on);
    drawBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    drawBtn.querySelector('span').textContent = on ? 'Drag on the map' : (circle ? (narrow() ? 'Redraw' : 'Redraw area') : 'Draw area');
  }
  // Phones: shorter labels so the buttons and the area chip fit on one row.
  function narrow() { return window.innerWidth < 700; }
  function paintArea() {
    areaChip.hidden = !circle;
    if (circle) {
      var across = 2 * circle.radius;
      areaText.textContent = (narrow() ? '' : 'Your area · ') + (across >= 1000 ? (across / 1000).toFixed(1) + ' km' : across + ' m');
      areaChip.title = 'The walk only uses places inside the area you drew';
    }
    setDrawBtn(drawBtn.classList.contains('is-on'));
    syncIdle();
    sendInsets();
  }
  // A fresh map (no walk, nothing drawn): Stories and Draw area sit centred at the very top.
  function syncIdle() { root.classList.toggle('is-idle', !plan && !circle); }
  // The map answers mansfield:draw {on}. If it doesn't, it's an older map that can't draw yet.
  var drawAck = null, areaAt = 0;
  drawBtn.addEventListener('click', function () {
    var on = !drawBtn.classList.contains('is-on');
    post({ type: 'mansfield:draw-circle', on: on });
    setDrawBtn(on);
    intro.hidden = true;
    clearTimeout(drawAck);
    if (on) hideTab();
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
    showTab();
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
    if (t === 'mansfield:draw') { clearTimeout(drawAck); setDrawBtn(!!e.data.on); if (e.data.on) hideTab(); return; }
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
      makeShare();
      loadFootprints();
    } else if (t === 'mansfield:rate') {   // a footprint rating from the map's "You're at stop 2" card
      var rslug = String(e.data.listing || ''), rsteps = Math.round(Number(e.data.steps));
      if (!window.MansfieldFootprints || !READER.signedIn || !(rsteps >= 0 && rsteps <= 5)) return;
      var rstop = plan && plan.stops.filter(function (x) { return x.slug === rslug; })[0];
      if (!rstop) return;
      window.MansfieldFootprints.rate(rslug, rsteps, { visited: true, walk: plan.key, name: rstop.name })
        .then(sendFootprints, sendFootprints);
    } else if (t === 'mansfield:route-walk') {
      legs = Array.isArray(e.data.legs) ? e.data.legs.map(Number) : legs;
      durations = Array.isArray(e.data.durations) ? e.data.durations.map(Number) : null;
      renderSheet();
    } else if (t === 'mansfield:read-about') {
      var at = Math.round(Number(e.data.index));
      openSheet(at);
      if (plan.stops[at]) track('place', [plan.stops[at]]);
    } else if (t === 'mansfield:walk') {
      root.classList.toggle('is-walking', !!e.data.on);
      if (e.data.on) track('start');
      sendInsets();   // Start walk is on: the map's walk banner takes the top
      if (e.data.on) msg.hidden = true;
      if (!e.data.on && e.data.finished) offerPost();   // reached the last stop: offer to post it to the feed
      if (!e.data.on) logWalk(!!e.data.finished, Number(e.data.reached) || 0);
    }
  });

  // ── Footprints: "worth the walk?" ratings of the stops (static/js/footprints.js) ──
  // The map gets them too, for its stop cards and the "You're at stop 2" card (mansfield:footprints).
  function sendFootprints(repaint) {
    var d = window.MansfieldFootprints && window.MansfieldFootprints.data;
    if (!d || !plan) return;
    var mine = {}, stats = {};
    plan.stops.forEach(function (x) {
      if (d.mine[x.slug]) mine[x.slug] = d.mine[x.slug];
      if (d.stats[x.slug]) stats[x.slug] = d.stats[x.slug];
    });
    post({ type: 'mansfield:footprints', signedIn: !!READER.signedIn, mine: mine, stats: stats });
    if (repaint !== false && sheet.classList.contains('is-open')) renderSheet();
  }
  function loadFootprints() {
    if (!plan || !window.MansfieldFootprints) return;
    window.MansfieldFootprints.load(plan.stops.map(function (x) { return x.slug; })).then(sendFootprints);
  }

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
      var fp = window.MansfieldFootprints;
      var walkers = fp && s.slug ? fp.badgeEl(s.slug) : null;   // "4.6 · 3 walkers" found it worth the walk
      if (meta.length || walkers) {
        var metaEl = el('div', 'wsheet__meta', meta.join(' · '));
        if (walkers) { if (meta.length) metaEl.appendChild(document.createTextNode(' · ')); metaEl.appendChild(walkers); }
        body.appendChild(metaEl);
      }
      if (s.why) body.appendChild(el('p', 'wsheet__why', s.why));
      if (fp && s.slug) body.appendChild(fp.row(s.slug, s.name, { walk: plan.key, onRated: function () { sendFootprints(false); } }));
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

  // ── What readers do with a walk ───────────────────────────────────────
  // Save, share, start and "read about" are counted (no location, no name)
  // so the weekly feed can show what people actually liked.
  function track(kind, stops) {
    if (!plan) return;
    var mine = plan;
    var list = (stops || mine.stops).filter(function (s) { return s.slug; }).map(function (s) { return { slug: s.slug, name: s.name || '' }; });
    ensureKey().then(function (key) {
      var body = JSON.stringify({ kind: kind, walk: key || (mine.share ? mine.share.slice(3) : ''), places: list });
      fetch('/api/walk-event', { method: 'POST', credentials: 'same-origin', keepalive: true,
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': READER.csrf || '' }, body: body }).catch(function () {});
    });
  }

  // ── Save and share ────────────────────────────────────────────────────
  function paintSave() {
    saveBtn.classList.toggle('is-saved', saved);
    saveBtn.setAttribute('aria-pressed', saved ? 'true' : 'false');
    saveBtn.querySelector('span').textContent = saved ? 'Saved' : 'Save route';
    if (msgSave) {
      msgSave.classList.toggle('is-saved', saved);
      msgSave.setAttribute('aria-pressed', saved ? 'true' : 'false');
      msgSave.querySelector('span').textContent = saved ? 'Saved' : 'Save';
    }
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
        track(saved ? 'save' : 'unsave');
        say(saved ? 'Saved to your profile, under Routes.' : 'Removed from your profile.', true);
      })
      .catch(function () { saving = false; say('Couldn’t reach the server. Please try again.', false); });
  });
  // A short link (/w/<code>) that keeps the stops, the title and the drawn
  // area. It's made as soon as a walk is ready, so tapping Share can open the
  // share sheet straight away (phones only allow that right after a tap).
  function makeShare() {
    if (!plan) return Promise.resolve('');
    if (plan.share) return Promise.resolve(plan.share);
    if (!READER.signedIn) return Promise.resolve('');
    var mine = plan;
    if (mine.sharing) return mine.sharing;
    var ready = mine.stops.filter(function (s) { return s.slug; });
    mine.sharing = fetch('/api/walk-share', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRF-Token': READER.csrf || '' },
      body: JSON.stringify({ title: mine.title, stops: ready.map(function (s) { return { slug: s.slug, name: s.name || '' }; }),
                             polygon: circle && circle.polygon ? circle.polygon : null })
    }).then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function (d) { mine.sharing = null; if (d && d.ok && d.url) mine.share = d.url; return mine.share || ''; })
      .catch(function () { mine.sharing = null; return ''; });
    return mine.sharing;
  }
  function shareUrl(url) {
    if (window.MansfieldNative && window.MansfieldNative.share && window.MansfieldNative.share(url, plan.title)) return;
    if (navigator.share) {
      navigator.share({ title: plan.title, url: url }).catch(function (err) {
        if (err && err.name === 'NotAllowedError') copyUrl(url);   // the tap "expired" while the link was made
      });
      return;
    }
    copyUrl(url);
  }
  function copyUrl(url) {
    var done = function () { say('Link copied. Paste it anywhere to share this walk.', true); };
    try { navigator.clipboard.writeText(url).then(done, function () { say(url, false, true); }); } catch (e) { say(url, false, true); }
  }
  shareBtn.addEventListener('click', function () {
    if (!plan) return;
    track('share');
    if (plan.share) { shareUrl(window.location.origin + plan.share); return; }
    makeShare().then(function (u) { shareUrl(window.location.origin + (u || plan.open || '/walk')); });
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

  // /walk?group=<id> (the Friends page) opens that group map; /walk?group=new starts one.
  var groupLink = false;   // opened that way: no first-visit welcome card on top of it
  (function () {
    var g = (new URLSearchParams(location.search || '').get('group') || '').trim();
    var withWho = (new URLSearchParams(location.search || '').get('with') || '').toLowerCase();   // "Plan a map together" on a profile
    if (!g || !mine || !/^(new|[0-9a-f]{16})$/.test(g)) return;
    try { history.replaceState(null, '', '/walk'); } catch (e) {}
    mineView = 'groups';
    groupLink = true;
    if (g === 'new') groupNew = { title: '', picks: /^[a-z0-9_.]{3,20}$/.test(withWho) ? [withWho] : [], stops: [] }; else openGroup(g);
    setMine(true);
  })();
  // Opened with a walk in the link (a saved route)? If not, start on the explore map.
  if (!showLinked()) { loadMap(exploreUrl(), 'explore'); if (!groupLink) welcome(); }
  // /walk?ask=... ("Plan a walk in Chicago" on the stories feed): plan it straight away.
  (function () {
    var asked = (new URLSearchParams(location.search || '').get('ask') || '').trim().slice(0, 200);
    if (!asked || plan) return;
    try { history.replaceState(null, '', '/walk'); } catch (e) {}
    input.value = asked;
    setTimeout(function () { ask(asked); }, 700);
  })();
})();
