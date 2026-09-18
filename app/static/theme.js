/* Design: hell oder dunkel und Akzentfarbe. Wird im <head> geladen, damit nichts aufblitzt.
   theme: 'system' (Windows-Einstellung, Standard) | 'light' | 'dark'. ui_scale: 'auto' oder Prozent. Lässt sich das System nicht
   erkennen, bleibt es dunkel. Gespeichert wird im Browser (sofort da) und zusätzlich in den
   Einstellungen von Voicitool (app.js gleicht beides ab). */
(function () {
  const STORE = 'voicitool.design';
  const ACCENTS = {   // [hell (Rahmen, Links), kräftig (Knöpfe), Verlaufsende]
    violett: ['#6aa0ff', '#3f7cf0', '#a855f7'],
    blau: ['#60a5fa', '#2563eb', '#06b6d4'],
    gruen: ['#4ade80', '#16a34a', '#14b8a6'],
    orange: ['#fdba74', '#ea580c', '#f59e0b'],
    pink: ['#f9a8d4', '#db2777', '#a855f7'],
    rot: ['#fca5a5', '#dc2626', '#f97316'],
    tuerkis: ['#67e8f9', '#0891b2', '#10b981'],
  };
  const DEFAULTS = { theme: 'system', accent: 'violett', custom_color: '#3f7cf0', reduce_motion: false, ui_scale: 'auto' };

  function load() {
    try { return Object.assign({}, DEFAULTS, JSON.parse(localStorage.getItem(STORE) || '{}')); }
    catch (e) { return Object.assign({}, DEFAULTS); }
  }

  // ---- Farbhilfen für die eigene Akzentfarbe
  function hexToRgb(h) {
    const m = /^#?([0-9a-f]{6})$/i.exec(h || '');
    if (!m) return null;
    const n = parseInt(m[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function rgbToHex(r, g, b) { return '#' + [r, g, b].map(v => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, '0')).join(''); }
  function rgbToHsl(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2;
    if (max === min) return [0, 0, l];
    const d = max - min, s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    let h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return [h * 60, s, l];
  }
  function hslToHex(h, s, l) {
    h = ((h % 360) + 360) % 360 / 360;
    const f = t => {
      t = (t + 1) % 1;
      const q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
      if (t < 1 / 6) return p + (q - p) * 6 * t;
      if (t < 1 / 2) return q;
      if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
      return p;
    };
    return rgbToHex(f(h + 1 / 3) * 255, f(h) * 255, f(h - 1 / 3) * 255);
  }
  function customAccent(hex) {
    const rgb = hexToRgb(hex) || [63, 124, 240];
    const [h, s, l] = rgbToHsl(...rgb);
    return [hslToHex(h, Math.min(1, s), Math.min(0.78, l + 0.18)), rgbToHex(...rgb), hslToHex(h + 45, Math.min(1, s * 1.05), l)];
  }
  function luminance(hex) {
    const rgb = hexToRgb(hex); if (!rgb) return 0;
    const [r, g, b] = rgb.map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }

  // ---- System erkennen: hell nur, wenn Windows es ausdrücklich meldet, sonst dunkel
  const mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;
  const systemTheme = () => (mq && mq.matches ? 'light' : 'dark');
  const motionMq = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;

  // ---- Größe der Oberfläche: 'auto' wächst mit dem Fenster (Bezug 1320 × 800), sonst feste Prozent
  function uiZoom() {
    const v = current.ui_scale;
    if (v && v !== 'auto') return Math.max(0.7, Math.min(2, Number(v) / 100 || 1));
    return Math.max(0.9, Math.min(1.6, Math.min(innerWidth / 1320, innerHeight / 800)));
  }
  function applyZoom() { document.documentElement.style.setProperty('--z', uiZoom().toFixed(3)); }
  window.addEventListener('resize', applyZoom);

  let current = load();
  function apply(s) {
    if (s) current = Object.assign({}, current, s);
    const root = document.documentElement;
    const theme = current.theme === 'light' || current.theme === 'dark' ? current.theme : systemTheme();
    root.dataset.theme = theme;
    const acc = current.accent === 'eigene' ? customAccent(current.custom_color) : (ACCENTS[current.accent] || ACCENTS.violett);
    root.style.setProperty('--accent', theme === 'light' ? acc[1] : acc[0]);
    root.style.setProperty('--accent2', acc[1]);
    root.style.setProperty('--accent3', acc[2]);
    root.style.setProperty('--on-accent', luminance(acc[1]) > 0.55 ? '#111318' : '#ffffff');
    root.classList.toggle('reduce-motion', !!current.reduce_motion || !!(motionMq && motionMq.matches));
    applyZoom();
    try { localStorage.setItem(STORE, JSON.stringify(current)); } catch (e) { /* privates Fenster */ }
    document.dispatchEvent(new CustomEvent('vt-theme', { detail: { theme, accent: acc } }));
    return theme;
  }
  if (mq && mq.addEventListener) mq.addEventListener('change', () => { if (current.theme === 'system') apply(); });
  // zusätzlich beim Zurückkehren ins Fenster prüfen (nicht jede Umgebung meldet den Wechsel)
  window.addEventListener('focus', () => {
    if (current.theme === 'system' && document.documentElement.dataset.theme !== systemTheme()) apply();
  });

  window.VT_THEME = { apply, get: () => Object.assign({}, current), accents: ACCENTS, zoom: uiZoom,
                      resolved: () => document.documentElement.dataset.theme };
  apply();
})();
