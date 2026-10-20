/* =============================================================================
   editor.js — everything only the admin sees: the unlock gate, the writing
   editor, and the settings panels.

   On what "admin" actually means here. This is a static site: there is no
   server to check a password against, so the passphrase gate is a convenience,
   not a wall — a determined visitor can flip a flag in devtools and see these
   panels. That costs nothing, because editing here only ever touches their own
   browser. Changing the real site requires a GitHub token, which lives solely
   in the admin's localStorage and appears in no file this site ever serves.
   ========================================================================== */
(function (global) {
  'use strict';

  var el = UI.el, icon = UI.icon, t = function () { return I18n.t.apply(I18n, arguments); };

  function data() { return Store.state.data; }

  function touched() {
    Store.markDirty();
    Theme.applyTheme(data().theme);
  }

  /* ------------------------------------------------------------- tabbed body */

  function tabbed(tabs, initialId, onSwitch) {
    var activeId = initialId || tabs[0].id;
    var nav = el('div', { class: 'settings-nav' });
    var panel = el('div', { style: { paddingTop: '18px' } });

    function draw() {
      UI.clear(nav);
      tabs.forEach(function (tab) {
        nav.appendChild(el('button', {
          class: 'tab' + (tab.id === activeId ? ' is-active' : ''),
          type: 'button',
          text: tab.label,
          onclick: function () { activeId = tab.id; draw(); }
        }));
      });
      UI.clear(panel);
      var current = tabs.filter(function (x) { return x.id === activeId; })[0];
      if (current) panel.appendChild(current.render());
      if (onSwitch) onSwitch(activeId);
    }

    draw();
    return {
      node: el('div', {}, [nav, panel]),
      redraw: draw,
      active: function () { return activeId; },
      show: function (id) { activeId = id; draw(); }
    };
  }

  /* -------------------------------------------------------------- font picker */
  /* opts.noneLabel adds a first choice meaning "not set" — "Site font" for the
     writing, "Same as writing" for the heading — previewed in opts.noneFamily. */
  function fontPicker(script, value, onChange, opts) {
    opts = opts || {};
    var sampleText = script === 'gu' ? 'કવિતા' : script === 'hi' ? 'कविता' : 'Writing';
    var items = [];
    if (opts.noneLabel) items.push({ id: '', label: opts.noneLabel, family: opts.noneFamily || 'inherit' });
    Theme.fontsFor(script).forEach(function (f) {
      Theme.ensureFont(f.id);
      items.push({ id: f.id, label: f.label, family: Theme.fontStack(f.id, script) });
    });

    var grid = el('div', { class: 'opt-grid' }, items.map(function (item) {
      return el('button', {
        class: 'opt' + (item.id === (value || '') ? ' is-active' : ''),
        type: 'button',
        onclick: function () {
          value = item.id;
          UI.$$('.opt', grid).forEach(function (n, i) {
            n.classList.toggle('is-active', items[i].id === item.id);
          });
          onChange(item.id);
        }
      }, [
        el('div', { class: 'opt-sample', text: sampleText, style: { fontFamily: item.family } }),
        el('span', { text: item.label })
      ]);
    }));
    return grid;
  }

  /* ----------------------------------------------------------- colour choice */
  /* "Automatic" or a chosen colour. Automatic means: work it out from the
     background, which is almost always what you want. */
  function colourChoice(value, autoLabel, onChange) {
    var host = el('div', {});
    var current = value || '';
    function draw() {
      UI.clear(host);
      host.appendChild(UI.segmented([
        { id: 'auto', label: autoLabel || 'Automatic' },
        { id: 'custom', label: 'Choose' }
      ], current ? 'custom' : 'auto', function (id) {
        current = id === 'auto' ? '' : (current || '#FFFFFF');
        onChange(current);
        draw();
      }));
      if (current) {
        var row = UI.colorRow(current, function (v) { current = v; onChange(v); });
        row.style.marginTop = '10px';
        host.appendChild(row);
      }
    }
    draw();
    return host;
  }

  /* --------------------------------------------------------- gradient editor */
  /* Any number of colours from two to six, each at its own position, drawn
     straight or as a circle. Stored as gradStops; gradFrom/gradTo are kept in
     step with the first and last colour so an older copy of the site still
     shows something close. */
  var MAX_STOPS = 6;

  function gradientEditor(target, onChange) {
    var host = el('div', {});

    function stops() {
      if (!Array.isArray(target.gradStops) || target.gradStops.length < 2) {
        target.gradStops = Theme.gradientStops(target).map(function (s) { return { c: s.c, p: s.p }; });
      }
      return target.gradStops;
    }

    function sync() {
      var list = stops().slice().sort(function (a, b) { return a.p - b.p; });
      target.gradFrom = list[0].c;
      target.gradTo = list[list.length - 1].c;
      onChange();
      paintStrip();
    }

    var strip = el('div', { class: 'grad-strip' });
    function paintStrip() {
      strip.style.backgroundImage = Theme.gradientCSS({ gradStops: stops(), gradKind: 'linear', gradAngle: 90 });
    }

    function addStop() {
      var list = stops().slice().sort(function (a, b) { return a.p - b.p; });
      // Put the new colour in the widest gap, blended from its neighbours.
      var at = 0, widest = -1;
      for (var i = 0; i < list.length - 1; i++) {
        var w = list[i + 1].p - list[i].p;
        if (w > widest) { widest = w; at = i; }
      }
      var a = list[at], b = list[at + 1];
      target.gradStops = list.concat([{ c: Theme.mixHex(a.c, b.c, 0.5), p: Math.round((a.p + b.p) / 2) }])
        .sort(function (x, y) { return x.p - y.p; });
      sync();
      draw();
    }

    function draw() {
      UI.clear(host);
      paintStrip();
      host.appendChild(strip);

      host.appendChild(el('div', { class: 'field' }, [
        el('label', { class: 'field-label', text: 'Shape' }),
        UI.segmented([
          { id: 'linear', label: 'Straight' },
          { id: 'radial', label: 'Circle' }
        ], target.gradKind || 'linear', function (id) {
          target.gradKind = id;
          onChange();
          draw();
        })
      ]));

      if ((target.gradKind || 'linear') === 'linear') {
        host.appendChild(UI.field('Direction', UI.rangeRow(0, 360, 5, Number(target.gradAngle) || 160,
          function (v) { return v + '°'; },
          function (v) { target.gradAngle = v; onChange(); })));
      }

      var list = stops();
      host.appendChild(el('label', { class: 'field-label', text: 'Colours (' + list.length + ')' }));
      var rows = el('div', { class: 'stop-list' });
      list.forEach(function (stop, i) {
        rows.appendChild(el('div', { class: 'stop-row' }, [
          UI.colorRow(stop.c, function (v) { stop.c = v; sync(); }),
          el('div', { class: 'stop-pos' }, UI.rangeRow(0, 100, 1, stop.p,
            function (v) { return v + '%'; },
            function (v) { stop.p = v; sync(); })),
          list.length > 2 ? el('button', {
            class: 'icon-btn', type: 'button', 'aria-label': 'Remove this colour', title: 'Remove this colour',
            onclick: function () { list.splice(i, 1); sync(); draw(); }
          }, icon('x')) : el('span', { class: 'stop-spacer' })
        ]));
      });
      host.appendChild(rows);

      if (list.length < MAX_STOPS) {
        host.appendChild(el('button', {
          class: 'btn btn-sm', type: 'button', style: { marginTop: '4px' }, onclick: addStop
        }, [icon('plus'), el('span', { text: 'Add a colour' })]));
      }

      host.appendChild(el('div', { class: 'field', style: { marginTop: '18px' } }, [
        el('label', { class: 'field-label', text: 'Ready-made' }),
        el('div', { class: 'opt-grid grad-presets' }, Theme.GRADIENTS.map(function (g) {
          return el('button', {
            class: 'opt grad-opt', type: 'button', title: g.label,
            onclick: function () {
              target.gradStops = Theme.spreadStops(g.stops);
              sync();
              draw();
            }
          }, [
            el('div', { class: 'opt-swatch', style: {
              backgroundImage: Theme.gradientCSS({ gradStops: Theme.spreadStops(g.stops), gradAngle: 135 })
            } }),
            el('span', { text: g.label })
          ]);
        }))
      ]));
    }

    draw();
    return host;
  }

  /* --------------------------------------------------------- background editor */
  /* Shared by the entry editor and the site theme: both need "solid colour,
     gradient, or image" with the same controls. */
  function backgroundEditor(target, opts, onChange) {
    opts = opts || {};
    var host = el('div', {});

    var types = [{ id: opts.inheritId || 'inherit', label: opts.inheritLabel || 'Theme' }]
      .concat([
        { id: 'solid', label: 'Colour' },
        { id: 'gradient', label: 'Gradient' },
        { id: 'image', label: 'Image' }
      ]);

    function draw() {
      UI.clear(host);
      host.appendChild(UI.segmented(types, target.bgType, function (id) {
        target.bgType = id;
        onChange();
        draw();
      }, { wrap: true }));

      var panel = el('div', { style: { marginTop: '16px' } });
      host.appendChild(panel);

      if (target.bgType === 'solid') {
        panel.appendChild(UI.field('Colour', UI.colorRow(target.bgColor, function (v) {
          target.bgColor = v; onChange();
        })));
      }

      if (target.bgType === 'gradient') {
        panel.appendChild(gradientEditor(target, onChange));
      }

      if (target.bgType === 'image') {
        var urlInput = el('input', {
          class: 'input', type: 'url',
          value: /^data:/.test(target.bgImage || '') ? '(uploaded image)' : (target.bgImage || ''),
          placeholder: 'https://… or choose a photo',
          oninput: function () { target.bgImage = urlInput.value; onChange(); }
        });
        panel.appendChild(UI.field('Image', el('div', {}, [
          el('button', {
            class: 'btn btn-sm btn-primary',
            style: { marginBottom: '8px' },
            type: 'button',
            onclick: function () {
              UI.pickImage(function (dataUrl, info) {
                target.bgImage = dataUrl;
                urlInput.value = '(uploaded image)';
                onChange();
                if (info && info.originalKb > info.kb * 1.5) {
                  UI.toast('Image added — shrunk from ' + info.originalKb + ' KB to ' + info.kb + ' KB.', 'ok');
                }
              });
            }
          }, [icon('upload'), el('span', { text: 'Choose a photo' })]),
          urlInput
        ]), 'Any photo works — it is shrunk automatically and stored inside your content, so it keeps working offline.'));

        panel.appendChild(UI.field('Darken', UI.rangeRow(0, 0.85, 0.05, Number(target.bgDim) || 0,
          function (v) { return Math.round(v * 100) + '%'; },
          function (v) { target.bgDim = v; onChange(); }),
          'Darkening the picture is what keeps the words readable on top of it.'));

        panel.appendChild(UI.field('Blur', UI.rangeRow(0, 24, 1, Number(target.bgBlur) || 0,
          function (v) { return v + 'px'; },
          function (v) { target.bgBlur = v; onChange(); })));
      }
    }

    draw();
    return host;
  }

  /* ------------------------------------------------------------- frame editor */
  /* Which controls mean something for which frame. Showing a "rounding" slider
     for a side line just invites fiddling that changes nothing. */
  var FRAME_CONTROLS = {
    rule:     ['color', 'width', 'style', 'pad'],
    box:      ['color', 'width', 'radius', 'style', 'pad'],
    double:   ['color', 'width', 'radius', 'pad'],
    corners:  ['color', 'width', 'pad'],
    card:     ['color', 'width', 'radius', 'style', 'pad'],
    ornament: ['color', 'width', 'pad'],
    lines:    ['color', 'pad'],
    quotes:   ['color', 'pad'],
    stamp:    ['color', 'width', 'radius', 'style', 'pad']
  };

  function frameMini(type) {
    var box = el('div', { class: 'frame-mini' + (type === 'none' ? '' : ' framed frame-' + type) }, [
      el('i'), el('i'), el('i')
    ]);
    return box;
  }

  function frameEditor(st, onChange) {
    var host = el('div', {});

    function draw() {
      UI.clear(host);
      var type = st.frame || 'none';

      host.appendChild(el('div', { class: 'opt-grid frame-grid' }, Theme.FRAMES.map(function (f) {
        return el('button', {
          class: 'opt' + (f.id === type ? ' is-active' : ''), type: 'button',
          onclick: function () { st.frame = f.id; onChange(); draw(); }
        }, [frameMini(f.id), el('span', { text: f.label })]);
      })));

      var controls = FRAME_CONTROLS[type];
      if (!controls) return;
      var panel = el('div', { style: { marginTop: '18px' } });
      host.appendChild(panel);

      if (controls.indexOf('color') !== -1) {
        panel.appendChild(UI.field('Colour', colourChoice(st.frameColor, 'Accent colour', function (v) {
          st.frameColor = v; onChange();
        })));
      }
      if (controls.indexOf('width') !== -1) {
        panel.appendChild(UI.field('Thickness', UI.rangeRow(1, 12, 1, Number(st.frameWidth) || 2,
          function (v) { return v + 'px'; },
          function (v) { st.frameWidth = v; onChange(); })));
      }
      if (controls.indexOf('radius') !== -1) {
        panel.appendChild(UI.field('Corner rounding', UI.rangeRow(0, 40, 1, Number(st.frameRadius) || 0,
          function (v) { return v + 'px'; },
          function (v) { st.frameRadius = v; onChange(); })));
      }
      if (controls.indexOf('pad') !== -1) {
        panel.appendChild(UI.field('Space inside', UI.rangeRow(0, 64, 2, Number(st.framePad) || 0,
          function (v) { return v + 'px'; },
          function (v) { st.framePad = v; onChange(); })));
      }
      if (controls.indexOf('style') !== -1) {
        panel.appendChild(el('div', { class: 'field' }, [
          el('label', { class: 'field-label', text: 'Line' }),
          UI.segmented([
            { id: 'solid', label: 'Solid' }, { id: 'dashed', label: 'Dashed' },
            { id: 'dotted', label: 'Dotted' }, { id: 'double', label: 'Double' }
          ], st.frameStyle || 'solid', function (id) { st.frameStyle = id; onChange(); })
        ]));
      }
    }

    draw();
    return host;
  }

  /* =========================================================== unlock gate  */

  function openUnlock() {
    if (!Store.hasPassphrase()) return openSetPassphrase();

    var input = el('input', {
      class: 'input', type: 'password', autofocus: true,
      placeholder: t('admin.passphrase'),
      onkeydown: function (e) { if (e.key === 'Enter') submit(); }
    });
    var error = el('div', {
      class: 'field-hint',
      style: { color: '#E5484D', display: 'none' },
      text: t('admin.wrongPass')
    });
    var button = el('button', { class: 'btn btn-primary', text: t('admin.unlock'), onclick: function () { submit(); } });

    function submit() {
      if (!input.value) return;
      button.disabled = true;
      error.style.display = 'none';
      Store.checkPassphrase(input.value).then(function (ok) {
        button.disabled = false;
        if (!ok) {
          error.style.display = 'block';
          input.value = '';
          input.focus();
          return;
        }
        Store.unlock();
        UI.closeSheet();
        UI.toast(t('admin.badge'), 'ok');
        App.rerender();
      }).catch(function () {
        button.disabled = false;
        error.textContent = 'This browser cannot check the passphrase (needs a secure page).';
        error.style.display = 'block';
      });
    }

    UI.openSheet({
      title: t('admin.unlock'),
      variant: 'center',
      body: el('div', {}, [UI.field(t('admin.passphrase'), input), error]),
      foot: [button]
    });
  }

  function openSetPassphrase() {
    var a = el('input', { class: 'input', type: 'password', autofocus: true, placeholder: 'Passphrase' });
    var b = el('input', { class: 'input', type: 'password', placeholder: 'Repeat it' });
    var error = el('div', { class: 'field-hint', style: { color: '#E5484D', display: 'none' } });

    function save() {
      if (a.value.length < 6) {
        error.textContent = 'Use at least 6 characters.';
        error.style.display = 'block';
        return;
      }
      if (a.value !== b.value) {
        error.textContent = 'The two do not match.';
        error.style.display = 'block';
        return;
      }
      Store.setPassphrase(a.value).then(function () {
        Store.unlock();
        UI.closeSheet();
        UI.toast('Admin passphrase set', 'ok');
        App.rerender();
      }).catch(function () {
        error.textContent = 'This browser cannot set a passphrase (needs a secure page).';
        error.style.display = 'block';
      });
    }

    UI.openSheet({
      title: t('admin.setPass'),
      variant: 'center',
      body: el('div', {}, [
        el('p', {
          class: 'field-hint',
          style: { marginBottom: '18px' },
          text: t('admin.setPassHint') + ' It is saved with your content, hashed — never in plain text.'
        }),
        UI.field('Passphrase', a),
        UI.field('Repeat', b),
        error
      ]),
      foot: [el('button', { class: 'btn btn-primary', text: t('action.save'), onclick: save })]
    });
  }

  /* ========================================================== entry editor  */

  /* One-tap looks. Each is a complete style that suits poetry, built per
     script so a Gujarati poem gets a Gujarati serif and not an English one. */
  var FACES = {
    serif:   { gu: 'Noto Serif Gujarati', hi: 'Tiro Devanagari Hindi', en: 'Lora' },
    display: { gu: 'Rasa',                hi: 'Eczar',                 en: 'Playfair Display' },
    hand:    { gu: 'Baloo Bhai 2',        hi: 'Kalam',                 en: 'Caveat' },
    sans:    { gu: 'Anek Gujarati',       hi: 'Anek Devanagari',       en: 'Outfit' }
  };

  function spread(colours) { return Theme.spreadStops(colours); }

  var LOOKS = [
    { id: 'classic', label: 'Classic', make: function (lang) { return {
        font: FACES.serif[lang], align: 'center', bgType: 'inherit', frame: 'none',
        textColor: '', titleColor: '', weight: 400, italic: false, underline: false,
        titleItalic: false, titleWeight: 0, textShadow: false }; } },
    { id: 'night', label: 'Night sky', make: function (lang) { return {
        font: FACES.serif[lang], align: 'center', bgType: 'gradient', gradKind: 'linear', gradAngle: 170,
        gradStops: spread(['#0B0C2A', '#1B1F4B', '#2B2E83']), textColor: '', titleColor: '#FFD86F',
        titleItalic: true, frame: 'ornament', frameColor: '#FFD86F', framePad: 20, textShadow: false }; } },
    { id: 'sunset', label: 'Sunset', make: function (lang) { return {
        font: FACES.display[lang], align: 'center', bgType: 'gradient', gradKind: 'linear', gradAngle: 160,
        gradStops: spread(['#FF5F6D', '#FFC371', '#FFE29F']), textColor: '#2B1B12', titleColor: '#7A1F2B',
        titleItalic: true, frame: 'none', textShadow: false }; } },
    { id: 'notebook', label: 'Notebook', make: function (lang) { return {
        font: FACES.hand[lang], align: 'left', bgType: 'solid', bgColor: '#FBF6EA', textColor: '#2B2520',
        titleColor: '#1E3A8A', titleItalic: false, frame: 'lines', frameColor: '#7FA7D9', framePad: 22,
        textShadow: false }; } },
    { id: 'quote', label: 'Quote card', make: function (lang) { return {
        font: FACES.display[lang], size: 1.2, weight: 600, align: 'center', bgType: 'gradient',
        gradKind: 'linear', gradAngle: 135, gradStops: spread(['#4B1248', '#B24592', '#F15F79', '#F0C27B']),
        textColor: '', titleColor: '', frame: 'quotes', frameColor: '#FFFFFF', textShadow: true }; } },
    { id: 'golden', label: 'Black & gold', make: function (lang) { return {
        font: FACES.serif[lang], align: 'center', bgType: 'solid', bgColor: '#0A0A0A', textColor: '#F2EFE7',
        titleColor: '#D4AF37', frame: 'double', frameColor: '#D4AF37', frameWidth: 2, frameRadius: 4,
        framePad: 26, textShadow: false }; } },
    { id: 'monsoon', label: 'Monsoon', make: function (lang) { return {
        font: FACES.sans[lang], align: 'center', bgType: 'gradient', gradKind: 'radial',
        gradStops: spread(['#C4DFE6', '#66A5AD', '#07575B', '#003B46']), textColor: '', titleColor: '',
        frame: 'card', frameRadius: 18, framePad: 24, textShadow: false }; } },
    { id: 'plain', label: 'Plain', make: function () { return Store.clone(Store.DEFAULT_STYLE); } }
  ];

  function applyLook(style, look, lang) {
    var patch = look.make(lang);
    Object.keys(patch).forEach(function (k) { style[k] = Store.clone(patch[k]); });
    if (Array.isArray(style.gradStops) && style.gradStops.length >= 2) {
      style.gradFrom = style.gradStops[0].c;
      style.gradTo = style.gradStops[style.gradStops.length - 1].c;
    }
    (patch.font ? [patch.font] : []).forEach(Theme.ensureFont);
  }

  /* Render a piece the way the reader will see it, into `host`. Used by the
     editor's live preview and by the small previews on the Look tab. */
  function paintPiece(host, entry, opts) {
    opts = opts || {};
    UI.clear(host);
    var s = Views.styleFor(entry);
    host.className = (opts.className || '') + ' reader ' + (s.bg ? s.cls : '');
    if (s.bg) {
      var bg = el('div', { class: 'reader-bg' });
      bg.style.backgroundImage = s.bg.image;
      bg.style.backgroundColor = s.bg.color;
      if (s.bg.blur) bg.style.filter = 'blur(' + s.bg.blur + 'px)';
      host.appendChild(bg);
    }
    var inner = el('div', { class: 'preview-inner' });
    if (entry.title) {
      var title = el('div', { class: 'preview-title', text: entry.title });
      Views.applyStyle(title, Views.titleStyleFor(entry).css);
      inner.appendChild(title);
    }
    var body = Rich.render(el('div', { class: 'reader-body' }), entry.body || opts.placeholder || '');
    if (!entry.body && opts.placeholder) body.style.opacity = '.45';
    Views.applyStyle(body, s.css);
    Views.applyFrame(body, entry);
    if (opts.scale) {
      // Shrink a frame's spacing along with the text for the small previews.
      ['--fw', '--fr', '--fp'].forEach(function (k) {
        var v = parseFloat(body.style.getPropertyValue(k));
        if (v) body.style.setProperty(k, Math.max(k === '--fw' ? 1 : 0, Math.round(v * opts.scale)) + 'px');
      });
    }
    inner.appendChild(body);
    host.appendChild(inner);
    return host;
  }

  var EDIT_STASH = 'pp.editing.v1';

  function openEntryEditor(existing) {
    if (!Store.canEdit()) {
      UI.toast('Your writing has not loaded yet. Reload the page first.', 'error');
      return;
    }
    var isNew = !existing;
    var target = existing ? existing.id : '';
    // Edit a copy, so cancelling really does leave the original alone.
    var draft = Store.normaliseEntry(existing ? Store.clone(existing) : {
      type: 'poem',
      lang: I18n.getLang(),
      date: Store.today()
    });
    var initial = JSON.stringify(draft);
    var langTouched = !isNew;                 // an existing piece keeps its language
    var applyWritingFont = function () {};
    var bodyInput = null;

    function isDirty() { return JSON.stringify(draft) !== initial; }

    /* Keep a copy of the editor's contents on the device as you type, so a
       closed tab, a dead battery or a mis-tap does not take the poem with it. */
    var stashTimer = null;
    function stash() {
      clearTimeout(stashTimer);
      stashTimer = setTimeout(function () {
        if (isDirty()) Store.writeLS(EDIT_STASH, { target: target, entry: draft, at: Date.now() });
      }, 350);
    }
    function clearStash() { clearTimeout(stashTimer); Store.removeLS(EDIT_STASH); }

    function changed() { drawPreview(); stash(); }

    /* ---- live preview, pinned above the style tabs ---- */
    var preview = el('div', { class: 'editor-preview' });
    function drawPreview() {
      paintPiece(preview, draft, {
        className: 'editor-preview',
        placeholder: 'Your writing will appear here…'
      });
    }

    /* ---- restore offer ---- */
    var restoreHost = el('div', {});
    (function offerRestore() {
      var saved = Store.readLS(EDIT_STASH, null);
      if (!saved || saved.target !== target || !saved.entry) return;
      if (JSON.stringify(saved.entry) === initial) return;
      var mins = Math.max(1, Math.round((Date.now() - (saved.at || 0)) / 60000));
      var when = mins < 60 ? mins + ' min ago' : mins < 1440 ? Math.round(mins / 60) + ' h ago' : Math.round(mins / 1440) + ' days ago';
      var words = Views.stats(saved.entry).words;
      restoreHost.appendChild(el('div', { class: 'notice notice-info restore-note' }, [
        icon('refresh'),
        el('span', { text: 'You have unsaved writing here from ' + when + ' (' + words + ' words). ' }),
        el('button', { class: 'btn btn-sm btn-primary', type: 'button', text: 'Restore it', onclick: function () {
          draft = Store.normaliseEntry(saved.entry);
          langTouched = true;
          UI.clear(restoreHost);
          tabs.redraw();
          drawPreview();
        } }),
        el('button', { class: 'btn btn-sm btn-ghost', type: 'button', text: 'Discard', onclick: function () {
          clearStash();
          UI.clear(restoreHost);
        } })
      ]));
    })();

    /* ================================================================ Write */
    function writeTab() {
      var host = el('div', {});

      host.appendChild(el('div', { class: 'field' }, [
        el('label', { class: 'field-label', text: 'What is it?' }),
        UI.segmented([
          { id: 'poem', label: Views.typeLabel('poem'), icon: 'feather' },
          { id: 'blog', label: Views.typeLabel('blog'), icon: 'book' },
          { id: 'quote', label: Views.typeLabel('quote'), icon: 'quote' }
        ], draft.type, function (id) { draft.type = id; changed(); })
      ]));

      var langHint = el('div', { class: 'field-hint' });
      var langSeg = UI.segmented(I18n.LANGS.map(function (l) {
        return { id: l.code, label: l.label };
      }), draft.lang, function (id) {
        langTouched = true;
        langHint.textContent = '';
        setLang(id);
      });
      function setLang(code, auto) {
        draft.lang = code;
        langSeg.setValue(code);
        applyWritingFont();
        if (auto) {
          var name = (I18n.LANGS.filter(function (l) { return l.code === code; })[0] || {}).label;
          langHint.textContent = 'Set to ' + name + ' from what you typed — tap another if that is wrong.';
        }
        changed();
      }
      host.appendChild(el('div', { class: 'field' }, [
        el('label', { class: 'field-label', text: 'Language' }), langSeg, langHint
      ]));

      /* Pick the language from the script being typed, until the author
         chooses one themselves. Gujarati and Devanagari are unambiguous;
         English only wins once there is enough of it to be sure. */
      function autoLang() {
        if (langTouched) return;
        var text = (draft.title || '') + '\n' + (draft.body || '');
        var guess = Share.detectLang(text);
        if (guess === draft.lang) return;
        if (guess === 'en' && (text.match(/[A-Za-z]/g) || []).length < 12) return;
        setLang(guess, true);
      }

      var titleInput = el('input', {
        class: 'input', value: draft.title, placeholder: 'Title (optional for quotes)',
        oninput: function () { draft.title = titleInput.value; autoLang(); changed(); }
      });
      host.appendChild(UI.field('Title', titleInput));

      bodyInput = el('textarea', {
        class: 'textarea textarea-tall',
        placeholder: 'Write here. Line breaks are kept exactly as you type them.',
        oninput: function () {
          draft.body = bodyInput.value;
          autoLang();
          drawCount();
          changed();
        }
      });
      bodyInput.value = draft.body;

      function fmt(token, label, title, style) {
        return el('button', {
          class: 'fmt-btn', type: 'button', title: title, 'aria-label': title,
          // Keep the textarea's selection: do not let the button take focus.
          onpointerdown: function (e) { e.preventDefault(); },
          onclick: function () { Rich.toggleInTextarea(bodyInput, token); }
        }, el('span', { text: label, style: style }));
      }
      var toolbar = el('div', { class: 'format-bar' }, [
        fmt('**', 'B', 'Bold — select words first', { fontWeight: '800' }),
        fmt('*', 'I', 'Italic — select words first', { fontStyle: 'italic', fontFamily: 'Georgia, serif' }),
        fmt('__', 'U', 'Underline — select words first', { textDecoration: 'underline' }),
        el('span', { class: 'format-hint', text: 'Select words, then tap B, I or U' })
      ]);

      var count = el('div', { class: 'field-hint count-line' });
      function drawCount() {
        var lines = String(draft.body || '').split('\n').filter(function (l) { return l.trim(); }).length;
        var words = Views.stats(draft).words;
        count.textContent = draft.body ? lines + (lines === 1 ? ' line' : ' lines') + ' · ' +
                                         words + (words === 1 ? ' word' : ' words') : '';
      }
      drawCount();

      host.appendChild(el('div', { class: 'field' }, [
        el('label', { class: 'field-label', text: 'Writing' }), toolbar, bodyInput, count
      ]));

      applyWritingFont = function () {
        [titleInput, bodyInput].forEach(function (node) {
          node.setAttribute('lang', draft.lang);
          node.style.fontFamily = 'var(--ff-' + draft.lang + ')';
        });
      };
      applyWritingFont();

      host.appendChild(el('div', { class: 'field-row' }, [
        UI.field('Date', el('input', {
          class: 'input', type: 'date', value: draft.date,
          oninput: function (e) { draft.date = e.target.value; stash(); }
        })),
        UI.field('Credit or note', el('input', {
          class: 'input', value: draft.credit, placeholder: 'e.g. written in Ahmedabad, 2024',
          oninput: function (e) { draft.credit = e.target.value; stash(); }
        }))
      ]));

      host.appendChild(UI.field('Tags', tagInput(), 'Type a tag and press Enter. Tags let readers find related pieces.'));
      host.appendChild(UI.field('Collections', collectionPicker()));

      host.appendChild(el('div', { style: { marginTop: '6px' } }, [
        UI.switchRow('Pin to the top', 'Pinned pieces lead every list.', draft.pinned,
          function (v) { draft.pinned = v; stash(); }),
        UI.switchRow('Keep as draft', 'Only you can see a draft. It is not shown to readers.', draft.draft,
          function (v) { draft.draft = v; stash(); })
      ]));

      /* Put the cursor somewhere useful — but only if the author has not
         already chosen where to type. Moving focus after they tapped into
         the writing box sent the first words of a poem into the title. */
      setTimeout(function () {
        var focusOn = isNew ? (draft.body ? bodyInput : titleInput) : null;
        var active = document.activeElement;
        if (active && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName)) return;
        if (focusOn) try { focusOn.focus({ preventScroll: true }); } catch (err) { /* ignore */ }
      }, 120);
      return host;
    }

    /* Tags as chips: Enter or comma adds one, Backspace on an empty field
       removes the last, and tags already used elsewhere are one tap away. */
    function tagInput() {
      var wrap = el('div', {});
      var box = el('div', { class: 'tag-input' });
      var suggest = el('div', { class: 'tag-suggest' });
      var field = el('input', {
        class: 'tag-input-field', type: 'text', enterkeyhint: 'done',
        placeholder: 'Add a tag…',
        onkeydown: function (e) {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault();
            add(field.value);
          } else if (e.key === 'Backspace' && !field.value && draft.tags.length) {
            draft.tags.pop();
            stash();
            draw();
          }
        },
        oninput: function () {
          if (field.value.indexOf(',') !== -1) add(field.value);
          else drawSuggest();
        },
        onblur: function () { if (field.value.trim()) add(field.value); }
      });

      function add(raw) {
        String(raw).split(',').map(function (x) { return x.trim(); }).filter(Boolean).forEach(function (tag) {
          if (draft.tags.indexOf(tag) === -1) draft.tags.push(tag);
        });
        field.value = '';
        stash();
        draw();
        field.focus();
      }

      function draw() {
        UI.clear(box);
        draft.tags.forEach(function (tag, i) {
          box.appendChild(el('span', { class: 'tag-chip' }, [
            el('span', { text: tag }),
            el('button', {
              type: 'button', 'aria-label': 'Remove ' + tag,
              onclick: function () { draft.tags.splice(i, 1); stash(); draw(); }
            }, icon('x'))
          ]));
        });
        box.appendChild(field);
        drawSuggest();
      }

      function drawSuggest() {
        UI.clear(suggest);
        var typed = field.value.trim().toLowerCase();
        var options = Store.allTags()
          .map(function (x) { return x.tag; })
          .filter(function (tag) {
            return draft.tags.indexOf(tag) === -1 && (!typed || tag.toLowerCase().indexOf(typed) === 0);
          })
          .slice(0, 10);
        options.forEach(function (tag) {
          suggest.appendChild(el('button', {
            class: 'tag-pill', type: 'button', text: '+ ' + tag,
            onpointerdown: function (e) { e.preventDefault(); },
            onclick: function () { add(tag); }
          }));
        });
      }

      box.addEventListener('click', function (e) { if (e.target === box) field.focus(); });
      draw();
      wrap.appendChild(box);
      wrap.appendChild(suggest);
      return wrap;
    }

    /* Collections this piece belongs to, plus making a new one on the spot. */
    function collectionPicker() {
      var host = el('div', {});
      function draw() {
        UI.clear(host);
        var row = el('div', { class: 'chip-row', style: { flexWrap: 'wrap', gap: '6px' } });
        (data().collections || []).forEach(function (col) {
          var on = draft.collections.indexOf(col.id) !== -1;
          row.appendChild(el('button', {
            class: 'chip' + (on ? ' is-active' : ''), type: 'button',
            onclick: function () {
              var i = draft.collections.indexOf(col.id);
              if (i === -1) draft.collections.push(col.id); else draft.collections.splice(i, 1);
              stash();
              draw();
            }
          }, [on ? icon('check') : null, el('span', { text: col.name || 'Untitled' })]));
        });
        var making = el('div', { class: 'new-col', hidden: true });
        var name = el('input', {
          class: 'input', placeholder: 'Name of the new collection',
          onkeydown: function (e) { if (e.key === 'Enter') { e.preventDefault(); create(); } }
        });
        function create() {
          var n = name.value.trim();
          if (!n) { name.focus(); return; }
          var col = Store.addCollection({ name: n, order: (data().collections || []).length });
          draft.collections.push(col.id);
          stash();
          UI.toast('Collection “' + n + '” created', 'ok');
          draw();
        }
        making.appendChild(name);
        making.appendChild(el('button', { class: 'btn btn-sm btn-primary', type: 'button', text: 'Create', onclick: create }));
        row.appendChild(el('button', {
          class: 'chip chip-new', type: 'button',
          onclick: function () { making.hidden = false; name.focus(); }
        }, [icon('plus'), el('span', { text: 'New collection' })]));
        host.appendChild(row);
        host.appendChild(making);
      }
      draw();
      return host;
    }

    /* ================================================================= Look */
    function lookTab() {
      var host = el('div', {});
      host.appendChild(el('p', { class: 'field-hint', style: { margin: '0 0 14px' },
        text: 'One tap sets the font, colours, background and frame together — shown here with your own words. ' +
              'Change any detail afterwards in the other tabs.' }));
      var sample = String(draft.body || '').split('\n').filter(function (l) { return l.trim(); }).slice(0, 2).join('\n') ||
                   (draft.lang === 'gu' ? 'શબ્દોની એક સાંજ' : draft.lang === 'hi' ? 'शब्दों की एक शाम' : 'An evening of words');
      host.appendChild(el('div', { class: 'look-grid' }, LOOKS.map(function (look) {
        var style = Store.clone(draft.style);
        applyLook(style, look, draft.lang);
        var mini = paintPiece(el('div', {}), {
          lang: draft.lang, title: draft.title ? draft.title.slice(0, 24) : '', body: sample, style: style
        }, { className: 'look-mini', scale: 0.4 });
        return el('button', {
          class: 'look-opt', type: 'button',
          onclick: function () {
            applyLook(draft.style, look, draft.lang);
            changed();
            UI.toast('“' + look.label + '” applied', 'ok', 1600);
          }
        }, [mini, el('span', { class: 'look-label', text: look.label })]);
      })));
      return host;
    }

    /* ================================================================= Text */
    var WEIGHTS = [
      { id: 300, label: 'Light' }, { id: 400, label: 'Regular' }, { id: 500, label: 'Medium' },
      { id: 600, label: 'Semibold' }, { id: 700, label: 'Bold' }
    ];

    function textTab() {
      var st = draft.style;
      var host = el('div', {});

      host.appendChild(UI.field('Font', fontPicker(draft.lang, st.font, function (id) {
        st.font = id; changed();
      }, { noneLabel: 'Site font', noneFamily: 'var(--ff-' + draft.lang + ')' })));

      host.appendChild(UI.field('Size', UI.rangeRow(0.75, 2.2, 0.05, Number(st.size) || 1,
        function (v) { return Math.round(v * 100) + '%'; },
        function (v) { st.size = v; changed(); })));

      host.appendChild(el('div', { class: 'field' }, [
        el('label', { class: 'field-label', text: 'Weight' }),
        UI.segmented(WEIGHTS, Number(st.weight) || 400, function (id) { st.weight = id; changed(); }, { wrap: true }),
        el('div', { class: 'field-hint', text: 'Not every font has every weight; the nearest one is used.' })
      ]));

      host.appendChild(el('div', { class: 'field' }, [
        el('label', { class: 'field-label', text: 'Style' }),
        el('div', { class: 'segmented' }, [
          UI.toggleButton(el('span', { text: 'Italic', style: { fontStyle: 'italic' } }), !!st.italic,
            function (v) { st.italic = v; changed(); }),
          UI.toggleButton(el('span', { text: 'Underline', style: { textDecoration: 'underline' } }), !!st.underline,
            function (v) { st.underline = v; changed(); }),
          UI.toggleButton(el('span', { text: 'Shadow' }), !!st.textShadow,
            function (v) { st.textShadow = v; changed(); }, { title: 'Helps words stay readable over a photo' })
        ]),
        el('div', { class: 'field-hint', text: 'These apply to the whole piece. For single words, use B, I and U on the Write tab.' })
      ]));

      host.appendChild(el('div', { class: 'field' }, [
        el('label', { class: 'field-label', text: 'Alignment' }),
        UI.segmented([
          { id: 'left', label: 'Left' }, { id: 'center', label: 'Centre' },
          { id: 'right', label: 'Right' }, { id: 'justify', label: 'Justify' }
        ], st.align || 'left', function (id) { st.align = id; changed(); })
      ]));

      host.appendChild(UI.field('Line spacing', UI.rangeRow(1.2, 3, 0.05, Number(st.lineHeight) || 1.85,
        function (v) { return v.toFixed(2); },
        function (v) { st.lineHeight = v; changed(); })));

      host.appendChild(UI.field('Letter spacing', UI.rangeRow(-0.03, 0.25, 0.005, Number(st.letterSpacing) || 0,
        function (v) { return v.toFixed(3) + 'em'; },
        function (v) { st.letterSpacing = v; changed(); })));

      host.appendChild(UI.field('Colour', colourChoice(st.textColor, 'Automatic', function (v) {
        st.textColor = v; changed();
      }), 'Automatic picks light or dark text to suit the background.'));

      return host;
    }

    /* ============================================================== Heading */
    function headingTab() {
      var st = draft.style;
      var host = el('div', {});
      if (!draft.title) {
        host.appendChild(el('div', { class: 'notice notice-info', style: { marginBottom: '16px' } }, [
          icon('info'), el('span', { text: 'This piece has no title yet — add one on the Write tab to see these settings in the preview.' })
        ]));
      }

      var sameFamily = st.font ? Theme.fontStack(st.font, draft.lang) : 'var(--fh-' + draft.lang + ')';
      host.appendChild(UI.field('Font', fontPicker(draft.lang, st.titleFont, function (id) {
        st.titleFont = id; changed();
      }, { noneLabel: st.font ? 'Same as writing' : 'Site heading font', noneFamily: sameFamily })));

      host.appendChild(UI.field('Size', UI.rangeRow(0.6, 2.5, 0.05, Number(st.titleSize) || 1,
        function (v) { return Math.round(v * 100) + '%'; },
        function (v) { st.titleSize = v; changed(); })));

      host.appendChild(el('div', { class: 'field' }, [
        el('label', { class: 'field-label', text: 'Weight' }),
        UI.segmented([{ id: 0, label: 'Site' }].concat(WEIGHTS.slice(1)).concat([{ id: 800, label: 'Black' }]),
          Number(st.titleWeight) || 0, function (id) { st.titleWeight = id; changed(); }, { wrap: true })
      ]));

      host.appendChild(el('div', { class: 'field' }, [
        el('label', { class: 'field-label', text: 'Style' }),
        el('div', { class: 'segmented' }, [
          UI.toggleButton(el('span', { text: 'Italic', style: { fontStyle: 'italic' } }), !!st.titleItalic,
            function (v) { st.titleItalic = v; changed(); }),
          UI.toggleButton(el('span', { text: 'Underline', style: { textDecoration: 'underline' } }), !!st.titleUnderline,
            function (v) { st.titleUnderline = v; changed(); })
        ])
      ]));

      host.appendChild(el('div', { class: 'field' }, [
        el('label', { class: 'field-label', text: 'Alignment' }),
        UI.segmented([
          { id: '', label: 'Same as writing' }, { id: 'left', label: 'Left' },
          { id: 'center', label: 'Centre' }, { id: 'right', label: 'Right' }
        ], st.titleAlign || '', function (id) { st.titleAlign = id; changed(); }, { wrap: true })
      ]));

      host.appendChild(UI.field('Colour', colourChoice(st.titleColor, 'Same as writing', function (v) {
        st.titleColor = v; changed();
      })));
      return host;
    }

    /* =========================================================== Background */
    function backgroundTab() {
      return el('div', {}, [backgroundEditor(draft.style, {}, function () { changed(); })]);
    }

    /* ================================================================ Frame */
    function frameTab() {
      return el('div', {}, [
        frameEditor(draft.style, function () { changed(); }),
        el('button', {
          class: 'btn btn-sm btn-ghost', type: 'button', style: { marginTop: '22px' },
          text: 'Reset all styling to plain',
          onclick: function () {
            draft.style = Store.clone(Store.DEFAULT_STYLE);
            changed();
            tabs.redraw();
          }
        })
      ]);
    }

    var tabs = tabbed([
      { id: 'write',   label: 'Write',      render: writeTab },
      { id: 'look',    label: 'Look',       render: lookTab },
      { id: 'text',    label: 'Text',       render: textTab },
      { id: 'heading', label: 'Heading',    render: headingTab },
      { id: 'bg',      label: 'Background', render: backgroundTab },
      { id: 'frame',   label: 'Frame',      render: frameTab }
    ], 'write', function (id) {
      // The writing tab shows the words being typed; every other tab needs
      // the preview to see what a change did.
      preview.hidden = id === 'write';
    });

    /* ---- saving ---- */
    function save(andPublish) {
      if (!String(draft.body || '').trim()) {
        UI.toast('Write something first.', 'error');
        tabs.show('write');
        return;
      }
      draft.tags = (draft.tags || []).filter(Boolean);
      if (isNew) Store.addEntry(draft);
      else Store.updateEntry(existing.id, draft);
      clearStash();
      initial = JSON.stringify(draft);
      UI.closeSheet();
      UI.toast(t('toast.saved'), 'ok');
      App.rerender();
      if (andPublish) Publish.publish();
    }

    var canPublish = !!Store.adminSession().token;
    var foot = [];
    if (!isNew) {
      foot.push(el('button', {
        class: 'btn btn-danger', style: { flex: '0 0 auto' }, 'aria-label': t('action.delete'), title: t('action.delete'),
        onclick: function () {
          UI.confirmAction({
            title: t('action.delete') + '?',
            message: 'This removes the piece from your site. It cannot be undone once you publish.',
            danger: true
          }).then(function (yes) {
            if (!yes) return;
            Store.removeEntry(existing.id);
            clearStash();
            initial = JSON.stringify(draft);
            UI.closeSheet();
            UI.toast(t('toast.deleted'), 'ok');
            App.rerender();
          });
        }
      }, icon('trash')));
    }
    if (!canPublish) {
      foot.push(el('button', { class: 'btn', text: t('action.cancel'), onclick: function () { UI.requestClose(); } }));
    }
    foot.push(el('button', { class: 'btn' + (canPublish ? '' : ' btn-primary'), text: t('action.save'), onclick: function () { save(false); } }));
    if (canPublish) {
      foot.push(el('button', { class: 'btn btn-primary', onclick: function () { save(true); } },
        [icon('upload'), el('span', { text: 'Save & publish' })]));
    }

    drawPreview();
    preview.hidden = true;
    var sheet = UI.openSheet({
      title: isNew ? 'New writing' : t('action.edit'),
      variant: 'center',
      dismissible: false,
      body: el('div', {}, [restoreHost, preview, tabs.node]),
      foot: foot,
      beforeClose: function () {
        if (!isDirty()) { clearStash(); return true; }
        return UI.confirmAction({
          title: 'Discard your changes?',
          message: 'What you wrote here has not been saved.',
          confirmText: 'Discard',
          cancelText: 'Keep writing',
          danger: true
        }).then(function (yes) {
          if (yes) clearStash();
          return yes;
        });
      }
    });

    // Ctrl/Cmd + S saves from anywhere inside the editor.
    sheet.addEventListener('keydown', function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        save(false);
      }
    });
  }

  global.Editor = {
    openUnlock: openUnlock,
    openSetPassphrase: openSetPassphrase,
    openEntryEditor: openEntryEditor,
    tabbed: tabbed,
    fontPicker: fontPicker,
    colourChoice: colourChoice,
    gradientEditor: gradientEditor,
    backgroundEditor: backgroundEditor,
    frameEditor: frameEditor,
    paintPiece: paintPiece,
    LOOKS: LOOKS,
    touched: touched
  };
})(window);
