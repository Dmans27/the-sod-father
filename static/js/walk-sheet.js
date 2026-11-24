/*
 * The walk pop-up on the stories feed (templates/_walk_sheet.html).
 *
 * Tapping a "Walks this week" card opens a sheet with a map preview of the
 * route, the walk's name, the Vienna district(s) it's in, how long it takes,
 * how many people have walked it, the stops, and "Map this walk", which opens
 * the walk on the walk map (it doesn't start walking).
 *
 * The preview is the directory's route map (/embed/route-map, an allowed map
 * host only). It answers the same messages as the travel stories' maps:
 *   page -> map  { type: 'mansfield:route', stops: [{listing, name}] }
 *   map  -> page { type: 'mansfield:route-ready', stops: [{address, category, ...}], total }
 *   map  -> page { type: 'mansfield:route-walk', total, duration }   (the real walking path)
 * The district comes from the stops' postcodes (1010 = 1st district ... 1230 = 23rd).
 */
(function () {
  'use strict';

  var sheet = document.getElementById('wpop');
  if (!sheet) return;
  var cards = document.querySelectorAll('.walk-strip__card[data-walk]');
  if (!cards.length) return;

  var HOSTS = (window.MAP_HOSTS || []).map(function (h) { return String(h).toLowerCase(); });
  var DISTRICTS = ['', 'Innere Stadt', 'Leopoldstadt', 'Landstraße', 'Wieden', 'Margareten', 'Mariahilf', 'Neubau',
    'Josefstadt', 'Alsergrund', 'Favoriten', 'Simmering', 'Meidling', 'Hietzing', 'Penzing', 'Rudolfsheim-Fünfhaus',
    'Ottakring', 'Hernals', 'Währing', 'Döbling', 'Brigittenau', 'Floridsdorf', 'Donaustadt', 'Liesing'];

  var $ = function (id) { return document.getElementById(id); };
  var mapBox = $('wpop-map'), panel = sheet.querySelector('.wpop__panel');
  var frame = null, origin = '', asking = null, current = null, realTime = false, lastFocus = null;

  function ordinal(n) {
    var s = (n % 100 >= 11 && n % 100 <= 13) ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] || 'th');
    return n + s;
  }
  function districtText(addresses) {
    var seen = [];
    addresses.forEach(function (a) {
      var m = /\b1(0[1-9]|1\d|2[0-3])0\b/.exec(String(a || ''));
      var n = m ? parseInt(m[1], 10) : 0;
      if (n && seen.indexOf(n) < 0) seen.push(n);
    });
    if (!seen.length) return '';
    if (seen.length === 1) return DISTRICTS[seen[0]] + ' · ' + ordinal(seen[0]) + ' district';
    var parts = seen.slice(0, 3).map(function (n) { return DISTRICTS[n] + ' (' + ordinal(n) + ')'; });
    var last = parts.pop();
    return parts.join(', ') + ' & ' + last;
  }
  function distText(m) {
    m = Math.max(0, Number(m) || 0);
    return m < 1000 ? (Math.round(m / 10) * 10) + ' m' : (m / 1000).toFixed(1) + ' km';
  }
  function minutes(sec) { return Math.max(1, Math.round(sec / 60)) + ' min'; }

  function setWalked(n) {
    n = Math.max(0, parseInt(n, 10) || 0);
    $('wpop-walked').textContent = n ? String(n) : 'New';
    $('wpop-walked-label').textContent = n === 0 ? 'be the first' : (n === 1 ? 'walker' : 'walkers');
  }

  function fillStops(stops, extra) {
    var list = $('wpop-stops');
    list.innerHTML = '';
    stops.forEach(function (s, i) {
      var li = document.createElement('li');
      var num = document.createElement('span');
      num.className = 'wpop__num'; num.textContent = String(i + 1);
      var box = document.createElement('span');
      box.className = 'wpop__stop';
      var b = document.createElement('b'); b.textContent = s.name || '';
      box.appendChild(b);
      var info = extra && extra[i];
      if (info && info.category) {
        var c = document.createElement('span'); c.textContent = String(info.category).replace(/_/g, ' ');
        box.appendChild(c);
      }
      li.appendChild(num); li.appendChild(box);
      list.appendChild(li);
    });
  }

  // ---- The map preview ------------------------------------------------------
  function post(m) { if (frame && origin) { try { frame.contentWindow.postMessage(m, origin); } catch (e) {} } }
  function stopAsking() { if (asking) { clearInterval(asking); asking = null; } }
  function askRoute() {
    stopAsking();
    var tries = 0, wanted = current.stops.map(function (s) { return { listing: s.slug, name: s.name }; });
    function send() {
      post({ type: 'mansfield:route', stops: wanted });
      if (++tries > 20) { stopAsking(); mapBox.classList.add('is-ready'); }   // slow map: show it as it is
    }
    asking = setInterval(send, 700);
    send();
  }
  function loadMap(url) {
    origin = '';
    try {
      var u = new URL(url, location.href);
      if (HOSTS.indexOf(u.host.toLowerCase()) >= 0 && (u.protocol === 'https:' || /^(localhost|127\.0\.0\.1)(:\d+)?$/.test(u.host))) origin = u.origin;
    } catch (e) {}
    if (!origin) return;
    frame = document.createElement('iframe');
    frame.title = 'Map of this walk';
    frame.setAttribute('tabindex', '-1');
    frame.setAttribute('loading', 'eager');
    frame.addEventListener('load', askRoute);
    frame.src = url;
    mapBox.appendChild(frame);
  }
  window.addEventListener('message', function (e) {
    if (!frame || e.source !== frame.contentWindow || e.origin !== origin || !e.data || !current) return;
    if (e.data.type === 'mansfield:route-ready') {
      stopAsking();
      var got = Array.isArray(e.data.stops) ? e.data.stops : [];
      // Fit the whole route in the part of the map that shows (below the hidden route bar), then fade it in.
      post({ type: 'mansfield:route-view', view: 'strip', pad: { top: 64 + 46, bottom: 34 } });
      setTimeout(function () { mapBox.classList.add('is-ready'); }, 450);
      var where = districtText(got.map(function (s) { return s && s.address; }));
      $('wpop-where').hidden = !where;
      $('wpop-where').querySelector('span').textContent = where;
      fillStops(current.stops, got);
      if (!realTime && Number(e.data.total) > 0) {   // a first guess from straight lines, until the real path comes
        var m = Number(e.data.total) * 1.3;
        $('wpop-time').textContent = '≈' + minutes(m / 1.33);
        $('wpop-dist').textContent = distText(m);
      }
    } else if (e.data.type === 'mansfield:route-walk') {
      realTime = true;
      if (Number(e.data.duration) > 0) $('wpop-time').textContent = minutes(Number(e.data.duration));
      if (Number(e.data.total) > 0) $('wpop-dist').textContent = distText(e.data.total);
    }
  });

  // ---- Open and close -------------------------------------------------------
  function open(data) {
    current = data;
    realTime = false;
    $('wpop-tag').textContent = data.tag || '';
    $('wpop-tag').hidden = !data.tag;
    $('wpop-title').textContent = data.title || 'A walk to try';
    $('wpop-blurb').textContent = data.blurb || '';
    $('wpop-blurb').hidden = !data.blurb;
    $('wpop-where').hidden = true;
    $('wpop-time').textContent = '–';
    $('wpop-dist').textContent = '–';
    $('wpop-stopcount').textContent = data.stops.length + ' stops';
    setWalked(data.walked);
    fillStops(data.stops, null);
    var link = String(data.link || '');
    $('wpop-cta').href = /^\/walk(\?|$)/.test(link) ? link : '/walk';
    mapBox.classList.remove('is-ready');
    sheet.querySelector('.wpop__scroll').scrollTop = 0;
    panel.style.removeProperty('--drag');
    sheet.classList.add('is-open');
    sheet.setAttribute('aria-hidden', 'false');
    document.documentElement.classList.add('wpop-open');
    lastFocus = document.activeElement;
    setTimeout(function () { var c = sheet.querySelector('.wpop__close'); if (c) c.focus({ preventScroll: true }); }, 60);
    if (data.map) loadMap(data.map);
  }
  function close() {
    if (!sheet.classList.contains('is-open')) return;
    stopAsking();
    sheet.classList.remove('is-open');
    sheet.setAttribute('aria-hidden', 'true');
    document.documentElement.classList.remove('wpop-open');
    current = null;
    var old = frame;
    frame = null; origin = '';
    setTimeout(function () { if (old && old.parentNode) old.parentNode.removeChild(old); panel.style.removeProperty('--drag'); }, 350);
    if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
  }

  Array.prototype.forEach.call(cards, function (card) {
    card.addEventListener('click', function (e) {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.button > 0) return;   // new tab: the plain walk link
      var data;
      try { data = JSON.parse(card.getAttribute('data-walk')); } catch (err) { return; }
      if (!data || !Array.isArray(data.stops) || data.stops.length < 2) return;
      e.preventDefault();
      open(data);
    });
  });
  sheet.querySelectorAll('[data-wpop-close]').forEach(function (b) { b.addEventListener('click', close); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });

  // Pull the sheet down by its top (the map) to close it.
  var startY = null;
  mapBox.addEventListener('touchstart', function (e) {
    if (e.target.closest('.wpop__close')) return;
    startY = e.touches[0].clientY;
    sheet.classList.add('is-dragging');
  }, { passive: true });
  mapBox.addEventListener('touchmove', function (e) {
    if (startY === null) return;
    var dy = Math.max(0, e.touches[0].clientY - startY);
    panel.style.setProperty('--drag', dy + 'px');
  }, { passive: true });
  function endDrag(e) {
    if (startY === null) return;
    var end = e.changedTouches && e.changedTouches[0] ? e.changedTouches[0].clientY : startY;
    sheet.classList.remove('is-dragging');
    if (end - startY > 90) close();
    else panel.style.setProperty('--drag', '0px');
    startY = null;
  }
  mapBox.addEventListener('touchend', endDrag);
  mapBox.addEventListener('touchcancel', endDrag);
})();
