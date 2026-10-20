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

  function tabbed(tabs, initialId) {
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
    }

    draw();
    return { node: el('div', {}, [nav, panel]), redraw: draw };
  }

  /* -------------------------------------------------------------- font picker */

  function fontPicker(script, value, onChange) {
    var fonts = Theme.fontsFor(script);
    var sampleText = script === 'gu' ? 'કવિતા' : script === 'hi' ? 'कविता' : 'Writing';

    var grid = el('div', { class: 'opt-grid' }, fonts.map(function (font) {
      Theme.ensureFont(font.id);
      var btn = el('button', {
        class: 'opt' + (font.id === value ? ' is-active' : ''),
        type: 'button',
        onclick: function () {
          value = font.id;
          UI.$$('.opt', grid).forEach(function (n, i) {
            n.classList.toggle('is-active', fonts[i].id === font.id);
          });
          onChange(font.id);
        }
      }, [
        el('div', {
          class: 'opt-sample',
          text: sampleText,
          style: { fontFamily: Theme.fontStack(font.id, script) }
        }),
        el('span', { text: font.label })
      ]);
      return btn;
    }));
    return grid;
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
      host.appendChild(UI.optGrid(types, target.bgType, function (id) {
        target.bgType = id;
        onChange();
        draw();
      }));

      if (target.bgType === 'solid') {
        host.appendChild(UI.field('Colour', UI.colorRow(target.bgColor, function (v) {
          target.bgColor = v; onChange();
        })));
      }

      if (target.bgType === 'gradient') {
        host.appendChild(el('div', { class: 'field-row' }, [
          UI.field('From', UI.colorRow(target.gradFrom, function (v) {
            target.gradFrom = v; onChange();
          })),
          UI.field('To', UI.colorRow(target.gradTo, function (v) {
            target.gradTo = v; onChange();
          }))
        ]));
        host.appendChild(UI.field('Angle', UI.rangeRow(0, 360, 5, Number(target.gradAngle) || 160,
          function (v) { return v + '°'; },
          function (v) { target.gradAngle = v; onChange(); })));

        host.appendChild(el('div', { class: 'field' }, [
          el('label', { class: 'field-label', text: 'Quick pairs' }),
          el('div', { class: 'opt-grid' }, [
            ['#1B2735', '#090A0F'], ['#FF9A6C', '#C2427D'], ['#134E5E', '#71B280'],
            ['#F6D365', '#FDA085'], ['#4B1248', '#F0C27B'], ['#2C3E50', '#4CA1AF'],
            ['#B24592', '#F15F79'], ['#232526', '#414345']
          ].map(function (pair) {
            return el('button', {
              class: 'opt',
              type: 'button',
              title: pair[0] + ' → ' + pair[1],
              style: { padding: '0', height: '40px', overflow: 'hidden',
                       background: 'linear-gradient(135deg,' + pair[0] + ',' + pair[1] + ')' },
              onclick: function () {
                target.gradFrom = pair[0];
                target.gradTo = pair[1];
                onChange();
                draw();
              }
            });
          }))
        ]));
      }

      if (target.bgType === 'image') {
        var urlInput = el('input', {
          class: 'input', type: 'url', value: target.bgImage || '',
          placeholder: 'https://… or pick a file',
          oninput: function () { target.bgImage = urlInput.value; onChange(); }
        });
        host.appendChild(UI.field('Image', el('div', {}, [
          urlInput,
          el('button', {
            class: 'btn btn-sm',
            style: { marginTop: '8px' },
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
          }, [icon('upload'), el('span', { text: 'Choose file' })])
        ]), 'Any photo works — it is shrunk automatically and stored inside your content, so it keeps working offline.'));

        host.appendChild(UI.field('Darken', UI.rangeRow(0, 0.85, 0.05, Number(target.bgDim) || 0,
          function (v) { return Math.round(v * 100) + '%'; },
          function (v) { target.bgDim = v; onChange(); }),
          'Darkening the picture is what keeps the words readable on top of it.'));

        host.appendChild(UI.field('Blur', UI.rangeRow(0, 24, 1, Number(target.bgBlur) || 0,
          function (v) { return v + 'px'; },
          function (v) { target.bgBlur = v; onChange(); })));
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

  function openEntryEditor(existing) {
    if (!Store.canEdit()) {
      UI.toast('Your writing has not loaded yet. Reload the page first.', 'error');
      return;
    }
    var isNew = !existing;
    // Edit a copy, so hitting Cancel really does leave the original alone.
    var draft = Store.normaliseEntry(existing ? Store.clone(existing) : {
      type: 'poem',
      lang: I18n.getLang() === 'en' ? 'en' : I18n.getLang(),
      date: Store.today()
    });

    var preview = el('div', {
      style: {
        borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)',
        padding: '20px', marginBottom: '18px', overflow: 'hidden',
        position: 'relative', minHeight: '120px'
      }
    });

    // Rebound each time the Write tab renders; a no-op before then.
    var applyWritingFont = function () {};

    function drawPreview() {
      UI.clear(preview);
      preview.removeAttribute('style');
      preview.className = '';
      Object.assign(preview.style, {
        borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)',
        padding: '20px', marginBottom: '18px', overflow: 'hidden',
        position: 'relative', minHeight: '120px', background: 'var(--surface-2)'
      });

      var s = Views.styleFor(draft);
      if (s.bg) {
        var bgNode = el('div', {
          style: {
            position: 'absolute', inset: '0',
            backgroundImage: s.bg.image, backgroundColor: s.bg.color,
            backgroundSize: 'cover', backgroundPosition: 'center',
            filter: s.bg.blur ? 'blur(' + s.bg.blur + 'px)' : ''
          }
        });
        preview.appendChild(bgNode);
        preview.style.color = s.bg.dark ? '#FFFFFF' : '#14110D';
      }

      var inner = el('div', { style: { position: 'relative' }, lang: draft.lang });
      if (draft.title) {
        var ti = el('div', {
          text: draft.title,
          style: { fontSize: '21px', fontWeight: '620', marginBottom: '10px', lineHeight: '1.3' }
        });
        ti.style.fontFamily = s.css.fontFamily;
        if (s.css.textAlign) ti.style.textAlign = s.css.textAlign;
        inner.appendChild(ti);
      }
      var bodyNode = el('div', {
        class: draft.style.frame && draft.style.frame !== 'none' ? 'reader-body frame-' + draft.style.frame : '',
        text: draft.body || 'Your writing will appear here…',
        style: { whiteSpace: 'pre-wrap', lineHeight: '1.9', opacity: draft.body ? '1' : '.4' }
      });
      Object.keys(s.css).forEach(function (k) { bodyNode.style[k] = s.css[k]; });
      inner.appendChild(bodyNode);
      preview.appendChild(inner);
    }

    /* ---- Write tab ---- */
    function writeTab() {
      var host = el('div', {});

      host.appendChild(el('div', { class: 'field-row' }, [
        UI.field('Kind', (function () {
          var sel = el('select', { class: 'select', onchange: function () {
            draft.type = sel.value; drawPreview();
          } });
          ['poem', 'blog', 'quote'].forEach(function (id) {
            sel.appendChild(el('option', { value: id, selected: draft.type === id, text: Views.typeLabel(id) }));
          });
          return sel;
        })()),
        UI.field('Language', (function () {
          var sel = el('select', { class: 'select', onchange: function () {
            draft.lang = sel.value;
            // Type Gujarati in a Gujarati face: retag the fields so the
            // browser shapes the script correctly while you are writing it.
            applyWritingFont();
            drawPreview();
          } });
          I18n.LANGS.forEach(function (lang) {
            sel.appendChild(el('option', { value: lang.code, selected: draft.lang === lang.code, text: lang.label }));
          });
          return sel;
        })())
      ]));

      var titleInput = el('input', {
        class: 'input', value: draft.title, placeholder: 'Title (optional for quotes)',
        oninput: function () { draft.title = titleInput.value; drawPreview(); }
      });
      host.appendChild(UI.field('Title', titleInput));

      var bodyInput = el('textarea', {
        class: 'textarea textarea-tall',
        placeholder: 'Write here. Line breaks are kept exactly as you type them.',
        oninput: function () { draft.body = bodyInput.value; drawPreview(); }
      });
      bodyInput.value = draft.body;
      host.appendChild(UI.field('Writing', bodyInput));

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
          oninput: function (e) { draft.date = e.target.value; }
        })),
        UI.field('Tags', el('input', {
          class: 'input', value: (draft.tags || []).join(', '), placeholder: 'love, monsoon, ગઝલ',
          oninput: function (e) {
            draft.tags = e.target.value.split(',')
              .map(function (x) { return x.trim(); })
              .filter(Boolean);
          }
        }))
      ]));

      host.appendChild(UI.field('Credit / note', el('input', {
        class: 'input', value: draft.credit, placeholder: 'e.g. written in Ahmedabad, 2024',
        oninput: function (e) { draft.credit = e.target.value; }
      })));

      var collections = data().collections || [];
      if (collections.length) {
        host.appendChild(el('div', { class: 'field' }, [
          el('label', { class: 'field-label', text: 'Collections' }),
          el('div', { class: 'chip-row', style: { flexWrap: 'wrap', gap: '6px' } },
            collections.map(function (col) {
              var on = (draft.collections || []).indexOf(col.id) !== -1;
              return el('button', {
                class: 'chip' + (on ? ' is-active' : ''),
                type: 'button',
                text: col.name || 'Untitled',
                onclick: function (e) {
                  var idx = (draft.collections || []).indexOf(col.id);
                  if (idx === -1) draft.collections.push(col.id);
                  else draft.collections.splice(idx, 1);
                  e.currentTarget.classList.toggle('is-active', idx === -1);
                }
              });
            }))
        ]));
      }

      host.appendChild(el('div', { style: { marginTop: '10px' } }, [
        UI.switchRow('Pin to the top', 'Pinned pieces lead every list.', draft.pinned,
          function (v) { draft.pinned = v; }),
        UI.switchRow('Keep as draft', 'Only you can see a draft. It is not published.', draft.draft,
          function (v) { draft.draft = v; })
      ]));

      return host;
    }

    /* ---- Style tab ---- */
    function styleTab() {
      var st = draft.style;
      var host = el('div', {});

      host.appendChild(el('div', { class: 'section-title', text: 'Typeface' }));
      host.appendChild(el('div', { class: 'field' }, [
        el('label', { class: 'field-label', text: 'Font for this piece' }),
        el('div', { class: 'field-hint', style: { marginBottom: '10px', marginTop: '0' },
          text: 'Leave unset to follow the site font for ' + t('lang.' + draft.lang) + '.' }),
        el('button', {
          class: 'btn btn-sm' + (st.font ? '' : ' btn-primary'),
          type: 'button',
          style: { marginBottom: '10px' },
          text: 'Use site font',
          onclick: function () { st.font = ''; drawPreview(); rebuildStyle(); }
        }),
        fontPicker(draft.lang, st.font, function (id) { st.font = id; drawPreview(); })
      ]));

      host.appendChild(UI.field('Size', UI.rangeRow(0.75, 2.2, 0.05, Number(st.size) || 1,
        function (v) { return Math.round(v * 100) + '%'; },
        function (v) { st.size = v; drawPreview(); })));

      host.appendChild(UI.field('Line spacing', UI.rangeRow(1.2, 3, 0.05, Number(st.lineHeight) || 1.85,
        function (v) { return v.toFixed(2); },
        function (v) { st.lineHeight = v; drawPreview(); })));

      host.appendChild(UI.field('Letter spacing', UI.rangeRow(-0.03, 0.25, 0.005, Number(st.letterSpacing) || 0,
        function (v) { return v.toFixed(3) + 'em'; },
        function (v) { st.letterSpacing = v; drawPreview(); })));

      host.appendChild(el('div', { class: 'field' }, [
        el('label', { class: 'field-label', text: 'Alignment' }),
        UI.optGrid([
          { id: 'left', label: 'Left' }, { id: 'center', label: 'Centre' },
          { id: 'right', label: 'Right' }, { id: 'justify', label: 'Justify' }
        ], st.align, function (id) { st.align = id; drawPreview(); rebuildStyle(); })
      ]));

      host.appendChild(el('div', { class: 'section-title', text: 'Colour & background' }));

      var colorWrap = el('div', {});
      function drawColor() {
        UI.clear(colorWrap);
        colorWrap.appendChild(el('button', {
          class: 'btn btn-sm' + (st.textColor ? '' : ' btn-primary'),
          type: 'button',
          style: { marginBottom: '10px' },
          text: 'Automatic',
          onclick: function () { st.textColor = ''; drawPreview(); drawColor(); }
        }));
        colorWrap.appendChild(UI.colorRow(st.textColor || '#FFFFFF', function (v) {
          st.textColor = v; drawPreview();
        }));
      }
      drawColor();
      host.appendChild(UI.field('Text colour', colorWrap,
        'Automatic picks black or white based on how dark the background is.'));

      host.appendChild(el('div', { class: 'field' }, [
        el('label', { class: 'field-label', text: 'Background' }),
        backgroundEditor(st, {}, function () { drawPreview(); })
      ]));

      host.appendChild(el('div', { style: { marginTop: '4px' } },
        UI.switchRow('Text shadow', 'Helps words stay readable over a photo.', st.textShadow,
          function (v) { st.textShadow = v; drawPreview(); })));

      host.appendChild(el('div', { class: 'section-title', text: 'Framing' }));
      host.appendChild(UI.optGrid([
        { id: 'none', label: 'None' }, { id: 'rule', label: 'Side rule' },
        { id: 'box', label: 'Box' }, { id: 'quotes', label: 'Quote mark' }
      ], st.frame || 'none', function (id) { st.frame = id; drawPreview(); rebuildStyle(); }));

      host.appendChild(el('button', {
        class: 'btn btn-sm btn-ghost',
        type: 'button',
        style: { marginTop: '20px' },
        text: 'Reset styling to defaults',
        onclick: function () {
          draft.style = Store.clone(Store.DEFAULT_STYLE);
          drawPreview();
          rebuildStyle();
        }
      }));

      return host;
    }

    var tabs = tabbed([
      { id: 'write', label: 'Write', render: writeTab },
      { id: 'style', label: 'Style', render: styleTab }
    ]);

    function rebuildStyle() { tabs.redraw(); }

    function save() {
      if (!String(draft.body || '').trim()) {
        UI.toast('Write something first.', 'error');
        return;
      }
      if (isNew) Store.addEntry(draft);
      else Store.updateEntry(existing.id, draft);
      UI.closeSheet();
      UI.toast(t('toast.saved'), 'ok');
      App.rerender();
    }

    var foot = [
      el('button', { class: 'btn', text: t('action.cancel'), onclick: function () { UI.closeSheet(); } }),
      el('button', { class: 'btn btn-primary', text: t('action.save'), onclick: save })
    ];

    if (!isNew) {
      foot.unshift(el('button', {
        class: 'btn btn-danger',
        style: { flex: '0 0 auto' },
        'aria-label': t('action.delete'),
        onclick: function () {
          UI.confirmAction({
            title: t('action.delete') + '?',
            message: 'This removes the piece from your site. It cannot be undone once you publish.',
            danger: true
          }).then(function (yes) {
            if (!yes) return;
            Store.removeEntry(existing.id);
            UI.closeSheet();
            UI.toast(t('toast.deleted'), 'ok');
            App.rerender();
          });
        }
      }, icon('trash')));
    }

    drawPreview();
    UI.openSheet({
      title: isNew ? t('action.new') : t('action.edit'),
      variant: 'center',
      dismissible: false,
      body: el('div', {}, [preview, tabs.node]),
      foot: foot
    });
  }

  global.Editor = {
    openUnlock: openUnlock,
    openSetPassphrase: openSetPassphrase,
    openEntryEditor: openEntryEditor,
    tabbed: tabbed,
    fontPicker: fontPicker,
    backgroundEditor: backgroundEditor,
    touched: touched
  };
})(window);
