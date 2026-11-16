/* Phone-friendly helpers for the admin story editor.
 *
 *  1. Paste a story   -- paste text written with ## headings, - bullets and a few
 *                        tags and it becomes blocks (same rules as tools/new_story.py).
 *  2. Add-block sheet -- on small screens, one "+ Add block" button opens a menu.
 *  3. Block controls  -- move up/down (drag doesn't work on a phone) and collapse.
 *  4. Place search    -- search the directory by name instead of pasting a link.
 *  5. Add link        -- highlight words in a text block and link them.
 *  6. Writing helpers  -- draft a story from a transcript; suggest a title, summary, category.
 *
 * It only runs on the editor page and builds on window.__storyEditor
 * (set up by the inline script in templates/admin/story_form.html).
 */
(function () {
  'use strict';

  var form = document.getElementById('story-form');
  var container = document.getElementById('blocks-container');
  var ed = window.__storyEditor;
  if (!form || !container || !ed) return;

  var PLACE_BASE = 'https://ai-app-repo.onrender.com';

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  // ───────────────────────────── 1. Paste a story ─────────────────────────────

  var BULLET = /^\s*[-*•]\s+(.*)$/;
  var NUMBER = /^\s*\d+[.)]\s+(.*)$/;

  function splitFrontMatter(raw) {
    raw = raw.replace(/^﻿/, '');
    var m = raw.match(/^---[ \t]*\n([\s\S]*?)\n---[ \t]*(?:\n([\s\S]*))?$/);
    if (!m) return { meta: {}, body: raw };
    var meta = {};
    m[1].split('\n').forEach(function (line) {
      var i = line.indexOf(':');
      if (i > 0) meta[line.slice(0, i).trim().toLowerCase()] = line.slice(i + 1).trim();
    });
    return { meta: meta, body: m[2] || '' };
  }

  function parseBlocks(body) {
    var lines = body.split('\n');
    var blocks = [];
    var para = [];
    var i = 0;

    function flush() {
      var text = para.map(function (s) { return s.trim(); }).filter(Boolean).join(' ');
      if (text) blocks.push({ type: 'paragraph', data: { text: text } });
      para = [];
    }

    while (i < lines.length) {
      var line = lines[i];
      var t = line.trim();
      var m;

      if (!t) { flush(); i++; continue; }

      if ((m = t.match(/^!takeaways\s*(.*)$/i))) {
        flush();
        var items = [];
        i++;
        while (i < lines.length && BULLET.test(lines[i])) { items.push(lines[i].match(BULLET)[1].trim()); i++; }
        if (items.length) blocks.push({ type: 'takeaways', data: { title: m[1].trim() || 'Key takeaways', items: items } });
        continue;
      }

      if ((m = t.match(/^!(tip|warning)\s*:\s*(.+)$/i))) {
        flush();
        blocks.push({ type: 'tip', data: { variant: m[1].toLowerCase(), text: m[2].trim() } });
        i++; continue;
      }

      if (/^(\*{3,}|_{3,}|-{3,})$/.test(t)) { flush(); blocks.push({ type: 'divider', data: {} }); i++; continue; }

      if ((m = t.match(/^#{1,6}\s+(.*)$/))) {
        flush();
        blocks.push({ type: 'subheading', data: { text: m[1].trim() } });
        i++; continue;
      }

      if ((m = t.match(/^\[place:\s*([^\]|]+?)\s*(?:\|\s*([^\]]*?))?\s*\]$/i))) {
        flush();
        var ref = m[1].trim();
        var url = ref.indexOf('/') >= 0 ? ref : PLACE_BASE + '/listing/' + ref;
        blocks.push({ type: 'place', data: { url: url, caption: (m[2] || '').trim(), style: 'photo' } });
        i++; continue;
      }

      if (t.charAt(0) === '>') {
        flush();
        var q = [];
        while (i < lines.length && lines[i].trim().charAt(0) === '>') { q.push(lines[i].trim().replace(/^>\s*/, '')); i++; }
        var qt = q.join(' '), attr = '';
        var bar = qt.lastIndexOf('|');
        if (bar >= 0) { attr = qt.slice(bar + 1).trim(); qt = qt.slice(0, bar).trim(); }
        blocks.push({ type: 'quote', data: { text: qt, attribution: attr } });
        continue;
      }

      if (BULLET.test(line) || NUMBER.test(line)) {
        flush();
        var numbered = NUMBER.test(line);
        var re = numbered ? NUMBER : BULLET;
        var list = [];
        while (i < lines.length && re.test(lines[i])) { list.push(lines[i].match(re)[1].trim()); i++; }
        blocks.push({ type: 'list', data: { style: numbered ? 'numbered' : 'bulleted', items: list } });
        continue;
      }

      para.push(line);
      i++;
    }
    flush();
    return blocks;
  }

  function fire(node) {
    ['input', 'change'].forEach(function (n) { node.dispatchEvent(new Event(n, { bubbles: true })); });
  }

  function applyMeta(meta) {
    var f = form.elements;
    function setVal(name, v) { if (v && f[name]) { f[name].value = v; fire(f[name]); } }
    setVal('title', meta.title);
    setVal('dek', meta.dek);
    setVal('published_at', meta.date);
    if (meta.category && f.category) {
      f.category.value = meta.category; fire(f.category);
      if (meta.subcategory && f.subcategory) { f.subcategory.value = meta.subcategory; fire(f.subcategory); }
    }
    if (meta.author && f.author_slug) { f.author_slug.value = meta.author; fire(f.author_slug); }
  }

  var builderHead = document.querySelector('.admin-builder__head');
  var pasteBtn = el('button', 'admin-btn admin-tools__btn', 'Paste a story');
  pasteBtn.type = 'button';
  var collapseAllBtn = el('button', 'admin-btn admin-tools__btn', 'Collapse all');
  collapseAllBtn.type = 'button';
  var toolsRow = el('div', 'admin-tools');
  toolsRow.appendChild(pasteBtn);
  toolsRow.appendChild(collapseAllBtn);

  var panel = el('div', 'admin-paste');
  panel.hidden = true;
  panel.innerHTML =
    '<p class="admin-hint">Paste your text. <strong>## Heading</strong> makes a subheading, lines starting ' +
    'with <strong>-</strong> make a bulleted list, <strong>1.</strong> a numbered list, ' +
    '<strong>!tip:</strong> a tip box, <strong>!takeaways Title</strong> followed by bullets a takeaways box, ' +
    '<strong>&gt;</strong> a quote, <strong>***</strong> a divider, and ' +
    '<strong>[place: listing-slug | Caption]</strong> a place card. Everything else becomes paragraphs. ' +
    'A <strong>---</strong> block at the top can set title:, dek:, category:, subcategory:, date:.</p>' +
    '<textarea class="admin-paste__text" rows="9" placeholder="Paste your story here"></textarea>' +
    '<div class="admin-paste__mode">' +
    '<label><input type="radio" name="paste_mode" value="append" checked> Add to the end of the story</label>' +
    '<label><input type="radio" name="paste_mode" value="replace"> Replace all blocks</label></div>' +
    '<div class="admin-paste__actions"></div>' +
    '<p class="admin-paste__msg" role="status" aria-live="polite"></p>';
  var pasteText = panel.querySelector('.admin-paste__text');
  var pasteMsg = panel.querySelector('.admin-paste__msg');
  var buildBtn = el('button', 'admin-btn admin-btn--primary', 'Build blocks');
  buildBtn.type = 'button';
  var closeBtn = el('button', 'admin-btn', 'Close');
  closeBtn.type = 'button';
  panel.querySelector('.admin-paste__actions').appendChild(buildBtn);
  panel.querySelector('.admin-paste__actions').appendChild(closeBtn);

  if (builderHead) {
    builderHead.appendChild(toolsRow);
    builderHead.appendChild(panel);
  }

  pasteBtn.addEventListener('click', function () {
    panel.hidden = !panel.hidden;
    if (!panel.hidden) {
      Array.prototype.forEach.call(builderHead.querySelectorAll('.admin-paste'), function (p) { if (p !== panel) p.hidden = true; });
      pasteText.focus();
    }
  });
  closeBtn.addEventListener('click', function () { panel.hidden = true; });

  buildBtn.addEventListener('click', function () {
    var raw = pasteText.value;
    if (!raw.trim()) { pasteMsg.textContent = 'Paste some text first.'; return; }
    var parts = splitFrontMatter(raw);
    var blocks = parseBlocks(parts.body);
    if (!blocks.length) { pasteMsg.textContent = 'I couldn’t find anything to build from that text.'; return; }
    var replace = panel.querySelector('input[name="paste_mode"]:checked').value === 'replace';
    if (replace) ed.clear();
    if (Object.keys(parts.meta).length) applyMeta(parts.meta);
    var first = null;
    blocks.forEach(function (b) {
      var node = ed.addBlock(b.type, b.data);
      if (!first) first = node;
    });
    ed.syncOrder();
    var counts = {};
    blocks.forEach(function (b) { counts[b.type] = (counts[b.type] || 0) + 1; });
    pasteMsg.textContent = 'Built ' + blocks.length + ' blocks: ' + Object.keys(counts).map(function (k) { return counts[k] + ' ' + k; }).join(', ') + '.';
    pasteText.value = '';
    panel.hidden = true;
    if (first && first.scrollIntoView) first.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  // ───────────────────────────── 2. Add-block sheet ─────────────────────────────

  var addRow = document.querySelector('.admin-builder__add');
  if (addRow) {
    // A round green + that lives in the bottom bar (with Publish / Save draft)
    var fab = el('button', 'admin-addfab', '+');
    fab.type = 'button';
    fab.setAttribute('aria-label', 'Add block');
    fab.setAttribute('aria-expanded', 'false');
    var bar = form.querySelector('.admin-form__actions');
    var cancelLink = bar && bar.querySelector('a.admin-btn');
    if (cancelLink) cancelLink.setAttribute('aria-label', 'Cancel');
    var backdrop = el('div', 'admin-addsheet-backdrop');
    var sheetTitle = el('div', 'admin-addsheet-title', 'Add a block');
    addRow.insertBefore(sheetTitle, addRow.firstChild);
    if (bar) bar.insertBefore(fab, bar.firstChild); else document.body.appendChild(fab);
    document.body.appendChild(backdrop);

    function setSheet(open) {
      addRow.classList.toggle('is-sheet-open', open);
      backdrop.classList.toggle('is-open', open);
      fab.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    fab.addEventListener('click', function () { setSheet(!addRow.classList.contains('is-sheet-open')); });
    backdrop.addEventListener('click', function () { setSheet(false); });

    var before = null;
    addRow.addEventListener('click', function (e) {
      if (e.target.closest('.admin-block-add')) before = new Set(container.children);
    }, true);
    addRow.addEventListener('click', function (e) {
      var btn = e.target.closest('.admin-block-add');
      if (!btn) return;
      setSheet(false);
      // the existing handler has already added the block; find it and bring it into view
      var added = Array.prototype.filter.call(container.children, function (c) { return before && !before.has(c); })[0];
      if (added) {
        added.scrollIntoView({ behavior: 'smooth', block: 'center' });
        var focusable = added.querySelector('textarea, input[type="text"]');
        if (focusable) setTimeout(function () { focusable.focus({ preventScroll: true }); }, 350);
      }
    });
  }

  // ───────────────────────────── 3. Block controls ─────────────────────────────

  function summaryOf(block) {
    var f = block.querySelector('textarea, input[type="text"]');
    var v = f ? f.value.trim().replace(/\s+/g, ' ') : '';
    return v.length > 70 ? v.slice(0, 70) + '…' : v;
  }

  function setCollapsed(block, on) {
    block.classList.toggle('is-collapsed', on);
    var btn = block.querySelector('.admin-block__fold');
    var sum = block.querySelector('.admin-block__summary');
    if (btn) { btn.textContent = on ? '▸' : '▾'; btn.setAttribute('aria-expanded', on ? 'false' : 'true'); }
    if (sum) sum.textContent = on ? summaryOf(block) : '';
  }

  function enhanceBlock(block) {
    if (block.dataset.enhanced) return;
    var head = block.querySelector('.admin-block__head');
    var remove = block.querySelector('.admin-block__remove');
    var label = block.querySelector('.admin-block__label');
    if (!head || !remove) return;
    block.dataset.enhanced = '1';

    var fold = el('button', 'admin-block__fold', '▾');
    fold.type = 'button';
    fold.title = 'Collapse or expand';
    fold.setAttribute('aria-expanded', 'true');
    head.insertBefore(fold, head.firstChild);

    var summary = el('span', 'admin-block__summary');
    if (label && label.nextSibling) head.insertBefore(summary, label.nextSibling); else head.appendChild(summary);

    var up = el('button', 'admin-block__move', '▲');
    up.type = 'button'; up.title = 'Move up'; up.setAttribute('aria-label', 'Move block up');
    var down = el('button', 'admin-block__move', '▼');
    down.type = 'button'; down.title = 'Move down'; down.setAttribute('aria-label', 'Move block down');
    head.insertBefore(up, remove);
    head.insertBefore(down, remove);

    fold.addEventListener('click', function () { setCollapsed(block, !block.classList.contains('is-collapsed')); });
    head.addEventListener('click', function (e) {
      if (e.target === head || e.target === label || e.target === summary) setCollapsed(block, !block.classList.contains('is-collapsed'));
    });
    up.addEventListener('click', function () {
      var prev = block.previousElementSibling;
      if (prev) { container.insertBefore(block, prev); ed.syncOrder(); block.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
    });
    down.addEventListener('click', function () {
      var next = block.nextElementSibling;
      if (next) { container.insertBefore(next, block); ed.syncOrder(); block.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
    });
    if (block.dataset.blockType === 'place') enhancePlace(block);
    addLinkTool(block);
  }

  collapseAllBtn.addEventListener('click', function () {
    var blocks = container.querySelectorAll('.admin-block');
    var anyOpen = Array.prototype.some.call(blocks, function (b) { return !b.classList.contains('is-collapsed'); });
    Array.prototype.forEach.call(blocks, function (b) { setCollapsed(b, anyOpen); });
    collapseAllBtn.textContent = anyOpen ? 'Expand all' : 'Collapse all';
  });

  // ───────────────────────────── 4. Place search ─────────────────────────────

  function enhancePlace(block) {
    var urlInput = block.querySelector('.admin-block__url');
    var capInput = block.querySelector('.admin-block__caption');
    if (!urlInput || block.querySelector('.admin-psearch')) return;

    var wrap = el('div', 'admin-psearch');
    var input = el('input', 'admin-psearch__input');
    input.type = 'search';
    input.placeholder = 'Search your directory by name…';
    input.setAttribute('aria-label', 'Search places by name');
    input.autocomplete = 'off';
    input.autocapitalize = 'off';
    var status = el('p', 'admin-psearch__status');
    var list = el('div', 'admin-psearch__results');
    wrap.appendChild(input);
    wrap.appendChild(status);
    wrap.appendChild(list);
    urlInput.parentNode.insertBefore(wrap, urlInput);

    var timer = null, ctl = null;

    function pick(r) {
      urlInput.value = r.url;
      fire(urlInput);
      if (capInput && !capInput.value.trim()) { capInput.value = r.name; fire(capInput); }
      list.textContent = '';
      input.value = '';
      status.textContent = '✓ Added ' + r.name + (r.city ? ' · ' + r.city : '');
    }

    function run() {
      var q = input.value.trim();
      if (q.length < 2) { list.textContent = ''; status.textContent = ''; return; }
      if (ctl) ctl.abort();
      ctl = typeof AbortController === 'function' ? new AbortController() : null;
      status.textContent = 'Searching…';
      fetch('/admin/place-search?q=' + encodeURIComponent(q), { credentials: 'same-origin', signal: ctl ? ctl.signal : undefined })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          list.textContent = '';
          if (data.error) { status.textContent = data.error; return; }
          if (!data.results.length) { status.textContent = 'No matches. Try a different spelling.'; return; }
          status.textContent = '';
          data.results.forEach(function (r) {
            var b = el('button', 'admin-psearch__item');
            b.type = 'button';
            var name = el('strong', null, r.name);
            var meta = el('span', null, [r.city, r.category].filter(Boolean).join(' · '));
            b.appendChild(name);
            b.appendChild(meta);
            b.addEventListener('click', function () { pick(r); });
            list.appendChild(b);
          });
        })
        .catch(function (err) { if (!err || err.name !== 'AbortError') status.textContent = 'Search failed. Check your connection.'; });
    }

    input.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(run, 350); });
    // Enter must not submit the whole story form.
    input.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); run(); } });
  }

  // ───────────────────────────── 5. Add a link inside text ─────────────────────────────
  // Highlight some words, tap "Add link", paste the address. The words become
  // [words](https://address) in the text; readers see them underlined.

  function addLinkTool(block) {
    var type = block.dataset.blockType;
    if (['paragraph', 'list', 'tip', 'takeaways'].indexOf(type) < 0) return;
    var body = block.querySelector('.admin-block__body');
    var ta = body && body.querySelector('textarea.admin-block__text');
    if (!ta || block.querySelector('.admin-linktool')) return;

    var bar = el('div', 'admin-linktool');
    var btn = el('button', 'admin-linktool__btn', '🔗 Add link');
    btn.type = 'button';
    var row = el('div', 'admin-linktool__row');
    row.hidden = true;
    var input = el('input', 'admin-linktool__input');
    input.type = 'text';
    input.placeholder = 'Paste the web address';
    input.setAttribute('inputmode', 'url');
    input.setAttribute('autocapitalize', 'off');
    input.setAttribute('autocorrect', 'off');
    input.autocomplete = 'off';
    input.setAttribute('aria-label', 'Link address');
    var ok = el('button', 'admin-btn admin-btn--primary', 'Insert');
    ok.type = 'button';
    var cancel = el('button', 'admin-btn', 'Cancel');
    cancel.type = 'button';
    var msg = el('p', 'admin-linktool__msg');
    msg.setAttribute('role', 'status');
    row.appendChild(input);
    row.appendChild(ok);
    row.appendChild(cancel);
    bar.appendChild(btn);
    bar.appendChild(row);
    bar.appendChild(msg);
    ta.parentNode.insertBefore(bar, ta.nextSibling);

    // Remember the highlighted words: tapping a button can clear the selection on some phones.
    var sel = null;
    function remember() { sel = ta.selectionStart !== ta.selectionEnd ? { s: ta.selectionStart, e: ta.selectionEnd } : null; }
    document.addEventListener('selectionchange', function () { if (document.activeElement === ta) remember(); });
    ['pointerdown', 'mousedown', 'touchstart'].forEach(function (n) {
      btn.addEventListener(n, function () { remember(); }, { passive: true });
    });

    function close() { row.hidden = true; input.value = ''; }

    btn.addEventListener('click', function () {
      if (document.activeElement === ta) remember();
      if (!sel || sel.s === sel.e || sel.e > ta.value.length) {
        msg.textContent = 'First highlight the words you want to link, then tap Add link.';
        row.hidden = true;
        return;
      }
      var picked = ta.value.slice(sel.s, sel.e);
      if (/\n/.test(picked)) { msg.textContent = 'Highlight words on one line only.'; row.hidden = true; return; }
      msg.textContent = 'Linking “' + (picked.length > 40 ? picked.slice(0, 40) + '…' : picked) + '”';
      row.hidden = false;
      input.focus();
    });

    function insert() {
      var url = input.value.trim();
      if (url && !/^https?:\/\//i.test(url) && /^[^\s\/]+\.[^\s]+$/.test(url)) url = 'https://' + url;
      if (!/^https?:\/\/[^\s()<>\[\]"']+$/i.test(url)) { msg.textContent = 'That doesn’t look like a web address. It should start with https://'; return; }
      var v = ta.value;
      var picked = v.slice(sel.s, sel.e).replace(/[\[\]]/g, '');
      var made = '[' + picked + '](' + url + ')';
      ta.value = v.slice(0, sel.s) + made + v.slice(sel.e);
      var caret = sel.s + made.length;
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      sel = null;
      close();
      msg.textContent = 'Link added.';
      try { ta.focus({ preventScroll: true }); ta.setSelectionRange(caret, caret); } catch (e) {}
    }
    ok.addEventListener('click', insert);
    cancel.addEventListener('click', function () { close(); sel = null; msg.textContent = ''; });
    input.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); insert(); } });
  }

  // ───────────────────────────── 6. Writing helpers ─────────────────────────────
  // "Draft from transcript" and "Suggest title" ask the server (admin.py -> ai_tools.py),
  // which asks the same AI model the Ask feature uses. Nothing is published or changed
  // until you tap something: the draft lands in the paste box for you to read first.

  var csrf = form.getAttribute('data-csrf') || '';

  function aiPost(path, body) {
    return fetch(path, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () {
        return { ok: false, error: r.ok ? 'Unexpected reply from the server. You may need to sign in again.' : 'The server had a problem or took too long (' + r.status + '). Please try again.' };
      });
    }, function () {
      return { ok: false, error: 'Couldn’t reach the server. Check your connection and try again.' };
    });
  }

  function storyText() {
    var parts = [];
    Array.prototype.forEach.call(container.querySelectorAll('.admin-block'), function (b) {
      var t = b.dataset.blockType;
      if (['paragraph', 'subheading', 'quote', 'tip', 'list', 'takeaways'].indexOf(t) < 0) return;
      var title = b.querySelector('.admin-block__title');
      if (title && title.value.trim()) parts.push(title.value.trim());
      var ta = b.querySelector('textarea.admin-block__text');
      var v = ta ? ta.value.trim() : '';
      if (v) parts.push((t === 'subheading' ? '## ' : '') + v.replace(/\[([^\[\]]+)\]\((https?:\/\/[^\s()]+)\)/g, '$1'));
    });
    return parts.join('\n\n');
  }

  if (builderHead) {
    // -- Draft from transcript --
    var tBtn = el('button', 'admin-btn admin-tools__btn', 'Draft from transcript');
    tBtn.type = 'button';
    var tPanel = el('div', 'admin-paste');
    tPanel.hidden = true;
    tPanel.innerHTML =
      '<p class="admin-hint">Paste a transcript or rough notes. The assistant writes a draft with subheadings, ' +
      'bullets and key takeaways, in its own words, and puts it in the paste box for you to check before anything is built. ' +
      'It can get details wrong, so read it against what you learned.</p>' +
      '<textarea class="admin-paste__text" rows="9" placeholder="Paste the transcript or your notes here"></textarea>' +
      '<div class="admin-paste__mode">' +
      '<label><input type="radio" name="ai_style" value="study" checked> Study sheet</label>' +
      '<label><input type="radio" name="ai_style" value="article"> Article</label></div>' +
      '<div class="admin-paste__actions"></div>' +
      '<p class="admin-paste__msg" role="status" aria-live="polite"></p>';
    var tText = tPanel.querySelector('.admin-paste__text');
    var tMsg = tPanel.querySelector('.admin-paste__msg');
    var tGo = el('button', 'admin-btn admin-btn--primary', 'Write draft');
    tGo.type = 'button';
    var tClose = el('button', 'admin-btn', 'Close');
    tClose.type = 'button';
    tPanel.querySelector('.admin-paste__actions').appendChild(tGo);
    tPanel.querySelector('.admin-paste__actions').appendChild(tClose);

    // -- Suggest title, summary, category --
    var sBtn = el('button', 'admin-btn admin-tools__btn', 'Suggest title');
    sBtn.type = 'button';
    var sPanel = el('div', 'admin-paste admin-suggest');
    sPanel.hidden = true;
    var sMsg = el('p', 'admin-paste__msg');
    sMsg.setAttribute('role', 'status');
    sMsg.setAttribute('aria-live', 'polite');
    var sList = el('div', 'admin-suggest__list');
    var sActions = el('div', 'admin-paste__actions');
    var sAgain = el('button', 'admin-btn', 'Try again');
    sAgain.type = 'button';
    var sClose = el('button', 'admin-btn', 'Close');
    sClose.type = 'button';
    sActions.appendChild(sAgain);
    sActions.appendChild(sClose);
    sPanel.appendChild(sMsg);
    sPanel.appendChild(sList);
    sPanel.appendChild(sActions);

    toolsRow.appendChild(tBtn);
    toolsRow.appendChild(sBtn);
    builderHead.appendChild(tPanel);
    builderHead.appendChild(sPanel);

    tBtn.addEventListener('click', function () {
      tPanel.hidden = !tPanel.hidden;
      if (!tPanel.hidden) { panel.hidden = true; sPanel.hidden = true; tText.focus(); }
    });
    tClose.addEventListener('click', function () { tPanel.hidden = true; });

    tGo.addEventListener('click', function () {
      var raw = tText.value.trim();
      if (raw.length < 200) { tMsg.textContent = 'Paste a longer transcript or notes first.'; return; }
      tGo.disabled = true;
      tGo.textContent = 'Writing…';
      tMsg.textContent = 'Writing the draft. This can take up to a minute.';
      var style = tPanel.querySelector('input[name="ai_style"]:checked').value;
      aiPost('/admin/ai/draft', { text: raw, style: style }).then(function (d) {
        tGo.disabled = false;
        tGo.textContent = 'Write draft';
        if (!d.ok) { tMsg.textContent = d.error || 'Something went wrong. Please try again.'; return; }
        pasteText.value = d.draft;
        pasteMsg.textContent = 'Draft ready. Read it through and change anything you like, then tap Build blocks.' +
          (d.cut ? ' The transcript was very long, so only the first part was used.' : '');
        tMsg.textContent = '';
        tPanel.hidden = true;
        panel.hidden = false;
        if (panel.scrollIntoView) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    });

    function option(label, text, meta) {
      var b = el('button', 'admin-suggest__opt');
      b.type = 'button';
      b.appendChild(el('span', 'admin-suggest__label', label));
      b.appendChild(el('span', 'admin-suggest__text', text));
      b.addEventListener('click', function () {
        applyMeta(meta);
        Array.prototype.forEach.call(sList.querySelectorAll('.admin-suggest__opt'), function (o) {
          if (o.querySelector('.admin-suggest__label').textContent === label) o.classList.remove('is-used');
        });
        b.classList.add('is-used');
        sMsg.textContent = label + ' set. You can still change it at the top of the page.';
      });
      return b;
    }

    function runSuggest() {
      var text = storyText();
      sList.textContent = '';
      if (text.length < 120) { sMsg.textContent = 'Write a little more of the story first, then ask for suggestions.'; return; }
      sBtn.disabled = true;
      sAgain.disabled = true;
      sMsg.textContent = 'Reading your story…';
      aiPost('/admin/ai/suggest', { text: text, title: (form.elements.title && form.elements.title.value) || '' }).then(function (d) {
        sBtn.disabled = false;
        sAgain.disabled = false;
        if (!d.ok) { sMsg.textContent = d.error || 'Something went wrong. Please try again.'; return; }
        sMsg.textContent = 'Tap one to use it.';
        (d.titles || []).forEach(function (t) { sList.appendChild(option('Title', t, { title: t })); });
        if (d.dek) sList.appendChild(option('Summary', d.dek, { dek: d.dek }));
        if (d.category) {
          sList.appendChild(option('Category', d.category_label + (d.subcategory_label ? ' · ' + d.subcategory_label : ''),
            { category: d.category, subcategory: d.subcategory }));
        }
      });
    }

    sBtn.addEventListener('click', function () {
      if (!sPanel.hidden) { sPanel.hidden = true; return; }
      sPanel.hidden = false;
      panel.hidden = true;
      tPanel.hidden = true;
      runSuggest();
    });
    sAgain.addEventListener('click', runSuggest);
    sClose.addEventListener('click', function () { sPanel.hidden = true; });
  }

  // ───────────────────────────── wiring ─────────────────────────────

  Array.prototype.forEach.call(container.querySelectorAll('.admin-block'), enhanceBlock);
  new MutationObserver(function (muts) {
    muts.forEach(function (m) {
      Array.prototype.forEach.call(m.addedNodes, function (n) {
        if (n.nodeType === 1 && n.classList.contains('admin-block')) enhanceBlock(n);
      });
    });
  }).observe(container, { childList: true });
})();
