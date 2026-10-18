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
  // Only the article (the pop-up sheet and the /article/ page) goes dark;
  // the home feed stays light. It always starts light: the choice is held
  // in memory only, so opening a different story in the same visit keeps
  // the reader's choice, but a fresh page load or app launch starts light.
  var readingDark = false;

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

  function applyReadingTheme() {
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
    Array.prototype.forEach.call(document.querySelectorAll('[data-action="theme"]'), function (b) {
      b.setAttribute('aria-pressed', readingDark ? 'true' : 'false');
      b.setAttribute('aria-label', readingDark ? 'Switch to light reading mode' : 'Switch to dark reading mode');
      b.querySelector('.story-action__label').textContent = readingDark ? 'Light' : 'Dark';
      b.querySelector('use').setAttribute('href', readingDark ? '#i-sun' : '#i-moon');
    });
  }

  // The three buttons. `compact` = icons only (used on the list rows).
  function actionsHtml(slug, title, compact, withTheme) {
    var saved = isSaved(slug);
    var n = COUNTS[slug] || 0;
    return '<div class="story-actions' + (compact ? ' story-actions--compact' : '') + '" data-slug="' + esc(slug) + '" data-title="' + esc(title) + '">' +
      '<button type="button" class="story-action story-action--save' + (saved ? ' is-saved' : '') + '" data-action="save" aria-pressed="' + saved + '" aria-label="' + (saved ? 'Unsave story' : 'Save story') + '">' +
        icon('save') + '<span class="story-action__label">' + (saved ? 'Saved' : 'Save') + '</span></button>' +
      '<button type="button" class="story-action" data-action="comment" aria-label="Comments">' +
        icon('comment') + '<span class="story-action__label">Comment</span><span class="story-action__count">' + (n || '') + '</span></button>' +
      '<button type="button" class="story-action" data-action="share" aria-label="Share story">' +
        icon('share') + '<span class="story-action__label">Share</span></button>' +
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
    var head = document.querySelector('.comments[data-slug="' + slug + '"] .comments__count');
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
      if (res.ok) paintSaved(slug, !!res.data.saved);
      else if (res.status === 401) location.href = loginUrl({ then: 'save:' + slug });
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
      '&body=' + encodeURIComponent('I thought you’d like this story from The Sod Father:\n\n' + title + '\n' + url);

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

    menu.classList.toggle('is-dark', readingDark && !!document.querySelector('.article-sheet.is-dark, body.article-page.is-dark'));
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
    return li;
  }

  function mountComments(container, slug) {
    if (!container) return;
    container.innerHTML =
      '<section class="comments" id="comments" data-slug="' + esc(slug) + '">' +
        '<h2 class="comments__title">Comments <span class="comments__count"></span></h2>' +
        '<ul class="comments__list" data-empty="true"></ul>' +
        '<p class="comments__loading">Loading&hellip;</p>' +
        '<div class="comments__compose"></div>' +
      '</section>';
    var root = container.querySelector('.comments');
    var list = root.querySelector('.comments__list');
    var loading = root.querySelector('.comments__loading');
    var compose = root.querySelector('.comments__compose');

    if (READER.signedIn) {
      compose.innerHTML =
        '<form class="comments__form">' +
          '<label class="comments__as">Commenting as <strong></strong></label>' +
          '<textarea name="body" rows="3" maxlength="1000" placeholder="Add to the conversation&hellip;" required></textarea>' +
          '<div class="comments__row"><span class="comments__error" role="alert"></span>' +
          '<button type="submit" class="account-btn account-btn--small">Post comment</button></div>' +
        '</form>';
      compose.querySelector('strong').textContent = READER.name;
      var form = compose.querySelector('form');
      var err = compose.querySelector('.comments__error');
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        var ta = form.elements.body;
        var text = ta.value.trim();
        if (!text) return;
        err.textContent = '';
        var btn = form.querySelector('button');
        btn.disabled = true;
        api('POST', '/api/comments/' + encodeURIComponent(slug), { body: text }).then(function (res) {
          btn.disabled = false;
          if (res.ok) {
            list.appendChild(commentEl(res.data.comment, slug, list));
            list.removeAttribute('data-empty');
            ta.value = '';
            paintCount(slug, (COUNTS[slug] || 0) + 1);
          } else if (res.status === 401) {
            location.href = loginUrl({ hash: '#comments' });
          } else if (res.data && res.data.error === 'slow_down') {
            err.textContent = 'Easy -- wait a few seconds before posting again.';
          } else if (res.data && res.data.error === 'too_long') {
            err.textContent = 'That’s too long (1,000 characters max).';
          } else {
            err.textContent = 'Couldn’t post that. Please try again.';
          }
        }).catch(function () { btn.disabled = false; err.textContent = 'Couldn’t post that. Check your connection.'; });
      });
    } else {
      compose.innerHTML =
        '<div class="comments__signin"><p>Join the conversation.</p>' +
        '<a class="account-btn account-btn--small" href="' + esc(loginUrl({ hash: '#comments' })) + '">Sign in to comment</a>' +
        '<a class="comments__alt" href="' + esc(loginUrl({ hash: '#comments' }).replace('/account/login', '/account/signup')) + '">Create an account</a></div>';
    }

    api('GET', '/api/comments/' + encodeURIComponent(slug)).then(function (res) {
      loading.parentNode && loading.parentNode.removeChild(loading);
      if (!res.ok) return;
      res.data.comments.forEach(function (c) { list.appendChild(commentEl(c, slug, list)); });
      if (res.data.comments.length) list.removeAttribute('data-empty');
      paintCount(slug, res.data.comments.length);
    }).catch(function () { loading.textContent = 'Couldn’t load comments.'; });
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
    var row = btn.closest('.story-actions');
    var slug = row.getAttribute('data-slug');
    var title = row.getAttribute('data-title');
    var action = btn.getAttribute('data-action');

    if (action === 'textsize') { textStep = (textStep + 1) % TEXT_SCALES.length; applyReadingTheme(); return; }
    if (action === 'theme') { readingDark = !readingDark; applyReadingTheme(); return; }
    if (action === 'save') toggleSave(slug);
    else if (action === 'share') openShare(slug, title);
    else if (action === 'comment') {
      var inSheet = !!btn.closest('.article-sheet');
      var onArticlePage = !!document.getElementById('comments') && !document.getElementById('card-grid');
      if (inSheet || onArticlePage) scrollToComments();
      else if (window.__openArticleSheet) window.__openArticleSheet(slug, { scrollTo: 'comments' });
      else location.href = '/article/' + slug + '#comments';
    }
  }, true);

  function scrollToComments() {
    var target = document.getElementById('comments');
    if (!target) return;
    var scroller = target.closest('.article-sheet__scroll');
    if (scroller) {
      var delta = target.getBoundingClientRect().top - scroller.getBoundingClientRect().top - 12;
      scroller.scrollTo({ top: scroller.scrollTop + delta, behavior: 'smooth' });
    } else {
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

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

  window.SodFather = {
    esc: esc,
    actionsHtml: actionsHtml,
    applyReadingTheme: applyReadingTheme,
    mountComments: mountComments,
    scrollToComments: scrollToComments
  };

  // Static article page: mount the comments section (and honor #comments).
  document.addEventListener('DOMContentLoaded', function () {
    applyReadingTheme();
    var host = document.getElementById('article-comments');
    if (!host) return;
    mountComments(host, host.getAttribute('data-slug'));
    if (location.hash === '#comments') setTimeout(scrollToComments, 350);
  });
})();
