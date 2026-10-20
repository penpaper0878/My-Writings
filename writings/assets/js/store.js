/* =============================================================================
   store.js — the single source of truth.

   Three layers, in priority order when the app boots:
     1. data/content.json   the PUBLISHED site. Everyone sees this.
     2. localStorage draft  the admin's unpublished working copy. Only on the
                            admin's own device; "Publish" pushes it to layer 1.
     3. DEFAULTS            used the very first time, before anything exists.

   Visitors never read layer 2. They physically cannot write to layer 1 either,
   because writing requires a GitHub token that only the admin holds.
   ========================================================================== */
(function (global) {
  'use strict';

  var LS = {
    draft:   'pp.draft.v1',        // admin working copy of the whole site
    meta:    'pp.draftmeta.v1',    // what the draft was based on, for safe merging
    recent:  'pp.lastpublish.v1',  // the last publish, until GitHub Pages serves it
    admin:   'pp.admin.v1',        // { unlocked, token, creds }
    prefs:   'pp.prefs.v1',        // per-visitor: ui language, forced light/dark
    inbox:   'pp.inbox.v1'         // feedback drafts queued while offline
  };

  // How long a publish may take to appear on GitHub Pages before this device
  // stops covering for it. Deploys take a minute or two; this is generous.
  var RECENT_PUBLISH_MS = 15 * 60 * 1000;

  // The settings blocks that merge as a unit when a publish meets the live file.
  var SETTINGS_BLOCKS = ['site', 'theme', 'home', 'feedback'];

  var SCHEMA_VERSION = 1;

  /* ---------------------------------------------------------------- helpers */

  function uid(prefix) {
    return (prefix || 'e') + '_' +
      Date.now().toString(36) +
      Math.random().toString(36).slice(2, 7);
  }

  function isPlainObject(v) {
    return v !== null && typeof v === 'object' && !Array.isArray(v);
  }

  /* Recursively fill in anything the stored data is missing. Arrays are taken
     wholesale from the source — merging them element-wise would resurrect
     entries the author deleted. */
  function deepDefaults(target, defaults) {
    var out = isPlainObject(target) ? target : {};
    Object.keys(defaults).forEach(function (key) {
      var dv = defaults[key];
      if (!(key in out) || out[key] === undefined || out[key] === null) {
        out[key] = isPlainObject(dv) ? deepDefaults({}, dv)
                 : Array.isArray(dv) ? dv.slice()
                 : dv;
      } else if (isPlainObject(dv)) {
        out[key] = deepDefaults(out[key], dv);
      }
    });
    return out;
  }

  function clone(v) {
    return JSON.parse(JSON.stringify(v));
  }

  function readLS(key, fallback) {
    try {
      var raw = global.localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (err) {
      return fallback;
    }
  }

  /* Returns false when the browser refuses — storage full, or blocked in a
     private window. Callers that hold the author's writing must act on that;
     losing a draft silently is how work disappears on refresh. */
  function writeLS(key, value) {
    try {
      global.localStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch (err) {
      return false;
    }
  }

  function removeLS(key) {
    try { global.localStorage.removeItem(key); } catch (err) { /* ignore */ }
  }

  function wait(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  function today() {
    var d = new Date();
    return d.getFullYear() + '-' +
      String(d.getMonth() + 1).padStart(2, '0') + '-' +
      String(d.getDate()).padStart(2, '0');
  }

  /* ---------------------------------------------------------------- defaults */

  var DEFAULT_STYLE = {
    preset: 'inherit',   // 'inherit' = follow the site theme

    /* --- the writing itself --- */
    font: '',            // '' = follow the language default
    size: 1,             // multiplier on the base reading size
    weight: 400,         // 300 light … 800 extra bold
    italic: false,
    underline: false,
    lineHeight: 1.85,
    letterSpacing: 0,
    align: 'left',       // left | center | right | justify
    textColor: '',
    textShadow: false,

    /* --- the heading, styled separately. Empty values follow the writing
       (or, with no piece font set, the site's heading font). --- */
    titleFont: '',
    titleSize: 1,        // multiplier on the default heading size
    titleWeight: 0,      // 0 = the site's heading weight
    titleItalic: false,
    titleUnderline: false,
    titleColor: '',
    titleAlign: '',      // '' = same as the writing

    /* --- background --- */
    bgType: 'inherit',   // inherit | solid | gradient | image
    bgColor: '#101418',
    gradStops: [],       // [{ c: '#hex', p: 0..100 }]; empty = the two below
    gradFrom: '#1b2735',
    gradTo: '#090a0f',
    gradAngle: 160,
    gradKind: 'linear',  // linear | radial
    bgImage: '',
    bgDim: 0.35,
    bgBlur: 0,

    /* --- frame around the writing --- */
    frame: 'none',       // see Theme.FRAMES
    frameColor: '',      // '' = the accent colour
    frameWidth: 2,
    frameRadius: 14,
    framePad: 24,
    frameStyle: 'solid'  // solid | dashed | dotted | double
  };

  var DEFAULTS = {
    version: SCHEMA_VERSION,
    updatedAt: '',

    site: {
      name: 'My Writings',
      nameGu: '',
      nameHi: '',
      tagline: 'Poems, blogs and quotes',
      taglineGu: '',
      taglineHi: '',
      author: '',
      avatar: '',
      about: '',
      email: '',
      footer: '',
      links: []            // [{ label, url }]
    },

    theme: {
      preset: 'ink',
      mode: 'auto',        // auto | light | dark
      accent: '#B4763C',
      bgType: 'preset',    // preset | solid | gradient | image
      bgColor: '',
      gradFrom: '',
      gradTo: '',
      gradAngle: 160,
      gradStops: [],
      gradKind: 'linear',
      bgImage: '',
      bgDim: 0.45,
      bgBlur: 0,
      fontEn: 'Fraunces',
      fontGu: 'Hind Vadodara',
      fontHi: 'Hind',
      fontUi: 'Inter',
      headEn: '',          // heading fonts; '' = same as the body font
      headGu: '',
      headHi: '',
      headWeight: 620,
      baseSize: 17,
      radius: 18,
      cardStyle: 'paper',  // paper | glass | solid | outline | minimal
      density: 'comfortable'
    },

    home: {
      layout: 'grid',      // grid | masonry | list | magazine
      perPage: 9,
      hero: {
        enabled: true,
        title: '',
        subtitle: '',
        showAvatar: true,
        align: 'center',
        featuredId: ''
      },
      tabs: ['all', 'poems', 'blogs', 'quotes', 'collections'],
      showCounts: true,
      showSearch: true,
      showLangFilter: true,
      sortBy: 'date-desc'  // date-desc | date-asc | title | manual
    },

    entries: [],
    collections: [],

    feedback: {
      enabled: true,
      endpoint: '',        // Web3Forms / Formspree / Apps Script URL
      endpointKey: '',     // Web3Forms access_key, if that's the service
      email: '',           // mailto: fallback
      intro: '',
      askName: true,
      askEmail: true,
      askAbout: true       // let a reader point at a specific piece
    },

    admin: {
      passSalt: '',
      passHash: '',
      iterations: 250000
    },

    publish: {
      owner: '',
      repo: '',
      branch: 'main',
      path: 'writings/data/content.json'
    }
  };

  /* ------------------------------------------------------------------- state */

  var state = {
    data: null,          // the live, in-memory site
    published: null,     // the live site as last loaded, or null if it could not be
    base: null,          // what the current edits started from; see snapshotBase()
    isAdmin: false,      // has the admin unlocked this device?
    dirty: false,        // draft differs from published?
    loadFailed: false,   // the live content could not be loaded
    loadError: '',
    saveFailed: false,   // the latest change could not be stored on this device
    pendingLive: false,  // published from here, GitHub Pages not serving it yet
    lastFold: null       // what loading folded into a waiting draft
  };

  /* ---------------------------------------------------------------- normalise */

  function normaliseEntry(raw) {
    var e = deepDefaults(raw || {}, {
      id: '',
      type: 'poem',        // poem | blog | quote
      lang: 'gu',          // gu | hi | en
      title: '',
      body: '',
      excerpt: '',
      tags: [],
      collections: [],
      date: today(),
      pinned: false,
      draft: false,
      cover: '',
      credit: '',
      updatedAt: '',
      style: clone(DEFAULT_STYLE)
    });
    if (!e.id) e.id = uid('e');
    if (!Array.isArray(e.tags)) e.tags = [];
    if (!Array.isArray(e.collections)) e.collections = [];
    e.style = deepDefaults(e.style, DEFAULT_STYLE);
    e.style.gradStops = normaliseStops(e.style.gradStops);
    return e;
  }

  function normaliseStops(stops) {
    if (!Array.isArray(stops)) return [];
    return stops.filter(function (st) { return st && typeof st.c === 'string'; })
      .slice(0, 8)
      .map(function (st) {
        var p = Number(st.p);
        return { c: st.c, p: isFinite(p) ? Math.max(0, Math.min(100, p)) : 0 };
      });
  }

  function normalise(raw) {
    var data = deepDefaults(clone(raw || {}), DEFAULTS);
    data.version = SCHEMA_VERSION;
    data.entries = (Array.isArray(data.entries) ? data.entries : []).map(normaliseEntry);
    data.collections = (Array.isArray(data.collections) ? data.collections : [])
      .map(function (c) {
        var col = deepDefaults(c || {}, {
          id: '', name: '', desc: '', cover: '', accent: '', order: 0
        });
        if (!col.id) col.id = uid('c');
        return col;
      });
    if (!Array.isArray(data.site.links)) data.site.links = [];
    data.theme.gradStops = normaliseStops(data.theme.gradStops);
    return data;
  }

  /* -------------------------------------------------------------- admin gate */

  function bufToHex(buf) {
    return Array.prototype.map.call(new Uint8Array(buf), function (b) {
      return b.toString(16).padStart(2, '0');
    }).join('');
  }

  function randomSaltHex() {
    var bytes = new Uint8Array(16);
    (global.crypto || global.msCrypto).getRandomValues(bytes);
    return bufToHex(bytes.buffer);
  }

  /* PBKDF2-SHA256. The hash lives in a public file, so we make guessing slow
     rather than pretending it's secret. The real barrier to publishing is the
     GitHub token, which is never in any file. */
  function derive(passphrase, saltHex, iterations) {
    var subtle = (global.crypto || {}).subtle;
    if (!subtle) return Promise.reject(new Error('no-webcrypto'));
    var enc = new TextEncoder();
    var salt = new Uint8Array((saltHex.match(/.{1,2}/g) || []).map(function (h) {
      return parseInt(h, 16);
    }));
    return subtle.importKey('raw', enc.encode(passphrase), 'PBKDF2', false, ['deriveBits'])
      .then(function (key) {
        return subtle.deriveBits({
          name: 'PBKDF2', salt: salt, iterations: iterations, hash: 'SHA-256'
        }, key, 256);
      })
      .then(bufToHex);
  }

  /* The published content file is the portable copy — it lets the author unlock
     on any device. The device copy covers the window before the first publish.
     Either one counts. */
  function credentials() {
    var published = state.data && state.data.admin;
    if (published && published.passHash) return published;
    var local = adminSession().creds;
    if (local && local.passHash) return local;
    return null;
  }

  function hasPassphrase() {
    return !!credentials();
  }

  function setPassphrase(passphrase) {
    var salt = randomSaltHex();
    var iters = DEFAULTS.admin.iterations;
    return derive(passphrase, salt, iters).then(function (hash) {
      var creds = { passSalt: salt, passHash: hash, iterations: iters };
      state.data.admin.passSalt = salt;
      state.data.admin.passHash = hash;
      state.data.admin.iterations = iters;
      // Also keep it on the device. The copy in the content file only reaches
      // this browser once the site has been published, and until then locking
      // would otherwise lose the passphrase that unlocks the draft holding it.
      saveAdminSession({ creds: creds });
      markDirty();
      return true;
    });
  }

  function checkPassphrase(passphrase) {
    var a = credentials();
    if (!a) return Promise.resolve(false);
    return derive(passphrase, a.passSalt, a.iterations || DEFAULTS.admin.iterations)
      .then(function (hash) {
        // Constant-time-ish compare. Both strings are the same length here.
        if (hash.length !== a.passHash.length) return false;
        var diff = 0;
        for (var i = 0; i < hash.length; i++) {
          diff |= hash.charCodeAt(i) ^ a.passHash.charCodeAt(i);
        }
        return diff === 0;
      });
  }

  function adminSession() {
    return readLS(LS.admin, { unlocked: false, token: '' });
  }

  function saveAdminSession(patch) {
    var s = deepDefaults(patch || {}, adminSession());
    writeLS(LS.admin, s);
    state.isAdmin = !!s.unlocked;
    return s;
  }

  function unlock() {
    saveAdminSession({ unlocked: true });
    state.isAdmin = true;

    /* A draft is only loaded for an admin, so anything written before this
       device was locked is sitting in storage unread. Adopt it now — otherwise
       the author unlocks and finds their unpublished writing gone. */
    var draft = readLS(LS.draft, null);
    if (draft) {
      var draftData = normalise(draft);
      var meta = draftMeta();
      var base = (meta && meta.base) || null;
      if (state.published) {
        try { draftData = fold(draftData, state.published, meta, base).data; } catch (err) { /* keep draft */ }
      }
      if (!state.published || !sameContent(draftData, state.published)) {
        state.data = draftData;
        state.dirty = true;
        state.base = base || state.base;
        writeLS(LS.draft, state.data);
      }
    }
    emit('change');
  }

  /* Locking hides the editing tools; it does not throw the token away.
     GitHub shows a token exactly once, so discarding it on every lock forces
     the author to generate a brand new one each time they step away — which
     is not security, just a tax on writing. Use forgetToken() to actually
     remove it. */
  function lock() {
    // Flip the flag and keep everything else. Rebuilding the session object
    // here silently drops whatever else it carries — which is how the saved
    // passphrase went missing the moment the author locked the site.
    var session = adminSession();
    session.unlocked = false;
    writeLS(LS.admin, session);
    state.isAdmin = false;
  }

  function forgetToken() {
    var session = adminSession();
    session.token = '';
    writeLS(LS.admin, session);
  }

  /* --------------------------------------------------------------- lifecycle */

  /* ------------------------------------------------------------ draft base */
  /* A publish has to tell apart "deleted here" from "never seen here". Without
     that, it either resurrects deletions or — far worse — erases whatever this
     device had not loaded. The base is a snapshot of the site as it stood when
     the current round of edits began: which pieces existed, and what each
     settings block held. Taken whenever the data is clean, saved with the
     draft on the first change, and cleared by a successful publish. */

  function snapshotBase(data) {
    var ids = {};
    (data.entries || []).forEach(function (e) { ids[e.id] = e.updatedAt || ''; });
    var cols = {};
    (data.collections || []).forEach(function (c) { cols[c.id] = true; });
    var blocks = {};
    SETTINGS_BLOCKS.forEach(function (k) { blocks[k] = JSON.stringify(data[k] || {}); });
    return { ids: ids, cols: cols, blocks: blocks, takenAt: new Date().toISOString() };
  }

  function draftMeta() {
    return readLS(LS.meta, null);
  }

  function setCleanBase() {
    state.base = snapshotBase(state.data);
  }

  /* ------------------------------------------------------------ lifecycle */

  function markDirty() {
    if (!state.dirty) {
      // First change since the data was clean: record what it started from.
      var meta = draftMeta() || {};
      if (!meta.base) {
        meta.base = state.base || snapshotBase(state.published || normalise(DEFAULTS));
        writeLS(LS.meta, meta);
      }
    }
    state.data.updatedAt = new Date().toISOString();
    state.dirty = true;
    var saved = writeLS(LS.draft, state.data);
    var changed = saved === state.saveFailed;
    state.saveFailed = !saved;
    emit('change');
    if (changed) emit('save-state');
  }

  /* After a successful publish. The draft is gone, but the published copy is
     kept on this device until GitHub Pages starts serving it — otherwise a
     refresh in that minute shows the previous version and the newest piece
     appears to have vanished. */
  function markPublished(data) {
    state.data = normalise(data);
    state.published = clone(state.data);
    state.dirty = false;
    state.saveFailed = false;
    state.loadFailed = false;
    state.pendingLive = true;
    removeLS(LS.draft);
    removeLS(LS.meta);
    writeLS(LS.recent, { at: data.updatedAt || '', savedAt: Date.now(), data: data });
    setCleanBase();
    emit('change');
  }

  function discardDraft() {
    // Discarding needs something real to go back to.
    if (!state.published) return false;
    removeLS(LS.draft);
    removeLS(LS.meta);
    state.data = clone(state.published);
    state.dirty = false;
    state.saveFailed = false;
    setCleanBase();
    emit('change');
    return true;
  }

  /* A real content file always has an entries list and a site block. Anything
     else — an empty object, an error page, a truncated download — is a failed
     load, not an empty site. */
  function looksLikeContent(d) {
    return !!d && typeof d === 'object' && !Array.isArray(d) &&
      Array.isArray(d.entries) && !!d.site && typeof d.site === 'object';
  }

  /* Mobile connections drop requests. Three tries, a little apart, before
     concluding the content really cannot be had. */
  function fetchPublished(attempt) {
    attempt = attempt || 1;
    var url = 'data/content.json?v=' + Date.now();
    return fetch(url, { cache: 'no-store' })
      .then(function (res) {
        if (!res.ok) throw new Error('the server answered ' + res.status);
        return res.text();
      })
      .then(function (text) {
        var parsed;
        try { parsed = JSON.parse(text); } catch (err) {
          throw new Error('the content file could not be read');
        }
        if (!looksLikeContent(parsed)) throw new Error('the content file came back empty');
        return parsed;
      })
      .catch(function (err) {
        if (attempt >= 3) throw err;
        return wait(600 * attempt).then(function () { return fetchPublished(attempt + 1); });
      });
  }

  function load() {
    return fetchPublished()
      .then(function (published) {
        state.loadFailed = false;
        state.loadError = '';
        return published;
      }, function (err) {
        state.loadFailed = true;
        state.loadError = String(err && err.message || err);
        return null;
      })
      .then(function (published) {
        var session = adminSession();
        state.isAdmin = !!session.unlocked;

        // A publish from this device that GitHub Pages is not serving yet
        // outranks the older file it is still handing out.
        var recent = readLS(LS.recent, null);
        state.pendingLive = false;
        if (recent && recent.data && looksLikeContent(recent.data)) {
          var fresh = Date.now() - (recent.savedAt || 0) < RECENT_PUBLISH_MS;
          var served = (published && published.updatedAt) || '';
          if (fresh && (!published || served < (recent.at || ''))) {
            published = recent.data;
            state.pendingLive = true;
            state.loadFailed = false;
          } else {
            removeLS(LS.recent);
          }
        }

        state.published = published ? normalise(published) : null;

        var draft = readLS(LS.draft, null);
        var meta = draftMeta();

        if (draft && state.isAdmin) {
          var draftData = normalise(draft);
          state.base = (meta && meta.base) || null;
          if (state.published) {
            // A draft can be older than what is live — written before another
            // device published, or before a restore. Fold the live pieces in
            // so the screen never shows less than the site actually has.
            try {
              var folded = fold(draftData, state.published, meta, state.base);
              draftData = folded.data;
              state.lastFold = folded.report;
            } catch (err) {
              state.lastFold = null;
            }
          }
          if (state.published && sameContent(draftData, state.published)) {
            removeLS(LS.draft);
            removeLS(LS.meta);
            state.data = clone(state.published);
            state.dirty = false;
            setCleanBase();
          } else {
            state.data = draftData;
            state.dirty = true;
            writeLS(LS.draft, state.data);
          }
        } else if (state.published) {
          state.data = clone(state.published);
          state.dirty = false;
          setCleanBase();
        } else {
          // Nothing trustworthy to show. This used to fall through to an empty
          // default site presented as the real one — an admin who then wrote a
          // poem and pressed Publish erased everything that was live. Now the
          // page says the load failed, and editing waits until it succeeds.
          state.data = normalise(DEFAULTS);
          state.dirty = false;
          state.base = null;
        }
        emit('change');
        return state.data;
      });
  }

  /* Can the admin safely change things right now? Only with a real baseline:
     the live content, or a draft of their own. */
  function canEdit() {
    return !!(state.published || state.dirty);
  }

  /* ------------------------------------------------------------ publish merge */
  /* Fold the live file into what this device is about to publish. The rule is
     simple and absolute: a publish may remove only what was removed on this
     device. Pieces this device never saw are kept; a piece edited more
     recently elsewhere wins over the older copy here; settings untouched here
     follow the live site; and the passphrase is never dropped. */
  function fold(localData, remote, meta, base) {
    var local = clone(localData);
    meta = meta || {};
    var report = { kept: 0, updated: 0, deletedHere: 0, settings: [] };

    if (!remote || !looksLikeContent(remote)) {
      return { data: normalise(local), report: report };
    }

    if (!meta.replaced) {
      var baseIds = (base && base.ids) || {};
      var mine = {};
      local.entries.forEach(function (e, i) { mine[e.id] = i; });

      remote.entries.forEach(function (re) {
        if (!re || !re.id) return;
        var at = re.updatedAt || '';
        if (mine[re.id] === undefined) {
          var wasHere = Object.prototype.hasOwnProperty.call(baseIds, re.id);
          var editedSince = at > (baseIds[re.id] || '');
          if (wasHere && !editedSince) { report.deletedHere++; return; }
          local.entries.push(normaliseEntry(clone(re)));
          report.kept++;
        } else {
          var le = local.entries[mine[re.id]];
          if (at && at > (le.updatedAt || '')) {
            local.entries[mine[re.id]] = normaliseEntry(clone(re));
            report.updated++;
          }
        }
      });

      var baseCols = (base && base.cols) || {};
      var haveCol = {};
      (local.collections || []).forEach(function (c) { haveCol[c.id] = true; });
      (remote.collections || []).forEach(function (rc) {
        if (!rc || !rc.id || haveCol[rc.id] || baseCols[rc.id]) return;
        local.collections.push(clone(rc));
      });

      SETTINGS_BLOCKS.forEach(function (k) {
        if (!remote[k]) return;
        var untouched = base
          ? JSON.stringify(local[k] || {}) === base.blocks[k]
          : JSON.stringify(normalise({})[k]) === JSON.stringify(normalise(local)[k]);
        if (untouched && JSON.stringify(remote[k]) !== JSON.stringify(local[k])) {
          local[k] = clone(remote[k]);
          report.settings.push(k);
        }
      });
    }

    if ((!local.admin || !local.admin.passHash) && remote.admin && remote.admin.passHash) {
      local.admin = clone(remote.admin);
    }
    if (remote.publish) {
      ['owner', 'repo', 'branch', 'path'].forEach(function (k) {
        if (!local.publish[k] && remote.publish[k]) local.publish[k] = remote.publish[k];
      });
    }

    var merged = normalise(local);
    // Belt and braces: by construction every live piece is either in the
    // result or was deleted here on purpose. If that ever stops being true,
    // refuse rather than publish a loss.
    if (!meta.replaced && merged.entries.length < remote.entries.length - report.deletedHere) {
      throw new Error('Publishing was stopped: it would have removed writing that was not ' +
                      'deleted on this device. Nothing was changed.');
    }
    return { data: merged, report: report };
  }

  function mergeForPublish(remote) {
    var meta = draftMeta() || {};
    return fold(state.data, remote, meta, meta.base || state.base || null);
  }

  /* Two versions hold the same site when everything but the timestamp matches;
     the timestamp changes on every keystroke and would never compare equal. */
  function sameContent(a, b) {
    var strip = function (data) {
      var copy = clone(data);
      copy.updatedAt = '';
      return JSON.stringify(copy);
    };
    try {
      return strip(a) === strip(b);
    } catch (err) {
      return false;
    }
  }

  /* ------------------------------------------------------------------ events */

  var listeners = {};

  function on(name, fn) {
    (listeners[name] = listeners[name] || []).push(fn);
    return function off() {
      listeners[name] = (listeners[name] || []).filter(function (f) { return f !== fn; });
    };
  }

  function emit(name, payload) {
    (listeners[name] || []).forEach(function (fn) {
      try { fn(payload); } catch (err) { console.error('[store]', name, err); }
    });
  }

  /* ------------------------------------------------------------ entry CRUD  */

  function entries() {
    return state.data ? state.data.entries : [];
  }

  function visibleEntries() {
    // Drafts are the author's private work-in-progress until published.
    return entries().filter(function (e) { return state.isAdmin || !e.draft; });
  }

  function getEntry(id) {
    return entries().filter(function (e) { return e.id === id; })[0] || null;
  }

  function addEntry(partial) {
    var e = normaliseEntry(partial);
    e.updatedAt = new Date().toISOString();
    state.data.entries.unshift(e);
    markDirty();
    return e;
  }

  function updateEntry(id, patch) {
    var e = getEntry(id);
    if (!e) return null;
    Object.keys(patch).forEach(function (k) { e[k] = patch[k]; });
    // Set after copying, so a patch carrying an old stamp cannot keep it.
    e.updatedAt = new Date().toISOString();
    markDirty();
    return e;
  }

  function removeEntry(id) {
    state.data.entries = entries().filter(function (e) { return e.id !== id; });
    markDirty();
  }

  function getCollection(id) {
    return (state.data.collections || []).filter(function (c) { return c.id === id; })[0] || null;
  }

  function addCollection(partial) {
    var c = deepDefaults(partial || {}, { id: uid('c'), name: '', desc: '', cover: '', accent: '', order: 0 });
    if (!c.id) c.id = uid('c');
    state.data.collections.push(c);
    markDirty();
    return c;
  }

  function removeCollection(id) {
    state.data.collections = state.data.collections.filter(function (c) { return c.id !== id; });
    entries().forEach(function (e) {
      e.collections = (e.collections || []).filter(function (cid) { return cid !== id; });
    });
    markDirty();
  }

  /* --------------------------------------------------------------- selectors */

  var TYPE_OF_TAB = { poems: 'poem', blogs: 'blog', quotes: 'quote' };

  function select(opts) {
    opts = opts || {};
    var list = visibleEntries();

    if (opts.type) list = list.filter(function (e) { return e.type === opts.type; });
    if (opts.lang && opts.lang !== 'all') list = list.filter(function (e) { return e.lang === opts.lang; });
    if (opts.collection) {
      list = list.filter(function (e) {
        return (e.collections || []).indexOf(opts.collection) !== -1;
      });
    }
    if (opts.tag) {
      list = list.filter(function (e) { return (e.tags || []).indexOf(opts.tag) !== -1; });
    }
    if (opts.query) {
      var q = opts.query.trim().toLowerCase();
      if (q) {
        list = list.filter(function (e) {
          return (e.title + ' ' + e.body + ' ' + (e.tags || []).join(' '))
            .toLowerCase().indexOf(q) !== -1;
        });
      }
    }

    var sortBy = opts.sortBy || (state.data.home && state.data.home.sortBy) || 'date-desc';
    if (sortBy !== 'manual') {
      list = list.slice().sort(function (a, b) {
        if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
        if (sortBy === 'title') return String(a.title).localeCompare(String(b.title));
        if (sortBy === 'date-asc') return String(a.date).localeCompare(String(b.date));
        return String(b.date).localeCompare(String(a.date));
      });
    }
    return list;
  }

  function allTags() {
    var seen = {};
    visibleEntries().forEach(function (e) {
      (e.tags || []).forEach(function (t) { seen[t] = (seen[t] || 0) + 1; });
    });
    return Object.keys(seen).sort(function (a, b) { return seen[b] - seen[a]; })
      .map(function (t) { return { tag: t, count: seen[t] }; });
  }

  function counts() {
    var c = { all: 0, poems: 0, blogs: 0, quotes: 0, collections: 0, gu: 0, hi: 0, en: 0 };
    visibleEntries().forEach(function (e) {
      c.all++;
      if (e.type === 'poem') c.poems++;
      else if (e.type === 'blog') c.blogs++;
      else if (e.type === 'quote') c.quotes++;
      if (c[e.lang] !== undefined) c[e.lang]++;
    });
    c.collections = (state.data.collections || []).length;
    return c;
  }

  /* ------------------------------------------------------------------ prefs */

  function prefs() {
    return readLS(LS.prefs, { uiLang: '', mode: '' });
  }

  function setPref(key, value) {
    var p = prefs();
    p[key] = value;
    writeLS(LS.prefs, p);
    emit('prefs');
    return p;
  }

  /* ------------------------------------------------------------------ export */

  function exportData() {
    return clone(state.data);
  }

  function importData(raw, mode) {
    var incoming = normalise(raw);
    if (mode === 'merge') {
      var byId = {};
      entries().forEach(function (e) { byId[e.id] = true; });
      incoming.entries.forEach(function (e) {
        if (byId[e.id]) e.id = uid('e');           // keep both copies
        state.data.entries.push(e);
      });
      var colById = {};
      (state.data.collections || []).forEach(function (c) { colById[c.id] = true; });
      incoming.collections.forEach(function (c) {
        if (!colById[c.id]) state.data.collections.push(c);
      });
    } else {
      // Replacing everything must not lock the admin out of their own site.
      var keptAdmin = state.data.admin;
      var keptPublish = state.data.publish;
      state.data = incoming;
      if (!state.data.admin.passHash) state.data.admin = keptAdmin;
      if (!state.data.publish.owner) state.data.publish = keptPublish;
      // "Replace all" means exactly this file — publishing should not fold the
      // live pieces back in.
      var meta = draftMeta() || {};
      meta.replaced = true;
      writeLS(LS.meta, meta);
    }
    markDirty();
    return state.data;
  }

  global.Store = {
    LS: LS,
    DEFAULTS: DEFAULTS,
    DEFAULT_STYLE: DEFAULT_STYLE,
    TYPE_OF_TAB: TYPE_OF_TAB,
    state: state,
    uid: uid,
    clone: clone,
    today: today,
    deepDefaults: deepDefaults,
    readLS: readLS,
    writeLS: writeLS,
    removeLS: removeLS,

    load: load,
    canEdit: canEdit,
    looksLikeContent: looksLikeContent,
    mergeForPublish: mergeForPublish,
    fold: fold,
    on: on,
    emit: emit,
    markDirty: markDirty,
    markPublished: markPublished,
    discardDraft: discardDraft,
    sameContent: sameContent,

    hasPassphrase: hasPassphrase,
    credentials: credentials,
    setPassphrase: setPassphrase,
    checkPassphrase: checkPassphrase,
    adminSession: adminSession,
    saveAdminSession: saveAdminSession,
    unlock: unlock,
    lock: lock,
    forgetToken: forgetToken,

    entries: entries,
    visibleEntries: visibleEntries,
    getEntry: getEntry,
    addEntry: addEntry,
    updateEntry: updateEntry,
    removeEntry: removeEntry,
    normaliseEntry: normaliseEntry,
    normaliseStops: normaliseStops,

    getCollection: getCollection,
    addCollection: addCollection,
    removeCollection: removeCollection,

    select: select,
    allTags: allTags,
    counts: counts,

    prefs: prefs,
    setPref: setPref,

    exportData: exportData,
    importData: importData
  };
})(window);
