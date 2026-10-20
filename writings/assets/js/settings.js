/* =============================================================================
   settings.js — the admin settings panels. Attaches openSettings() onto Editor.

   Every control writes straight into Store.state.data and calls touched(),
   which saves the draft and re-applies the theme. There is no "save settings"
   button on purpose: you see each change on the real site as you make it, and
   Publish is the one moment anything becomes public.
   ========================================================================== */
(function (global) {
  'use strict';

  var el = UI.el, icon = UI.icon, t = function () { return I18n.t.apply(I18n, arguments); };

  function data() { return Store.state.data; }
  function touched() { Editor.touched(); }

  /* A text input bound to one field of an object. */
  function bound(obj, key, opts) {
    opts = opts || {};
    var node = el(opts.multiline ? 'textarea' : 'input', {
      class: opts.multiline ? 'textarea' : 'input',
      type: opts.type || 'text',
      placeholder: opts.placeholder || '',
      lang: opts.lang || null,
      oninput: function () {
        obj[key] = node.value;
        touched();
        if (opts.onInput) opts.onInput(node.value);
      }
    });
    node.value = obj[key] == null ? '' : obj[key];
    if (opts.lang) node.style.fontFamily = 'var(--ff-' + opts.lang + ')';
    return node;
  }

  function numberSelect(obj, key, options, onAfter) {
    var sel = el('select', {
      class: 'select',
      onchange: function () {
        obj[key] = isNaN(Number(sel.value)) ? sel.value : Number(sel.value);
        touched();
        if (onAfter) onAfter();
      }
    });
    options.forEach(function (o) {
      sel.appendChild(el('option', {
        value: o.value,
        selected: String(obj[key]) === String(o.value),
        text: o.label
      }));
    });
    return sel;
  }

  /* ================================================================ Content */
  /* Everything written, in one list: search it, filter it, edit it, delete it.
     Without this, changing a piece means finding it on the home page first —
     which stops working somewhere around the fiftieth poem, and never worked
     at all for drafts, since those are scattered among the rest. */

  var contentFilter = { q: '', type: '', drafts: false };
  var contentSelection = {};
  var selectMode = false;

  function contentTab() {
    var host = el('div', {});
    var listHost = el('div', {});

    function matching() {
      var list = Store.select({
        type: contentFilter.type || null,
        query: contentFilter.q,
        sortBy: 'date-desc'
      });
      if (contentFilter.drafts) {
        list = list.filter(function (e) { return e.draft; });
      }
      return list;
    }

    function selectedIds() {
      return Object.keys(contentSelection).filter(function (id) {
        return contentSelection[id] && Store.getEntry(id);
      });
    }

    function row(entry) {
      var meta = [
        Views.typeLabel(entry.type),
        I18n.formatDate(entry.date),
        entry.lang.toUpperCase()
      ].join(' · ');

      var actions = el('div', { class: 'row-actions' }, [
        el('button', {
          class: 'icon-btn' + (entry.pinned ? ' is-on' : ''),
          type: 'button',
          title: entry.pinned ? 'Unpin' : 'Pin to the top',
          'aria-label': entry.pinned ? 'Unpin' : 'Pin to the top',
          onclick: function (e) {
            e.stopPropagation();
            Store.updateEntry(entry.id, { pinned: !entry.pinned });
            drawList();
            App.rerender();
          }
        }, icon('pin')),
        el('button', {
          class: 'icon-btn' + (entry.draft ? ' is-on' : ''),
          type: 'button',
          title: entry.draft ? 'Publish this one (currently a draft)' : 'Make it a draft again',
          'aria-label': entry.draft ? 'Currently a draft' : 'Currently visible',
          onclick: function (e) {
            e.stopPropagation();
            Store.updateEntry(entry.id, { draft: !entry.draft });
            drawList();
            App.rerender();
          }
        }, icon(entry.draft ? 'eyeOff' : 'eye')),
        el('button', {
          class: 'icon-btn',
          type: 'button',
          title: t('action.delete'),
          'aria-label': t('action.delete'),
          onclick: function (e) {
            e.stopPropagation();
            UI.confirmAction({
              title: t('action.delete') + '?',
              message: '“' + (entry.title || Views.preview(entry, 1)).slice(0, 60) +
                       '” will be removed. This cannot be undone once you publish.',
              danger: true
            }).then(function (yes) {
              if (!yes) return;
              Store.removeEntry(entry.id);
              delete contentSelection[entry.id];
              drawList();
              App.rerender();
            });
          }
        }, icon('trash'))
      ]);

      var checkbox = selectMode ? el('button', {
        class: 'row-check' + (contentSelection[entry.id] ? ' is-on' : ''),
        type: 'button',
        'aria-label': 'Select',
        'aria-pressed': contentSelection[entry.id] ? 'true' : 'false',
        onclick: function (e) {
          e.stopPropagation();
          contentSelection[entry.id] = !contentSelection[entry.id];
          drawList();
        }
      }, contentSelection[entry.id] ? icon('check') : null) : null;

      return el('div', {
        class: 'row-item',
        role: 'button',
        tabindex: '0',
        onclick: function () {
          if (selectMode) {
            contentSelection[entry.id] = !contentSelection[entry.id];
            drawList();
            return;
          }
          UI.closeSheet();
          Editor.openEntryEditor(entry);
        },
        onkeydown: function (e) {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.currentTarget.click(); }
        }
      }, [
        checkbox,
        el('div', { class: 'row-main' }, [
          el('div', { class: 'row-meta' }, [
            el('span', { text: meta }),
            entry.draft ? el('span', { class: 'card-draft', text: 'Draft' }) : null,
            entry.pinned ? el('span', { class: 'row-flag', text: 'Pinned' }) : null
          ]),
          el('div', {
            class: 'row-title',
            lang: entry.lang,
            text: entry.title || Views.preview(entry, 1).replace(/\s+/g, ' ').slice(0, 70)
          }),
          entry.title ? el('div', {
            class: 'row-sub',
            lang: entry.lang,
            text: Views.preview(entry, 1).replace(/\s+/g, ' ').slice(0, 90)
          }) : null
        ]),
        selectMode ? null : actions
      ]);
    }

    function drawList() {
      UI.clear(listHost);
      var list = matching();
      var total = Store.entries().length;

      var bar = el('div', { class: 'row-toolbar' }, [
        el('span', {
          class: 'field-hint',
          style: { marginTop: '0', flex: '1' },
          text: list.length === total
            ? total + (total === 1 ? ' piece' : ' pieces')
            : list.length + ' of ' + total + ' shown'
        }),
        el('button', {
          class: 'btn btn-sm' + (selectMode ? ' btn-primary' : ''),
          type: 'button',
          text: selectMode ? 'Done' : 'Select',
          onclick: function () {
            selectMode = !selectMode;
            if (!selectMode) contentSelection = {};
            drawList();
          }
        })
      ]);
      listHost.appendChild(bar);

      if (selectMode) {
        var picked = selectedIds();
        listHost.appendChild(el('div', { class: 'row-toolbar' }, [
          el('button', {
            class: 'btn btn-sm', type: 'button',
            text: picked.length === list.length ? 'Select none' : 'Select all shown',
            onclick: function () {
              var all = picked.length === list.length;
              list.forEach(function (e) { contentSelection[e.id] = !all; });
              drawList();
            }
          }),
          el('button', {
            class: 'btn btn-sm btn-danger',
            type: 'button',
            disabled: !picked.length,
            text: picked.length ? 'Delete ' + picked.length : 'Delete',
            onclick: function () {
              UI.confirmAction({
                title: 'Delete ' + picked.length + ' ' + (picked.length === 1 ? 'piece' : 'pieces') + '?',
                message: 'This cannot be undone once you publish.',
                danger: true
              }).then(function (yes) {
                if (!yes) return;
                picked.forEach(function (id) { Store.removeEntry(id); });
                contentSelection = {};
                selectMode = false;
                UI.toast(picked.length + ' deleted', 'ok');
                drawList();
                App.rerender();
              });
            }
          })
        ]));
      }

      if (!list.length) {
        listHost.appendChild(el('div', {
          class: 'field-hint',
          style: { padding: '26px 0', textAlign: 'center' },
          text: total ? 'Nothing matches that.' : 'Nothing written yet. Use the + button to start.'
        }));
        return;
      }

      listHost.appendChild(el('div', { class: 'row-list' }, list.map(row)));
    }

    /* --- search and filters --- */
    var search = el('input', {
      class: 'input',
      type: 'search',
      value: contentFilter.q,
      placeholder: t('action.searchPlaceholder'),
      oninput: function () { contentFilter.q = search.value; drawList(); }
    });
    host.appendChild(UI.field(null, search));

    var chips = el('div', { class: 'chip-row', style: { flexWrap: 'wrap', marginBottom: '14px' } });
    [
      { id: '', label: t('tab.all') },
      { id: 'poem', label: t('tab.poems') },
      { id: 'blog', label: t('tab.blogs') },
      { id: 'quote', label: t('tab.quotes') }
    ].forEach(function (opt) {
      chips.appendChild(el('button', {
        class: 'chip' + (contentFilter.type === opt.id && !contentFilter.drafts ? ' is-active' : ''),
        type: 'button',
        text: opt.label,
        onclick: function () {
          contentFilter.type = opt.id;
          contentFilter.drafts = false;
          drawChips();
          drawList();
        }
      }));
    });
    chips.appendChild(el('button', {
      class: 'chip' + (contentFilter.drafts ? ' is-active' : ''),
      type: 'button',
      text: 'Drafts',
      onclick: function () {
        contentFilter.drafts = !contentFilter.drafts;
        if (contentFilter.drafts) contentFilter.type = '';
        drawChips();
        drawList();
      }
    }));

    function drawChips() {
      UI.$$('.chip', chips).forEach(function (n, i) {
        var isDraftChip = i === 4;
        var ids = ['', 'poem', 'blog', 'quote'];
        n.classList.toggle('is-active', isDraftChip
          ? contentFilter.drafts
          : (contentFilter.type === ids[i] && !contentFilter.drafts));
      });
    }

    host.appendChild(chips);
    host.appendChild(el('button', {
      class: 'btn btn-sm btn-primary',
      type: 'button',
      style: { marginBottom: '16px' },
      onclick: function () { UI.closeSheet(); Editor.openEntryEditor(null); }
    }, [icon('plus'), el('span', { text: 'Write something new' })]));

    host.appendChild(listHost);
    drawList();
    return host;
  }

  /* ================================================================== Site  */

  function siteTab() {
    var s = data().site;
    var host = el('div', {});

    host.appendChild(el('div', { class: 'section-title', text: 'Name' }));
    host.appendChild(UI.field('Site name', bound(s, 'name', { placeholder: 'My Writings' }),
      'Shown in the header, on the home page, and on shared images.'));
    host.appendChild(el('div', { class: 'field-row' }, [
      UI.field('Name in Gujarati', bound(s, 'nameGu', { lang: 'gu', placeholder: 'વૈકલ્પિક' })),
      UI.field('Name in Hindi', bound(s, 'nameHi', { lang: 'hi', placeholder: 'वैकल्पिक' }))
    ]));
    host.appendChild(el('div', { class: 'field-hint', style: { marginTop: '-8px', marginBottom: '16px' },
      text: 'If you fill these in, readers who pick that language see the name in their own script.' }));

    host.appendChild(UI.field('Tagline', bound(s, 'tagline', { placeholder: 'Poems, blogs and quotes' })));
    host.appendChild(el('div', { class: 'field-row' }, [
      UI.field('Tagline in Gujarati', bound(s, 'taglineGu', { lang: 'gu' })),
      UI.field('Tagline in Hindi', bound(s, 'taglineHi', { lang: 'hi' }))
    ]));

    host.appendChild(el('div', { class: 'section-title', text: 'You' }));
    host.appendChild(UI.field('Author name', bound(s, 'author', { placeholder: 'Your name' }),
      'Signed onto every image you share.'));

    var avatarPreview = el('div', { style: { marginBottom: '10px' } });
    function drawAvatar() {
      UI.clear(avatarPreview);
      var url = UI.safeUrl(s.avatar);
      if (url) {
        avatarPreview.appendChild(el('img', {
          src: url, alt: '',
          style: { width: '68px', height: '68px', borderRadius: '50%', objectFit: 'cover' }
        }));
      }
    }
    drawAvatar();
    host.appendChild(UI.field('Photo', el('div', {}, [
      avatarPreview,
      el('div', { style: { display: 'flex', gap: '8px' } }, [
        el('button', { class: 'btn btn-sm', type: 'button', onclick: function () {
          UI.pickImage(function (dataUrl) { s.avatar = dataUrl; touched(); drawAvatar(); }, { maxSide: 512 });
        } }, [icon('upload'), el('span', { text: 'Choose' })]),
        s.avatar ? el('button', { class: 'btn btn-sm btn-ghost', type: 'button', text: 'Remove',
          onclick: function () { s.avatar = ''; touched(); drawAvatar(); } }) : null
      ])
    ]), 'Any photo works — it is shrunk automatically.'));

    host.appendChild(UI.field('About you', bound(s, 'about', { multiline: true,
      placeholder: 'A few lines about yourself and your writing.' })));
    host.appendChild(UI.field('Contact email', bound(s, 'email', { type: 'email',
      placeholder: 'you@example.com' }), 'Shown on the About page and used for feedback if no form service is set.'));
    host.appendChild(UI.field('Footer line', bound(s, 'footer',
      { placeholder: '© ' + new Date().getFullYear() + ' Your Name' })));

    /* --- links --- */
    host.appendChild(el('div', { class: 'section-title', text: 'Links' }));
    var linksHost = el('div', {});
    function drawLinks() {
      UI.clear(linksHost);
      (s.links || []).forEach(function (link, i) {
        linksHost.appendChild(el('div', {
          style: { display: 'flex', gap: '8px', marginBottom: '8px', alignItems: 'center' }
        }, [
          el('input', {
            class: 'input', value: link.label, placeholder: 'Instagram',
            style: { flex: '0 0 34%' },
            oninput: function (e) { link.label = e.target.value; touched(); }
          }),
          el('input', {
            class: 'input', value: link.url, placeholder: 'https://…', type: 'url',
            style: { flex: '1' },
            oninput: function (e) { link.url = e.target.value; touched(); }
          }),
          el('button', {
            class: 'icon-btn', type: 'button', 'aria-label': t('action.delete'),
            onclick: function () { s.links.splice(i, 1); touched(); drawLinks(); }
          }, icon('trash'))
        ]));
      });
      linksHost.appendChild(el('button', {
        class: 'btn btn-sm', type: 'button',
        onclick: function () { s.links.push({ label: '', url: '' }); touched(); drawLinks(); }
      }, [icon('plus'), el('span', { text: 'Add link' })]));
    }
    drawLinks();
    host.appendChild(linksHost);

    return host;
  }

  /* ============================================================== Homepage  */

  function homeTab() {
    var h = data().home;
    var host = el('div', {});

    host.appendChild(el('div', { class: 'section-title', text: 'Layout' }));
    host.appendChild(UI.optGrid(Theme.LAYOUTS, h.layout, function (id) {
      h.layout = id; touched(); App.rerender(); redraw();
    }));

    host.appendChild(el('div', { class: 'field', style: { marginTop: '16px' } }, [
      el('label', { class: 'field-label', text: 'Pieces before “See more”' }),
      UI.optGrid([6, 9, 12, 15, 18].map(function (n) {
        return { id: String(n), label: String(n) };
      }), String(h.perPage), function (id) {
        h.perPage = Number(id);
        Views.view.shown = 0;
        touched(); App.rerender(); redraw();
      })
    ]));

    host.appendChild(UI.field('Order', numberSelect(h, 'sortBy', [
      { value: 'date-desc', label: 'Newest first' },
      { value: 'date-asc', label: 'Oldest first' },
      { value: 'title', label: 'By title' },
      { value: 'manual', label: 'The order I added them' }
    ], function () { App.rerender(); })));

    host.appendChild(el('div', { class: 'section-title', text: 'Header' }));
    host.appendChild(UI.switchRow('Show the big header', 'The large title block at the top of the home page.',
      h.hero.enabled, function (v) { h.hero.enabled = v; touched(); App.rerender(); }));
    host.appendChild(UI.switchRow('Show your photo there', '', h.hero.showAvatar,
      function (v) { h.hero.showAvatar = v; touched(); App.rerender(); }));

    host.appendChild(el('div', { class: 'field', style: { marginTop: '14px' } }, [
      el('label', { class: 'field-label', text: 'Header alignment' }),
      UI.optGrid([{ id: 'center', label: 'Centred' }, { id: 'left', label: 'Left' }],
        h.hero.align, function (id) { h.hero.align = id; touched(); App.rerender(); redraw(); })
    ]));

    host.appendChild(UI.field('Header title', bound(h.hero, 'title', {
      placeholder: data().site.name, onInput: function () { App.rerender(); }
    }), 'Leave empty to use the site name.'));
    host.appendChild(UI.field('Header subtitle', bound(h.hero, 'subtitle', {
      multiline: true, placeholder: data().site.tagline,
      onInput: function () { App.rerender(); }
    })));

    /* --- featured piece --- */
    var pieces = Store.select({});
    host.appendChild(UI.field('Featured piece', (function () {
      var sel = el('select', { class: 'select', onchange: function () {
        h.hero.featuredId = sel.value; touched(); App.rerender();
      } });
      sel.appendChild(el('option', { value: '', selected: !h.hero.featuredId, text: '— none —' }));
      pieces.forEach(function (entry) {
        sel.appendChild(el('option', {
          value: entry.id,
          selected: h.hero.featuredId === entry.id,
          text: (entry.title || Views.preview(entry, 1)).slice(0, 60)
        }));
      });
      return sel;
    })(), 'Shown in a large panel just under the header.'));

    host.appendChild(el('div', { class: 'section-title', text: 'Tabs' }));
    host.appendChild(el('div', { class: 'field-hint', style: { marginBottom: '12px' },
      text: 'Turn off any tab you do not use. “All” is always shown first.' }));

    ['poems', 'blogs', 'quotes', 'collections'].forEach(function (id) {
      var on = h.tabs.indexOf(id) !== -1;
      host.appendChild(UI.switchRow(t('tab.' + id), '', on, function (v) {
        var idx = h.tabs.indexOf(id);
        if (v && idx === -1) {
          // Keep the canonical order rather than appending to the end.
          var order = ['all', 'poems', 'blogs', 'quotes', 'collections'];
          h.tabs = order.filter(function (x) { return x === id || h.tabs.indexOf(x) !== -1; });
        } else if (!v && idx !== -1) {
          h.tabs.splice(idx, 1);
        }
        touched();
        App.rerender();
      }));
    });

    host.appendChild(el('div', { class: 'section-title', text: 'Controls' }));
    host.appendChild(UI.switchRow('Show counts on tabs', '', h.showCounts,
      function (v) { h.showCounts = v; touched(); App.rerender(); }));
    host.appendChild(UI.switchRow('Show the search box', '', h.showSearch,
      function (v) { h.showSearch = v; touched(); App.rerender(); }));
    host.appendChild(UI.switchRow('Show language filter', '', h.showLangFilter,
      function (v) { h.showLangFilter = v; touched(); App.rerender(); }));

    return host;
  }

  /* ================================================================= Theme  */

  function themeTab() {
    var th = data().theme;
    var host = el('div', {});

    host.appendChild(el('div', { class: 'section-title', text: 'Palette' }));
    host.appendChild(UI.optGrid(Theme.PRESETS.map(function (p) {
      return {
        id: p.id,
        label: p.label,
        swatchHtml: '<div class="opt-swatch" style="background:linear-gradient(135deg,' +
          p.swatch[0] + ' 0 50%,' + p.swatch[1] + ' 50% 100%)"></div>'
      };
    }), th.preset, function (id) {
      th.preset = id;
      // The accent belongs to the preset unless it has been overridden since.
      th.accent = Theme.PRESET_BY_ID[id][Theme.resolveMode(th)].accent;
      touched(); App.rerender(); redraw();
    }));

    host.appendChild(el('div', { class: 'field', style: { marginTop: '16px' } }, [
      el('label', { class: 'field-label', text: 'Light or dark' }),
      UI.optGrid([
        { id: 'auto', label: 'Match device' },
        { id: 'light', label: 'Light' },
        { id: 'dark', label: 'Dark' }
      ], th.mode, function (id) {
        th.mode = id;
        // A site-wide choice should override whatever this device picked.
        Store.setPref('mode', '');
        touched(); App.rerender(); redraw();
      })
    ]));

    host.appendChild(UI.field('Accent colour', UI.colorRow(th.accent, function (v) {
      th.accent = v; touched(); App.rerender();
    }), 'Used for highlights, buttons and the active tab.'));

    host.appendChild(el('div', { class: 'section-title', text: 'Page background' }));
    host.appendChild(Editor.backgroundEditor(th, {
      inheritId: 'preset', inheritLabel: 'From palette'
    }, function () { touched(); }));

    host.appendChild(el('div', { class: 'section-title', text: 'Fonts' }));
    [
      { key: 'fontGu', script: 'gu', label: 'Gujarati' },
      { key: 'fontHi', script: 'hi', label: 'Hindi' },
      { key: 'fontEn', script: 'en', label: 'English' }
    ].forEach(function (row) {
      host.appendChild(el('div', { class: 'field' }, [
        el('label', { class: 'field-label', text: row.label }),
        Editor.fontPicker(row.script, th[row.key], function (id) {
          th[row.key] = id; touched(); App.rerender();
        })
      ]));
    });

    host.appendChild(UI.field('Reading size', UI.rangeRow(14, 22, 1, Number(th.baseSize) || 17,
      function (v) { return v + 'px'; },
      function (v) { th.baseSize = v; touched(); })));

    host.appendChild(el('div', { class: 'section-title', text: 'Cards' }));
    host.appendChild(UI.optGrid(Theme.CARD_STYLES, th.cardStyle, function (id) {
      th.cardStyle = id; touched(); redraw();
    }));

    host.appendChild(UI.field('Corner rounding', UI.rangeRow(0, 34, 2, Number(th.radius) || 18,
      function (v) { return v + 'px'; },
      function (v) { th.radius = v; touched(); })));

    host.appendChild(el('div', { class: 'field', style: { marginTop: '14px' } }, [
      el('label', { class: 'field-label', text: 'Spacing' }),
      UI.optGrid([
        { id: 'comfortable', label: 'Comfortable' },
        { id: 'compact', label: 'Compact' }
      ], th.density, function (id) { th.density = id; touched(); redraw(); })
    ]));

    return host;
  }

  /* =========================================================== Collections  */

  function collectionsTab() {
    var host = el('div', {});
    var list = el('div', {});

    function drawList() {
      UI.clear(list);
      var cols = data().collections || [];
      if (!cols.length) {
        list.appendChild(el('div', { class: 'field-hint', style: { padding: '12px 0' },
          text: 'No collections yet. A collection groups pieces that belong together — a series, a book, a theme.' }));
      }
      cols.forEach(function (col, i) {
        var count = Store.select({ collection: col.id }).length;
        list.appendChild(el('div', {
          style: {
            padding: '14px', marginBottom: '10px',
            border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
            background: 'var(--surface)'
          }
        }, [
          el('div', { style: { display: 'flex', gap: '8px', marginBottom: '10px' } }, [
            el('input', {
              class: 'input', value: col.name, placeholder: 'Collection name',
              style: { flex: '1' },
              oninput: function (e) { col.name = e.target.value; touched(); }
            }),
            el('button', {
              class: 'icon-btn', type: 'button', 'aria-label': t('action.delete'),
              onclick: function () {
                UI.confirmAction({
                  title: 'Delete this collection?',
                  message: 'The ' + count + ' piece' + (count === 1 ? '' : 's') +
                    ' inside will stay — only the grouping goes.',
                  danger: true
                }).then(function (yes) {
                  if (!yes) return;
                  Store.removeCollection(col.id);
                  drawList();
                  App.rerender();
                });
              }
            }, icon('trash'))
          ]),
          el('input', {
            class: 'input', value: col.desc, placeholder: 'A short description',
            style: { marginBottom: '10px' },
            oninput: function (e) { col.desc = e.target.value; touched(); }
          }),
          el('div', { style: { display: 'flex', gap: '10px', alignItems: 'center' } }, [
            el('div', { style: { flex: '1' } },
              UI.colorRow(col.accent || '#888888', function (v) { col.accent = v; touched(); })),
            el('span', { class: 'field-hint', style: { marginTop: '0', whiteSpace: 'nowrap' },
              text: count + ' ' + (count === 1 ? t('meta.piece') : t('meta.pieces')) })
          ])
        ]));
      });
    }

    drawList();
    host.appendChild(list);
    host.appendChild(el('button', {
      class: 'btn btn-sm', type: 'button',
      onclick: function () {
        Store.addCollection({ name: 'New collection', order: (data().collections || []).length });
        drawList();
        App.rerender();
      }
    }, [icon('plus'), el('span', { text: 'Add collection' })]));

    return host;
  }

  /* ============================================================== Feedback  */

  function feedbackTab() {
    var f = data().feedback;
    var host = el('div', {});

    host.appendChild(UI.switchRow('Let readers send feedback', 'Adds a feedback page and a link in the header.',
      f.enabled, function (v) { f.enabled = v; touched(); App.rerender(); }));

    host.appendChild(el('div', { class: 'section-title', text: 'Where it goes' }));
    host.appendChild(el('div', { class: 'feedback-note', text:
      'A site with no server of its own cannot receive messages by itself. Paste a free form endpoint below ' +
      'and messages arrive in your inbox automatically. Leave it empty and the Send button opens the reader’s ' +
      'own email app with the message already written — that always works and costs nothing.' }));

    host.appendChild(UI.field('Form endpoint', bound(f, 'endpoint', {
      type: 'url', placeholder: 'https://api.web3forms.com/submit'
    }), 'Works with Web3Forms, Formspree, Getform, or a Google Apps Script web app. Any service that accepts a POST.'));

    host.appendChild(UI.field('Access key', bound(f, 'endpointKey', {
      placeholder: 'Only needed for Web3Forms'
    }), 'Web3Forms gives you an access key — paste it here. Formspree and the others need nothing.'));

    host.appendChild(UI.field('Fallback email', bound(f, 'email', {
      type: 'email', placeholder: data().site.email || 'you@example.com'
    }), 'Used when no endpoint is set. Falls back to your contact email if left blank.'));

    host.appendChild(el('div', { class: 'section-title', text: 'The form' }));
    host.appendChild(UI.field('Intro text', bound(f, 'intro', {
      multiline: true, placeholder: t('feedback.intro')
    })));
    host.appendChild(UI.switchRow('Ask for a name', '', f.askName, function (v) { f.askName = v; touched(); }));
    host.appendChild(UI.switchRow('Ask for an email', 'So you can reply.', f.askEmail,
      function (v) { f.askEmail = v; touched(); }));
    host.appendChild(UI.switchRow('Let them pick a piece', 'Adds a dropdown so feedback can point at one writing.',
      f.askAbout, function (v) { f.askAbout = v; touched(); }));

    host.appendChild(el('button', {
      class: 'btn btn-sm', type: 'button', style: { marginTop: '18px' },
      onclick: function () { UI.closeSheet(); Router.go('/feedback'); }
    }, [icon('eye'), el('span', { text: 'Preview the form' })]));

    return host;
  }

  /* =============================================================== Publish  */

  function publishTab() {
    var p = data().publish;
    var session = Store.adminSession();
    var host = el('div', {});

    host.appendChild(el('div', { class: 'feedback-note', text:
      'Publishing writes your content into this site’s GitHub repository. GitHub Pages then rebuilds the ' +
      'site automatically, usually within a minute. This is what keeps the whole thing free forever: no server, ' +
      'no database, nothing to renew.' }));

    host.appendChild(el('div', { class: 'section-title', text: 'Repository' }));
    host.appendChild(el('div', { class: 'field-row' }, [
      UI.field('Owner', bound(p, 'owner', { placeholder: 'your-github-username' })),
      UI.field('Repository', bound(p, 'repo', { placeholder: 'instatements' }))
    ]));
    host.appendChild(el('div', { class: 'field-row' }, [
      UI.field('Branch', bound(p, 'branch', { placeholder: 'main' })),
      UI.field('File path', bound(p, 'path', { placeholder: 'writings/data/content.json' }))
    ]));

    var detect = Publish.guessTarget();
    if (detect.owner && (!p.owner || !p.repo)) {
      host.appendChild(el('button', {
        class: 'btn btn-sm', type: 'button', style: { marginBottom: '16px' },
        text: 'Use ' + detect.owner + '/' + detect.repo,
        onclick: function () {
          p.owner = detect.owner;
          p.repo = detect.repo;
          if (detect.path) p.path = detect.path;
          touched();
          redraw();
        }
      }));
    }

    host.appendChild(el('div', { class: 'section-title', text: 'Access token' }));
    host.appendChild(el('div', { class: 'feedback-note', text:
      'Either kind of GitHub token works. A fine-grained one is tidier — limit it to this one repository and to ' +
      '“Contents: Read and write”. A classic token with the “repo” box ticked is quicker to make. Whichever you ' +
      'choose, it is stored in this browser alone and never written into any file this site serves, so no ' +
      'visitor can see it.' }));

    var tokenInput = el('input', {
      class: 'input', type: 'password',
      value: session.token || '',
      placeholder: 'github_pat_…',
      oninput: function () { Store.saveAdminSession({ token: tokenInput.value.trim() }); }
    });

    host.appendChild(UI.field('Token', el('div', { style: { display: 'flex', gap: '8px' } }, [
      tokenInput,
      el('button', {
        class: 'icon-btn', type: 'button', 'aria-label': 'Show or hide',
        onclick: function () {
          tokenInput.type = tokenInput.type === 'password' ? 'text' : 'password';
        }
      }, icon('eye'))
    ]), 'Paste it once. It stays on this device — locking the site does not remove it, and you will not ' +
        'need to paste it again. Tap the eye to read it back: GitHub never shows a token a second time, ' +
        'so this field is the only place you can still see it.'));

    if (session.token) {
      host.appendChild(el('button', {
        class: 'btn btn-sm btn-danger', type: 'button',
        style: { marginBottom: '16px' },
        onclick: function () {
          UI.confirmAction({
            title: 'Remove the token from this device?',
            message: 'Publishing will stop working here until you paste a token again. GitHub cannot show ' +
                     'you this one a second time, so you would need to generate a new one. Your writing is ' +
                     'not affected.',
            confirmText: 'Remove it',
            danger: true
          }).then(function (yes) {
            if (!yes) return;
            Store.forgetToken();
            UI.toast('Token removed from this device', 'ok');
            redraw();
          });
        }
      }, [icon('trash'), el('span', { text: 'Remove token from this device' })]));
    }

    host.appendChild(el('div', {
      style: { display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '18px' }
    }, [
      el('a', {
        class: 'btn btn-sm',
        href: 'https://github.com/settings/personal-access-tokens/new',
        target: '_blank', rel: 'noopener noreferrer'
      }, [icon('github'), el('span', { text: 'Fine-grained token' })]),
      el('a', {
        class: 'btn btn-sm',
        href: 'https://github.com/settings/tokens/new?scopes=repo&description=writings%20site',
        target: '_blank', rel: 'noopener noreferrer'
      }, [icon('github'), el('span', { text: 'Classic token' })])
    ]));

    var status = el('div', { class: 'field-hint', style: { marginBottom: '14px' } });
    host.appendChild(status);

    host.appendChild(el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap' } }, [
      el('button', {
        class: 'btn btn-sm', type: 'button',
        onclick: function () {
          status.textContent = 'Checking…';
          Publish.test().then(function (msg) {
            status.style.color = 'var(--accent)';
            status.textContent = msg;
          }).catch(function (err) {
            status.style.color = '#E5484D';
            status.textContent = err.message;
          });
        }
      }, [icon('refresh'), el('span', { text: 'Test connection' })]),
      el('button', {
        class: 'btn btn-sm btn-primary', type: 'button',
        onclick: function () { Publish.publish(); }
      }, [icon('upload'), el('span', { text: t('admin.publish') })])
    ]));

    if (Store.state.dirty) {
      host.appendChild(el('div', {
        class: 'field-hint',
        style: { marginTop: '14px', color: '#E9A23B' },
        text: 'You have changes that are not published yet.'
      }));
    }

    /* Tokens are the pleasant way to publish, not the only way. If GitHub's
       token screens are being difficult, this downloads exactly the file the
       Publish button would have committed, to upload by hand. Same result,
       a few more clicks, nothing to set up. */
    host.appendChild(el('div', { class: 'section-title', text: 'Publishing without a token' }));
    host.appendChild(el('div', { class: 'feedback-note', text:
      'Download the file below, then on GitHub open ' + p.path.replace(/\/[^/]*$/, '') + ' → Add file → ' +
      'Upload files, drop it in and press Commit changes. Your site updates the same way it would have. ' +
      'Keep the name content.json exactly.' }));

    host.appendChild(el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap' } }, [
      el('button', {
        class: 'btn btn-sm', type: 'button',
        onclick: function () { Publish.downloadContentFile(); }
      }, [icon('download'), el('span', { text: 'Download content.json' })]),
      el('a', {
        class: 'btn btn-sm',
        href: p.owner && p.repo
          ? 'https://github.com/' + encodeURIComponent(p.owner) + '/' + encodeURIComponent(p.repo) +
            '/upload/' + encodeURIComponent(p.branch || 'main') + '/' + p.path.replace(/\/[^/]*$/, '')
          : 'https://github.com',
        target: '_blank', rel: 'noopener noreferrer'
      }, [icon('github'), el('span', { text: 'Open the upload page' })])
    ]));

    return host;
  }

  /* ================================================================== Data  */

  function dataTab() {
    var host = el('div', {});

    host.appendChild(el('div', { class: 'section-title', text: 'Back up' }));
    host.appendChild(el('div', { class: 'feedback-note', text:
      'A backup file holds every piece, every collection and all your settings. Keep one somewhere safe — it is ' +
      'the whole site in a single file, and it will still open in ten years.' }));

    host.appendChild(el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '6px' } }, [
      el('button', { class: 'btn btn-sm', type: 'button', onclick: Share.exportAll },
        [icon('download'), el('span', { text: 'Backup (.json)' })]),
      el('button', { class: 'btn btn-sm', type: 'button', onclick: Share.exportAllText },
        [icon('fileText'), el('span', { text: 'All writing (.txt)' })])
    ]));

    host.appendChild(el('div', { class: 'section-title', text: 'Bring writing in' }));
    host.appendChild(el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap' } }, [
      el('button', { class: 'btn btn-sm', type: 'button', onclick: Share.importFile },
        [icon('upload'), el('span', { text: 'Restore a backup' })]),
      el('button', {
        class: 'btn btn-sm', type: 'button',
        onclick: function () { Share.importTextFiles(function () { App.rerender(); redraw(); }); }
      }, [icon('fileText'), el('span', { text: 'Import .txt files' })])
    ]));
    host.appendChild(el('div', { class: 'field-hint', style: { marginTop: '10px' },
      text: 'Text files come in as drafts, one piece per file, with the language detected from the script. ' +
            'Nothing goes live until you review it and publish.' }));

    host.appendChild(el('div', { class: 'section-title', text: 'This device' }));

    if (Store.state.dirty) {
      host.appendChild(el('button', {
        class: 'btn btn-sm btn-danger', type: 'button', style: { marginBottom: '12px' },
        onclick: function () {
          UI.confirmAction({
            title: t('admin.discard') + '?',
            message: 'Everything you changed since the last publish will be thrown away.',
            danger: true
          }).then(function (yes) {
            if (!yes) return;
            if (!Store.discardDraft()) {
              UI.toast('There is no loaded copy of the live site to go back to. Reload first.', 'error');
              return;
            }
            UI.closeSheet();
            App.rerender();
          });
        }
      }, [icon('refresh'), el('span', { text: t('admin.discard') })]));
    }

    host.appendChild(el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap' } }, [
      el('button', {
        class: 'btn btn-sm', type: 'button',
        onclick: function () { UI.closeSheet(); Editor.openSetPassphrase(); }
      }, [icon('lock'), el('span', { text: 'Change passphrase' })]),
      el('button', {
        class: 'btn btn-sm btn-ghost', type: 'button',
        title: 'Hides the editing tools. Your token and your writing stay.',
        onclick: function () {
          Store.lock();
          UI.closeSheet();
          UI.toast(t('admin.lock'), 'ok');
          App.rerender();
        }
      }, [icon('lock'), el('span', { text: t('admin.lock') + ' this device' })])
    ]));

    var counts = Store.counts();
    host.appendChild(el('div', {
      class: 'field-hint',
      style: { marginTop: '22px', paddingTop: '16px', borderTop: '1px solid var(--border)' },
      text: counts.all + ' pieces · ' + counts.poems + ' poems · ' + counts.blogs + ' blogs · ' +
            counts.quotes + ' quotes · ' + counts.collections + ' collections'
    }));

    return host;
  }

  /* ================================================================= shell  */

  var currentTabs = null;
  function redraw() { if (currentTabs) currentTabs.redraw(); }

  function openSettings(initial) {
    if (!Store.canEdit()) {
      UI.toast('Your writing has not loaded yet. Reload the page first.', 'error');
      return;
    }
    var tabs = Editor.tabbed([
      { id: 'content',     label: 'Content',     render: contentTab },
      { id: 'site',        label: 'Site',        render: siteTab },
      { id: 'home',        label: 'Home page',   render: homeTab },
      { id: 'theme',       label: 'Theme',       render: themeTab },
      { id: 'collections', label: 'Collections', render: collectionsTab },
      { id: 'feedback',    label: t('nav.feedback'), render: feedbackTab },
      { id: 'publish',     label: 'Publish',     render: publishTab },
      { id: 'data',        label: 'Data',        render: dataTab }
    ], initial);

    currentTabs = tabs;

    UI.openSheet({
      title: t('admin.settings'),
      variant: 'side',
      body: tabs.node,
      foot: [
        el('button', {
          class: 'btn', text: t('action.close'),
          onclick: function () { UI.closeSheet(); }
        }),
        el('button', {
          class: 'btn btn-primary',
          onclick: function () { Publish.publish(); }
        }, [icon('upload'), el('span', { text: t('admin.publish') })])
      ],
      onClose: function () { currentTabs = null; App.rerender(); }
    });
  }

  Editor.openSettings = openSettings;
})(window);
