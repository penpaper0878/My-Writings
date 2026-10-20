/* =============================================================================
   publish.js — writes the site's content back into its own GitHub repository.

   This is the piece that makes "free forever" literal. There is no server and
   no database: your writing is a JSON file in a Git repo, GitHub Pages serves
   it, and publishing is a commit. Nothing to renew, nothing to bill, and every
   version you ever published stays in the repo's history.

   The token never leaves this browser. It is not in any file the site serves,
   so a visitor has nothing to find — which is why "only I can upload" holds
   even though every line of this code is public.
   ========================================================================== */
(function (global) {
  'use strict';

  var API = 'https://api.github.com';
  var el = UI.el, t = function () { return I18n.t.apply(I18n, arguments); };

  function data() { return Store.state.data; }
  function token() { return (Store.adminSession().token || '').trim(); }

  /* GitHub wants base64, and the content is full of Gujarati and Devanagari —
     so btoa() alone would throw. Encode to UTF-8 bytes first. */
  function toBase64(str) {
    var bytes = new TextEncoder().encode(str);
    var bin = '';
    var CHUNK = 0x8000;   // chunked, or a long site blows the argument limit
    for (var i = 0; i < bytes.length; i += CHUNK) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
    }
    return btoa(bin);
  }

  /* Work out the repo from the URL the site is being served from, so the admin
     does not have to type it. Handles both user.github.io and project pages. */
  function guessTarget() {
    var host = global.location.hostname || '';
    var parts = global.location.pathname.split('/').filter(Boolean);
    var out = { owner: '', repo: '', path: '' };

    var match = host.match(/^([\w-]+)\.github\.io$/i);
    if (match) {
      out.owner = match[1];
      if (parts.length) {
        // user.github.io/<repo>/<...>/index.html  → project page
        out.repo = parts[0];
        var rest = parts.slice(1).filter(function (p) { return !/\.html?$/i.test(p); });
        out.path = (rest.length ? rest.join('/') + '/' : '') + 'data/content.json';
      } else {
        out.repo = host;                      // user.github.io itself
        out.path = 'data/content.json';
      }
    }
    return out;
  }

  function config() {
    var p = data().publish;
    return {
      owner: (p.owner || '').trim(),
      repo: (p.repo || '').trim(),
      branch: (p.branch || 'main').trim(),
      path: (p.path || 'writings/data/content.json').trim().replace(/^\/+/, '')
    };
  }

  function headers() {
    return {
      'Authorization': 'Bearer ' + token(),
      'Accept': 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28'
    };
  }

  /* Turn GitHub's HTTP codes into something an author can act on. */
  function describeError(res, body) {
    var msg = (body && body.message) || ('HTTP ' + res.status);
    if (res.status === 401) {
      return 'GitHub rejected the token. It may be wrong, expired, or revoked.';
    }
    if (res.status === 403) {
      if (/rate limit/i.test(msg)) return 'GitHub rate limit reached. Try again in a few minutes.';
      return 'The token does not have permission to write to this repository. ' +
             'It needs "Contents: Read and write" for this repo.';
    }
    if (res.status === 404) {
      return 'Repository or branch not found. Check the owner, repository and branch names — ' +
             'and that the token can see this repository.';
    }
    if (res.status === 409) return 'The file changed on GitHub while publishing.';
    if (res.status === 422) return 'GitHub refused the commit: ' + msg;
    return msg;
  }

  function apiJson(res) {
    return res.json().catch(function () { return {}; });
  }

  /* fetch() rejects with a bare "Failed to fetch" whenever the request never
     reaches GitHub at all — no connection, a blocked host, or a page served
     from somewhere that is not allowed to call the API. That message tells an
     author nothing they can act on, so name the real possibilities instead. */
  function networkError(err) {
    var offline = global.navigator && global.navigator.onLine === false;
    var message = offline
      ? 'You appear to be offline. Publishing needs a connection to GitHub — your writing is saved here ' +
        'and will still be waiting when you are back online.'
      : 'Could not reach GitHub at all. Three things do this: no connection; opening this site from a ' +
        'preview or copied link instead of its own address, which browsers do not allow to call GitHub; ' +
        'or an extension or network blocking api.github.com.';
    var wrapped = new Error(message);
    wrapped.cause = err;
    wrapped.network = true;
    return wrapped;
  }

  /* Every call to GitHub goes through here, so a dead network reads the same
     way wherever it happens. */
  function apiFetch(url, init) {
    return fetch(url, init).catch(function (err) {
      // A real HTTP error resolves; only a failed request rejects.
      throw networkError(err);
    });
  }

  /* Fetch the file's current blob SHA. Null means the file is not there yet,
     which is a normal first publish, not an error. */
  function contentsUrl(cfg) {
    return API + '/repos/' + cfg.owner + '/' + cfg.repo + '/contents/' +
      cfg.path.split('/').map(encodeURIComponent).join('/');
  }

  /* GitHub returns file contents as base64 of UTF-8 bytes. atob() alone would
     mangle every Gujarati and Devanagari character, so decode the bytes. */
  function decodeBase64Utf8(b64) {
    var bin = atob(String(b64 || '').replace(/\s+/g, ''));
    var bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new TextDecoder('utf-8').decode(bytes);
  }

  /* What is actually live in the repository right now — sha and parsed
     content. This is the source of truth a publish has to respect; the copy
     this page loaded may be stale, or may never have loaded at all. */
  function fetchRemote(cfg) {
    var url = contentsUrl(cfg) + '?ref=' + encodeURIComponent(cfg.branch);

    return apiFetch(url, { headers: headers(), cache: 'no-store' }).then(function (res) {
      if (res.status === 404) return { sha: null, data: null };
      if (!res.ok) return apiJson(res).then(function (b) { throw new Error(describeError(res, b)); });
      return apiJson(res).then(function (meta) {
        if (meta.content && meta.encoding === 'base64') {
          return { sha: meta.sha, text: decodeBase64Utf8(meta.content) };
        }
        // Over 1 MB the contents API leaves the body out; the blob API has it.
        if (!meta.sha) return { sha: null, text: '' };
        var blobUrl = API + '/repos/' + cfg.owner + '/' + cfg.repo + '/git/blobs/' + meta.sha;
        return apiFetch(blobUrl, { headers: headers(), cache: 'no-store' }).then(function (bres) {
          if (!bres.ok) return apiJson(bres).then(function (b) { throw new Error(describeError(bres, b)); });
          return apiJson(bres).then(function (blob) {
            return { sha: meta.sha, text: decodeBase64Utf8(blob.content) };
          });
        });
      });
    }).then(function (r) {
      if (r.data !== undefined) return r;
      var parsed = null;
      try { parsed = r.text ? JSON.parse(r.text) : null; } catch (err) { parsed = null; }
      return { sha: r.sha, data: Store.looksLikeContent(parsed) ? parsed : null };
    });
  }

  function currentSha(cfg) {
    return fetchRemote(cfg).then(function (r) { return r.sha; });
  }

  function putFile(cfg, sha, content, message) {
    var url = API + '/repos/' + cfg.owner + '/' + cfg.repo + '/contents/' +
      cfg.path.split('/').map(encodeURIComponent).join('/');

    return apiFetch(url, {
      method: 'PUT',
      headers: Object.assign({ 'Content-Type': 'application/json' }, headers()),
      body: JSON.stringify({
        message: message,
        content: toBase64(content),
        branch: cfg.branch,
        sha: sha || undefined
      })
    }).then(function (res) {
      if (!res.ok) return apiJson(res).then(function (b) {
        var err = new Error(describeError(res, b));
        err.status = res.status;
        throw err;
      });
      return apiJson(res);
    });
  }

  /* ------------------------------------------------------------------ test  */

  function test() {
    var cfg = config();
    if (!token()) return Promise.reject(new Error('Paste a GitHub token first.'));
    if (!cfg.owner || !cfg.repo) return Promise.reject(new Error('Fill in the owner and repository.'));

    return apiFetch(API + '/repos/' + cfg.owner + '/' + cfg.repo, { headers: headers() })
      .then(function (res) {
        if (!res.ok) return apiJson(res).then(function (b) { throw new Error(describeError(res, b)); });
        return apiJson(res);
      })
      .then(function (repo) {
        if (repo.permissions && repo.permissions.push === false) {
          throw new Error('The token can read ' + cfg.owner + '/' + cfg.repo + ' but not write to it. ' +
                          'Give it "Contents: Read and write".');
        }
        return currentSha(cfg).then(function (sha) {
          return 'Connected to ' + repo.full_name + ' (' + cfg.branch + '). ' +
            (sha ? 'The content file is there and can be updated.'
                 : 'The content file does not exist yet — it will be created on first publish.');
        });
      });
  }

  /* ------------------------------------------------------- the file itself  */

  /* Exactly what a publish commits. Kept in one place so the manual route and
     the automatic one can never drift apart and produce different files. */
  function contentFile() {
    var payload = Store.exportData();
    payload.updatedAt = new Date().toISOString();
    return { payload: payload, text: JSON.stringify(payload, null, 2) + '\n' };
  }

  /* Counted from the file actually being written, which after a merge can
     hold more than this device had on screen. */
  function commitMessageFor(data) {
    var c = { all: 0, poem: 0, blog: 0, quote: 0 };
    (data.entries || []).forEach(function (e) {
      c.all++;
      if (c[e.type] !== undefined) c[e.type]++;
    });
    return 'Update writings — ' + c.all + ' pieces (' +
      c.poem + ' poems, ' + c.blog + ' blogs, ' + c.quote + ' quotes)';
  }

  function commitMessage() {
    return commitMessageFor(Store.state.data);
  }

  /* The escape hatch when GitHub's token screens will not cooperate. Downloads
     the same file for the author to upload through GitHub's web interface. */
  function downloadContentFile() {
    // This route uploads by hand and cannot merge with the live file, so it
    // must never hand out a file built from nothing.
    if (!Store.canEdit()) {
      UI.toast('Your writing has not loaded on this device yet, so there is nothing safe to ' +
               'download. Reload the page and try again.', 'error', 8000);
      return;
    }
    var file = contentFile();
    UI.downloadBlob(
      new Blob([file.text], { type: 'application/json' }),
      'content.json'
    );
    UI.toast('Saved. Upload it to ' + config().path + ' on GitHub.', 'ok', 6000);
  }

  /* --------------------------------------------------------------- publish  */

  var inFlight = false;

  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }

  /* Tell the author, in words, what the merge did for them. Silence here is
     how they would find out later that a poem came back, or did not. */
  function describeMerge(report) {
    var bits = [];
    if (report.kept) bits.push('kept ' + plural(report.kept, 'piece', 'pieces') +
      ' from your live site that this device did not have');
    if (report.updated) bits.push('took the newer version of ' +
      plural(report.updated, 'piece', 'pieces') + ' edited on another device');
    if (report.settings.length) bits.push('kept your live site settings');
    return bits.length ? bits.join(', ') : '';
  }

  function publish() {
    if (inFlight) return Promise.resolve();
    if (!Store.canEdit()) {
      UI.toast('Your writing has not loaded on this device yet. Reload the page — publishing ' +
               'now could not show you what it was about to change.', 'error', 8000);
      return Promise.resolve();
    }

    var cfg = config();
    if (!token() || !cfg.owner || !cfg.repo) {
      UI.toast('Set up publishing first — owner, repository and token.', 'error');
      Editor.openSettings('publish');
      return Promise.resolve();
    }

    inFlight = true;
    var note = UI.toast(t('admin.publishing'), null, 60000);

    /* Read what is live, fold it into this device's copy, then write that.
       The fold keeps anything this device did not deliberately delete, so a
       publish from a device that loaded nothing — or loaded an old version —
       can no longer wipe the site. A 409 means another publish landed between
       our read and our write; read again and redo the merge. */
    function attempt(retry) {
      return fetchRemote(cfg).then(function (remote) {
        var merged = Store.mergeForPublish(remote.data);
        var payload = merged.data;
        payload.updatedAt = new Date().toISOString();
        var text = JSON.stringify(payload, null, 2) + '\n';
        return putFile(cfg, remote.sha, text, commitMessageFor(payload)).then(function () {
          return { payload: payload, report: merged.report };
        });
      }).catch(function (err) {
        if (err.status === 409 && !retry) return attempt(true);
        throw err;
      });
    }

    return attempt(false)
      .then(function (result) {
        Store.markPublished(result.payload);
        if (note.parentNode) note.parentNode.removeChild(note);
        UI.toast(t('admin.published'), 'ok', 5000);
        var merged = describeMerge(result.report);
        if (merged) UI.toast('While publishing, I ' + merged + '.', null, 9000);
        App.rerender();
      })
      .catch(function (err) {
        if (note.parentNode) note.parentNode.removeChild(note);
        UI.toast(err.message || 'Publishing failed.', 'error', 11000);
        if (err.network) {
          // Nothing was lost — say so, and offer the route that needs no API.
          UI.toast('Your writing is safe on this device. Settings → Publish also lets you ' +
                   'download the file and upload it to GitHub by hand.', null, 11000);
        }
        console.error('[publish]', err);
      })
      .then(function () { inFlight = false; });
  }

  global.Publish = {
    publish: publish,
    fetchRemote: fetchRemote,
    decodeBase64Utf8: decodeBase64Utf8,
    contentFile: contentFile,
    downloadContentFile: downloadContentFile,
    test: test,
    guessTarget: guessTarget,
    toBase64: toBase64
  };
})(window);
