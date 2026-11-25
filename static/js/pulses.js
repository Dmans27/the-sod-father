/*
 * Pulses on the walk map (pulses.py): short videos that disappear after 8 hours.
 *
 *  - The map (the directory's embed in #walkplan-map) shows a pulsing circle in each district with live
 *    clips: this page sends mansfield:pulses {pulses} and the map answers mansfield:pulse-open {id} on a tap.
 *  - The camera button (top left) opens the composer: record or pick several videos, tag a place on any of
 *    them, and post. Each file goes straight to Cloudinary with a signature from /api/pulses/sign; then
 *    /api/pulses saves the post in the district you're in (its centre, never your exact spot).
 *  - Tapping a pulse opens the player: that district's clips, newest first, like stories.
 */
(function () {
  'use strict';
  var root = document.getElementById('walkplan');
  var frame = document.getElementById('walkplan-map');
  var cam = document.getElementById('walkplan-cam');
  if (!root || !frame) return;
  var READER = window.READER || { signedIn: false, csrf: '' };
  var origin = '';
  try { origin = new URL(root.getAttribute('data-directory') || '').origin; } catch (e) { return; }
  var MAX_CLIPS = 10, MAX_MB = 100, CLIP_S = 8;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  function api(method, url, body) {
    return fetch(url, { method: method, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRF-Token': READER.csrf || '' },
      body: body ? JSON.stringify(body) : undefined })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, status: r.status, d: d }; }); },
            function () { return { ok: false, status: 0, d: { error: 'Couldn’t reach the server. Check your connection.' } }; });
  }
  var PIN = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/></svg>';

  // ── The map layer ────────────────────────────────────────────────────
  var pulses = [], center = null, on = true;
  function km(a, b) {
    var dx = (a[0] - b[0]) * 111.32 * Math.cos(a[1] * Math.PI / 180), dy = (a[1] - b[1]) * 110.57;
    return Math.sqrt(dx * dx + dy * dy);
  }
  function send() {
    var list = root.classList.contains('is-walking') ? [] : pulses;
    try { frame.contentWindow.postMessage({ type: 'mansfield:pulses', pulses: list }, origin); } catch (e) {}
  }
  function load() {
    var q = center ? '?lat=' + center[1].toFixed(4) + '&lng=' + center[0].toFixed(4) : '';
    fetch('/api/pulses' + q, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        on = d.enabled !== false;
        if (cam) cam.hidden = !on;
        pulses = d.pulses || [];
        send();
      })
      .catch(function () {});
  }
  frame.addEventListener('load', function () { send(); setTimeout(send, 800); setTimeout(send, 2500); });
  window.addEventListener('message', function (e) {
    if (e.source !== frame.contentWindow || !e.data || typeof e.data.type !== 'string') return;
    var t = e.data.type;
    if (t === 'mansfield:view' && Array.isArray(e.data.center)) {
      var c = [Number(e.data.center[0]), Number(e.data.center[1])];
      if (!center || km(center, c) > 15) { center = c; load(); }
    } else if (t === 'mansfield:pulse-open') {
      openPlayer(String(e.data.id || ''));
    } else if (t === 'mansfield:walk') {
      setTimeout(send, 50);   // walking: the pulses step aside; back after
    }
  });
  load();
  setInterval(function () { if (!document.hidden) load(); }, 60000);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) load(); });

  // ── Toast ────────────────────────────────────────────────────────────
  var toastEl = null, toastTimer = null;
  function toast(text) {
    if (!toastEl) { toastEl = el('div', 'pz-toast'); toastEl.setAttribute('role', 'status'); document.body.appendChild(toastEl); }
    toastEl.textContent = text;
    toastEl.classList.add('is-on');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove('is-on'); }, 4000);
  }

  // ── The composer ─────────────────────────────────────────────────────
  var box = null, clips = [], here = null, district = '', busy = false;
  function whereAmI() {
    return new Promise(function (resolve) {
      if (!('geolocation' in navigator)) return resolve(null);
      navigator.geolocation.getCurrentPosition(function (p) { resolve({ lat: p.coords.latitude, lng: p.coords.longitude }); },
        function () { resolve(null); }, { enableHighAccuracy: false, maximumAge: 60000, timeout: 10000 });
    });
  }
  function openComposer() {
    if (!READER.signedIn) { location.href = '/account/login?next=' + encodeURIComponent('/walk'); return; }
    if (!box) buildComposer();
    clips.forEach(function (c) { URL.revokeObjectURL(c.url); });
    clips = [];
    box.hidden = false;
    document.body.classList.add('pz-open');
    paintClips();
    whereLine.textContent = 'Finding where you are…';
    here = null; district = '';
    paintPost();
    whereAmI().then(function (at) {
      if (!at) { whereLine.textContent = 'Turn on location to post: your video shows in the district you’re in.'; return; }
      here = at;
      api('POST', '/api/pulses/where', at).then(function (res) {
        if (res.d && res.d.ok) {
          district = res.d.name;
          whereLine.innerHTML = '';
          whereLine.insertAdjacentHTML('beforeend', PIN);
          whereLine.appendChild(document.createTextNode('Shows in ' + district + ' for 8 hours, then disappears.'));
        } else whereLine.textContent = (res.d && res.d.error) || 'Couldn’t work out where you are.';
        paintPost();
      });
    });
  }
  function closeComposer() {
    if (busy) return;
    box.hidden = true;
    document.body.classList.remove('pz-open');
    clips.forEach(function (c) { URL.revokeObjectURL(c.url); });
    clips = [];
  }
  var whereLine, list, postBtn, note, recIn, pickIn;
  function buildComposer() {
    box = el('section', 'pz');
    box.setAttribute('aria-label', 'Post videos');
    box.hidden = true;
    var head = el('div', 'pz__head');
    head.appendChild(el('h2', 'pz__title', 'New pulse'));
    var x = el('button', 'pz__close', '×');
    x.type = 'button';
    x.setAttribute('aria-label', 'Close');
    x.addEventListener('click', closeComposer);
    head.appendChild(x);
    box.appendChild(head);
    whereLine = el('p', 'pz__where');
    box.appendChild(whereLine);
    var adds = el('div', 'pz__adds');
    recIn = el('input'); recIn.type = 'file'; recIn.accept = 'video/*'; recIn.setAttribute('capture', 'environment'); recIn.hidden = true;
    pickIn = el('input'); pickIn.type = 'file'; pickIn.accept = 'video/*'; pickIn.multiple = true; pickIn.hidden = true;
    var rec = el('button', 'pz__add', 'Record');
    rec.type = 'button';
    rec.insertAdjacentHTML('afterbegin', '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="6" width="13" height="12" rx="2.5"/><path d="M16 10.5l5-3v9l-5-3z"/></svg>');
    var pick = el('button', 'pz__add', 'Choose videos');
    pick.type = 'button';
    pick.insertAdjacentHTML('afterbegin', '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M8 13l3 3 5-6"/></svg>');
    rec.addEventListener('click', function () { recIn.click(); });
    pick.addEventListener('click', function () { pickIn.click(); });
    [recIn, pickIn].forEach(function (inp) {
      inp.addEventListener('change', function () { addFiles(inp.files); inp.value = ''; });
    });
    adds.appendChild(rec); adds.appendChild(pick); adds.appendChild(recIn); adds.appendChild(pickIn);
    box.appendChild(adds);
    note = el('p', 'pz__note', 'Each video plays for up to 8 seconds; longer ones start from the beginning and stop at 8 s. Up to 10 at once.');
    box.appendChild(note);
    list = el('ul', 'pz__list');
    box.appendChild(list);
    postBtn = el('button', 'pz__post', 'Post');
    postBtn.type = 'button';
    postBtn.addEventListener('click', post);
    var foot = el('div', 'pz__foot');
    foot.appendChild(postBtn);
    box.appendChild(foot);
    document.body.appendChild(box);
  }
  function addFiles(files) {
    var tooBig = 0, tooMany = 0;
    Array.prototype.forEach.call(files || [], function (f) {
      if (!/^video\//.test(f.type || 'video/')) return;
      if (f.size > MAX_MB * 1024 * 1024) { tooBig++; return; }
      if (clips.length >= MAX_CLIPS) { tooMany++; return; }
      var c = { file: f, url: URL.createObjectURL(f), duration: 0, place: null, progress: 0, finding: false };
      clips.push(c);
      var probe = document.createElement('video');
      probe.preload = 'metadata';
      probe.muted = true;
      probe.onloadedmetadata = function () { c.duration = probe.duration || 0; paintClips(); };
      probe.src = c.url;
    });
    if (tooBig) toast(tooBig + ' video' + (tooBig === 1 ? ' is' : 's are') + ' over ' + MAX_MB + ' MB. Pick a shorter clip.');
    else if (tooMany) toast('Up to ' + MAX_CLIPS + ' videos at once.');
    paintClips();
  }
  function paintPost() {
    if (!postBtn) return;
    var n = clips.length;
    postBtn.disabled = busy || !n || !here || !district;
    postBtn.textContent = busy ? 'Posting…' : n ? 'Post ' + n + ' video' + (n === 1 ? '' : 's') + (district ? ' to ' + district : '') : 'Add a video to post';
  }
  function paintClips() {
    list.innerHTML = '';
    clips.forEach(function (c, i) {
      var li = el('li', 'pz__clip');
      var v = el('video', 'pz__thumb');
      v.muted = true; v.playsInline = true; v.setAttribute('playsinline', ''); v.preload = 'metadata';
      v.src = c.url + '#t=0.3';
      li.appendChild(v);
      var body = el('div', 'pz__clip-body');
      var len = c.duration ? (c.duration > CLIP_S + 0.5 ? 'First 8 s of ' + Math.round(c.duration) + ' s' : Math.max(1, Math.round(c.duration)) + ' s') : 'Video';
      body.appendChild(el('span', 'pz__len', len));
      if (c.place) {
        var chip = el('span', 'pz__tag');
        chip.insertAdjacentHTML('afterbegin', PIN);
        chip.appendChild(document.createTextNode(c.place.name));
        var rm = el('button', 'pz__tag-x', '×');
        rm.type = 'button';
        rm.setAttribute('aria-label', 'Remove the place');
        rm.addEventListener('click', function () { c.place = null; paintClips(); });
        chip.appendChild(rm);
        body.appendChild(chip);
      } else if (!busy) {
        var tagBtn = el('button', 'pz__tagbtn', 'Tag a place');
        tagBtn.type = 'button';
        tagBtn.insertAdjacentHTML('afterbegin', PIN);
        tagBtn.addEventListener('click', function () { c.finding = !c.finding; paintClips(); });
        body.appendChild(tagBtn);
      }
      if (c.finding && !c.place) body.appendChild(placeFinder(c));
      if (busy) {
        var bar = el('span', 'pz__bar');
        var fill = el('i');
        fill.style.width = Math.round(c.progress * 100) + '%';
        bar.appendChild(fill);
        body.appendChild(bar);
        c.barFill = fill;
      }
      li.appendChild(body);
      if (!busy) {
        var del = el('button', 'pz__rm', '×');
        del.type = 'button';
        del.setAttribute('aria-label', 'Remove this video');
        del.addEventListener('click', function () { URL.revokeObjectURL(c.url); clips.splice(i, 1); paintClips(); });
        li.appendChild(del);
      }
      list.appendChild(li);
    });
    if (!clips.length) list.appendChild(el('li', 'pz__empty', 'Record a moment or choose videos from your camera roll.'));
    paintPost();
  }
  // Search the directory (and Google, via the directory) for the place in a clip.
  function placeFinder(c) {
    var wrap = el('div', 'pz__find');
    var q = el('input', 'pz__find-in');
    q.type = 'search'; q.placeholder = 'Search a restaurant, café, place…'; q.autocomplete = 'off';
    q.setAttribute('aria-label', 'Search a place to tag');
    var out = el('ul', 'pz__find-list');
    wrap.appendChild(q); wrap.appendChild(out);
    var timer = null, seq = 0;
    q.addEventListener('input', function () {
      clearTimeout(timer);
      var text = q.value.trim();
      if (text.length < 2) { out.innerHTML = ''; return; }
      timer = setTimeout(function () {
        var mine = ++seq, at = here || { lat: 48.2082, lng: 16.3738 };
        out.innerHTML = '';
        out.appendChild(el('li', 'pz__hint', 'Searching…'));
        fetch('/api/place-search?' + new URLSearchParams({ q: text, lat: at.lat.toFixed(5), lng: at.lng.toFixed(5) }).toString(),
              { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (mine !== seq) return;
            out.innerHTML = '';
            var found = (d && d.places) || [];
            if (!found.length) { out.appendChild(el('li', 'pz__hint', 'Nothing found for “' + text + '”.')); return; }
            found.slice(0, 6).forEach(function (p) {
              var li = el('li'), b = el('button', '', p.name);
              b.type = 'button';
              var sub = [p.category, p.address].filter(Boolean).join(' · ');
              if (sub) b.appendChild(el('span', '', sub));
              b.addEventListener('click', function () {
                if (p.slug) { c.place = { slug: p.slug, name: p.name }; c.finding = false; paintClips(); return; }
                b.disabled = true;   // a place from Google: the directory adds it first
                api('POST', '/api/place-import', { placeId: p.placeId }).then(function (res) {
                  if (res.d && res.d.ok && res.d.place) { c.place = { slug: res.d.place.slug, name: res.d.place.name || p.name }; c.finding = false; paintClips(); }
                  else { b.disabled = false; toast((res.d && res.d.error) || 'Couldn’t tag that place.'); }
                });
              });
              li.appendChild(b);
              out.appendChild(li);
            });
          })
          .catch(function () { if (mine === seq) { out.innerHTML = ''; out.appendChild(el('li', 'pz__hint', 'Couldn’t search right now.')); } });
      }, 300);
    });
    setTimeout(function () { q.focus(); }, 50);
    return wrap;
  }
  function upload(sign, c) {
    return new Promise(function (resolve, reject) {
      var fd = new FormData();
      fd.append('file', c.file);
      fd.append('api_key', sign.api_key);
      Object.keys(sign.params).forEach(function (k) { fd.append(k, sign.params[k]); });
      var xhr = new XMLHttpRequest();
      xhr.open('POST', sign.url);
      xhr.upload.onprogress = function (e) {
        if (!e.lengthComputable) return;
        c.progress = e.loaded / e.total;
        if (c.barFill) c.barFill.style.width = Math.round(c.progress * 100) + '%';
      };
      xhr.onload = function () {
        var d = {};
        try { d = JSON.parse(xhr.responseText); } catch (e) {}
        if (xhr.status < 300 && d.public_id) { c.progress = 1; if (c.barFill) c.barFill.style.width = '100%'; resolve(d.public_id); }
        else reject(new Error((d.error && d.error.message) || 'upload failed'));
      };
      xhr.onerror = function () { reject(new Error('network')); };
      xhr.send(fd);
    });
  }
  function post() {
    if (busy || !clips.length || !here) return;
    busy = true;
    paintClips();
    api('POST', '/api/pulses/sign').then(function (res) {
      if (!res.ok || !res.d.ok) { throw new Error((res.d && res.d.error) || (res.d && res.d.verify ? '' : 'Couldn’t start the upload.')); }
      var sign = res.d, done = [];
      // One after another: kinder to a phone connection than all at once.
      return clips.reduce(function (p, c) {
        return p.then(function () { return upload(sign, c).then(function (pid) { done.push({ public_id: pid, place_slug: c.place ? c.place.slug : '', place_name: c.place ? c.place.name : '' }); }); });
      }, Promise.resolve()).then(function () { return api('POST', '/api/pulses', { lat: here.lat, lng: here.lng, clips: done }); });
    }).then(function (res) {
      busy = false;
      if (!res.ok || !res.d.ok) { paintClips(); if (!(res.d && res.d.verify)) toast((res.d && res.d.error) || 'Couldn’t post. Try again.'); return; }
      var where = res.d.district, id = res.d.pulse;
      closeComposer();
      toast('Posted in ' + where + '. It disappears in 8 hours.');
      load();
      setTimeout(function () { openPlayer(id); }, 400);
    }).catch(function (err) {
      busy = false;
      paintClips();
      if (err && err.message) toast(err.message === 'network' ? 'The upload stopped. Check your connection and try again.' : err.message);
    });
  }
  if (cam) cam.addEventListener('click', openComposer);

  // ── The player ───────────────────────────────────────────────────────
  var player = null, items = [], at = 0, muted = true, video, bars, who, when, placeBtn, soundBtn, menu, districtLine;
  function buildPlayer() {
    player = el('section', 'pp');
    player.setAttribute('aria-label', 'Videos');
    player.hidden = true;
    bars = el('div', 'pp__bars');
    player.appendChild(bars);
    var top = el('div', 'pp__top');
    who = el('span', 'pp__who');
    when = el('span', 'pp__when');
    var meta = el('div', 'pp__meta');
    meta.appendChild(who); meta.appendChild(when);
    top.appendChild(meta);
    var more = el('button', 'pp__btn', '•••');
    more.type = 'button';
    more.setAttribute('aria-label', 'More');
    more.addEventListener('click', function (e) { e.stopPropagation(); toggleMenu(); });
    var close = el('button', 'pp__btn', '×');
    close.type = 'button';
    close.setAttribute('aria-label', 'Close');
    close.addEventListener('click', function (e) { e.stopPropagation(); closePlayer(); });
    top.appendChild(more); top.appendChild(close);
    player.appendChild(top);
    video = el('video', 'pp__video');
    video.playsInline = true; video.setAttribute('playsinline', ''); video.setAttribute('webkit-playsinline', '');
    video.muted = true; video.preload = 'auto';
    video.addEventListener('ended', function () { step(1); });
    video.addEventListener('timeupdate', function () {
      var fill = bars.children[at] && bars.children[at].firstChild;
      if (fill && video.duration) fill.style.width = Math.min(100, video.currentTime / Math.min(video.duration, CLIP_S) * 100) + '%';
      if (video.currentTime >= CLIP_S) step(1);   // the 8-second copy may still be on its way: never more than 8 s
    });
    video.addEventListener('error', function () { setTimeout(function () { step(1); }, 600); });
    player.appendChild(video);
    var prev = el('button', 'pp__zone pp__zone--prev'), next = el('button', 'pp__zone pp__zone--next');
    prev.type = next.type = 'button';
    prev.setAttribute('aria-label', 'Previous video'); next.setAttribute('aria-label', 'Next video');
    prev.addEventListener('click', function () { step(-1); });
    next.addEventListener('click', function () { step(1); });
    player.appendChild(prev); player.appendChild(next);
    var bottom = el('div', 'pp__bottom');
    districtLine = el('p', 'pp__district');
    bottom.appendChild(districtLine);
    placeBtn = el('a', 'pp__place');
    bottom.appendChild(placeBtn);
    soundBtn = el('button', 'pp__sound', 'Tap for sound');
    soundBtn.type = 'button';
    soundBtn.addEventListener('click', function (e) { e.stopPropagation(); muted = !muted; video.muted = muted; paintSound(); if (video.paused) video.play().catch(function () {}); });
    bottom.appendChild(soundBtn);
    player.appendChild(bottom);
    menu = el('div', 'pp__menu');
    menu.hidden = true;
    player.appendChild(menu);
    document.body.appendChild(player);
    document.addEventListener('keydown', function (e) {
      if (player.hidden) return;
      if (e.key === 'Escape') closePlayer();
      else if (e.key === 'ArrowRight') step(1);
      else if (e.key === 'ArrowLeft') step(-1);
    });
  }
  function paintSound() { soundBtn.textContent = muted ? 'Tap for sound' : 'Sound on'; soundBtn.classList.toggle('is-on', !muted); }
  function openPlayer(id) {
    if (!/^[0-9a-f]{16}$/.test(id)) return;
    if (!player) buildPlayer();
    fetch('/api/pulses/' + id, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        items = (d && d.clips) || [];
        if (!items.length) { toast((d && d.error) || 'These videos have disappeared.'); load(); return; }
        bars.innerHTML = '';
        items.forEach(function () { var b = el('span', 'pp__bar'); b.appendChild(el('i')); bars.appendChild(b); });
        player.hidden = false;
        document.body.classList.add('pp-open');
        show(0);
      })
      .catch(function () { toast('Couldn’t load the videos.'); });
  }
  function closePlayer() {
    if (!player) return;
    video.pause();
    video.removeAttribute('src');
    video.load();
    player.hidden = true;
    menu.hidden = true;
    document.body.classList.remove('pp-open');
  }
  function show(i) {
    at = i;
    var c = items[i];
    menu.hidden = true;
    Array.prototype.forEach.call(bars.children, function (b, k) { b.firstChild.style.width = k < i ? '100%' : '0%'; });
    who.textContent = c.mine ? 'You' : c.by;
    when.textContent = c.ago + ' · ' + c.left;
    districtLine.innerHTML = '';
    districtLine.insertAdjacentHTML('afterbegin', PIN);
    districtLine.appendChild(document.createTextNode(c.district));
    if (c.place && c.place.slug) {
      placeBtn.hidden = false;
      placeBtn.innerHTML = '';
      placeBtn.appendChild(el('b', '', c.place.name));
      placeBtn.appendChild(el('span', '', 'Walk there →'));
      placeBtn.href = '/walk?' + new URLSearchParams({ go: c.place.slug, t: c.place.name }).toString();
    } else placeBtn.hidden = true;
    video.poster = c.poster || '';
    video.src = c.video;
    video.muted = muted;
    paintSound();
    var p = video.play();
    if (p && p.catch) p.catch(function () { video.muted = muted = true; paintSound(); video.play().catch(function () {}); });
  }
  function step(d) {
    var n = at + d;
    if (n < 0) { show(0); return; }
    if (n >= items.length) { closePlayer(); return; }
    show(n);
  }
  function toggleMenu() {
    if (!menu.hidden) { menu.hidden = true; return; }
    var c = items[at];
    menu.innerHTML = '';
    function item(label, cls, fn) {
      var b = el('button', cls || '', label);
      b.type = 'button';
      b.addEventListener('click', function (e) { e.stopPropagation(); fn(); });
      menu.appendChild(b);
    }
    if (!READER.signedIn) {
      item('Sign in to report', '', function () { location.href = '/account/login?next=/walk'; });
    } else if (c.mine) {
      item('Delete my video post', 'is-danger', function () {
        if (!window.confirm('Delete this post now? It’s gone for everyone.')) return;
        api('DELETE', '/api/pulses/post/' + encodeURIComponent(c.post)).then(function () { closePlayer(); load(); toast('Deleted.'); });
      });
    } else {
      item('Report', '', function () {
        api('POST', '/api/pulses/post/' + encodeURIComponent(c.post) + '/report').then(function () { menu.hidden = true; toast('Thanks. We’ll take a look.'); });
      });
      item('Block ' + c.by, 'is-danger', function () {
        if (!window.confirm('Block ' + c.by + '? You won’t see their videos, walks or comments.')) return;
        api('POST', '/api/pulses/post/' + encodeURIComponent(c.post) + '/block').then(function () { closePlayer(); load(); toast(c.by + ' is blocked.'); });
      });
    }
    item('Cancel', 'is-ghost', function () { menu.hidden = true; });
    menu.hidden = false;
  }
})();
