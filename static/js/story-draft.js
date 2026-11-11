/* Drafts and autosave for the admin story editor (loaded on every admin page).
 *
 * Two layers of protection:
 *
 *  1. Autosave -- while you type, the whole form (fields, every block, and any
 *     photos you picked) is copied into this browser (localStorage for the
 *     text, IndexedDB for the photo files). Nothing is sent anywhere. If the
 *     tab closes or the laptop dies, the next time you open the editor it
 *     offers to restore it.
 *
 *  2. "Save draft" -- sends the story to the server as a draft: it is stored
 *     with the other stories but kept off the public site until you publish.
 *     On the live site every save is a GitHub commit and a Render redeploy,
 *     which is why this one is a button and the autosave stays in the browser.
 *
 * On pages without the editor this file only tidies up after a publish.
 */
(function () {
  'use strict';

  var PREFIX = 'column-editor:';
  var CLEAR_FLAG = 'column-editor-clear';
  var DB_NAME = 'column-editor';
  var STORE = 'files';
  var MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;

  // ── Small IndexedDB helpers for photo files (best effort: text still saves without them) ──
  function openDb() {
    return new Promise(function (resolve, reject) {
      if (!window.indexedDB) { reject(new Error('IndexedDB unavailable')); return; }
      var req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = function () { req.result.createObjectStore(STORE); };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error); };
    });
  }
  function dropFiles(prefix) {
    return openDb().then(function (db) {
      return new Promise(function (resolve) {
        var tx = db.transaction(STORE, 'readwrite');
        tx.objectStore(STORE).delete(IDBKeyRange.bound(prefix, prefix + '￿'));
        tx.oncomplete = function () { resolve(); };
        tx.onerror = function () { resolve(); };
      });
    }).catch(function () {});
  }
  function putFiles(prefix, files) {
    var names = Object.keys(files);
    return openDb().then(function (db) {
      return new Promise(function (resolve) {
        var tx = db.transaction(STORE, 'readwrite');
        var store = tx.objectStore(STORE);
        store.delete(IDBKeyRange.bound(prefix, prefix + '￿'));
        names.forEach(function (name) { store.put(files[name], prefix + name); });
        tx.oncomplete = function () { resolve(); };
        tx.onerror = function () { resolve(); };
      });
    }).catch(function () {});
  }
  function getFiles(prefix) {
    return openDb().then(function (db) {
      return new Promise(function (resolve) {
        var out = {};
        var tx = db.transaction(STORE, 'readonly');
        var req = tx.objectStore(STORE).openCursor(IDBKeyRange.bound(prefix, prefix + '￿'));
        req.onsuccess = function () {
          var cur = req.result;
          if (cur) { out[String(cur.key).slice(prefix.length)] = cur.value; cur.continue(); }
        };
        tx.oncomplete = function () { resolve(out); };
        tx.onerror = function () { resolve(out); };
      });
    }).catch(function () { return {}; });
  }

  function lsGet(key) {
    try { var raw = localStorage.getItem(key); return raw ? JSON.parse(raw) : null; } catch (e) { return null; }
  }
  function lsSet(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch (e) { return false; } }
  function lsRemove(key) { try { localStorage.removeItem(key); } catch (e) {} }

  function forgetDraft(slugKey) {
    lsRemove(PREFIX + slugKey);
    return dropFiles(PREFIX + slugKey + '/');
  }

  // Throw away copies nobody came back for in a month.
  function sweepOld() {
    try {
      for (var i = localStorage.length - 1; i >= 0; i--) {
        var k = localStorage.key(i);
        if (k && k.indexOf(PREFIX) === 0) {
          var snap = lsGet(k);
          if (!snap || !snap.savedAt || Date.now() - snap.savedAt > MAX_AGE_MS) {
            forgetDraft(k.slice(PREFIX.length));
          }
        }
      }
    } catch (e) {}
  }

  function clock(d) {
    return (d || new Date()).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  }

  var form = document.getElementById('story-form');

  // ── Not on the editor: after a successful publish, forget the browser copy ──
  if (!form) {
    sweepOld();
    try {
      var pending = sessionStorage.getItem(CLEAR_FLAG);
      if (pending && document.querySelector('.admin-flash--success, .admin-flash--warning')) {
        sessionStorage.removeItem(CLEAR_FLAG);
        forgetDraft(pending);
      }
    } catch (e) {}
    return;
  }

  // ── On the editor ──
  try { sessionStorage.removeItem(CLEAR_FLAG); } catch (e) {}

  var ed = window.__storyEditor;
  if (!ed) return;
  var container = document.getElementById('blocks-container');
  var statusEl = document.getElementById('draft-status');
  var slugField = document.getElementById('draft-slug');
  var saveDraftBtn = document.getElementById('save-draft-btn');
  var slugKey = form.getAttribute('data-draft-key') || 'new';
  var storageKey = PREFIX + slugKey;
  var filePrefix = storageKey + '/';
  var postUrl = form.getAttribute('action') || location.pathname; // stays put even after the address bar changes
  var hasServerErrors = !!document.querySelector('.admin-flash--error');

  var dirtyServer = false;   // changes not yet saved to the server
  var saving = false;
  var submitting = false;
  var timer = null;
  var lastLocalAt = null;
  var lastServerAt = null;

  function setStatus(text, cls) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.className = 'admin-draft-status' + (cls ? ' ' + cls : '');
  }
  function refreshStatus() {
    if (dirtyServer) {
      setStatus(lastLocalAt ? 'Unsaved changes · autosaved on this device at ' + clock(lastLocalAt) : 'Unsaved changes…');
    } else if (lastServerAt) {
      setStatus('Draft saved at ' + clock(lastServerAt));
    } else {
      setStatus('');
    }
  }

  // ── Reading the form into a plain object ──
  var TOP_TEXT = ['title', 'dek', 'category', 'subcategory', 'author_slug', 'author_custom', 'published_at'];
  var TOP_CHECK = ['featured', 'remove_cover_image'];

  function capture() {
    var fields = {};
    var files = {};
    var blocks = [];
    TOP_TEXT.forEach(function (n) { var el = form.elements[n]; if (el) fields[n] = el.value; });
    TOP_CHECK.forEach(function (n) { var el = form.elements[n]; if (el) fields[n] = !!el.checked; });
    var cover = form.elements.cover_image;
    if (cover && cover.files && cover.files[0]) files.cover = cover.files[0];

    Array.prototype.forEach.call(container.querySelectorAll('.admin-block'), function (node, i) {
      var id = node.dataset.blockId;
      var typeEl = node.querySelector('input[name^="block_type_"]');
      var block = { type: typeEl ? typeEl.value : '', vals: {}, gallery: null };
      Array.prototype.forEach.call(node.querySelectorAll('[name]'), function (el) {
        var name = el.name;
        if (name.indexOf('block_type_') === 0 || name.indexOf('block_gallery_') === 0) return;
        var key = name.split(id).join('{ID}');
        if (el.type === 'file') {
          if (el.files && el.files[0]) files[i + ':' + key] = el.files[0];
          return;
        }
        block.vals[key] = el.type === 'checkbox' ? !!el.checked : el.value;
      });
      if (block.type === 'gallery') {
        block.gallery = [];
        Array.prototype.forEach.call(node.querySelectorAll('.admin-gallery__row'), function (row, j) {
          var fileEl = row.querySelector('input[type=file]');
          var capEl = row.querySelector('input[type=text]');
          var keepEl = row.querySelector('input[type=hidden]');
          if (fileEl && fileEl.files && fileEl.files[0]) files[i + ':g' + j] = fileEl.files[0];
          block.gallery.push({ caption: capEl ? capEl.value : '', existing: keepEl ? keepEl.value : '' });
        });
      }
      blocks.push(block);
    });
    return { fields: fields, blocks: blocks, files: files };
  }

  function signature(state) {
    return JSON.stringify({ f: state.fields, b: state.blocks, k: Object.keys(state.files).sort() });
  }

  // ── Writing a saved copy back into the form ──
  function fire(el, type) { el.dispatchEvent(new Event(type, { bubbles: true })); }

  function setFile(input, file) {
    try {
      var dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      fire(input, 'change');
    } catch (e) { /* very old browser: pick the photo again by hand */ }
  }

  function restore(snap, savedFiles) {
    var f = snap.fields || {};
    TOP_TEXT.forEach(function (n) {
      var el = form.elements[n];
      if (!el || !(n in f) || n === 'subcategory') return;
      el.value = f[n];
      fire(el, n === 'category' || n === 'author_slug' ? 'change' : 'input');
    });
    var sub = form.elements.subcategory;               // after category, so its options exist
    if (sub && 'subcategory' in f) { sub.value = f.subcategory; fire(sub, 'change'); }
    TOP_CHECK.forEach(function (n) {
      var el = form.elements[n];
      if (el && n in f) { el.checked = !!f[n]; fire(el, 'change'); }
    });

    ed.clear();
    (snap.blocks || []).forEach(function (b, i) {
      var v = b.vals || {};
      var data = {};
      if (b.type === 'photo') data.image = v['block_existing_image_{ID}'] || '';
      if (b.type === 'compare') {
        data.before = v['block_existing_before_{ID}'] || '';
        data.after = v['block_existing_after_{ID}'] || '';
      }
      if (b.type === 'gallery') {
        data.photos = (b.gallery || []).map(function (g) { return { image: g.existing || '', caption: g.caption || '' }; });
      }
      if (b.type === 'places') {
        // The rows have to exist before their saved values can be put back.
        data.items = (v['block_places_order_{ID}'] || '').split(',').filter(Boolean).map(function (rid) {
          return {
            url: v['block_places_url_{ID}_' + rid] || '', book_url: v['block_places_book_{ID}_' + rid] || '',
            book_label: v['block_places_label_{ID}_' + rid] || '', video: v['block_places_existing_video_{ID}_' + rid] || ''
          };
        });
      }
      var node = ed.addBlock(b.type, data);
      if (!node) return;
      var id = node.dataset.blockId;
      Array.prototype.forEach.call(node.querySelectorAll('[name]'), function (el) {
        var key = el.name.split(id).join('{ID}');
        if (el.type === 'file' || !(key in v)) return;
        if (el.type === 'checkbox') el.checked = !!v[key]; else el.value = v[key];
        fire(el, el.tagName === 'SELECT' ? 'change' : 'input');
      });
      Object.keys(savedFiles).forEach(function (k) {
        var parts = k.split(':');
        if (String(i) !== parts[0]) return;
        var rest = parts.slice(1).join(':');
        if (/^g\d+$/.test(rest)) {
          var rows = node.querySelectorAll('.admin-gallery__row');
          var row = rows[parseInt(rest.slice(1), 10)];
          var input = row && row.querySelector('input[type=file]');
          if (input) setFile(input, savedFiles[k]);
        } else {
          var target = node.querySelector('[name="' + rest.split('{ID}').join(id) + '"]');
          if (target) setFile(target, savedFiles[k]);
        }
      });
    });
    if (savedFiles.cover && form.elements.cover_image) setFile(form.elements.cover_image, savedFiles.cover);
    ed.syncOrder();
    if (window.updateHomepagePreview) window.updateHomepagePreview();
  }

  // ── Autosave to this browser ──
  function saveLocal() {
    timer = null;
    var state = capture();
    var ok = lsSet(storageKey, { v: 1, savedAt: Date.now(), fields: state.fields, blocks: state.blocks, fileKeys: Object.keys(state.files) });
    putFiles(filePrefix, state.files);
    if (ok) lastLocalAt = new Date();
    refreshStatus();
  }
  function scheduleSave() {
    dirtyServer = true;
    if (!saving) refreshStatus();
    if (timer) clearTimeout(timer);
    timer = setTimeout(saveLocal, 1200);
  }

  form.addEventListener('input', scheduleSave);
  form.addEventListener('change', scheduleSave);
  new MutationObserver(scheduleSave).observe(container, { childList: true, subtree: true });
  window.addEventListener('pagehide', function () { if (dirtyServer && !submitting) saveLocal(); });
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden' && dirtyServer && !submitting) saveLocal();
  });

  // Enter inside a one-line field used to submit (publish!) the whole form.
  form.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' || e.isComposing) return;
    var t = e.target;
    if (t && t.tagName === 'INPUT' && t.type !== 'submit' && t.type !== 'button' && t.type !== 'checkbox') e.preventDefault();
  });

  window.addEventListener('beforeunload', function (e) {
    if (dirtyServer && !submitting) { e.preventDefault(); e.returnValue = ''; }
  });

  // ── Normal submits (Publish / Save changes / Move to drafts) ──
  form.addEventListener('submit', function (e) {
    var sub = e.submitter;
    if (sub && sub.id === 'move-to-drafts-btn' &&
        !window.confirm('Move this story to drafts? It will come off the site until you publish it again.')) {
      e.preventDefault();
      return;
    }
    submitting = true;
    if (timer) { clearTimeout(timer); timer = null; }
    try { sessionStorage.setItem(CLEAR_FLAG, slugKey); } catch (err) {}
  });

  // ── "Save draft" (stays on the page) ──
  function saveDraft() {
    if (saving) return;
    saving = true;
    if (timer) { clearTimeout(timer); saveLocal(); }
    ed.syncOrder();
    var fd = new FormData(form);
    fd.set('action', 'draft');
    if (saveDraftBtn) saveDraftBtn.disabled = true;
    setStatus('Saving draft…', 'is-saving');

    fetch('/admin/story/draft', {
      method: 'POST', body: fd, credentials: 'same-origin', headers: { Accept: 'application/json' }
    }).then(function (res) {
      return res.json().then(function (j) { return j; }, function () {
        return { ok: false, error: 'Unexpected response (' + res.status + '). If you were logged out, log in again in another tab — your work is safe on this device.' };
      });
    }).then(function (data) {
      if (!data.ok) throw new Error(data.error || 'Could not save the draft.');
      slugField.value = data.slug;
      form.setAttribute('action', postUrl);          // Publish keeps posting to the page we loaded
      try { history.replaceState(null, '', data.edit_url); } catch (e) {}
      dirtyServer = false;
      lastServerAt = new Date();
      forgetDraft(slugKey);
      var title = document.querySelector('.admin-page-head h1');
      if (title && /^New story/.test(title.textContent)) title.textContent = 'Edit draft';
      refreshStatus();
      if (data.warnings && data.warnings.length) setStatus('Draft saved at ' + clock(lastServerAt) + ' — skipped: ' + data.warnings.join(' '), 'is-error');
    }).catch(function (err) {
      dirtyServer = true;
      setStatus('Could not save the draft: ' + err.message + ' (still autosaved on this device)', 'is-error');
    }).then(function () {
      saving = false;
      if (saveDraftBtn) saveDraftBtn.disabled = false;
    });
  }
  if (saveDraftBtn) {
    saveDraftBtn.addEventListener('click', function (e) { e.preventDefault(); saveDraft(); });
  }
  document.addEventListener('keydown', function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's' && saveDraftBtn) { e.preventDefault(); saveDraft(); }
  });

  // ── Offer to restore a browser copy that differs from what the page loaded with ──
  function offerRestore() {
    if (hasServerErrors) return;                        // the server already put your text back
    var snap = lsGet(storageKey);
    if (!snap) return;
    var baseline = signature(capture());
    var snapSig = JSON.stringify({ f: snap.fields, b: snap.blocks, k: (snap.fileKeys || []).slice().sort() });
    if (baseline === snapSig) { forgetDraft(slugKey); return; }

    var when = new Date(snap.savedAt);
    var bar = document.createElement('div');
    bar.className = 'admin-restore';
    bar.innerHTML =
      '<span class="admin-restore__text">Unsaved work from ' + clock(when) +
      (when.toDateString() === new Date().toDateString() ? '' : ' on ' + when.toLocaleDateString()) +
      ' was autosaved on this device. Restore it?</span>' +
      '<span class="admin-restore__actions">' +
      '<button type="button" class="admin-btn admin-btn--primary" data-act="restore">Restore it</button>' +
      '<button type="button" class="admin-btn" data-act="discard">Discard</button></span>';
    form.parentNode.insertBefore(bar, form);

    bar.addEventListener('click', function (e) {
      var act = e.target && e.target.getAttribute('data-act');
      if (act === 'discard') { forgetDraft(slugKey); bar.remove(); }
      if (act === 'restore') {
        getFiles(filePrefix).then(function (saved) {
          restore(snap, saved);
          bar.remove();
          dirtyServer = true;
          lastLocalAt = when;
          refreshStatus();
        });
      }
    });
  }

  offerRestore();
  sweepOld();
})();
