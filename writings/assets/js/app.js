/* =============================================================================
   app.js — routing and start-up.

   Routes live in the hash so the whole site stays a single static file that
   GitHub Pages can serve from any folder, with no redirect rules and no build
   step:

     #/            the home page
     #/c/<id>      one collection
     #/e/<id>      one piece, opened over whatever list you came from
     #/feedback    the feedback form
     #/about       the about page
     #/admin       the unlock prompt
   ========================================================================== */
(function (global) {
  'use strict';

  var el = UI.el, icon = UI.icon, t = function () { return I18n.t.apply(I18n, arguments); };

  /* ---------------------------------------------------------------- router */

  var lastList = '/';
  var routing = false;

  function current() {
    var hash = global.location.hash.replace(/^#/, '');
    return hash || '/';
  }

  function go(path) {
    if (current() === path) { route(); return; }
    global.location.hash = path;
  }

  function replace(path) {
    var url = global.location.pathname + global.location.search + '#' + path;
    try {
      global.history.replaceState(null, '', url);
    } catch (err) {
      global.location.hash = path;   // older browsers, or a file:// page
    }
  }

  function route() {
    if (routing) return;
    routing = true;

    var path = current();

    try {
      if (path.indexOf('/e/') === 0) {
        var entry = Store.getEntry(path.slice(3));
        if (!entry || (entry.draft && !Store.state.isAdmin)) {
          UI.toast('That piece is not here.', 'error');
          replace('/');
          renderRoute('/');
          return;
        }
        // Only build the list underneath when there isn't one — arriving on a
        // shared link. Rebuilding it for a card you just tapped would scroll
        // the page back to the top behind the reader, and closing it would
        // drop you somewhere you never were.
        var main = UI.$('#main');
        if (!main || !main.childNodes.length) renderRoute(lastList);
        UI.closeAllSheets();
        Views.openReader(entry);
        return;
      }

      UI.closeAllSheets();
      lastList = path;
      renderRoute(path);
    } finally {
      routing = false;
    }
  }

  function renderRoute(path, keepScroll) {
    if (path === '/feedback') {
      Views.view.collection = '';
      Feedback.render();
    } else if (path === '/about') {
      Views.view.collection = '';
      Views.renderAbout();
    } else if (path === '/admin') {
      Views.view.collection = '';
      Views.renderMain();
      if (!Store.state.isAdmin) Editor.openUnlock();
      else Editor.openSettings();
    } else if (path.indexOf('/c/') === 0) {
      Views.view.collection = path.slice(3);
      Views.view.tab = 'all';
      Views.view.shown = 0;
      Views.renderMain();
    } else {
      Views.view.collection = '';
      Views.renderMain();
    }

    renderFab();
    if (!keepScroll) global.scrollTo({ top: 0, behavior: 'auto' });
  }

  /* ------------------------------------------------------------------- fab */

  function renderFab() {
    var existing = UI.$('#fab');
    if (existing) existing.parentNode.removeChild(existing);
    if (!Store.state.isAdmin || !Store.canEdit()) return;
    if (current() === '/feedback') return;

    document.body.appendChild(el('button', {
      id: 'fab',
      class: 'fab',
      'aria-label': t('action.new'),
      title: t('action.new'),
      onclick: function () { Editor.openEntryEditor(null); }
    }, icon('plus')));
  }

  /* -------------------------------------------------------------- rerender */

  /* Redraw in place after an edit. Holding the scroll position matters: saving
     a piece halfway down a long list should leave you looking at it, not back
     at the top of the page. */
  function rerender() {
    var y = global.scrollY;
    syncTitle();
    Views.renderTopbar();
    renderRoute(current().indexOf('/e/') === 0 ? lastList : current(), true);
    global.scrollTo({ top: y, behavior: 'auto' });
  }

  /* --------------------------------------------------------- sticky topbar */

  function watchScroll() {
    var bar = UI.$('#topbar-outer');
    var ticking = false;

    function apply() {
      var scrolled = global.scrollY > 8;
      if (bar) bar.classList.toggle('is-stuck', scrolled);
      document.body.classList.toggle('is-scrolled', scrolled);
      ticking = false;
    }

    global.addEventListener('scroll', function () {
      if (ticking) return;
      ticking = true;
      global.requestAnimationFrame(apply);
    }, { passive: true });

    apply();
  }

  /* -------------------------------------------------------- service worker */

  var bootedAt = Date.now();

  function registerWorker() {
    if (!('serviceWorker' in global.navigator)) return;
    if (global.location.protocol === 'file:') return;   // not allowed there
    if (global.__PREVIEW__) return;                    // sandboxed preview

    /* When a new version of the site installs, the page in front of the reader
       is still running the old code. Reload once into the new version — but
       only right after opening, never with an editor open or work in hand. */
    var hadController = !!global.navigator.serviceWorker.controller;
    var reloading = false;
    global.navigator.serviceWorker.addEventListener('controllerchange', function () {
      if (!hadController || reloading) return;
      var early = Date.now() - bootedAt < 20000;
      if (early && !UI.sheetDepth()) {
        reloading = true;
        global.location.reload();
      } else {
        UI.toast('A newer version of the site is ready — reload when convenient.', null, 8000);
      }
    });

    global.navigator.serviceWorker.register('sw.js').catch(function (err) {
      console.warn('[sw]', err && err.message);
    });
  }

  /* ------------------------------------------------------------ page title */

  function syncTitle() {
    var name = Views.siteName();
    document.title = name;
    var desc = document.querySelector('meta[name="description"]');
    if (desc && Views.siteTagline()) desc.setAttribute('content', Views.siteTagline());

    var manifestName = document.querySelector('link[rel="manifest"]');
    if (manifestName) manifestName.setAttribute('href', 'manifest.json');
  }

  /* ------------------------------------------------------------------ boot */

  function boot() {
    I18n.setLang(I18n.detect());

    Store.load().then(function () {
      // The publish target is usually derivable from the URL; fill it in so
      // the admin only ever has to paste a token.
      var p = Store.state.data.publish;
      var guess = Publish.guessTarget();
      if (!p.owner || !p.repo) {
        if (guess.owner && guess.repo) {
          p.owner = p.owner || guess.owner;
          p.repo = p.repo || guess.repo;
          if (guess.path && p.path === Store.DEFAULTS.publish.path) p.path = guess.path;
        }
      } else if (guess.owner && guess.repo &&
                 (p.owner.toLowerCase() !== guess.owner.toLowerCase() ||
                  p.repo.toLowerCase() !== guess.repo.toLowerCase())) {
        // On github.io the address names the repository serving this page,
        // and that is the only one whose content.json this page reads. A
        // renamed repository leaves the old name in the saved settings.
        p.owner = guess.owner;
        p.repo = guess.repo;
      }

      Theme.applyTheme(Store.state.data.theme);
      syncTitle();
      Views.renderTopbar();
      route();
      watchScroll();
      registerWorker();

      var fold = Store.state.lastFold;
      if (fold && (fold.kept || fold.updated)) {
        UI.toast('Your copy on this device was older than your live site — brought in ' +
          (fold.kept + fold.updated) + ' newer ' + (fold.kept + fold.updated === 1 ? 'piece' : 'pieces') +
          '. Publish when ready.', null, 9000);
      }

      if (Store.state.loadError && Store.state.isAdmin) {
        console.info('[store] no published content yet:', Store.state.loadError);
      }
    }).catch(function (err) {
      console.error('[boot]', err);
      var main = UI.$('#main');
      if (main) {
        UI.clear(main);
        main.appendChild(el('div', { class: 'empty' }, [
          el('div', { class: 'empty-icon' }, icon('alert')),
          el('div', { class: 'empty-title', text: 'This page could not start.' }),
          el('div', { class: 'empty-hint', text: String(err && err.message || err) })
        ]));
      }
    });

    global.addEventListener('hashchange', route);

    // Follow the OS between light and dark, but only while the site is set to
    // "match device" and the reader has not overridden it themselves.
    Theme.watchSystemMode(function () {
      if (!Store.state.data) return;
      if (Store.prefs().mode) return;
      if (Store.state.data.theme.mode !== 'auto') return;
      Theme.applyTheme(Store.state.data.theme);
      rerender();
    });

    // Keyboard shortcuts for the admin. Ignored while typing.
    document.addEventListener('keydown', function (e) {
      var tag = (e.target && e.target.tagName) || '';
      if (/INPUT|TEXTAREA|SELECT/.test(tag) || e.target.isContentEditable) return;
      if (!Store.state.isAdmin || !Store.canEdit()) return;

      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        Publish.publish();
      } else if (e.key === 'n' && !e.metaKey && !e.ctrlKey && !UI.sheetDepth()) {
        e.preventDefault();
        Editor.openEntryEditor(null);
      }
    });
  }

  global.Router = {
    go: go,
    replace: replace,
    current: current,
    route: route,
    lastList: function () { return lastList; }
  };

  global.App = {
    boot: boot,
    rerender: rerender,
    renderFab: renderFab,
    syncTitle: syncTitle
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})(window);
