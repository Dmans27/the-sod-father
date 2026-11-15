/* Phone-friendly helpers for the admin story editor.
 *
 *  1. Paste a story   -- paste text written with ## headings, - bullets and a few
 *                        tags and it becomes blocks (same rules as tools/new_story.py).
 *  2. Add-block sheet -- on small screens, one "+ Add block" button opens a menu.
 *  3. Block controls  -- move up/down (drag doesn't work on a phone) and collapse.
 *  4. Place search    -- search the directory by name instead of pasting a link.
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
    if (!panel.hidden) pasteText.focus();
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
    var fab = el('button', 'admin-addfab', '+ Add block');
    fab.type = 'button';
    fab.setAttribute('aria-expanded', 'false');
    var backdrop = el('div', 'admin-addsheet-backdrop');
    var sheetTitle = el('div', 'admin-addsheet-title', 'Add a block');
    addRow.insertBefore(sheetTitle, addRow.firstChild);
    document.body.appendChild(fab);
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
