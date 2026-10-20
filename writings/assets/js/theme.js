/* =============================================================================
   theme.js — presets, the font catalogue, and everything that turns a theme
   object into real CSS.

   Fonts are loaded lazily: a Google Fonts <link> is injected the first time a
   family is actually used. Loading all thirty up front would cost a second of
   blank text on a phone, which is exactly the wrong trade for a reading site.
   ========================================================================== */
(function (global) {
  'use strict';

  /* ---------------------------------------------------------- font catalogue */
  /* `spec` is the Google Fonts axis query. `stack` is what we fall back to
     while the webfont loads, or forever if the reader is offline. */

  var SYS_SANS  = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
  var SYS_SERIF = 'Georgia, "Times New Roman", serif';
  var GU_SYS    = '"Noto Sans Gujarati", "Shruti", "Gujarati Sangam MN", sans-serif';
  var HI_SYS    = '"Noto Sans Devanagari", "Nirmala UI", "Devanagari Sangam MN", sans-serif';

  var FONTS = [
    /* ---- Gujarati ---- */
    { id: 'Hind Vadodara',        label: 'Hind Vadodara',   script: 'gu', cat: 'sans',  spec: 'wght@300;400;500;600;700', stack: GU_SYS },
    { id: 'Mukta Vaani',          label: 'Mukta Vaani',     script: 'gu', cat: 'sans',  spec: 'wght@200;300;400;500;600;700;800', stack: GU_SYS },
    { id: 'Noto Sans Gujarati',   label: 'Noto Sans',       script: 'gu', cat: 'sans',  spec: 'wght@100..900', stack: GU_SYS },
    { id: 'Noto Serif Gujarati',  label: 'Noto Serif',      script: 'gu', cat: 'serif', spec: 'wght@100..900', stack: GU_SYS },
    { id: 'Anek Gujarati',        label: 'Anek Gujarati',   script: 'gu', cat: 'sans',  spec: 'wght@100..800', stack: GU_SYS },
    { id: 'Rasa',                 label: 'Rasa',            script: 'gu', cat: 'serif', spec: 'wght@300..700', stack: GU_SYS },
    { id: 'Tiro Gujarati',        label: 'Tiro Gujarati',   script: 'gu', cat: 'serif', spec: '',              stack: GU_SYS },
    { id: 'Baloo Bhai 2',         label: 'Baloo Bhai',      script: 'gu', cat: 'display', spec: 'wght@400..800', stack: GU_SYS },
    { id: 'Shrikhand',            label: 'Shrikhand',       script: 'gu', cat: 'display', spec: '',            stack: GU_SYS },
    { id: 'Farsan',               label: 'Farsan',          script: 'gu', cat: 'display', spec: '',            stack: GU_SYS },
    { id: 'Kumar One',            label: 'Kumar One',       script: 'gu', cat: 'display', spec: '',            stack: GU_SYS },
    { id: 'Gotu',                 label: 'Gotu',            script: 'gu', cat: 'sans',  spec: '',              stack: GU_SYS },

    /* ---- Devanagari / Hindi ---- */
    { id: 'Hind',                 label: 'Hind',            script: 'hi', cat: 'sans',  spec: 'wght@300;400;500;600;700', stack: HI_SYS },
    { id: 'Mukta',                label: 'Mukta',           script: 'hi', cat: 'sans',  spec: 'wght@200;300;400;500;600;700;800', stack: HI_SYS },
    { id: 'Noto Sans Devanagari', label: 'Noto Sans',       script: 'hi', cat: 'sans',  spec: 'wght@100..900', stack: HI_SYS },
    { id: 'Noto Serif Devanagari',label: 'Noto Serif',      script: 'hi', cat: 'serif', spec: 'wght@100..900', stack: HI_SYS },
    { id: 'Anek Devanagari',      label: 'Anek Devanagari', script: 'hi', cat: 'sans',  spec: 'wght@100..800', stack: HI_SYS },
    { id: 'Tiro Devanagari Hindi',label: 'Tiro Devanagari', script: 'hi', cat: 'serif', spec: '',              stack: HI_SYS },
    { id: 'Eczar',                label: 'Eczar',           script: 'hi', cat: 'serif', spec: 'wght@400..800', stack: HI_SYS },
    { id: 'Martel',               label: 'Martel',          script: 'hi', cat: 'serif', spec: 'wght@200;300;400;600;700;800;900', stack: HI_SYS },
    { id: 'Baloo 2',              label: 'Baloo 2',         script: 'hi', cat: 'display', spec: 'wght@400..800', stack: HI_SYS },
    { id: 'Kalam',                label: 'Kalam (hand)',    script: 'hi', cat: 'hand',  spec: 'wght@300;400;700', stack: HI_SYS },
    { id: 'Rozha One',            label: 'Rozha One',       script: 'hi', cat: 'display', spec: '',            stack: HI_SYS },
    { id: 'Yatra One',            label: 'Yatra One',       script: 'hi', cat: 'display', spec: '',            stack: HI_SYS },
    { id: 'Poppins',              label: 'Poppins',         script: 'hi', cat: 'sans',  spec: 'wght@300;400;500;600;700', stack: HI_SYS },

    /* ---- Latin / English ---- */
    { id: 'Inter',                label: 'Inter',           script: 'en', cat: 'sans',  spec: 'wght@100..900', stack: SYS_SANS },
    { id: 'Outfit',               label: 'Outfit',          script: 'en', cat: 'sans',  spec: 'wght@100..900', stack: SYS_SANS },
    { id: 'DM Sans',              label: 'DM Sans',         script: 'en', cat: 'sans',  spec: 'wght@400;500;700', stack: SYS_SANS },
    { id: 'Fraunces',             label: 'Fraunces',        script: 'en', cat: 'serif', spec: 'opsz,wght@9..144,100..900', stack: SYS_SERIF },
    { id: 'Playfair Display',     label: 'Playfair',        script: 'en', cat: 'serif', spec: 'wght@400..900', stack: SYS_SERIF },
    { id: 'Lora',                 label: 'Lora',            script: 'en', cat: 'serif', spec: 'wght@400..700', stack: SYS_SERIF },
    { id: 'Cormorant Garamond',   label: 'Cormorant',       script: 'en', cat: 'serif', spec: 'wght@300;400;500;600;700', stack: SYS_SERIF },
    { id: 'EB Garamond',          label: 'EB Garamond',     script: 'en', cat: 'serif', spec: 'wght@400..800', stack: SYS_SERIF },
    { id: 'Spectral',             label: 'Spectral',        script: 'en', cat: 'serif', spec: 'wght@200;300;400;500;600;700;800', stack: SYS_SERIF },
    { id: 'Crimson Pro',          label: 'Crimson Pro',     script: 'en', cat: 'serif', spec: 'wght@200..900', stack: SYS_SERIF },
    { id: 'Libre Baskerville',    label: 'Baskerville',     script: 'en', cat: 'serif', spec: 'wght@400;700', stack: SYS_SERIF },
    { id: 'Marcellus',            label: 'Marcellus',       script: 'en', cat: 'serif', spec: '',              stack: SYS_SERIF },
    { id: 'Caveat',               label: 'Caveat (hand)',   script: 'en', cat: 'hand',  spec: 'wght@400..700', stack: 'cursive' }
  ];

  var FONT_BY_ID = {};
  FONTS.forEach(function (f) { FONT_BY_ID[f.id] = f; });

  function fontsFor(script) {
    return FONTS.filter(function (f) { return f.script === script; });
  }

  /* --------------------------------------------------------- lazy font loading */

  var loaded = {};

  function ensureFont(id) {
    if (!id || loaded[id]) return;
    var font = FONT_BY_ID[id];
    if (!font) return;
    loaded[id] = true;

    var family = id.replace(/ /g, '+');
    var href = 'https://fonts.googleapis.com/css2?family=' + family +
      (font.spec ? ':' + font.spec : '') + '&display=swap';

    var link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = href;
    // If Google Fonts is unreachable the fallback stack simply stays in place.
    link.onerror = function () { loaded[id] = false; };
    document.head.appendChild(link);
  }

  function ensureFonts(ids) {
    (ids || []).forEach(ensureFont);
  }

  /* Build a CSS font-family value: the chosen face, then a same-script system
     fallback, so Gujarati never degrades to tofu while the webfont loads. */
  function fontStack(id, script) {
    var font = FONT_BY_ID[id];
    var tail = font ? font.stack
             : script === 'gu' ? GU_SYS
             : script === 'hi' ? HI_SYS
             : SYS_SANS;
    return (font ? '"' + font.id + '", ' : '') + tail;
  }

  /* ------------------------------------------------------------ theme presets */
  /* Every preset carries a full light and dark palette. Switching modes should
     never leave a reader with grey-on-grey. */

  var PRESETS = [
    {
      id: 'ink', label: 'Ink & Paper',
      swatch: ['#F6F1E7', '#B4763C'],
      light: { bg: '#F6F1E7', bg2: '#EDE4D4', surface: '#FFFBF3', surface2: '#F3ECDD',
               border: '#DFD3BE', text: '#2B2520', dim: '#6E6152', faint: '#9C8D79',
               accent: '#A9662B', onAccent: '#FFF9F0',
               page: 'radial-gradient(1200px 700px at 12% -8%, #FFFCF5 0%, rgba(255,252,245,0) 62%), linear-gradient(168deg, #F8F3EA 0%, #F1E9DA 55%, #EADFCB 100%)' },
      dark:  { bg: '#15120E', bg2: '#1C1813', surface: '#1E1A15', surface2: '#26211A',
               border: '#332C23', text: '#F0E7D8', dim: '#A99983', faint: '#8A7B66',
               accent: '#D9A566', onAccent: '#1A1510',
               page: 'radial-gradient(1000px 640px at 18% -10%, #241E17 0%, rgba(36,30,23,0) 60%), linear-gradient(170deg, #17130F 0%, #120F0C 100%)' }
    },
    {
      id: 'midnight', label: 'Midnight',
      swatch: ['#0B0D14', '#8B8DF5'],
      light: { bg: '#F4F5FA', bg2: '#E9EBF5', surface: '#FFFFFF', surface2: '#F2F3FA',
               border: '#DCDFEE', text: '#191B2A', dim: '#5B6078', faint: '#8B90A8',
               accent: '#5457D6', onAccent: '#FFFFFF',
               page: 'radial-gradient(1100px 640px at 80% -12%, #E4E6FA 0%, rgba(228,230,250,0) 60%), linear-gradient(172deg, #F7F8FD 0%, #EDEFF8 100%)' },
      dark:  { bg: '#0B0D14', bg2: '#11141F', surface: '#141827', surface2: '#1B2033',
               border: '#262C42', text: '#EEF0FA', dim: '#98A0BE', faint: '#6B7495',
               accent: '#8B8DF5', onAccent: '#0C0E18',
               page: 'radial-gradient(1000px 620px at 78% -14%, #1E2340 0%, rgba(30,35,64,0) 58%), linear-gradient(172deg, #0C0E17 0%, #08090F 100%)' }
    },
    {
      id: 'saffron', label: 'Saffron',
      swatch: ['#FFF3E3', '#E07B39'],
      light: { bg: '#FFF6EB', bg2: '#FBE9D5', surface: '#FFFDF9', surface2: '#FDF0E1',
               border: '#F0DCC2', text: '#31220F', dim: '#7A6144', faint: '#A88C68',
               accent: '#C9601E', onAccent: '#FFF6EC',
               page: 'radial-gradient(1000px 620px at 15% -10%, #FFE9CE 0%, rgba(255,233,206,0) 60%), linear-gradient(165deg, #FFF8EF 0%, #FCEEDC 55%, #F8E3C8 100%)' },
      dark:  { bg: '#17100A', bg2: '#1F160D', surface: '#221810', surface2: '#2C2015',
               border: '#3B2B1B', text: '#F7E9D7', dim: '#B5997B', faint: '#907553',
               accent: '#F0A85C', onAccent: '#1A1209',
               page: 'radial-gradient(980px 600px at 14% -12%, #33230F 0%, rgba(51,35,15,0) 58%), linear-gradient(168deg, #191109 0%, #110C07 100%)' }
    },
    {
      id: 'sage', label: 'Sage',
      swatch: ['#F1F5EF', '#5E8C61'],
      light: { bg: '#F2F6F0', bg2: '#E5EDE3', surface: '#FCFDFB', surface2: '#EDF3EB',
               border: '#D6E1D4', text: '#1E2A20', dim: '#586357', faint: '#8A9788',
               accent: '#4A7A51', onAccent: '#F4FAF3',
               page: 'radial-gradient(1000px 600px at 82% -10%, #E2EEDF 0%, rgba(226,238,223,0) 58%), linear-gradient(170deg, #F5F8F3 0%, #EAF1E7 100%)' },
      dark:  { bg: '#0D120E', bg2: '#131A14', surface: '#151D17', surface2: '#1C261E',
               border: '#26332A', text: '#E6EFE5', dim: '#9AAC9B', faint: '#71856F',
               accent: '#86B889', onAccent: '#0E150F',
               page: 'radial-gradient(960px 580px at 82% -12%, #1A261C 0%, rgba(26,38,28,0) 58%), linear-gradient(170deg, #0E140F 0%, #0A0F0B 100%)' }
    },
    {
      id: 'rose', label: 'Rose',
      swatch: ['#FDF1F4', '#C2607A'],
      light: { bg: '#FDF2F5', bg2: '#F8E3E9', surface: '#FFFBFC', surface2: '#FCEDF1',
               border: '#EFD5DD', text: '#2E1C22', dim: '#725560', faint: '#A8838F',
               accent: '#B24E6B', onAccent: '#FFF5F8',
               page: 'radial-gradient(1000px 620px at 20% -10%, #FBE1E9 0%, rgba(251,225,233,0) 60%), linear-gradient(168deg, #FEF6F8 0%, #F9E9EE 100%)' },
      dark:  { bg: '#16090D', bg2: '#1E0F14', surface: '#201116', surface2: '#2A171D',
               border: '#3A2029', text: '#F6E5EA', dim: '#BC94A0', faint: '#94707C',
               accent: '#E38DA5', onAccent: '#190A0F',
               page: 'radial-gradient(960px 580px at 20% -12%, #33161F 0%, rgba(51,22,31,0) 58%), linear-gradient(168deg, #180A0E 0%, #0F0709 100%)' }
    },
    {
      id: 'monsoon', label: 'Monsoon',
      swatch: ['#EEF4F6', '#2E7D8F'],
      light: { bg: '#EFF5F7', bg2: '#E0EBEF', surface: '#FBFDFE', surface2: '#E9F2F5',
               border: '#CFE0E6', text: '#152329', dim: '#4E6670', faint: '#84999F',
               accent: '#1F6F81', onAccent: '#F2FAFC',
               page: 'radial-gradient(1050px 620px at 85% -10%, #DCEDF2 0%, rgba(220,237,242,0) 58%), linear-gradient(170deg, #F4F9FB 0%, #E7F1F4 100%)' },
      dark:  { bg: '#081014', bg2: '#0D181D', surface: '#0F1B21', surface2: '#15242B',
               border: '#1E323A', text: '#E2EFF3', dim: '#92AFB8', faint: '#688891',
               accent: '#56B3C7', onAccent: '#071216',
               page: 'radial-gradient(1000px 600px at 85% -12%, #10242C 0%, rgba(16,36,44,0) 58%), linear-gradient(172deg, #091216 0%, #060C0F 100%)' }
    },
    {
      id: 'noir', label: 'Noir & Gold',
      swatch: ['#0A0A0A', '#D4AF37'],
      light: { bg: '#F5F3EF', bg2: '#E9E5DC', surface: '#FFFFFF', surface2: '#F1EEE7',
               border: '#DED8CA', text: '#1A1814', dim: '#5E594E', faint: '#908A7C',
               accent: '#8A6D2F', onAccent: '#FFFDF7',
               page: 'linear-gradient(170deg, #F7F5F1 0%, #EDE9E1 100%)' },
      dark:  { bg: '#09090A', bg2: '#0F0F11', surface: '#131315', surface2: '#1A1A1D',
               border: '#26262A', text: '#F2EFE7', dim: '#A09B90', faint: '#6F6B62',
               accent: '#D4AF37', onAccent: '#100E08',
               page: 'radial-gradient(900px 560px at 50% -14%, #1B1A16 0%, rgba(27,26,22,0) 60%), linear-gradient(175deg, #0A0A0B 0%, #070707 100%)' }
    },
    {
      id: 'sunset', label: 'Sunset',
      swatch: ['#FFF2EC', '#E2574C'],
      light: { bg: '#FFF4EF', bg2: '#FBE4DC', surface: '#FFFCFA', surface2: '#FDEDE6',
               border: '#F2D8CD', text: '#301C18', dim: '#7A5B53', faint: '#AC8980',
               accent: '#D0463C', onAccent: '#FFF6F3',
               page: 'radial-gradient(900px 560px at 10% -8%, #FFE0D2 0%, rgba(255,224,210,0) 55%), radial-gradient(800px 520px at 92% 8%, #FBDCEC 0%, rgba(251,220,236,0) 55%), linear-gradient(165deg, #FFF7F3 0%, #FCEAE2 100%)' },
      dark:  { bg: '#150A10', bg2: '#1D0F16', surface: '#1F1118', surface2: '#29171F',
               border: '#39212B', text: '#F8E6E0', dim: '#BE9790', faint: '#96736D',
               accent: '#FF8A6B', onAccent: '#1A0C09',
               page: 'radial-gradient(900px 560px at 8% -10%, #3A1A1C 0%, rgba(58,26,28,0) 56%), radial-gradient(820px 520px at 92% 6%, #2A1430 0%, rgba(42,20,48,0) 56%), linear-gradient(168deg, #160A0F 0%, #0D0609 100%)' }
    }
  ];

  var PRESET_BY_ID = {};
  PRESETS.forEach(function (p) { PRESET_BY_ID[p.id] = p; });

  /* Frames drawn around a piece. Each has a CSS treatment (app.css, .frame-*)
     and a canvas one (share.js) so the shared image matches the page. */
  var FRAMES = [
    { id: 'none',     label: 'None' },
    { id: 'rule',     label: 'Side line' },
    { id: 'box',      label: 'Box' },
    { id: 'double',   label: 'Double' },
    { id: 'corners',  label: 'Corners' },
    { id: 'card',     label: 'Card' },
    { id: 'ornament', label: 'Ornament' },
    { id: 'lines',    label: 'Notebook' },
    { id: 'quotes',   label: 'Quote mark' },
    { id: 'stamp',    label: 'Stamp' }
  ];

  /* Gradients worth one tap. Three and four colours, because the two-colour
     ones are easy to make by hand and these are not. */
  var GRADIENTS = [
    { id: 'dusk',     label: 'Dusk',     stops: ['#0F2027', '#203A43', '#2C5364'] },
    { id: 'saffron',  label: 'Saffron',  stops: ['#FF9933', '#FFB347', '#FFCC70'] },
    { id: 'peacock',  label: 'Peacock',  stops: ['#003B46', '#07575B', '#66A5AD', '#C4DFE6'] },
    { id: 'sunset',   label: 'Sunset',   stops: ['#FF5F6D', '#FFC371', '#FFE29F'] },
    { id: 'monsoon',  label: 'Monsoon',  stops: ['#2C3E50', '#3E5F7B', '#4CA1AF', '#C4E0E5'] },
    { id: 'rose',     label: 'Rose',     stops: ['#4B1248', '#B24592', '#F15F79', '#F0C27B'] },
    { id: 'forest',   label: 'Forest',   stops: ['#0B3D2E', '#134E5E', '#71B280'] },
    { id: 'aurora',   label: 'Aurora',   stops: ['#0B0C2A', '#2B2E83', '#3BB78F', '#A8E063'] },
    { id: 'diya',     label: 'Diya',     stops: ['#1F1300', '#7A3E00', '#F09819', '#FFD86F'] },
    { id: 'lavender', label: 'Lavender', stops: ['#E0C3FC', '#C3B1E1', '#8EC5FC'] },
    { id: 'ink',      label: 'Ink',      stops: ['#232526', '#414345', '#232526'] },
    { id: 'rainbow',  label: 'Holi',     stops: ['#FF0080', '#FF8C00', '#FFD700', '#00C9A7', '#845EC2'] }
  ];

  var CARD_STYLES = [
    { id: 'paper',   label: 'Paper' },
    { id: 'glass',   label: 'Glass' },
    { id: 'solid',   label: 'Solid' },
    { id: 'outline', label: 'Outline' },
    { id: 'minimal', label: 'Minimal' }
  ];

  var LAYOUTS = [
    { id: 'grid',     label: 'Grid' },
    { id: 'masonry',  label: 'Masonry' },
    { id: 'list',     label: 'List' },
    { id: 'magazine', label: 'Magazine' }
  ];

  /* ------------------------------------------------------------ mode resolution */

  function prefersDark() {
    return global.matchMedia && global.matchMedia('(prefers-color-scheme: dark)').matches;
  }

  /* A reader's own light/dark choice beats the site default — their eyes, their
     call. `theme.mode` is only the starting point. */
  function resolveMode(theme) {
    var override = (global.Store && Store.prefs().mode) || '';
    var mode = override || (theme && theme.mode) || 'auto';
    if (mode === 'auto') return prefersDark() ? 'dark' : 'light';
    return mode === 'dark' ? 'dark' : 'light';
  }

  /* ------------------------------------------------------- background building */

  function hexToRgb(hex) {
    var h = String(hex || '').replace('#', '');
    if (h.length === 3) h = h.split('').map(function (c) { return c + c; }).join('');
    if (h.length !== 6 || /[^0-9a-f]/i.test(h)) return null;
    return {
      r: parseInt(h.slice(0, 2), 16),
      g: parseInt(h.slice(2, 4), 16),
      b: parseInt(h.slice(4, 6), 16)
    };
  }

  function rgba(hex, alpha) {
    var c = hexToRgb(hex);
    if (!c) return 'rgba(0,0,0,' + alpha + ')';
    return 'rgba(' + c.r + ',' + c.g + ',' + c.b + ',' + alpha + ')';
  }

  /* Blend two hex colours. Used to ask what a gradient looks like overall. */
  function mixHex(a, b, ratio) {
    var ca = hexToRgb(a), cb = hexToRgb(b);
    if (!ca || !cb) return a;
    var t = Math.max(0, Math.min(1, ratio));
    var to2 = function (v) { return Math.round(v).toString(16).padStart(2, '0'); };
    return '#' + to2(ca.r + (cb.r - ca.r) * t) +
                 to2(ca.g + (cb.g - ca.g) * t) +
                 to2(ca.b + (cb.b - ca.b) * t);
  }

  /* Is this colour light enough that dark text sits on it comfortably?
     Uses relative luminance, so it is right for saturated colours too. */
  function isLight(hex) {
    var c = hexToRgb(hex);
    if (!c) return true;
    var srgb = [c.r, c.g, c.b].map(function (v) {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    var lum = 0.2126 * srgb[0] + 0.7152 * srgb[1] + 0.0722 * srgb[2];
    return lum > 0.45;
  }

  /* Colours and image URLs are stored in content.json, which can arrive from an
     imported backup file. These two guards keep a hostile value from turning
     into a CSS construct of its own — a colour smuggling `), url(...)` into a
     gradient, say, which would make the page fetch something the author never
     asked for. Both are assigned through CSSOM, so this is hygiene rather than
     an XSS hole, but a bad value should fail closed rather than sideways. */

  var COLOUR_RE = /^(#[0-9a-f]{3,8}|rgba?\([\d.,%\s/]+\)|hsla?\([\d.,%\sdegrad/]+\)|[a-z]{3,20})$/i;

  /* An invalid or empty value returns the fallback exactly as given — an empty
     string included, which callers use to mean "nothing chosen". Only with no
     fallback at all does it return 'transparent'. (Collapsing an empty
     fallback to 'transparent' once painted every unstyled poem invisible.) */
  function safeColour(value, fallback) {
    var v = String(value == null ? '' : value).trim();
    if (COLOUR_RE.test(v)) return v;
    return fallback === undefined ? 'transparent' : fallback;
  }

  /* Only images may become a background: an https URL, an inline data: image,
     or a path inside this site. Anything else yields '' and the caller falls
     back to a plain colour. */
  function safeImageUrl(value) {
    var v = String(value == null ? '' : value).trim();
    if (!v) return '';
    if (/[\s"'()\\]/.test(v)) return '';          // nothing that can close url()
    if (/^data:image\/(png|jpe?g|gif|webp|avif);base64,[A-Za-z0-9+/=]+$/i.test(v)) return v;
    if (/^https:\/\/[^\s]+$/i.test(v)) return v;
    if (/^\.{0,2}\/[^\s]*$/.test(v) && v.indexOf('//') !== 0) return v;
    return '';
  }

  /* A gradient's colours as [{ c, p }], whichever way it was stored: the
     multi-stop list, or the older two-colour from/to pair. */
  function gradientStops(spec) {
    var stops = Array.isArray(spec.gradStops) ? spec.gradStops : [];
    var clean = stops
      .map(function (st) { return { c: safeColour(st && st.c, ''), p: Number(st && st.p) }; })
      .filter(function (st) { return st.c && st.c !== 'transparent' && isFinite(st.p); });
    if (clean.length >= 2) {
      return clean.slice().sort(function (a, b) { return a.p - b.p; });
    }
    return [
      { c: safeColour(spec.gradFrom, '#222222'), p: 0 },
      { c: safeColour(spec.gradTo, '#000000'), p: 100 }
    ];
  }

  /* Evenly spread stops for a list of colours. */
  function spreadStops(colours) {
    var n = colours.length;
    return colours.map(function (c, i) {
      return { c: c, p: n === 1 ? 0 : Math.round((i / (n - 1)) * 100) };
    });
  }

  function gradientCSS(spec) {
    var stops = gradientStops(spec);
    var list = stops.map(function (st) { return st.c + ' ' + st.p + '%'; }).join(', ');
    if (spec.gradKind === 'radial') {
      return 'radial-gradient(circle at 50% 45%, ' + list + ')';
    }
    var ang = Number(spec.gradAngle);
    if (!isFinite(ang)) ang = 160;
    return 'linear-gradient(' + ang + 'deg, ' + list + ')';
  }

  /* Is the gradient, taken as a whole, dark? Average every colour weighted by
     how much of the area it covers, then ask the usual luminance question. */
  function gradientIsDark(spec) {
    var stops = gradientStops(spec);
    var r = 0, g = 0, b = 0, total = 0;
    for (var i = 0; i < stops.length; i++) {
      var c = hexToRgb(stops[i].c);
      if (!c) continue;
      var left = i > 0 ? (stops[i].p - stops[i - 1].p) / 2 : stops[i].p;
      var right = i < stops.length - 1 ? (stops[i + 1].p - stops[i].p) / 2 : 100 - stops[i].p;
      var w = Math.max(1, left + right);
      r += c.r * w; g += c.g * w; b += c.b * w; total += w;
    }
    if (!total) return true;
    var hex = '#' + [r, g, b].map(function (v) {
      return Math.round(v / total).toString(16).padStart(2, '0');
    }).join('');
    return !isLight(hex);
  }

  /* Turn a {bgType, ...} spec into CSS. Returns null when the spec says
     "inherit", so callers know to leave the surface alone. */
  function backgroundCSS(spec) {
    if (!spec) return null;
    var type = spec.bgType;
    if (!type || type === 'inherit' || type === 'preset') return null;

    if (type === 'solid') {
      var solid = safeColour(spec.bgColor, '#000000');
      return { image: 'none', color: solid, dark: !isLight(solid) };
    }
    if (type === 'gradient') {
      var stops = gradientStops(spec);
      return {
        image: gradientCSS(spec),
        color: stops[0].c,
        // Judge the blend, not the ends. A gradient running dark-to-light is
        // light at one end and dark at the other; asking about an end calls
        // it wrong and lands black text on a near-black corner.
        dark: gradientIsDark(spec)
      };
    }
    if (type === 'image') {
      var src = safeImageUrl(spec.bgImage);
      if (!src) return null;
      var dim = Number(spec.bgDim);
      if (!isFinite(dim)) dim = 0.35;
      dim = Math.max(0, Math.min(1, dim));
      var veil = 'linear-gradient(rgba(0,0,0,' + dim + '), rgba(0,0,0,' + dim + '))';
      return {
        image: veil + ', url("' + src + '")',
        color: '#111111',
        blur: Math.max(0, Math.min(40, Number(spec.bgBlur) || 0)),
        dark: true,
        isImage: true
      };
    }
    return null;
  }

  /* ----------------------------------------------------------- apply to :root */

  function applyTheme(theme) {
    theme = theme || {};
    var preset = PRESET_BY_ID[theme.preset] || PRESET_BY_ID.ink;
    var mode = resolveMode(theme);
    var p = preset[mode];
    var root = document.documentElement;

    var accent = safeColour(theme.accent, p.accent);
    var onAccent = isLight(accent) ? '#14110D' : '#FFFFFF';

    var vars = {
      '--bg': p.bg,
      '--bg-2': p.bg2,
      '--surface': p.surface,
      '--surface-2': p.surface2,
      '--border': p.border,
      '--text': p.text,
      '--text-dim': p.dim,
      '--text-faint': p.faint,
      '--accent': accent,
      '--accent-soft': rgba(accent, mode === 'dark' ? 0.18 : 0.12),
      '--accent-line': rgba(accent, mode === 'dark' ? 0.4 : 0.3),
      '--on-accent': onAccent,
      '--shadow-1': mode === 'dark'
        ? '0 1px 2px rgba(0,0,0,.5), 0 8px 26px rgba(0,0,0,.42)'
        : '0 1px 2px rgba(60,45,25,.06), 0 10px 30px rgba(60,45,25,.09)',
      '--shadow-2': mode === 'dark'
        ? '0 3px 8px rgba(0,0,0,.55), 0 22px 60px rgba(0,0,0,.55)'
        : '0 4px 12px rgba(60,45,25,.10), 0 26px 64px rgba(60,45,25,.14)',
      '--radius': (Number(theme.radius) || 18) + 'px',
      '--radius-sm': Math.max(6, (Number(theme.radius) || 18) * 0.55) + 'px',
      '--base-size': (Number(theme.baseSize) || 17) + 'px',
      '--ff-ui': fontStack(theme.fontUi || 'Inter', 'en'),
      '--ff-en': fontStack(theme.fontEn || 'Fraunces', 'en'),
      '--ff-gu': fontStack(theme.fontGu || 'Hind Vadodara', 'gu'),
      '--ff-hi': fontStack(theme.fontHi || 'Hind', 'hi'),
      // Headings get their own faces; empty means "same as the body".
      '--fh-en': fontStack(theme.headEn || theme.fontEn || 'Fraunces', 'en'),
      '--fh-gu': fontStack(theme.headGu || theme.fontGu || 'Hind Vadodara', 'gu'),
      '--fh-hi': fontStack(theme.headHi || theme.fontHi || 'Hind', 'hi'),
      '--head-weight': String(Number(theme.headWeight) || 620)
    };

    Object.keys(vars).forEach(function (k) { root.style.setProperty(k, vars[k]); });

    root.setAttribute('data-mode', mode);
    root.setAttribute('data-preset', preset.id);
    root.setAttribute('data-card', theme.cardStyle || 'paper');
    root.setAttribute('data-density', theme.density || 'comfortable');

    // The browser chrome (address bar, status bar) should match the page.
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', p.bg);

    applyPageBackground(theme, p);
    ensureFonts([theme.fontUi, theme.fontEn, theme.fontGu, theme.fontHi,
                 theme.headEn, theme.headGu, theme.headHi]);
    return mode;
  }

  /* The page background lives on its own fixed layer so that an image can be
     blurred without dragging the text along with it. */
  function applyPageBackground(theme, palette) {
    var layer = document.getElementById('page-bg');
    if (!layer) return;
    var custom = backgroundCSS(theme);

    if (!custom) {
      layer.style.backgroundImage = palette.page;
      layer.style.backgroundColor = palette.bg;
      layer.style.filter = '';
      layer.style.backgroundSize = 'cover';
      return;
    }
    layer.style.backgroundImage = custom.image;
    layer.style.backgroundColor = custom.color;
    layer.style.backgroundSize = 'cover';
    layer.style.backgroundPosition = 'center';
    layer.style.filter = custom.blur ? 'blur(' + custom.blur + 'px)' : '';
    // A blurred layer shrinks at the edges; scale it up so no gap shows.
    layer.style.transform = custom.blur ? 'scale(1.06)' : '';
  }

  /* React to the OS flipping between light and dark while the page is open. */
  function watchSystemMode(onChange) {
    if (!global.matchMedia) return;
    var mq = global.matchMedia('(prefers-color-scheme: dark)');
    var handler = function () { onChange(); };
    if (mq.addEventListener) mq.addEventListener('change', handler);
    else if (mq.addListener) mq.addListener(handler);
  }

  global.Theme = {
    FONTS: FONTS,
    FONT_BY_ID: FONT_BY_ID,
    PRESETS: PRESETS,
    PRESET_BY_ID: PRESET_BY_ID,
    CARD_STYLES: CARD_STYLES,
    FRAMES: FRAMES,
    GRADIENTS: GRADIENTS,
    gradientStops: gradientStops,
    gradientCSS: gradientCSS,
    gradientIsDark: gradientIsDark,
    spreadStops: spreadStops,
    LAYOUTS: LAYOUTS,
    fontsFor: fontsFor,
    ensureFont: ensureFont,
    ensureFonts: ensureFonts,
    fontStack: fontStack,
    applyTheme: applyTheme,
    backgroundCSS: backgroundCSS,
    resolveMode: resolveMode,
    watchSystemMode: watchSystemMode,
    isLight: isLight,
    mixHex: mixHex,
    safeColour: safeColour,
    safeImageUrl: safeImageUrl,
    rgba: rgba,
    hexToRgb: hexToRgb
  };
})(window);
