/* Sprache der Oberfläche. Deutsch ist die Quelle im Code, voreingestellt ist die Sprache von Windows (sonst Englisch).
   Die Übersetzungen stehen in lang/strings.js (eine Zeile pro Text, eine Spalte pro Sprache).
   Übersetzt wird beim Anzeigen: alles, was in die Seite geschrieben wird, läuft durch t().
   Schlüssel mit {} sind Muster, z. B. '{} Zeilen' -> '{} lines'.
   Jeder Text merkt sich seine deutsche Quelle. Dadurch geht ein Sprachwechsel ohne Neuladen:
   Die sichtbaren Texte schreiben sich von oben nach unten in die neue Sprache um. */
(function () {
  const STORE = 'voicitool.lang';
  const LANGS = [['en', 'English'], ['de', 'Deutsch'], ['es', 'Español'], ['fr', 'Français'],
                 ['pt', 'Português'], ['it', 'Italiano']];
  const COL = { en: 1, es: 2, fr: 3, pt: 4, it: 5 };   // Spalte in VT_STRINGS
  // Weitere Sprachen: je eine Datei lang/<code>.js (VT_LANG), wird erst bei Bedarf geladen.
  // Was dort fehlt, erscheint auf Englisch.
  const EXTRA = [['ru', 'Русский'], ['pl', 'Polski'], ['tr', 'Türkçe'], ['nl', 'Nederlands'], ['uk', 'Українська'],
                 ['id', 'Bahasa Indonesia'], ['ja', '日本語'], ['zh', '中文（简体）'], ['ko', '한국어'], ['hi', 'हिन्दी'],
                 ['cs', 'Čeština'], ['sk', 'Slovenčina'], ['sr', 'Srpski'], ['sv', 'Svenska'], ['da', 'Dansk'],
                 ['ro', 'Română'], ['hu', 'Magyar'], ['el', 'Ελληνικά'], ['vi', 'Tiếng Việt'], ['th', 'ไทย']]
    .filter(([c]) => (window.VT_LANG_FILES || []).includes(c));
  LANGS.push(...EXTRA);
  window.VT_LANG = window.VT_LANG || {};
  const ATTRS = ['title', 'placeholder', 'aria-label', 'data-tip'];

  // Startsprache: eigene Wahl (vom Fenster übergeben oder gemerkt), sonst die von Windows, sonst Englisch
  const ok = l => LANGS.some(x => x[0] === l);
  const q = new URLSearchParams(location.search);
  let saved = null;
  try { saved = localStorage.getItem(STORE); } catch (e) { /* privates Fenster */ }
  const chosen = [q.get('lang'), saved].find(ok);
  const system = [q.get('sys'), ...(navigator.languages || [navigator.language])]
    .map(l => String(l || '').split('-')[0].toLowerCase()).find(ok);
  const state = { lang: chosen || system || 'en', chosen: !!chosen };
  if (chosen && chosen !== saved) try { localStorage.setItem(STORE, chosen); } catch (e) { /* egal */ }

  // Wörterbuch der gewählten Sprache; Muster mit festen Teilen zuerst prüfen (längste zuerst)
  let DICT = {}, patterns = [], WORDS = [], col = 0;
  function build(lang) {
    const extra = window.VT_LANG[lang];
    DICT = {}; patterns = []; col = COL[lang] || (extra ? 1 : 0);
    if (col) {
      for (const row of window.VT_STRINGS || []) {
        if (row[col]) DICT[row[0]] = row[col];
      }
      if (extra) Object.assign(DICT, extra.strings);
      for (const key of Object.keys(DICT)) {
        if (!key.includes('{}')) continue;
        const parts = key.split('{}');
        const rx = new RegExp('^' + parts.map(p => p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('([\\s\\S]*?)') + '$');
        patterns.push([rx, DICT[key], parts.join('').length]);
      }
      patterns.sort((a, b) => b[2] - a[2]);
    }
    WORDS = (window.VT_WORDS || {})[lang] || [];
    if (extra) {
      WORDS = [[/(\d+) Zeilen\b/g, extra.lines.replace('{}', '$1')],
               [/(\d+) Charaktere\b/g, extra.characters.replace('{}', '$1')]];
    }
  }
  // Zusatzsprache nachladen (einmal), danach wechseln
  const loading = {};
  function loadLang(l) {
    if (COL[l] || l === 'de' || window.VT_LANG[l]) return Promise.resolve();
    return loading[l] || (loading[l] = new Promise((res, rej) => {
      const sc = document.createElement('script');
      sc.src = `lang/${l}.js`;
      sc.onload = res;
      sc.onerror = () => { delete loading[l]; rej(new Error(l)); };
      document.head.appendChild(sc);
    }));
  }
  build(state.lang);

  // Übersetzung -> deutsche Quelle. Für Texte, die der Code schon übersetzt hineinschreibt.
  const REV = new Map();
  function remember(out, src) {
    if (typeof out !== 'string' || out === src) return;
    REV.delete(out);
    REV.set(out, src);
    if (REV.size > 4000) REV.delete(REV.keys().next().value);
  }
  const SRC = new WeakMap();    // Textknoten -> [Quelle, angezeigt]
  const ASRC = new WeakMap();   // Element -> { Attribut: [Quelle, angezeigt] }
  function srcOf(rec, cur) {
    if (rec && rec[1] === cur) return rec[0];   // unverändert seit der letzten Übersetzung
    const r = REV.get(cur);
    return r === undefined ? cur : r;           // sonst ist der neue Text selbst die Quelle
  }

  function lookup(raw) {
    const hit = DICT[raw];
    if (hit !== undefined) return hit;
    for (const [rx, out] of patterns) {
      const m = raw.match(rx);
      if (m) {
        let i = 1;
        return out.replace(/\{\}/g, () => t(m[i++] ?? ''));
      }
    }
    return null;
  }

  function t(s) {
    if (!col || typeof s !== 'string' || !s) return s;
    const raw = s.trim();
    if (!raw) return s;
    let out = lookup(raw);
    if (out === null) {
      // Fehler vom Server: „RuntimeError: Text“ -> Text übersetzen, Vorsatz behalten
      const err = raw.match(/^([A-Za-z]*(?:Error|Exception|Pack)): ([\s\S]+)$/);
      if (err) {
        const inner = lookup(err[2].trim());
        if (inner !== null) out = `${err[1]}: ${inner}`;
      }
    }
    if (out === null && raw.includes('\n')) {   // mehrzeilig: Zeile für Zeile
      const lines = raw.split('\n').map(x => { const y = lookup(x.trim()); return y === null ? x : y; });
      const joined = lines.join('\n');
      if (joined !== raw) out = joined;
    }
    if (out === null && /[.!?…]\s/.test(raw)) {   // mehrere Sätze: einzeln übersetzen
      const parts = raw.split(/(?<=[.!?…])\s+/).map(x => { const y = lookup(x); return y === null ? x : y; });
      const joined = parts.join(' ');
      if (joined !== raw.replace(/\s+/g, ' ')) out = joined;
    }
    if (out === null) {
      let word = raw;
      for (const [rx, rep] of WORDS) word = word.replace(rx, rep);
      if (word !== raw) out = word;
    }
    if (out === null) return s;
    const res = s.replace(raw, () => out);
    remember(res, s);
    return res;
  }

  const skip = el => !el || el.closest('[data-nolang]') || el.tagName === 'SCRIPT' || el.tagName === 'STYLE';

  function localizeText(n) {
    if (skip(n.parentElement)) return;
    const cur = n.nodeValue;
    const src = srcOf(SRC.get(n), cur);
    const out = t(src);
    SRC.set(n, [src, out]);
    if (out !== cur) n.nodeValue = out;
  }

  function localizeAttr(el, a) {
    const cur = el.getAttribute(a);
    if (!cur) return;
    let recs = ASRC.get(el);
    const src = srcOf(recs && recs[a], cur);
    const out = t(src);
    if (!recs) ASRC.set(el, recs = {});
    recs[a] = [src, out];
    if (out !== cur) el.setAttribute(a, out);
  }

  function localizeNode(node) {
    if (!col || !node) return;
    if (node.nodeType === 3) { localizeText(node); return; }
    if (node.nodeType !== 1) return;
    // Attribute (Tooltips, Platzhalter) immer übersetzen; data-nolang schützt nur den Inhalt
    for (const el of [node, ...node.querySelectorAll('*')]) {
      for (const a of ATTRS) if (el.hasAttribute(a)) localizeAttr(el, a);
    }
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    const texts = [];
    while (walker.nextNode()) texts.push(walker.currentNode);
    texts.forEach(localizeText);
  }

  function localize(root) {
    localizeNode(root || document.body);
  }

  // Alles, was neu in die Seite kommt, wird automatisch übersetzt.
  const morphing = new Map();   // Textknoten -> zuletzt selbst geschriebener Zwischenstand
  let obs = null;
  function handle(muts) {
    for (const m of muts) {
      if (m.type === 'childList') m.addedNodes.forEach(localizeNode);
      else if (m.type === 'characterData' && morphing.has(m.target)) {
        // eigener Zwischenstand der Umschreib-Animation: nicht anfassen. Hat der Code den Text
        // inzwischen selbst geändert, gilt der neue Text.
        if (m.target.nodeValue === morphing.get(m.target)) continue;
        morphing.delete(m.target);
        localizeNode(m.target);
      } else localizeNode(m.target);
    }
  }
  function start() {
    obs.observe(document.body, {
      childList: true, subtree: true, characterData: true,
      attributes: true, attributeFilter: ATTRS,
    });
  }
  function watch() {
    obs = new MutationObserver(muts => { obs.disconnect(); handle(muts); start(); });
    start();
  }

  /* ------------------------------------------------ Sprachwechsel ohne Neuladen */
  const EASE = p => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);

  // Oberkante eines sichtbaren Texts im Fenster, sonst null (dann ohne Animation tauschen)
  function visibleTop(n) {
    let el = n.parentElement;
    if (!el) return null;
    if (el.tagName === 'OPTION') {
      const sel = el.closest('select');
      if (!sel || !el.selected) return null;
      el = sel;
    }
    if (el.checkVisibility && !el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return null;
    const r = el.getBoundingClientRect();
    if (!r.width || r.bottom < 0 || r.top > innerHeight) return null;
    return r.top;
  }

  // Jeder Text schreibt sich von links nach rechts in die neue Sprache um; gleiche Anfänge und
  // Enden bleiben stehen. Die Länge wächst oder schrumpft dabei gleichmäßig mit.
  function morph(list) {
    const H = innerHeight || 1;
    for (const m of list) {
      const a = m.from, b = m.to;
      let pre = 0;
      while (pre < a.length && pre < b.length && a[pre] === b[pre]) pre++;
      let suf = 0;
      while (suf < a.length - pre && suf < b.length - pre && a[a.length - 1 - suf] === b[b.length - 1 - suf]) suf++;
      m.head = b.slice(0, pre);
      m.tail = b.slice(b.length - suf);
      m.oldMid = a.slice(pre, a.length - suf);
      m.newMid = b.slice(pre, b.length - suf);
      m.delay = Math.min(1, Math.max(0, m.top / H)) * 260;
      m.dur = Math.min(460, 220 + 10 * Math.max(m.oldMid.length, m.newMid.length));
      morphing.set(m.n, a);
    }
    const t0 = performance.now();
    function frame(now) {
      let busy = false;
      for (const m of list) {
        if (!morphing.has(m.n)) continue;   // vom Code ersetzt oder schon fertig
        const p = Math.min(1, Math.max(0, (now - t0 - m.delay) / m.dur));
        let v = m.to;
        if (p < 1) {
          const e = EASE(p);
          v = m.head + m.newMid.slice(0, Math.round(e * m.newMid.length)) +
              m.oldMid.slice(Math.round(e * m.oldMid.length)) + m.tail;
          busy = true;
        }
        if (v !== m.n.nodeValue) { morphing.set(m.n, v); m.n.nodeValue = v; }
        if (p >= 1) morphing.delete(m.n);
      }
      if (busy) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
    // Fenster verborgen oder ausgebremst: keine Bilder, dann direkt auf den Endstand springen
    setTimeout(() => {
      for (const m of list) {
        if (!morphing.has(m.n)) continue;
        morphing.set(m.n, m.to);
        m.n.nodeValue = m.to;
        morphing.delete(m.n);
      }
    }, 1400);
  }

  function setLang(l) {
    if (!LANGS.some(x => x[0] === l)) return;
    if (!COL[l] && l !== 'de' && !window.VT_LANG[l]) {
      loadLang(l).then(() => setLang(l), () => console.warn('Sprachdatei fehlt:', l));
      return;
    }
    try { localStorage.setItem(STORE, l); } catch (e) { /* egal */ }
    state.chosen = true;
    if (l === state.lang) return;
    // noch nicht verarbeitete Änderungen mit dem alten Wörterbuch abschließen
    if (obs) { const pending = obs.takeRecords(); obs.disconnect(); handle(pending); }
    // laufende Umschreibung sofort beenden
    for (const [n] of morphing) { const r = SRC.get(n); if (r) n.nodeValue = r[1]; }
    morphing.clear();

    // 1. Quellen bestimmen (mit dem Wissen der alten Sprache) und sichtbare Texte messen
    const texts = [], attrs = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const animate = !document.hidden && !document.documentElement.classList.contains('reduce-motion');
    while (walker.nextNode()) {
      const n = walker.currentNode;
      if (!n.nodeValue.trim() || skip(n.parentElement)) continue;
      texts.push({ n, src: srcOf(SRC.get(n), n.nodeValue), top: animate ? visibleTop(n) : null });
    }
    for (const el of document.body.querySelectorAll(ATTRS.map(a => `[${a}]`).join(','))) {
      for (const a of ATTRS) {
        if (el.hasAttribute(a)) attrs.push([el, a, srcOf(ASRC.get(el)?.[a], el.getAttribute(a))]);
      }
    }

    // 2. neue Sprache laden und alles übersetzen; Sichtbares wird umgeschrieben, der Rest getauscht
    REV.clear();
    state.lang = l;
    build(l);
    document.documentElement.lang = l;
    const list = [];
    for (const x of texts) {
      const cur = x.n.nodeValue, out = t(x.src);
      SRC.set(x.n, [x.src, out]);
      if (out === cur) continue;
      if (x.top !== null && cur.length + out.length < 400) list.push({ n: x.n, from: cur, to: out, top: x.top });
      else x.n.nodeValue = out;
    }
    for (const [el, a, src] of attrs) {
      const out = t(src);
      let recs = ASRC.get(el);
      if (!recs) ASRC.set(el, recs = {});
      recs[a] = [src, out];
      if (out !== el.getAttribute(a)) el.setAttribute(a, out);
    }
    if (obs) { obs.takeRecords(); start(); }
    if (list.length) morph(list);
    document.querySelectorAll('select.lang-pick').forEach(s => { s.value = l; });
    document.dispatchEvent(new CustomEvent('vt-lang', { detail: { lang: l } }));
  }

  function picker() {
    const sel = document.createElement('select');
    sel.className = 'lang-pick';
    sel.title = t('Sprache');
    sel.setAttribute('data-nolang', '');
    for (const [code, label] of LANGS) {
      const o = document.createElement('option');
      o.value = code;
      o.textContent = label;
      if (code === state.lang) o.selected = true;
      sel.appendChild(o);
    }
    sel.addEventListener('change', () => setLang(sel.value));
    return sel;
  }

  window.VT_I18N = { t, localize, setLang, remember, languages: LANGS, get lang() { return state.lang; },
                     get chosen() { return state.chosen; } };   // chosen: selbst gewählt statt Windows-Sprache
  window.t = t;

  // Dialoge laufen nicht über die Seite, deshalb hier übersetzen
  for (const fn of ['confirm', 'prompt', 'alert']) {
    const orig = window[fn].bind(window);
    window[fn] = (msg, ...rest) => orig(typeof msg === 'string' ? t(msg) : msg, ...rest);
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.documentElement.lang = state.lang;
    if (col) localize(document.body);
    watch();
    document.querySelector('.topbar')?.insertBefore(picker(), document.getElementById('jobBox'));
  });
})();
