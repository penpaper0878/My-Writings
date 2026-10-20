/* =============================================================================
   views.js — everything the reader sees: the header, hero, tabs, the grid of
   cards, and the reading view.
   ========================================================================== */
(function (global) {
  'use strict';

  var el = UI.el, esc = UI.esc, icon = UI.icon, t = function () { return I18n.t.apply(I18n, arguments); };

  /* View state. Deliberately not in the URL beyond the tab — a reader who
     shares a link should share the writing, not their scroll position. */
  var view = {
    tab: 'all',
    lang: 'all',
    query: '',
    tag: '',
    shown: 0,
    collection: ''
  };

  /* --------------------------------------------------------------- helpers */

  function data() { return Store.state.data; }

  function siteName() {
    var s = data().site;
    var lang = I18n.getLang();
    return (lang === 'gu' && s.nameGu) || (lang === 'hi' && s.nameHi) || s.name || 'My Writings';
  }

  function siteTagline() {
    var s = data().site;
    var lang = I18n.getLang();
    return (lang === 'gu' && s.taglineGu) || (lang === 'hi' && s.taglineHi) || s.tagline || '';
  }

  function typeLabel(type) { return t('type.' + type); }

  /* Words are whitespace-separated in all three scripts, so one counter serves. */
  function stats(entry) {
    var words = String(entry.body || '').trim().split(/\s+/).filter(Boolean).length;
    return { words: words, minutes: Math.max(1, Math.round(words / 180)) };
  }

  function preview(entry, lines) {
    var body = String(entry.body || '').trim();
    if (entry.excerpt) return entry.excerpt;
    if (entry.type === 'blog') {
      var flat = body.replace(/\s+/g, ' ');
      return flat.length > 240 ? flat.slice(0, 240).trim() + '…' : flat;
    }
    // Poems and quotes keep their line breaks — the shape is the point.
    var rows = body.split('\n');
    var take = rows.slice(0, lines || 6).join('\n');
    return rows.length > (lines || 6) ? take + '\n…' : take;
  }

  /* Resolve an entry's own styling into inline CSS. Returns the class the
     container needs so text stays readable over whatever background is set. */
  function styleFor(entry, opts) {
    opts = opts || {};
    var st = entry.style || {};
    var css = {};
    var cls = '';

    var family = st.font
      ? Theme.fontStack(st.font, entry.lang)
      : 'var(--ff-' + entry.lang + ')';
    if (st.font) Theme.ensureFont(st.font);
    css.fontFamily = family;

    if (st.align && st.align !== 'left') css.textAlign = st.align;
    if (Number(st.lineHeight)) css.lineHeight = String(st.lineHeight);
    if (Number(st.letterSpacing)) css.letterSpacing = Number(st.letterSpacing) + 'em';
    if (opts.scale !== false && Number(st.size) && Number(st.size) !== 1) {
      css.fontSize = 'calc(var(--base-size) * ' + Number(st.size) + ')';
    }
    if (st.textShadow) css.textShadow = '0 2px 14px rgba(0,0,0,.45)';

    var bg = Theme.backgroundCSS(st);
    if (bg) {
      cls = bg.dark ? 'on-dark' : 'on-light';
      if (st.textColor) css.color = st.textColor;
    } else if (st.textColor) {
      css.color = st.textColor;
    }

    return { css: css, cls: cls, bg: bg };
  }

  function applyStyle(node, css) {
    Object.keys(css).forEach(function (k) { node.style[k] = css[k]; });
  }

  /* Tags are the natural way through a few hundred poems — everything tagged
     ગઝલ, everything about વરસાદ. Clicking the tag already showing turns the
     filter back off, so a tag pill is a toggle wherever it appears. */
  function filterByTag(tag) {
    view.tag = view.tag === tag ? '' : tag;
    view.shown = 0;
    renderMain();
    var host = UI.$('#grid-host');
    if (host) {
      var top = host.getBoundingClientRect().top + global.scrollY - 150;
      if (global.scrollY > top) global.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
    }
  }

  /* A tag pill. Inside a card it must not also trigger the card's own click,
     hence the stopPropagation. */
  function tagPill(tag, opts) {
    opts = opts || {};
    return el('button', {
      class: 'tag-pill' + (view.tag === tag ? ' is-active' : ''),
      type: 'button',
      title: view.tag === tag ? t('action.clear') : tag,
      text: tag,
      onclick: function (e) {
        e.stopPropagation();
        if (opts.closeSheet) UI.closeSheet();
        filterByTag(tag);
      }
    });
  }

  /* ------------------------------------------------------------------ card */

  function entryCard(entry) {
    var s = styleFor(entry, { scale: false });
    /* A div rather than a button: the tag pills inside are buttons themselves,
       and nesting buttons is invalid HTML that browsers handle inconsistently.
       role and tabindex keep it reachable by keyboard and screen reader. */
    function open() { Router.go('/e/' + entry.id); }
    var card = el('div', {
      class: 'card' + (s.bg ? ' has-bg ' + s.cls : ''),
      role: 'button',
      tabindex: '0',
      'data-type': entry.type,
      lang: entry.lang,
      'aria-label': entry.title || preview(entry, 1),
      onclick: open,
      onkeydown: function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
      }
    });

    if (s.bg) {
      var bgNode = el('div', { class: 'card-bg' });
      bgNode.style.backgroundImage = s.bg.image;
      bgNode.style.backgroundColor = s.bg.color;
      if (s.bg.blur) bgNode.style.filter = 'blur(' + s.bg.blur + 'px)';
      card.appendChild(bgNode);
    }

    var head = el('div', { class: 'card-head' }, [
      el('span', { class: 'card-kind', text: typeLabel(entry.type) }),
      el('span', { class: 'card-dot' }),
      el('span', { class: 'card-date', text: I18n.formatDate(entry.date) }),
      entry.pinned ? el('span', { class: 'card-pin' }, icon('pin')) : null,
      entry.draft ? el('span', { class: 'card-draft', text: 'Draft' }) : null
    ]);

    var body = el('div', { class: 'card-body', text: preview(entry) });
    var title = entry.title
      ? el('h3', { class: 'card-title', text: entry.title })
      : null;
    applyStyle(body, s.css);
    if (title) title.style.fontFamily = s.css.fontFamily;

    var main = el('div', { class: 'card-main' }, [
      entry.type === 'quote' && !title ? el('div', { class: 'card-quote-mark', text: '“' }) : null,
      title,
      body
    ]);

    var tags = el('div', { class: 'card-tags' },
      (entry.tags || []).slice(0, 3).map(function (tag) { return tagPill(tag); }));

    var foot = el('div', { class: 'card-foot' }, [
      tags,
      el('span', { class: 'card-lang', text: entry.lang.toUpperCase() })
    ]);

    card.appendChild(head);
    card.appendChild(main);
    card.appendChild(foot);
    return card;
  }

  function collectionCard(col) {
    var count = Store.select({ collection: col.id }).length;
    var card = el('button', {
      class: 'col-card',
      type: 'button',
      onclick: function () { Router.go('/c/' + col.id); }
    }, [
      el('div', { class: 'col-card-count',
        text: count + ' ' + (count === 1 ? t('meta.piece') : t('meta.pieces')) }),
      el('div', { class: 'col-card-name', text: col.name || 'Untitled' }),
      col.desc ? el('div', { class: 'col-card-desc', text: col.desc }) : null
    ]);
    if (col.accent) {
      card.style.background =
        'linear-gradient(160deg, ' + Theme.rgba(col.accent, 0.3) + ', var(--surface))';
      card.style.borderColor = Theme.rgba(col.accent, 0.4);
    }
    var cover = Theme.safeImageUrl(col.cover);
    if (cover) {
      card.style.backgroundImage =
        'linear-gradient(to top, rgba(0,0,0,.82), rgba(0,0,0,.25)), url("' + cover + '")';
      card.style.backgroundSize = 'cover';
      card.style.backgroundPosition = 'center';
      card.style.color = '#FFFFFF';
    }
    return card;
  }

  /* ---------------------------------------------------------------- topbar */

  function renderTopbar() {
    var host = UI.$('#topbar');
    UI.clear(host);
    var s = data().site;

    var avatar = UI.safeUrl(s.avatar);
    var mark = el('a', { class: 'brand-mark', href: '#/', 'aria-label': siteName() });
    if (avatar) mark.appendChild(el('img', { src: avatar, alt: '' }));
    else mark.textContent = (siteName().trim()[0] || 'W').toUpperCase();

    var brand = el('a', { class: 'brand', href: '#/' }, [
      mark,
      el('div', { class: 'brand-text' }, [
        el('div', { class: 'brand-name', text: siteName() }),
        siteTagline() ? el('div', { class: 'brand-tag', text: siteTagline() }) : null
      ])
    ]);

    var mode = Theme.resolveMode(data().theme);
    var actions = el('div', { class: 'topbar-actions' }, [
      el('button', {
        class: 'icon-btn',
        'aria-label': 'Language',
        title: 'Language',
        onclick: openLangMenu
      }, icon('globe')),
      el('button', {
        class: 'icon-btn',
        'aria-label': mode === 'dark' ? 'Light mode' : 'Dark mode',
        title: mode === 'dark' ? 'Light mode' : 'Dark mode',
        onclick: function () {
          Store.setPref('mode', mode === 'dark' ? 'light' : 'dark');
          Theme.applyTheme(data().theme);
          renderTopbar();
        }
      }, icon(mode === 'dark' ? 'sun' : 'moon')),
      Feedback.isOffered() ? el('a', {
        class: 'icon-btn',
        href: '#/feedback',
        'aria-label': t('nav.feedback'),
        title: t('nav.feedback')
      }, icon('message')) : null,
      Store.state.isAdmin ? el('button', {
        class: 'icon-btn is-on',
        'aria-label': t('admin.settings'),
        title: t('admin.settings'),
        onclick: function () { Editor.openSettings(); }
      }, icon('settings')) : null
    ]);

    host.appendChild(brand);
    host.appendChild(actions);
  }

  function openLangMenu() {
    var body = el('div', {}, I18n.LANGS.map(function (lang) {
      var active = I18n.getLang() === lang.code;
      return el('button', {
        class: 'btn btn-block' + (active ? ' btn-primary' : ''),
        style: { marginBottom: '8px', justifyContent: 'flex-start' },
        text: lang.label,
        onclick: function () {
          I18n.setLang(lang.code);
          UI.closeSheet();
          App.rerender();
        }
      });
    }));
    UI.openSheet({ title: 'Language / ભાષા / भाषा', body: body, variant: 'center' });
  }

  /* -------------------------------------------------------------- admin bar */

  /* Status notes above the admin bar. Each one exists because its absence let
     the author believe something false about where their writing was. */
  function adminNotices() {
    if (!Store.state.isAdmin) return null;
    var notes = [];

    if (Store.state.saveFailed) {
      notes.push({ kind: 'danger', icon: 'alert', text:
        'This device could not save your latest change — its storage is full. Publish now so ' +
        'nothing is lost, and keep images small.' });
    }
    if (Store.state.loadFailed && Store.state.dirty) {
      notes.push({ kind: 'warn', icon: 'info', text:
        'Could not reach your live site, so you are seeing this device’s own copy. Publishing ' +
        'will still merge safely with what is live.' });
    }
    if (Store.state.pendingLive) {
      notes.push({ kind: 'info', icon: 'refresh', text:
        'Your last publish is still going live on GitHub — usually a minute or two. Until then ' +
        'this device shows what you published.' });
    }
    if (!notes.length) return null;

    return el('div', { class: 'notice-stack' }, notes.map(function (n) {
      return el('div', { class: 'notice notice-' + n.kind, role: n.kind === 'danger' ? 'alert' : 'status' },
        [icon(n.icon), el('span', { text: n.text })]);
    }));
  }

  /* Shown instead of the site when the writing could not be loaded and this
     device holds no copy of its own. An empty site here is a lie — and was
     the start of the chain that published one over real writing. */
  function loadFailedView() {
    var wrap = el('div', { class: 'empty load-failed' }, [
      el('div', { class: 'empty-icon' }, icon('alert')),
      el('div', { class: 'empty-title', text: 'The writing could not be loaded' }),
      el('div', { class: 'empty-hint', text:
        'This is a connection problem, not a missing site — nothing has been deleted. ' +
        'Check the connection and try again.' }),
      el('button', {
        class: 'btn btn-primary',
        style: { marginTop: '20px' },
        onclick: function (e) {
          var btn = e.currentTarget;
          btn.disabled = true;
          btn.textContent = t('state.loading');
          Store.load().then(function () { App.rerender(); });
        }
      }, [icon('refresh'), el('span', { text: t('action.retry') })])
    ]);
    if (Store.state.loadError) {
      wrap.appendChild(el('div', {
        class: 'field-hint', style: { marginTop: '14px' },
        text: 'Details: ' + Store.state.loadError
      }));
    }
    return wrap;
  }

  function adminBar() {
    if (!Store.state.isAdmin || !Store.canEdit()) return null;
    var bar = el('div', { class: 'admin-bar' });

    var text = el('div', { class: 'admin-bar-text' });
    if (Store.state.dirty) {
      text.appendChild(el('span', { class: 'admin-dot' }));
      text.appendChild(document.createTextNode(t('admin.unsaved')));
    } else {
      text.textContent = t('admin.badge');
    }
    bar.appendChild(text);

    bar.appendChild(el('button', {
      class: 'btn btn-sm',
      onclick: function () { Editor.openEntryEditor(null); }
    }, [icon('plus'), el('span', { text: t('action.new') })]));

    bar.appendChild(el('button', {
      class: 'btn btn-sm',
      onclick: function () { Editor.openSettings(); }
    }, [icon('settings'), el('span', { text: t('admin.settings') })]));

    if (Store.state.dirty) {
      bar.appendChild(el('button', {
        class: 'btn btn-sm btn-primary',
        onclick: function () { Publish.publish(); }
      }, [icon('upload'), el('span', { text: t('admin.publish') })]));
    }

    bar.appendChild(el('button', {
      class: 'btn btn-sm btn-ghost',
      'aria-label': t('admin.lock'),
      title: t('admin.lock'),
      onclick: function () {
        Store.lock();
        UI.toast(t('admin.lock'), 'ok');
        App.rerender();
      }
    }, icon('lock')));

    return bar;
  }

  /* ------------------------------------------------------------------ hero */

  function renderHero() {
    var h = data().home.hero;
    if (!h.enabled) return null;

    var hero = el('section', { class: 'hero', 'data-align': h.align || 'center' });
    var inner = el('div', { class: 'hero-inner' });

    var avatar = UI.safeUrl(data().site.avatar);
    if (h.showAvatar && avatar) {
      inner.appendChild(el('img', { class: 'hero-avatar', src: avatar, alt: '' }));
    }
    inner.appendChild(el('h1', { class: 'hero-title', text: h.title || siteName() }));
    var sub = h.subtitle || siteTagline();
    if (sub) inner.appendChild(el('p', { class: 'hero-sub', text: sub }));
    inner.appendChild(el('div', { class: 'hero-rule' }));

    hero.appendChild(inner);
    return hero;
  }

  function renderFeatured() {
    var id = data().home.hero.featuredId;
    if (!id) return null;
    var entry = Store.getEntry(id);
    if (!entry || (entry.draft && !Store.state.isAdmin)) return null;

    var s = styleFor(entry, { scale: false });
    var node = el('article', {
      class: 'featured' + (s.bg ? ' ' + s.cls : ''),
      lang: entry.lang,
      tabindex: '0',
      role: 'button',
      onclick: function () { Router.go('/e/' + entry.id); },
      onkeydown: function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); Router.go('/e/' + entry.id); }
      }
    });
    if (s.bg) {
      node.style.backgroundImage = s.bg.image;
      node.style.backgroundColor = s.bg.color;
    }

    node.appendChild(el('div', { class: 'featured-label', text: typeLabel(entry.type) }));
    if (entry.title) {
      var ft = el('h2', { class: 'featured-title', text: entry.title });
      ft.style.fontFamily = s.css.fontFamily;
      node.appendChild(ft);
    }
    var fb = el('div', { class: 'featured-body', text: preview(entry, 4) });
    applyStyle(fb, s.css);
    if (s.bg) fb.style.color = 'inherit';
    node.appendChild(fb);
    return node;
  }

  /* ------------------------------------------------------------------ tabs */

  function renderTabs() {
    var home = data().home;
    var c = Store.counts();
    var wrap = el('div', { class: 'tabs-wrap' });
    var tabs = el('nav', { class: 'tabs', role: 'tablist' });

    /* "All" is always offered, whatever an imported file says. And if the tab
       currently in view was just switched off in settings, fall back to it —
       otherwise the grid keeps filtering by a tab that is no longer on screen,
       and the page looks broken for no visible reason. */
    var list = (home.tabs || []).slice();
    if (list.indexOf('all') === -1) list.unshift('all');
    if (list.indexOf(view.tab) === -1) {
      view.tab = 'all';
      view.shown = 0;
    }

    list.forEach(function (id) {
      if (id === 'collections' && !c.collections && !Store.state.isAdmin) return;
      var count = c[id] || 0;
      tabs.appendChild(el('button', {
        class: 'tab' + (view.tab === id ? ' is-active' : ''),
        role: 'tab',
        'aria-selected': view.tab === id ? 'true' : 'false',
        onclick: function () {
          view.tab = id;
          view.shown = 0;
          renderMain();
          var wrapNode = UI.$('.tabs-wrap');
          if (wrapNode) {
            var top = wrapNode.getBoundingClientRect().top + global.scrollY - 70;
            if (global.scrollY > top) global.scrollTo({ top: top, behavior: 'smooth' });
          }
        }
      }, [
        el('span', { text: t('tab.' + id) }),
        home.showCounts && count
          ? el('span', { class: 'tab-count', text: String(count) })
          : null
      ]));
    });

    wrap.appendChild(tabs);
    return wrap;
  }

  /* --------------------------------------------------------------- filters */

  function renderFilters() {
    var home = data().home;
    if (!home.showSearch && !home.showLangFilter) return null;
    if (view.tab === 'collections') return null;

    var row = el('div', { class: 'filters' });

    if (home.showSearch) {
      var input = el('input', {
        type: 'search',
        value: view.query,
        placeholder: t('action.searchPlaceholder'),
        'aria-label': t('action.search'),
        oninput: function () {
          view.query = input.value;
          view.shown = 0;
          box.classList.toggle('has-value', !!input.value);
          renderGridOnly();
        }
      });
      var box = el('div', { class: 'search' + (view.query ? ' has-value' : '') }, [
        icon('search'),
        input,
        el('button', {
          class: 'search-clear',
          'aria-label': t('action.clear'),
          onclick: function () {
            input.value = '';
            view.query = '';
            view.shown = 0;
            box.classList.remove('has-value');
            renderGridOnly();
            input.focus();
          },
          type: 'button'
        }, icon('x'))
      ]);
      row.appendChild(box);
    }

    if (home.showLangFilter) {
      var langs = [{ code: 'all', short: t('tab.all') }].concat(I18n.LANGS);
      row.appendChild(el('div', { class: 'chip-row' }, langs.map(function (lang) {
        return el('button', {
          class: 'chip' + (view.lang === lang.code ? ' is-active' : ''),
          text: lang.short,
          title: lang.code === 'all' ? t('lang.all') : t('lang.' + lang.code),
          onclick: function () {
            view.lang = lang.code;
            view.shown = 0;
            renderMain();
          }
        });
      })));
    }

    /* The tag browser stays behind one small button rather than spilling every
       tag across the page. Sites accumulate tags; a filter bar should not grow
       with them. */
    var tags = Store.allTags();
    if (tags.length) {
      row.appendChild(el('button', {
        class: 'chip' + (view.tag ? ' is-active' : ''),
        'aria-label': 'Browse by tag',
        title: 'Browse by tag',
        onclick: function () { openTagBrowser(tags); }
      }, icon('tag')));
    }

    if (view.tag) {
      row.appendChild(el('button', {
        class: 'chip is-active chip-clearable',
        title: t('action.clear'),
        onclick: function () { filterByTag(view.tag); }
      }, [el('span', { text: view.tag }), icon('x')]));
    }

    return row;
  }

  function openTagBrowser(tags) {
    var body = el('div', { class: 'tag-cloud' }, tags.map(function (item) {
      return el('button', {
        class: 'tag-pill tag-pill-lg' + (view.tag === item.tag ? ' is-active' : ''),
        type: 'button',
        onclick: function () {
          UI.closeSheet();
          filterByTag(item.tag);
        }
      }, [
        el('span', { text: item.tag }),
        el('span', { class: 'tag-count', text: String(item.count) })
      ]);
    }));

    UI.openSheet({
      title: 'Browse by tag',
      variant: 'center',
      body: body
    });
  }

  /* ------------------------------------------------------------------ grid */

  function currentEntries() {
    return Store.select({
      type: Store.TYPE_OF_TAB[view.tab] || null,
      lang: view.lang,
      query: view.query,
      tag: view.tag || null,
      collection: view.collection || null
    });
  }

  function emptyState(isSearch) {
    return el('div', { class: 'empty' }, [
      el('div', { class: 'empty-icon' }, icon(isSearch ? 'search' : 'feather')),
      el('div', { class: 'empty-title', text: isSearch ? t('state.noResults') : t('state.empty') }),
      el('div', { class: 'empty-hint', text: isSearch ? t('state.noResultsHint') : t('state.emptyHint') })
    ]);
  }

  /* Renders the grid plus its "See more" button into #grid-host. Kept separate
     from renderMain so typing in the search box doesn't rebuild the header and
     steal focus from the input. */
  function renderGridOnly() {
    var host = UI.$('#grid-host');
    if (!host) return;
    UI.clear(host);

    var home = data().home;

    if (view.tab === 'collections') {
      var cols = (data().collections || []).slice()
        .sort(function (a, b) { return (a.order || 0) - (b.order || 0); });
      if (!cols.length) { host.appendChild(emptyState(false)); return; }
      host.appendChild(el('div', { class: 'grid', 'data-layout': 'grid' },
        cols.map(collectionCard)));
      return;
    }

    var list = currentEntries();
    if (!list.length) {
      host.appendChild(emptyState(!!(view.query || view.tag || view.lang !== 'all')));
      return;
    }

    var perPage = Math.max(1, Number(home.perPage) || 9);
    if (!view.shown) view.shown = perPage;
    var slice = list.slice(0, view.shown);
    var remaining = list.length - slice.length;

    host.appendChild(el('div', {
      class: 'grid',
      'data-layout': home.layout || 'grid'
    }, slice.map(entryCard)));

    if (remaining > 0) {
      host.appendChild(el('div', { class: 'more-wrap' }, el('button', {
        class: 'more-btn',
        onclick: function () {
          view.shown += perPage;
          renderGridOnly();
        }
      }, [
        el('span', { text: t('action.seeMore') }),
        el('span', { class: 'more-count', text: '+' + remaining }),
        icon('arrowDown')
      ])));
    } else if (view.shown > perPage) {
      host.appendChild(el('div', { class: 'more-wrap' }, el('button', {
        class: 'more-btn',
        onclick: function () {
          view.shown = perPage;
          renderGridOnly();
          var g = UI.$('#grid-host');
          if (g) global.scrollTo({ top: g.offsetTop - 120, behavior: 'smooth' });
        }
      }, [el('span', { text: t('action.seeLess') })])));
    }
  }

  /* ------------------------------------------------------------------ main */

  function renderMain() {
    var main = UI.$('#main');
    UI.clear(main);

    if (!Store.canEdit()) {
      main.appendChild(loadFailedView());
      return;
    }

    var notices = adminNotices();
    if (notices) main.appendChild(notices);
    var bar = adminBar();
    if (bar) main.appendChild(bar);

    if (view.collection) {
      var col = Store.getCollection(view.collection);
      main.appendChild(el('section', { class: 'hero', 'data-align': 'left' },
        el('div', { class: 'hero-inner' }, [
          el('button', {
            class: 'btn btn-sm btn-ghost',
            style: { marginBottom: '16px', paddingLeft: '0' },
            onclick: function () { Router.go('/'); }
          }, [icon('arrowLeft'), el('span', { text: t('tab.collections') })]),
          el('h1', { class: 'hero-title', text: (col && col.name) || t('tab.collections') }),
          col && col.desc ? el('p', { class: 'hero-sub', text: col.desc }) : null
        ])));
    } else {
      var hero = renderHero();
      if (hero) main.appendChild(hero);
      var featured = renderFeatured();
      if (featured) main.appendChild(featured);
      main.appendChild(renderTabs());
    }

    var filters = renderFilters();
    if (filters) main.appendChild(filters);

    main.appendChild(el('div', { id: 'grid-host' }));
    renderGridOnly();

    main.appendChild(renderFooter());
  }

  /* ---------------------------------------------------------------- footer */

  function renderFooter() {
    var s = data().site;
    var foot = el('footer', { class: 'site-foot' });

    var links = (s.links || []).filter(function (l) { return l.url && l.label; });
    if (links.length) {
      foot.appendChild(el('div', { class: 'foot-links' }, links.map(function (l) {
        return el('a', {
          href: UI.safeUrl(l.url) || '#',
          target: '_blank',
          rel: 'noopener noreferrer',
          text: l.label
        });
      })));
    }

    foot.appendChild(el('div', { class: 'foot-links' }, [
      el('a', { href: '#/about', text: t('nav.about') }),
      Feedback.isOffered() ? el('a', { href: '#/feedback', text: t('nav.feedback') }) : null
    ].filter(Boolean)));

    if (s.footer) foot.appendChild(el('div', { text: s.footer }));
    else if (s.author) {
      foot.appendChild(el('div', {
        text: '© ' + new Date().getFullYear() + ' ' + s.author
      }));
    }

    if (!Store.state.isAdmin) {
      // A quiet way back in. Not a security boundary — see README.
      foot.appendChild(el('button', {
        class: 'btn btn-sm btn-ghost',
        style: { marginTop: '10px', opacity: '.5' },
        'aria-label': t('admin.unlock'),
        onclick: function () { Editor.openUnlock(); }
      }, icon('lock')));
    }

    return foot;
  }

  /* ----------------------------------------------------------------- about */

  function renderAbout() {
    var main = UI.$('#main');
    UI.clear(main);
    var s = data().site;

    var wrap = el('div', { class: 'about-wrap' });
    wrap.appendChild(el('button', {
      class: 'btn btn-sm btn-ghost',
      style: { marginBottom: '18px', paddingLeft: '0' },
      onclick: function () { Router.go('/'); }
    }, [icon('arrowLeft'), el('span', { text: t('nav.home') })]));

    var avatar = UI.safeUrl(s.avatar);
    if (avatar) wrap.appendChild(el('img', { class: 'about-avatar', src: avatar, alt: '' }));

    wrap.appendChild(el('h1', {
      class: 'hero-title',
      style: { fontSize: 'clamp(28px,5vw,40px)', textAlign: 'center', marginBottom: '18px' },
      text: s.author || siteName()
    }));

    if (s.about) wrap.appendChild(el('p', { class: 'about-text', text: s.about }));

    var links = (s.links || []).filter(function (l) { return l.url && l.label; });
    if (links.length || s.email) {
      wrap.appendChild(el('div', { class: 'about-links' }, links.map(function (l) {
        return el('a', {
          class: 'btn btn-sm',
          href: UI.safeUrl(l.url) || '#',
          target: '_blank',
          rel: 'noopener noreferrer',
          text: l.label
        });
      }).concat(s.email ? [el('a', {
        class: 'btn btn-sm',
        href: 'mailto:' + encodeURIComponent(s.email),
        text: s.email
      })] : [])));
    }

    main.appendChild(wrap);
    main.appendChild(renderFooter());
  }

  /* ---------------------------------------------------------------- reader */

  function openReader(entry) {
    var s = styleFor(entry);
    var st = entry.style || {};
    var meta = stats(entry);

    var reader = el('article', { class: 'reader ' + (s.bg ? s.cls : ''), lang: entry.lang });

    if (s.bg) {
      var bgNode = el('div', { class: 'reader-bg' });
      bgNode.style.backgroundImage = s.bg.image;
      bgNode.style.backgroundColor = s.bg.color;
      if (s.bg.blur) bgNode.style.filter = 'blur(' + s.bg.blur + 'px)';
      reader.appendChild(bgNode);
    }

    var inner = el('div', { class: 'reader-inner' });
    inner.appendChild(el('div', { class: 'reader-kind', text: typeLabel(entry.type) }));
    if (entry.title) {
      var title = el('h1', { class: 'reader-title', text: entry.title });
      title.style.fontFamily = s.css.fontFamily;
      if (st.align && st.align !== 'left') title.style.textAlign = st.align;
      inner.appendChild(title);
    }

    inner.appendChild(el('div', { class: 'reader-meta' }, [
      el('span', { text: I18n.formatDate(entry.date) }),
      el('span', { class: 'card-dot' }),
      el('span', { text: meta.words + ' ' + t('meta.words') }),
      entry.type === 'blog' ? el('span', { class: 'card-dot' }) : null,
      entry.type === 'blog' ? el('span', { text: meta.minutes + ' ' + t('meta.minRead') }) : null,
      entry.draft ? el('span', { class: 'card-draft', text: 'Draft' }) : null
    ]));

    var body = el('div', {
      class: 'reader-body' + (st.frame && st.frame !== 'none' ? ' frame-' + st.frame : ''),
      text: entry.body
    });
    applyStyle(body, s.css);
    inner.appendChild(body);

    if (entry.credit) inner.appendChild(el('div', { class: 'reader-credit', text: entry.credit }));

    if ((entry.tags || []).length) {
      inner.appendChild(el('div', { class: 'reader-tags' }, entry.tags.map(function (tag) {
        return tagPill(tag, { closeSheet: true });
      })));
    }

    var actions = el('div', { class: 'reader-actions' }, [
      el('button', { class: 'btn btn-primary', onclick: function () { Share.openShareSheet(entry); } },
        [icon('share'), el('span', { text: t('action.share') })]),
      el('button', { class: 'btn', onclick: function () { Share.printEntry(entry); } },
        [icon('printer'), el('span', { text: t('action.print') })]),
      Store.state.isAdmin ? el('button', {
        class: 'btn',
        onclick: function () { UI.closeSheet(); Editor.openEntryEditor(entry); }
      }, [icon('edit'), el('span', { text: t('action.edit') })]) : null
    ]);
    inner.appendChild(actions);

    reader.appendChild(inner);

    UI.openSheet({
      title: entry.title || typeLabel(entry.type),
      variant: 'center',
      body: reader,
      onClose: function () {
        // Leaving the reader should put the URL back on the list behind it.
        if (Router.current().indexOf('/e/') === 0) Router.replace(Router.lastList());
      }
    });
  }

  global.Views = {
    view: view,
    loadFailedView: loadFailedView,
    filterByTag: filterByTag,
    tagPill: tagPill,
    renderTopbar: renderTopbar,
    renderMain: renderMain,
    renderAbout: renderAbout,
    renderGridOnly: renderGridOnly,
    renderFooter: renderFooter,
    openReader: openReader,
    entryCard: entryCard,
    styleFor: styleFor,
    preview: preview,
    stats: stats,
    siteName: siteName,
    siteTagline: siteTagline,
    typeLabel: typeLabel
  };
})(window);
