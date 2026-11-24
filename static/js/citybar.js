/*
 * The city search bar on the stories page (templates/_citybar.html, city_feed.py).
 *
 *  - Typing suggests cities (/api/cities); picking one opens its feed: /?city=&label=&ll=lat,lng
 *    (the server remembers it in the mf_city cookie).
 *  - The arrow button finds the city you're in (/api/city-at).
 *  - First visit (no city remembered yet): asks for your location once and opens your city's feed.
 */
(function () {
  'use strict';

  var bars = document.querySelectorAll('[data-citybar]');
  if (!bars.length) return;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function open(c) {
    location.href = '/?' + new URLSearchParams({ city: c.name, label: c.label || c.name, ll: c.lat + ',' + c.lng }).toString();
  }
  function hereCity(btn, quiet) {
    if (!navigator.geolocation) { if (!quiet) window.alert('Location isn’t available on this device.'); return; }
    if (btn) btn.classList.add('is-busy');
    navigator.geolocation.getCurrentPosition(function (pos) {
      fetch('/api/city-at?lat=' + pos.coords.latitude.toFixed(4) + '&lng=' + pos.coords.longitude.toFixed(4), { headers: { 'Accept': 'application/json' } })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (btn) btn.classList.remove('is-busy');
          if (d && d.city) open(d.city);
          else if (!quiet) window.alert('Couldn’t tell which city you’re in. Try searching for it.');
        })
        .catch(function () { if (btn) btn.classList.remove('is-busy'); });
    }, function () {
      if (btn) btn.classList.remove('is-busy');
      if (!quiet) window.alert('Allow location for this app to see the city you’re in, or search for it.');
    }, { enableHighAccuracy: false, timeout: 12000, maximumAge: 10 * 60 * 1000 });
  }

  Array.prototype.forEach.call(bars, function (bar) {
    var form = bar.querySelector('form'), input = bar.querySelector('.citybar__input');
    var list = bar.querySelector('.citybar__list'), here = bar.querySelector('[data-city-here]');
    var timer = null, seq = 0, hits = [];
    function close() { list.hidden = true; list.innerHTML = ''; }
    function paint(cities, q) {
      hits = cities;
      list.innerHTML = '';
      if (!cities.length) {
        if (q) list.appendChild(el('li', 'citybar__none', 'No city called “' + q + '” found.'));
        list.hidden = !q;
        return;
      }
      cities.forEach(function (c) {
        var li = el('li');
        var b = el('button', 'citybar__hit');
        b.type = 'button';
        b.setAttribute('role', 'option');
        b.appendChild(el('b', null, c.name));
        var where = (c.label || '').split(', ').slice(1).join(', ');
        if (where) b.appendChild(el('span', null, where));
        b.addEventListener('mousedown', function (e) { e.preventDefault(); });   // keep focus so blur doesn't close first
        b.addEventListener('click', function () { open(c); });
        li.appendChild(b);
        list.appendChild(li);
      });
      list.hidden = false;
    }
    input.addEventListener('input', function () {
      clearTimeout(timer);
      var q = input.value.trim();
      if (q.length < 2) { close(); return; }
      timer = setTimeout(function () {
        var mine = ++seq;
        fetch('/api/cities?q=' + encodeURIComponent(q), { headers: { 'Accept': 'application/json' } })
          .then(function (r) { return r.json(); })
          .then(function (d) { if (mine === seq) paint((d && d.cities) || [], q); })
          .catch(function () {});
      }, 250);
    });
    input.addEventListener('keydown', function (e) { if (e.key === 'Escape') { close(); input.blur(); } });
    input.addEventListener('blur', function () { setTimeout(close, 150); });
    form.addEventListener('submit', function (e) {
      if (hits.length) { e.preventDefault(); open(hits[0]); }   // Enter: the first suggestion
      else if (input.value.trim().length < 2) e.preventDefault();
      // otherwise the form goes to /?city=<text> and the server finds it
    });
    if (here) here.addEventListener('click', function () { hereCity(here, false); });
  });

  // First visit: no city chosen yet -> the city you're in (asked once).
  var chosen = /(?:^|;\s*)mf_city=/.test(document.cookie) || /[?&]city=/.test(location.search);
  var asked = false;
  try { asked = !!localStorage.getItem('mf-city-asked'); localStorage.setItem('mf-city-asked', '1'); } catch (e) { asked = true; }
  if (!chosen && !asked) hereCity(null, true);
})();
