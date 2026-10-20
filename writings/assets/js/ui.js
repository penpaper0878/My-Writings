/* =============================================================================
   ui.js — the small pieces every view reuses: DOM helpers, inline icons,
   overlay sheets, toasts.
   ========================================================================== */
(function (global) {
  'use strict';

  /* ------------------------------------------------------------ DOM helpers */

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }

  /* Anything interpolated into innerHTML goes through this first. Feedback text
     and imported files are the obvious risks, but a poem containing "<3" is the
     one that actually happens. */
  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  /* For values landing inside a CSS url() or style attribute. */
  function escCss(value) {
    return String(value == null ? '' : value).replace(/["'\\<>();]/g, '');
  }

  /* Only http(s) and data:image URLs may become a src or an href. Blocks
     javascript: and friends arriving through an imported file, along with the
     quotes and parentheses that would let a value escape a CSS url() if it
     ever reached one. Backgrounds use Theme.safeImageUrl, which is stricter. */
  function safeUrl(value) {
    var v = String(value == null ? '' : value).trim();
    if (!v) return '';
    if (/["'<>\\\s]/.test(v)) return '';
    if (/^data:image\/(png|jpe?g|gif|webp|avif|svg\+xml);base64,/i.test(v)) return v;
    if (/^https?:\/\//i.test(v)) return v;
    if (/^\.{0,2}\//.test(v) && !/^\/\//.test(v)) return v;   // site-relative
    return '';
  }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === 'class') node.className = v;
      else if (k === 'html') node.innerHTML = v;
      else if (k === 'text') node.textContent = v;
      else if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
      else if (k.slice(0, 2) === 'on' && typeof v === 'function') {
        node.addEventListener(k.slice(2).toLowerCase(), v);
      } else node.setAttribute(k, v === true ? '' : v);
    });
    (Array.isArray(children) ? children : children ? [children] : [])
      .forEach(function (c) {
        if (c === null || c === undefined || c === false) return;
        node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
      });
    return node;
  }

  function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }

  /* ----------------------------------------------------------------- icons  */
  /* Stroke icons on a 24-grid. `currentColor` everywhere so they inherit. */

  var PATHS = {
    search:    '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    x:         '<path d="M18 6 6 18M6 6l12 12"/>',
    share:     '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="m8.6 13.5 6.8 4M15.4 6.5l-6.8 4"/>',
    download:  '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5M12 15V3"/>',
    printer:   '<path d="M6 9V2h12v7"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><path d="M6 14h12v8H6z"/>',
    image:     '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-4.5-4.5L3 21"/>',
    fileText:  '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z"/><path d="M14 2v5h6M9 13h6M9 17h6"/>',
    link:      '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
    copy:      '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    check:     '<path d="m20 6-11 11-5-5"/>',
    settings:  '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9v.09a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    plus:      '<path d="M12 5v14M5 12h14"/>',
    edit:      '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4z"/>',
    trash:     '<path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M10 11v6M14 11v6"/>',
    lock:      '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    unlock:    '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/>',
    chevronDown:  '<path d="m6 9 6 6 6-6"/>',
    chevronUp:    '<path d="m18 15-6-6-6 6"/>',
    chevronRight: '<path d="m9 18 6-6-6-6"/>',
    arrowLeft: '<path d="M19 12H5M12 19l-7-7 7-7"/>',
    arrowDown: '<path d="M12 5v14M19 12l-7 7-7-7"/>',
    sun:       '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    moon:      '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    globe:     '<circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15 15 0 0 1 0 20 15 15 0 0 1 0-20z"/>',
    message:   '<path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.5 8.5 0 0 1-3.8-.9L3 21l2-4.9A8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5z"/>',
    send:      '<path d="M22 2 11 13M22 2l-7 20-4-9-9-4z"/>',
    book:      '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
    feather:   '<path d="M20.2 3.8a5.5 5.5 0 0 0-7.8 0L3 13.2V21h7.8l9.4-9.4a5.5 5.5 0 0 0 0-7.8z"/><path d="M16 8 2 22M17.5 12.5H9"/>',
    quote:     '<path d="M9 7H5a2 2 0 0 0-2 2v3a2 2 0 0 0 2 2h2a2 2 0 0 1 2 2v1a3 3 0 0 1-3 3"/><path d="M19 7h-4a2 2 0 0 0-2 2v3a2 2 0 0 0 2 2h2a2 2 0 0 1 2 2v1a3 3 0 0 1-3 3"/>',
    pin:       '<path d="M12 15.5V22"/><path d="M8.6 2h6.8l-.9 5.2 3 3.1a1 1 0 0 1-.7 1.7H7.2a1 1 0 0 1-.7-1.7l3-3.1z"/>',
    upload:    '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 9 5-5 5 5M12 4v12"/>',
    folder:    '<path d="M3 7a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    palette:   '<path d="M12 22a10 10 0 1 1 10-10c0 2-1.6 3-3.5 3H16a2 2 0 0 0-1.4 3.4 2 2 0 0 1-1.4 3.4z"/><circle cx="7.5" cy="10.5" r="1.2"/><circle cx="12" cy="7.5" r="1.2"/><circle cx="16.5" cy="10.5" r="1.2"/>',
    type:      '<path d="M4 7V5h16v2M9 19h6M12 5v14"/>',
    layout:    '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/>',
    github:    '<path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.9a3.4 3.4 0 0 0-1-2.6c3.1-.3 6.4-1.5 6.4-7A5.4 5.4 0 0 0 20 4.8a5 5 0 0 0-.1-3.7s-1.2-.3-4 1.5a13.4 13.4 0 0 0-7 0C6 .8 4.8 1.1 4.8 1.1A5 5 0 0 0 4.7 4.8 5.4 5.4 0 0 0 3.2 8.6c0 5.4 3.3 6.6 6.4 7a3.4 3.4 0 0 0-1 2.6V22"/>',
    info:      '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
    alert:     '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
    eye:       '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>',
    eyeOff:    '<path d="M17.9 17.9A10.4 10.4 0 0 1 12 19c-6.4 0-10-7-10-7a18 18 0 0 1 5.1-5.9m3-1A10.4 10.4 0 0 1 12 5c6.4 0 10 7 10 7a18 18 0 0 1-2.2 3.2"/><path d="M9.9 4.2 2 2l20 20"/>',
    refresh:   '<path d="M3 12a9 9 0 0 1 15.2-6.5L21 8"/><path d="M21 3v5h-5M21 12a9 9 0 0 1-15.2 6.5L3 16"/><path d="M3 21v-5h5"/>',
    sliders:   '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
    whatsapp:  '<path d="M20.5 3.5A11 11 0 0 0 3.2 17.3L2 22l4.8-1.2A11 11 0 1 0 20.5 3.5z"/><path d="M8.5 8c.3-.7.6-.7 1-.7h.7c.3 0 .5.2.7.7l.7 1.7c.1.3 0 .5-.2.7l-.5.6c-.2.2-.2.4-.1.6a7 7 0 0 0 3.2 3c.2.1.4.1.6-.1l.6-.6c.2-.2.4-.3.7-.2l1.7.8c.4.2.6.4.6.7v.7c0 .4 0 .7-.6 1a3 3 0 0 1-2.6.4 11 11 0 0 1-6.7-6.6A3 3 0 0 1 8.5 8z"/>',
    heart:     '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1.1 1L12 21l7.7-7.6 1.1-1a5.5 5.5 0 0 0 0-7.8z"/>',
    tag:       '<path d="M12.6 2.6A2 2 0 0 0 11.2 2H4a2 2 0 0 0-2 2v7.2a2 2 0 0 0 .6 1.4l8.7 8.7a2.4 2.4 0 0 0 3.4 0l6.6-6.6a2.4 2.4 0 0 0 0-3.4z"/><circle cx="7.5" cy="7.5" r="1.1"/>',
    grid:      '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>'
  };

  function iconHtml(name, size) {
    var d = PATHS[name];
    if (!d) return '';
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"' +
      (size ? ' width="' + size + '" height="' + size + '"' : '') + '>' + d + '</svg>';
  }

  function icon(name, size) {
    var span = document.createElement('span');
    span.style.display = 'contents';
    span.innerHTML = iconHtml(name, size);
    return span.firstChild || document.createTextNode('');
  }

  /* ----------------------------------------------------------------- toast  */

  function toastHost() {
    var host = $('#toast-host');
    if (!host) {
      host = el('div', { id: 'toast-host', class: 'toast-host' });
      document.body.appendChild(host);
    }
    return host;
  }

  function toast(message, kind, ms) {
    var node = el('div', { class: 'toast', role: 'status' });
    if (kind === 'ok') node.appendChild(icon('check'));
    else if (kind === 'error') node.appendChild(icon('alert'));
    node.appendChild(el('span', { text: message }));
    toastHost().appendChild(node);

    var life = ms || (kind === 'error' ? 5200 : 2600);
    setTimeout(function () {
      node.classList.add('is-out');
      setTimeout(function () { if (node.parentNode) node.parentNode.removeChild(node); }, 260);
    }, life);
    return node;
  }

  /* ----------------------------------------------------------------- sheet  */
  /* A stack, so the share sheet can open on top of the reader and closing it
     returns you to the reader rather than all the way home. */

  var stack = [];

  function backdrop() {
    var bd = $('#sheet-backdrop');
    if (!bd) {
      bd = el('div', { id: 'sheet-backdrop', class: 'sheet-backdrop' });
      bd.addEventListener('click', function () {
        var top = stack[stack.length - 1];
        if (top && top.dismissible !== false) closeSheet();
      });
      document.body.appendChild(bd);
    }
    return bd;
  }

  function lockScroll(on) {
    document.body.style.overflow = on ? 'hidden' : '';
  }

  /* opts: { title, body (Node), foot (Node), variant, dismissible, onClose } */
  function openSheet(opts) {
    opts = opts || {};
    var variant = opts.variant || 'center';

    var head = null;
    if (opts.title !== false) {
      head = el('div', { class: 'sheet-head' }, [
        el('div', { class: 'sheet-title', text: opts.title || '' }),
        opts.headExtra || null,
        el('button', {
          class: 'icon-btn',
          'aria-label': I18n.t('action.close'),
          onclick: function () { requestClose(); }
        }, icon('x'))
      ]);
    }

    var sheet = el('div', {
      class: 'sheet sheet-' + variant,
      role: 'dialog',
      'aria-modal': 'true',
      'aria-label': opts.title || ''
    }, [
      el('div', { class: 'sheet-grip' }),
      head,
      el('div', { class: 'sheet-body' }, opts.body || null),
      opts.foot ? el('div', { class: 'sheet-foot' }, opts.foot) : null
    ]);

    document.body.appendChild(sheet);
    var bd = backdrop();
    lockScroll(true);

    // One frame before adding .is-open, so the entry transition actually runs.
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        bd.classList.add('is-open');
        sheet.classList.add('is-open');
      });
    });

    var entry = {
      node: sheet,
      onClose: opts.onClose,
      beforeClose: opts.beforeClose,
      dismissible: opts.dismissible,
      lastFocus: document.activeElement
    };
    stack.push(entry);

    // Focus the first control so keyboard and screen-reader users land inside.
    // Never take focus from a field the person is already typing in.
    setTimeout(function () {
      if (sheet.contains(document.activeElement)) return;
      var target = sheet.querySelector('[autofocus]') ||
                   sheet.querySelector('input, textarea, select, button');
      if (target) try { target.focus({ preventScroll: true }); } catch (err) { target.focus(); }
    }, 90);

    return sheet;
  }

  function closeSheet() {
    var entry = stack.pop();
    if (!entry) return;

    entry.node.classList.remove('is-open');
    if (!stack.length) {
      backdrop().classList.remove('is-open');
      lockScroll(false);
    }
    setTimeout(function () {
      if (entry.node.parentNode) entry.node.parentNode.removeChild(entry.node);
      if (entry.lastFocus && entry.lastFocus.focus) {
        try { entry.lastFocus.focus({ preventScroll: true }); } catch (err) { /* ignore */ }
      }
      if (typeof entry.onClose === 'function') entry.onClose();
    }, 320);
  }

  /* Close the top sheet, unless it has a beforeClose hook that says not to.
     The hook may return a boolean or a promise of one — the editor uses it to
     ask before throwing away words that have not been saved. */
  function requestClose() {
    var top = stack[stack.length - 1];
    if (!top) return;
    if (typeof top.beforeClose !== 'function') return closeSheet();
    Promise.resolve(top.beforeClose()).then(function (ok) {
      if (ok !== false && stack[stack.length - 1] === top) closeSheet();
    });
  }

  function closeAllSheets() {
    while (stack.length) closeSheet();
  }

  function sheetDepth() { return stack.length; }

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' || !stack.length) return;
    var top = stack[stack.length - 1];
    if (top.dismissible !== false) { e.preventDefault(); closeSheet(); }
  });

  /* Keep Tab inside the topmost sheet. */
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Tab' || !stack.length) return;
    var sheet = stack[stack.length - 1].node;
    var items = $$('a[href], button:not(:disabled), input:not(:disabled), ' +
                   'textarea:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex="-1"])', sheet)
      .filter(function (n) { return n.offsetParent !== null; });
    if (!items.length) return;
    var first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });

  /* ------------------------------------------------------------- confirm    */

  function confirmAction(opts) {
    return new Promise(function (resolve) {
      var settled = false;
      function finish(value) {
        if (settled) return;
        settled = true;
        resolve(value);
      }
      openSheet({
        title: opts.title || '',
        variant: 'center',
        body: el('p', {
          text: opts.message || '',
          style: { fontSize: '14.5px', lineHeight: '1.7', color: 'var(--text-dim)', margin: '4px 0' }
        }),
        foot: [
          el('button', {
            class: 'btn',
            text: opts.cancelText || I18n.t('action.cancel'),
            onclick: function () { finish(false); closeSheet(); }
          }),
          el('button', {
            class: 'btn ' + (opts.danger ? 'btn-danger' : 'btn-primary'),
            text: opts.confirmText || I18n.t('action.delete'),
            onclick: function () { finish(true); closeSheet(); }
          })
        ],
        onClose: function () { finish(false); }
      });
    });
  }

  /* --------------------------------------------------------------- clipboard */

  function copyText(text) {
    if (global.navigator.clipboard && global.isSecureContext) {
      return global.navigator.clipboard.writeText(text);
    }
    // http:// or an older browser — the textarea trick still works there.
    return new Promise(function (resolve, reject) {
      var ta = el('textarea', {
        style: { position: 'fixed', top: '-1000px', opacity: '0' }
      });
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
      document.body.removeChild(ta);
      ok ? resolve() : reject(new Error('copy-failed'));
    });
  }

  function downloadBlob(blob, filename) {
    var url = URL.createObjectURL(blob);
    var a = el('a', { href: url, download: filename });
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
  }

  /* ----------------------------------------------------------- form helpers */

  function field(label, control, hint) {
    return el('div', { class: 'field' }, [
      label ? el('label', { class: 'field-label', text: label }) : null,
      control,
      hint ? el('div', { class: 'field-hint', text: hint }) : null
    ]);
  }

  function switchRow(label, sub, value, onChange) {
    var sw = el('div', { class: 'switch' + (value ? ' is-on' : '') });
    var row = el('div', {
      class: 'switch-row',
      role: 'switch',
      tabindex: '0',
      'aria-checked': value ? 'true' : 'false'
    }, [
      el('div', {}, [
        el('div', { class: 'switch-label', text: label }),
        sub ? el('div', { class: 'switch-sub', text: sub }) : null
      ]),
      sw
    ]);
    function toggle() {
      var next = !sw.classList.contains('is-on');
      sw.classList.toggle('is-on', next);
      row.setAttribute('aria-checked', next ? 'true' : 'false');
      onChange(next);
    }
    row.addEventListener('click', toggle);
    row.addEventListener('keydown', function (e) {
      if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); toggle(); }
    });
    return row;
  }

  function rangeRow(min, max, step, value, format, onChange) {
    var out = el('span', { class: 'range-value', text: format(value) });
    var input = el('input', {
      type: 'range', min: min, max: max, step: step, value: value,
      oninput: function () {
        out.textContent = format(Number(input.value));
        onChange(Number(input.value));
      }
    });
    return el('div', { class: 'range-field' }, [input, out]);
  }

  function colorRow(value, onChange) {
    var hex = /^#[0-9a-f]{6}$/i.test(value || '') ? value : '#888888';
    var picker = el('input', {
      type: 'color', value: hex,
      oninput: function () { text.value = picker.value; onChange(picker.value); }
    });
    var text = el('input', {
      class: 'input', type: 'text', value: value || '', placeholder: '#000000',
      oninput: function () {
        if (/^#[0-9a-f]{6}$/i.test(text.value)) {
          picker.value = text.value;
          onChange(text.value);
        }
      }
    });
    return el('div', { class: 'color-field' }, [picker, text]);
  }

  /* A row of mutually exclusive choices — the "Poem / Blog / Quote" kind of
     picker. Items: { id, label, icon? }. */
  function segmented(items, activeId, onPick, opts) {
    opts = opts || {};
    var row = el('div', { class: 'segmented' + (opts.wrap ? ' segmented-wrap' : ''), role: 'radiogroup' });
    function paint(id) {
      UI.$$('button', row).forEach(function (b, i) {
        var on = items[i].id === id;
        b.classList.toggle('is-active', on);
        b.setAttribute('aria-checked', on ? 'true' : 'false');
      });
    }
    items.forEach(function (item) {
      row.appendChild(el('button', {
        type: 'button',
        role: 'radio',
        class: 'seg',
        title: item.title || null,
        style: item.style || null,
        onclick: function () { paint(item.id); onPick(item.id); }
      }, [item.icon ? icon(item.icon) : null, item.label ? el('span', { text: item.label }) : null]));
    });
    paint(activeId);
    row.setValue = paint;          // let callers move the selection themselves
    return row;
  }

  /* A button that stays pressed — Italic, Underline and the like. */
  function toggleButton(content, on, onChange, opts) {
    opts = opts || {};
    var btn = el('button', {
      type: 'button',
      class: 'seg toggle' + (on ? ' is-active' : ''),
      'aria-pressed': on ? 'true' : 'false',
      title: opts.title || null,
      style: opts.style || null,
      onclick: function () {
        on = !on;
        btn.classList.toggle('is-active', on);
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
        onChange(on);
      }
    }, content);
    return btn;
  }

  function optGrid(items, activeId, onPick) {
    return el('div', { class: 'opt-grid' }, items.map(function (item) {
      return el('button', {
        class: 'opt' + (item.id === activeId ? ' is-active' : ''),
        type: 'button',
        html: (item.swatchHtml || '') + '<span>' + esc(item.label) + '</span>',
        onclick: function () { onPick(item.id); }
      });
    }));
  }

  /* Reads a picked file as a data URL so images survive export/import and work
     offline — no upload host to depend on, and nothing to pay for. */
  /* Raster formats only. An SVG can carry markup of its own, and a picture
     someone dropped into a backup file is not worth the argument — so the
     dialog does not offer one, and a renamed file is refused out loud rather
     than silently doing nothing. */
  var IMAGE_TYPES = 'image/png,image/jpeg,image/gif,image/webp,image/avif';

  /* Pictures live inside the content file, which every reader downloads and
     the editing device keeps a copy of. A phone photo straight off the camera
     is 3–8 MB: enough to make the site slow for everyone and, on a phone, to
     fill the storage drafts are saved in — after which changes stop saving.
     So every picture is scaled down and re-encoded before it is stored. A
     poem's background never needs more than this. */
  function compressImage(file, maxSide) {
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () {
        URL.revokeObjectURL(url);
        var scale = Math.min(1, maxSide / Math.max(img.naturalWidth, img.naturalHeight));
        var w = Math.max(1, Math.round(img.naturalWidth * scale));
        var h = Math.max(1, Math.round(img.naturalHeight * scale));
        var canvas = document.createElement('canvas');
        canvas.width = w;
        canvas.height = h;
        var ctx = canvas.getContext('2d');
        ctx.imageSmoothingQuality = 'high';

        // WebP keeps transparency and is smaller; browsers that cannot encode
        // it hand back PNG instead, in which case fall back to JPEG on white.
        ctx.drawImage(img, 0, 0, w, h);
        var webp = canvas.toDataURL('image/webp', 0.82);
        if (/^data:image\/webp/.test(webp)) return resolve(webp);

        ctx.globalCompositeOperation = 'destination-over';
        ctx.fillStyle = '#FFFFFF';
        ctx.fillRect(0, 0, w, h);
        resolve(canvas.toDataURL('image/jpeg', 0.82));
      };
      img.onerror = function () {
        URL.revokeObjectURL(url);
        reject(new Error('unreadable'));
      };
      img.src = url;
    });
  }

  function readAsDataUrl(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(String(reader.result)); };
      reader.onerror = function () { reject(new Error('unreadable')); };
      reader.readAsDataURL(file);
    });
  }

  /* opts.maxSide: longest edge after scaling (1600 for backgrounds, smaller
     for an avatar). Large camera photos are accepted and shrunk; only truly
     enormous files are turned away. */
  function pickImage(onLoad, opts) {
    if (typeof opts === 'number') opts = {};          // old signature: a byte limit
    opts = opts || {};
    var maxSide = opts.maxSide || 1600;
    var input = el('input', { type: 'file', accept: IMAGE_TYPES, style: { display: 'none' } });
    document.body.appendChild(input);
    input.addEventListener('change', function () {
      var file = input.files && input.files[0];
      document.body.removeChild(input);
      if (!file) return;
      if (IMAGE_TYPES.indexOf(file.type) === -1) {
        toast('Please use a PNG, JPEG, GIF, WebP or AVIF image.', 'error');
        return;
      }
      if (file.size > 30 * 1024 * 1024) {
        toast('That image is over 30 MB. Please choose a smaller one.', 'error');
        return;
      }

      // An animated GIF would lose its animation if redrawn; keep small ones as they are.
      var job = (file.type === 'image/gif' && file.size < 1200000)
        ? readAsDataUrl(file)
        : compressImage(file, maxSide).catch(function () { return readAsDataUrl(file); });

      job.then(function (dataUrl) {
        var kb = Math.round(dataUrl.length * 0.75 / 1024);
        if (kb > 1500) {
          toast('Even after shrinking, that image is ' + kb + ' KB. Please choose a simpler one.', 'error');
          return;
        }
        onLoad(dataUrl, { kb: kb, originalKb: Math.round(file.size / 1024) });
      }).catch(function () {
        toast('Could not read that image.', 'error');
      });
    });
    input.click();
  }

  function pickFile(accept, onLoad) {
    var input = el('input', { type: 'file', accept: accept, style: { display: 'none' } });
    document.body.appendChild(input);
    input.addEventListener('change', function () {
      var file = input.files && input.files[0];
      document.body.removeChild(input);
      if (!file) return;
      var reader = new FileReader();
      reader.onload = function () { onLoad(String(reader.result), file); };
      reader.onerror = function () { toast('Could not read that file.', 'error'); };
      reader.readAsText(file);
    });
    input.click();
  }

  global.UI = {
    $: $, $$: $$, el: el, esc: esc, escCss: escCss, safeUrl: safeUrl, clear: clear,
    icon: icon, iconHtml: iconHtml, PATHS: PATHS,
    toast: toast,
    openSheet: openSheet, closeSheet: closeSheet, closeAllSheets: closeAllSheets, requestClose: requestClose,
    sheetDepth: sheetDepth, confirmAction: confirmAction,
    copyText: copyText, downloadBlob: downloadBlob,
    field: field, switchRow: switchRow, rangeRow: rangeRow, colorRow: colorRow,
    optGrid: optGrid, segmented: segmented, toggleButton: toggleButton, pickImage: pickImage, pickFile: pickFile, compressImage: compressImage
  };
})(window);
