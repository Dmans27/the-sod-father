/*
 * Save / Comment / Share -- the action row under each story, the comments
 * section, and the share menu. Loaded on every page from base.html, before
 * the sheet script, which calls window.SodFather.actionsHtml() and
 * .mountComments() when it renders an article.
 *
 * Who's signed in comes from window.READER (set by base.html from the
 * server session): { signedIn, name, saved: [slugs], csrf }.
 */
(function () {
  var READER = window.READER || { signedIn: false, name: null, saved: [], csrf: '' };
  var COUNTS = window.COMMENT_COUNTS || {};
  window.COMMENT_COUNTS = COUNTS;

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function icon(name) {
    return '<svg class="story-action__icon" viewBox="0 0 24 24" aria-hidden="true"><use href="#i-' + name + '"></use></svg>';
  }

  function isSaved(slug) { return READER.saved.indexOf(slug) !== -1; }

  // ── Dark reading mode ────────────────────────────────────────────────
  // Dark mode is site-wide now: the switch in the hamburger menu and the
  // moon button in an article drive the same setting, which is remembered on
  // this device (localStorage key "sf-theme"). Text size still resets on reload.
  var readingDark = false;
  try { readingDark = localStorage.getItem('sf-theme') === 'dark'; } catch (e) {}

  // Text size: the "Aa" button steps through these multipliers of the base
  // 18px paragraph size (18 -> 20 -> 22.5 -> 25px) and wraps back to normal.
  // Same rule as dark mode: held in memory only, so a reload starts at normal.
  var TEXT_SCALES = [1, 1.12, 1.25, 1.4];
  var TEXT_NAMES = ['normal', 'large', 'larger', 'largest'];
  var textStep = 0;

  function textButtonHtml() {
    return '<button type="button" class="story-action story-action--text" data-action="textsize" aria-label="Change text size (currently ' + TEXT_NAMES[textStep] + ')">' +
      '<span class="story-action__aa" aria-hidden="true"><i>A</i><b>A</b></span><span class="story-action__label">Text size</span></button>';
  }

  function themeButtonHtml() {
    return '<button type="button" class="story-action story-action--theme" data-action="theme" aria-pressed="' + readingDark + '" aria-label="' +
      (readingDark ? 'Switch to light reading mode' : 'Switch to dark reading mode') + '">' +
      icon(readingDark ? 'sun' : 'moon') + '<span class="story-action__label">' + (readingDark ? 'Light' : 'Dark') + '</span></button>';
  }

  function setDark(on) {
    readingDark = !!on;
    try { localStorage.setItem('sf-theme', readingDark ? 'dark' : 'light'); } catch (e) {}
    applyReadingTheme();
  }

  function applyReadingTheme() {
    document.documentElement.classList.toggle('is-dark', readingDark);
    var scopes = document.querySelectorAll('.article-sheet, body.article-page');
    Array.prototype.forEach.call(scopes, function (el) { el.classList.toggle('is-dark', readingDark); });
    Array.prototype.forEach.call(scopes, function (el) {
      if (textStep) el.style.setProperty('--read-scale', TEXT_SCALES[textStep]);
      else el.style.removeProperty('--read-scale');
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-action="textsize"]'), function (b) {
      b.setAttribute('aria-label', 'Change text size (currently ' + TEXT_NAMES[textStep] + ')');
      b.classList.toggle('is-active', textStep > 0);
    });
    if (menu) menu.classList.toggle('is-dark', readingDark);
    if (csheet) csheet.classList.toggle('is-dark', readingDark);
    var sw = document.getElementById('site-theme-toggle');
    if (sw) {
      sw.setAttribute('aria-checked', readingDark ? 'true' : 'false');
    }
    var bt = document.getElementById('bar-theme-toggle');
    if (bt) {
      bt.setAttribute('aria-pressed', readingDark ? 'true' : 'false');
      bt.setAttribute('aria-label', readingDark ? 'Switch to light mode' : 'Switch to dark mode');
      bt.querySelector('use').setAttribute('href', readingDark ? '#i-sun' : '#i-moon');
      bt.querySelector('.bottom-nav__label').textContent = readingDark ? 'Light' : 'Dark';
    }
    var tc = document.querySelector('meta[name="theme-color"]');
    if (tc) tc.setAttribute('content', readingDark ? '#1c231f' : '#ffffff');
    Array.prototype.forEach.call(document.querySelectorAll('[data-action="theme"]'), function (b) {
      b.setAttribute('aria-pressed', readingDark ? 'true' : 'false');
      b.setAttribute('aria-label', readingDark ? 'Switch to light reading mode' : 'Switch to dark reading mode');
      b.querySelector('.story-action__label').textContent = readingDark ? 'Light' : 'Dark';
      b.querySelector('use').setAttribute('href', readingDark ? '#i-sun' : '#i-moon');
    });
  }

  // Icon-only text size / dark mode / share buttons for the bottom name tag
  // (the tag carries data-slug and data-title itself, since it isn't inside
  // a .story-actions row).
  function popToolsHtml() {
    return '<div class="author-pop__tools">' + textButtonHtml() + themeButtonHtml() +
      '<button type="button" class="story-action story-action--share" data-action="share" aria-label="Share story">' +
        icon('share') + '<span class="story-action__label">Share</span></button>' +
      '<button type="button" class="story-action story-action--comment" data-action="comment" aria-label="Comments">' +
        icon('comment') + '<span class="story-action__label">Comments</span></button>' +
    '</div>';
  }

  // The three buttons. `compact` = icons only (used on the list rows).
  function actionsHtml(slug, title, compact, withTheme) {
    var saved = isSaved(slug);
    var n = COUNTS[slug] || 0;
    return '<div class="story-actions' + (compact ? ' story-actions--compact' : '') + '" data-slug="' + esc(slug) + '" data-title="' + esc(title) + '">' +
      '<button type="button" class="story-action story-action--save' + (saved ? ' is-saved' : '') + '" data-action="save" aria-pressed="' + saved + '" aria-label="' + (saved ? 'Unsave story' : 'Save story') + '">' +
        icon('save') + '<span class="story-action__label">' + (saved ? 'Saved' : 'Save') + '</span></button>' +
      '<button type="button" class="story-action" data-action="share" aria-label="Share story">' +
        icon('share') + '<span class="story-action__label">Share</span></button>' +
      '<button type="button" class="story-action" data-action="comment" aria-label="Comments">' +
        icon('comment') + '<span class="story-action__label">Comment</span><span class="story-action__count">' + (n || '') + '</span></button>' +
      (withTheme && !compact ? '<span class="story-actions__tools">' + textButtonHtml() + themeButtonHtml() + '</span>' : '') +
    '</div>';
  }

  // ── Keeping every copy of a story's buttons in sync ──────────────────
  function eachRow(slug, fn) {
    Array.prototype.forEach.call(document.querySelectorAll('.story-actions'), function (row) {
      if (row.getAttribute('data-slug') === slug) fn(row);
    });
  }

  function paintSaved(slug, saved) {
    var i = READER.saved.indexOf(slug);
    if (saved && i === -1) READER.saved.push(slug);
    if (!saved && i !== -1) READER.saved.splice(i, 1);
    eachRow(slug, function (row) {
      var b = row.querySelector('[data-action="save"]');
      if (!b) return;
      b.classList.toggle('is-saved', saved);
      b.setAttribute('aria-pressed', saved ? 'true' : 'false');
      b.setAttribute('aria-label', saved ? 'Unsave story' : 'Save story');
      b.querySelector('.story-action__label').textContent = saved ? 'Saved' : 'Save';
    });
  }

  function paintCount(slug, n) {
    COUNTS[slug] = Math.max(0, n);
    eachRow(slug, function (row) {
      var c = row.querySelector('.story-action__count');
      if (c) c.textContent = COUNTS[slug] || '';
    });
    var head = document.querySelector('#csheet[data-slug="' + slug + '"] .comments__count');
    if (head) head.textContent = COUNTS[slug] ? '(' + COUNTS[slug] + ')' : '';
  }

  // ── API helper ───────────────────────────────────────────────────────
  function api(method, url, body) {
    var opts = {
      method: method,
      credentials: 'same-origin',
      headers: { 'X-CSRF-Token': READER.csrf, 'Accept': 'application/json' }
    };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        return { ok: r.ok, status: r.status, data: data };
      });
    });
  }

  function loginUrl(extra) {
    var next = location.pathname + location.search + (extra && extra.hash ? extra.hash : '');
    var url = '/account/login?next=' + encodeURIComponent(next);
    if (extra && extra.then) url += '&then=' + encodeURIComponent(extra.then);
    return url;
  }

  // ── Save ─────────────────────────────────────────────────────────────
  function toggleSave(slug) {
    if (!READER.signedIn) { location.href = loginUrl({ then: 'save:' + slug }); return; }
    var was = isSaved(slug);
    paintSaved(slug, !was); // optimistic
    api('POST', '/api/save/' + encodeURIComponent(slug)).then(function (res) {
      if (res.ok) {
        paintSaved(slug, !!res.data.saved);
        // The iOS app listens for this to tap-feedback and refresh its offline copies (native.js).
        document.dispatchEvent(new CustomEvent('mansfield:saved', { detail: { slug: slug, saved: !!res.data.saved } }));
      } else if (res.status === 401) location.href = loginUrl({ then: 'save:' + slug });
      else paintSaved(slug, was);
    }).catch(function () { paintSaved(slug, was); });
  }

  // ── Share menu ───────────────────────────────────────────────────────
  var menu = null;
  function buildMenu() {
    menu = document.createElement('div');
    menu.className = 'share-menu';
    menu.setAttribute('aria-hidden', 'true');
    menu.innerHTML =
      '<div class="share-menu__scrim" data-share-close></div>' +
      '<div class="share-menu__panel" role="dialog" aria-label="Share this story">' +
        '<p class="share-menu__title"></p>' +
        '<a class="share-menu__item" id="share-email" href="#">' + icon('mail') + '<span>Email a friend</span></a>' +
        '<button type="button" class="share-menu__item" id="share-copy">' + icon('link') + '<span>Copy link</span></button>' +
        '<button type="button" class="share-menu__item" id="share-native" hidden>' + icon('share') + '<span>More options&hellip;</span></button>' +
        '<button type="button" class="share-menu__cancel" data-share-close>Cancel</button>' +
      '</div>';
    document.body.appendChild(menu);
    menu.addEventListener('click', function (e) {
      if (e.target.closest('[data-share-close]')) closeShare();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && menu.classList.contains('is-open')) closeShare();
    });
  }

  function closeShare() {
    if (!menu) return;
    menu.classList.remove('is-open');
    menu.setAttribute('aria-hidden', 'true');
  }

  function openShare(slug, title) {
    if (!menu) buildMenu();
    var url = location.origin + '/article/' + slug;
    menu.querySelector('.share-menu__title').textContent = title;
    menu.querySelector('#share-email').href =
      'mailto:?subject=' + encodeURIComponent(title) +
      '&body=' + encodeURIComponent('I thought you’d like this story from \\\\Mansfield:\n\n' + title + '\n' + url);

    var copy = menu.querySelector('#share-copy');
    copy.querySelector('span').textContent = 'Copy link';
    copy.onclick = function () {
      function done() {
        copy.querySelector('span').textContent = 'Link copied';
        setTimeout(closeShare, 900);
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(done, fallback);
      } else { fallback(); }
      function fallback() {
        var ta = document.createElement('textarea');
        ta.value = url; ta.setAttribute('readonly', '');
        ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select();
        try { document.execCommand('copy'); done(); } catch (e) {}
        document.body.removeChild(ta);
      }
    };

    var native = menu.querySelector('#share-native');
    native.hidden = !navigator.share;
    native.onclick = function () {
      navigator.share({ title: title, url: url }).catch(function () {});
      closeShare();
    };

    menu.classList.toggle('is-dark', readingDark);
    menu.classList.add('is-open');
    menu.setAttribute('aria-hidden', 'false');
  }

  // ── Comments ─────────────────────────────────────────────────────────
  function ago(iso) {
    var t = Date.parse(iso);
    if (isNaN(t)) return '';
    var s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    if (s < 86400 * 7) return Math.floor(s / 86400) + 'd ago';
    return new Date(t).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  }

  function commentEl(c, slug, list) {
    var li = document.createElement('li');
    li.className = 'comment';
    li.setAttribute('data-id', c.id);

    var head = document.createElement('div');
    head.className = 'comment__head';
    var name = document.createElement('span');
    name.className = 'comment__name';
    name.textContent = c.name;
    var when = document.createElement('span');
    when.className = 'comment__time';
    when.textContent = ago(c.created_at);
    head.appendChild(name);
    head.appendChild(when);

    var body = document.createElement('p');
    body.className = 'comment__body';
    body.textContent = c.body; // textContent: comments are never parsed as HTML

    li.appendChild(head);
    li.appendChild(body);

    if (c.can_delete) {
      var del = document.createElement('button');
      del.type = 'button';
      del.className = 'comment__delete';
      del.textContent = 'Delete';
      var armed = false;
      del.addEventListener('click', function () {
        if (!armed) {
          armed = true;
          del.textContent = 'Tap again to delete';
          setTimeout(function () { armed = false; del.textContent = 'Delete'; }, 3000);
          return;
        }
        api('DELETE', '/api/comments/' + encodeURIComponent(c.id)).then(function (res) {
          if (res.ok) {
            li.parentNode && li.parentNode.removeChild(li);
            paintCount(slug, (COUNTS[slug] || 1) - 1);
            if (!list.children.length) list.setAttribute('data-empty', 'true');
          }
        });
      });
      head.appendChild(del);
    }

    // Report + Block on other people's comments (App Store rule 1.2). A comment
    // reported by 3 readers is hidden by the server until the owner reviews it.
    if (c.can_report !== false) addSafetyTools(li, c, slug, list);
    return li;
  }

  var REPORT_REASONS = [
    ['spam', 'Spam'], ['abuse', 'Harassment or hate'],
    ['inappropriate', 'Sexual or violent'], ['other', 'Something else']
  ];

  function dropComment(li, slug, list) {
    if (li.parentNode) li.parentNode.removeChild(li);
    paintCount(slug, (COUNTS[slug] || 1) - 1);
    if (!list.children.length) list.setAttribute('data-empty', 'true');
  }

  function toolButton(label) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'comment__tool';
    b.textContent = label;
    return b;
  }

  function addSafetyTools(li, c, slug, list) {
    var tools = document.createElement('div');
    tools.className = 'comment__tools';
    var report = toolButton('Report');
    tools.appendChild(report);

    report.addEventListener('click', function () {
      if (!READER.signedIn) { location.href = loginUrl({ hash: '#comments' }); return; }
      tools.innerHTML = '';
      var ask = document.createElement('span');
      ask.className = 'comment__notice';
      ask.textContent = 'Why are you reporting this?';
      tools.appendChild(ask);
      var reasons = document.createElement('div');
      reasons.className = 'comment__reasons';
      REPORT_REASONS.forEach(function (r) {
        var chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'comment__reason';
        chip.textContent = r[1];
        chip.addEventListener('click', function () {
          Array.prototype.forEach.call(reasons.children, function (x) { x.disabled = true; });
          api('POST', '/api/comments/' + encodeURIComponent(c.id) + '/report', { reason: r[0] }).then(function (res) {
            if (res.status === 401) { location.href = loginUrl({ hash: '#comments' }); return; }
            if (!res.ok) {
              Array.prototype.forEach.call(reasons.children, function (x) { x.disabled = false; });
              ask.textContent = 'Couldn’t send that. Please try again.';
              return;
            }
            if (res.data && res.data.hidden) { dropComment(li, slug, list); return; }
            tools.innerHTML = '';
            var thanks = document.createElement('span');
            thanks.className = 'comment__notice';
            thanks.textContent = 'Thanks — we’ll review this comment.';
            tools.appendChild(thanks);
          }).catch(function () {
            Array.prototype.forEach.call(reasons.children, function (x) { x.disabled = false; });
            ask.textContent = 'Couldn’t send that. Check your connection.';
          });
        });
        reasons.appendChild(chip);
      });
      var cancel = document.createElement('button');
      cancel.type = 'button';
      cancel.className = 'comment__reason comment__reason--cancel';
      cancel.textContent = 'Cancel';
      cancel.addEventListener('click', function () {
        if (tools.parentNode) { var fresh = li.querySelector('.comment__tools'); if (fresh) fresh.remove(); addSafetyTools(li, c, slug, list); }
      });
      reasons.appendChild(cancel);
      tools.appendChild(reasons);
    });

    if (READER.signedIn) {
      var block = toolButton('Block');
      var armed = false;
      block.addEventListener('click', function () {
        if (!armed) {
          armed = true;
          block.textContent = 'Tap again to block ' + c.name;
          setTimeout(function () { armed = false; block.textContent = 'Block'; }, 3500);
          return;
        }
        block.disabled = true;
        api('POST', '/api/comments/' + encodeURIComponent(c.id) + '/block').then(function (res) {
          if (!res.ok) { block.disabled = false; armed = false; block.textContent = 'Block'; return; }
          var ids = (res.data && res.data.hidden_ids) || [];
          Array.prototype.slice.call(list.children).forEach(function (el) {
            if (ids.indexOf(el.getAttribute('data-id')) !== -1) dropComment(el, slug, list);
          });
        }).catch(function () { block.disabled = false; armed = false; block.textContent = 'Block'; });
      });
      tools.appendChild(block);
    }
    li.appendChild(tools);
  }

  // The comments live in one half-page panel that slides up from the bottom
  // (built the first time it's needed). The Comment button on a story card,
  // in the article header or in the bottom name tag all open this same panel.
  var csheet = null, cLastFocus = null, cSlug = null;

  function buildCsheet() {
    csheet = document.createElement('div');
    csheet.className = 'csheet';
    csheet.id = 'csheet';
    csheet.setAttribute('aria-hidden', 'true');
    csheet.setAttribute('inert', '');
    csheet.innerHTML =
      '<div class="csheet__scrim" data-csheet-close></div>' +
      '<section class="csheet__panel" role="dialog" aria-modal="true" aria-labelledby="csheet-title" tabindex="-1">' +
        '<header class="csheet__head">' +
          '<span class="csheet__grab" aria-hidden="true"></span>' +
          '<div class="csheet__headtext">' +
            '<h2 class="csheet__title" id="csheet-title">Comments <span class="comments__count"></span></h2>' +
            '<p class="csheet__story"></p>' +
          '</div>' +
          '<button type="button" class="csheet__close" data-csheet-close aria-label="Close comments">&times;</button>' +
        '</header>' +
        '<div class="csheet__scroll">' +
          '<ul class="comments__list" data-empty="true"></ul>' +
          '<p class="comments__loading" hidden>Loading&hellip;</p>' +
        '</div>' +
        '<div class="csheet__compose comments__compose"></div>' +
      '</section>';
    document.body.appendChild(csheet);

    csheet.addEventListener('click', function (e) {
      if (e.target.closest('[data-csheet-close]')) closeComments();
    });
    // Escape closes the comments first, without also closing a story
    // sheet that may be open underneath.
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && csheet.classList.contains('is-open')) {
        e.stopImmediatePropagation();
        closeComments();
      }
    }, true);

    // Swipe down on the header to dismiss (phones).
    var panel = csheet.querySelector('.csheet__panel');
    var head = csheet.querySelector('.csheet__head');
    var startY = null, dy = 0;
    head.addEventListener('touchstart', function (e) {
      if (e.target.closest('button')) return;
      startY = e.touches[0].clientY; dy = 0; panel.style.transition = 'none';
    }, { passive: true });
    head.addEventListener('touchmove', function (e) {
      if (startY === null) return;
      dy = Math.max(0, e.touches[0].clientY - startY);
      panel.style.transform = 'translateY(' + dy + 'px)';
    }, { passive: true });
    function endDrag() {
      if (startY === null) return;
      startY = null; panel.style.transition = '';
      var far = dy > 80; dy = 0;
      panel.style.transform = '';
      if (far) closeComments();
    }
    head.addEventListener('touchend', endDrag);
    head.addEventListener('touchcancel', endDrag);

    // When the on-screen keyboard opens, lift the panel above it.
    if (window.visualViewport) {
      var sync = function () {
        var vv = window.visualViewport;
        var kb = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
        csheet.style.setProperty('--kb', kb + 'px');
      };
      window.visualViewport.addEventListener('resize', sync);
      window.visualViewport.addEventListener('scroll', sync);
    }
  }

  function openComments(slug, title) {
    if (!csheet) buildCsheet();
    if (csheet.classList.contains('is-open') && cSlug === slug) return;
    cLastFocus = document.activeElement;
    cSlug = slug;
    csheet.setAttribute('data-slug', slug);
    csheet.querySelector('.csheet__story').textContent = title || '';
    csheet.removeAttribute('inert');
    csheet.setAttribute('aria-hidden', 'false');
    csheet.classList.toggle('is-dark', readingDark);
    paintCount(slug, COUNTS[slug] || 0);
    renderComments(slug);
    // One frame later so the closed state is painted and the slide-up animates.
    requestAnimationFrame(function () { csheet.classList.add('is-open'); });
    setTimeout(function () {
      var panel = csheet.querySelector('.csheet__panel');
      if (csheet.classList.contains('is-open') && panel) { try { panel.focus({ preventScroll: true }); } catch (e) {} }
    }, 380);
  }

  function closeComments() {
    if (!csheet || !csheet.classList.contains('is-open')) return;
    csheet.classList.remove('is-open');
    csheet.setAttribute('aria-hidden', 'true');
    csheet.setAttribute('inert', '');
    cSlug = null;
    if (document.activeElement && csheet.contains(document.activeElement)) document.activeElement.blur();
    if (cLastFocus && cLastFocus.focus) { try { cLastFocus.focus({ preventScroll: true }); } catch (e) {} }
  }

  function renderComments(slug) {
    var list = csheet.querySelector('.comments__list');
    var loading = csheet.querySelector('.comments__loading');
    var scroller = csheet.querySelector('.csheet__scroll');
    var compose = csheet.querySelector('.csheet__compose');
    list.innerHTML = '';
    list.setAttribute('data-empty', 'false'); // no "be the first" line until we know
    loading.hidden = false;
    loading.textContent = 'Loading…';
    scroller.scrollTop = 0;

    if (READER.signedIn) {
      compose.innerHTML =
        '<form class="comments__form csheet__form">' +
          '<div class="csheet__field">' +
            '<textarea name="body" rows="1" maxlength="1000" placeholder="Add to the conversation&hellip;" aria-label="Your comment" required></textarea>' +
            '<button type="submit" class="csheet__send" aria-label="Post comment">' +
              '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13"/><path d="M13 6l6 6-6 6"/></svg></button>' +
          '</div>' +
          '<div class="comments__row"><span class="comments__as">Commenting as <strong></strong></span>' +
          '<span class="comments__error" role="alert"></span></div>' +
        '</form>';
      compose.querySelector('strong').textContent = READER.name;
      var form = compose.querySelector('form');
      var err = compose.querySelector('.comments__error');
      var ta = form.elements.body;
      ta.addEventListener('input', function () {
        ta.style.height = 'auto';
        ta.style.height = Math.min(ta.scrollHeight, 110) + 'px';
      });
      ta.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); form.requestSubmit(); }
      });
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        var text = ta.value.trim();
        if (!text) return;
        err.textContent = '';
        var btn = form.querySelector('button');
        btn.disabled = true;
        api('POST', '/api/comments/' + encodeURIComponent(slug), { body: text }).then(function (res) {
          btn.disabled = false;
          if (cSlug !== slug) return;
          if (res.ok) {
            var li = commentEl(res.data.comment, slug, list);
            list.appendChild(li);
            list.setAttribute('data-empty', 'false');
            ta.value = ''; ta.style.height = '';
            if (window.MansfieldNative) MansfieldNative.haptic('success');
            paintCount(slug, (COUNTS[slug] || 0) + 1);
            scroller.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' });
          } else if (res.status === 401) {
            location.href = loginUrl({ hash: '#comments' });
          } else if (res.data && res.data.error === 'slow_down') {
            err.textContent = 'Easy -- wait a few seconds before posting again.';
          } else if (res.data && res.data.error === 'blocked_words') {
            err.textContent = 'Please keep it civil — that comment contains language we don’t allow.';
          } else if (res.data && res.data.error === 'too_many_links') {
            err.textContent = 'Please use no more than two links in a comment.';
          } else if (res.data && res.data.error === 'too_long') {
            err.textContent = 'That’s too long (1,000 characters max).';
          } else {
            err.textContent = 'Couldn’t post that. Please try again.';
          }
        }).catch(function () { btn.disabled = false; err.textContent = 'Couldn’t post that. Check your connection.'; });
      });
    } else {
      compose.innerHTML =
        '<div class="comments__signin"><span>Join the conversation.</span>' +
        '<a class="account-btn account-btn--small" href="' + esc(loginUrl({ hash: '#comments' })) + '">Sign in</a>' +
        '<a class="comments__alt" href="' + esc(loginUrl({ hash: '#comments' }).replace('/account/login', '/account/signup')) + '">Create an account</a></div>';
    }

    api('GET', '/api/comments/' + encodeURIComponent(slug)).then(function (res) {
      if (cSlug !== slug) return; // closed, or switched to another story
      loading.hidden = true;
      if (!res.ok) { loading.hidden = false; loading.textContent = 'Couldn’t load comments.'; return; }
      res.data.comments.forEach(function (c) { list.appendChild(commentEl(c, slug, list)); });
      list.setAttribute('data-empty', res.data.comments.length ? 'false' : 'true');
      paintCount(slug, res.data.comments.length);
    }).catch(function () { if (cSlug === slug) { loading.hidden = false; loading.textContent = 'Couldn’t load comments.'; } });
  }

  // ── Button clicks ────────────────────────────────────────────────────
  // Capture phase on document, so this runs before the card-opens-the-sheet
  // handler in base.html. The buttons live inside the story's <a> link, so
  // without preventDefault + stopPropagation a tap would open the story.
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('.story-action');
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    var row = btn.closest('.story-actions') || btn.closest('[data-slug]');
    var slug = row.getAttribute('data-slug');
    var title = row.getAttribute('data-title');
    var action = btn.getAttribute('data-action');

    if (action === 'textsize') { textStep = (textStep + 1) % TEXT_SCALES.length; applyReadingTheme(); return; }
    if (action === 'theme') { setDark(!readingDark); return; }
    if (action === 'save') toggleSave(slug);
    else if (action === 'share') {
      // Inside the iOS app use the real share sheet; on the web use our own menu.
      if (!(window.MansfieldNative && MansfieldNative.share(location.origin + '/article/' + slug, title))) openShare(slug, title);
    }
    else if (action === 'comment') openComments(slug, title);
  }, true);

  // Before / after slider: the range input is invisible but covers the
  // image, so dragging anywhere (mouse or finger) moves the divider. This
  // is delegated on document so it also works for sliders the pop-up
  // sheet adds after page load (scripts can't live inside that markup).
  document.addEventListener('input', function (e) {
    var range = e.target;
    if (!range.classList || !range.classList.contains('compare__range')) return;
    var fig = range.closest('.compare');
    if (fig) fig.style.setProperty('--pos', range.value + '%');
  });

  // Dark-mode switch in the hamburger menu.
  document.addEventListener('click', function (e) {
    var sw = e.target.closest('[data-site-theme]');
    if (!sw) return;
    e.preventDefault();
    setDark(!readingDark);
  });

  window.SodFather = {
    esc: esc,
    actionsHtml: actionsHtml,
    popToolsHtml: popToolsHtml,
    applyReadingTheme: applyReadingTheme,
    openComments: openComments,
    closeComments: closeComments
  };

  // Static article page: honor #comments (e.g. coming back from sign-in).
  document.addEventListener('DOMContentLoaded', function () {
    applyReadingTheme();
    if (location.hash !== '#comments') return;
    var row = document.querySelector('body.article-page .story-actions[data-slug]');
    if (row) setTimeout(function () { openComments(row.getAttribute('data-slug'), row.getAttribute('data-title')); }, 350);
  });
})();
