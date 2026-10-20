/* =============================================================================
   share.js — getting a piece of writing out of the site: as a picture, a PDF,
   plain text, or a link.

   Two deliberate choices worth knowing about:

   * The image is drawn on a <canvas> by hand rather than with a screenshot
     library. That keeps the site dependency-free and, more importantly, lets
     the browser's own text engine shape Gujarati and Devanagari — matras,
     conjuncts and all.

   * "Save as PDF" goes through the browser's print dialog instead of a
     JavaScript PDF library. Those libraries need an embedded font with full
     Indic shaping tables to render કવિતા or कविता correctly, and mostly get it
     wrong. Print-to-PDF uses the same renderer as the screen, so it is always
     right — and it is free.
   ========================================================================== */
(function (global) {
  'use strict';

  var el = UI.el, icon = UI.icon, t = function () { return I18n.t.apply(I18n, arguments); };

  var SIZES = [
    { id: 'square',   label: 'Square',    w: 1080, h: 1080, note: 'Instagram post' },
    { id: 'portrait', label: 'Portrait',  w: 1080, h: 1350, note: 'Instagram / Facebook' },
    { id: 'story',    label: 'Story',     w: 1080, h: 1920, note: 'Status / Reels' },
    { id: 'wide',     label: 'Wide',      w: 1600, h: 900,  note: 'Twitter / X' }
  ];

  function data() { return Store.state.data; }

  /* ------------------------------------------------------------- plain text */

  function asText(entry, opts) {
    opts = opts || {};
    var out = [];
    if (entry.title) out.push(entry.title, '');
    out.push(Rich.plain(String(entry.body || '').trim()));
    if (entry.credit) out.push('', entry.credit);

    if (opts.signature !== false) {
      var s = data().site;
      var who = s.author || s.name;
      if (who) out.push('', '— ' + who);
      if (opts.link !== false) out.push(entryUrl(entry));
    }
    return out.join('\n');
  }

  function entryUrl(entry) {
    var base = global.location.origin + global.location.pathname;
    return base + '#/e/' + entry.id;
  }

  function safeFilename(entry) {
    var name = (entry.title || Views.typeLabel(entry.type) || 'writing')
      .replace(/[\\/:*?"<>|]+/g, '')
      .replace(/\s+/g, '-')
      .slice(0, 60);
    return (name || 'writing') + '-' + entry.date;
  }

  /* ------------------------------------------------------------ canvas image */
  /* The shared picture is drawn by hand so the browser's own text engine
     shapes Gujarati and Devanagari. It mirrors the page: the same fonts,
     weights, slants, underlines, heading style, background and frame. */

  /* A line of writing → words, each carrying its own style, so one bold word
     in a line is measured and drawn bold. Spaces are kept as their own pieces
     so wrapping can break on them. */
  function tokens(line) {
    var out = [];
    Rich.runs(line).forEach(function (run) {
      run.t.split(/(\s+)/).forEach(function (piece) {
        if (piece) out.push({ t: piece, b: run.b, i: run.i, u: run.u, space: /^\s+$/.test(piece) });
      });
    });
    return out;
  }

  function fontString(tok, base) {
    var weight = tok.b ? Math.max(700, Math.min(900, base.weight + 300)) : base.weight;
    var italic = tok.i || base.italic;
    return (italic ? 'italic ' : '') + weight + ' ' + base.size + 'px ' + base.family;
  }

  /* Greedy word wrap that honours the author's own line breaks first — in a
     poem those breaks are the poem. Returns lines of positioned pieces. */
  function layout(ctx, text, maxWidth, base) {
    var lines = [];
    String(text || '').split('\n').forEach(function (paragraph) {
      if (!paragraph.trim()) { lines.push({ items: [], width: 0 }); return; }
      var line = { items: [], width: 0 };
      tokens(paragraph).forEach(function (tok) {
        ctx.font = fontString(tok, base);
        var w = ctx.measureText(tok.t).width;
        if (tok.space && !line.items.length) return;           // no leading space
        if (!tok.space && line.items.length && line.width + w > maxWidth) {
          while (line.items.length && line.items[line.items.length - 1].space) {
            line.width -= line.items.pop().w;
          }
          lines.push(line);
          line = { items: [], width: 0 };
        }
        line.items.push({ t: tok.t, w: w, font: ctx.font, u: tok.u || base.underline, space: tok.space });
        line.width += w;
      });
      while (line.items.length && line.items[line.items.length - 1].space) {
        line.width -= line.items.pop().w;
      }
      lines.push(line);
    });
    return lines;
  }

  function drawLines(ctx, lines, x0, width, firstBaseline, lineH, base) {
    var y = firstBaseline;
    lines.forEach(function (line) {
      var x = base.align === 'center' ? x0 + (width - line.width) / 2
            : base.align === 'right'  ? x0 + width - line.width
            : x0;
      line.items.forEach(function (it) {
        ctx.font = it.font;
        ctx.fillText(it.t, x, y);
        if (it.u) {
          var thick = Math.max(1.5, base.size * 0.06);
          ctx.fillRect(x, y + base.size * 0.14, it.w, thick);
        }
        x += it.w;
      });
      line.baseline = y;
      y += lineH;
    });
    return y;
  }

  /* Ask the browser to have the webfont ready before measuring anything.
     Measuring against a fallback and drawing with the real face is what makes
     hand-drawn canvas text overflow. */
  function waitForFont(family, weights, italic) {
    if (!document.fonts || !document.fonts.load) return Promise.resolve();
    var jobs = [];
    (weights || [400, 700]).forEach(function (w) {
      jobs.push(document.fonts.load(w + ' 64px ' + family, 'કવિતા कविता Writing'));
      if (italic) jobs.push(document.fonts.load('italic ' + w + ' 64px ' + family, 'કવિતા कविता Writing'));
    });
    return Promise.all(jobs).catch(function () { /* fallback face is fine */ });
  }

  function loadImage(src) {
    return new Promise(function (resolve, reject) {
      var img = new Image();
      // Only needed for remote images; data: URLs are same-origin already.
      if (!/^data:/i.test(src)) img.crossOrigin = 'anonymous';
      img.onload = function () { resolve(img); };
      img.onerror = function () { reject(new Error('image-load-failed')); };
      img.src = src;
    });
  }

  /* Cover-fit an image into the canvas, like CSS background-size: cover. */
  function drawCover(ctx, img, w, h) {
    var scale = Math.max(w / img.width, h / img.height);
    var dw = img.width * scale, dh = img.height * scale;
    ctx.drawImage(img, (w - dw) / 2, (h - dh) / 2, dw, dh);
  }

  function resolveImageStyle(entry) {
    var st = entry.style || {};
    var theme = data().theme;
    var mode = Theme.resolveMode(theme);
    var preset = Theme.PRESET_BY_ID[theme.preset] || Theme.PRESET_BY_ID.ink;
    var palette = preset[mode];
    var accent = Theme.safeColour(theme.accent, palette.accent);

    var custom = Theme.backgroundCSS(st);
    if (custom) {
      return {
        kind: custom.isImage ? 'image' : st.bgType,
        spec: st,
        image: Theme.safeImageUrl(st.bgImage),
        dim: Number(st.bgDim) || 0,
        color: custom.color,
        fg: Theme.safeColour(st.textColor, '') || (custom.dark ? '#FFFFFF' : '#14110D'),
        accent: accent
      };
    }
    // No background of its own: the theme's surface, as a soft gradient.
    return {
      kind: 'gradient',
      spec: { gradStops: [{ c: palette.surface, p: 0 }, { c: palette.bg, p: 100 }], gradAngle: 160 },
      fg: Theme.safeColour(st.textColor, '') || palette.text,
      accent: accent
    };
  }

  function paintGradient(ctx, spec, W, H) {
    var stops = Theme.gradientStops(spec);
    var g;
    if (spec.gradKind === 'radial') {
      // Matches CSS "circle at 50% 45%", which reaches the farthest corner.
      var cx = W * 0.5, cy = H * 0.45;
      var r = Math.sqrt(Math.pow(Math.max(cx, W - cx), 2) + Math.pow(Math.max(cy, H - cy), 2));
      g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
    } else {
      // The CSS gradient line for an angle, across the canvas.
      var rad = ((Number(spec.gradAngle) || 160) - 90) * Math.PI / 180;
      var half = (Math.abs(W * Math.cos(rad)) + Math.abs(H * Math.sin(rad))) / 2;
      g = ctx.createLinearGradient(W / 2 - Math.cos(rad) * half, H / 2 - Math.sin(rad) * half,
                                   W / 2 + Math.cos(rad) * half, H / 2 + Math.sin(rad) * half);
    }
    stops.forEach(function (s) { g.addColorStop(Math.max(0, Math.min(1, s.p / 100)), s.c); });
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);
  }

  function roundRect(ctx, x, y, w, h, r) {
    r = Math.max(0, Math.min(r, w / 2, h / 2));
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  /* Space each frame takes inside its edge, in canvas pixels. */
  function frameInsets(type, m) {
    switch (type) {
      case 'rule':     return { t: m.pad * 0.3, r: 0, b: m.pad * 0.3, l: m.pad + m.width };
      case 'double':   return { t: m.pad + m.width * 3, r: m.pad + m.width * 3, b: m.pad + m.width * 3, l: m.pad + m.width * 3 };
      case 'stamp':    return { t: m.pad + 8 * m.k, r: m.pad + 8 * m.k, b: m.pad + 8 * m.k, l: m.pad + 8 * m.k };
      case 'ornament': return { t: m.pad + 34 * m.k, r: m.pad, b: m.pad + 34 * m.k, l: m.pad };
      case 'lines':    return { t: m.pad * 0.5, r: m.pad, b: m.pad * 0.5, l: m.pad + 6 * m.k };
      case 'quotes':   return { t: m.pad + 46 * m.k, r: m.pad, b: m.pad, l: m.pad };
      case 'box': case 'card': case 'corners':
                       return { t: m.pad, r: m.pad, b: m.pad, l: m.pad };
      default:         return { t: 0, r: 0, b: 0, l: 0 };
    }
  }

  function setDash(ctx, style, m) {
    if (style === 'dashed') ctx.setLineDash([m.width * 4, m.width * 2.5]);
    else if (style === 'dotted') { ctx.setLineDash([0.1, m.width * 2.2]); ctx.lineCap = 'round'; }
    else ctx.setLineDash([]);
  }

  /* Drawn before the text: anything that sits behind the words. */
  function drawFrameUnder(ctx, type, box, m, look) {
    if (type === 'card') {
      roundRect(ctx, box.x, box.y, box.w, box.h, m.radius);
      ctx.save();
      ctx.shadowColor = 'rgba(0,0,0,.28)';
      ctx.shadowBlur = 30 * m.k;
      ctx.shadowOffsetY = 8 * m.k;
      ctx.fillStyle = look.cardFill;
      ctx.fill();
      ctx.restore();
    }
  }

  /* Drawn after the text: lines and marks on top. */
  function drawFrameOver(ctx, type, box, m, bodyLines, bodyMetrics) {
    var c = m.color;
    ctx.save();
    ctx.strokeStyle = c;
    ctx.fillStyle = c;
    ctx.lineWidth = m.width;
    var hw = m.width / 2;

    if (type === 'box' || type === 'stamp' || type === 'card') {
      setDash(ctx, m.style, m);
      if (type === 'card') ctx.globalAlpha = 0.4;
      roundRect(ctx, box.x + hw, box.y + hw, box.w - m.width, box.h - m.width, m.radius);
      ctx.stroke();
      ctx.globalAlpha = 1;
      if (type === 'stamp') {
        ctx.setLineDash([]);
        ctx.lineWidth = Math.max(1, m.k);
        var inset = m.width + 7 * m.k;
        roundRect(ctx, box.x + inset, box.y + inset, box.w - inset * 2, box.h - inset * 2, Math.max(0, m.radius - inset));
        ctx.stroke();
      }
    } else if (type === 'double') {
      ctx.lineWidth = m.width;
      roundRect(ctx, box.x + hw, box.y + hw, box.w - m.width, box.h - m.width, m.radius);
      ctx.stroke();
      var gap = m.width * 2;
      roundRect(ctx, box.x + gap + hw, box.y + gap + hw, box.w - gap * 2 - m.width, box.h - gap * 2 - m.width,
                Math.max(0, m.radius - gap));
      ctx.stroke();
    } else if (type === 'rule') {
      setDash(ctx, m.style, m);
      ctx.beginPath();
      ctx.moveTo(box.x + hw, box.y);
      ctx.lineTo(box.x + hw, box.y + box.h);
      ctx.stroke();
    } else if (type === 'corners') {
      var L = 18 * m.k + m.pad * 0.5;
      [[box.x, box.y, 1, 1], [box.x + box.w, box.y, -1, 1],
       [box.x, box.y + box.h, 1, -1], [box.x + box.w, box.y + box.h, -1, -1]].forEach(function (p) {
        ctx.fillRect(p[2] > 0 ? p[0] : p[0] - L, p[3] > 0 ? p[1] : p[1] - m.width, L, m.width);
        ctx.fillRect(p[2] > 0 ? p[0] : p[0] - m.width, p[3] > 0 ? p[1] : p[1] - L, m.width, L);
      });
    } else if (type === 'ornament') {
      [[box.y + 17 * m.k, 0], [box.y + box.h - 17 * m.k, Math.PI]].forEach(function (o) {
        var cy = o[0], cx = box.x + box.w / 2, span = box.w * 0.4;
        [-1, 1].forEach(function (dir) {
          var g = ctx.createLinearGradient(cx + dir * span, cy, cx + dir * 22 * m.k, cy);
          g.addColorStop(0, 'rgba(0,0,0,0)');
          g.addColorStop(1, c);
          ctx.fillStyle = g;
          var x1 = Math.min(cx + dir * span, cx + dir * 22 * m.k);
          ctx.fillRect(x1, cy - m.width / 2, Math.abs(span - 22 * m.k), m.width);
        });
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(o[1]);
        ctx.fillStyle = c;
        ctx.font = (24 * m.k) + 'px Georgia, "Noto Sans Symbols 2", serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('❦', 0, 0);
        ctx.restore();
      });
    } else if (type === 'lines') {
      ctx.globalAlpha = 0.45;
      ctx.lineWidth = Math.max(1, m.k);
      (bodyLines || []).forEach(function (line) {
        if (line.baseline === undefined) return;
        var y = line.baseline + bodyMetrics.size * 0.42;
        ctx.beginPath();
        ctx.moveTo(box.x + m.pad * 0.5, y);
        ctx.lineTo(box.x + box.w - m.pad * 0.5, y);
        ctx.stroke();
      });
      ctx.globalAlpha = 1;
      ctx.strokeStyle = 'rgba(229,72,77,.6)';
      ctx.lineWidth = m.width;
      ctx.beginPath();
      ctx.moveTo(box.x + hw, box.y);
      ctx.lineTo(box.x + hw, box.y + box.h);
      ctx.stroke();
    } else if (type === 'quotes') {
      ctx.globalAlpha = 0.38;
      ctx.font = (110 * m.k) + 'px Georgia, "Times New Roman", serif';
      ctx.textBaseline = 'top';
      ctx.fillText('“', box.x + m.pad * 0.4, box.y - 6 * m.k);
      ctx.textBaseline = 'alphabetic';
      ctx.globalAlpha = 1;
    }
    ctx.restore();
  }

  function familyFor(id, lang, fallbackId) {
    var chosen = id || fallbackId;
    if (chosen) Theme.ensureFont(chosen);
    return Theme.fontStack(chosen, lang);
  }

  function renderImage(entry, sizeId) {
    var size = SIZES.filter(function (s) { return s.id === sizeId; })[0] || SIZES[1];
    var W = size.w, H = size.h;
    var st = entry.style || {};
    var theme = data().theme;
    var look = resolveImageStyle(entry);

    var langKey = entry.lang === 'gu' ? 'Gu' : entry.lang === 'hi' ? 'Hi' : 'En';
    var bodyFamily = familyFor(st.font, entry.lang, theme['font' + langKey]);
    var titleFamily = familyFor(st.titleFont || st.font, entry.lang,
                                theme['head' + langKey] || theme['font' + langKey]);

    var canvas = document.createElement('canvas');
    canvas.width = W;
    canvas.height = H;
    var ctx = canvas.getContext('2d');

    var bgReady = (look.kind === 'image' && look.image)
      ? loadImage(look.image).then(function (img) {
          drawCover(ctx, img, W, H);
          ctx.fillStyle = 'rgba(0,0,0,' + look.dim + ')';
          ctx.fillRect(0, 0, W, H);
        }).catch(function () {
          ctx.fillStyle = '#1A1A1A';
          ctx.fillRect(0, 0, W, H);
        })
      : Promise.resolve().then(function () {
          if (look.kind === 'solid') {
            ctx.fillStyle = look.color || '#111111';
            ctx.fillRect(0, 0, W, H);
          } else {
            paintGradient(ctx, look.spec, W, H);
          }
        });

    var bodyWeight = Math.max(100, Math.min(900, Number(st.weight) || 400));
    var titleWeight = Number(st.titleWeight) || Number(theme.headWeight) || 620;

    return bgReady
      .then(function () {
        return Promise.all([
          waitForFont(bodyFamily, [bodyWeight, Math.min(900, bodyWeight + 300)], true),
          waitForFont(titleFamily, [titleWeight], !!st.titleItalic)
        ]);
      })
      .then(function () {
        var pad = Math.round(W * 0.09);
        var k = W / 720;                                     // page px → canvas px
        var frameType = st.frame || 'none';
        var m = {
          k: k,
          color: Theme.safeColour(st.frameColor, '') || look.accent,
          width: Math.max(1, Math.min(12, Number(st.frameWidth) || 2)) * k,
          radius: Math.max(0, Math.min(40, Number(st.frameRadius) || 0)) * k,
          pad: Math.max(0, Math.min(64, Number(st.framePad) || 0)) * k,
          style: st.frameStyle || 'solid'
        };
        var ins = frameInsets(frameType, m);

        var boxW = W - pad * 2;
        var textW = boxW - ins.l - ins.r;

        var sigSize = Math.round(W * 0.026);
        var footerRoom = sigSize * 3.2;
        var align = st.align === 'center' || st.align === 'right' ? st.align : 'left';
        var titleAlign = st.titleAlign || align;
        if (titleAlign === 'justify') titleAlign = 'left';

        /* Shrink until the whole piece fits. Poems vary enormously in length;
           a fixed size would clip a long one or strand a short one. The
           author's own size setting is the starting point. */
        var bodySize = Math.round(W * 0.048 * Math.max(0.6, Math.min(2, Number(st.size) || 1)));
        var minSize = Math.round(W * 0.017);
        var ratio = Number(st.lineHeight) || (entry.lang === 'en' ? 1.6 : 1.85);
        var titleScale = Math.max(0.5, Math.min(2.5, Number(st.titleSize) || 1));
        var bodyBase, titleBase, bodyLines, titleLines, lineH, titleLH, gap, textH;

        while (true) {
          bodyBase = { family: bodyFamily, size: bodySize, weight: bodyWeight,
                       italic: !!st.italic, underline: !!st.underline, align: align };
          titleBase = { family: titleFamily, size: Math.round(bodySize * 1.22 * titleScale),
                        weight: titleWeight, italic: !!st.titleItalic, underline: !!st.titleUnderline,
                        align: titleAlign };
          lineH = Math.round(bodySize * ratio);
          titleLH = Math.round(titleBase.size * 1.28);
          gap = entry.title ? Math.round(bodySize * 0.9) : 0;

          titleLines = entry.title ? layout(ctx, entry.title, textW, titleBase) : [];
          bodyLines = layout(ctx, String(entry.body || '').trim(), textW, bodyBase);
          textH = titleLines.length * titleLH + gap + bodyLines.length * lineH;

          var fits = textH + ins.t + ins.b <= H - pad * 2 - footerRoom;
          if (fits || bodySize <= minSize) break;
          bodySize -= 2;
        }

        var boxH = textH + ins.t + ins.b;
        var region = H - pad - footerRoom - pad;
        var boxY = Math.max(pad, pad + (region - boxH) / 2);
        var box = { x: pad, y: boxY, w: boxW, h: boxH };
        // The card sits behind the words: light words need a dark card, and
        // dark words a light one.
        var lightText = Theme.hexToRgb(look.fg) ? Theme.isLight(look.fg) : true;
        look.cardFill = lightText ? 'rgba(0,0,0,0.38)' : 'rgba(255,255,255,0.6)';

        drawFrameUnder(ctx, frameType, box, m, look);

        if (st.textShadow) {
          ctx.shadowColor = 'rgba(0,0,0,.5)';
          ctx.shadowBlur = Math.round(W * 0.02);
          ctx.shadowOffsetY = Math.round(W * 0.004);
        }
        if ('letterSpacing' in ctx && Number(st.letterSpacing)) {
          ctx.letterSpacing = (Number(st.letterSpacing) * bodySize) + 'px';
        }
        ctx.textAlign = 'left';
        ctx.textBaseline = 'alphabetic';

        var x0 = box.x + ins.l;
        var y = box.y + ins.t;
        if (titleLines.length) {
          ctx.fillStyle = Theme.safeColour(st.titleColor, '') || look.fg;
          drawLines(ctx, titleLines, x0, textW, y + titleBase.size * 0.92, titleLH, titleBase);
          y += titleLines.length * titleLH + gap;
        }
        ctx.fillStyle = look.fg;
        drawLines(ctx, bodyLines, x0, textW, y + bodySize * 0.95, lineH, bodyBase);

        ctx.shadowColor = 'transparent';
        ctx.shadowBlur = 0;
        ctx.shadowOffsetY = 0;
        if ('letterSpacing' in ctx) ctx.letterSpacing = '0px';

        drawFrameOver(ctx, frameType, box, m, bodyLines, bodyBase);

        /* Signature: a short accent rule and the author's name. */
        var site = data().site;
        var who = site.author || site.name || '';
        if (who) {
          var sigY = H - pad + Math.round(sigSize * 0.2);
          var sigX = align === 'center' ? W / 2 : align === 'right' ? W - pad : pad;
          ctx.globalAlpha = 0.85;
          var ruleW = Math.round(W * 0.07);
          ctx.fillStyle = look.accent;
          ctx.fillRect(align === 'center' ? W / 2 - ruleW / 2 : align === 'right' ? W - pad - ruleW : pad,
                       sigY - Math.round(sigSize * 1.5), ruleW, Math.max(2, Math.round(W * 0.0028)));
          ctx.fillStyle = look.fg;
          ctx.font = '600 ' + sigSize + 'px "Inter", system-ui, sans-serif';
          ctx.textAlign = align === 'center' ? 'center' : align === 'right' ? 'right' : 'left';
          ctx.fillText(who, sigX, sigY);
          ctx.textAlign = 'left';
          ctx.globalAlpha = 1;
        }

        return canvas;
      });
  }

  function canvasToBlob(canvas) {
    return new Promise(function (resolve, reject) {
      if (canvas.toBlob) {
        canvas.toBlob(function (blob) {
          blob ? resolve(blob) : reject(new Error('blob-failed'));
        }, 'image/png');
      } else {
        try {
          var parts = canvas.toDataURL('image/png').split(',');
          var bin = atob(parts[1]);
          var arr = new Uint8Array(bin.length);
          for (var i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
          resolve(new Blob([arr], { type: 'image/png' }));
        } catch (err) { reject(err); }
      }
    });
  }

  /* --------------------------------------------------------- image preview  */

  function openImageMaker(entry) {
    var chosen = 'portrait';
    var currentBlob = null;

    var preview = el('div', {
      style: {
        display: 'grid', placeItems: 'center', minHeight: '260px',
        background: 'var(--surface-2)', borderRadius: 'var(--radius-sm)',
        padding: '16px', marginBottom: '16px', overflow: 'hidden'
      }
    }, el('div', { class: 'empty-hint', text: t('state.loading') }));

    var downloadBtn = el('button', { class: 'btn btn-primary', disabled: true },
      [icon('download'), el('span', { text: t('action.download') })]);
    var shareBtn = null;

    function refresh() {
      UI.clear(preview);
      preview.appendChild(el('div', { class: 'empty-hint', text: t('state.loading') }));
      downloadBtn.disabled = true;
      if (shareBtn) shareBtn.disabled = true;

      renderImage(entry, chosen).then(function (canvas) {
        return canvasToBlob(canvas).then(function (blob) {
          currentBlob = blob;
          var url = URL.createObjectURL(blob);
          UI.clear(preview);
          preview.appendChild(el('img', {
            src: url,
            alt: '',
            style: {
              maxWidth: '100%', maxHeight: '340px', width: 'auto',
              borderRadius: '10px', boxShadow: 'var(--shadow-2)'
            },
            onload: function () { setTimeout(function () { URL.revokeObjectURL(url); }, 1000); }
          }));
          downloadBtn.disabled = false;
          if (shareBtn) shareBtn.disabled = false;
        });
      }).catch(function (err) {
        UI.clear(preview);
        preview.appendChild(el('div', { class: 'empty-hint', text: 'Could not draw the image.' }));
        console.error('[share] image', err);
      });
    }

    var sizeGrid = UI.optGrid(SIZES.map(function (s) {
      return {
        id: s.id,
        label: s.label,
        swatchHtml: '<div class="opt-swatch" style="height:34px;display:grid;place-items:center;' +
          'font-size:10px;color:var(--text-faint)">' + s.w + '×' + s.h + '</div>'
      };
    }), chosen, function (id) {
      chosen = id;
      var host = body.querySelector('.opt-grid');
      UI.$$('.opt', host).forEach(function (n, i) {
        n.classList.toggle('is-active', SIZES[i].id === id);
      });
      refresh();
    });

    downloadBtn.addEventListener('click', function () {
      if (!currentBlob) return;
      UI.downloadBlob(currentBlob, safeFilename(entry) + '.png');
      UI.toast(t('toast.exported'), 'ok');
    });

    var foot = [downloadBtn];
    if (global.navigator.canShare) {
      shareBtn = el('button', { class: 'btn', disabled: true, onclick: function () {
        if (!currentBlob) return;
        var file = new File([currentBlob], safeFilename(entry) + '.png', { type: 'image/png' });
        if (!global.navigator.canShare({ files: [file] })) {
          UI.toast('This browser cannot share images directly.', 'error');
          return;
        }
        global.navigator.share({ files: [file], title: entry.title || '' })
          .catch(function (err) {
            if (err && err.name !== 'AbortError') UI.toast('Sharing was cancelled.', 'error');
          });
      } }, [icon('share'), el('span', { text: t('share.native') })]);
      foot.unshift(shareBtn);
    }

    var body = el('div', {}, [
      preview,
      el('div', { class: 'field-label', text: 'Size' }),
      sizeGrid,
      el('div', { class: 'share-hint', text: t('share.imageHint') })
    ]);

    UI.openSheet({ title: t('share.image'), body: body, foot: foot, variant: 'center' });
    refresh();
  }

  /* ------------------------------------------------------------------ print */

  /* Builds a clean, print-only copy of the entry outside the app shell, so the
     output never depends on what happens to be open on screen. */
  function printEntry(entry) {
    var host = document.getElementById('print-root');
    if (!host) {
      host = el('div', { id: 'print-root' });
      document.body.appendChild(host);
    }
    UI.clear(host);

    var st = entry.style || {};
    var s = Views.styleFor(entry);
    var site = data().site;

    var article = el('article', { class: 'reader', lang: entry.lang });
    var inner = el('div', { class: 'reader-inner' });

    inner.appendChild(el('div', { class: 'reader-kind', text: Views.typeLabel(entry.type) }));
    if (entry.title) {
      // The heading keeps its face, weight, slant and size; colour is dropped
      // for paper along with the background.
      var title = el('h1', { class: 'reader-title', text: entry.title });
      var tcss = Views.titleStyleFor(entry).css;
      delete tcss.color;
      Views.applyStyle(title, tcss);
      inner.appendChild(title);
    }
    inner.appendChild(el('div', { class: 'reader-meta' }, [
      el('span', { text: I18n.formatDate(entry.date) }),
      site.author ? el('span', { class: 'card-dot' }) : null,
      site.author ? el('span', { text: site.author }) : null
    ]));

    var body = Rich.render(el('div', { class: 'reader-body' }), entry.body);
    // Keep the typeface, weight, slant, underline, spacing and alignment; drop
    // colours, shadows and backgrounds, which waste ink and hurt legibility.
    var bcss = Object.assign({}, s.css);
    delete bcss.color;
    delete bcss.textShadow;
    Views.applyStyle(body, bcss);
    Views.applyFrame(body, entry);
    inner.appendChild(body);

    if (entry.credit) inner.appendChild(el('div', { class: 'reader-credit', text: entry.credit }));
    article.appendChild(inner);
    host.appendChild(article);

    document.body.classList.add('is-printing');

    var cleanup = function () {
      document.body.classList.remove('is-printing');
      UI.clear(host);
      global.removeEventListener('afterprint', cleanup);
    };
    global.addEventListener('afterprint', cleanup);

    // Give the layout a frame to settle, and the webfont a moment to arrive.
    var family = '"' + (st.font || (
      entry.lang === 'gu' ? data().theme.fontGu :
      entry.lang === 'hi' ? data().theme.fontHi : data().theme.fontEn)) + '"';

    waitForFont(family, [400, 600]).then(function () {
      setTimeout(function () {
        global.print();
        // Safari on iOS never fires afterprint; clean up regardless.
        setTimeout(function () {
          if (document.body.classList.contains('is-printing')) cleanup();
        }, 1500);
      }, 120);
    });
  }

  /* ------------------------------------------------------------ share sheet */

  function openShareSheet(entry) {
    var url = entryUrl(entry);

    function opt(iconName, label, onClick) {
      return el('button', { class: 'share-opt', type: 'button', onclick: onClick },
        [icon(iconName), el('span', { text: label })]);
    }

    var options = [
      opt('image', t('share.image'), function () { openImageMaker(entry); }),
      opt('printer', t('share.pdf'), function () { UI.closeSheet(); printEntry(entry); }),
      opt('copy', t('share.text'), function () {
        UI.copyText(asText(entry))
          .then(function () { UI.toast(t('action.copied'), 'ok'); UI.closeSheet(); })
          .catch(function () { UI.toast('Could not copy.', 'error'); });
      }),
      opt('fileText', t('share.txtFile'), function () {
        UI.downloadBlob(
          new Blob([asText(entry)], { type: 'text/plain;charset=utf-8' }),
          safeFilename(entry) + '.txt'
        );
        UI.toast(t('toast.exported'), 'ok');
        UI.closeSheet();
      }),
      opt('link', t('share.link'), function () {
        UI.copyText(url)
          .then(function () { UI.toast(t('action.copied'), 'ok'); UI.closeSheet(); })
          .catch(function () { UI.toast('Could not copy.', 'error'); });
      }),
      opt('whatsapp', t('share.whatsapp'), function () {
        var text = asText(entry, { link: false }) + '\n\n' + url;
        global.open('https://wa.me/?text=' + encodeURIComponent(text), '_blank', 'noopener');
        UI.closeSheet();
      })
    ];

    if (global.navigator.share) {
      options.push(opt('share', t('share.native'), function () {
        global.navigator.share({
          title: entry.title || Views.typeLabel(entry.type),
          text: asText(entry, { signature: false, link: false }),
          url: url
        }).then(function () { UI.closeSheet(); })
          .catch(function (err) { if (err && err.name !== 'AbortError') console.warn(err); });
      }));
    }

    UI.openSheet({
      title: t('share.title'),
      variant: 'center',
      body: el('div', {}, [el('div', { class: 'share-grid' }, options)])
    });
  }

  /* ------------------------------------------------------- whole-site export */

  function exportAll() {
    var payload = Store.exportData();
    // The passphrase hash and the publish target are this device's business,
    // not something to scatter through backup files.
    delete payload.admin;
    var stamp = new Date().toISOString().slice(0, 10);
    UI.downloadBlob(
      new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }),
      'writings-backup-' + stamp + '.json'
    );
    UI.toast(t('toast.exported'), 'ok');
  }

  function exportAllText() {
    var list = Store.select({});
    var out = list.map(function (entry) {
      return [
        '── ' + (entry.title || Views.typeLabel(entry.type)) + ' ──',
        Views.typeLabel(entry.type) + ' · ' + entry.lang.toUpperCase() + ' · ' + entry.date,
        '',
        String(entry.body || '').trim(),
        entry.credit ? '\n' + entry.credit : ''
      ].join('\n');
    }).join('\n\n\n');

    var stamp = new Date().toISOString().slice(0, 10);
    UI.downloadBlob(
      new Blob([out], { type: 'text/plain;charset=utf-8' }),
      'writings-' + stamp + '.txt'
    );
    UI.toast(t('toast.exported'), 'ok');
  }

  function importFile() {
    UI.pickFile('application/json,.json', function (text) {
      var parsed;
      try {
        parsed = JSON.parse(text);
      } catch (err) {
        UI.toast('That file is not valid JSON.', 'error');
        return;
      }
      if (!parsed || typeof parsed !== 'object') {
        UI.toast('That file does not look like a backup.', 'error');
        return;
      }
      var incoming = Array.isArray(parsed.entries) ? parsed.entries.length : 0;

      UI.openSheet({
        title: 'Import ' + incoming + ' ' + (incoming === 1 ? 'piece' : 'pieces'),
        variant: 'center',
        body: el('p', {
          style: { fontSize: '14.5px', lineHeight: '1.7', color: 'var(--text-dim)' },
          text: 'Add these to what is already here, or replace everything with this file?'
        }),
        foot: [
          el('button', {
            class: 'btn',
            text: 'Replace all',
            onclick: function () {
              Store.importData(parsed, 'replace');
              UI.closeSheet();
              UI.toast(t('toast.imported'), 'ok');
              App.rerender();
            }
          }),
          el('button', {
            class: 'btn btn-primary',
            text: 'Add to existing',
            onclick: function () {
              Store.importData(parsed, 'merge');
              UI.closeSheet();
              UI.toast(t('toast.imported'), 'ok');
              App.rerender();
            }
          })
        ]
      });
    });
  }

  /* Bring in a folder of .txt files as separate pieces — the usual way years of
     writing already exist on someone's laptop. */
  function importTextFiles(onDone) {
    var input = el('input', {
      type: 'file', accept: '.txt,text/plain', multiple: true, style: { display: 'none' }
    });
    document.body.appendChild(input);
    input.addEventListener('change', function () {
      var files = Array.prototype.slice.call(input.files || []);
      document.body.removeChild(input);
      if (!files.length) return;

      Promise.all(files.map(function (file) {
        return new Promise(function (resolve) {
          var reader = new FileReader();
          reader.onload = function () {
            var raw = String(reader.result || '').replace(/\r\n/g, '\n');
            var title = file.name.replace(/\.txt$/i, '').replace(/[-_]+/g, ' ').trim();
            resolve({ title: title, body: raw.trim(), lang: detectLang(raw) });
          };
          reader.onerror = function () { resolve(null); };
          reader.readAsText(file);
        });
      })).then(function (results) {
        var added = 0;
        results.filter(Boolean).forEach(function (r) {
          if (!r.body) return;
          Store.addEntry({
            title: r.title, body: r.body, lang: r.lang,
            type: r.body.split('\n').length > 14 ? 'blog' : 'poem',
            draft: true
          });
          added++;
        });
        UI.toast(added + ' imported as drafts', 'ok');
        if (onDone) onDone();
      });
    });
    input.click();
  }

  /* Unicode blocks are unambiguous here: Gujarati is U+0A80–0AFF, Devanagari
     U+0900–097F. Whichever script dominates wins. */
  function detectLang(text) {
    var gu = (text.match(/[઀-૿]/g) || []).length;
    var hi = (text.match(/[ऀ-ॿ]/g) || []).length;
    if (gu > hi && gu > 3) return 'gu';
    if (hi > gu && hi > 3) return 'hi';
    return 'en';
  }

  global.Share = {
    SIZES: SIZES,
    asText: asText,
    entryUrl: entryUrl,
    renderImage: renderImage,
    openImageMaker: openImageMaker,
    openShareSheet: openShareSheet,
    printEntry: printEntry,
    exportAll: exportAll,
    exportAllText: exportAllText,
    importFile: importFile,
    importTextFiles: importTextFiles,
    detectLang: detectLang
  };
})(window);
