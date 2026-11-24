/*
 * Community walks on the page (community.py is the server side).
 *
 *   MansfieldCommunity.compose({ title, stops: [{slug, name}], source: 'route'|'walk', onPosted })
 *       the "Post to the feed" sheet: title, note, optional photo (made smaller on the phone first)
 *   MansfieldCommunity.like(id)  -> Promise {liked, likes}   (signed out: goes to Sign in)
 *   MansfieldCommunity.menu(item, onGone)
 *       Report / Hide walks from <name> for someone else's walk, Delete for your own.
 *       onGone() runs when the walk should disappear from this page.
 *
 * Cards anywhere on the page can carry data-cwalk='{"id", "by", "mine", "liked", "likes"}' with
 * buttons [data-cwalk-like] (and a [data-cwalk-likes] count inside) and [data-cwalk-more].
 */
(function () {
  'use strict';

  var READER = window.READER || { signedIn: false, csrf: '' };
  var REASONS = [['spam', 'Spam or advertising'], ['abuse', 'Harassment or hate'],
                 ['inappropriate', 'Sexual or violent content'], ['other', 'Something else']];

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function signIn() {
    location.href = '/account/login?next=' + encodeURIComponent(location.pathname + location.search);
  }
  function api(method, url, body) {
    return fetch(url, {
      method: method, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRF-Token': READER.csrf || '' },
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) {
      if (r.status === 401) { signIn(); throw new Error('sign_in'); }
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.error || 'Something went wrong.');
        return d;
      });
    });
  }

  // ---- A short note at the bottom of the screen -----------------------------
  var toastEl = null, toastTimer = null;
  function toast(text) {
    if (!toastEl) { toastEl = el('div', 'cwalk-toast'); toastEl.setAttribute('role', 'status'); document.body.appendChild(toastEl); }
    toastEl.textContent = text;
    toastEl.classList.add('is-on');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove('is-on'); }, 3200);
  }

  // ---- Action sheet (report / hide / delete) ---------------------------------
  function sheet(title, options) {
    var back = el('div', 'cwalk-menu');
    var box = el('div', 'cwalk-menu__box');
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    if (title) box.appendChild(el('p', 'cwalk-menu__title', title));
    function close() { back.classList.remove('is-open'); setTimeout(function () { back.remove(); }, 220); }
    options.forEach(function (o) {
      var b = el('button', 'cwalk-menu__btn' + (o.danger ? ' is-danger' : ''), o.label);
      b.type = 'button';
      b.addEventListener('click', function () { close(); if (o.run) o.run(); });
      box.appendChild(b);
    });
    var cancel = el('button', 'cwalk-menu__btn is-cancel', 'Cancel');
    cancel.type = 'button';
    cancel.addEventListener('click', close);
    box.appendChild(cancel);
    back.addEventListener('click', function (e) { if (e.target === back) close(); });
    back.appendChild(box);
    document.body.appendChild(back);
    requestAnimationFrame(function () { back.classList.add('is-open'); });
  }

  function menu(item, onGone) {
    if (!item || !item.id) return;
    var id = encodeURIComponent(item.id);
    if (!READER.signedIn) { signIn(); return; }
    if (item.mine) {
      sheet('Your walk', [{ label: 'Delete my post', danger: true, run: function () {
        if (!window.confirm('Delete “' + (item.title || 'this walk') + '” from the feed?')) return;
        api('DELETE', '/api/community/walks/' + id).then(function () { toast('Deleted.'); if (onGone) onGone(); })
          .catch(function (e) { if (e.message !== 'sign_in') toast(e.message); });
      } }]);
      return;
    }
    sheet(null, [
      { label: 'Report this walk', danger: true, run: function () {
        sheet('Why are you reporting it?', REASONS.map(function (r) {
          return { label: r[1], run: function () {
            api('POST', '/api/community/walks/' + id + '/report', { reason: r[0] })
              .then(function () { toast('Thanks. It’s hidden for you, and we’ll take a look.'); if (onGone) onGone(); })
              .catch(function (e) { if (e.message !== 'sign_in') toast(e.message); });
          } };
        }));
      } },
      { label: 'Hide walks from ' + (item.by || 'this walker'), run: function () {
        if (!window.confirm('You won’t see walks or comments from ' + (item.by || 'this walker') + ' any more. You can undo this in your account settings.')) return;
        api('POST', '/api/community/walks/' + id + '/block')
          .then(function () { toast('Done. You won’t see their walks any more.'); if (onGone) onGone(); })
          .catch(function (e) { if (e.message !== 'sign_in') toast(e.message); });
      } }
    ]);
  }

  function like(id) {
    if (!READER.signedIn) { signIn(); return Promise.reject(new Error('sign_in')); }
    return api('POST', '/api/community/walks/' + encodeURIComponent(id) + '/like');
  }

  // ---- Cards with data-cwalk ---------------------------------------------------
  function paintLike(card, d) {
    var btn = card.querySelector('[data-cwalk-like]');
    if (!btn) return;
    btn.classList.toggle('is-on', !!d.liked);
    btn.setAttribute('aria-pressed', d.liked ? 'true' : 'false');
    var n = btn.querySelector('[data-cwalk-likes]');
    if (n) n.textContent = String(d.likes || 0);
  }
  document.addEventListener('click', function (e) {
    var likeBtn = e.target.closest('[data-cwalk-like]'), moreBtn = e.target.closest('[data-cwalk-more]');
    if (!likeBtn && !moreBtn) return;
    var card = e.target.closest('[data-cwalk]');
    if (!card) return;
    e.preventDefault();
    e.stopPropagation();
    var item;
    try { item = JSON.parse(card.getAttribute('data-cwalk')); } catch (err) { return; }
    if (likeBtn) {
      if (item.mine) { toast('That’s your own walk.'); return; }
      like(item.id).then(function (d) {
        item.liked = d.liked; item.likes = d.likes;
        card.setAttribute('data-cwalk', JSON.stringify(item));
        paintLike(card, item);
      }).catch(function (err) { if (err.message !== 'sign_in') toast(err.message); });
    } else {
      menu(item, function () { card.style.display = 'none'; });
    }
  }, true);

  // ---- The "Post to the feed" sheet ---------------------------------------------
  function shrink(file) {
    return new Promise(function (resolve) {
      if (!file || !/^image\//.test(file.type || '') || !window.URL || !URL.createObjectURL) { resolve(file); return; }
      var url = URL.createObjectURL(file), img = new Image();
      img.onload = function () {
        var w = img.naturalWidth, h = img.naturalHeight, k = Math.min(1, 1600 / Math.max(w, h || 1));
        try {
          var c = document.createElement('canvas');
          c.width = Math.max(1, Math.round(w * k)); c.height = Math.max(1, Math.round(h * k));
          c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
          URL.revokeObjectURL(url);
          c.toBlob(function (b) { resolve(b ? new File([b], 'photo.jpg', { type: 'image/jpeg' }) : file); }, 'image/jpeg', 0.85);
        } catch (err) { URL.revokeObjectURL(url); resolve(file); }
      };
      img.onerror = function () { URL.revokeObjectURL(url); resolve(file); };
      img.src = url;
    });
  }

  var post = null;   // the open composer
  function compose(opts) {
    if (!READER.signedIn) { signIn(); return; }
    var stops = (opts.stops || []).filter(function (s) { return s && s.slug; });
    if (stops.length < 2) return;
    if (post) post.close(true);
    var photo = null, busy = false;

    var back = el('div', 'cpost');
    var box = el('form', 'cpost__box');
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-label', 'Post this walk to the feed');
    box.noValidate = true;

    var head = el('div', 'cpost__head');
    head.appendChild(el('h2', 'cpost__h', 'Post to the feed'));
    var x = el('button', 'cpost__x', '×');
    x.type = 'button'; x.setAttribute('aria-label', 'Close');
    head.appendChild(x);
    box.appendChild(head);
    box.appendChild(el('p', 'cpost__sub', 'Share it with walkers in ' + (window.FEED_CITY || 'your city') + '.'));

    var route = el('div', 'cpost__route');
    route.appendChild(el('b', null, stops.length + ' stops'));
    route.appendChild(el('span', null, stops.map(function (s) { return s.name; }).join(' → ')));
    box.appendChild(route);

    var tLabel = el('label', 'cpost__field');
    tLabel.appendChild(el('span', null, 'Name'));
    var title = el('input');
    title.type = 'text'; title.maxLength = 60; title.required = true; title.value = (opts.title || '').slice(0, 60);
    title.placeholder = 'Sunday coffee loop';
    tLabel.appendChild(title);
    box.appendChild(tLabel);

    var nLabel = el('label', 'cpost__field');
    var nHead = el('span', null, 'Why it’s good ');
    var count = el('em', 'cpost__count', '0/280');
    nHead.appendChild(count);
    nLabel.appendChild(nHead);
    var note = el('textarea');
    note.maxLength = 280; note.rows = 3;
    note.placeholder = 'The best cinnamon roll is at stop 2. Go early on Saturdays.';
    note.addEventListener('input', function () { count.textContent = note.value.length + '/280'; });
    nLabel.appendChild(note);
    box.appendChild(nLabel);

    var pic = el('div', 'cpost__photo');
    var pick = el('label', 'cpost__pick');
    pick.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7.5h3l1.6-2.5h6.8L17 7.5h3v11H4z"/><circle cx="12" cy="13" r="3.5"/></svg><span>Add a photo</span>';
    var file = el('input');
    file.type = 'file'; file.accept = 'image/*';
    pick.appendChild(file);
    var preview = el('div', 'cpost__preview');
    preview.hidden = true;
    var img = el('img');
    img.alt = '';
    var drop = el('button', 'cpost__drop', '×');
    drop.type = 'button'; drop.setAttribute('aria-label', 'Remove the photo');
    preview.appendChild(img); preview.appendChild(drop);
    pic.appendChild(pick); pic.appendChild(preview);
    box.appendChild(pic);

    var err = el('p', 'cpost__err');
    err.setAttribute('role', 'alert');
    err.hidden = true;
    box.appendChild(err);
    var go = el('button', 'cpost__go', 'Post walk');
    go.type = 'submit';
    box.appendChild(go);
    box.appendChild(el('p', 'cpost__fine', 'Your name, the stops, your note and photo will be public. Be kind: walks can be reported.'));

    file.addEventListener('change', function () {
      var f = file.files && file.files[0];
      if (!f) return;
      go.disabled = true;
      shrink(f).then(function (small) {
        photo = small;
        if (img.src) URL.revokeObjectURL(img.src);
        img.src = URL.createObjectURL(small);
        preview.hidden = false; pick.hidden = true;
        go.disabled = false;
      });
    });
    drop.addEventListener('click', function () {
      photo = null; file.value = '';
      if (img.src) URL.revokeObjectURL(img.src);
      img.removeAttribute('src');
      preview.hidden = true; pick.hidden = false;
    });

    function close(now) {
      back.classList.remove('is-open');
      setTimeout(function () { back.remove(); }, now ? 0 : 260);
      if (post && post.back === back) post = null;
      document.documentElement.classList.remove('cpost-open');
    }
    x.addEventListener('click', function () { close(); });
    back.addEventListener('click', function (e) { if (e.target === back && !busy) close(); });

    box.addEventListener('submit', function (e) {
      e.preventDefault();
      if (busy) return;
      var t = title.value.trim();
      if (t.length < 2) { err.textContent = 'Give your walk a name.'; err.hidden = false; title.focus(); return; }
      busy = true; go.disabled = true; go.textContent = 'Posting…'; err.hidden = true;
      var fd = new FormData();
      fd.append('title', t);
      fd.append('note', note.value);
      fd.append('source', opts.source === 'walk' ? 'walk' : 'route');
      fd.append('stops', JSON.stringify(stops.map(function (s) { return { slug: s.slug, name: s.name }; })));
      if (photo) fd.append('photo', photo, 'photo.jpg');
      fetch('/api/community/walks', { method: 'POST', credentials: 'same-origin', body: fd,
                                      headers: { 'Accept': 'application/json', 'X-CSRF-Token': READER.csrf || '' } })
        .then(function (r) {
          if (r.status === 401) { signIn(); throw new Error('sign_in'); }
          return r.json().catch(function () { return {}; }).then(function (d) {
            if (!r.ok) throw new Error(d.error || 'Couldn’t post it. Try again.');
            return d;
          });
        })
        .then(function (d) {
          close();
          toast('Posted! Walkers in ' + (d.city || 'your city') + ' can see it now.');
          if (opts.onPosted) opts.onPosted(d.post);
        })
        .catch(function (e2) {
          busy = false; go.disabled = false; go.textContent = 'Post walk';
          if (e2.message !== 'sign_in') { err.textContent = e2.message; err.hidden = false; }
        });
    });

    back.appendChild(box);
    document.body.appendChild(back);
    document.documentElement.classList.add('cpost-open');
    post = { back: back, close: close };
    requestAnimationFrame(function () { back.classList.add('is-open'); });
  }

  window.MansfieldCommunity = { compose: compose, like: like, menu: menu, toast: toast };
})();
