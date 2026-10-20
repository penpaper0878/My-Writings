/* =============================================================================
   rich.js — bold, italic and underline inside a piece of writing.

   The writing stays plain text with three light markers, so it survives
   export, backup, copy-and-paste and ten years of browsers unchanged:

       **bold**        *italic*        __underline__

   Markers pair up within a single line. One without a partner is just a
   character, shown as typed — so a lone "*" in a poem never swallows the rest
   of the verse.

   Every renderer goes through runs(): the page, the print view and the image
   drawn on canvas. That is what keeps all three agreeing about which words
   are bold.
   ========================================================================== */
(function (global) {
  'use strict';

  var MARKERS = [
    { token: '**', flag: 'b' },
    { token: '__', flag: 'u' },
    { token: '*',  flag: 'i' }
  ];

  /* Find marker positions that have a partner later on the same line. */
  function pairedMarkers(line) {
    var taken = new Array(line.length).fill(false);
    var events = [];

    MARKERS.forEach(function (m) {
      var found = [];
      var i = 0;
      while (i <= line.length - m.token.length) {
        if (!taken[i] && line.substr(i, m.token.length) === m.token) {
          // A single "*" that is really half of "**" belongs to bold.
          var free = true;
          for (var k = 0; k < m.token.length; k++) if (taken[i + k]) free = false;
          if (free) {
            found.push(i);
            i += m.token.length;
            continue;
          }
        }
        i++;
      }
      // Pair them left to right; an odd one out stays literal.
      for (var j = 0; j + 1 < found.length; j += 2) {
        var open = found[j], close = found[j + 1];
        // An empty pair ("****") formats nothing; leave it as text.
        if (close === open + m.token.length) continue;
        [open, close].forEach(function (pos) {
          for (var k = 0; k < m.token.length; k++) taken[pos + k] = true;
          events.push({ at: pos, len: m.token.length, flag: m.flag });
        });
      }
    });

    events.sort(function (a, b) { return a.at - b.at; });
    return events;
  }

  /* One line → [{ t: text, b, i, u }]. */
  function runs(line) {
    line = String(line == null ? '' : line);
    var events = pairedMarkers(line);
    var out = [];
    var flags = { b: false, i: false, u: false };
    var pos = 0;
    var buf = '';

    function flush() {
      if (buf) out.push({ t: buf, b: flags.b, i: flags.i, u: flags.u });
      buf = '';
    }

    events.forEach(function (ev) {
      buf += line.slice(pos, ev.at);
      flush();
      flags[ev.flag] = !flags[ev.flag];
      pos = ev.at + ev.len;
    });
    buf += line.slice(pos);
    flush();
    return out;
  }

  function hasMarkup(text) {
    return String(text || '').split('\n').some(function (line) {
      return pairedMarkers(line).length > 0;
    });
  }

  /* The words without the markers — for copy-as-text, WhatsApp, .txt files
     and anywhere else formatting cannot travel. */
  function plain(text) {
    return String(text == null ? '' : text).split('\n').map(function (line) {
      return runs(line).map(function (r) { return r.t; }).join('');
    }).join('\n');
  }

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /* Safe HTML: every character of the writing is escaped, and the only tags
     present are the three this module adds. Line breaks stay as "\n" for a
     white-space: pre-wrap container to keep. */
  function html(text) {
    return String(text == null ? '' : text).split('\n').map(function (line) {
      return runs(line).map(function (r) {
        var h = esc(r.t);
        if (r.u) h = '<u>' + h + '</u>';
        if (r.i) h = '<em>' + h + '</em>';
        if (r.b) h = '<strong>' + h + '</strong>';
        return h;
      }).join('');
    }).join('\n');
  }

  /* Write formatted text into an element. */
  function render(node, text) {
    node.innerHTML = html(text);
    return node;
  }

  /* Wrap the selected text in a textarea with a marker, or remove the marker if
     it is already there — pressing B twice should undo, not double up. The
     selection stays on the words themselves, never on the markers, so the
     next press sees them as wrapped. */
  function isWrapped(text, token) {
    var L = token.length;
    return text.length > 2 * L && text.slice(0, L) === token && text.slice(-L) === token;
  }

  function toggleInTextarea(textarea, token) {
    var v = textarea.value;
    var a = textarea.selectionStart, z = textarea.selectionEnd;
    var L = token.length;
    var selected = v.slice(a, z);

    function finish(value, start, end) {
      textarea.value = value;
      textarea.selectionStart = start;
      textarea.selectionEnd = end;
      textarea.focus();
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
    }

    // Nothing selected: drop in a pair with the cursor between.
    if (a === z) return finish(v.slice(0, a) + token + token + v.slice(z), a + L, a + L);

    // Markers sit just outside the selection: take them away.
    if (v.slice(a - L, a) === token && v.slice(z, z + L) === token) {
      return finish(v.slice(0, a - L) + selected + v.slice(z + L), a - L, z - L);
    }

    var lines = selected.split('\n');
    var filled = lines.filter(function (l) { return l.trim(); });

    // Formatting stops at line ends, so each line is wrapped on its own. If
    // every selected line is already wrapped, unwrap them all instead.
    if (filled.length && filled.every(function (l) { return isWrapped(l, token); })) {
      var bare = lines.map(function (l) { return isWrapped(l, token) ? l.slice(L, -L) : l; }).join('\n');
      return finish(v.slice(0, a) + bare + v.slice(z), a, a + bare.length);
    }

    if (lines.length === 1) {
      return finish(v.slice(0, a) + token + selected + token + v.slice(z), a + L, z + L);
    }
    var wrapped = lines.map(function (l) { return l.trim() ? token + l + token : l; }).join('\n');
    return finish(v.slice(0, a) + wrapped + v.slice(z), a, a + wrapped.length);
  }

  global.Rich = {
    runs: runs,
    plain: plain,
    html: html,
    render: render,
    hasMarkup: hasMarkup,
    toggleInTextarea: toggleInTextarea
  };
})(window);
