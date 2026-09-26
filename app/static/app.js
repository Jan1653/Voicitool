'use strict';
/* Voicitool: Editor-Frontend */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const S = {
  pid: null, p: null, words: [], peaks: null,
  sel: null, undo: [], redo: [], saveTimer: null,
  audioMode: 'orig', playUntil: null,
  pxPerSec: 60, viewStart: 0, drag: null,
  lastActive: new Set(), clip: null, tlH: 0,
};

/* ------------------------------------------------------------ Hilfen */
function fmt(t, ms = true) {
  if (!isFinite(t)) t = 0;
  const m = Math.floor(t / 60), s = t - m * 60;
  return ms ? `${m}:${s.toFixed(2).padStart(5, '0')}` : `${m}:${Math.floor(s).toString().padStart(2, '0')}`;
}
async function api(method, url, body, timeoutMs = 60000) {
  const opt = { method, headers: {} };
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  opt.signal = ctl.signal;
  let r;
  try { r = await fetch(url, opt); }
  catch (e) {
    throw new Error(e.name === 'AbortError' ? tf('Keine Antwort von Voicitool (Zeitüberschreitung).') : tf('Voicitool ist gerade nicht erreichbar.'));
  } finally { clearTimeout(timer); }
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = j.detail || msg; } catch { /* leer */ }
    const err = new Error(msg);
    err.status = r.status;
    throw err;
  }
  return r.json();
}
let toastTimer;
function toast(msg, error = false, ms = 3500) {
  const el = $('#toast');
  el.textContent = msg; el.classList.toggle('error', error); el.hidden = false;
  if (error) {   // Fehler lassen sich kopieren und so direkt weiterschicken
    const b = document.createElement('button');
    b.className = 'copy-btn'; b.innerHTML = ic('copy') + '<span>Kopieren</span>'; b.title = 'Fehlermeldung kopieren';
    b.onclick = () => copyError(msg);
    el.appendChild(b);
    ms = Math.max(ms, 9000);
  }
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (el.hidden = true), ms);
}
{
  const el = $('#toast');   // solange die Maus darauf ist, nicht ausblenden
  el.addEventListener('mouseenter', () => clearTimeout(toastTimer));
  el.addEventListener('mouseleave', () => { toastTimer = setTimeout(() => (el.hidden = true), 2500); });
}

/** Fehlertext samt Version in die Zwischenablage (zum Weiterschicken). */
async function copyError(text) {
  const info = `Voicitool ${lastState?.version || ''} (Build ${lastState?.build ?? '?'})\n${text}`;
  try { await navigator.clipboard.writeText(info); }
  catch {
    const ta = document.createElement('textarea');
    ta.value = info; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
  }
  const el = $('#toast');
  el.textContent = 'Fehlermeldung kopiert.'; el.classList.remove('error'); el.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (el.hidden = true), 2000);
}

/** Text mit Platzhaltern {} übersetzen und füllen. */
/* ------------------------------------------------------------ Dialoge
   Eigene Fenster statt confirm()/prompt(): passen zu Design und Sprache.
   dialog({ title, text, html, icon, tone: 'danger'|'warn', input, placeholder, required, hint,
            list: [{ level: 'block'|'warn'|'info', text, sub }], check: { label, checked },
            buttons: [{ label, value, kind, side: 'left', main }], ok, cancel, wide })
   Ergebnis: Wert des Knopfs (OK = true, mit Eingabefeld der Text), Abbrechen/Esc = null.
   Mit check: { value, checked }. */
const dlgStack = [];
function dialog(o = {}) {
  return new Promise(resolve => {
    const prevFocus = document.activeElement;
    const ov = document.createElement('div');
    ov.className = 'dlg-overlay';
    const box = document.createElement('div');
    box.className = `dlg ${o.tone || ''} ${o.wide ? 'wide' : ''}`;
    box.setAttribute('role', o.tone ? 'alertdialog' : 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.innerHTML = '<div class="dlg-head"><div class="dlg-icon" aria-hidden="true"></div><div class="dlg-titles"><h2></h2><p class="dlg-text"></p></div></div>';
    $('.dlg-icon', box).innerHTML = ic(o.icon || { danger: 'trash', warn: 'warning' }[o.tone] || (o.input !== undefined ? 'pencil' : 'help'));
    $('h2', box).textContent = o.title || '';
    const para = $('.dlg-text', box);
    if (o.html) para.innerHTML = o.html; else if (o.text) para.textContent = o.text; else para.remove();
    if (o.raw) para.setAttribute('data-nolang', '');   // Nutzertext (z. B. Zeilentext) nicht übersetzen

    let input = null, hint = null;
    if (o.input !== undefined) {
      input = document.createElement('input');
      input.className = 'dlg-input';
      input.value = o.input ?? '';
      input.placeholder = o.placeholder || '';
      input.maxLength = o.maxLength || 200;
      input.spellcheck = false;
      if (o.readonly) { input.readOnly = true; input.classList.add('readonly'); }
      hint = document.createElement('p');
      hint.className = 'dlg-hint';
      hint.textContent = o.hint || '';
      hint.hidden = !o.hint;
      box.append(input, hint);
    }
    if (o.list?.length) {
      const ul = document.createElement('ul');
      ul.className = 'dlg-list';
      for (const it of o.list) {
        const li = document.createElement('li');
        li.className = it.level || '';
        li.innerHTML = '<span class="li-icon"></span><span><span class="li-text"></span><span class="li-sub"></span></span>';
        $('.li-icon', li).innerHTML = ic(LEVEL_ICON[it.level] || 'info');
        $('.li-text', li).textContent = it.text;
        if (it.sub) $('.li-sub', li).textContent = it.sub; else $('.li-sub', li).remove();
        ul.appendChild(li);
      }
      box.appendChild(ul);
    }
    let check = null;
    if (o.check) {
      const lab = document.createElement('label');
      lab.className = 'dlg-check';
      lab.innerHTML = '<input type="checkbox"><span></span>';
      check = $('input', lab);
      check.checked = !!o.check.checked;
      $('span', lab).textContent = o.check.label;
      box.appendChild(lab);
    }

    const acts = document.createElement('div');
    acts.className = 'dlg-actions';
    const buttons = o.buttons || [
      ...(o.cancel === false ? [] : [{ label: o.cancel || 'Abbrechen', value: null }]),
      { label: o.ok || 'OK', value: true, kind: o.tone === 'danger' ? 'danger-fill' : 'primary', main: true },
    ];
    let mainBtn = null;
    const mk = b => {
      const el = document.createElement('button');
      el.type = 'button';
      el.className = `btn ${b.kind || ''}`;
      el.textContent = b.label;
      el.onclick = () => finish(b.value);
      if (b.main) mainBtn = el;
      return el;
    };
    const left = buttons.filter(b => b.side === 'left');
    left.forEach(b => acts.appendChild(mk(b)));
    if (left.length) acts.appendChild(Object.assign(document.createElement('span'), { className: 'spacer' }));
    buttons.filter(b => b.side !== 'left').forEach(b => acts.appendChild(mk(b)));
    box.appendChild(acts);
    ov.appendChild(box);

    const valid = () => !input || !o.required || input.value.trim() !== '';
    const refresh = () => {
      if (!input) return;
      const ok = valid();
      if (mainBtn) mainBtn.disabled = !ok;
      input.classList.toggle('bad', !ok);
      if (o.required) {
        hint.hidden = ok && !o.hint;
        hint.classList.toggle('bad', !ok);
        hint.textContent = ok ? (o.hint || '') : tf('Der Name darf nicht leer sein.');
      }
    };
    input?.addEventListener('input', refresh);

    function finish(value) {
      if (input && value === true) {
        if (!valid()) { refresh(); input.focus(); return; }
        value = input.value.trim();
      }
      window.removeEventListener('keydown', onKey, true);
      dlgStack.splice(dlgStack.indexOf(ov), 1);
      ov.remove();
      try { prevFocus?.focus?.({ preventScroll: true }); } catch { /* egal */ }
      resolve(o.check ? { value, checked: check.checked } : value);
    }
    function onKey(e) {
      if (dlgStack[dlgStack.length - 1] !== ov) return;   // nur das oberste Fenster reagiert
      e.stopPropagation();                                 // Editor-Kürzel ruhen, solange ein Dialog offen ist
      if (e.key === 'Escape') { e.preventDefault(); finish(null); return; }
      if (e.key === 'Enter' && !(e.target.tagName === 'BUTTON' && box.contains(e.target))) {
        e.preventDefault();
        if (mainBtn && !mainBtn.disabled) mainBtn.click();
        return;
      }
      if (e.key === 'Tab') {
        const f = [...box.querySelectorAll('button:not([disabled]), input')];
        const i = f.indexOf(document.activeElement);
        e.preventDefault();
        f[(i + (e.shiftKey ? -1 : 1) + f.length) % f.length]?.focus();
        return;
      }
      if (!box.contains(e.target)) e.preventDefault();
    }
    window.addEventListener('keydown', onKey, true);
    ov.addEventListener('mousedown', e => { if (e.target === ov) finish(null); });
    dlgStack.push(ov);
    document.body.appendChild(ov);
    requestAnimationFrame(() => {
      if (input) { input.focus(); input.select(); } else (mainBtn || acts.querySelector('button'))?.focus();
    });
  });
}

const tf = (tpl, ...args) => {
  let i = 0, j = 0;
  const out = (window.t ? window.t(tpl) : tpl).replace(/\{\}/g, () => args[i++] ?? '');
  // Quelle merken, damit ein Sprachwechsel ohne Neuladen auch fertig eingesetzte Texte umschreiben kann
  if (args.length) window.VT_I18N?.remember(out, tpl.replace(/\{\}/g, () => args[j++] ?? ''));
  return out;
};

/** Dauer in Worten: „wenige Sekunden“, „ca. 4 min“, „ca. 1 h 20 min“. */
function fmtDuration(s) {
  if (s < 45) return tf('wenige Sekunden');
  if (s < 3600) return tf('ca. {} min', Math.max(1, Math.round(s / 60)));
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return m ? tf('ca. {} h {} min', h, m) : tf('ca. {} h', h);
}
const mediaUrl = name => `/api/projects/${encodeURIComponent(S.pid)}/media/${encodeURIComponent(name)}`;
const imgUrl = name => `/api/projects/${encodeURIComponent(S.pid)}/bilder/${encodeURIComponent(name)}`;
const newId = () => 'l' + Math.random().toString(36).slice(2, 12);
const charById = id => S.p.characters.find(c => c.id === id);
const lineById = id => S.p.lines.find(l => l.id === id);
const sortLines = () => S.p.lines.sort((a, b) => a.start - b.start);
const masterOf = l => (l.repeat_of ? lineById(l.repeat_of) : null);
const repeatsOf = id => S.p.lines.filter(l => l.repeat_of === id);
const lineNo = l => S.p.lines.indexOf(l) + 1;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

/* ------------------------------------------------------------ Bewegung
   Animationen für Ansichtswechsel. Alles aus bei „Bewegungen reduzieren“ (Einstellung oder Windows):
   theme.js setzt dann die Klasse reduce-motion, CSS-Animationen sind darüber schon abgeschaltet. */
const motionOK = () => !document.documentElement.classList.contains('reduce-motion');
const EASE_OUT = 'cubic-bezier(.2, .85, .25, 1)';
const EASE_POP = 'cubic-bezier(.22, 1.12, .36, 1)';   // leichtes Nachfedern wie bei macOS

/** Ansichtswechsel über View Transitions. type wählt die Animation im CSS (html[data-vt="…"]),
    vars sind CSS-Variablen dafür (z. B. Startrechteck). Ohne Unterstützung: sofort umschalten. */
let vtRunning = null;
function viewTransition(type, update, vars = {}) {
  if (!document.startViewTransition || !motionOK()) { update(); return Promise.resolve(); }
  vtRunning?.skipTransition();
  const root = document.documentElement;
  root.dataset.vt = type;
  for (const [k, v] of Object.entries(vars)) root.style.setProperty(k, v);
  const t = document.startViewTransition(update);
  vtRunning = t;
  t.ready.catch(() => {});   // abgebrochen (z. B. Fenster verborgen): kein Fehler, die Änderung gilt trotzdem
  const cleanup = () => {
    if (vtRunning !== t) return;
    vtRunning = null;
    delete root.dataset.vt;
    for (const k of Object.keys(vars)) root.style.removeProperty(k);
  };
  t.finished.then(cleanup, cleanup);
  return t.updateCallbackDone;
}
/** Rechteck eines Elements als clip-path (Start- oder Zielform einer Zoom-Animation). */
function clipOf(el, radius = 10) {
  const r = el?.getBoundingClientRect();
  if (!r || !r.width) return null;
  return `inset(${r.top}px ${innerWidth - r.right}px ${innerHeight - r.bottom}px ${r.left}px round ${radius}px)`;
}
/** Element fliegt aus einem anderen heraus (wie ein Programmfenster aus dem Dock). */
function zoomFrom(el, fromEl, reverse = false) {
  if (!motionOK() || !fromEl) return null;
  const a = fromEl.getBoundingClientRect(), b = el.getBoundingClientRect();
  if (!a.width || !b.width) return null;
  const z = el.currentCSSZoom || 1;   // Verschiebung gilt in den (vergrößerten) Pixeln des Elements
  const dx = (a.left + a.width / 2 - (b.left + b.width / 2)) / z, dy = (a.top + a.height / 2 - (b.top + b.height / 2)) / z;
  const s = Math.max(a.width / b.width, 0.04);
  const small = { transform: `translate(${dx}px, ${dy}px) scale(${s})`, opacity: 0 };
  const frames = [small, { opacity: 1, offset: reverse ? 0.7 : 0.3 }, { transform: 'none', opacity: 1 }];
  return el.animate(reverse ? frames.reverse() : frames,
                    { duration: reverse ? 260 : 440, easing: reverse ? 'cubic-bezier(.4, 0, .9, .6)' : EASE_POP, fill: reverse ? 'forwards' : 'none' });
}
/** Neue Einträge einer Liste gleiten nacheinander herein (nur Einträge, die vorher nicht da waren). */
const seenItems = new Map();   // Liste -> Set der bekannten Schlüssel
/** Mehrere Bereiche kurz nacheinander hereingleiten lassen (Editor öffnen, App-Start). */
function stagger(els, { delay = 0, step = 55, dy = 10, duration = 380 } = {}) {
  if (!motionOK()) return;
  els.filter(Boolean).forEach((el, i) => el.animate(
    [{ opacity: 0, transform: `translateY(${dy}px)` }, { opacity: 1, transform: 'none' }],
    { duration, delay: delay + i * step, easing: EASE_OUT, fill: 'backwards' }));
}

// Kästchen federn beim Umschalten kurz
document.addEventListener('change', e => {
  const el = e.target;
  if (el.type !== 'checkbox' || el.closest('.switch') || !motionOK()) return;
  el.animate([{ transform: 'scale(1)' }, { transform: 'scale(.82)', offset: .35 }, { transform: 'scale(1.08)', offset: .7 }, { transform: 'scale(1)' }],
             { duration: 320, easing: 'ease-out' });
}, true);

function enterItems(listEl, selector, keyAttr, stagger = 35) {
  const known = seenItems.get(listEl);
  const now = new Set();
  let n = 0;
  $$(selector, listEl).forEach(el => {
    const k = el.dataset[keyAttr];
    now.add(k);
    if (known && !known.has(k) && motionOK()) {
      el.animate([{ opacity: 0, transform: 'translateY(8px) scale(.985)' }, { opacity: 1, transform: 'none' }],
                 { duration: 320, delay: Math.min(n++, 8) * stagger, easing: EASE_OUT, fill: 'backwards' });
    }
  });
  seenItems.set(listEl, now);
}

/* ------------------------------------------------------------ Prüfung vor dem Start
   Fragt den Server, ob das auf diesem PC klappt. Bei Warnungen oder „geht so nicht“ erscheint ein
   Fenster; weitermachen geht immer (man weiß vielleicht, dass bei einem selbst etwas anders ist).
   Ergebnis: null = abgebrochen, sonst { device: 'cpu' | undefined }. */
const PF_HIDDEN = 'vt.hiddenWarnings';
function hiddenWarnings() { try { return JSON.parse(localStorage.getItem(PF_HIDDEN) || '[]'); } catch { return []; } }
async function preflight(kind, params = {}) {
  let r;
  try { r = await api('POST', '/api/preflight', { kind, ...params }, 20000); }
  catch { return {}; }   // Prüfung selbst klappt nicht: nicht im Weg stehen
  const hidden = hiddenWarnings();
  const shown = r.issues.filter(i => !(i.level === 'warn' && hidden.includes(i.id)));
  const blocked = shown.some(i => i.level === 'block');
  if (!blocked && !shown.some(i => i.level === 'warn')) return {};
  const onlyCpuFixable = blocked && shown.filter(i => i.level === 'block').every(i => i.cpu);
  const buttons = [];
  if (blocked) buttons.push({ label: 'Ich weiß, trotzdem versuchen', value: 'force', kind: 'quiet', side: 'left' });
  buttons.push({ label: 'Abbrechen', value: null });
  if (r.cpu_option) buttons.push({ label: 'Auf dem Prozessor rechnen', value: 'cpu', kind: onlyCpuFixable ? 'primary' : '', main: onlyCpuFixable });
  if (!blocked) buttons.push({ label: 'Trotzdem starten', value: 'go', kind: 'primary', main: true });
  const warnIds = shown.filter(i => i.level === 'warn').map(i => i.id);
  const res = await dialog({
    title: blocked ? 'Das klappt so leider nicht' : 'Bevor es losgeht',
    text: blocked ? 'Auf diesem PC wird das so sehr wahrscheinlich nicht funktionieren.' : 'Das kann auf diesem PC Probleme machen:',
    tone: blocked ? 'danger' : 'warn', icon: blocked ? 'blocked' : 'warning', wide: true,
    list: shown.map(i => ({ level: i.level, text: i.text, sub: i.sub })),
    check: blocked ? undefined : { label: 'Diese Hinweise nicht mehr zeigen' },
    buttons,
  });
  const v = blocked ? res : res?.value;
  if (!blocked && res?.checked && v) {
    try { localStorage.setItem(PF_HIDDEN, JSON.stringify([...new Set([...hidden, ...warnIds])])); } catch { /* egal */ }
  }
  if (!v) return null;
  return v === 'cpu' ? { device: 'cpu' } : {};
}

/* ------------------------------------------------------------ Navigation */
/** Abspielzustand eines Projekts zurücksetzen (Knopf, Untertitel), damit nichts ins nächste Projekt überläuft. */
function resetPlayer() {
  $('#btnPlay').innerHTML = ic('play');
  $('#caption').innerHTML = '';
  S.lastActive = new Set();
}
function showHome() {
  const pid = S.pid;
  S.pid = null; S.p = null;
  viewTransition('close', () => {
    $('#editor').hidden = true; $('#home').hidden = false;
    document.body.classList.remove('in-editor');
    resetPlayer();
    const row = pid && $(`#projectList .item[data-id="${CSS.escape(pid)}"]`);
    const to = clipOf(row);
    if (to) document.documentElement.style.setProperty('--vt-to', to);
  }, { '--vt-to': 'inset(40% 30% 40% 30% round 16px)' });
  $('#projTitle').textContent = ''; $('#saveState').textContent = '';
  const v = $('#video'); v.pause(); v.removeAttribute('src'); v.load();
  $('#audioVoc').pause(); $('#audioBack').pause();
  history.replaceState(null, '', '#');
  refreshState();
}
$('#btnHome').onclick = $('#btnBack').onclick = () => { if (S.p) { flushSave(); showHome(); } };

/* ------------------------------------------------------------ Übersicht */
let lastState = null;
let stateFails = 0, stateBusy = false, lastOk = Date.now();
async function refreshState() {
  if (stateBusy) { if (Date.now() - lastOk > 8000) showConnWarn(); return; }
  stateBusy = true;
  try {
    const st = await api('GET', '/api/state', undefined, 15000);
    lastState = st;
    stateFails = 0; lastOk = Date.now(); hideConnWarn();
    renderJobBox(st.jobs);
    renderJobWarn(st.jobs);
    if (!$('#home').hidden) renderHome(st);
  } catch {
    if (++stateFails >= 3) showConnWarn();   // Hintergrunddienst antwortet nicht
  } finally { stateBusy = false; }
}
setInterval(refreshState, 1500);

/* Verbindungswächter: der Hintergrunddienst antwortet nicht */
function showConnWarn() {
  const el = $('#connWarn');
  const secs = Math.round((Date.now() - lastOk) / 1000);
  $('#connWarnText').textContent = secs > 45 ? tf('Voicitool antwortet seit {} s nicht. Starte Voicitool neu, falls das so bleibt.', secs)
                                             : tf('Voicitool antwortet gerade nicht. Einen Moment …');
  el.hidden = false;
}
function hideConnWarn() { $('#connWarn').hidden = true; }

/* Hänger-Hinweis: ein Auftrag kommt nicht voran (vom Server erkannt) */
const dismissedWarn = new Set();
function renderJobWarn(j) {
  const job = (j.running || []).find(x => x.warning && !dismissedWarn.has(x.id + x.warning.code));
  const el = $('#jobWarn');
  if (!job) { el.hidden = true; return; }
  el.dataset.job = job.id; el.dataset.code = job.warning.code; el.dataset.label = job.label;
  $('#jobWarnTitle').textContent = job.warning.code === 'frozen' ? tf('Die Verarbeitung reagiert nicht') :
    job.warning.code === 'vram_full' ? tf('Grafikspeicher voll') : tf('Das dauert ungewöhnlich lange');
  $('#jobWarnText').textContent = tf(job.warning.text);
  $('#jobWarnCpu').hidden = !(job.kind === 'process' && job.device !== 'cpu' && ['vram_full', 'slow', 'frozen'].includes(job.warning.code));
  el.hidden = false;
}
$('#jobWarnWait').onclick = () => { const el = $('#jobWarn'); dismissedWarn.add(el.dataset.job + el.dataset.code); el.hidden = true; };
$('#jobWarnCancel').onclick = () => { const el = $('#jobWarn'); cancelJob(el.dataset.job, el.dataset.label); };
$('#jobWarnLog').onclick = () => api('POST', '/api/open-folder', { which: 'logs' }).catch(e => toast(e.message, true));
$('#jobWarnCpu').onclick = async () => {
  const el = $('#jobWarn');
  const job = (lastState?.jobs.running || []).find(x => x.id === el.dataset.job);
  if (!job) return;
  if (!await dialog({ title: 'Auf dem Prozessor neu starten?', text: 'Die laufende Verarbeitung wird abgebrochen und startet auf dem Prozessor neu. Das dauert länger, hängt aber nicht am Grafikspeicher.',
                      icon: 'monitor', ok: 'Neu starten' })) return;
  try {
    await api('POST', `/api/jobs/${encodeURIComponent(job.id)}/cancel`);
    for (let i = 0; i < 40 && lastState?.jobs.running.some(x => x.id === job.id); i++) { await new Promise(r => setTimeout(r, 500)); await refreshState(); }
    await api('POST', `/api/projects/${encodeURIComponent(job.project)}/reprocess`, { device: 'cpu' });
    toast('Verarbeitung neu gestartet.'); projectSig = ''; refreshState();
  } catch (e) { toast(e.message, true); }
};

/* Qualitätsstufen: alle gleich aufgebaut (Beschreibung, Modell, geschätzte Dauer) */
const QUALITY_DESC = {
  schnell: 'Am schnellsten, etwas mehr Erkennungsfehler.',
  standard: 'Empfohlen: guter Kompromiss aus Tempo und Genauigkeit.',
  maximal: 'Gründlicher, dauert deutlich länger.',
  extrem: 'Das Beste, was geht. Sehr langsam.',
};
// Erkennungssprachen (Whisper kann fast 100, hier die gängigsten)
/* Sprachen der Texterkennung: alle, die Whisper kann (100). Die Namen liefert der Browser (Intl.DisplayNames)
   in der Sprache der Oberfläche, dazu der Eigenname; die Suche findet Name, Eigenname, englischen Namen und Code. */
const WHISPER_LANGS = ('af am ar as az ba be bg bn bo br bs ca cs cy da de el en es et eu fa fi fo fr gl gu ha haw he '
  + 'hi hr ht hu hy id is it ja jw ka kk km kn ko la lb ln lo lt lv mg mi mk ml mn mr ms mt my ne nl nn no oc pa pl ps '
  + 'pt ro ru sa sd si sk sl sn so sq sr su sv sw ta te tg th tk tl tr tt uk ur uz vi yi yo zh yue').split(' ');
const COMMON_LANGS = ['en', 'de', 'es', 'fr', 'pt', 'it', 'nl', 'pl', 'tr', 'ru', 'uk', 'ja', 'ko', 'zh'];
const LANG_CODES = ['auto', 'mixed', ...WHISPER_LANGS];
const LANG_ALIAS = { jw: 'jv' };   // Whisper nutzt für Javanisch noch den alten Code
const langNames = {};
function langName(code, inLang = window.VT_I18N?.lang || 'en') {
  if (code === 'auto') return tf('Automatisch');
  if (code === 'mixed') return tf('Gemischt DE/EN');
  try {
    const dn = langNames[inLang] ||= new Intl.DisplayNames([inLang], { type: 'language', fallback: 'none' });
    const n = dn.of(LANG_ALIAS[code] || code);
    return n ? n[0].toLocaleUpperCase(inLang) + n.slice(1) : code;
  } catch { return code; }
}
const langOptions = selected => LANG_CODES
  .map(k => `<option value="${k}" ${k === selected ? 'selected' : ''}>${esc(langName(k))}</option>`).join('');
const hasMultilingual = () => (lastState?.whisper_installed || []).some(r => r !== 'distil-large-v3.5');
function languageHint(lang) {
  if (lang === 'en' || hasMultilingual()) return '';
  if (lang === 'auto' && !(lastState?.whisper_installed || []).length) return tf('Noch kein Sprachpaket installiert.');
  if (lang === 'auto') return tf('Nur das Englisch-Paket ist installiert, erkannt wird Englisch.');
  return tf('Für diese Sprache fehlt das Sprachpaket „Alle Sprachen“.');
}
const stripExt = f => f.replace(/\.[^.]+$/, '');

function qualityModel(key) {
  const i = lastState?.quality_info?.[key];
  if (!i) return '';
  return tf('Whisper {}, Suchbreite {}, {}', i.whisper, i.beam,
    i.speaker2 ? tf('zwei Stimm-Modelle') : tf('ein Stimm-Modell'));
}
function qualityHint(key, est) {
  let s = `${tf(QUALITY_DESC[key] || '')} ${qualityModel(key)}.`;
  const secs = est?.levels?.[key];
  if (secs) {
    s += ' ' + tf('Dauer {}', fmtDuration(secs));
    s += est.device === 'cpu' ? ' ' + tf('(auf dem Prozessor)') : '';
    s += est.learned_runs ? '' : ' ' + tf('(erste Schätzung, wird mit jedem Video genauer)');
  }
  return s.trim();
}
function qualityOptions(selected, est) {
  const q = lastState?.quality || { schnell: 'Schnell', standard: 'Standard', maximal: 'Maximal' };
  return Object.entries(q).map(([k, label]) => {
    const time = est?.levels?.[k] ? ` · ${fmtDuration(est.levels[k])}` : '';
    return `<option value="${k}" ${k === selected ? 'selected' : ''}>${esc(tf(label))}${esc(time)}</option>`;
  }).join('');
}

/* Geschätzte Dauer je Video im Eingang (für jede Stufe) */
const estCache = {};
async function loadEstimate(file, laugh) {
  const key = `${file}|${laugh}`;
  if (estCache[key]) return estCache[key];
  try {
    estCache[key] = await api('GET', `/api/estimate?file=${encodeURIComponent(file)}&laugh=${laugh ? 'true' : 'false'}`);
  } catch { estCache[key] = null; }
  return estCache[key];
}
async function updateInboxEstimate(it) {
  const file = it.dataset.file, v = inboxForm[file];
  if (!v) return;
  const est = await loadEstimate(file, v.laugh);
  if (!est || !it.isConnected) return;
  const sel = $('.f-quality', it);
  if (sel && document.activeElement !== sel) sel.innerHTML = qualityOptions(v.quality, est);
  $('.qhint', it).textContent = qualityHint(v.quality, est);
}

/* Fortschritt: gleichmäßig nach geschätzter Zeit, wird langsamer statt zu springen oder stehenzubleiben */
const shownPct = {};
function jobProgress(j) {
  const est = j.estimate;
  const real = j.pct || 0;
  if (!est || !est.steps || j.state !== 'läuft') return { pct: real, remaining: null };
  const steps = est.steps, total = steps.reduce((a, s) => a + s[1], 0) || 1;
  const idx = steps.findIndex(s => s[0] === j.step);
  if (idx < 0) return { pct: Math.max(real, shownPct[j.id] || 0), remaining: null };
  const before = steps.slice(0, idx).reduce((a, s) => a + s[1], 0);
  const cur = Math.max(1, steps[idx][1]);
  const inStep = Math.max(0, Date.now() / 1000 - (j.step_started || j.started));
  // bis 85 % der geschätzten Schrittzeit gleichmäßig, danach immer langsamer (nie ganz 100 %)
  const lin = inStep / cur;
  const byTime = lin < 0.85 ? lin : 0.85 + 0.14 * (1 - Math.exp(-(lin - 0.85) / 0.6));
  const frac = real > 0.02 ? Math.max(real, Math.min(byTime, real + 0.15)) : byTime;
  let pct = (before + cur * Math.min(0.99, frac)) / total;
  pct = Math.max(pct, shownPct[j.id] || 0);   // nie rückwärts
  shownPct[j.id] = pct;
  // Restzeit: aktueller Schritt nach echtem Fortschritt, falls vorhanden, sonst nach Schätzung
  const restCur = real > 0.08 ? inStep * (1 - real) / real : Math.max(cur - inStep, cur * 0.15);
  const slow = real > 0.08 ? Math.min(3, Math.max(1, (inStep / real) / cur)) : 1;
  const after = steps.slice(idx + 1).reduce((a, s) => a + s[1], 0) * Math.sqrt(slow);
  return { pct, remaining: restCur + after };
}

async function cancelJob(jid, label) {
  if (!await dialog({ title: tf('„{}" abbrechen?', label), text: 'Der bisherige Fortschritt dieses Vorgangs geht verloren.',
                     tone: 'danger', icon: 'stop', ok: 'Vorgang abbrechen', cancel: 'Weiterlaufen lassen' })) return;
  try { await api('POST', `/api/jobs/${encodeURIComponent(jid)}/cancel`); toast('Abgebrochen.'); refreshState(); }
  catch (e) { toast(e.message, true); }
}

let shownJob = null;
function renderJobBox(j) {
  const run = j.running || [];
  $('#jobBox').hidden = !run.length;
  shownJob = run.length ? run[run.length - 1] : null;
  if (shownJob) {
    const cur = shownJob;
    const more = run.length - 1 + j.pending.length;
    const q = more ? ` (+${more} weitere)` : '';
    const pr = jobProgress(cur);
    const rest = pr.remaining != null ? ' · ' + tf('noch {}', fmtDuration(pr.remaining)) : '';
    $('#jobLabel').textContent = `${cur.label} · ${tf(cur.step)}${cur.message ? ': ' + tf(cur.message) : ''}${rest}${q}`;
    $('#jobLabel').title = run.map(r => `${r.label}: ${r.step} ${Math.round(jobProgress(r).pct * 100)} %`).join('\n');
    $('#jobBar').style.width = `${(pr.pct * 100).toFixed(1)}%`;
  }
  taskbarProgress(run);
}
/* Fortschritt auch im Taskleisten-Symbol (nur im Voicitool-Fenster, nicht im Browser): Mittel aller laufenden Vorgänge */
let taskbarLast = null;
function taskbarProgress(run) {
  const api = window.pywebview?.api;
  if (!api?.taskbar_progress) return;
  let v = null;
  if (run.length) {
    const ps = run.map(r => jobProgress(r).pct);
    v = ps.every(p => !p) ? -1 : ps.reduce((a, b) => a + b, 0) / ps.length;
  }
  const key = v === null ? 'aus' : v < 0 ? 'unbestimmt' : String(Math.round(v * 200));
  if (key === taskbarLast) return;
  taskbarLast = key;
  api.taskbar_progress(v).catch(() => {});
}
$('#jobCancel').onclick = () => { if (shownJob) cancelJob(shownJob.id, shownJob.label); };

/* Eingang: Formularwerte bleiben beim Neuzeichnen erhalten */
const inboxForm = {};
const retryQuality = {};
let inboxSig = '', projectSig = '';

function renderInbox(st) {
  const inbox = $('#inboxList');
  if (uploadNote.file && !st.inbox.includes(uploadNote.file)) clearUploadState();   // Video weg: Meldung auch
  const sig = JSON.stringify(st.inbox) + JSON.stringify(st.inbox_subs || []) + JSON.stringify(onlineState?.ready || {}) + Object.values(onlineState?.services || {}).map(x => +x.set).join('') + (onlineState?.weak ? 'w' : '') + (lastState?.default_quality || '') + JSON.stringify(st.whisper_installed) + JSON.stringify(st.categories || []);
  if (sig === inboxSig) return;
  inboxSig = sig;
  st.inbox.forEach(f => {
    // nur das Englisch-Paket installiert: Englisch statt „automatisch“ vorschlagen
    // Voreinstellungen aus den Einstellungen; nur Englisch-Paket da: Englisch statt „automatisch“
    inboxForm[f] ??= { name: stripExt(f), language: setting('default_language') || (hasMultilingual() ? 'auto' : 'en'),
                       speakers: '', quality: setting('default_quality') || st.default_quality || 'standard',
                       laugh: !!setting('default_laugh') };
    // beim Herunterladen mitgeholte Untertitel einmalig als „Text vorgeben“ übernehmen (entfernen bleibt entfernt)
    if ((st.inbox_subs || []).includes(f) && !inboxForm[f].subsChecked) {
      inboxForm[f].subsChecked = true;
      api('GET', `/api/textsources/inbox?file=${encodeURIComponent(f)}`).then(ref => {
        if (inboxForm[f] && !inboxForm[f].reftext) { inboxForm[f].reftext = ref; inboxSig = ''; refreshState(); }
      }).catch(() => {});
    }
  });
  inbox.innerHTML = st.inbox.length ? st.inbox.map(f => {
    const v = inboxForm[f];
    return `<div class="item inbox-item" data-file="${esc(f)}">
      <div class="inbox-head"><span class="file" title="Datei im Eingang">${ic('clapper')}<span data-nolang>${esc(f)}</span></span>
        <button class="icon-btn del-inbox" title="Video in den Papierkorb verschieben">${ic('trash')}</button></div>
      <div class="inbox-form">
        <label class="grow">Projektname <input class="f-name" value="${esc(v.name)}" placeholder="${esc(stripExt(f))}"></label>
        <label>Sprache <select class="f-language">${langOptions(v.language)}</select></label>
        <label>Sprecher <input class="f-speakers" type="number" min="1" max="30" placeholder="auto" value="${esc(v.speakers)}"></label>
        <label>Qualität <select class="f-quality">${qualityOptions(v.quality)}</select></label>
        ${(st.categories || []).length ? `<label>Kategorie <select class="f-category"><option value="">${esc(tf('Keine'))}</option>${st.categories.map(c => `<option data-nolang value="${esc(c.id)}" ${c.id === v.category ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select></label>` : ''}
        <label class="check-inline" title="Lacher als eigene Zeilen „(lacht)“ anlegen"><span>Lachen</span><span class="check"><input type="checkbox" class="f-laugh" ${v.laugh ? 'checked' : ''}> erkennen</span></label>
        <button class="btn ${onlineReady() && onlineState?.weak ? '' : 'primary'} go">Verarbeiten</button>
        ${onlineReady() ? (() => {
          const opts = asrChoices(), cur = opts.some(o => o.value === v.online_asr) ? v.online_asr : defaultAsr();
          const note = opts.find(o => o.value === cur)?.note || '';
          return `<label class="online-pick" title="${esc(note)}">${esc(tf('Online-Dienst'))}
            <select class="f-online_asr">${opts.map(o => `<option value="${o.value}" ${o.value === cur ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}</select></label>`;
        })() : ''}
        <button class="btn ${onlineReady() && onlineState?.weak ? 'primary' : ''} go-online" title="${esc(onlineReady()
          ? tf('Online rechnen: {}. Die Tonspur wird dafür hochgeladen.', onlineSummary(v.online_asr))
          : tf('Online rechnen einrichten: Kostenlose Dienste übernehmen Stimmen trennen und Text erkennen.'))}">${ic('cloud')}<span>${esc(tf('Online rechnen'))}</span></button>
      </div>
      <div class="muted small qhint">${esc(qualityHint(v.quality))}</div>
      <div class="reftext-row${v.reftext ? ' on' : ''}">
        <button class="btn small reftext-btn">${ic('music')}<span>${esc(tf(v.reftext ? 'Text ändern' : 'Text vorgeben'))}</span></button>
        <span class="muted small">${v.reftext
          ? esc(tf('Vorgegebener Text: {} ({} Zeilen)', v.reftext.source || tf('selbst eingefügt'), v.reftext.text.split('\n').filter(x => x.trim()).length))
          : esc(tf('Liedtext, Drehbuch oder Untertitel vorgeben: Voicitool ordnet ihn dann nur noch zu.'))}</span>
        ${v.reftext ? `<button class="link-btn reftext-clear">${esc(tf('entfernen'))}</button>` : ''}
      </div>
      <div class="lang-hint" ${languageHint(v.language) ? '' : 'hidden'}><span>${esc(languageHint(v.language))}</span>
        <button class="btn small open-models">${esc(tf('Sprachpaket laden'))}</button></div>
    </div>`;
  }).join('') : `<div class="empty">${ic('inbox', 'big')}<div>Keine neuen Videos.</div></div>`;
  $('#inboxCount').textContent = st.inbox.length || '';
  $$('select.f-language', inbox).forEach(enhanceLangSelect);
  enterItems(inbox, '.inbox-item', 'file');
  $$('.inbox-item', inbox).forEach(updateInboxEstimate);
}

/* Text vorgeben: Liedtext, Drehbuch oder Untertitel suchen (Datei, YouTube, LRCLIB, lyrics.ovh, Fandom-Wikis)
   oder selbst einfügen. Der Text landet immer erst im Textfeld (prüfen, Strophen streichen), dann übernehmen.
   Bei der Verarbeitung übernimmt Voicitool die Schreibweise und ergänzt fehlende Wörter, die Zeiten kommen
   weiter aus dem Video. -> Promise<{text, source} | null> */
function openRefTextDialog(file, current) {
  return new Promise(resolve => {
    const ov = document.createElement('div');
    ov.className = 'dlg-overlay';
    ov.innerHTML = `<div class="dlg wide reftext-dlg" role="dialog" aria-modal="true">
      <div class="dlg-head"><div class="dlg-icon" aria-hidden="true">${ic('music')}</div>
        <div class="dlg-titles"><h2>Text vorgeben</h2>
        <p class="dlg-text">Such den Liedtext, das Drehbuch oder die Untertitel zu diesem Video, oder füg den Text selbst ein. Voicitool übernimmt dann die Schreibweise und ergänzt fehlende Wörter, die Zeiten kommen weiter aus dem Video.</p></div></div>
      <div class="rt-search"><input class="rt-q" type="search" spellcheck="false" placeholder="Titel, Interpret oder Serie und Folge"><button class="btn small primary rt-go">${ic('search')}<span>Suchen</span></button></div>
      <div class="rt-results"><div class="muted small rt-status"></div></div>
      <label class="rt-label">Text <span class="muted small rt-count"></span></label>
      <textarea class="rt-text" spellcheck="false" placeholder="Hier landet der gefundene Text. Du kannst ihn auch selbst einfügen oder kürzen, z. B. Strophen, die im Video nicht vorkommen."></textarea>
      <div class="dlg-actions"><button class="btn rt-cancel">Abbrechen</button><button class="btn primary rt-ok">Übernehmen</button></div>
    </div>`;
    document.body.appendChild(ov);
    const box = $('.reftext-dlg', ov), q = $('.rt-q', box), list = $('.rt-results', box), ta = $('.rt-text', box);
    let source = current?.source || '';
    ta.value = current?.text || '';
    const count = () => {
      const n = ta.value.split('\n').filter(x => x.trim()).length;
      $('.rt-count', box).textContent = n ? tf('{} Zeilen', n) : '';
      $('.rt-ok', box).disabled = !n;
    };
    ta.addEventListener('input', () => { source = ''; count(); });
    count();
    const status = t => { $('.rt-status', box).textContent = t; };
    async function run() {
      const query = q.value.trim();
      status(tf('Suche …'));
      $$('.rt-hit', list).forEach(x => x.remove());
      let r;
      try { r = await api('GET', `/api/textsources/search?q=${encodeURIComponent(query)}&file=${encodeURIComponent(file)}&lang=${encodeURIComponent(inboxForm[file]?.language || '')}`, undefined, 90000); }
      catch (e) { status(e.message); return; }
      const failed = r.failed?.length ? ' ' + tf('Nicht erreichbar: {}.', r.failed.join(', ')) : '';
      status((r.results.length ? tf('{} Treffer. Klick übernimmt den Text ins Feld unten.', r.results.length) : tf('Nichts gefunden. Probier andere Wörter oder füg den Text selbst ein.')) + failed);
      for (const hit of r.results) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'rt-hit';
        const own = hit.source === 'Datei' || hit.source === 'YouTube';   // Beschriftung von Voicitool selbst: übersetzen
        b.innerHTML = `<span class="rt-src">${esc(tf(hit.source))}</span><span class="rt-main"><b ${own ? '' : 'data-nolang'}>${esc(own ? tf(hit.title) : hit.title)}</b><span class="muted small" ${own ? '' : 'data-nolang'}>${esc(own ? tf(hit.subtitle || '') : hit.subtitle || '')}${hit.lines ? ' · ' + esc(tf('{} Zeilen', hit.lines)) : ''}</span>${hit.preview ? `<span class="muted small rt-prev" data-nolang>${esc(hit.preview)}</span>` : ''}</span>`;
        b.onclick = async () => {
          let text = hit.text;
          if (!text) {
            b.disabled = true;
            try { text = (await api('GET', `/api/textsources/fetch?source=${encodeURIComponent(hit.source)}&id=${encodeURIComponent(hit.id)}`)).text; }
            catch (e) { toast(e.message, true); b.disabled = false; return; }
            b.disabled = false;
          }
          ta.value = text; count();
          source = `${hit.source}: ${hit.title}`;
          $$('.rt-hit', list).forEach(x => x.classList.toggle('on', x === b));
          ta.scrollTop = 0;
        };
        list.appendChild(b);
      }
    }
    $('.rt-go', box).onclick = run;
    q.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); run(); } });
    const close = val => { ov.remove(); document.removeEventListener('keydown', onKey, true); resolve(val); };
    const onKey = e => { if (e.key === 'Escape') { e.stopPropagation(); close(null); } };
    document.addEventListener('keydown', onKey, true);
    $('.rt-cancel', box).onclick = () => close(null);
    $('.rt-ok', box).onclick = () => close({ text: ta.value.trim(), source: source || tf('selbst eingefügt') });
    api('GET', `/api/textsources/suggest?file=${encodeURIComponent(file)}`).then(r => {
      if (!q.value) q.value = r.query || '';
      if (!current) run();   // gleich suchen: Untertitel in der Datei oder von YouTube tauchen so sofort auf
    }).catch(() => {});
    q.focus();
  });
}

/* Sprachwahl mit Suche statt einer langen Liste. Das eigentliche <select> bleibt (unsichtbar) für den
   Wert und die bisherigen Ereignisse; daneben sitzt ein Knopf, der eine Liste mit Suchfeld öffnet.
   Sonderwerte des Felds (z. B. „Automatisch“) stehen oben, dann „Häufig“, dann alle Sprachen A bis Z. */
function enhanceLangSelect(sel) {
  if (!sel || sel.dataset.combo) return;
  sel.dataset.combo = '1';
  sel.classList.add('lang-native');
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'lang-combo';
  btn.setAttribute('data-nolang', '');
  const label = () => WHISPER_LANGS.includes(sel.value) ? langName(sel.value) : (sel.selectedOptions[0]?.textContent || sel.value);
  btn._paint = () => { btn.innerHTML = `<span>${esc(label())}</span>${ic('chevron-down')}`; };
  btn._paint();
  btn.addEventListener('click', () => openLangPicker(btn, sel, code => {
    sel.value = code;
    btn._paint();
    sel.dispatchEvent(new Event('input', { bubbles: true }));
    sel.dispatchEvent(new Event('change', { bubbles: true }));
  }));
  sel.after(btn);
}
document.addEventListener('vt-lang', () => $$('.lang-combo').forEach(b => b._paint?.()));

function openLangPicker(anchor, sel, onPick) {
  closeLangPicker();
  const ui = window.VT_I18N?.lang || 'en';
  const norm = s => (s || '').normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();
  const current = sel.value;
  const make = (code, name) => {
    const native = WHISPER_LANGS.includes(code) ? langName(code, LANG_ALIAS[code] || code) : '';
    const en = WHISPER_LANGS.includes(code) ? langName(code, 'en') : '';
    return { code, name, native: norm(native) === norm(name) ? '' : native,
             words: norm([name, native, en, code].join(' ')).split(/[\s()/]+/).filter(Boolean) };
  };
  const special = [...sel.options].filter(o => !WHISPER_LANGS.includes(o.value)).map(o => make(o.value, o.textContent));
  const all = WHISPER_LANGS.map(c => make(c, langName(c, ui))).sort((a, b) => a.name.localeCompare(b.name, ui));
  const common = COMMON_LANGS.map(c => all.find(i => i.code === c)).filter(Boolean);

  const el = document.createElement('div');
  el.className = 'lang-pop';
  el.setAttribute('popover', 'manual');
  el.setAttribute('data-nolang', '');
  el.innerHTML = `<div class="lp-search">${ic('search')}<input type="text" spellcheck="false" autocomplete="off"
      placeholder="${esc(tf('Sprache suchen …'))}"></div><div class="lp-list" role="listbox"></div>`;
  document.body.appendChild(el);
  anchor.style.anchorName = '--vt-langpick';
  el.showPopover();
  const list = $('.lp-list', el), input = $('input', el);
  let shown = [], active = 0;

  function render() {
    const q = norm(input.value.trim());
    shown = [];
    let html = '';
    const item = i => {
      html += `<div class="lp-item${i.code === current ? ' on' : ''}" role="option" data-i="${shown.length}">
        <span class="lp-name">${esc(i.name)}</span>${i.native ? `<span class="lp-native">${esc(i.native)}</span>` : ''}
        <span class="lp-code">${esc(i.code || '')}</span><span class="lp-check">${i.code === current ? ic('check') : ''}</span></div>`;
      shown.push(i);
    };
    const group = (title, arr) => {
      if (!arr.length) return;
      if (title) html += `<div class="lp-group">${esc(title)}</div>`;
      arr.forEach(item);
    };
    if (q) {
      // Treffer: Name beginnt so > ein Wort beginnt so > irgendwo enthalten
      const score = i => (norm(i.name).startsWith(q) ? 0 : i.words.some(w => w.startsWith(q)) ? 1 : 2);
      group('', [...special, ...all].filter(i => i.words.some(w => w.includes(q)) || norm(i.name).includes(q))
        .sort((a, b) => score(a) - score(b)));
    } else {
      group('', special);
      group(tf('Häufig'), common);
      group(tf('Alle Sprachen ({})', all.length), all);
    }
    list.innerHTML = html || `<div class="lp-empty">${esc(tf('Keine Sprache gefunden.'))}</div>`;
    active = q ? 0 : Math.max(0, shown.findIndex(i => i.code === current));
    mark(true);
  }
  function mark(scroll) {
    $$('.lp-item', list).forEach(x => x.classList.toggle('active', +x.dataset.i === active));
    if (scroll) $(`.lp-item[data-i="${active}"]`, list)?.scrollIntoView({ block: input.value ? 'nearest' : 'center' });
  }
  function pick(i) {
    if (!i) return;
    closeLangPicker();
    if (i.code !== current) onPick(i.code);
  }
  input.addEventListener('input', render);
  input.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      active = clamp(active + (e.key === 'ArrowDown' ? 1 : -1), 0, shown.length - 1);
      mark(true);
    } else if (e.key === 'Enter') { e.preventDefault(); pick(shown[active]); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeLangPicker(); }
  });
  list.addEventListener('mousemove', e => {
    const x = e.target.closest('.lp-item');
    if (x && +x.dataset.i !== active) { active = +x.dataset.i; mark(false); }
  });
  list.addEventListener('click', e => { const x = e.target.closest('.lp-item'); if (x) pick(shown[+x.dataset.i]); });
  const outside = e => { if (!el.contains(e.target) && !anchor.contains(e.target)) closeLangPicker(); };
  document.addEventListener('pointerdown', outside, true);
  el._cleanup = () => {
    document.removeEventListener('pointerdown', outside, true);
    anchor.style.anchorName = '';
    anchor.focus({ preventScroll: true });
  };
  render();
  input.focus();
}
function closeLangPicker() {
  const el = document.querySelector('.lang-pop');
  if (!el) return;
  el._cleanup?.();
  if (!motionOK()) { el.remove(); return; }
  el.classList.add('closing');
  setTimeout(() => el.remove(), 140);
}

const inboxEl = $('#inboxList');
inboxEl.addEventListener('input', e => {
  const it = e.target.closest('.item'); if (!it) return;
  const key = [...e.target.classList].find(c => c.startsWith('f-'))?.slice(2);
  if (!key) return;
  inboxForm[it.dataset.file][key] = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
  if (key === 'quality' || key === 'laugh') updateInboxEstimate(it);
  if (key === 'online_asr') {
    const o = asrChoices().find(x => x.value === e.target.value);
    e.target.closest('.online-pick').title = o?.note || '';
    $('.go-online', it).title = tf('Online rechnen: {}. Die Tonspur wird dafür hochgeladen.', onlineSummary(e.target.value));
  }
  if (key === 'language') {
    const hint = languageHint(e.target.value);
    $('.lang-hint', it).hidden = !hint;
    $('.lang-hint span', it).textContent = hint;
  }
});
inboxEl.addEventListener('click', async e => {
  const it = e.target.closest('.item'); if (!it) return;
  const file = it.dataset.file;
  if (e.target.closest('.open-models')) { openSettings(); return; }
  if (e.target.closest('.reftext-btn')) {
    const r = await openRefTextDialog(file, inboxForm[file]?.reftext);
    if (r && inboxForm[file]) {
      inboxForm[file].reftext = r;
      // am Video merken: sonst ist der Text nach einem Neustart weg, ohne dass es jemand bemerkt
      api('PUT', '/api/textsources/inbox', { file, text: r.text, source: r.source }).catch(() => {});
      inboxSig = ''; refreshState();
    }
    return;
  }
  if (e.target.closest('.reftext-clear')) {
    if (inboxForm[file]) delete inboxForm[file].reftext;
    api('PUT', '/api/textsources/inbox', { file, text: '' }).catch(() => {});
    inboxSig = ''; refreshState();
    return;
  }
  if (e.target.closest('.del-inbox')) {
    if (!await dialog({ title: tf('„{}" in den Papierkorb verschieben?', file), text: 'Du kannst es aus dem Papierkorb von Windows wiederherstellen.',
                       tone: 'danger', ok: 'In den Papierkorb' })) return;
    try { await api('DELETE', `/api/inbox/${encodeURIComponent(file)}`); delete inboxForm[file]; toast('In den Papierkorb verschoben.'); }
    catch (err) { toast(err.message, true); }
    inboxSig = ''; refreshState();
  } else if (e.target.closest('.go-online')) {
    if (!onlineReady()) {
      toast(tf('Richte zuerst Online rechnen ein. Das dauert etwa 5 Minuten.'), false, 5000);
      openSettings('online');
      return;
    }
    const b = e.target.closest('.go-online'); b.disabled = true;
    const v = inboxForm[file];
    if (!await preflight('process', { file, quality: v.quality, laugh: v.laugh, language: v.language, device: 'online' })) { b.disabled = false; return; }
    try {
      const asr = asrChoices().some(o => o.value === v.online_asr) ? v.online_asr : defaultAsr();
      await api('POST', '/api/projects', { filename: file, name: v.name.trim() || stripExt(file), language: v.language, speakers: v.speakers, quality: v.quality, laugh: v.laugh, ui_lang: window.VT_I18N?.lang, category: v.category || null, reftext: v.reftext || null, online: true, online_asr: asr });
      delete inboxForm[file];
      toast(tf('Online rechnen gestartet: {}.', onlineSummary(asr)));
    } catch (err) { toast(err.message, true); b.disabled = false; }
    inboxSig = ''; refreshState();
  } else if (e.target.closest('.go')) {
    const b = e.target.closest('.go'); b.disabled = true;
    const v = inboxForm[file];
    const pf = await preflight('process', { file, quality: v.quality, laugh: v.laugh, language: v.language });
    if (!pf) { b.disabled = false; return; }
    try {
      await api('POST', '/api/projects', { filename: file, name: v.name.trim() || stripExt(file), language: v.language, speakers: v.speakers, quality: v.quality, laugh: v.laugh, ui_lang: window.VT_I18N?.lang, device: pf.device, category: v.category || null, reftext: v.reftext || null });
      delete inboxForm[file];
      toast('Verarbeitung gestartet.');
    } catch (err) { toast(err.message, true); b.disabled = false; }
    inboxSig = ''; refreshState();
  }
});

/* Projektliste: Suche und Sortierung (Sortierung bleibt gespeichert) */
const projView = { q: '', sort: 'recent' };
try { projView.sort = localStorage.getItem('vt.projSort') || 'recent'; } catch { /* egal */ }
$('#projSort').value = projView.sort;
$('#projSearch').addEventListener('input', e => { projView.q = e.target.value.trim().toLowerCase(); projectSig = ''; if (lastState) renderProjects(lastState); });
$('#projSort').addEventListener('change', e => {
  projView.sort = e.target.value;
  try { localStorage.setItem('vt.projSort', projView.sort); } catch { /* egal */ }
  projectSig = ''; if (lastState) renderProjects(lastState);
});
function viewProjects(all, order = []) {
  const arr = all.filter(p => !projView.q || (p.name || '').toLowerCase().includes(projView.q));
  if (projView.sort === 'custom') {   // per Ziehen festgelegt; noch nie verschobene (neue) stehen vorne
    const pos = new Map(order.map((id, i) => [id, i]));
    arr.sort((a, b) => (pos.get(a.id) ?? -1) - (pos.get(b.id) ?? -1));
  }
  const rank = { fehler: 0, verarbeitet: 1, wartet: 2, abgebrochen: 3, fertig: 4 };
  if (projView.sort === 'name') arr.sort((a, b) => (a.name || '').localeCompare(b.name || '', undefined, { numeric: true, sensitivity: 'base' }));
  else if (projView.sort === 'length') arr.sort((a, b) => (b.duration || 0) - (a.duration || 0));
  else if (projView.sort === 'status') arr.sort((a, b) => (rank[a.status] ?? 5) - (rank[b.status] ?? 5));
  return arr;
}

function renderProjects(st) {
  const list = $('#projectList');
  const act = document.activeElement;
  if (act && list.contains(act) && act.tagName === 'SELECT') return; // offenes Auswahlfeld nicht zerstören
  const jobsByProject = {};
  [...(st.jobs.running || []), ...st.jobs.pending].forEach(j => { if (!jobsByProject[j.project]) jobsByProject[j.project] = j; });
  const shown = viewProjects(st.projects, st.order);
  const cats = st.categories || [];
  const sig = JSON.stringify([shown, cats, onlineReady(), Object.values(jobsByProject).map(j => [j.id, j.state, j.step])]);
  if (sig === projectSig) { updateProjectProgress(jobsByProject); return; }
  projectSig = sig;
  $('#projectCount').textContent = shown.length === st.projects.length ? st.projects.length : `${shown.length} / ${st.projects.length}`;
  // Kategorien als aufklappbare Gruppen, darunter die Projekte ohne Kategorie
  const known = new Set(cats.map(c => c.id));
  const groups = {};
  shown.forEach(p => { (groups[known.has(p.category) ? p.category : ''] ||= []).push(p); });
  const searching = !!projView.q;
  let html = '';
  for (const c of cats) {
    const items = groups[c.id] || [];
    if (searching && !items.length) continue;
    const all = st.projects.filter(p => p.category === c.id);
    const ready = all.filter(p => p.status === 'fertig').length;
    const open = searching || !c.collapsed;
    html += `<div class="cat${open ? '' : ' collapsed'}" data-cat="${esc(c.id)}">
      <div class="cat-head" draggable="true" title="${esc(tf('Klicken zum Auf- und Zuklappen, ziehen zum Umsortieren'))}">
        <span class="grip" aria-hidden="true">${ic('grip')}</span><span class="chev" aria-hidden="true">${ic('chevron-right')}</span><span class="cat-name">${ic('folder')}<span data-nolang>${esc(c.name)}</span></span>
        <span class="pill">${all.length}</span><span class="muted small">${esc(tf('{} fertig', ready))}</span>
        <span class="spacer"></span>
        <button class="btn small cat-share" title="${esc(tf('Alle fertigen Projekte als Packs exportieren: eine ZIP für Freunde oder direkt ins Spiel'))}">${ic('package')}<span>${esc(tf('Exportieren'))}</span></button>
        <button class="link-btn cat-ren" title="Umbenennen">${ic('pencil')}</button>
        <button class="link-btn cat-del" title="${esc(tf('Kategorie entfernen (Projekte bleiben)'))}">${ic('trash')}</button>
      </div>
      <div class="cat-body"><div class="cat-inner"><div class="cat-items">${items.length ? items.map(p => projectItem(p, jobsByProject[p.id])).join('')
        : `<div class="empty">${esc(tf('Leer. Projekte hierher ziehen oder über das Ordner-Symbol zuordnen.'))}</div>`}</div></div></div>
    </div>`;
  }
  const loose = groups[''] || [];
  if (loose.length || (cats.length && !searching)) {
    if (cats.length) html += `<div class="cat-loose-head" data-cat="">${esc(tf('Ohne Kategorie'))}</div>`;
    html += `<div class="cat-loose" data-cat="">${loose.map(p => projectItem(p, jobsByProject[p.id])).join('')}</div>`;
  }
  list.innerHTML = shown.length || cats.length ? html
    : `<div class="empty">${ic(st.projects.length ? 'search' : 'folders', 'big')}<div>${st.projects.length ? 'Kein Projekt passt zur Suche.' : 'Noch keine Projekte.'}</div></div>`;
  enterItems(list, '.item', 'id');
  flipPlay();
  updateProjectProgress(jobsByProject);
}

/** Eine Projektzeile in der Übersicht. */
function projectItem(p, j) {
  const processing = j && j.kind === 'process';
  // Status „läuft“, aber dieses Fenster kennt keinen Auftrag: ein zweites Voicitool (anderer Port)
  // verarbeitet es gerade. Fortschritt kennt nur jenes, deshalb hier kein Balken.
  const elsewhere = !j && ['wartet', 'verarbeitet'].includes(p.status);
  const statusText = j ? (j.state === 'wartet' ? 'wartet' : j.step) : elsewhere ? 'läuft in einem anderen Voicitool' : p.status;
  const canRetry = !j && ['fehler', 'abgebrochen', 'wartet', 'verarbeitet'].includes(p.status);
  const q = retryQuality[p.id] || p.quality || 'standard';
  return `<div class="item" data-id="${esc(p.id)}" data-name="${esc(p.name)}" draggable="true">
    <div class="name"><span data-nolang>${esc(p.name)}</span> <button class="link-btn ren" title="Umbenennen">${ic('pencil')}</button>
      <button class="link-btn cat-move" title="${esc(tf('In Kategorie verschieben'))}">${ic('folder-move')}</button></div>
    <span class="status ${esc(j ? 'verarbeitet' : p.status)}${elsewhere ? ' elsewhere' : ''}"${elsewhere ? ` title="${esc(tf('Fortschritt siehst du in dem Fenster, das es verarbeitet. Hängt es, hilft Neu starten.'))}"` : ''}>${esc(statusText)}</span>
    ${processing ? `<div class="bar"><div style="width:0%"></div></div><span class="eta muted small"></span>` : ''}
    <span class="meta">${p.duration ? fmt(p.duration, false) : ''} ${p.lines ? `· ${p.lines} Zeilen · ${p.characters} Charaktere` : ''} ${p.language ? '· ' + p.language.toUpperCase() : ''} ${p.quality ? '· ' + esc(tf(lastState?.quality?.[p.quality] || p.quality)) : ''}${p.online ? ' · ' + esc(tf('Online')) : ''}</span>
    ${j ? `<button class="btn small danger cancel" data-job="${esc(j.id)}" data-label="${esc(j.label)}">Abbrechen</button>` : ''}
    ${!j && p.status === 'fertig' ? '<button class="btn primary small open">Öffnen</button>' : ''}
    ${canRetry ? `<select class="retry-q" title="Qualität">${qualityOptions(q)}</select><button class="btn small retry">Neu starten</button>` : ''}
    ${!j ? `<button class="btn small danger del" title="Projekt löschen (Video kommt zurück in den Eingang)">${ic('trash')}</button>` : ''}
    ${p.status === 'fehler' && p.error && !j ? `<div class="err"><span>${esc(p.error)}</span>
      ${['vram', 'gpu', 'crash', 'ram'].includes(p.error_code) && p.device !== 'cpu' ? '<button class="btn small retry-cpu">Auf dem Prozessor neu starten</button>' : ''}
      ${p.error_code === 'disk' ? '<button class="btn small goto-storage">Speicher freigeben</button>' : ''}
      ${p.online && ['online_key', 'online_setup'].includes(p.error_code) ? `<button class="btn small goto-online">${esc(tf('Online rechnen einrichten'))}</button>` : ''}
      ${p.online && String(p.error_code || '').startsWith('online') || p.online && p.error_code === 'net' ? `<button class="btn small retry-local">${esc(tf('Auf diesem PC rechnen'))}</button>` : ''}
      ${!p.online && onlineReady() ? `<button class="btn small retry-online">${ic('cloud')}<span>${esc(tf('Online neu starten'))}</span></button>` : ''}
      <button class="copy-btn copy-err" title="Fehlermeldung kopieren">Kopieren</button></div>` : ''}
  </div>`;
}

function updateProjectProgress(jobsByProject) {
  for (const [pid, j] of Object.entries(jobsByProject)) {
    const it = $(`#projectList .item[data-id="${CSS.escape(pid)}"]`);
    if (!it || j.kind !== 'process') continue;
    const pr = jobProgress(j);
    const bar = $('.bar > div', it);
    if (bar) bar.style.width = `${(pr.pct * 100).toFixed(1)}%`;
    const eta = $('.eta', it);
    if (eta) eta.textContent = pr.remaining != null ? tf('noch {}', fmtDuration(pr.remaining)) : '';
  }
}

const projectEl = $('#projectList');
/* ------------------------------------------------------------ Kategorien
   Projekte in Gruppen sortieren (z. B. 30 Family-Guy-Clips), auf- und zuklappen, zusammen teilen. */
const catById = id => (lastState?.categories || []).find(c => c.id === id);
async function newCategory(assignPid) {
  const name = await dialog({ title: 'Neue Kategorie', text: 'Zum Beispiel eine Serie oder ein Pack für Freunde.', icon: 'folder-plus',
                              input: '', placeholder: tf('z. B. Family Guy'), required: true, ok: 'Anlegen', maxLength: 80 });
  if (!name) return null;
  try {
    const { id } = await api('POST', '/api/categories', { name });
    if (assignPid) await api('POST', `/api/projects/${encodeURIComponent(assignPid)}/category`, { category: id });
    projectSig = ''; await refreshState();
    return id;
  } catch (e) { toast(e.message, true); return null; }
}
async function moveToCategory(pid, cid) {
  try {
    await api('POST', `/api/projects/${encodeURIComponent(pid)}/category`, { category: cid || null });
    projectSig = ''; await refreshState();
  } catch (e) { toast(e.message, true); }
}
function categoryMenu(pid, anchor) {
  const cur = lastState?.projects.find(p => p.id === pid)?.category || null;
  const r = anchor.getBoundingClientRect();
  openMenu([
    ...(lastState?.categories || []).map(c => ({ label: c.name, icon: 'folder', checked: c.id === cur, action: () => moveToCategory(pid, c.id) })),
    (lastState?.categories || []).length ? { sep: true } : null,
    { label: tf('Ohne Kategorie'), checked: !cur, action: () => moveToCategory(pid, null) },
    { label: tf('Neue Kategorie…'), icon: 'folder-plus', action: () => newCategory(pid) },
  ], r.left, r.bottom + 4);
}
$('#btnAddCat').onclick = () => newCategory();

// Kopfzeile einer Kategorie: auf-/zuklappen, teilen, umbenennen, entfernen
projectEl.addEventListener('click', async e => {
  const head = e.target.closest('.cat-head');
  if (!head) return;
  const box = head.closest('.cat'), cid = box.dataset.cat, c = catById(cid);
  if (!c) return;
  if (e.target.closest('.cat-share')) return shareCategory(cid);
  if (e.target.closest('.cat-ren')) {
    const name = await dialog({ title: 'Kategorie umbenennen', input: c.name, required: true, ok: 'Umbenennen', maxLength: 80 });
    if (name && name !== c.name) {
      try { await api('PUT', `/api/categories/${cid}`, { name }); projectSig = ''; refreshState(); } catch (err) { toast(err.message, true); }
    }
    return;
  }
  if (e.target.closest('.cat-del')) {
    if (!await dialog({ title: tf('Kategorie „{}“ entfernen?', c.name), text: 'Die Projekte bleiben erhalten und stehen danach ohne Kategorie da.',
                        tone: 'danger', ok: 'Entfernen' })) return;
    try { await api('DELETE', `/api/categories/${cid}`); projectSig = ''; refreshState(); } catch (err) { toast(err.message, true); }
    return;
  }
  if (e.target.closest('button')) return;
  const collapsed = !box.classList.contains('collapsed');
  box.classList.toggle('collapsed', collapsed);   // sofort (animiert), gespeichert wird im Hintergrund
  c.collapsed = collapsed;
  try { await api('PUT', `/api/categories/${cid}`, { collapsed }); } catch { /* egal */ }
});

/* Ziehen in der Projektliste:
   Projekt auf ein anderes Projekt: davor/dahinter einsortieren (auch in eine andere Kategorie).
   Projekt auf eine Kategorie oder „Ohne Kategorie“: dorthin verschieben.
   Kategorie am Kopf ziehen: Kategorien umsortieren. Eine Linie zeigt, wo es landet;
   danach gleitet die Liste in die neue Ordnung (FLIP). */
const DRAG_TYPE = 'application/x-voicitool-project';
const DRAG_CAT = 'application/x-voicitool-category';
const drag = { kind: null, id: null, at: null };
const clearMarks = () => $$('#projectList .drop-target, #projectList .ins-before, #projectList .ins-after')
  .forEach(x => x.classList.remove('drop-target', 'ins-before', 'ins-after'));
projectEl.addEventListener('dragstart', e => {
  const head = e.target.closest?.('.cat-head');
  if (head) {
    const box = head.closest('.cat');
    Object.assign(drag, { kind: 'cat', id: box.dataset.cat, at: null });
    e.dataTransfer.setData(DRAG_CAT, box.dataset.cat);
    e.dataTransfer.effectAllowed = 'move';
    requestAnimationFrame(() => box.classList.add('dragging'));
    return;
  }
  const it = e.target.closest?.('.item[draggable="true"]');
  if (!it) return;
  Object.assign(drag, { kind: 'project', id: it.dataset.id, at: null });
  e.dataTransfer.setData(DRAG_TYPE, it.dataset.id);
  e.dataTransfer.effectAllowed = 'move';
  requestAnimationFrame(() => it.classList.add('dragging'));
});
projectEl.addEventListener('dragend', () => {
  $$('#projectList .dragging').forEach(x => x.classList.remove('dragging'));
  clearMarks();
  Object.assign(drag, { kind: null, id: null, at: null });
});
function dropSpot(e) {
  if (drag.kind === 'cat') {
    const box = e.target.closest?.('.cat');
    if (box && box.dataset.cat !== drag.id) {
      const r = box.getBoundingClientRect();
      return { kind: 'cat', el: box, before: e.clientY < r.top + r.height / 2 };
    }
    if (!box && e.target.closest?.('.cat-loose, .cat-loose-head')) {   // unter alle Kategorien
      const last = $$('#projectList .cat').filter(c => c.dataset.cat !== drag.id).pop();
      return last ? { kind: 'cat', el: last, before: false } : null;
    }
    return box ? 'self' : null;
  }
  const it = e.target.closest?.('.item[data-id]');
  if (it) {
    if (it.dataset.id === drag.id) return 'self';
    const r = it.getBoundingClientRect();
    return { kind: 'item', el: it, before: e.clientY < r.top + r.height / 2,
             cid: it.parentElement.closest('[data-cat]')?.dataset.cat || null };
  }
  const z = e.target.closest?.('.cat, .cat-loose, .cat-loose-head');
  return z ? { kind: 'zone', el: z, cid: z.dataset.cat || null } : null;
}
projectEl.addEventListener('dragover', e => {
  if (!drag.kind) return;
  const spot = dropSpot(e);
  if (!spot) { clearMarks(); drag.at = null; return; }
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  if (spot === 'self') { clearMarks(); drag.at = null; return; }
  const cls = spot.kind === 'zone' ? 'drop-target' : spot.before ? 'ins-before' : 'ins-after';
  if (!spot.el.classList.contains(cls) || drag.at?.el !== spot.el) { clearMarks(); spot.el.classList.add(cls); }
  drag.at = spot;
});
projectEl.addEventListener('drop', e => {
  if (!drag.kind) return;
  e.preventDefault();
  e.stopPropagation();
  const { kind, id } = drag;
  let at = dropSpot(e);   // genau dort, wo losgelassen wurde (das letzte dragover kann älter sein)
  if (at === 'self') return clearMarks();
  at = at || drag.at;
  clearMarks();
  if (!at) return;
  if (kind === 'cat') reorderCategory(id, at.el.dataset.cat, at.before);
  else if (at.kind === 'item') placeProject(id, at.el.dataset.id, at.before, at.cid);
  else if ((lastState?.projects.find(p => p.id === id)?.category || null) !== at.cid) {
    flipCapture('item');
    moveToCategory(id, at.cid);
  }
});

// Projekt vor/hinter ein anderes setzen. Grundlage ist die sichtbare Reihenfolge, damit alles andere
// genau da bleibt, wo man es gerade sieht. Sortierung wechselt dabei auf „Eigene Reihenfolge“.
async function placeProject(pid, targetId, before, cid) {
  const st = lastState;
  if (!st) return;
  const shown = $$('#projectList .item[data-id]').map(x => x.dataset.id);
  const hidden = viewProjects(st.projects.filter(p => !shown.includes(p.id)), st.order).map(p => p.id);
  const order = [...shown, ...hidden].filter(x => x !== pid);
  const i = order.indexOf(targetId);
  order.splice(i < 0 ? order.length : i + (before ? 0 : 1), 0, pid);
  const p = st.projects.find(x => x.id === pid);
  const oldCat = p?.category || null;
  flipCapture('item');
  if (p) p.category = cid;
  st.order = order;
  if (projView.sort !== 'custom') {
    projView.sort = 'custom';
    $('#projSort').value = 'custom';
    try { localStorage.setItem('vt.projSort', 'custom'); } catch { /* egal */ }
    toast(tf('Sortierung: Eigene Reihenfolge'));
  }
  projectSig = ''; renderProjects(st);
  try {
    await api('PUT', '/api/projects/order', { ids: order });
    if (oldCat !== cid) await api('POST', `/api/projects/${encodeURIComponent(pid)}/category`, { category: cid });
  } catch (err) { toast(err.message, true); }
  refreshState();
}
async function reorderCategory(cid, targetCid, before) {
  const st = lastState;
  if (!st || cid === targetCid) return;
  const ids = st.categories.map(c => c.id).filter(x => x !== cid);
  const i = ids.indexOf(targetCid);
  ids.splice(i < 0 ? ids.length : i + (before ? 0 : 1), 0, cid);
  flipCapture('cat');
  st.categories.sort((a, b) => ids.indexOf(a.id) - ids.indexOf(b.id));
  projectSig = ''; renderProjects(st);
  try { await api('PUT', '/api/categories/order', { ids }); } catch (err) { toast(err.message, true); }
  refreshState();
}

// FLIP: Positionen vor dem Neuzeichnen merken, danach jedes Element von dort in die neue Lage gleiten lassen
let flipBefore = null;
function flipCapture(kind) {
  if (!motionOK()) return;
  const sel = kind === 'cat' ? '#projectList .cat[data-cat]' : '#projectList .item[data-id]';
  const key = kind === 'cat' ? 'cat' : 'id';
  flipBefore = { sel, key, rects: new Map($$(sel).map(el => [el.dataset[key], el.getBoundingClientRect()])) };
}
function flipPlay() {
  if (!flipBefore) return;
  const { sel, key, rects } = flipBefore;
  flipBefore = null;
  for (const el of $$(sel)) {
    const a = rects.get(el.dataset[key]);
    if (!a) continue;
    const b = el.getBoundingClientRect(), z = el.currentCSSZoom || 1;
    const dx = (a.left - b.left) / z, dy = (a.top - b.top) / z;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
    el.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }],
               { duration: 340, easing: 'cubic-bezier(.3, 1.15, .45, 1)' });
  }
}

// Teilen: alle fertigen Projekte als Packs exportieren und in eine ZIP packen (oder ins Spiel installieren)
async function shareCategory(cid) {
  const c = catById(cid);
  const all = (lastState?.projects || []).filter(p => p.category === cid);
  const ready = all.filter(p => p.status === 'fertig');
  if (!ready.length) {
    return dialog({ title: 'Noch nichts zum Exportieren', text: 'In dieser Kategorie ist noch kein Projekt fertig verarbeitet.', icon: 'package', cancel: false });
  }
  const list = [{ level: 'info', text: tf('{} fertige Projekte werden als Packs exportiert.', ready.length) }];
  if (all.length > ready.length) list.push({ level: 'warn', text: tf('{} Projekte sind noch nicht fertig und werden übersprungen.', all.length - ready.length) });
  const choice = await dialog({
    title: tf('Kategorie „{}“ exportieren', c.name), icon: 'package', wide: true, list,
    text: 'Alle fertigen Projekte werden als Packs exportiert und zusammen in eine ZIP gepackt. Deine Freunde entpacken die Ordner in packs_voice des Spiels, eine Anleitung liegt in der ZIP.',
    buttons: [{ label: 'Abbrechen', value: null }, { label: 'Ins Spiel installieren', value: 'install' },
              { label: 'Als ZIP exportieren', value: 'zip', kind: 'primary', main: true }],
  });
  if (!choice) return;
  const install = choice === 'install';
  if (!(await preflight('category', { cid, install }))) return;
  runCategoryExport(cid, install, false, null);
}
async function runCategoryExport(cid, install, overwrite, only) {
  try {
    const { job } = await api('POST', `/api/categories/${cid}/export`, { install, overwrite, only });
    toast('Export läuft … (Fortschritt oben rechts)');
    refreshState();
    const r = await waitJob(job);
    if (install && !overwrite && r.exists?.length) {
      const again = await dialog({ title: 'Packs ersetzen?', tone: 'warn', icon: 'gamepad', ok: 'Ersetzen',
                                   html: `${esc(tf('Diese Packs gibt es schon im Spiel:'))}<br>${r.exists.map(x => `<code data-nolang>${esc(x.name)}</code>`).join('<br>')}` });
      if (again) return runCategoryExport(cid, true, true, r.exists.map(x => x.id));
    }
    const list = [{ level: 'ok', text: tf('{} Packs mit {} Clips exportiert.', r.packs, r.clips) }];
    if (install) list.push({ level: 'ok', text: tf('Davon {} im Spiel installiert.', r.installed) });
    (r.failed || []).forEach(f => list.push({ level: 'warn', text: `${f.name}: ${tf(f.error)}` }));
    const v = await dialog({ title: 'Fertig zum Teilen', icon: 'check-circle', wide: true, list,
                             html: r.zip ? `${esc(tf('Die ZIP liegt hier:'))}<br><code>${esc(r.zip)}</code>` : '',
                             buttons: [...(r.zip ? [{ label: 'Im Ordner zeigen', value: 'open', side: 'left' },
                                                    { label: 'Link erstellen (72 h)', value: 'link' }] : []),
                                       { label: 'OK', value: true, kind: 'primary', main: true }] });
    if (v === 'open') api('POST', '/api/open', { path: r.zip }).catch(err => toast(err.message, true));
    if (v === 'link') shareLink(r.zip);
  } catch (e) {
    if (e.message !== 'Abgebrochen') toast(e.message, true);
  }
}


projectEl.addEventListener('change', e => {
  if (e.target.classList.contains('retry-q')) retryQuality[e.target.closest('.item').dataset.id] = e.target.value;
});
projectEl.addEventListener('click', async e => {
  const it = e.target.closest('.item'); if (!it) return;
  const id = it.dataset.id, name = it.dataset.name;
  const done = () => { projectSig = ''; refreshState(); };
  if (e.target.closest('.copy-err')) {
    const p = lastState?.projects?.find(x => x.id === id);
    copyError(`Projekt „${name}“: ${p?.error || ''}${p?.error_detail ? '\n\n' + p.error_detail : ''}`);
    return;
  }
  if (e.target.closest('.goto-storage')) { openSettings('storage'); return; }
  if (e.target.closest('.goto-online')) { openSettings('online'); return; }
  if (e.target.closest('.retry-local') || e.target.closest('.retry-online')) {
    const on = !!e.target.closest('.retry-online');
    if (!await preflight('process', { pid: id, quality: retryQuality[id] || $('.retry-q', it)?.value, device: on ? 'online' : 'auto' })) return;
    try {
      await api('POST', `/api/projects/${encodeURIComponent(id)}/reprocess`, { online: on, quality: retryQuality[id] || $('.retry-q', it)?.value });
      toast(on ? tf('Online rechnen gestartet: {}.', onlineSummary()) : 'Verarbeitung neu gestartet.'); projectSig = ''; refreshState();
    } catch (err) { toast(err.message, true); }
    return;
  }
  if (e.target.closest('.cat-move')) { categoryMenu(id, e.target.closest('.cat-move')); return; }
  try {
    if (e.target.closest('.open')) openProject(id, e.target.closest('.open'));
    else if (e.target.closest('.cancel')) { const b = e.target.closest('.cancel'); await cancelJob(b.dataset.job, b.dataset.label); done(); }
    else if (e.target.closest('.retry') || e.target.closest('.retry-cpu')) {
      const cpuBtn = !!e.target.closest('.retry-cpu');
      const quality = retryQuality[id] || $('.retry-q', it)?.value;
      const pf = cpuBtn ? { device: 'cpu' } : await preflight('process', { pid: id, quality, device: 'auto' });
      if (!pf) return;
      await api('POST', `/api/projects/${encodeURIComponent(id)}/reprocess`, { quality, device: pf.device || 'auto' });
      toast('Verarbeitung neu gestartet.'); done();
    } else if (e.target.closest('.ren')) {
      const nn = await askProjectName(name);
      if (nn && nn !== name) { await api('POST', `/api/projects/${encodeURIComponent(id)}/rename`, { name: nn }); done(); }
    } else if (e.target.closest('.del')) {
      if (!await dialog({ title: tf('Projekt „{}" löschen?', name), text: 'Alle Bearbeitungen gehen verloren, das Video wandert zurück in „eingang".',
                         tone: 'danger', ok: 'Löschen' })) return;
      await api('DELETE', `/api/projects/${encodeURIComponent(id)}`); inboxSig = ''; done();
    }
  } catch (err) { toast(err.message, true); done(); }
});

function renderHome(st) {
  renderInbox(st);
  renderProjects(st);
}

/* ------------------------------------------------------------ Einstellungen */
// gespeichert in daten/einstellungen.json; hier die Werte, die gelten, solange nichts gespeichert ist
let SET = {};
let defaultGameDir = '';
const SET_DEFAULTS = {
  theme: 'system', accent: 'violett', custom_color: '#3f7cf0', reduce_motion: false, ui_scale: 'auto',
  default_quality: '', default_language: '', default_laugh: true,
  snap: true, follow: true, auto_text: true, confirm_delete: false, playback_rate: '1', usage_stats: true,
  export_video_height: 720, export_video_fps: 30, export_video_quality: 7, export_normalize: 'lautheit',
  export_image_mode: 'frame', export_keep_voices: true, check_updates: true, compute_device: 'auto', export_line_format: 'ini',
};
const setting = k => (SET[k] ?? SET_DEFAULTS[k]);

async function loadSettings() {
  try {
    const r = await api('GET', '/api/settings');
    SET = r.settings || {}; defaultGameDir = r.default_game_dir || '';
    statsSite = r.stats_site || ''; appBuild = r.build || 0;
    showVoicigame(r.voicigame);
  } catch { SET = {}; }
  const design = {};   // Design aus den gespeicherten Einstellungen übernehmen
  for (const k of ['theme', 'accent', 'custom_color', 'reduce_motion', 'ui_scale']) if (SET[k] !== undefined) design[k] = SET[k];
  if (Object.keys(design).length) window.VT_THEME?.apply(design);
  inboxSig = '';
  // Selbst gewählte Sprache dem Server melden (Text der Desktop-Verknüpfung, Startsprache des Fensters).
  // Ohne eigene Wahl bleibt es bei der Windows-Sprache und folgt ihr, wenn sie sich ändert.
  const lang = window.VT_I18N?.lang;
  if (lang && window.VT_I18N.chosen && SET.ui_lang !== lang) saveSetting('ui_lang', lang);
}
async function saveSetting(key, value) {
  SET[key] = value;
  try { await api('PUT', '/api/settings', { [key]: value }); } catch (e) { toast(e.message, true); }
}
loadSettings().then(() => {
  afterUpdateNote();
  countActive();
  setTimeout(maybeAskOnline, 2500);
  if (setting('check_updates')) checkUpdate(false);
});
setInterval(() => { if (setting('check_updates')) checkUpdate(false); }, 6 * 3600e3);   // lange offene Fenster

const modelJobs = {};   // Modell-ID -> laufender Download-Auftrag
let settingsSection = 'look';
async function openSettings(section) {
  const ov = $('#settings');
  const wasOpen = !ov.hidden;
  ov.getAnimations({ subtree: true }).forEach(a => a.cancel());
  ov.hidden = false;
  if (!wasOpen) zoomFrom($('.sheet', ov), $('#btnSettings'));
  await loadSettings();
  showSection(typeof section === 'string' ? section : settingsSection, !wasOpen);
}
async function closeSettings() {
  const ov = $('#settings');
  if (ov.hidden || ov.dataset.closing) return;
  const anim = zoomFrom($('.sheet', ov), $('#btnSettings'), true);
  if (anim) {
    ov.dataset.closing = '1';
    ov.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 260, easing: 'ease-in', fill: 'forwards' });
    // Zeitlimit: im minimierten Fenster laufen Animationen nicht weiter
    try { await Promise.race([anim.finished, new Promise(r => setTimeout(r, 450))]); } catch { /* abgebrochen */ }
    delete ov.dataset.closing;
  }
  ov.hidden = true;
  ov.getAnimations({ subtree: true }).forEach(a => a.cancel());
}
function showSection(sec, first = false) {
  const changed = sec !== settingsSection;
  settingsSection = sec;
  const btn = $(`#setNav button[data-sec="${sec}"]`);
  $$('#setNav button').forEach(b => b.classList.toggle('on', b === btn));
  $$('#setBody > section').forEach(s => (s.hidden = s.dataset.sec !== sec));
  $('#setTitle').textContent = btn.querySelector('span:last-child').textContent;
  $('#setBody').scrollTop = 0;
  const shown = $(`#setBody > section[data-sec="${sec}"]`);
  if (changed && !first && motionOK() && shown) {
    shown.animate([{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }], { duration: 240, easing: EASE_OUT });
  }
  fillSettings();
  if (sec === 'models') renderModels();
  if (sec === 'storage') renderStorage();
  if (sec === 'system') renderSystem();
  if (sec === 'online') renderOnline();
  if (sec === 'look') renderLook();
  if (sec === 'voicigame') renderVoicigame();
}
$('#btnSettings').onclick = () => openSettings();
$('#setClose').onclick = closeSettings;
$('#settings').addEventListener('click', e => { if (e.target.id === 'settings') closeSettings(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('#settings').hidden) closeSettings(); });
$('#setNav').addEventListener('click', e => { const b = e.target.closest('button[data-sec]'); if (b) showSection(b.dataset.sec); });

function fillSettings() {
  // einfache Felder mit data-setting
  $$('#setBody [data-setting]').forEach(el => {
    const v = setting(el.dataset.setting);
    if (el.type === 'checkbox') el.checked = !!v;
    else if (el.tagName === 'SELECT' && ![...el.options].some(o => o.value === String(v))) el.selectedIndex = 0;
    else el.value = String(v);
  });
  $('#setVq').textContent = setting('export_video_quality');
  // Sprache
  const langs = window.VT_I18N?.languages || [['en', 'English'], ['de', 'Deutsch']];
  $('#setLang').innerHTML = langs.map(([c, n]) => `<option value="${c}" ${c === window.VT_I18N?.lang ? 'selected' : ''}>${esc(n)}</option>`).join('');
  // Standards für neue Projekte
  const q = lastState?.quality || {};
  $('#setDefQuality').innerHTML = `<option value="">${esc(tf('Wie empfohlen (Standard)'))}</option>` +
    Object.entries(q).map(([k, l]) => `<option value="${k}" ${k === SET.default_quality ? 'selected' : ''}>${esc(tf(l))}</option>`).join('');
  $('#setDefLanguage').innerHTML = `<option value="">${esc(tf('Automatisch (je nach Sprachpaket)'))}</option>` +
    langOptions(SET.default_language);
  enhanceLangSelect($('#setDefLanguage'));
  $('#setDefLanguage').nextElementSibling?._paint?.();
  // Spielordner
  $('#setGameDir').value = SET.game_dir || defaultGameDir;
  // Updates und Über
  const ver = tf('Version {} · Build {}', lastState?.version || '', lastState?.build ?? '');
  $('#aboutVersion').textContent = ver;
  $('#updVersion').textContent = ver;
  $('#aboutRepo').innerHTML = lastState?.repo
    ? `<a href="https://github.com/${esc(lastState.repo)}" target="_blank" rel="noopener">github.com/${esc(lastState.repo)}</a>` : '';
}

$('#setBody').addEventListener('change', async e => {
  const el = e.target.closest('[data-setting]');
  if (!el) return;
  const key = el.dataset.setting;
  let value = el.type === 'checkbox' ? el.checked : el.value;
  if (typeof SET_DEFAULTS[key] === 'number') value = Number(value);
  await saveSetting(key, value);
  if (key === 'reduce_motion' || key === 'ui_scale') window.VT_THEME?.apply({ [key]: value });
  if (key === 'export_video_quality') $('#setVq').textContent = value;
  if (['snap', 'follow', 'auto_text', 'playback_rate'].includes(key)) applyEditorSettings();
  if (key.startsWith('default_')) inboxSig = '';
  toast(tf('Gespeichert.'), false, 1200);
});
$('#setBody').addEventListener('input', e => { if (e.target.dataset.setting === 'export_video_quality') $('#setVq').textContent = e.target.value; });
$('#setLang').onchange = e => window.VT_I18N?.setLang(e.target.value);
// Sprache gewechselt (Einstellungen oder Auswahl oben): merken und nachzeichnen, was nicht im DOM steht
document.addEventListener('vt-lang', e => {
  const l = e.detail.lang;
  $('#setLang').value = l;
  if (SET.ui_lang !== l) saveSetting('ui_lang', l);
  if (S.p) drawTimeline();
});

/* Darstellung: Farbschema und Akzentfarbe (wirken sofort) */
function renderLook() {
  const d = window.VT_THEME?.get() || {};
  $$('#setTheme button').forEach(b => b.classList.toggle('on', b.dataset.v === (d.theme || 'system')));
  const sys = window.matchMedia?.('(prefers-color-scheme: light)').matches ? tf('Hell') : tf('Dunkel');
  $('#themeHint').textContent = d.theme === 'system' || !d.theme ? tf('Folgt Windows (gerade: {})', sys) : '';
  const acc = window.VT_THEME?.accents || {};
  $$('#setAccent > button').forEach(b => {
    const c = acc[b.dataset.v];
    if (c) b.style.background = `linear-gradient(135deg, ${c[1]}, ${c[2]})`;
    b.classList.toggle('on', b.dataset.v === d.accent);
  });
  $('#setCustomDot').style.background = d.custom_color || '#3f7cf0';
  $('.swatch-custom').classList.toggle('on', d.accent === 'eigene');
}
async function setDesign(values, fromEl) {
  const r = fromEl?.getBoundingClientRect();
  const x = r ? r.left + r.width / 2 : innerWidth / 2, y = r ? r.top + r.height / 2 : innerHeight / 2;
  const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
  viewTransition('theme', () => { window.VT_THEME?.apply(values); renderLook(); },
                 { '--vt-x': x + 'px', '--vt-y': y + 'px', '--vt-r': radius + 'px' });
  for (const [k, v] of Object.entries(values)) await saveSetting(k, v);
}
$('#setTheme').addEventListener('click', e => { const b = e.target.closest('button[data-v]'); if (b) setDesign({ theme: b.dataset.v }, b); });
$('#setAccent').addEventListener('click', e => { const b = e.target.closest('button[data-v]'); if (b) setDesign({ accent: b.dataset.v }, b); });
$('#setCustomBtn').addEventListener('click', e => {
  const btn = e.currentTarget;
  const start = window.VT_THEME?.get().custom_color || '#3f7cf0';
  openColorPicker(btn, start, hex => {
    window.VT_THEME?.apply({ accent: 'eigene', custom_color: hex });
    $('#setCustomDot').style.background = hex;
  }, hex => {
    if (!hex) return;
    renderLook();
    saveSetting('accent', 'eigene'); saveSetting('custom_color', hex);
  });
});
document.addEventListener('vt-theme', () => { if (!$('#settings').hidden && settingsSection === 'look') renderLook(); });

/* Eigene Farbauswahl im Voicitool-Stil (statt des Windows-Farbdialogs): Farbfeld, Farbton-Leiste,
   Hex-Eingabe und Vorschläge. Die Vorschau wird höchstens einmal pro Bildaufbau angewendet, damit
   schnelles Ziehen flüssig bleibt; gespeichert wird erst beim Schließen. */
function hexToHsv(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || '');
  const n = m ? parseInt(m[1], 16) : 0x3f7cf0;
  const r = (n >> 16 & 255) / 255, g = (n >> 8 & 255) / 255, b = (n & 255) / 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
  let h = 0;
  if (d) h = max === r ? ((g - b) / d + 6) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return [h * 60, max ? d / max : 0, max];
}
function hsvToHex(h, s, v) {
  const f = k => { const x = (k + h / 60) % 6; return v - v * s * Math.max(0, Math.min(x, 4 - x, 1)); };
  return '#' + [f(5), f(3), f(1)].map(c => Math.round(c * 255).toString(16).padStart(2, '0')).join('');
}
function openColorPicker(anchor, initial, onInput, onDone) {
  document.querySelector('.cpick')?.remove();
  let [h, sat, val] = hexToHsv(initial);
  let hex = initial, pending = false, done = false;
  const PRESETS = ['#3f7cf0', '#7c5cff', '#e0457b', '#ff7a45', '#f5b700', '#22b573', '#10a3b8', '#8a8f98'];
  const el = document.createElement('div');
  el.className = 'cpick';
  el.innerHTML = `
    <div class="cp-sv"><div class="cp-thumb"></div></div>
    <div class="cp-hue"><div class="cp-hthumb"></div></div>
    <div class="cp-row"><span class="cp-prev"></span><input class="cp-hex" maxlength="7" spellcheck="false">
      <button type="button" class="btn small primary cp-ok">OK</button></div>
    <div class="cp-presets">${PRESETS.map(c => `<button type="button" data-c="${c}" style="background:${c}" title="${c}"></button>`).join('')}</div>`;
  // oberste Ebene (wird nie abgeschnitten), per CSS-Anker am Knopf: folgt ihm auch während Animationen
  el.setAttribute('popover', 'manual');
  anchor.style.anchorName = '--vt-cpick';
  document.body.appendChild(el);
  el.showPopover?.();
  const sv = $('.cp-sv', el), hue = $('.cp-hue', el), hexIn = $('.cp-hex', el);
  function paint(fromInput = false) {
    hex = hsvToHex(h, sat, val);
    sv.style.setProperty('--hue', `hsl(${h}, 100%, 50%)`);
    $('.cp-thumb', el).style.left = `${sat * 100}%`;
    $('.cp-thumb', el).style.top = `${(1 - val) * 100}%`;
    $('.cp-thumb', el).style.background = hex;
    $('.cp-hthumb', el).style.left = `${h / 360 * 100}%`;
    $('.cp-hthumb', el).style.background = `hsl(${h}, 100%, 50%)`;
    $('.cp-prev', el).style.background = hex;
    if (!fromInput) hexIn.value = hex;
    if (!pending) {   // höchstens einmal pro Bild anwenden
      pending = true;
      requestAnimationFrame(() => { pending = false; onInput(hex); });
    }
  }
  function drag(area, fn) {
    area.addEventListener('pointerdown', e => {
      area.setPointerCapture(e.pointerId);
      const move = ev => { const r = area.getBoundingClientRect(); fn(clamp((ev.clientX - r.left) / r.width, 0, 1), clamp((ev.clientY - r.top) / r.height, 0, 1)); paint(); };
      move(e);
      const up = () => { area.removeEventListener('pointermove', move); area.removeEventListener('pointerup', up); };
      area.addEventListener('pointermove', move);
      area.addEventListener('pointerup', up);
    });
  }
  drag(sv, (x, y) => { sat = x; val = 1 - y; });
  drag(hue, x => { h = x * 359.9; });
  hexIn.addEventListener('input', () => {
    const v = hexIn.value.trim();
    if (/^#?[0-9a-f]{6}$/i.test(v)) { [h, sat, val] = hexToHsv(v.startsWith('#') ? v : '#' + v); paint(true); }
  });
  el.addEventListener('click', e => {
    const c = e.target.closest('[data-c]');
    if (c) { [h, sat, val] = hexToHsv(c.dataset.c); paint(); }
    if (e.target.closest('.cp-ok')) close(true);
  });
  function close(keep) {
    if (done) return;
    done = true;
    document.removeEventListener('pointerdown', outside, true);
    window.removeEventListener('keydown', key, true);
    el.classList.add('closing');
    setTimeout(() => el.remove(), 150);
    if (!keep) onInput(initial);
    onDone(keep ? hex : null);
  }
  const outside = e => { if (!el.contains(e.target) && !anchor.contains(e.target)) close(true); };
  const key = e => {
    if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); close(false); }
    if (e.key === 'Enter' && document.activeElement === hexIn) { e.preventDefault(); close(true); }
  };
  document.addEventListener('pointerdown', outside, true);
  window.addEventListener('keydown', key, true);
  if (!CSS.supports('position-anchor: --a')) {   // ältere Chromium-Versionen: einmal messen
    const r = anchor.getBoundingClientRect();
    el.style.left = `${clamp(r.left, 8, innerWidth - 252)}px`;
    el.style.top = `${clamp(r.bottom + 8, 8, innerHeight - 300)}px`;
  }
  paint();
}

/* Export: Pack-Ordner des Spiels */
$('#setGameDir').addEventListener('change', async e => {
  const v = e.target.value.trim();
  await saveSetting('game_dir', v === defaultGameDir ? '' : v);
});
$('#setGameDirReset').onclick = async () => { await saveSetting('game_dir', ''); $('#setGameDir').value = defaultGameDir; toast(tf('Gespeichert.'), false, 1200); };

/* Spiel-Mod voicigame (Mitspielen am Handy und im Browser): installieren, aktualisieren, entfernen.
   Nur sichtbar, wenn der Schalter in app/config.py an ist oder der Mod schon installiert ist. */
function showVoicigame(on) {
  $('#setNav [data-sec="voicigame"]').hidden = !on;
  $('#btnVoicigame').hidden = !on;
  if (!on && settingsSection === 'voicigame') settingsSection = 'look';
}
async function renderVoicigame() {
  let s;
  try { s = await api('GET', '/api/voicigame', undefined, 30000); }
  catch (e) {
    $('#vgState').textContent = e.message;
    ['#vgInstall', '#vgUpdate', '#vgRemove', '#vgPick'].forEach(q => ($(q).disabled = false));
    return;
  }
  if (!s.visible) { showVoicigame(false); if (!$('#settings').hidden) showSection('look'); return; }
  const ready = s.bundled && s.game_dir && s.exe;
  let state = tf('Nicht installiert.'), extra = '';
  if (!s.bundled) state = tf('Die Mod-Dateien fehlen in Voicitool. Bitte unter Über Voicitool die Installation prüfen.');
  else if (!ready) state = tf('Spiel nicht gefunden. Wähle die exe mit „compatibility“ im Namen.');
  else if (s.installed) {
    state = tf('Installiert, Version {}.', s.installed_version || s.version || '?');
    if (!s.up_to_date) extra = tf('Ein Update liegt bereit.');
  } else if (s.missing_files) state = tf('Die Mod-Dateien fehlen. Bitte neu installieren.');
  else if (s.other_entry) {
    state = tf('Im Spiel ist Voicigame aus einem anderen Ordner eingetragen: {}', s.other_entry);
    extra = tf('Beim Installieren wird dieser Eintrag ersetzt.');
  }
  $('#vgState').textContent = state;
  $('#vgState2').textContent = extra;
  $('#vgState2').hidden = !extra;
  $('#vgInstall').hidden = s.installed;
  $('#vgInstall').disabled = !ready;
  $('#vgUpdate').hidden = !s.installed;
  $('#vgUpdate').disabled = !ready;
  $('#vgUpdate').classList.toggle('primary', s.installed && !s.up_to_date);
  $('#vgRemove').hidden = !s.removable;
  $('#vgRemove').disabled = $('#vgPick').disabled = false;
  if (document.activeElement !== $('#vgGameDir')) $('#vgGameDir').value = s.game_dir || '';
  // weitere gefundene Spielordner (z. B. zwei Fassungen des Spiels): ein Klick wählt ihn
  const others = (s.found || []).filter(p => p.toLowerCase() !== (s.game_dir || '').toLowerCase());
  $('#vgOtherList').innerHTML = others.map(p => `<button class="btn small quiet" data-dir="${esc(p)}">${esc(p)}</button>`).join('');
  $('#vgOthers').hidden = !others.length;
  $('#vgFound').hidden = s.source !== 'found';
  $('#vgOpen').disabled = !s.exe;
}
async function setVoicigameDir(path) {
  try {
    await api('POST', '/api/voicigame/game-dir', { path });
    toast(tf('Spielordner gespeichert.'), false, 1500);
  } catch (e) { toast(e.message, true); }
  renderVoicigame();
}
async function voicigameAction(what) {
  if (what === 'remove' && !await dialog({ title: 'Voicigame entfernen?', tone: 'danger', ok: 'Entfernen',
    text: 'Der Eintrag in override.cfg wird entfernt, die Mod-Dateien kommen in den Papierkorb.' })) return;
  const btns = ['#vgInstall', '#vgUpdate', '#vgRemove', '#vgPick'].map(s => $(s));
  btns.forEach(b => (b.disabled = true));
  try {
    await api('POST', `/api/voicigame/${what === 'remove' ? 'remove' : 'install'}`);
    toast(tf({ install: 'Voicigame ist installiert. Es startet mit dem nächsten Spielstart.',
               update: 'Voicigame ist aktualisiert.', remove: 'Voicigame ist entfernt.' }[what]));
  } catch (e) { toast(e.message, true); }
  renderVoicigame();
}
$('#vgInstall').onclick = () => voicigameAction('install');
$('#vgUpdate').onclick = () => voicigameAction('update');
$('#vgRemove').onclick = () => voicigameAction('remove');
$('#vgGameDir').addEventListener('change', e => { const v = e.target.value.trim(); if (v) setVoicigameDir(v); });
$('#vgPick').onclick = async () => {
  const cur = $('#vgGameDir').value.trim();
  let path = null;
  const pick = window.pywebview?.api?.pick_game_exe;   // im Voicitool-Fenster: Windows-Dialog
  if (pick) {
    try { path = await pick(cur); } catch (e) { toast(e.message, true); return; }
  } else {   // im Browser: Pfad eintippen oder einfügen
    path = await dialog({ title: 'Spielordner', text: 'Pfad zum Spielordner oder zur exe mit „compatibility“ im Namen.',
                          input: cur, placeholder: 'C:\\…\\The Choicer Voicer', maxLength: 500, icon: 'folder' });
  }
  if (path) setVoicigameDir(path);
};
$('#vgOtherList').addEventListener('click', e => { const b = e.target.closest('[data-dir]'); if (b) setVoicigameDir(b.dataset.dir); });
$('#vgOpen').onclick = async () => { try { await api('POST', '/api/voicigame/open'); } catch (e) { toast(e.message, true); } };
$('#btnVoicigame').onclick = () => openSettings('voicigame');

/* Ordner öffnen (Speicher, Protokolle, Spielordner) */
$('#settings').addEventListener('click', async e => {
  const b = e.target.closest('[data-open]');
  if (!b) return;
  try { await api('POST', '/api/open-folder', { which: b.dataset.open }); } catch (err) { toast(err.message, true); }
});

/* System: Hardware, Hinweise, Rechnen auf Grafikkarte oder Prozessor */
const LEVEL_ICON = { block: 'blocked', warn: 'warning', info: 'info', ok: 'check-circle' };
function healthList(el, items) {
  el.innerHTML = '';
  const list = items.length ? items : [{ level: 'ok', text: tf('Alles in Ordnung. Dieser PC ist gut gerüstet.') }];
  for (const it of list) {
    const li = document.createElement('li');
    li.className = it.level;
    li.innerHTML = '<span class="li-icon"></span><span><span class="li-text"></span><span class="li-sub"></span></span>';
    $('.li-icon', li).innerHTML = ic(LEVEL_ICON[it.level] || 'info');
    $('.li-text', li).textContent = it.text;
    if (it.sub) $('.li-sub', li).textContent = it.sub; else $('.li-sub', li).remove();
    el.appendChild(li);
  }
}
const gb = v => (v == null ? '?' : (v < 10 ? v.toLocaleString(window.VT_I18N?.lang || 'en', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) : Math.round(v)) + ' GB');
function meter(used, total) {
  if (!total) return '';
  const f = Math.max(0, Math.min(1, used / total));
  return `<div class="meter ${f > 0.9 ? 'hot' : ''}"><div style="width:${(f * 100).toFixed(0)}%"></div></div>`;
}
async function renderSystem(recheck = false) {
  const grid = $('#sysGrid');
  if (recheck) grid.innerHTML = `<div class="set-note">${esc(tf('Wird geprüft …'))}</div>`;
  let r;
  try { r = await api('GET', '/api/system' + (recheck ? '?recheck=1' : ''), undefined, 90000); }
  catch (e) { grid.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  const i = r.info, g = i.gpu;
  const devText = i.device === 'gpu' ? tf('Grafikkarte') : tf('Prozessor');
  const rows = [
    [tf('Grafikkarte'), g ? `${esc(g.name)} · ${gb(g.vram_total)} · ${esc(tf('Treiber {}', g.driver))}`
                         : esc((i.gpus || []).map(x => x.name).join(', ') || tf('keine gefunden'))],
    ...(g ? [[tf('Grafikspeicher'), `${esc(tf('{} belegt, {} frei', gb(g.vram_used), gb(g.vram_free)))}${meter(g.vram_used, g.vram_total)}`]] : []),
    [tf('Arbeitsspeicher'), `${esc(tf('{} gesamt, {} frei', gb(i.ram.total), gb(i.ram.free)))}${meter(i.ram.total - i.ram.free, i.ram.total)}`],
    [tf('Prozessor'), `${esc(i.cpu.name)} · ${esc(tf('{} Kerne', i.cpu.cores))}`],
    [tf('Speicherplatz'), `${esc(tf('{} frei auf {}', gb(i.disk.free), i.disk.drive))}${meter(i.disk.total - i.disk.free, i.disk.total)}`],
    [tf('KI rechnet auf'), `<b>${esc(devText)}</b>${i.gpu_check_running ? ' · ' + esc(tf('Grafikkarte wird noch geprüft …')) : ''}`],
  ];
  grid.innerHTML = rows.map(([k, v]) => `<div class="k">${esc(k)}</div><div data-nolang>${v}</div>`).join('');
  healthList($('#sysHealth'), r.health);
  if (i.gpu_check_running) setTimeout(() => { if (!$('#settings').hidden && settingsSection === 'system') renderSystem(); }, 3000);
}
$('#btnSysCheck').onclick = async e => {
  const b = e.currentTarget; b.disabled = true;
  try { await renderSystem(true); } finally { b.disabled = false; }
};
$('#btnShowWarnings').onclick = () => {
  try { localStorage.removeItem(PF_HIDDEN); } catch { /* egal */ }
  toast(tf('Alle Warnungen werden wieder gezeigt.'));
};

/* Systemcheck beim Start: Hinweis oben, wenn etwas nicht passt (einmal ausblendbar je Befund) */
async function startupHealth() {
  let r;
  try { r = await api('GET', '/api/system', undefined, 30000); } catch { return; }
  const bad = r.health.filter(h => h.level === 'warn' || h.level === 'block');
  let hidden = '';
  try { hidden = localStorage.getItem('vt.healthHidden') || ''; } catch { /* egal */ }
  const sig = bad.map(h => h.id).sort().join(',');
  if (!bad.length || sig === hidden) return;
  $('#healthText').textContent = bad.length === 1 ? tf(bad[0].text) : tf('{} Hinweise zu diesem PC.', bad.length);
  $('#healthNote').dataset.sig = sig;
  $('#healthNote').hidden = false;
  // läuft die Grafikkarten-Prüfung noch, später erneut schauen
  if (r.info.gpu_check_running) setTimeout(startupHealth, 8000);
}
$('#healthShow').onclick = () => { $('#healthNote').hidden = true; openSettings('system'); };
$('#healthHide').onclick = () => {
  try { localStorage.setItem('vt.healthHidden', $('#healthNote').dataset.sig || ''); } catch { /* egal */ }
  $('#healthNote').hidden = true;
};
setTimeout(startupHealth, 2500);

/* Speicher: Größe je Bereich, Zwischenspeicher leeren */
const STORAGE_ROWS = [
  ['projects', 'Projekte', 'Deine Projekte mit Stimmen, Hintergrund und Analyse.'],
  ['export', 'Exporte', 'Fertige Packs und ZIP-Dateien.'],
  ['inbox', 'Eingang', 'Videos, die noch nicht verarbeitet sind.'],
  ['models', 'KI-Modelle', 'Sprachpakete und Modelle für Trennung, Sprecher und Lachen.'],
  ['cache', 'Zwischenspeicher', 'Export-Videos und Download-Reste. Wird bei Bedarf neu erzeugt.'],
  ['packages', 'Paket-Downloads', 'Heruntergeladene Python-Pakete. Nur für Reparaturen nötig.'],
];
async function renderStorage() {
  $('#storageList').innerHTML = `<div class="set-note">${esc(tf('Wird berechnet …'))}</div>`;
  let st;
  try { st = await api('GET', '/api/storage'); } catch (e) { $('#storageList').textContent = e.message; return; }
  const size = mb => (mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB` : `${mb} MB`);
  $('#storageRoot').textContent = st.root;
  $('#storageList').innerHTML = STORAGE_ROWS.map(([k, name, hint]) => `<div class="set-row">
      <div><div class="set-label">${esc(tf(name))}</div><div class="set-hint">${esc(tf(hint))}</div></div>
      <span class="row-inline"><span class="size">${size(st[k] || 0)}</span>
        ${['cache', 'packages'].includes(k) ? `<button class="btn small" data-clear="${k}" ${st[k] ? '' : 'disabled'}>${esc(tf('Leeren'))}</button>`
          : `<button class="btn small" data-open="${k}">${esc(tf('Öffnen'))}</button>`}</span>
    </div>`).join('');
}
$('#storageList').addEventListener('click', async e => {
  const b = e.target.closest('[data-clear]');
  if (!b) return;
  if (b.dataset.clear === 'packages' && !await dialog({ title: 'Paket-Downloads löschen?', text: 'Eine spätere Reparatur lädt sie dann neu.',
                                                         tone: 'danger', ok: 'Löschen' })) return;
  b.disabled = true;
  try {
    const r = await api('POST', '/api/storage/clear', { what: b.dataset.clear });
    toast(tf('{} MB freigegeben.', r.freed_mb));
  } catch (err) { toast(err.message, true); }
  renderStorage();
});

/* Anonyme Zählung: höchstens einmal am Tag „aktiv“ plus Build-Nummer an GoatCounter (keine Namen, Dateien oder
   Kennungen). Läuft aus der Oberfläche, damit nur wirklich benutzte Installationen zählen. Abschaltbar unter
   Einstellungen → Updates. Der Seitenname steht in app/version.json („stats“), leer = aus. */
let statsSite = '', appBuild = 0;
function countActive() {
  if (!statsSite || !setting('usage_stats') || !/^[a-z0-9-]+$/.test(statsSite)) return;
  const day = new Date().toISOString().slice(0, 10);
  try { if (localStorage.getItem('vt.countDay') === day) return; localStorage.setItem('vt.countDay', day); } catch { return; }
  const url = `https://${statsSite}.goatcounter.com/count?p=${encodeURIComponent('/build/' + appBuild)}&t=Voicitool&rnd=${Math.random().toString(36).slice(2)}`;
  fetch(url, { mode: 'no-cors', credentials: 'omit', referrerPolicy: 'no-referrer', keepalive: true }).catch(() => {});
}

/* Updates: Hinweis oben, wenn auf GitHub eine neuere Version liegt („Jetzt aktualisieren“ startet über
   Voicitool.exe neu). Nach einem Update einmal „Was ist neu?“. Die Änderungen stehen in app/changelog.json. */
let updInfo = null;
function changeItems(entries) {
  return entries.flatMap(e => (e.changes || []).map(c => ({ text: c, sub: `Build ${e.build}${e.date ? ' · ' + e.date : ''}`, level: 'info' })));
}
function showChanges(entries, title) {
  return dialog({ title, icon: 'sparkles', wide: true, cancel: false, ok: 'OK', list: changeItems(entries),
                  text: entries.length ? '' : 'Noch keine Änderungen eingetragen.' });
}
async function checkUpdate(manual) {
  let r;
  try { r = await api('GET', '/api/update-check'); } catch (e) { if (manual) throw e; return null; }
  updInfo = r;
  let hidden = null;
  try { hidden = localStorage.getItem('vt.updHidden'); } catch { /* egal */ }
  if (r.update_available && (manual || String(r.latest) !== hidden)) {
    $('#updText').textContent = tf('Build {} ist da. Voicitool startet dafür kurz neu.', r.latest);
    $('#updNews').hidden = !r.changes?.length;
    $('#updNote').hidden = false;
  }
  return r;
}
async function applyUpdate(force = false) {
  if (updInfo && !updInfo.can_apply) {
    await dialog({ title: 'Update', icon: 'download', text: 'Bitte Voicitool neu starten, dann wird das Update installiert.', cancel: false });
    return;
  }
  try {
    await api('POST', '/api/update/apply', { force });
    $('#updNote').hidden = true;
    toast(tf('Voicitool wird aktualisiert und startet gleich neu …'), false, 10000);
  } catch (e) {
    if (e.status !== 409) { toast(e.message, true); return; }
    const ok = await dialog({ title: 'Jetzt aktualisieren?', tone: 'warn', ok: 'Trotzdem aktualisieren',
                              text: tf('Es läuft noch: {}. Beim Aktualisieren wird das abgebrochen.', e.message) });
    if (ok) applyUpdate(true);
  }
}
async function afterUpdateNote() {
  let seen = 0;
  try { seen = Number(localStorage.getItem('vt.seenBuild')) || 0; } catch { /* egal */ }
  let r;
  try { r = await api('GET', '/api/changelog'); } catch { return; }
  try { localStorage.setItem('vt.seenBuild', String(r.current)); } catch { /* egal */ }
  const news = r.entries.filter(e => e.build > seen && e.build <= r.current);
  if (!seen || !news.length) return;   // erster Start: nichts zeigen
  $('#newsText').textContent = tf('Jetzt auf Build {}.', r.current);
  $('#newsNote').hidden = false;
  $('#newsShow').onclick = () => { $('#newsNote').hidden = true; showChanges(news, tf('Neu in Build {}', r.current)); };
}
$('#updApply').onclick = () => applyUpdate();
$('#updNews').onclick = () => showChanges(updInfo?.changes || [], tf('Neu in Build {}', updInfo?.latest));
$('#updLater').onclick = () => {
  try { localStorage.setItem('vt.updHidden', String(updInfo?.latest)); } catch { /* egal */ }
  $('#updNote').hidden = true;
};
$('#newsHide').onclick = () => { $('#newsNote').hidden = true; };
$('#btnNews').onclick = async () => {
  try { const r = await api('GET', '/api/changelog'); showChanges(r.entries, 'Was ist neu?'); } catch (e) { toast(e.message, true); }
};
$('#btnUpdCheck').onclick = async () => {
  const out = $('#updResult');
  out.textContent = tf('Suche nach Updates …');
  try {
    const r = await checkUpdate(true);
    if (!r.configured) out.textContent = tf('Für diese Version ist noch kein Update-Server eingetragen.');
    else if (r.update_available) out.textContent = tf('Neue Version verfügbar: Build {}.', r.latest);
    else out.textContent = tf('Voicitool ist auf dem neuesten Stand.');
  } catch (err) { out.textContent = err.message; }
};

/* Editor-Einstellungen anwenden (beim Start und wenn sie sich ändern) */
function applyEditorSettings() {
  if ($('#follow')) $('#follow').checked = !!setting('follow');
  if ($('#autoText')) $('#autoText').checked = !!setting('auto_text');
  const rate = String(setting('playback_rate'));
  if ($('#rate') && [...$('#rate').options].some(o => o.value === rate)) { $('#rate').value = rate; video.playbackRate = Number(rate); }
}

const WHISPER_NAMES = { 'distil-large-v3.5': 'Englisch (distil-large-v3.5)', 'large-v3-turbo': 'Alle Sprachen, schnell (large-v3-turbo)', 'large-v3': 'Alle Sprachen, beste Qualität (large-v3)' };
async function renderModels() {
  let data;
  try { data = await api('GET', '/api/models'); } catch (e) { $('#modelList').textContent = e.message; return; }
  let group = '';
  $('#modelList').innerHTML = data.models.map(m => {
    const head = m.group !== group ? `<div class="model-group">${esc(tf(m.group))}</div>` : '';
    group = m.group;
    const job = modelJobs[m.id];
    const size = m.installed ? `${m.disk_mb} MB` : `~${m.mb} MB`;
    const state = job ? tf('wird geladen …') : m.installed ? tf('installiert') : tf('nicht installiert');
    const btn = job ? '' : m.installed
      ? (m.required ? '' : `<button class="btn small danger m-del" data-id="${m.id}">${esc(tf('Löschen'))}</button>`)
      : `<button class="btn small primary m-get" data-id="${m.id}">${esc(tf('Laden'))}</button>`;
    return `${head}<div class="model" data-id="${m.id}">
      <div class="m-name">${esc(tf(m.name))}</div>
      <div class="m-side"><span class="m-state ${m.installed ? 'ok' : ''}">${m.installed && !job ? ic('check') : ''}${esc(state)} · ${esc(size)}</span>${btn}</div>
      <div class="m-desc">${esc(tf(m.desc))}</div>
      ${job ? `<div class="bar"><div style="width:${Math.round((job.pct || 0) * 100)}%"></div></div>` : ''}
    </div>`;
  }).join('');
  const choice = data.settings.whisper_model || 'auto';
  $('#setWhisper').innerHTML = [`<option value="auto">${esc(tf('Automatisch (empfohlen)'))}</option>`]
    .concat(data.whisper_installed.map(r => `<option value="${r}" ${r === choice ? 'selected' : ''}>${esc(tf(WHISPER_NAMES[r] || r))}</option>`)).join('');
}
$('#modelList').addEventListener('click', async e => {
  const get = e.target.closest('.m-get'), del = e.target.closest('.m-del');
  if (get) {
    const id = get.dataset.id;
    if (!(await preflight('model', { mid: id }))) return;
    try {
      const { job } = await api('POST', `/api/models/${id}/download`);
      modelJobs[id] = { id: job, pct: 0 };
      renderModels();
      for (;;) {
        await new Promise(r => setTimeout(r, 800));
        const j = await api('GET', `/api/jobs/${job}`);
        modelJobs[id].pct = j.pct;
        const bar = $(`.model[data-id="${id}"] .bar > div`);
        if (bar) bar.style.width = `${Math.round(j.pct * 100)}%`;
        const st = $(`.model[data-id="${id}"] .m-state`);
        if (st && j.message) st.textContent = j.message;
        if (j.state === 'fertig') { toast(tf('{} ist installiert.', tf(get.closest('.model').querySelector('.m-name').textContent))); break; }
        if (j.state === 'fehler') throw new Error(j.error);
        if (j.state === 'abgebrochen') break;
      }
    } catch (err) { toast(err.message, true); }
    delete modelJobs[id];
    inboxSig = ''; refreshState(); renderModels();
  } else if (del) {
    const name = del.closest('.model').querySelector('.m-name').textContent;
    if (!await dialog({ title: tf('„{}“ löschen?', name), text: 'Du kannst es später wieder laden.', tone: 'danger', ok: 'Löschen' })) return;
    try { await api('DELETE', `/api/models/${del.dataset.id}`); toast(tf('Gelöscht.')); }
    catch (err) { toast(err.message, true); }
    inboxSig = ''; refreshState(); renderModels();
  }
});
$('#setWhisper').onchange = async e => {
  try { await api('PUT', '/api/settings', { whisper_model: e.target.value }); toast(tf('Gespeichert.')); }
  catch (err) { toast(err.message, true); }
};
$('#btnSetupCheck').onclick = async () => {
  try { await api('POST', '/api/system/setup'); toast(tf('Die Prüfung startet in einem eigenen Fenster.')); }
  catch (err) { toast(err.message, true); }
};
$('#btnUninstall').onclick = async () => {
  if (!await dialog({ title: 'Voicitool deinstallieren?', text: 'Im nächsten Schritt kannst du wählen, ob deine Projekte bleiben.',
                     tone: 'danger', ok: 'Weiter' })) return;
  try { await api('POST', '/api/system/uninstall'); }
  catch (err) { toast(err.message, true); }
};

/* Meldung unter der Ablagefläche („… liegt im Eingang“): verschwindet von selbst, sobald das Video nicht mehr
   im Eingang liegt (gelöscht oder verarbeitet), spätestens nach ttl Millisekunden. */
const uploadNote = { file: null, timer: 0 };
function setUploadState(text, { file = null, ttl = 0 } = {}) {
  const el = $('#uploadState');
  clearTimeout(uploadNote.timer);
  uploadNote.file = file;
  el.classList.remove('fading');
  el.textContent = text;
  if (ttl) uploadNote.timer = setTimeout(clearUploadState, ttl);
}
function clearUploadState() {
  const el = $('#uploadState');
  clearTimeout(uploadNote.timer);
  uploadNote.file = null;
  if (!el.textContent) return;
  if (!motionOK()) { el.textContent = ''; return; }
  el.classList.add('fading');
  setTimeout(() => { if (el.classList.contains('fading')) { el.textContent = ''; el.classList.remove('fading'); } }, 320);
}


/* ------------------------------------------------------------ Online rechnen
   Kostenlose Dienste übernehmen Stimmen trennen (MVSEP) und Text erkennen (Groq, Cloudflare oder Gemini).
   Schlüssel bleiben auf dem PC, die Oberfläche sieht nur ihr Ende. */
let onlineState = null;
async function loadOnline() {
  try { onlineState = await api('GET', '/api/online'); } catch { onlineState = null; }
  return onlineState;
}
const onlineReady = () => !!(onlineState?.ready && (onlineState.ready.separate || onlineState.ready.transcribe));
const ONLINE_NAMES = { mvsep: 'MVSEP', groq: 'Groq', cloudflare: 'Cloudflare', gemini: 'Gemini' };
/* Dienste zum Text erkennen, beste zuerst (gemessen an 3 Referenz-Packs, je 1-Minuten-Stücke): Groq und Cloudflare gleich
   genau (13,3 / 13,4 % Wortfehler), Groq 2 bis 4 Mal schneller mit größerem Kontingent; Gemini deutlich ungenauer */
const ONLINE_MODELS = {
  groq: { model: 'Whisper large-v3', note: 'Am schnellsten, so genau wie auf dem PC, 8 Stunden Ton am Tag.' },
  cloudflare: { model: 'Whisper large-v3-turbo', note: 'Genauso genau, etwas langsamer, 3,5 Stunden Ton am Tag.' },
  gemini: { model: 'Gemini 3.5 Transcribe', note: 'Deutlich ungenauer, kleines Tageslimit. Google darf die Tonspur auswerten.' },
};
function asrChoices(withLocal = true) {
  const st = onlineState;
  const set = ['groq', 'cloudflare', 'gemini'].filter(s => st?.services?.[s]?.set);
  const out = set.map((s, i) => ({ value: s, label: `${ONLINE_NAMES[s]} · ${ONLINE_MODELS[s].model}${i === 0 ? ' · ' + tf('empfohlen') : ''}`,
                                   note: tf(ONLINE_MODELS[s].note) }));
  if (withLocal && st?.ready?.separate) {
    out.push({ value: 'local', label: tf('Text auf diesem PC erkennen'), note: tf('Nur die Stimmen werden online getrennt, den Text erkennt dein PC.') });
  }
  return out;
}
const defaultAsr = () => onlineState?.ready?.transcribe || (onlineState?.ready?.separate ? 'local' : '');
function onlineSummary(asr) {
  const r = onlineState?.ready || {}, parts = [];
  const tr = asr === 'local' ? null : (asr && onlineState?.services?.[asr]?.set ? asr : r.transcribe);
  if (r.separate) parts.push(tf('Stimmen trennen bei {}', 'MVSEP'));
  if (tr) parts.push(tf('Text erkennen bei {}', ONLINE_NAMES[tr]));
  return parts.join(', ');
}
const ONLINE_INFO = {
  mvsep: {
    role: 'Stimmen trennen',
    desc: 'Trennt Stimmen und Hintergrund mit demselben KI-Modell wie Voicitool (BS-RoFormer). Kostenlos: 50 Videos am Tag, bis 10 Minuten je Stück. Längere Videos zählen doppelt.',
    steps: [['Auf mvsep.com ein kostenloses Konto anlegen und die E-Mail bestätigen.', 'https://mvsep.com/register'],
            ['Eingeloggt die API-Seite öffnen. Dort steht dein API-Token.', 'https://mvsep.com/en/full_api'],
            ['Den Token hier einfügen und auf „Speichern und prüfen“ klicken.']],
    fields: [['token', 'API-Token']],
  },
  groq: {
    role: 'Text erkennen (empfohlen)',
    desc: 'Erkennt den Text mit Whisper large-v3, dem Modell, das Voicitool auch auf dem PC nutzt. Kostenlos: 8 Stunden Ton am Tag. Groq speichert die Tonspur nicht und trainiert nicht damit.',
    steps: [['Auf console.groq.com anmelden (Google, GitHub oder E-Mail).', 'https://console.groq.com/login'],
            ['Unter „API Keys“ auf „Create API Key“ klicken und den Schlüssel kopieren. Er wird nur einmal angezeigt.', 'https://console.groq.com/keys'],
            ['Den Schlüssel (beginnt mit gsk_) hier einfügen und prüfen.']],
    fields: [['key', 'API-Schlüssel']],
  },
  cloudflare: {
    role: 'Text erkennen (Alternative)',
    desc: 'Whisper large-v3-turbo über Cloudflare Workers AI. Kostenlos: etwa 3,5 Stunden Ton am Tag. Cloudflare trainiert nicht mit deinen Daten.',
    steps: [['Auf dash.cloudflare.com ein kostenloses Konto anlegen.', 'https://dash.cloudflare.com/sign-up'],
            ['Deine Account-ID kopieren: Sie steht nach dem Einloggen in der Adresszeile (dash.cloudflare.com/…) und unter „Workers AI“ → „REST API verwenden“.', 'https://dash.cloudflare.com/?to=/:account/ai/workers-ai'],
            ['Unter „Mein Profil“ → „API-Tokens“ ein Token mit der Vorlage „Workers AI“ erstellen.', 'https://dash.cloudflare.com/profile/api-tokens'],
            ['Account-ID und Token hier einfügen und prüfen.']],
    fields: [['account', 'Account-ID'], ['token', 'API-Token']],
  },
  gemini: {
    role: 'Text erkennen (optional)',
    desc: 'Gemini 3.5 Transcribe erkennt den Text und hört dabei, wer spricht. Das tägliche Gratis-Limit veröffentlicht Google nicht, du siehst es in AI Studio.',
    steps: [['In Google AI Studio anmelden.', 'https://aistudio.google.com'],
            ['Auf „Get API key“ → „Create API key“ klicken und den Schlüssel kopieren.', 'https://aistudio.google.com/apikey'],
            ['Den Hinweis oben bestätigen, den Schlüssel hier einfügen und prüfen.']],
    fields: [['key', 'API-Schlüssel']],
  },
};
function onlineCard(s, st) {
  const info = ONLINE_INFO[s], sv = st.services[s];
  const stored = Object.values(sv.fields).some(Boolean);
  const badge = sv.set ? (sv.ok === false ? `<span class="pill bad">${esc(tf('Schlüssel abgelehnt'))}</span>`
                          : sv.ok ? `<span class="pill ok">${esc(tf('Verbunden'))}</span>` : `<span class="pill">${esc(tf('Gespeichert'))}</span>`)
                       : `<span class="pill">${esc(tf(stored && s === 'gemini' ? 'Hinweis nicht bestätigt' : 'Nicht eingerichtet'))}</span>`;
  return `<div class="online-card${s === 'gemini' ? ' risky' : ''}" data-service="${s}">
    <div class="oc-head"><b>${ONLINE_NAMES[s]}</b><span class="oc-role">${esc(tf(info.role))}</span>${badge}</div>
    ${s === 'gemini' ? `<div class="oc-warn">${ic('warning')}<div>
      <b>${esc(tf('Achtung: Google darf deine Tonspuren auswerten'))}</b>
      <p>${esc(tf('Bei der kostenlosen Gemini-Stufe darf Google hochgeladene Tonspuren nutzen, um seine Produkte zu verbessern, und Menschen können sie anhören und lesen.'))}</p>
      <p>${esc(tf('Laut Googles Bedingungen gilt das nicht in der EU, im Vereinigten Königreich und in der Schweiz. Lade nichts hoch, was privat ist oder dir nicht gehört. Nutzung erst ab 18 Jahren.'))}</p>
      <button class="link-btn oc-link" data-url="https://ai.google.dev/gemini-api/terms">${esc(tf('Googles Bedingungen lesen'))} ↗</button></div></div>` : ''}
    <p class="oc-desc">${esc(tf(info.desc))}</p>
    ${s === 'mvsep' ? `<p class="oc-desc muted">${esc(tf('Gratis-Aufträge stehen dort in einer Warteschlange. Zu Stoßzeiten wartest du 10 bis 20 Minuten, die Trennung selbst dauert unter einer Minute.'))}</p>` : ''}
    <ol class="oc-steps">${info.steps.map(([t, url]) => `<li><span>${esc(tf(t))}</span>${url ? ` <button class="link-btn oc-link" data-url="${esc(url)}">${esc(new URL(url).hostname)} ↗</button>` : ''}</li>`).join('')}</ol>
    ${s === 'gemini' ? `<label class="check oc-accept"><input type="checkbox" class="oc-accepted" ${sv.accepted ? 'checked' : ''}> ${esc(tf('Ich habe den Hinweis gelesen und möchte Gemini trotzdem nutzen.'))}</label>` : ''}
    <div class="oc-fields">${info.fields.map(([f, label]) => `<label>${esc(tf(label))}<input type="password" class="oc-f" data-field="${f}" autocomplete="off" spellcheck="false"
        placeholder="${esc(sv.fields[f] ? tf('gespeichert ({})', sv.fields[f]) : tf('hier einfügen'))}"></label>`).join('')}
      <button class="btn small primary oc-save">${esc(tf('Speichern und prüfen'))}</button>
      ${stored ? `<button class="btn small oc-check">${esc(tf('Prüfen'))}</button><button class="btn small danger oc-remove">${esc(tf('Entfernen'))}</button>` : ''}
    </div>
    <div class="oc-result small"></div>
  </div>`;
}
async function renderOnline() {
  const box = $('#onlineBox');
  const st = await loadOnline();
  if (!st) { box.innerHTML = `<div class="set-note">${esc(tf('Der Stand ließ sich nicht laden.'))}</div>`; return; }
  const pc = st.weak
    ? tf('Dieser PC hat keine passende NVIDIA-Grafikkarte. Ein 3-Minuten-Clip dauert hier {}, mit Online rechnen {}.', fmtDuration(st.local_3min), fmtDuration(st.online_3min))
    : tf('Dieser PC rechnet mit der Grafikkarte, ein 3-Minuten-Clip dauert hier {}. Online rechnen hilft vor allem, wenn die Grafikkarte gerade belegt ist.', fmtDuration(st.local_3min));
  box.innerHTML = `
    <div class="online-intro${st.weak ? ' weak' : ''}">
      <p>${esc(tf('Kostenlose Online-Dienste übernehmen die zwei schwersten Schritte: Stimmen trennen und Text erkennen. Dafür wird die Tonspur des Videos dorthin hochgeladen. Sprecher, Lachen und alles andere rechnet weiter dein PC.'))}</p>
      <div class="online-pc">${ic(st.weak ? 'warning' : 'monitor')}<span>${esc(pc)}</span></div>
      <ol class="online-how">
        <li>${esc(tf('Zwei kostenlose Konten anlegen: MVSEP zum Trennen und Groq zum Erkennen des Textes.'))}</li>
        <li>${esc(tf('Die Schlüssel unten einfügen und prüfen lassen.'))}</li>
        <li>${esc(tf('Beim Video im Eingang auf „Online rechnen“ klicken.'))}</li>
      </ol>
    </div>
    ${['mvsep', 'groq', 'cloudflare', 'gemini'].map(s => onlineCard(s, st)).join('')}
    <div class="set-row">
      <div><div class="set-label">${esc(tf('Text erkennen mit'))}</div>
        <div class="set-hint" id="onlineAsrNote">${esc(asrChoices(false).find(o => o.value === st.ready.transcribe)?.note || tf('Wählbar sind nur eingerichtete Dienste.'))}</div></div>
      <select id="onlineAsr" ${asrChoices(false).length ? '' : 'disabled'}>${asrChoices(false).length
        ? asrChoices(false).map(o => `<option value="${o.value}" ${st.ready.transcribe === o.value ? 'selected' : ''}>${esc(o.label)}</option>`).join('')
        : `<option>${esc(tf('Noch kein Dienst eingerichtet'))}</option>`}</select>
    </div>
    <p class="set-note online-note">${esc(tf('Die Wahl gilt als Standard. Beim Video im Eingang kannst du jedes Mal einen anderen eingerichteten Dienst wählen.'))}</p>
    <p class="set-note online-note">${esc(tf('Die Schlüssel bleiben auf diesem PC (Ordner daten). Hochgeladen wird nur die Tonspur, und nur bei Videos, bei denen du „Online rechnen“ wählst.'))}</p>`;
}
async function onlineChanged() { await renderOnline(); inboxSig = ''; projectSig = ''; refreshState(); }
async function onlineCheck(card, s) {
  const res = $('.oc-result', card);
  res.className = 'oc-result small'; res.textContent = tf('Wird geprüft …');
  let r;
  try { r = await api('POST', '/api/online/check', { service: s }, 30000); } catch (e) { r = { ok: false, text: e.message }; }
  await onlineChanged();
  const card2 = $(`.online-card[data-service="${s}"]`);
  if (card2) { const out = $('.oc-result', card2); out.className = `oc-result small ${r.ok ? 'ok' : 'bad'}`; out.textContent = tf(r.text); }
}
$('#onlineBox').addEventListener('click', async e => {
  const link = e.target.closest('.oc-link');
  if (link) { api('POST', '/api/open-link', { url: link.dataset.url }).catch(err => toast(err.message, true)); return; }
  const card = e.target.closest('.online-card'); if (!card) return;
  const s = card.dataset.service;
  if (e.target.closest('.oc-save')) {
    if (s === 'gemini' && !$('.oc-accepted', card).checked) { toast(tf('Bitte zuerst den Hinweis zu Gemini lesen und bestätigen.'), true); return; }
    const vals = {};
    $$('.oc-f', card).forEach(i => { if (i.value.trim()) vals[i.dataset.field] = i.value.trim(); });
    if (!Object.keys(vals).length) { toast(tf('Füge zuerst den Schlüssel ein.'), true); return; }
    try { await api('PUT', '/api/online', { [s]: vals }); } catch (err) { toast(err.message, true); return; }
    return onlineCheck(card, s);
  }
  if (e.target.closest('.oc-check')) return onlineCheck(card, s);
  if (e.target.closest('.oc-remove')) {
    if (!await dialog({ title: tf('Schlüssel für {} entfernen?', ONLINE_NAMES[s]), text: 'Voicitool vergisst den Schlüssel. Dein Konto beim Dienst bleibt bestehen.', tone: 'danger', ok: 'Entfernen' })) return;
    const empty = Object.fromEntries(ONLINE_INFO[s].fields.map(([f]) => [f, '']));
    await api('PUT', '/api/online', { [s]: empty }).catch(err => toast(err.message, true));
    onlineChanged();
  }
});
$('#onlineBox').addEventListener('change', async e => {
  if (e.target.id === 'onlineAsr') {
    await api('PUT', '/api/online', { asr: e.target.value }).catch(err => toast(err.message, true));
    onlineChanged();
    return;
  }
  if (!e.target.classList.contains('oc-accepted')) return;
  await api('PUT', '/api/online', { gemini: { accepted: e.target.checked } }).catch(err => toast(err.message, true));
  onlineChanged();
});

/* Einmal beim Start fragen, ob Online rechnen eingerichtet werden soll. Bei PCs ohne passende Grafikkarte
   noch einmal nachfragen, wenn jemand ablehnt: dort dauert alles sehr lange. */
async function maybeAskOnline() {
  const st = await loadOnline();
  if (!st || st.prompted || onlineReady()) return;
  if (document.querySelector('.dlg-overlay') || !$('#settings').hidden || !$('#editor').hidden) { setTimeout(maybeAskOnline, 5000); return; }
  const pc = st.weak
    ? tf('Dein PC hat keine passende NVIDIA-Grafikkarte. Ein 3-Minuten-Clip dauert hier {}, mit Online rechnen {}.', fmtDuration(st.local_3min), fmtDuration(st.online_3min))
    : tf('Dein PC rechnet mit der Grafikkarte, das geht schon schnell. Online rechnen hilft, wenn die Grafikkarte gerade belegt ist.');
  const html = `<div class="online-ask"><p><b>${esc(pc)}</b></p>
    <p>${esc(tf('Kostenlose Online-Dienste übernehmen dann Stimmen trennen und Text erkennen. Du brauchst dafür zwei kostenlose Konten, das Einrichten dauert etwa 5 Minuten, eine Anleitung ist dabei.'))}</p>
    <p class="muted">${esc(tf('Du kannst es auch später jederzeit unter Einstellungen → Online rechnen einrichten.'))}</p></div>`;
  const v = await dialog({ title: 'Online rechnen einrichten?', html, icon: 'cloud', wide: true,
                           buttons: [{ label: 'Nein danke', value: 'no' }, { label: 'Jetzt einrichten', value: 'setup', kind: 'primary', main: true }] });
  let setup = v === 'setup';
  if (!setup && st.weak) {
    setup = !!await dialog({
      title: 'Bist du sicher?', tone: 'warn', icon: 'warning', wide: true,
      html: `<div class="online-ask"><p><b>${esc(tf('Ohne Online rechnen dauert ein 3-Minuten-Clip auf diesem PC {}. Längere Videos brauchen entsprechend länger, eine ganze Folge oft Stunden.', fmtDuration(st.local_3min)))}</b></p>
        <p>${esc(tf('Mit Online rechnen sind es etwa {}.', fmtDuration(st.online_3min)))}</p></div>`,
      buttons: [{ label: 'Ja, auf diesem PC rechnen', value: false }, { label: 'Doch einrichten', value: true, kind: 'primary', main: true }],
    });
  }
  await api('PUT', '/api/online', { prompted: true }).catch(() => {});
  if (setup) openSettings('online');
}


/* ------------------------------------------------------------ Aus dem Spiel: Pack bearbeiten, Aufnahme als Video
   Beides braucht keine KI: Das Pack bringt Zeilen, Sprecher und Zeiten schon mit, die Aufnahmen werden nur
   über den Hintergrund gelegt. */
async function pickFromGame(title, text, load, render) {
  // Fenster sofort zeigen: Das Einlesen der Packs im Spiel dauert je nach Menge ein paar Sekunden
  let items = [], sel = -1;
  const ov = document.createElement('div');
  ov.className = 'dlg-overlay';
  ov.innerHTML = `<div class="dlg wider" role="dialog" aria-modal="true">
    <div class="dlg-head"><div class="dlg-icon">${ic('gamepad')}</div>
      <div class="dlg-titles"><h2>${esc(tf(title))}</h2><p class="dlg-text">${esc(tf(text))}</p></div></div>
    <div class="game-pick">
      <div>
        <input class="game-search" type="search" spellcheck="false" hidden
          placeholder="${esc(tf('Suchen'))}" aria-label="${esc(tf('Suchen'))}">
        <div class="game-list"><div class="loadbar"></div></div>
      </div>
      <div class="game-info"><span class="muted">${esc(tf('Wähle links einen Eintrag.'))}</span></div>
    </div>
    <div class="dlg-actions"><button class="btn dlg-close">${esc(tf('Schließen'))}</button>
      <button class="btn primary game-go" disabled>${esc(tf('Starten'))}</button></div>`;
  document.body.appendChild(ov);
  const close = () => { ov.remove(); document.removeEventListener('keydown', onKey, true); };
  const list = $('.game-list', ov), info = $('.game-info', ov), go = $('.game-go', ov), q = $('.game-search', ov);

  const start = () => { if (sel >= 0) { close(); items[sel].run(); } };
  const onKey = e => {
    if (e.key === 'Escape') { e.stopPropagation(); return close(); }
    if (e.key === 'Enter' && sel >= 0 && ov.isConnected) { e.stopPropagation(); e.preventDefault(); start(); }
  };
  document.addEventListener('keydown', onKey, true);

  const pick = i => {
    sel = i;
    $$('.game-item', ov).forEach(b => b.classList.toggle('sel', +b.dataset.i === i));
    info.innerHTML = items[i].info || `<b data-nolang>${esc(items[i].search || '')}</b>`;
    go.disabled = false;
  };
  const paint = () => {
    const needle = (q.value || '').trim().toLowerCase();
    const hit = items.map((it, i) => [it, i])
      .filter(([it]) => !needle || (it.search || '').toLowerCase().includes(needle));
    list.innerHTML = hit.length
      ? hit.map(([it, i]) => `<button class="game-item${i === sel ? ' sel' : ''}" data-i="${i}">${it.html}</button>`).join('')
      : `<div class="set-note">${esc(tf('Nichts gefunden.'))}</div>`;
  };
  q.addEventListener('input', paint);
  ov.addEventListener('click', e => {
    if (e.target === ov || e.target.closest('.dlg-close')) return close();
    if (e.target.closest('.game-go')) return start();
    const b = e.target.closest('.game-item');
    if (b) pick(+b.dataset.i);
  });
  ov.addEventListener('dblclick', e => { if (e.target.closest('.game-item')) start(); });

  let data;
  try { data = await load(); } catch (e) { close(); return toast(e.message, true, 6000); }
  if (!ov.isConnected) return;   // inzwischen geschlossen
  items = render(data);
  if (!items.length) return close();
  q.hidden = items.length < 6;   // bei wenigen Einträgen braucht es kein Suchfeld
  if (!q.hidden) q.focus();
  paint();
  if (data.dir) {
    $('.dlg-text', ov).insertAdjacentHTML('afterend', `<p class="dlg-hint" data-nolang>${esc(data.dir)}</p>`);
  }
}

/* Zeitpunkt für die Infospalte, in der Sprache der Oberfläche */
function whenText(sec) {
  if (!sec) return '';
  return new Date(sec * 1000).toLocaleString(window.VT_I18N?.lang || undefined,
    { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}
function infoRow(k, v) { return `<div><span class="k">${esc(tf(k))}</span><span data-nolang>${esc(v)}</span></div>`; }

$('#btnImportPack').onclick = () => pickFromGame(
  'Pack bearbeiten', 'Ein fertiges Pack aus dem Spiel wird als Projekt übernommen: Video, Zeilen, Sprecher und Zeiten. Es wird nichts neu erkannt.',
  () => api('GET', '/api/game/packs'),
  data => {
    if (!data.exists) { toast(tf('Der Pack-Ordner des Spiels wurde nicht gefunden. Du kannst ihn in den Einstellungen setzen.'), true, 7000); openSettings('export'); return []; }
    if (!data.packs.length) { toast(tf('Im Spiel sind keine Packs.'), true); return []; }
    return data.packs.map(p => ({
      html: `<b data-nolang>${esc(p.title)}</b><span class="muted small">${esc(tf('{} Zeilen', p.lines))}${p.video ? '' : ' · ' + esc(tf('ohne Video'))}</span>`,
      search: `${p.title} ${p.name} ${p.authors || ''}`,
      info: `<b data-nolang>${esc(p.title)}</b>`
        + infoRow('Zeilen', p.lines)
        + (p.authors?.length ? infoRow('Von', [].concat(p.authors).join(', ')) : '')
        + infoRow('Video', p.video ? tf('ja') : tf('fehlt'))
        + (p.modified ? infoRow('Geändert', whenText(p.modified)) : '')
        + `<div class="muted small" style="margin-top:8px">${esc(tf('Zeilen, Sprecher und Zeiten werden übernommen. Es wird nichts neu erkannt.'))}</div>`
        + `<div style="margin-top:6px"><code data-nolang>${esc(p.name)}</code></div>`,
      run: async () => {
        if (!p.video) return toast(tf('In diesem Pack fehlt das Video.'), true);
        try {
          const { job } = await api('POST', '/api/game/import', { pack: p.name, ui_lang: window.VT_I18N?.lang });
          toast(tf('Pack wird übernommen …'));
          const r = await waitJob(job);
          projectSig = ''; await refreshState();
          toast(tf('Fertig. Das Projekt ist offen zum Bearbeiten.'));
          if (r?.id) openProject(r.id);
        } catch (e) { toast(e.message, true, 7000); }
      },
    }));
  });

$('#btnSessionVideo').onclick = () => pickFromGame(
  'Aufnahme als Video', 'Wähle eine Aufnahme aus dem Spiel. Voicitool legt sie über das Video des Packs und speichert eine Videodatei.',
  () => api('GET', '/api/game/sessions'),
  data => {
    if (!data.sessions.length) { toast(tf('Im Spiel sind noch keine Aufnahmen.'), true, 6000); return []; }
    return data.sessions.map(s => ({
      html: `<b data-nolang>${esc(s.pack || tf('Pack unbekannt'))}</b>
        <span class="muted small" data-nolang>${esc(s.label || '')}</span>
        <span class="muted small">${esc(tf('{} Aufnahmen', s.takes))}</span>
        ${s.video ? `<span class="pill ok small">${esc(tf('exportiert'))}</span>` : ''}`,
      search: `${s.pack || ''} ${s.label || ''}`,
      info: `<b data-nolang>${esc(s.pack || tf('Pack unbekannt'))}</b>`
        + infoRow('Aufnahmen', s.takes)
        + infoRow('Gespeichert in', s.kind === 'multi' ? tf('Mehrspieler-Aufnahmen') : tf('Eigene Aufnahmen'))
        + (s.when ? infoRow('Wann', whenText(s.when)) : '')
        + (s.label ? infoRow('Ordner', s.label) : '')
        + (s.video ? `<div style="margin-top:6px">${esc(tf('Schon exportiert'))}<br><code data-nolang>${esc(s.video)}</code></div>` : '')
        + `<div class="muted small" style="margin-top:8px">${esc(!s.pack
            ? tf('Zu dieser Aufnahme wurde kein Pack gefunden. Das Video lässt sich nicht bauen.')
            : s.video ? tf('Ein neuer Durchlauf ersetzt die vorhandene Datei.')
            : tf('Die Aufnahmen werden über das Video des Packs gelegt und als Videodatei gespeichert.'))}</div>`,
      run: async () => {
        try {
          const { job } = await api('POST', '/api/game/session-video', { session: s.id, pack: s.pack || null });
          toast(tf('Video wird gebaut …'));
          const r = await waitJob(job);
          const dlg = await dialog({ title: tf('Video ist fertig'), icon: 'clapper',
            html: `<b data-nolang>${esc(r.file.split(/[\\/]/).pop())}</b><br>${esc(tf('{} Aufnahmen eingesetzt.', r.lines))}`
              + `<br><code data-nolang>${esc(r.file)}</code>`,
            buttons: [{ label: 'Schließen', value: null }, { label: 'Im Ordner zeigen', value: 'open', kind: 'primary', main: true }] });
          if (dlg === 'open') api('POST', '/api/open', { path: r.file }).catch(e => toast(e.message, true));
        } catch (e) { toast(e.message, true, 8000); }
      },
    }));
  });

/* Video von einer Web-Adresse laden (YouTube u. a.) */
async function downloadFromUrl() {
  const input = $('#urlInput'), btn = $('#btnUrl');
  const url = input.value.trim();
  if (!url) return;
  if (!(await preflight('download'))) return;
  btn.disabled = true; input.disabled = true;
  setUploadState('Video wird geladen …');
  try {
    const subs = $('#urlSubs').checked;
    const { job } = await api('POST', '/api/download', { url, subs });
    const r = await waitJob(job);
    input.value = '';
    setUploadState(r.subs ? tf('„{}" liegt im Eingang ({} MB), mit Untertiteln.', r.title, r.mb)
                          : tf('„{}" liegt im Eingang ({} MB).', r.title, r.mb), { file: r.filename, ttl: 15000 });
    if (subs && !r.subs) toast(tf('Keine Untertitel vom Uploader gefunden. Unter „Text vorgeben“ gibt es vielleicht automatisch erzeugte.'), false, 7000);
    inboxSig = '';
    refreshState();
  } catch (e) {
    clearUploadState();
    toast(e.message === 'Abgebrochen' ? 'Download abgebrochen.' : 'Download fehlgeschlagen: ' + e.message, true, 6000);
  } finally { btn.disabled = false; input.disabled = false; }
}
$('#btnUrl').onclick = downloadFromUrl;
try { $('#urlSubs').checked = localStorage.getItem('vt.urlSubs') !== '0'; } catch { $('#urlSubs').checked = true; }
$('#urlSubs').addEventListener('change', e => { try { localStorage.setItem('vt.urlSubs', e.target.checked ? '1' : '0'); } catch { /* egal */ } });
$('#urlInput').addEventListener('keydown', e => { if (e.key === 'Enter') downloadFromUrl(); });

/* Upload */
const dz = $('#dropZone');
const hasFiles = e => [...(e.dataTransfer?.types || [])].includes('Files');
['dragenter', 'dragover'].forEach(ev => document.addEventListener(ev, e => { e.preventDefault(); if (!$('#home').hidden && hasFiles(e)) dz.classList.add('over'); }));
['dragleave', 'drop'].forEach(ev => document.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove('over'); }));
document.addEventListener('drop', e => { if (!$('#home').hidden) handleDrop(e.dataTransfer); });

/* Hereingezogen wird beides: Videos für den Eingang und fertige Packs (ZIP oder Ordner) zum Bearbeiten. */
async function handleDrop(dt) {
  // beides sofort auslesen: nach dem ersten await ist das DataTransfer-Objekt leer
  const dirs = [...(dt.items || [])].map(i => i.webkitGetAsEntry?.()).filter(en => en && en.isDirectory);
  const files = [...(dt.files || [])].filter(f => f.size || f.type);
  for (const dir of dirs) {
    const found = await readDirFiles(dir, dir.name);
    if (!found.length) continue;
    // Pack oder nur ein Ordner mit Videos? Sonst lädt man hunderte MB hoch und bekommt eine Absage
    const isPack = found.some(x => /\.ogg$/i.test(x.path)) && found.some(x => /\.(ini|txt)$/i.test(x.path));
    if (isPack) { await importDropped(found, dir.name); continue; }
    const vids = found.map(x => x.file).filter(f => /\.(mp4|mkv|mov|webm|avi|m4v|ogv|flv|wmv|ts|mpg|mpeg)$/i.test(f.name));
    if (vids.length) uploadFiles(vids);
    else toast(tf('Darin ist kein Pack: es fehlen die Sprachclips oder die Zeilen-Dateien.'), true, 7000);
  }
  const zips = files.filter(f => /\.zip$/i.test(f.name));
  for (const z of zips) await importDropped([{ file: z, path: z.name }], z.name.replace(/\.zip$/i, ''));
  const rest = files.filter(f => !/\.zip$/i.test(f.name));
  if (rest.length) uploadFiles(rest);
}

/* Ordner rekursiv auslesen (readEntries liefert höchstens 100 Einträge auf einmal) */
async function readDirFiles(dir, prefix, out = [], depth = 0) {
  if (depth > 3 || out.length > 4000) return out;
  const reader = dir.createReader();
  for (;;) {
    const batch = await new Promise(res => reader.readEntries(res, () => res([])));
    if (!batch.length) break;
    for (const en of batch) {
      const path = `${prefix}/${en.name}`;
      if (en.isDirectory) await readDirFiles(en, path, out, depth + 1);
      else out.push({ file: await new Promise(res => en.file(res, () => res(null))), path });
    }
  }
  return out.filter(x => x.file);
}

/* Pack übernehmen: Dateien hochladen, dann wie bei „Pack bearbeiten“ als Projekt anlegen */
async function importDropped(found, name) {
  const mb = Math.round(found.reduce((s, x) => s + x.file.size, 0) / 1048576);
  setUploadState(tf('Pack „{}“ wird gelesen … ({} MB)', name, mb));
  const fd = new FormData();
  for (const x of found) { fd.append('files', x.file, x.file.name); fd.append('paths', x.path); }
  fd.append('name', name);
  fd.append('ui_lang', window.VT_I18N?.lang || '');
  try {
    const res = await fetch('/api/game/import-upload', { method: 'POST', body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Fehler');
    setUploadState(tf('Pack wird übernommen …'));
    const r = await waitJob(data.job);
    clearUploadState();
    projectSig = ''; await refreshState();
    toast(r?.more ? tf('Darin waren {} Packs. Übernommen wurde das größte.', r.more + 1)
                  : tf('Fertig. Das Projekt ist offen zum Bearbeiten.'), false, r?.more ? 7000 : 3500);
    if (r?.id) openProject(r.id);
  } catch (e) {
    clearUploadState();
    toast(e.message, true, 8000);
  }
}
$('#fileInput').onchange = e => uploadFiles(e.target.files);
async function uploadFiles(files) {
  for (const f of files) {
    await new Promise(resolve => {
      const xhr = new XMLHttpRequest();
      const fd = new FormData(); fd.append('file', f);
      xhr.upload.onprogress = e => setUploadState(`Lade ${f.name} … ${Math.round(e.loaded / e.total * 100)} %`);
      xhr.onload = () => {
        let file = null;
        try { file = JSON.parse(xhr.responseText).filename; } catch { /* egal */ }
        if (xhr.status === 200) setUploadState(tf('{} liegt im Eingang.', file || f.name), { file, ttl: 15000 });
        else setUploadState(`Fehler: ${xhr.responseText}`, { ttl: 20000 });
        resolve();
      };
      xhr.onerror = () => { setUploadState('Upload fehlgeschlagen', { ttl: 20000 }); resolve(); };
      xhr.open('POST', '/api/upload'); xhr.send(fd);
    });
  }
  refreshState();
}

/* ------------------------------------------------------------ Projekt öffnen */
async function openProject(pid, fromEl) {
  let data;
  try { data = await api('GET', `/api/projects/${encodeURIComponent(pid)}`); }
  catch (e) { toast(e.message, true); return; }
  const from = clipOf(fromEl);
  viewTransition('open', () => showEditor(pid, data), from ? { '--vt-from': from } : {});
}

function showEditor(pid, data) {
  S.pid = pid; S.p = data;
  S.p.cuts = S.p.cuts || [];
  S.undo = []; S.redo = []; S.sel = null; S.multi = new Set(); S.viewStart = 0;
  setCutMode(false);
  seenItems.delete($('#lineList'));   // Zeilen eines neu geöffneten Projekts nicht einzeln einblenden
  S.voices = null;
  setTimeout(loadVoices, 0);
  sortLines();
  $('#home').hidden = true; $('#editor').hidden = false;
  document.body.classList.add('in-editor');
  resetPlayer();
  $('#projTitle').textContent = S.p.name;
  $('#saveState').textContent = '';
  history.replaceState(null, '', '#' + encodeURIComponent(pid));

  S.audioMode = 'orig';
  loadMedia();
  applyEditorSettings();

  S.peaks = null; S.words = [];
  fetch(mediaUrl('wellenform.json')).then(r => r.ok ? r.json() : null).then(j => { S.peaks = j; drawTimeline(); });
  fetch(mediaUrl('analyse.json')).then(r => r.ok ? r.json() : null).then(j => { S.words = j ? j.words : []; });

  fillExportForm();
  renderAll();
  requestAnimationFrame(() => { resizeTimeline(); drawTimeline(); });
  // während der Zoom aus der Projektzeile läuft, kommen die Teile des Editors nacheinander herein
  const tab = $$('.right > .tab').find(t => !t.hidden);
  stagger([$('.video-wrap'), $('.transport'), $('#timelineWrap'), $('.keys')], { delay: 140, step: 50 });
  stagger([$('.right > .tabs'), tab], { delay: 200, step: 70, dy: 0 });
}

/* Sprache automatisch, aber unsicher erkannt (z. B. starker Akzent: Tscheche spricht Englisch): Hinweis
   mit Sprachwahl. Neu verarbeiten ersetzt die Zeilen, deshalb vorher nachfragen. */
function renderLangHint() {
  const p = S.p, prob = p.language_probability;
  const unsure = (p.settings?.language || 'auto') === 'auto' && prob != null && prob < 0.6;
  $('#langUnsure').hidden = !unsure;
  if (!unsure) return;
  $('#langUnsureText').textContent = tf('Sprache unsicher erkannt: {} ({} %). Stimmt das nicht? Sprache wählen und neu verarbeiten.',
                                        langName(p.language), Math.round(prob * 100));
  const sel = $('#langUnsureSel');
  sel.innerHTML = langOptions(p.language);
  sel.removeAttribute('data-combo');
  sel.nextElementSibling?.classList.contains('lang-combo') && sel.nextElementSibling.remove();
  enhanceLangSelect(sel);
}
$('#btnLangRedo').onclick = async () => {
  const lang = $('#langUnsureSel').value;
  if (!await dialog({ title: tf('Mit „{}“ neu verarbeiten?', langName(lang)), icon: 'globe', tone: 'warn', ok: 'Neu verarbeiten',
                      text: 'Die Zeilen werden neu erkannt. Deine Änderungen an Zeilen und Sprechern in diesem Projekt gehen dabei verloren.' })) return;
  const pid = S.pid;
  try {
    await flushSave();
    await api('POST', `/api/projects/${encodeURIComponent(pid)}/reprocess`, { language: lang });
    toast('Verarbeitung neu gestartet.');
    showHome();
    projectSig = ''; refreshState();
  } catch (e) { toast(e.message, true); }
};

function renderAll() {
  renderLangHint();
  renderCharFilter();
  renderLines();
  renderChars();
  renderExportChecks();
  drawTimeline();
}

/* ------------------------------------------------------------ Umbenennen */
async function renameProject(name) {
  name = (name || '').trim();
  if (!S.p) return;
  if (!name) { $('#exTitle').value = S.p.name; return toast('Der Name darf nicht leer sein.', true); }
  if (name === S.p.name) return;
  await flushSave();
  // Medien freigeben, damit der Projektordner umbenannt werden kann
  const t = video.currentTime;
  video.pause();
  [video, ...allAudios()].forEach(m => { m.removeAttribute('src'); m.load(); });
  await new Promise(r => setTimeout(r, 300));
  try {
    const r = await api('POST', `/api/projects/${encodeURIComponent(S.pid)}/rename`, { name });
    S.pid = r.id; S.p.name = r.name;
    history.replaceState(null, '', '#' + encodeURIComponent(S.pid));
    toast(`Umbenannt in „${r.name}".`);
  } catch (e) { toast(e.message, true); }
  $('#projTitle').textContent = S.p.name; $('#exTitle').value = S.p.name;
  loadMedia(t);
}
/** Fragt nach einem neuen Projektnamen (null = abgebrochen). */
function askProjectName(current) {
  return dialog({ title: 'Projekt umbenennen', text: 'Der Name wird auch als Pack-Name, Export-Ordner und im Spiel genutzt.',
                  input: current, required: true, ok: 'Umbenennen', maxLength: 120 });
}
$('#projTitle').onclick = async () => {
  if (!S.p) return;
  const nn = await askProjectName(S.p.name);
  if (nn && nn !== S.p.name) renameProject(nn);
};

// „Hintergrund“ im Editor: das eigene Instrumental, wenn es als Hintergrund gewählt ist, sonst die KI-Trennung
const ownBacking = () => !!(S.p?.instrumental && (S.p.export?.backing_source || 'auto') === 'eigene');
function refreshBackingAudio() {
  refreshInstAudio();
  const a = $('#audioBack');
  const want = ownBacking() ? 'instrumental.ogg' : 'hintergrund.ogg';
  const ver = want === 'instrumental.ogg' ? `?v=${encodeURIComponent(S.p.instrumental?.versatz ?? '')}` : '';
  if (a.dataset.file === want && a.dataset.ver === ver) return;   // neu ausgerichtet: neue Fassung laden
  a.dataset.file = want;
  a.dataset.ver = ver;
  a.src = mediaUrl(want) + ver;
  if (S.audioMode === 'hinter') setAudioMode('hinter');
}
/* „Hintergrund + Stimmen“: das eigene Instrumental (immer, auch wenn im Export die KI-Trennung gewählt ist)
   zusammen mit den getrennten Stimmen. Den Knopf gibt es nur mit eigenem Instrumental. */
function refreshInstAudio() {
  const a = $('#audioInst'), has = !!S.p?.instrumental;
  const want = has ? mediaUrl('instrumental.ogg') + `?v=${encodeURIComponent(S.p.instrumental?.versatz ?? '')}` : '';
  $('#audioMode [data-mode=mix]').hidden = !has;
  if (a.dataset.url !== want) {
    a.dataset.url = want;
    if (want) a.src = want; else { a.removeAttribute('src'); a.load(); }
  }
  if (!has && S.audioMode === 'mix') setAudioMode('orig');
  else if (S.audioMode === 'mix') setAudioMode('mix');
}
for (const id of ['#audioBack', '#audioInst']) {
  $(id).addEventListener('error', e => {   // ältere Projekte haben nur instrumental.wav
    const a = e.target;
    if (!/instrumental\.ogg/.test(a.src)) return;
    a.dataset.file = 'instrumental.wav';
    a.src = mediaUrl('instrumental.wav');
    if (activeAudios().includes(a) && !video.paused) { a.currentTime = video.currentTime; a.play().catch(() => {}); }
  });
}

/* Lautstärke des Hintergrunds (0 bis 200 %): im Player für „Hintergrund“ und „Hintergrund + Stimmen“, im Export
   für den Backing-Track. Über 100 % geht es nur mit Web Audio, das wird erst dann eingeschaltet. */
const backingVolume = () => clamp(+(S.p?.export?.backing_volume ?? 1) || 0, 0, 2);
let backCtx = null;
const backGain = new Map();
function applyBackingVolume() {
  const v = backingVolume();
  for (const a of [$('#audioBack'), $('#audioInst')]) {
    if (v <= 1 && !backGain.has(a)) { a.volume = v; continue; }
    try {
      if (!backCtx) backCtx = new AudioContext();
      if (!backGain.has(a)) {
        const g = backCtx.createGain();
        backCtx.createMediaElementSource(a).connect(g).connect(backCtx.destination);
        backGain.set(a, g);
      }
      a.volume = 1;
      backGain.get(a).gain.value = v;
    } catch { a.volume = Math.min(1, v); }
  }
  if (backCtx?.state === 'suspended') backCtx.resume().catch(() => {});
  const pct = `${Math.round(v * 100)} %`;
  $('#backVol').value = String(Math.round(v * 100)); $('#exBackVol').value = String(Math.round(v * 100));
  $$('.back-vol-val').forEach(el => (el.textContent = pct));
}
function setBackingVolume(pct) {
  if (!S.p) return;
  S.p.export.backing_volume = clamp(Math.round(+pct) / 100, 0, 2);
  applyBackingVolume();
  scheduleSave();
}
$('#backVol').addEventListener('input', e => setBackingVolume(e.target.value));

function loadMedia(t = 0) {
  video.src = mediaUrl(S.p.preview || S.p.source);
  $('#audioVoc').src = mediaUrl('stimmen.ogg');
  $('#audioBack').dataset.file = '';
  $('#audioInst').dataset.url = '';
  refreshBackingAudio();
  applyBackingVolume();
  if (t) video.addEventListener('loadedmetadata', () => { video.currentTime = t; }, { once: true });
  setAudioMode(S.audioMode || 'orig');
}

/* ------------------------------------------------------------ Speichern & Undo */
function snapshot() {
  S.undo.push(JSON.stringify({ lines: S.p.lines, characters: S.p.characters, cuts: S.p.cuts }));
  if (S.undo.length > 200) S.undo.shift();
  S.redo = [];
}
function restore(from, to) {
  if (!from.length) return;
  to.push(JSON.stringify({ lines: S.p.lines, characters: S.p.characters, cuts: S.p.cuts }));
  const st = JSON.parse(from.pop());
  S.p.lines = st.lines; S.p.characters = st.characters;
  if (st.cuts) S.p.cuts = st.cuts;
  if (S.sel && !lineById(S.sel)) S.sel = null;
  renderAll(); scheduleSave();
}
const undo = () => restore(S.undo, S.redo);
const redo = () => restore(S.redo, S.undo);
$('#btnUndo').onclick = undo; $('#btnRedo').onclick = redo;

function scheduleSave() {
  $('#saveState').textContent = 'Ungespeichert…';
  clearTimeout(S.saveTimer);
  S.saveTimer = setTimeout(save, 700);
}
async function save() {
  S.saveTimer = null;
  if (!S.pid) return;
  const pid = S.pid;
  try {
    const r = await api('PUT', `/api/projects/${encodeURIComponent(pid)}`,
      { lines: S.p.lines, characters: S.p.characters, pack: S.p.pack, export: S.p.export, credits: S.p.credits, cuts: S.p.cuts });
    if (pid === S.pid) $('#saveState').textContent = `Gespeichert ${r.saved}`;
  } catch (e) { $('#saveState').textContent = 'Speichern fehlgeschlagen!'; toast('Speichern fehlgeschlagen: ' + e.message, true); }
}
async function flushSave() { if (S.saveTimer) { clearTimeout(S.saveTimer); await save(); } }
window.addEventListener('beforeunload', flushSave);

/* ------------------------------------------------------------ Video & Audio */
const video = $('#video');
const AUDIO_MODES = { stimmen: ['#audioVoc'], hinter: ['#audioBack'], mix: ['#audioVoc', '#audioInst'] };
const allAudios = () => [$('#audioVoc'), $('#audioBack'), $('#audioInst')];
const activeAudios = () => (AUDIO_MODES[S.audioMode] || []).map(id => $(id));

function setAudioMode(mode) {
  S.audioMode = mode;
  $$('#audioMode button').forEach(b => b.classList.toggle('on', b.dataset.mode === mode));
  $('#backVolBox').hidden = !(mode === 'hinter' || mode === 'mix');
  video.muted = mode !== 'orig';
  const active = activeAudios();
  allAudios().forEach(a => {
    if (active.includes(a)) { a.currentTime = video.currentTime; a.playbackRate = video.playbackRate; if (!video.paused) a.play().catch(() => {}); }
    else a.pause();
  });
}
$$('#audioMode button').forEach(b => b.onclick = () => setAudioMode(b.dataset.mode));
video.addEventListener('play', () => {
  if (backCtx?.state === 'suspended') backCtx.resume().catch(() => {});
  activeAudios().forEach(a => { a.currentTime = video.currentTime; a._seekAt = performance.now(); a.play().catch(() => {}); });
  $('#btnPlay').innerHTML = ic('pause');
});
video.addEventListener('pause', () => { allAudios().forEach(a => a.pause()); $('#btnPlay').innerHTML = ic('play'); });
video.addEventListener('seeked', () => activeAudios().forEach(a => { a.currentTime = video.currentTime; a._seekAt = performance.now(); }));
allAudios().forEach(a => a.addEventListener('seeked', () => {   // wie lange ein Sprung dauert: beim nächsten so weit vorausspringen
  if (a._seekAt) a._lag = clamp((performance.now() - a._seekAt) / 1000, 0, 1.5);
  a._seekAt = 0;
}));

/* Tonspur dem Video nachführen. Früher wurde bei mehr als 0,12 s Abstand in jedem Bild neu gesprungen: Dauert ein
   Sprung länger (PC ausgelastet, z. B. während eines Exports), sprang die Spur endlos und blieb stumm. Jetzt: kleine
   Abstände über die Geschwindigkeit ausgleichen, große mit einem Sprung, der die gemessene Sprungdauer einrechnet. */
function syncAudio(a, t) {
  if (a.seeking || a.readyState < 2) return;
  const d = a.currentTime - t, rate = video.playbackRate;
  if (Math.abs(d) > 0.3) {
    const now = performance.now();
    if (now - (a._jumpAt || 0) < 800) return;
    a._jumpAt = a._seekAt = now;
    a.playbackRate = rate;
    a.currentTime = t + (a._lag || 0.05) * rate;
  } else if (Math.abs(d) > 0.04) {
    a.playbackRate = rate * (d > 0 ? 0.96 : 1.04);
  } else if (a.playbackRate !== rate) {
    a.playbackRate = rate;
  }
}
video.addEventListener('click', () => togglePlay());
$('#btnPlay').onclick = () => togglePlay();
$('#rate').onchange = e => { video.playbackRate = +e.target.value; allAudios().forEach(a => (a.playbackRate = +e.target.value)); };

function togglePlay() { S.playUntil = null; video.paused ? startVideo() : video.pause(); }
function seek(t) { video.currentTime = clamp(t, 0, video.duration || S.p?.duration || 0); }
function playRange(start, end) { S.playUntil = end; seek(start); startVideo(); }

/* Abspielen mit Aufpasser: Startet das Video nicht (Fehler, oder es hängt, z. B. weil die Grafikkarte
   gerade mit einer Verarbeitung voll ausgelastet ist), wird es einmal an derselben Stelle neu geladen.
   Klappt auch das nicht, gibt es eine Meldung statt Stille. Beim Nachladen dreht ein Ring am Knopf. */
let playWatch = 0, lastRecover = 0;
function startVideo() {
  const p = video.play();
  if (p) p.catch(e => { if (e.name !== 'AbortError') recoverVideo(e); });
  clearTimeout(playWatch);
  const from = video.currentTime;
  playWatch = setTimeout(() => {
    if (!video.paused && video.currentTime === from && video.readyState < 3) recoverVideo();
  }, 3000);
}
function recoverVideo(err) {
  if (!S.p || !video.currentSrc) return;
  if (performance.now() - lastRecover < 15000) {
    video.pause();
    toast(tf('Das Video lässt sich gerade nicht abspielen. Läuft nebenbei eine Verarbeitung, ist die Grafikkarte evtl. ausgelastet.'), true);
    if (err) console.warn(err);
    return;
  }
  lastRecover = performance.now();
  const t = video.currentTime, src = video.currentSrc, pid = S.pid;
  video.addEventListener('loadedmetadata', () => {
    if (S.pid !== pid) return;
    video.currentTime = t;
    startVideo();
  }, { once: true });
  video.src = src;
}
const setBuffering = on => $('#btnPlay').classList.toggle('buffering', on && !video.paused);
video.addEventListener('waiting', () => setBuffering(true));
['playing', 'pause', 'emptied', 'canplay'].forEach(ev => video.addEventListener(ev, () => setBuffering(false)));

/* Video schneiden: rausgeschnittene Stellen ([start, ende] im Original). Das Original bleibt, der Editor springt
   beim Abspielen darüber, der Export lässt sie weg (Video, Hintergrund, Zeitstempel rücken nach). */
const cutAt = t => (S.p?.cuts || []).find(c => t >= c[0] && t < c[1]) || null;
function addCut(a, b) {
  const cuts = [...(S.p.cuts || []), [r3(Math.min(a, b)), r3(Math.max(a, b))]].sort((x, y) => x[0] - y[0]);
  const merged = [];
  for (const [s, e] of cuts) {
    if (merged.length && s <= merged[merged.length - 1][1] + 0.01) merged[merged.length - 1][1] = Math.max(merged[merged.length - 1][1], e);
    else merged.push([s, e]);
  }
  S.p.cuts = merged;
  renderExportChecks();
}
function cutLinesIn(c) { return S.p.lines.filter(l => l.start >= c[0] && l.end <= c[1]).length; }
async function removeCut(c) {
  snapshot();
  S.p.cuts = S.p.cuts.filter(x => x !== c);
  renderExportChecks();
  scheduleSave(); drawTimeline();
  toast(tf('Schnitt entfernt, die Stelle ist wieder im Video.'));
}

function tick() {
  if (S.p && !$('#editor').hidden) {
    let t = video.currentTime;
    const cut = !video.paused && cutAt(t);
    if (cut) { video.currentTime = Math.min(S.p.duration, cut[1] + 0.01); t = video.currentTime; }   // über Schnitte springen
    if (S.playUntil !== null && t >= S.playUntil) { video.pause(); S.playUntil = null; }
    if (!video.paused) activeAudios().forEach(a => syncAudio(a, t));
    $('#timeLabel').textContent = `${fmt(t)} / ${fmt(S.p.duration, false)}`;
    updateActive(t);
    if (!video.paused) followView(t);
    drawTimeline();
  }
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);

function updateActive(t) {
  const active = S.p.lines.filter(l => t >= l.start && t < l.end);
  const ids = new Set(active.map(l => l.id));
  const same = ids.size === S.lastActive.size && [...ids].every(i => S.lastActive.has(i));
  if (same) return;
  S.lastActive = ids;
  $('#caption').innerHTML = active.map(l => {
    const cs = l.chars.map(charById).filter(Boolean);
    const col = cs[0]?.color || '#fff';
    return `<div><b style="color:${col}">${esc(cs.map(c => c.name).join(' & '))}</b>${esc(l.text)}</div>`;
  }).join('');
  $$('#lineList .line').forEach(el => el.classList.toggle('active', ids.has(el.dataset.id)));
  if ($('#follow').checked && !video.paused && active.length) {
    const el = $(`#lineList .line[data-id="${active[0].id}"]`);
    if (el && document.activeElement?.tagName !== 'TEXTAREA') el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
}

/* ------------------------------------------------------------ Zeilenliste */
function renderCharFilter() {
  const sel = $('#charFilter'), cur = sel.value;
  sel.innerHTML = '<option value="">Alle Charaktere</option>' + S.p.characters.map(c => `<option data-nolang value="${c.id}">${esc(c.name)}</option>`).join('');
  sel.value = S.p.characters.some(c => c.id === cur) ? cur : '';
  $('#lineCount').textContent = S.p.lines.length;
  $('#charCount').textContent = S.p.characters.length;
}
$('#charFilter').onchange = renderLines;
$('#textFilter').oninput = renderLines;

function charOptions(selected) {
  return S.p.characters.map(c => `<option data-nolang value="${c.id}" ${c.id === selected ? 'selected' : ''}>${esc(c.name)}</option>`).join('')
    + '<option value="__new">Neuer Charakter…</option>';
}

function lineHtml(l, i) {
  const c0 = charById(l.chars[0]);
  const dur = l.end - l.start;
  const extras = l.chars.slice(1).map(id => charById(id)).filter(Boolean)
    .map(c => `<span class="extra-char" data-nolang data-rm="${c.id}" style="--c:${c.color}" title="Entfernen">+ ${esc(c.name)}</span>`).join('');
  const master = masterOf(l);
  const reps = master ? [] : repeatsOf(l.id);
  const repBadge = master
    ? `<span class="rep" data-master="${master.id}" title="Wiederholung: nutzt die Aufnahme von Zeile ${lineNo(master)} („${esc(master.text)}“)">${ic('repeat')}<span>wie #${lineNo(master)}</span></span>`
    : (reps.length ? `<span class="rep" data-first-rep="${reps[0].id}" title="Diese Aufnahme wird ${reps.length + 1}× im Video abgespielt">${ic('repeat')}<span>×${reps.length + 1}</span></span>` : '');
  return `<div class="line ${S.sel === l.id ? 'sel' : ''} ${S.sel !== l.id && isSel(l.id) ? 'msel' : ''} ${master ? 'repeat' : ''}" data-id="${l.id}" style="--c:${c0?.color || '#666'}">
    <div class="line-head">
      <span class="idx">${i + 1}</span>${repBadge}
      <select class="char-select" style="--c:${c0?.color || '#666'}">${charOptions(l.chars[0])}</select>
      ${extras}
      <span class="ltime ${dur > 59 ? 'long' : ''}" title="Zum Zeilenanfang springen">${fmt(l.start)} → ${fmt(l.end)} · ${dur.toFixed(1)} s</span>
      <div class="spacer"></div>
      ${l.image ? `<img class="line-img" src="${imgUrl(l.image)}" alt="" title="${esc(tf('Eigenes Bild dieser Zeile'))}">` : ''}
      <button class="icon-btn play" title="Zeile abspielen (Enter)">${ic('play')}</button>
    </div>
    <textarea data-nolang class="${l.text.trim() ? '' : 'empty-text'}" rows="1" spellcheck="true" placeholder="Text / Untertitel">${esc(l.text)}</textarea>
    <div class="line-tools">
      <span class="grp">Start <button data-a="s-" title="50 ms früher">${ic('minus')}</button><button data-a="s+" title="50 ms später">${ic('plus')}</button><button data-a="sP" title="Start = Playhead (I)">${ic('to-start')}<span>hier</span></button></span>
      <span class="grp">Ende <button data-a="e-" title="50 ms früher">${ic('minus')}</button><button data-a="e+" title="50 ms später">${ic('plus')}</button><button data-a="eP" title="Ende = Playhead (O)"><span>hier</span>${ic('to-end')}</button></span>
      <button data-a="split" title="Am Playhead teilen (S)">${ic('scissors')}<span>Aufteilen</span></button>
      <button data-a="merge" title="Mit nächster Zeile zusammenführen (M)">${ic('merge')}<span>Zusammen</span></button>
      <button data-a="retrans" title="Text dieser Zeile neu erkennen">${ic('retranscribe')}<span>Text neu</span></button>
      <button data-a="pic" title="${esc(l.image ? tf('Anderes Bild vom Playhead nehmen') : tf('Bild dieser Zeile selbst wählen: Playhead an die gewünschte Stelle, dann hier'))}">${ic('image')}<span>${esc(l.image ? tf('Bild neu') : tf('Bild'))}</span></button>${l.image ? `<button data-a="nopic" title="${esc(tf('Wieder das automatisch gewählte Bild nehmen'))}">${ic('undo')}<span>${esc(tf('Bild raus'))}</span></button>` : ''}
      <select data-a="addchar" title="Zweiten Sprecher hinzufügen (spricht gleichzeitig)"><option value="">+ Sprecher</option>${S.p.characters.filter(c => !l.chars.includes(c.id)).map(c => `<option data-nolang value="${c.id}">${esc(c.name)}</option>`).join('')}${S.p.characters.some(c => !l.chars.includes(c.id)) && S.p.characters.length > 2 ? `<option value="__all">${esc(tf('Alle Sprecher (Chor)'))}</option>` : ''}</select>
      <div class="spacer"></div>
      <button data-a="del" title="Zeile löschen (Entf)">${ic('trash')}</button>
    </div>
  </div>`;
}

function visibleLines() {
  const cf = $('#charFilter').value, tf = $('#textFilter').value.trim().toLowerCase();
  return S.p.lines.filter(l => (!cf || l.chars.includes(cf)) && (!tf || l.text.toLowerCase().includes(tf)));
}

function renderLines() {
  const list = $('#lineList');
  const scroll = list.scrollTop;
  const vis = visibleLines();
  const idx = new Map(S.p.lines.map((l, i) => [l.id, i]));
  list.innerHTML = vis.length ? vis.map(l => lineHtml(l, idx.get(l.id))).join('') : '<div class="empty">Keine Zeilen.</div>';
  $$('textarea', list).forEach(autoGrow);
  list.scrollTop = scroll;
  enterItems(list, '.line', 'id', 25);
  S.lastActive = new Set();
  $('#lineCount').textContent = S.p.lines.length;
}

/* Mehrfachauswahl: Strg und Klick nimmt Zeilen dazu. Die zuerst gewählte Zeile bleibt die Hauptzeile,
   nur sie zeigt die Knopfleiste; verschoben wird die ganze Auswahl gemeinsam. */
const isSel = id => S.sel === id || !!S.multi?.has(id);
const selectedLines = () => S.p.lines.filter(l => isSel(l.id));
function markSelection() {
  $$('#lineList .line').forEach(el => {
    el.classList.toggle('sel', el.dataset.id === S.sel);
    el.classList.toggle('msel', el.dataset.id !== S.sel && isSel(el.dataset.id));
  });
  renderMultiBar();
  drawTimeline();
}

/* Leiste über der Liste: zeigt die Auswahl und erlaubt einen Sprecher für alle auf einmal.
   Genau dafür da, wenn zwei gleichzeitig singen: Zeilen mit Strg auswählen, zweiten Sprecher dazu. */
function renderMultiBar() {
  const bar = $('#multiBar'); if (!bar) return;
  const n = selectedLines().length;
  bar.hidden = n < 2;
  if (bar.hidden) return;
  $('#multiCount').textContent = tf('{} Zeilen ausgewählt', n);
  const sel = $('#multiChar');
  sel.innerHTML = `<option value="">${esc(tf('+ Sprecher für alle'))}</option>`
    + S.p.characters.map(c => `<option data-nolang value="${c.id}">${esc(c.name)}</option>`).join('');
}
$('#multiClear').onclick = () => { S.multi.clear(); markSelection(); };
$('#multiChar').onchange = e => {
  const cid = e.target.value;
  e.target.value = '';
  if (!cid) return;
  const lines = selectedLines();
  if (!lines.length) return;
  snapshot();
  let n = 0;
  for (const l of lines) {
    if (l.chars.includes(cid)) continue;
    l.chars = [...l.chars, cid];
    n++;
  }
  afterChange(null, true);
  markSelection();
  toast(tf('{} Zeilen geändert.', n));
};
function toggleSel(id) {
  if (!S.sel || S.sel === id) return;
  S.multi.has(id) ? S.multi.delete(id) : S.multi.add(id);
  markSelection();
}

function selectLine(id, { seekTo = false, scroll = true, reveal = true, keep = false } = {}) {
  S.sel = id;
  if (!keep) S.multi?.clear();
  $$('#lineList .line').forEach(el => {
    el.classList.toggle('sel', el.dataset.id === id);
    el.classList.toggle('msel', el.dataset.id !== id && isSel(el.dataset.id));
  });
  renderMultiBar();
  const l = lineById(id);
  if (!l) return;
  if (seekTo) seek(l.start);
  if (scroll) $(`#lineList .line[data-id="${id}"]`)?.scrollIntoView({ block: 'nearest' });
  if (reveal) ensureVisible(l.start, l.end);
}

function refreshLine(id) {
  const l = lineById(id), el = $(`#lineList .line[data-id="${id}"]`);
  if (!l || !el) return renderLines();
  const i = S.p.lines.indexOf(l);
  const tmp = document.createElement('div'); tmp.innerHTML = lineHtml(l, i);
  const fresh = tmp.firstElementChild;
  el.replaceWith(fresh);
  autoGrow($('textarea', fresh));
}

const list = $('#lineList');
list.addEventListener('mousedown', e => {
  const el = e.target.closest('.line'); if (!el) return;
  if (e.ctrlKey || e.metaKey) {
    e.preventDefault();
    if (!S.sel) selectLine(el.dataset.id, { scroll: false });
    else toggleSel(el.dataset.id);
    return;
  }
  if (S.sel !== el.dataset.id) selectLine(el.dataset.id, { scroll: false });
});
list.addEventListener('click', e => {
  const el = e.target.closest('.line'); if (!el) return;
  const id = el.dataset.id, l = lineById(id);
  if (e.target.closest('.play')) return playRange(l.start, l.end);
  if (e.target.closest('.ltime')) return seek(l.start);
  const rep = e.target.closest('[data-master],[data-first-rep]');
  if (rep) { const t = lineById(rep.dataset.master || rep.dataset.firstRep); if (t) selectLine(t.id, { seekTo: true }); return; }
  const rm = e.target.closest('[data-rm]');
  if (rm) { snapshot(); l.chars = l.chars.filter(c => c !== rm.dataset.rm); afterChange(id); return; }
  const btn = e.target.closest('button[data-a]');
  if (btn) lineAction(id, btn.dataset.a);
});
list.addEventListener('change', async e => {
  const el = e.target.closest('.line'); if (!el) return;
  const id = el.dataset.id, l = lineById(id);
  if (e.target.classList.contains('char-select')) {
    let val = e.target.value;
    if (val === '__new') { val = await askCharacter(); if (!val) { refreshLine(id); return; } }
    setPrimaryChar(l, val);
    afterChange(id, true);
  } else if (e.target.dataset.a === 'addchar' && e.target.value) {
    const v = e.target.value;
    if (v === '__all') setAllChars(l);
    else { commit(x => { x.chars.push(v); }, l); afterChange(id); }
  }
});
list.addEventListener('contextmenu', e => {
  const el = e.target.closest('.line');
  if (!el || e.target.tagName === 'TEXTAREA') return; // im Textfeld normales Browser-Menü
  e.preventDefault();
  const l = lineById(el.dataset.id);
  selectLine(l.id, { scroll: false });
  openMenu(lineMenu(l, l.chars[0], video.currentTime), e.clientX, e.clientY);
});
list.addEventListener('focusin', e => { if (e.target.tagName === 'TEXTAREA') { snapshot(); autoGrow(e.target); } });
list.addEventListener('input', e => {
  if (e.target.tagName !== 'TEXTAREA') return;
  const l = lineById(e.target.closest('.line').dataset.id);
  l.text = e.target.value;
  e.target.classList.toggle('empty-text', !l.text.trim());
  autoGrow(e.target);
  S.lastActive = new Set();
  scheduleSave();
});
list.addEventListener('focusout', e => { if (e.target.tagName === 'TEXTAREA') renderExportChecks(); });
function autoGrow(t) {
  if (!t || !t.offsetParent) return; // unsichtbar (anderer Tab) -> beim Tabwechsel nachholen
  t.style.height = 'auto'; t.style.height = t.scrollHeight + 2 + 'px';
}

function afterChange(id, full = false) {
  sortLines();
  if (full) { renderCharFilter(); renderChars(); }
  if (id && lineById(id) && !full) refreshLine(id); else renderLines();
  S.lastActive = new Set();
  renderExportChecks();
  scheduleSave();
}

/* ------------------------------------------------------------ Überschneidungen */
// Derselbe Sprecher kann nicht zwei Zeilen gleichzeitig sprechen -> Zeilen werden gekappt.
// Verschiedene Sprecher dürfen sich überlappen.
const MIN_LEN = 0.2, GAP = 0.02;
const NEIGHBOUR_GAP = 0.12;  // bis hierhin gelten zwei Zeilen als „aneinander" (gemeinsame Kante)
const r3 = v => +(+v).toFixed(3);

/** Freier Bereich um Zeitpunkt t für die Sprecher `chars` (ohne Zeile excludeId). null = t liegt in einer Zeile. */
function freeGap(chars, t, exclude) {   // exclude: eine Zeilen-ID oder ein Set von IDs
  const skip = exclude instanceof Set ? exclude : new Set([exclude]);
  let lo = 0, hi = S.p.duration;
  for (const o of S.p.lines) {
    if (skip.has(o.id) || !o.chars.some(c => chars.includes(c))) continue;
    if (o.end <= t) lo = Math.max(lo, o.end + GAP);
    else if (o.start >= t) hi = Math.min(hi, o.start - GAP);
    else return null;
  }
  return { lo, hi };
}

/** Zeile in freien Bereich einpassen. Ergebnis: 'ok' | 'trimmed' | 'blocked'. */
function fitLine(l, anchor) {
  const tries = [anchor ?? (l.start + l.end) / 2, l.start + 0.001, l.end - 0.001];
  let g = null;
  for (const t of tries) { g = freeGap(l.chars, t, l.id); if (g) break; }
  if (!g) return 'blocked';
  const s = Math.max(l.start, g.lo, 0), e = Math.min(l.end, g.hi, S.p.duration);
  if (e - s < MIN_LEN) return 'blocked';
  const trimmed = Math.abs(s - l.start) > 0.0005 || Math.abs(e - l.end) > 0.0005;
  l.start = r3(s); l.end = r3(e);
  return trimmed ? 'trimmed' : 'ok';
}

const TRIM_MSG = 'Gekürzt: Derselbe Sprecher kann nicht zwei Zeilen gleichzeitig sprechen.';
const BLOCK_MSG = 'Geht nicht: Dieser Sprecher hat an der Stelle schon eine Zeile.';

/** Änderung an bestehender Zeile durchführen, einpassen, rückgängig-fähig machen. */
/** Chor: alle Sprecher sagen die Zeile gleichzeitig (im Spiel „viele reden durcheinander“). */
function setAllChars(l) {
  if (commit(x => { x.chars = [x.chars[0], ...S.p.characters.map(c => c.id)]; }, l)) afterChange(l.id);
  else refreshLine(l.id);
}

function commit(mutate, l, { anchor } = {}) {
  const snap = JSON.stringify({ lines: S.p.lines, characters: S.p.characters });
  const before = { start: l.start, end: l.end, chars: [...l.chars] };
  mutate(l);
  l.chars = [...new Set(l.chars)];
  const r = fitLine(l, anchor);
  if (r === 'blocked') { Object.assign(l, before); toast(BLOCK_MSG, true); return false; }
  S.undo.push(snap); if (S.undo.length > 200) S.undo.shift(); S.redo = [];
  if (r === 'trimmed') toast(TRIM_MSG);
  return true;
}

/** Neue Zeile einfügen (eingepasst). */
function insertLine(l, anchor, { quiet = false } = {}) {
  const r = fitLine(l, anchor);
  if (r === 'blocked') { if (!quiet) toast(BLOCK_MSG, true); return false; }
  if (!quiet) snapshot();   // beim Einfügen mehrerer Zeilen reicht ein Stand für alle
  S.p.lines.push(l);
  afterChange(null);
  if (!quiet) selectLine(l.id);
  if (r === 'trimmed' && !quiet) toast(TRIM_MSG);
  return true;
}

function setPrimaryChar(l, cid) {
  return commit(x => { x.chars = [cid, ...x.chars.slice(1).filter(c => c !== cid)]; }, l);
}

/** Zeilen desselben Sprechers, die sich überschneiden (z. B. aus älteren Projekten). */
function sameCharOverlaps() {
  const out = [], byChar = {};
  for (const l of S.p.lines) for (const c of l.chars) (byChar[c] ??= []).push(l);
  for (const arr of Object.values(byChar)) {
    arr.sort((a, b) => a.start - b.start);
    for (let i = 1; i < arr.length; i++) if (arr[i].start < arr[i - 1].end - 0.001) out.push(arr[i]);
  }
  return out;
}

/* ------------------------------------------------------------ Stimm-Erkennung in der Wellenform */
function voiceThreshold(i0, i1) {
  const pk = S.peaks.stimmen;
  let m = 0;
  for (let i = Math.max(0, i0); i < Math.min(pk.length, i1); i++) if (pk[i] > m) m = pk[i];
  return Math.max(14, m * 0.15);
}

/** Schwellen aus dem Bereich [a,b): sicherer Sprech-Pegel und weicher Pegel für An-/Ausklang. */
function voiceLevels(a, b) {
  const pk = S.peaks.stimmen;
  const seg = Array.from(pk.slice(Math.max(0, a), Math.min(pk.length, b))).sort((x, y) => x - y);
  if (seg.length < 3) return null;
  const floor = seg[Math.floor(seg.length * 0.1)], peak = seg[seg.length - 1];
  if (peak - floor < 10) return null;                     // nichts Deutliches zu hören
  return {
    floor, peak,
    thr: Math.max(floor + (peak - floor) * 0.18, peak * 0.12, 8),
    soft: Math.max(floor + (peak - floor) * 0.06, peak * 0.05, 4),
  };
}

/** Echter Sprechbeginn: deutlichste Flanke nahe `ref`, die 30 ms laut bleibt; weiche Anlaute davor mitnehmen. */
function voiceOnset(from, to, lv, ref) {
  const pk = S.peaks.stimmen, fps = S.peaks.fps, hold = Math.max(2, Math.round(0.03 * fps));
  const ctx = Math.round(0.15 * fps), lo = Math.max(0, from), hi = Math.min(pk.length - hold, to);
  const mean = (i0, i1) => { let s = 0, n = 0; for (let i = Math.max(0, i0); i < Math.min(pk.length, i1); i++) { s += pk[i]; n++; } return n ? s / n : 0; };
  let onset = -1, best = -1e9;
  for (let i = lo; i < hi; i++) {
    let ok = true;
    for (let k = 0; k < hold; k++) if (pk[i + k] < lv.thr) { ok = false; break; }
    if (!ok) continue;
    // Flankenhöhe in Stufen der Wellenform, Abstand zur Schätzung kostet
    const rise = mean(i, i + ctx) - mean(i - ctx, i);
    const sc = rise - (ref == null ? 0 : 60 * Math.abs(i - ref) / fps);
    if (sc > best) { onset = i; best = sc; }
  }
  if (onset < 0) return -1;
  let s = onset; const limit = Math.max(lo, onset - Math.round(0.10 * fps));
  while (s > limit && pk[s - 1] >= lv.soft) s--;          // weicher Anlaut davor
  return s;
}

/** Ende des Ausklangs ab dem letzten lauten Punkt vor `to`. */
function voiceOffset(from, to, lv) {
  const pk = S.peaks.stimmen, fps = S.peaks.fps;
  let last = -1;
  for (let i = Math.max(0, from); i < Math.min(pk.length, to); i++) if (pk[i] >= lv.thr) last = i;
  if (last < 0) return -1;
  const limit = Math.min(pk.length - 1, to - 1, last + Math.round(0.1 * fps));
  while (last < limit && pk[last + 1] >= lv.soft) last++;
  return last;
}

/** Zusammenhängende Stimme um t finden (für neue Zeilen). null = dort ist keine Stimme. */
function voiceSpanAround(t) {
  if (!S.peaks) return null;
  const pk = S.peaks.stimmen, fps = S.peaks.fps, i0 = Math.round(t * fps);
  const thr = voiceThreshold(i0 - 3 * fps, i0 + 3 * fps);
  let c = -1;
  for (let d = 0; d <= 0.25 * fps; d++) {
    if (pk[i0 + d] >= thr) { c = i0 + d; break; }
    if (i0 - d >= 0 && pk[i0 - d] >= thr) { c = i0 - d; break; }
  }
  if (c < 0) return null;
  const quiet = Math.round(0.3 * fps);
  let s = c, run = 0;
  while (s > 0 && c - s < 8 * fps) { if (pk[s - 1] < thr) { if (++run >= quiet) break; } else run = 0; s--; }
  s += run;
  let e = c; run = 0;
  while (e < pk.length - 1 && e - c < 10 * fps) { if (pk[e + 1] < thr) { if (++run >= quiet) break; } else run = 0; e++; }
  e -= run;
  const lv = voiceLevels(s - Math.round(0.3 * fps), e + Math.round(0.3 * fps));
  if (lv) {  // Ränder genau auf Sprechbeginn/-ende legen
    const on = voiceOnset(s - Math.round(0.2 * fps), s + Math.round(0.25 * fps), lv, s);
    if (on >= 0) s = on;
    const off = voiceOffset(Math.max(s, e - Math.round(0.3 * fps)), e + Math.round(0.35 * fps), lv);
    if (off >= 0) e = off;
  }
  return { s: Math.max(0, s / fps - 0.05), e: Math.min(S.p.duration, (e + 1) / fps + 0.1) };
}

/** Anfang/Ende einer Zeile an die tatsächliche Stimme anpassen (Stille abschneiden bzw. Ränder erweitern). */
function fitToVoice(l) {
  if (!S.peaks) return toast('Wellenform noch nicht geladen.', true);
  const pk = S.peaks.stimmen, fps = S.peaks.fps;
  const a = Math.max(0, Math.round((l.start - 0.35) * fps)), b = Math.min(pk.length, Math.round((l.end + 0.5) * fps));
  const lv = voiceLevels(a, b);
  const s = lv && voiceOnset(a, Math.round((l.start + 0.35) * fps), lv, Math.round(l.start * fps));
  const e = lv && voiceOffset(a, b, lv);
  if (!lv || s < 0 || e < 0) return toast('In dieser Zeile ist keine Stimme zu hören.', true);
  if (commit(x => { x.start = r3(Math.max(0, s / fps - 0.05)); x.end = r3(Math.min(S.p.duration, (e + 1) / fps + 0.1)); }, l)) {
    afterChange(l.id);
  }
}

/* ------------------------------------------------------------ Zeilen-Aktionen */
function lineAction(id, a) {
  const l = lineById(id); if (!l) return;
  const t = video.currentTime, step = 0.05;
  const i = S.p.lines.indexOf(l);
  let ok = true;
  switch (a) {
    case 's-': ok = commit(x => { x.start = r3(Math.max(0, x.start - step)); }, l, { anchor: l.end - 0.001 }); break;
    case 's+': ok = commit(x => { x.start = r3(Math.min(x.end - MIN_LEN, x.start + step)); }, l, { anchor: l.end - 0.001 }); break;
    case 'e-': ok = commit(x => { x.end = r3(Math.max(x.start + MIN_LEN, x.end - step)); }, l, { anchor: l.start + 0.001 }); break;
    case 'e+': ok = commit(x => { x.end = r3(Math.min(S.p.duration, x.end + step)); }, l, { anchor: l.start + 0.001 }); break;
    case 'sP':
      if (t >= l.end - MIN_LEN) return toast('Playhead liegt hinter dem Zeilenende.', true);
      ok = commit(x => { x.start = r3(t); }, l, { anchor: l.end - 0.001 }); break;
    case 'eP':
      if (t <= l.start + MIN_LEN) return toast('Playhead liegt vor dem Zeilenanfang.', true);
      ok = commit(x => { x.end = r3(t); }, l, { anchor: l.start + 0.001 }); break;
    case 'split': return splitLine(l, t);
    case 'merge': return mergeLine(l);
    case 'retrans': return retranscribe(l);
    case 'pic': return pickLineImage(l, t);
    case 'nopic':
      snapshot();
      delete l.image;
      refreshLine(id);
      scheduleSave();
      return;
    case 'voice': return fitToVoice(l);
    case 'copy': return copyLine(l);
    case 'cut': return copyLine(l, true);
    case 'del': return deleteLine(l);
    default: return;
  }
  if (!ok) return;
  afterChange(id);
  if (a.startsWith('s')) seek(l.start);
}

/* Bild einer Zeile selbst wählen. Sonst sucht der Export das Bild automatisch kurz nach dem
   Zeilenanfang; manchmal passt die Einstellung dort aber nicht zur Szene. */
async function pickLineImage(l, t) {
  try {
    const r = await api('POST', `/api/projects/${encodeURIComponent(S.pid)}/frame`,
                        { time: t, target: `zeile_${S.p.lines.indexOf(l) + 1}` });
    snapshot();
    l.image = r.image;
    refreshLine(l.id);
    scheduleSave();
    toast(tf('Bild für Zeile {} gespeichert.', S.p.lines.indexOf(l) + 1));
  } catch (e) { toast(e.message, true); }
}

async function deleteLine(l, ask = true) {
  if (ask && setting('confirm_delete')) {
    const r = await dialog({ title: tf('Zeile {} löschen?', S.p.lines.indexOf(l) + 1), text: l.text ? `„${l.text}“` : '',
                             raw: true, tone: 'danger', ok: 'Löschen', check: { label: 'Nicht mehr fragen' } });
    if (!r.value) return;
    if (r.checked) saveSetting('confirm_delete', false);
  }
  const el = $(`#lineList .line[data-id="${CSS.escape(l.id)}"]`);
  if (el && motionOK()) {
    try {
      const anim = el.animate([{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateX(-14px) scale(.98)' }],
                              { duration: 170, easing: 'ease-in', fill: 'forwards' });
      await Promise.race([anim.finished, new Promise(r => setTimeout(r, 260))]);
    } catch { /* egal */ }
  }
  const i = S.p.lines.indexOf(l);
  if (i < 0) return;
  snapshot();
  S.p.lines.splice(i, 1);
  S.p.lines.forEach(o => { if (o.repeat_of === l.id) delete o.repeat_of; });   // Vorlage weg: eigene Aufnahme
  const next = S.p.lines[i] || S.p.lines[i - 1];
  S.sel = next ? next.id : null;
  afterChange(null);
  if (next) selectLine(next.id, { scroll: false });
}

/* Zeile teilen: geschnitten wird genau an der gewählten Stelle. Welcher Text in welche Hälfte kommt, entscheiden
   die erkannten Wörter (Wörter des Textes auf die erkannten abgebildet, Wortmitte vor oder nach dem Schnitt).
   Nur nach Anteilen („60 % der Zeit, also 60 % der Wörter“) rutschte sonst ein Wort auf die falsche Seite, wenn im
   Text etwas anderes stand als erkannt (nachbearbeitet, Lacher, zweiter Sprecher im selben Zeitraum). */
const wordKey = s => (s || '').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');

/** Längste gemeinsame Folge zweier Wortlisten -> Paare [Text-Wort, erkanntes Wort] in Reihenfolge. */
function lcsPairs(a, b) {
  const n = a.length, m = b.length;
  if (!n || !m) return [];
  const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = (a[i] && a[i] === b[j]) ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const out = [];
  for (let i = 0, j = 0; i < n && j < m;) {
    if (a[i] && a[i] === b[j]) { out.push([i, j]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) i++;
    else j++;
  }
  return out;
}

/** Zeit je Wort im Text der Zeile (aus der Erkennung, Unbekanntes dazwischen geschätzt) oder null. */
function lineWordTimes(l) {
  const tokens = (l.text || '').split(/\s+/).filter(Boolean);
  const words = (S.words || []).filter(w => w.e > l.start + 0.02 && w.s < l.end - 0.02 && !w.sound);
  if (tokens.length < 2 || words.length < 2) return null;
  const pairs = lcsPairs(tokens.map(wordKey), words.map(w => wordKey(w.w)));
  if (pairs.length < 2) return null;
  const times = new Array(tokens.length).fill(null);
  for (const [i, j] of pairs) times[i] = { s: words[j].s, e: words[j].e };
  let lastKnown = { s: l.start, e: l.start };
  for (let i = 0; i < times.length; i++) {
    if (times[i]) { lastKnown = times[i]; continue; }
    let j = i;
    while (j < times.length && !times[j]) j++;
    const next = times[j] || { s: l.end, e: l.end };   // Lücke gleichmäßig auf die unbekannten Wörter verteilen
    const step = (next.s - lastKnown.e) / (j - i + 1);
    for (let k = i; k < j; k++) times[k] = { s: lastKnown.e + step * (k - i), e: lastKnown.e + step * (k - i + 1) };
    i = j - 1;
  }
  return times;
}

function splitLine(l, t) {
  if (l.end - l.start < 2 * MIN_LEN + 0.02) return toast('Diese Zeile ist zu kurz zum Teilen.', true);
  if (t <= l.start + 0.1 || t >= l.end - 0.1) return toast('Zum Teilen die Stelle innerhalb der Zeile wählen.', true);
  t = clamp(t, l.start + MIN_LEN + GAP / 2, l.end - MIN_LEN - GAP / 2);   // beide Hälften bleiben lang genug fürs Spiel
  snapshot();
  const tokens = l.text.split(/\s+/).filter(Boolean);
  const times = lineWordTimes(l);
  const cut = times ? clamp(times.filter(w => (w.s + w.e) / 2 < t).length, 1, tokens.length - 1)
    : clamp(Math.round(tokens.length * (t - l.start) / (l.end - l.start)), 1, Math.max(1, tokens.length - 1));
  const e1 = t - GAP / 2, s2 = t + GAP / 2;   // die übliche kleine Lücke zwischen zwei Zeilen, mittig um den Schnitt
  const second = { id: newId(), start: r3(s2), end: l.end, text: tokens.slice(cut).join(' '), chars: [...l.chars] };
  if (l.laugh) second.laugh = l.laugh;
  if (l.repeat_of) second.repeat_of = l.repeat_of;   // geteilte Wiederholung bleibt eine Wiederholung
  // Wird eine Vorlage geteilt, spielen ihre Wiederholungen sonst nur noch die erste Hälfte: sie bekommen
  // wieder eine eigene Aufnahme
  const freed = S.p.lines.filter(o => o.repeat_of === l.id);
  freed.forEach(o => delete o.repeat_of);
  l.end = r3(e1); l.text = tokens.slice(0, cut).join(' ');
  S.p.lines.push(second);
  if (freed.length) toast(tf('{} Wiederholung(en) haben jetzt eine eigene Aufnahme.', freed.length));
  afterChange(null);
  selectLine(second.id);
}

/** Mit der nächsten Zeile desselben Sprechers zusammenführen. */
function mergeLine(l) {
  const c0 = l.chars[0];
  const n = S.p.lines.filter(o => o !== l && o.chars.includes(c0) && o.start >= l.start).sort((a, b) => a.start - b.start)[0];
  if (!n) return toast(`Keine weitere Zeile von ${charById(c0)?.name || 'diesem Sprecher'}.`, true);
  snapshot();
  l.end = Math.max(l.end, n.end);
  l.text = [l.text, n.text].filter(s => s.trim()).join(' ');
  n.chars.forEach(c => { if (!l.chars.includes(c)) l.chars.push(c); });
  S.p.lines.splice(S.p.lines.indexOf(n), 1);
  if (fitLine(l, l.start + 0.001) === 'trimmed') toast(TRIM_MSG);
  afterChange(null);
  selectLine(l.id);
}

/** Neue Zeile bei t. Passt sich an die Stimme an, falls dort gesprochen wird. */
function addLineAt(t, charId, span) {
  const sel = lineById(S.sel);
  let cid = charId || sel?.chars[0] || S.p.characters[0]?.id;
  if (!cid) cid = addCharacter();
  const v = span || voiceSpanAround(t) || { s: t, e: t + 1.5 };
  const l = { id: newId(), start: r3(Math.max(0, v.s)), end: r3(Math.min(S.p.duration, v.e)), text: '', chars: [cid] };
  if (!insertLine(l, clamp(t, l.start + 0.001, l.end - 0.001))) return null;
  autoText(l);
  return l;
}
$('#btnAddLine').onclick = () => addLineAt(video.currentTime);

/* Wiederholungen automatisch finden */
$('#btnRepeats').onclick = async () => {
  await flushSave();
  try {
    const r = await api('POST', `/api/projects/${encodeURIComponent(S.pid)}/repeats`);
    snapshot();
    S.p.lines = r.project.lines; sortLines();
    renderAll();
    toast(r.count ? `${r.count} Wiederholung(en) gefunden. Sie teilen sich jetzt eine Aufnahme (Strg+Z macht es rückgängig).`
                  : 'Keine wiederholten Sätze gefunden.', false, 5000);
  } catch (e) { toast(e.message, true); }
};

/* Lachen suchen (bestehendes Projekt) */

/* ---------- Zeilen übersetzen ----------
   Der Ton bleibt, wie er ist: übersetzt wird nur der Text, den die Spieler beim Nachsprechen lesen.
   So entsteht aus einem Video dasselbe Pack in mehreren Sprachen. Das Original bleibt in „text_src“
   stehen, damit eine zweite Übersetzung wieder davon ausgeht und man zurück kann. */

$('#btnTranslate').onclick = async () => {
  const btn = $('#btnTranslate');
  btn.disabled = true;
  try {
    await flushSave();
    const info = await api('GET', `/api/projects/${encodeURIComponent(S.pid)}/translate`);
    const from = info.language ? (info.languages[info.language] || info.language) : null;
    const hasOrig = S.p.lines.some(l => l.text_src);
    const opts = Object.entries(info.languages).filter(([code]) => code !== info.language)
      .map(([code, name]) => `<option value="${code}">${esc(name)}</option>`).join('');
    const ways = info.methods.map((m, i) => `<label class="radio${m.ready ? '' : ' off'}">
        <input type="radio" name="trWay" value="${m.id}" ${m.ready && !info.methods.slice(0, i).some(x => x.ready) ? 'checked' : ''} ${m.ready ? '' : 'disabled'}>
        ${esc(tf(m.name))}${m.service ? ` (${esc(m.service)})` : ''} <span class="muted small">${esc(tf(m.hint))}</span></label>`).join('');
    const ask = dialog({
      title: tf('Zeilen übersetzen'),
      icon: 'globe',
      wide: true,
      html: `<span class="tr-box">
          <span class="small muted">${esc(from ? tf('Erkannte Sprache: {}. Der Ton bleibt unverändert, nur der Text wird übersetzt.', from)
                                              : tf('Der Ton bleibt unverändert, nur der Text wird übersetzt.'))}</span>
          <label class="tr-lang">${esc(tf('Zielsprache'))} <select class="tr-select">${opts}</select></label>
          <span class="tr-ways">${ways}</span>
        </span>`,
      buttons: [...(hasOrig ? [{ label: tf('Original zurück'), value: 'orig', side: 'left' }] : []),
                { label: tf('Abbrechen'), value: null }, { label: tf('Übersetzen'), value: true, main: true }],
    });
    // Felder jetzt merken: nach dem Schließen sind sie nicht mehr in der Seite
    const sel = $('.tr-select');
    const radios = [...document.querySelectorAll('input[name="trWay"]')];
    const v = await ask;
    if (v === 'orig') return restoreOriginal();
    if (!v) return;
    const target = sel.value;
    const method = (radios.find(r => r.checked) || {}).value || 'free';
    const { job } = await api('POST', `/api/projects/${encodeURIComponent(S.pid)}/translate`, { target, method });
    toast(tf('Wird übersetzt … (Fortschritt oben rechts)'));
    const res = await waitJob(job);
    applyTranslation(res);
  } catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; }
};

function applyTranslation(res) {
  const got = new Map((res.lines || []).map(x => [x.id, x.text]));
  let changed = 0;
  snapshot();
  for (const l of S.p.lines) {
    const t = (got.get(l.id) || '').trim();
    if (!t || t === l.text) continue;
    if (!l.text_src) l.text_src = l.text;   // Original merken, nur beim ersten Mal
    l.text = t;
    changed++;
  }
  if (!changed) { S.undo.pop(); return toast(tf('Es kam keine Übersetzung zurück.'), true); }
  afterChange(null, true);
  const miss = res.leer ? ' ' + tf('{} Zeilen blieben ohne Text.', res.leer) : '';
  toast(tf('{} Zeilen übersetzt ({}).', changed, res.source) + miss + ' ' + tf('Strg+Z macht es rückgängig.'), false, 6000);
}

function restoreOriginal() {
  snapshot();
  let n = 0;
  for (const l of S.p.lines) {
    if (!l.text_src) continue;
    l.text = l.text_src;
    delete l.text_src;
    n++;
  }
  if (!n) { S.undo.pop(); return; }
  afterChange(null, true);
  toast(tf('{} Zeilen wieder im Original.', n));
}

$('#btnLaughs').onclick = async () => {
  const btn = $('#btnLaughs');
  btn.disabled = true;
  try {
    await flushSave();
    const { job } = await api('POST', `/api/projects/${encodeURIComponent(S.pid)}/laughs`);
    toast('Suche Lacher … (Fortschritt oben rechts)');
    const res = await waitJob(job);
    const found = res?.laughs || [];
    const snap = JSON.stringify({ lines: S.p.lines, characters: S.p.characters });
    let added = 0, first = null;
    for (const item of found) {
      if (!item.chars.length || !charById(item.chars[0])) item.chars = [S.p.characters[0]?.id || addCharacter()];
      // bereits vorhandene Lach-Zeile an gleicher Stelle nicht doppelt anlegen
      if (S.p.lines.some(o => o.laugh && Math.abs(o.start - item.start) < 0.3)) continue;
      const l = { id: newId(), start: r3(item.start), end: r3(item.end), text: item.text, chars: item.chars, laugh: true };
      if (fitLine(l) === 'blocked') continue;
      S.p.lines.push(l); added++; first ??= l;
    }
    if (added) {
      S.undo.push(snap); S.redo = [];
      afterChange(null, true);
      selectLine(first.id, { seekTo: true });
    }
    toast(added ? `${added} Lacher als Zeile „${found[0].text}“ hinzugefügt (Strg+Z macht es rückgängig).` : 'Keine neuen Lacher gefunden.', false, 5000);
  } catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; }
};

/* Kopieren / Einfügen */
/* Kopieren nimmt die ganze Strg-Auswahl mit. Gemerkt werden die Abstände zur ersten Zeile,
   beim Einfügen liegt die erste am Playhead und die anderen im selben Abstand dahinter. */
function copyLine(l, cut = false) {
  const sel = selectedLines();
  const lines = (sel.length > 1 && isSel(l.id) ? sel : [l]).slice().sort((a, b) => a.start - b.start);
  const t0 = lines[0].start;
  S.clip = { items: lines.map(x => ({ text: x.text, chars: [...x.chars], off: x.start - t0, len: x.end - x.start })) };
  try { navigator.clipboard?.writeText(lines.map(x => x.text).join('\n')); } catch { /* egal */ }
  if (cut) { for (const x of lines.slice().reverse()) deleteLine(x, false); }
  const n = lines.length;
  toast(cut ? (n > 1 ? tf('{} Zeilen ausgeschnitten.', n) : tf('Zeile ausgeschnitten.'))
            : (n > 1 ? tf('{} Zeilen kopiert. Mit Strg+V am Playhead einfügen.', n)
                     : tf('Zeile kopiert. Mit Strg+V am Playhead einfügen.')), false, 2500);
}
function pasteAt(t, charId) {
  if (!S.clip?.items?.length) return toast('Nichts kopiert (Zeile auswählen, Strg+C).', true);
  const many = S.clip.items.length > 1;
  if (many) snapshot();   // ein Rückgängig für den ganzen Block
  const fresh = [];
  let done = 0, blocked = 0;
  for (const it of S.clip.items) {
    let chars = charId ? [charId] : it.chars.filter(c => charById(c));
    if (!chars.length) chars = [S.p.characters[0]?.id].filter(Boolean);
    const s = r3(Math.min(t + it.off, Math.max(0, S.p.duration - it.len)));
    const l = { id: newId(), start: s, end: r3(Math.min(S.p.duration, s + it.len)), text: it.text, chars };
    if (insertLine(l, s + 0.001, { quiet: many })) { done++; fresh.push(l.id); } else blocked++;
  }
  if (many) {
    if (fresh.length) { S.sel = fresh[0]; S.multi = new Set(fresh.slice(1)); markSelection(); }
    toast(blocked ? tf('{} Zeilen eingefügt, {} passten nicht.', done, blocked) : tf('{} Zeilen eingefügt.', done),
          !!blocked && !done, 4000);
  }
}

/* Text neuer Zeilen automatisch erkennen */
const autoTextBox = $('#autoText');
autoTextBox.checked = !!setting('auto_text');
autoTextBox.onchange = () => saveSetting('auto_text', autoTextBox.checked);   // gilt auch in den Einstellungen
function autoText(l) {
  if (autoTextBox.checked && l.end - l.start >= 0.4) retranscribe(l, { onlyIfEmpty: true });
  else $(`#lineList .line[data-id="${l.id}"] textarea`)?.focus();
}

async function retranscribe(l, { onlyIfEmpty = false } = {}) {
  const ta = () => $(`#lineList .line[data-id="${l.id}"] textarea`);
  if (ta()) ta().placeholder = 'Text wird erkannt …';
  try {
    const { job } = await api('POST', `/api/projects/${encodeURIComponent(S.pid)}/retranscribe`,
      { start: l.start, end: l.end, language: S.p.language, line: l.id });
    if (!onlyIfEmpty) toast('Text wird neu erkannt…');
    const res = await waitJob(job);
    const line = lineById(res.line);
    if (!line) return;
    if (onlyIfEmpty && line.text.trim()) return;
    if (!res.text) { if (!onlyIfEmpty) toast('Kein Text erkannt.', true); return; }
    snapshot(); line.text = res.text; afterChange(line.id);
    if (!onlyIfEmpty) toast('Text aktualisiert.');
  } catch (e) { toast(e.message, true); }
  finally { if (ta()) ta().placeholder = 'Text / Untertitel'; }
}

async function waitJob(jid) {
  for (;;) {
    await new Promise(r => setTimeout(r, 700));
    const j = await api('GET', `/api/jobs/${jid}`);
    if (j.state === 'fertig') return j.result;
    if (j.state === 'fehler') throw new Error(j.error);
    if (j.state === 'abgebrochen') throw new Error('Abgebrochen');
  }
}

/* ------------------------------------------------------------ Charaktere */
/* Standardname einer Figur („Speaker 3“) in der Sprache der Oberfläche, gleiche Wörter wie auf dem Server
   (pipeline/project.py SPEAKER_WORD). Einzahl, deshalb nicht über die Übersetzung von „Sprecher“. */
const SPEAKER_WORD = { de: 'Sprecher', en: 'Speaker', es: 'Hablante', fr: 'Locuteur', pt: 'Falante', it: 'Parlante',
  ru: 'Спикер', pl: 'Mówca', tr: 'Konuşmacı', nl: 'Spreker', ja: '話者', zh: '说话人', ko: '화자', uk: 'Мовець',
  id: 'Pembicara', hi: 'वक्ता', cs: 'Mluvčí', sk: 'Hovoriaci', sr: 'Govornik', sv: 'Talare', da: 'Taler',
  ro: 'Vorbitor', hu: 'Beszélő', el: 'Ομιλητής', vi: 'Người nói', th: 'ผู้พูด' };
const speakerWord = () => SPEAKER_WORD[window.VT_I18N?.lang] || 'Speaker';
// noch nicht benannte Figur, egal in welcher Sprache angelegt
const UNNAMED_RX = new RegExp(`^(${Object.values(SPEAKER_WORD).join('|')}) [0-9]+$`);

function addCharacter(name) {
  name = name || `${speakerWord()} ${S.p.characters.length + 1}`;
  snapshot();
  let n = S.p.characters.length + 1;
  while (S.p.characters.some(c => c.id === 'c' + n)) n++;
  const colors = ['#4f8cff', '#ff6b6b', '#3ecf8e', '#f5a524', '#b57bff', '#ff8fd1', '#22c3d6', '#c8d64a', '#ff9f5a', '#8fa3bf', '#e05ce0', '#5ad19a'];
  const c = { id: 'c' + n, name: name.trim(), color: colors[(n - 1) % colors.length], image: null };
  S.p.characters.push(c);
  renderCharFilter(); renderChars(); scheduleSave();
  return c.id;
}
/** Fragt nach dem Namen und legt den Charakter an (null = abgebrochen). */
async function askCharacter() {
  const name = await dialog({ title: 'Neuer Charakter', text: 'Name des neuen Charakters:', icon: 'user-plus',
                              input: `${speakerWord()} ${S.p.characters.length + 1}`, required: true, ok: 'Anlegen', maxLength: 80 });
  return name ? addCharacter(name) : null;
}
$('#btnAddChar').onclick = async () => { if (await askCharacter()) renderLines(); };

function renderChars() {
  const counts = {};
  S.p.lines.forEach(l => l.chars.forEach(c => (counts[c] = (counts[c] || 0) + 1)));
  // Nur eine Stimme erkannt: Hinweis auf „Anzahl angeben“ (verrauschte, geschriene Stimmen trennt die
  // Automatik oft nicht, mit vorgegebener Anzahl klappt es meist)
  $('#oneVoiceHint').hidden = !(S.p.characters.length === 1 && S.p.lines.length >= 4);
  $('#charList').innerHTML = S.p.characters.map((c, i) => `
    <div class="char" data-id="${c.id}" style="--c:${c.color}">
      <input type="color" value="${c.color}" title="Farbe">
      <input class="cname-in" value="${esc(c.name)}" title="Name (Taste ${i + 1} weist zu)">
      <button class="btn small play1" title="Erste Zeile anhören">${ic('play')}</button>
      <div class="cmeta">
        ${c.image ? `<img src="${imgUrl(c.image)}" alt="">` : ''}
        <span class="muted">${counts[c.id] || 0} Zeilen${i < 9 ? ' · Taste ' + (i + 1) : ''}</span>
        <button class="btn small frame" title="Aktuelles Videobild als Charakterbild">${ic('camera')}<span>Bild = Frame</span></button>
        ${c.image ? '<button class="btn small noimg">Bild entfernen</button>' : ''}
        <button class="btn small only">Nur diese Zeilen</button>
        <select class="merge"><option value="">Zusammenführen mit…</option>${mergeOptions(c)}</select>
        ${counts[c.id] ? '' : '<button class="btn small danger remove">Entfernen</button>'}
      </div>
    </div>`).join('');
  $('#charCount').textContent = S.p.characters.length;
}

/* Zusammenführen: ähnlichste Stimmen zuerst (Voicitool trennt lieber zu viele Sprecher;
   die ähnlichste Stimme ist oft dieselbe Person). Ähnlichkeit kommt vom Server (/voices). */
function mergeOptions(c) {
  const others = S.p.characters.filter(o => o.id !== c.id);
  const sims = Object.fromEntries((S.voices?.[c.id] || []).map(([id, v]) => [id, v]));
  others.sort((a, b) => (sims[b.id] ?? -1) - (sims[a.id] ?? -1));
  const top = others.length > 1 && sims[others[0].id] != null ? others[0].id : null;
  return others.map(o => `<option data-nolang value="${o.id}">${esc(o.name)}${o.id === top ? ' · ' + esc(tf('ähnlichste Stimme')) : ''}</option>`).join('');
}
async function loadVoices() {
  if (!S.pid) return;
  const pid = S.pid;
  try {
    const v = await api('GET', `/api/projects/${encodeURIComponent(pid)}/voices`);
    if (S.pid === pid) { S.voices = v; renderChars(); }
  } catch { /* ältere Projekte ohne Stimmprofile */ }
}

$('#btnOneVoice').onclick = () => {
  const box = $('#reclusterBox');
  box.open = true;
  $('#reclusterN').value = 2;
  box.scrollIntoView({ block: 'nearest', behavior: motionOK() ? 'smooth' : 'auto' });
  $('#reclusterN').focus();
};

const charList = $('#charList');
charList.addEventListener('focusin', e => { if (e.target.matches('.cname-in, input[type=color]')) snapshot(); });
charList.addEventListener('input', e => {
  const c = charById(e.target.closest('.char').dataset.id);
  if (e.target.classList.contains('cname-in')) c.name = e.target.value;
  else if (e.target.type === 'color') { c.color = e.target.value; e.target.closest('.char').style.setProperty('--c', c.color); }
  S.lastActive = new Set();
  scheduleSave();
});
charList.addEventListener('change', async e => {
  const c = charById(e.target.closest('.char').dataset.id);
  if (e.target.classList.contains('merge') && e.target.value) {
    const into = charById(e.target.value);
    if (!await dialog({ title: tf('„{}" mit „{}" zusammenführen?', c.name, into.name), text: tf('Alle Zeilen gehen an „{}".', into.name),
                       icon: 'merge', ok: 'Zusammenführen' })) { e.target.value = ''; return; }
    snapshot();
    S.p.lines.forEach(l => { l.chars = [...new Set(l.chars.map(x => (x === c.id ? into.id : x)))]; });
    S.p.characters = S.p.characters.filter(x => x.id !== c.id);
    renderAll();
    await flushSave();
    loadVoices();   // Ähnlichkeiten nach dem Zusammenführen neu berechnen
    return;
  }
  renderAll(); scheduleSave();
});
charList.addEventListener('click', async e => {
  const el = e.target.closest('.char'); if (!el) return;
  const c = charById(el.dataset.id);
  if (e.target.closest('.play1')) {
    const l = S.p.lines.find(x => x.chars.includes(c.id)); if (l) playRange(l.start, l.end);
  } else if (e.target.closest('.frame')) {
    try {
      const r = await api('POST', `/api/projects/${encodeURIComponent(S.pid)}/frame`, { time: video.currentTime, target: c.id });
      snapshot(); c.image = r.image; renderChars(); scheduleSave();
      if (S.p.export.image_mode === 'frame') toast('Tipp: Im Export „Charakterbild" wählen, damit das Bild genutzt wird.', false, 5000);
    } catch (err) { toast(err.message, true); }
  } else if (e.target.closest('.noimg')) {
    snapshot(); c.image = null; renderChars(); scheduleSave();
  } else if (e.target.closest('.only')) {
    $('#charFilter').value = c.id; switchTab('lines'); renderLines();
  } else if (e.target.closest('.remove')) {
    snapshot(); S.p.characters = S.p.characters.filter(x => x.id !== c.id); renderAll(); scheduleSave();
  }
});
charList.addEventListener('focusout', e => { if (e.target.classList.contains('cname-in')) { renderCharFilter(); renderLines(); renderExportChecks(); } });

$('#btnRecluster').onclick = async () => {
  if (!await dialog({ title: 'Sprecher neu zuordnen?', text: 'Namen und manuelle Zuordnungen gehen verloren.', tone: 'warn', ok: 'Neu zuordnen' })) return;
  await flushSave();
  try {
    const { job } = await api('POST', `/api/projects/${encodeURIComponent(S.pid)}/recluster`, { speakers: $('#reclusterN').value });
    await waitJob(job);
    await reloadProject('Sprecher neu zugeordnet.');
  } catch (e) { toast(e.message, true); }
};
$('#btnResegment').onclick = async () => {
  if (!await dialog({ title: 'Alle Zeilen neu aufteilen?', text: 'Manuelle Änderungen an Zeilen gehen verloren.', tone: 'warn', ok: 'Neu aufteilen' })) return;
  await flushSave();
  try {
    await api('POST', `/api/projects/${encodeURIComponent(S.pid)}/resegment`, { target_len: $('#segTarget').value, pause_split: $('#segPause').value });
    await reloadProject('Zeilen neu aufgeteilt.');
  } catch (e) { toast(e.message, true); }
};
async function reloadProject(msg) {
  snapshot();
  const fresh = await api('GET', `/api/projects/${encodeURIComponent(S.pid)}`);
  S.p.lines = fresh.lines; S.p.characters = fresh.characters; sortLines();
  S.sel = null; renderAll(); toast(msg);
}

/* ------------------------------------------------------------ Tabs */
const TABS = ['lines', 'chars', 'export'];
function switchTab(name) {
  const cur = $('#tabs button.on')?.dataset.tab || TABS.find(t => !$(`#tab-${t}`).hidden);
  if (cur === name) return;
  const oldEl = $(`#tab-${cur}`), newEl = $(`#tab-${name}`);
  const dir = TABS.indexOf(name) > TABS.indexOf(cur) ? 1 : -1;
  $$('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === name));
  moveTabInk();
  // Beide Seiten liegen in derselben Zelle (.right ist ein Grid) und gleiten gegeneinander. Bewusst ohne
  // View Transition: die würde das Video als Standbild mitnehmen, das dabei heller/dunkler wirkt.
  TABS.forEach(t => ($(`#tab-${t}`).hidden = t !== name && t !== cur));
  if (name === 'export') renderExportChecks();
  if (name === 'lines') $$('#lineList textarea').forEach(autoGrow);
  if (name === 'chars') loadVoices();
  const done = () => { if (oldEl && !newEl.hidden && $$('#tabs button.on')[0]?.dataset.tab === name) oldEl.hidden = true; };
  if (!oldEl || !motionOK()) { if (oldEl) oldEl.hidden = true; return; }
  oldEl.getAnimations().forEach(a => a.cancel());
  newEl.getAnimations().forEach(a => a.cancel());
  const opt = { duration: 300, easing: 'cubic-bezier(.3, .7, .2, 1)' };
  oldEl.style.pointerEvents = 'none';
  oldEl.animate([{ opacity: 1, transform: 'none' }, { opacity: 0, transform: `translateX(${-28 * dir}px)` }], { ...opt, fill: 'forwards' })
    .finished.catch(() => {}).then(() => { oldEl.style.pointerEvents = ''; oldEl.getAnimations().forEach(a => a.cancel()); done(); });
  newEl.animate([{ opacity: 0, transform: `translateX(${28 * dir}px)` }, { opacity: 1, transform: 'none' }], opt);
}
$$('#tabs button').forEach(b => b.onclick = () => switchTab(b.dataset.tab));
/** Farbige Linie unter dem aktiven Tab (gleitet beim Wechsel). */
function moveTabInk() {
  const nav = $('#tabs'), on = $('#tabs button.on');
  let ink = $('.tab-ink', nav);
  if (!ink) { ink = document.createElement('span'); ink.className = 'tab-ink'; nav.appendChild(ink); }
  if (!on || !on.offsetWidth) return;
  ink.style.width = on.offsetWidth + 'px';
  ink.style.transform = `translateX(${on.offsetLeft}px)`;
}
new ResizeObserver(() => moveTabInk()).observe($('#tabs'));

/* Umschalter (.seg, z. B. Original / Nur Stimmen / Hintergrund): Die Markierung gleitet zur neuen Wahl.
   Folgt automatisch jeder Änderung der Klasse „on“ und jeder Größenänderung (auch beim Einblenden). */
function segInk(seg, instant = false) {
  let ink = seg.querySelector(':scope > .seg-ink');
  if (!ink) {
    ink = document.createElement('span');
    ink.className = 'seg-ink';
    seg.prepend(ink);
    seg.classList.add('has-ink');
    instant = true;
  }
  const on = seg.querySelector(':scope > button.on');
  if (!on || !on.offsetWidth) { ink.style.opacity = on ? '' : '0'; return; }
  if (instant || !motionOK()) ink.style.transition = 'none';
  ink.style.opacity = '1';
  ink.style.width = `${on.offsetWidth}px`;
  ink.style.transform = `translateX(${on.offsetLeft}px)`;
  if (ink.style.transition) { void ink.offsetWidth; ink.style.transition = ''; }
}
{
  const segObs = new MutationObserver(ms => new Set(ms.map(m => m.target.closest('.seg'))).forEach(sg => sg && segInk(sg)));
  const segSize = new ResizeObserver(es => es.forEach(e => segInk(e.target, true)));
  $$('.seg').forEach(sg => {
    segObs.observe(sg, { subtree: true, attributes: true, attributeFilter: ['class'] });
    segSize.observe(sg);
  });
}

/* ------------------------------------------------------------ Export */
function fillExportForm() {
  const p = S.p.pack, x = S.p.export;
  $('#exTitle').value = S.p.name; $('#exAuthor').value = p.author || '';
  $('#exSubtitle').value = p.subtitle || ''; $('#exReadme').value = p.readme || '';
  $$('input[name=clipSource]').forEach(r => (r.checked = r.value === x.clip_source));
  $$('input[name=backingSource]').forEach(r => (r.checked = r.value === (x.backing_source || 'auto')));
  renderInstrumental();
  $('#exNormalize').value = x.normalize; $('#exImageMode').value = x.image_mode;
  $('#exLineFormat').value = x.line_format === 'txt' ? 'txt' : 'ini';
  $('#exKeepVoices').checked = !!x.keep_unused_voices;
  applyBackingVolume();
  $('#exHeight').value = String(x.video_height); $('#exFps').value = String(x.video_fps);
  $('#exQuality').value = x.video_quality; $('#exQualityVal').textContent = x.video_quality;
  $('#exIconImg').src = p.icon ? imgUrl(p.icon) : '';
  $('#exIconImg').style.visibility = p.icon ? 'visible' : 'hidden';
  $('#exResult').innerHTML = '';
  fillCredits();
}

/* ------------------------------------------------------------ Credits (pro Projekt, Standard: an) */
const CREDIT_DEFAULTS = { enabled: true, text: 'made_with', in_authors: true, in_readme: false, in_files: true };
function creditText(c) {
  const repo = lastState?.repo;
  if (c.text === 'made_with_by') return 'Made with Voicitool by Jan1653';
  if (c.text === 'made_with_link' && repo) return `Made with Voicitool (github.com/${repo})`;
  return 'Made with Voicitool';
}
function fillCredits() {
  const c = S.p.credits = Object.assign({}, CREDIT_DEFAULTS, S.p.credits || {});
  $('#crEnabled').checked = c.enabled;
  $('#crText').value = c.text;
  $('#crTextLink').hidden = !lastState?.repo;   // Link erst, wenn ein GitHub-Repo eingetragen ist
  $('#crAuthors').checked = c.in_authors; $('#crReadme').checked = c.in_readme; $('#crFiles').checked = c.in_files;
  $('#crPreview').textContent = c.enabled ? creditText(c) : 'Keine Credits für Voicitool';
  $('.credit-box').classList.toggle('off', !c.enabled);
  $$('#crOptions input, #crOptions select').forEach(el => (el.disabled = !c.enabled));
}
function readCredits() {
  const c = S.p.credits;
  c.enabled = $('#crEnabled').checked; c.text = $('#crText').value;
  c.in_authors = $('#crAuthors').checked; c.in_readme = $('#crReadme').checked; c.in_files = $('#crFiles').checked;
  fillCredits();
  scheduleSave();
}
$('.credit-box').addEventListener('change', e => { e.stopPropagation(); readCredits(); });
$('.credit-box').addEventListener('input', e => e.stopPropagation());
$('#crMore').onclick = () => {
  const open = $('#crOptions').hidden;
  $('#crOptions').hidden = !open;
  $('#crMore').setAttribute('aria-expanded', String(open));
  $('#crMore').classList.toggle('open', open);
};
function readExportForm() {
  const p = S.p.pack, x = S.p.export;
  p.author = $('#exAuthor').value; p.subtitle = $('#exSubtitle').value; p.readme = $('#exReadme').value;
  x.clip_source = $('input[name=clipSource]:checked')?.value || 'stimmen';
  x.backing_source = $('input[name=backingSource]:checked')?.value || 'auto';
  x.normalize = $('#exNormalize').value; x.image_mode = $('#exImageMode').value;
  x.line_format = $('#exLineFormat').value;
  x.keep_unused_voices = $('#exKeepVoices').checked;
  x.backing_volume = clamp(+$('#exBackVol').value / 100, 0, 2);
  applyBackingVolume();
  x.video_height = +$('#exHeight').value; x.video_fps = +$('#exFps').value; x.video_quality = +$('#exQuality').value;
  $('#exQualityVal').textContent = x.video_quality;
  scheduleSave();
}
$('#tab-export').addEventListener('input', e => {
  if (e.target.id === 'exTitle' || e.target.id === 'instFile') return; // gesondert behandelt
  if (e.target.closest('.form') && !e.target.closest('#exChecks')) readExportForm();
});
$('#exTitle').addEventListener('change', e => renameProject(e.target.value));
$('#exTitle').addEventListener('keydown', e => { if (e.key === 'Enter') e.target.blur(); });
$('#tab-export').addEventListener('change', e => {
  if (e.target.id === 'instFile') return;
  if (e.target.closest('.form')) { readExportForm(); renderExportChecks(); }
});

/* ---------- Pack-Icon zuschneiden ----------
   Das Spiel zeigt das Icon quadratisch. Hier wird der Ausschnitt gewählt: schieben, zoomen, fertig ist
   ein 432x432-Bild. Die Quelle ist das aktuelle Videobild oder ein Bild, das schon im Projekt liegt. */
const ICON_SIZE = 432;

function loadImage(src) {
  return new Promise((res, rej) => {
    const im = new Image();
    im.onload = () => res(im);
    im.onerror = () => rej(new Error(tf('Das Bild lässt sich nicht laden.')));
    im.src = src;
  });
}

/** Das aktuelle Videobild als Bild, ohne Umweg über den Server. */
function frameFromVideo() {
  const c = document.createElement('canvas');
  c.width = video.videoWidth || 1280;
  c.height = video.videoHeight || 720;
  c.getContext('2d').drawImage(video, 0, 0, c.width, c.height);
  return loadImage(c.toDataURL('image/jpeg', 0.95));
}

/**
 * Zuschneide-Fenster. img: Bild, das zugeschnitten wird.
 * -> Blob (JPEG, ICON_SIZE x ICON_SIZE) oder null bei Abbruch.
 */
async function cropDialog(img, title) {
  const view = 320;                                    // Anzeigegröße im Fenster
  const cover = Math.max(view / img.width, view / img.height);   // füllt das Quadrat
  const contain = Math.min(view / img.width, view / img.height); // ganzes Bild, mit Rändern
  let scale = cover, ox = (view - img.width * cover) / 2, oy = (view - img.height * cover) / 2;

  const p = dialog({
    title: title || tf('Ausschnitt fürs Icon'),
    icon: 'image',
    html: `<span class="crop-wrap">
        <canvas class="crop-canvas" width="${view * 2}" height="${view * 2}"></canvas>
        <span class="crop-row"><button type="button" class="btn small crop-fit">${esc(tf('Ganzes Bild'))}</button>
          <input class="crop-zoom" type="range" min="0" max="1000" value="0" aria-label="${esc(tf('Zoom'))}"></span>
        <span class="crop-hint small muted">${esc(tf('Bild schieben, mit dem Regler näher heran. Daraus wird ein Bild mit 432 x 432 Punkten.'))}</span>
      </span>`,
    buttons: [{ label: tf('Abbrechen'), value: null }, { label: tf('Übernehmen'), value: true, main: true }],
  });
  const wrap = $('.crop-wrap');
  const cv = $('.crop-canvas', wrap);
  const zoom = $('.crop-zoom', wrap);
  const ctx = cv.getContext('2d');

  const clamp = () => {
    const w = img.width * scale, h = img.height * scale;
    ox = w <= view ? (view - w) / 2 : Math.min(0, Math.max(view - w, ox));
    oy = h <= view ? (view - h) / 2 : Math.min(0, Math.max(view - h, oy));
  };
  const draw = () => {
    clamp();
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, cv.width, cv.height);
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(img, ox * 2, oy * 2, img.width * scale * 2, img.height * scale * 2);
  };
  const setScale = (s, px = view / 2, py = view / 2) => {
    const before = scale;
    scale = Math.min(cover * 4, Math.max(contain, s));
    // um den Punkt zoomen, der in der Mitte war
    ox = px - (px - ox) * (scale / before);
    oy = py - (py - oy) * (scale / before);
    zoom.value = String(Math.round(1000 * (scale - contain) / Math.max(1e-6, cover * 4 - contain)));
    draw();
  };
  setScale(cover);

  zoom.oninput = () => setScale(contain + (cover * 4 - contain) * (Number(zoom.value) / 1000));
  $('.crop-fit', wrap).onclick = () => setScale(contain);
  cv.onwheel = (e) => { e.preventDefault(); setScale(scale * (e.deltaY < 0 ? 1.12 : 1 / 1.12)); };
  let drag = null;
  cv.onpointerdown = (e) => { drag = { x: e.clientX, y: e.clientY, ox, oy }; cv.setPointerCapture(e.pointerId); };
  cv.onpointermove = (e) => {
    if (!drag) return;
    const r = cv.getBoundingClientRect();
    const k = view / r.width;   // Anzeige kann kleiner sein als 320 Punkte
    ox = drag.ox + (e.clientX - drag.x) * k;
    oy = drag.oy + (e.clientY - drag.y) * k;
    draw();
  };
  cv.onpointerup = cv.onpointercancel = () => { drag = null; };

  if (!await p) return null;
  const out = document.createElement('canvas');
  out.width = out.height = ICON_SIZE;
  const o = out.getContext('2d');
  o.fillStyle = '#000';
  o.fillRect(0, 0, ICON_SIZE, ICON_SIZE);
  o.imageSmoothingQuality = 'high';
  const k = ICON_SIZE / view;
  o.drawImage(img, ox * k, oy * k, img.width * scale * k, img.height * scale * k);
  return new Promise(res => out.toBlob(res, 'image/jpeg', 0.92));
}

/** Zugeschnittenes Bild hochladen. -> Dateiname im Projekt */
async function uploadImage(blob, target) {
  const fd = new FormData();
  fd.append('file', blob, 'bild.jpg');
  fd.append('target', target);
  const r = await fetch(`/api/projects/${encodeURIComponent(S.pid)}/bild`, { method: 'POST', body: fd });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || tf('Das Bild ließ sich nicht speichern.'));
  return (await r.json()).image;
}

$('#btnIconFrame').onclick = async () => {
  try {
    const r = await api('POST', `/api/projects/${encodeURIComponent(S.pid)}/frame`, { time: video.currentTime, target: 'icon' });
    setIcon(r.image);
  } catch (e) { toast(e.message, true); }
};

/** Ausschnitt wählen: aus dem aktuellen Videobild oder aus dem Icon, das schon da ist. */
$('#btnIconCrop').onclick = async () => {
  try {
    const src = S.p.pack.icon ? await loadImage(imgUrl(S.p.pack.icon) + '?v=' + Date.now()) : await frameFromVideo();
    const blob = await cropDialog(src);
    if (!blob) return;
    setIcon(await uploadImage(blob, 'icon'));
    toast(tf('Icon gespeichert ({} x {}).', ICON_SIZE, ICON_SIZE));
  } catch (e) { toast(e.message, true); }
};

function setIcon(name) {
  S.p.pack.icon = name;
  $('#exIconImg').src = imgUrl(name) + '?v=' + Date.now();
  $('#exIconImg').style.visibility = 'visible';
  scheduleSave();
}

function renderInstrumental() {
  const i = S.p.instrumental;
  $('#clipDiffRow').hidden = !(i && i.stimmen_moeglich);
  $('#backingOwnRadio').disabled = !i;
  $('#btnInstRemove').hidden = !i;
  $('#btnInstManual').hidden = !i;
  $('#btnInstManual').classList.toggle('primary', !!i && ['passt nicht', 'mäßig'].includes(i.bewertung));
  refreshBackingAudio();
  const rep = $('#instReport');
  if (!i) {
    rep.className = 'small muted';
    rep.textContent = 'Hast du die offizielle Instrumental-Version? Dann wird sie automatisch auf das Video ausgerichtet und als Hintergrund genutzt.';
    return;
  }
  const cls = i.bewertung === 'passt nicht' ? 'warn' : (i.bewertung === 'mäßig' ? 'warn' : 'ok');
  const drift = Math.abs(i.drift) > 0.05 ? `, Auseinanderlaufen ${(i.drift * 1000).toFixed(0)} ms ausgeglichen` : '';
  rep.className = 'small';
  rep.innerHTML = `<b>${esc(i.datei)}</b>: <span class="${cls}">${esc(i.bewertung)}</span>: ${esc(i.hinweis)}<br>
    <span class="muted">Versatz ${i.versatz >= 0 ? '+' : ''}${i.versatz.toFixed(2)} s korrigiert${drift},
    Lautstärke ×${i.lautstaerke.toFixed(2)}, Übereinstimmung ${Math.round(i.guete * 100)} %,
    Rest in stillen Stellen ${i.restpegel_db} dB</span>`;
}

$('#instFile').onchange = async e => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  const rep = $('#instReport');
  rep.className = 'small muted';
  try {
    await flushSave();
    const job = await new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const fd = new FormData(); fd.append('file', file);
      xhr.upload.onprogress = ev => { rep.textContent = `Lade ${file.name} … ${Math.round(ev.loaded / ev.total * 100)} %`; };
      xhr.onload = () => (xhr.status === 200 ? resolve(JSON.parse(xhr.responseText).job) : reject(new Error(xhr.responseText)));
      xhr.onerror = () => reject(new Error('Hochladen fehlgeschlagen'));
      xhr.open('POST', `/api/projects/${encodeURIComponent(S.pid)}/instrumental`);
      xhr.setRequestHeader('X-Voicitool', '1');
      xhr.send(fd);
    });
    rep.textContent = 'Instrumental wird auf das Video ausgerichtet …';
    const info = await waitJob(job);
    const fresh = await api('GET', `/api/projects/${encodeURIComponent(S.pid)}`);
    S.p.instrumental = fresh.instrumental; S.p.export = fresh.export;
    fillExportForm();
    toast(`Instrumental: ${info.bewertung}. ${info.hinweis}`, info.bewertung === 'passt nicht', 7000);
  } catch (err) {
    renderInstrumental();
    toast('Instrumental konnte nicht verwendet werden: ' + err.message, true, 7000);
  }
};

$('#btnInstUrl').onclick = async () => {
  const url = $('#instUrl').value.trim();
  if (!/^https?:\/\//i.test(url)) return toast('Bitte eine Adresse einfügen, die mit http:// oder https:// beginnt.', true);
  const rep = $('#instReport');
  rep.className = 'small muted';
  rep.textContent = 'Instrumental wird geladen …';
  $('#btnInstUrl').disabled = true;
  try {
    await flushSave();
    const { job } = await api('POST', `/api/projects/${encodeURIComponent(S.pid)}/instrumental_url`, { url });
    const info = await waitJob(job);
    const fresh = await api('GET', `/api/projects/${encodeURIComponent(S.pid)}`);
    S.p.instrumental = fresh.instrumental; S.p.export = fresh.export;
    $('#instUrl').value = '';
    fillExportForm();
    toast(`Instrumental: ${info.bewertung}. ${info.hinweis}`, info.bewertung === 'passt nicht', 7000);
  } catch (err) {
    renderInstrumental();
    toast('Instrumental konnte nicht verwendet werden: ' + err.message, true, 7000);
  } finally {
    $('#btnInstUrl').disabled = false;
  }
};

$$('input[name=backingSource]').forEach(r => r.addEventListener('change', () => setTimeout(refreshBackingAudio, 0)));
$('#btnInstManual').onclick = () => openInstAligner();

/* Instrumental von Hand ausrichten: oben der Hintergrund, den die KI aus dem Video getrennt hat, unten das eigene
   Instrumental auf der Zeitachse des Videos. Schieben mit der Maus oder den Knöpfen, Mausrad zoomt.
   Anhören einzeln oder beide zusammen: solange es nicht passt, hört man bei „Beide“ ein Echo. */
async function openInstAligner() {
  let W;
  try { W = await api('GET', `/api/projects/${encodeURIComponent(S.pid)}/instrumental/waves`); }
  catch (e) { toast(e.message, true); return; }
  video.pause();
  const fps = W.fps, tempo = 1 / (1 - (W.slope || 0)), total = W.ref.length / fps;
  let offset = W.offset || 0, mode = 'both', playing = null;
  const view = { len: Math.min(30, Math.max(5, total)), start: 0 };
  view.start = clamp(video.currentTime - view.len / 3, 0, Math.max(0, total - view.len));

  const ov = document.createElement('div');
  ov.className = 'dlg-overlay';
  ov.innerHTML = `<div class="dlg wide inst-align" role="dialog" aria-modal="true">
    <div class="dlg-head"><div class="dlg-icon" aria-hidden="true">${ic('music')}</div>
      <div class="dlg-titles"><h2>Instrumental von Hand ausrichten</h2>
      <p class="dlg-text">Oben der Hintergrund, den die KI aus dem Video getrennt hat, unten dein Instrumental. Schieb dein Instrumental mit der Maus, bis die Ausschläge übereinanderliegen. Mausrad zoomt.</p></div></div>
    <div class="ia-legend"><span class="ia-ref">KI-Hintergrund</span><span class="ia-own">Dein Instrumental</span></div>
    <canvas class="ia-canvas"></canvas>
    <input type="range" class="ia-pos" min="0" step="0.1" aria-label="Stelle im Video">
    <div class="ia-row">
      <label>Versatz <input type="number" class="ia-off" step="0.01"> s</label>
      <div class="row-btns">${[-1, -0.1, -0.01, 0.01, 0.1, 1].map(d => `<button class="btn small" data-d="${d}">${d > 0 ? '+' : '−'}${String(Math.abs(d)).replace('.', ',')} s</button>`).join('')}</div>
    </div>
    <div class="ia-row">
      <div class="seg ia-mode"><button data-m="ref">KI-Hintergrund</button><button data-m="own">Dein Instrumental</button><button data-m="both" class="on">Beide</button></div>
      <button class="btn small ia-play">${ic('play')}<span>Anhören</span></button>
    </div>
    <div class="dlg-actions"><button class="btn ia-cancel">Abbrechen</button><button class="btn primary ia-ok">Übernehmen</button></div>
  </div>`;
  document.body.appendChild(ov);
  const box = $('.inst-align', ov), cvA = $('.ia-canvas', ov), pos = $('.ia-pos', ov), off = $('.ia-off', ov);
  pos.max = Math.max(0, total - view.len).toFixed(1);
  // Anhören über Web Audio: beide Spuren liegen dekodiert im Speicher und starten auf die Probe genau zusammen.
  // Mit <audio> sprang das MP3 bei jedem Neustart nur ungefähr an die Stelle, danach lief es hörbar versetzt.
  const actx = new AudioContext();
  const gRef = actx.createGain(), gOwn = actx.createGain();
  gRef.connect(actx.destination); gOwn.connect(actx.destination);
  gOwn.gain.value = W.gain || 1;   // so laut, wie es danach als Hintergrund klingt
  const loadBuf = which => fetch(`/api/projects/${encodeURIComponent(S.pid)}/instrumental/preview/${which}`)
    .then(r => { if (!r.ok) throw new Error(tf('Ton konnte nicht geladen werden.')); return r.arrayBuffer(); })
    .then(b => actx.decodeAudioData(b));
  const bufs = Promise.all([loadBuf('ref'), loadBuf('own')]);
  let closed = false;
  bufs.catch(e => { if (!closed) toast(e.message, true); });

  // jede Spur nach ihrer eigenen Dynamik zeichnen (leiseste 25 % unten, lauteste Stelle oben): Schläge und Pausen
  // treten hervor, auch wenn die Musik durchgehend laut ist
  const range = a => { const s = a.filter(v => v > -59).sort((x, y) => x - y); return s.length ? [s[Math.floor(s.length * 0.25)], s[s.length - 1]] : [-60, 0]; };
  const rRef = range(W.ref), rOwn = range(W.own);
  const lvl = (v, r) => clamp((v - r[0]) / Math.max(1, r[1] - r[0]), 0, 1);
  const ownAt = t => { const i = Math.floor((t + offset) * tempo * fps); return i >= 0 && i < W.own.length ? lvl(W.own[i], rOwn) : 0; };
  const refAt = t => { const i = Math.floor(t * fps); return i >= 0 && i < W.ref.length ? lvl(W.ref[i], rRef) : 0; };
  function draw() {
    const dpr = window.devicePixelRatio || 1, w = cvA.clientWidth, h = cvA.clientHeight;
    if (cvA.width !== Math.round(w * dpr)) { cvA.width = Math.round(w * dpr); cvA.height = Math.round(h * dpr); }
    const c = cvA.getContext('2d');
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, w, h);
    const css = getComputedStyle(document.documentElement);
    const col = { ref: css.getPropertyValue('--muted').trim() || '#8d94a3', own: css.getPropertyValue('--accent').trim() || '#a855f7',
                  grid: css.getPropertyValue('--line').trim() || '#2c313c', text: css.getPropertyValue('--muted').trim() || '#8d94a3' };
    const half = h / 2;
    c.fillStyle = col.grid;
    c.fillRect(0, half, w, 1);
    c.font = '11px Segoe UI'; c.textBaseline = 'top';
    const step = view.len > 60 ? 10 : view.len > 20 ? 5 : 1;
    for (let s = Math.ceil(view.start / step) * step; s < view.start + view.len; s += step) {
      const x = (s - view.start) / view.len * w;
      c.fillStyle = col.grid; c.fillRect(x, 0, 1, h);
      c.fillStyle = col.text; c.fillText(fmt(s, false), x + 3, 2);
    }
    for (const [lane, fn, color] of [[0, refAt, col.ref], [1, ownAt, col.own]]) {
      c.fillStyle = color;
      const mid = lane === 0 ? half / 2 : half + half / 2;
      for (let x = 0; x < w; x++) {
        const t = view.start + x / w * view.len;
        const v = fn(t);                             // 0..1
        const bar = Math.max(1, v * (half / 2 - 6));
        c.fillRect(x, mid - bar, 1, bar * 2);
      }
    }
    if (playing) {
      const x = (playing.now() - view.start) / view.len * w;
      c.fillStyle = '#fff'; c.fillRect(x, 0, 2, h);
    }
  }
  const sync = () => { off.value = offset.toFixed(3); pos.value = view.start.toFixed(1); draw(); };
  const moved = () => { sync(); if (playing) startPlay(playing.now()); };   // neuer Versatz: genau an derselben Stelle weiter
  sync();

  let drag = null;
  cvA.addEventListener('pointerdown', e => { drag = { x: e.clientX, off: offset }; cvA.setPointerCapture(e.pointerId); });
  cvA.addEventListener('pointermove', e => {
    cvA.style.cursor = drag ? 'grabbing' : 'grab';
    if (!drag || !cvA.clientWidth) return;
    offset = drag.off - (e.clientX - drag.x) / cvA.clientWidth * view.len;   // nach rechts ziehen = Instrumental später
    off.value = offset.toFixed(3); draw();
  });
  cvA.addEventListener('pointerup', () => { if (drag) { const changed = drag.off !== offset; drag = null; changed ? moved() : sync(); } });
  cvA.addEventListener('wheel', e => {
    e.preventDefault();
    if (!cvA.clientWidth) return;   // Fenster geht gerade erst auf
    const t = view.start + e.offsetX / cvA.clientWidth * view.len;
    view.len = clamp(view.len * (e.deltaY > 0 ? 1.25 : 0.8), 2, Math.max(2, total));
    view.start = clamp(t - e.offsetX / cvA.clientWidth * view.len, 0, Math.max(0, total - view.len));
    pos.max = Math.max(0, total - view.len).toFixed(1);
    sync();
  }, { passive: false });
  pos.oninput = () => { view.start = +pos.value; draw(); };
  off.onchange = () => { const v = parseFloat(String(off.value).replace(',', '.')); if (Number.isFinite(v)) offset = v; moved(); };
  $$('[data-d]', box).forEach(b => b.onclick = () => { offset = Math.round((offset + +b.dataset.d) * 1000) / 1000; moved(); });
  $$('.ia-mode button', box).forEach(b => b.onclick = () => {
    mode = b.dataset.m;
    $$('.ia-mode button', box).forEach(x => x.classList.toggle('on', x === b));
    if (playing) startPlay(playing.now());
  });

  let srcs = [], raf = 0, tok = 0;
  const stopSrcs = () => { srcs.forEach(s => { try { s.stop(); } catch { /* schon zu Ende */ } }); srcs = []; };
  function stopPlay() {
    tok++; stopSrcs(); cancelAnimationFrame(raf);
    playing = null; $('.ia-play', box).innerHTML = `${ic('play')}<span>${esc(tf('Anhören'))}</span>`; draw();
  }
  async function startPlay(t0) {
    const my = ++tok;
    if (!srcs.length && !playing) $('.ia-play', box).innerHTML = `${ic('hourglass')}<span>${esc(tf('Lädt …'))}</span>`;
    let bRef, bOwn;
    try { [bRef, bOwn] = await bufs; if (actx.state === 'suspended') await actx.resume(); } catch { if (my === tok) stopPlay(); return; }
    if (my !== tok) return;   // inzwischen neu gestartet oder gestoppt
    stopSrcs();
    const when = actx.currentTime + 0.05;
    const add = (buf, gain, at, from, rate) => {
      const s = actx.createBufferSource();
      s.buffer = buf; s.playbackRate.value = rate; s.connect(gain); s.start(at, from); srcs.push(s);
    };
    if (mode !== 'own' && t0 < bRef.duration) add(bRef, gRef, when, t0, 1);
    if (mode !== 'ref') {
      const ot = (t0 + offset) * tempo;   // Stelle im eigenen Instrumental
      if (ot < 0) add(bOwn, gOwn, when - ot / tempo, 0, tempo);
      else if (ot < bOwn.duration) add(bOwn, gOwn, when, ot, tempo);
    }
    playing = { now: () => t0 + Math.max(0, actx.currentTime - when) };
    cancelAnimationFrame(raf);
    $('.ia-play', box).innerHTML = `${ic('stop')}<span>${esc(tf('Stopp'))}</span>`;
    const loop = () => {
      if (!playing) return;
      const t = playing.now();
      if (t > view.start + view.len) { view.start = Math.min(t, Math.max(0, total - view.len)); pos.value = view.start.toFixed(1); }
      if (t >= total) return stopPlay();
      draw();
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
  }
  $('.ia-play', box).onclick = () => (playing ? stopPlay() : startPlay(view.start));

  const close = () => { closed = true; stopPlay(); actx.close().catch(() => {}); ov.remove(); document.removeEventListener('keydown', onKey, true); };
  const onKey = e => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
  document.addEventListener('keydown', onKey, true);
  $('.ia-cancel', box).onclick = close;
  $('.ia-ok', box).onclick = async () => {
    const btn = $('.ia-ok', box);
    btn.disabled = true;
    try {
      await flushSave();
      const info = await api('POST', `/api/projects/${encodeURIComponent(S.pid)}/instrumental/manual`, { offset });
      S.p.instrumental = info;
      S.p.export.backing_source = 'eigene';
      if (S.p.export.clip_source === 'differenz') S.p.export.clip_source = 'stimmen';
      close();
      fillExportForm();
      toast(tf('Instrumental ausgerichtet (Versatz {} s).', offset.toFixed(2).replace('.', ',')));
    } catch (e) { btn.disabled = false; toast(e.message, true); }
  };
  new ResizeObserver(draw).observe(cvA);
}

$('#btnInstRemove').onclick = async () => {
  try {
    await api('DELETE', `/api/projects/${encodeURIComponent(S.pid)}/instrumental`);
    const fresh = await api('GET', `/api/projects/${encodeURIComponent(S.pid)}`);
    S.p.instrumental = null; S.p.export = fresh.export;
    fillExportForm();
    toast('Instrumental entfernt. Es wird wieder die KI-Trennung genutzt.');
  } catch (e) { toast(e.message, true); }
};

function renderExportChecks() {
  if (!S.p) return;
  const out = [];
  const lines = S.p.lines;
  const long = lines.filter(l => l.end - l.start > 59.5);
  const noText = lines.filter(l => !l.text.trim());
  const tiny = lines.filter(l => l.end - l.start < 0.3);
  const unnamed = S.p.characters.filter(c => UNNAMED_RX.test(c.name) && lines.some(l => l.chars.includes(c.id)));
  const link = (arr, label) => `<a data-goto="${arr[0].id}">${label}</a>`;
  const row = (cls, icon, html) => `<div class="${cls} chk">${ic(icon)}<span>${html}</span></div>`;
  if (!lines.length) out.push(row('warn', 'warning', 'Keine Zeilen vorhanden.'));
  const rt = S.p.reftext, rep = rt?.report;
  if (rt && rep && !rep.used) {
    const share = rep.share == null ? '' : ' ' + tf('Nur {} % der erkannten Wörter standen im Text.', Math.round(rep.share * 100));
    out.push(row('warn', 'warning', esc(tf('Der vorgegebene Text wurde nicht verwendet: {}',
      tf(rep.reason || 'Es war zu wenig zuzuordnen.')) + share) + ' ' + esc(tf('Prüfe, ob er zu diesem Video gehört, und starte die Verarbeitung neu.'))));
  } else if (rt && rep && rep.used) {
    out.push(row('ok', 'check-circle', esc(tf('Vorgegebener Text übernommen: {} Wörter berichtigt, {} ergänzt.', rep.replaced || 0, rep.inserted || 0))));
    if (rep.named) out.push(row('ok', 'user', esc(tf('Namen aus dem Text übernommen: {} Figuren.', rep.named))));
  }
  if (unnamed.length) out.push(row('warn', 'warning', `Noch nicht benannt: ${unnamed.map(c => esc(c.name)).join(', ')} (Tab „Charaktere")`));
  if (long.length) out.push(row('warn', 'warning', `${long.length} Zeile(n) länger als 60 s. Das Spiel lädt sie nicht, sie werden gekürzt. ${link(long, 'Erste zeigen')}`));
  if (noText.length) out.push(row('warn', 'warning', `${noText.length} Zeile(n) ohne Text. ${link(noText, 'Erste zeigen')}`));
  const ov = sameCharOverlaps();
  if (ov.length) out.push(row('warn', 'warning', `${ov.length} Zeile(n) überschneiden sich mit einer Zeile desselben Sprechers. ${link(ov, 'Erste zeigen')}`));
  if (tiny.length) out.push(row('warn', 'warning', `${tiny.length} sehr kurze Zeile(n) unter 0,3 s. ${link(tiny, 'Erste zeigen')}`));
  const sounds = lines.filter(l => l.text.trim() === '(…)');
  if (sounds.length) out.push(row('muted', 'sound', `${sounds.length} Laut-Zeile(n) „(…)“ ohne Worte (Keuchen, Kichern, Schrei). Text ergänzen oder so lassen. ${link(sounds, 'Erste zeigen')}`));
  const reps = lines.filter(l => l.repeat_of && lineById(l.repeat_of));
  if (reps.length) out.push(row('ok', 'repeat', `${reps.length} Wiederholung(en) teilen sich eine Aufnahme. Im Pack wird der Clip an allen Stellen abgespielt.`));
  const cuts = S.p.cuts || [];
  if (cuts.length) {
    const sec = cuts.reduce((a, c) => a + c[1] - c[0], 0);
    const gone = lines.filter(l => cuts.some(c => l.start >= c[0] && l.end <= c[1]));
    out.push(row('muted', 'scissors', tf('Video geschnitten: {} Stelle(n), zusammen {} s, fehlen im Pack. Alles danach rückt nach vorne.', cuts.length, sec.toFixed(1).replace('.', ','))
      + (gone.length ? ' ' + tf('{} Zeile(n) liegen darin und fallen weg.', gone.length) + ` ${link(gone, 'Erste zeigen')}` : '')));
  }
  if (!out.length) out.push(row('ok', 'check-circle', `${lines.length} Zeilen, ${S.p.characters.length} Charaktere, bereit zum Export.`));
  $('#exChecks').innerHTML = out.join('');
}
$('#exChecks').addEventListener('click', e => {
  const a = e.target.closest('[data-goto]'); if (!a) return;
  $('#charFilter').value = ''; $('#textFilter').value = '';
  switchTab('lines'); renderLines(); selectLine(a.dataset.goto, { seekTo: true });
});

async function doExport(install, overwrite = false) {
  readExportForm(); await flushSave();
  if (!overwrite && !(await preflight('export', { pid: S.pid, install }))) return;
  $('#btnExport').disabled = $('#btnExportInstall').disabled = true;
  $('#exResult').innerHTML = '<div class="muted">Export läuft … (Fortschritt oben rechts)</div>';
  try {
    const { job } = await api('POST', `/api/projects/${encodeURIComponent(S.pid)}/export`, { install, overwrite });
    $('#exResult').innerHTML = `<div class="muted">Export läuft … (Fortschritt oben rechts)
      <button class="btn small danger" id="exCancel">Abbrechen</button></div>`;
    $('#exCancel').onclick = () => cancelJob(job, 'Export');
    const r = await waitJob(job);
    $('#exResult').innerHTML = `<div class="paths">
      <div class="chk ok">${ic('check-circle')}<b>${r.clips} Clips exportiert.</b></div>
      <div>Ordner: <code>${esc(r.folder)}</code> <button class="btn small" data-open="${esc(r.folder)}">Öffnen</button></div>
      <div>ZIP: <code>${esc(r.zip)}</code> <button class="btn small" data-open="${esc(r.zip)}">Im Ordner zeigen</button>
        <button class="btn small" data-link="${esc(r.zip)}" title="${esc(tf('ZIP hochladen und einen Link zum Weiterschicken bekommen'))}">${ic('share')}<span>${esc(tf('Link erstellen (72 h)'))}</span></button></div>
      ${r.installed ? `<div class="chk">${ic('gamepad')}<span>Im Spiel installiert: <code>${esc(r.installed)}</code></span></div>` : ''}
      ${r.warnings.map(w => `<div class="warn chk">${ic('warning')}<span>${esc(w)}</span></div>`).join('')}
    </div>`;
  } catch (e) {
    if (e.message.startsWith('EXISTS:')) {
      $('#exResult').innerHTML = '';
      if (await dialog({ title: 'Pack ersetzen?', html: `${esc(tf('Im Spiel gibt es schon ein Pack mit diesem Namen:'))}<br><code>${esc(e.message.slice(7))}</code>`,
                        tone: 'warn', icon: 'gamepad', ok: 'Ersetzen' })) {
        $('#btnExport').disabled = $('#btnExportInstall').disabled = false;
        return doExport(install, true);
      }
    } else if (e.message === 'Abgebrochen') {
      $('#exResult').innerHTML = '<div class="muted">Export abgebrochen.</div>';
    } else {
      $('#exResult').innerHTML = `<div class="err">${esc(e.message)}</div>`;
    }
  } finally {
    $('#btnExport').disabled = $('#btnExportInstall').disabled = false;
  }
}
$('#btnExport').onclick = () => doExport(false);
$('#btnExportInstall').onclick = () => doExport(true);
$('#exResult').addEventListener('click', e => {
  const b = e.target.closest('[data-open]'); if (b) api('POST', '/api/open', { path: b.dataset.open }).catch(err => toast(err.message, true));
  const l = e.target.closest('[data-link]'); if (l) shareLink(l.dataset.link);
});

/* ------------------------------------------------------------ Download-Link
   Fertige ZIP zu Litterbox (catbox.moe) hochladen und einen Link zum Weiterschicken bekommen.
   Vorher ein Hinweis: die Datei liegt dann bei einem fremden Dienst, jeder mit dem Link kann sie laden. */
const LINK_HINT = 'vt.linkHintSeen';
async function shareLink(zip) {
  try {
    const { link } = await api('GET', `/api/share-link?path=${encodeURIComponent(zip)}`);
    if (link) return showLink(link, true);   // gibt es schon und gilt noch: nicht doppelt hochladen
  } catch (e) { return toast(e.message, true); }
  let seen = false;
  try { seen = localStorage.getItem(LINK_HINT) === '1'; } catch { /* egal */ }
  if (!seen) {
    const r = await dialog({
      title: 'Download-Link erstellen', icon: 'share', wide: true,
      text: 'Die ZIP wird hochgeladen, du bekommst einen Link zum Weiterschicken. Deine Freunde brauchen kein Konto.',
      list: [
        { level: 'info', text: tf('Dienst: Litterbox (catbox.moe), kostenlos und ohne Anmeldung. Der Link gilt {} Stunden, danach wird die Datei gelöscht.', 72) },
        { level: 'warn', text: 'Jeder mit dem Link kann die Datei laden. Teile nur, was du teilen darfst: Ausschnitte aus Serien und Filmen gehören meist anderen.' },
        { level: 'info', text: 'Die Datei liegt dann bei einem fremden Dienst. Voicitool hat darauf keinen Einfluss, er kann sie auch früher löschen. Höchstens 1 GB.' },
      ],
      check: { label: 'Nicht mehr anzeigen' },
      buttons: [{ label: 'Abbrechen', value: null }, { label: 'Hochladen', value: true, kind: 'primary', main: true }],
    });
    if (!r?.value) return;
    if (r.checked) { try { localStorage.setItem(LINK_HINT, '1'); } catch { /* egal */ } }
  }
  try {
    const { job } = await api('POST', '/api/share-link', { path: zip });
    toast('Wird hochgeladen … (Fortschritt oben rechts)');
    refreshState();
    showLink(await waitJob(job), false);
  } catch (e) {
    if (e.message !== 'Abgebrochen') toast(e.message, true);
  }
}
async function showLink(link, old) {
  let copied = false;
  try { await navigator.clipboard.writeText(link.url); copied = true; } catch { /* kein Fokus: Knopf Kopieren */ }
  const until = new Date(link.expires * 1000).toLocaleString(window.VT_I18N?.lang || undefined,
    { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  const v = await dialog({
    title: old ? 'Diesen Link gibt es schon' : 'Link ist fertig', icon: 'check-circle', wide: true,
    input: link.url, readonly: true,
    text: copied ? tf('Schon in die Zwischenablage kopiert. Gilt bis {}.', until) : tf('Gilt bis {}.', until),
    buttons: [{ label: 'Im Browser öffnen', value: 'open', side: 'left' }, { label: 'Kopieren', value: 'copy' },
              { label: 'Fertig', value: 'done', kind: 'primary', main: true }],
  });
  if (v === 'copy') {
    try { await navigator.clipboard.writeText(link.url); toast('Link kopiert.'); } catch (e) { toast(e.message, true); }
  }
  if (v === 'open') api('POST', '/api/open-link', { url: link.url }).catch(e => toast(e.message, true));
}

/* ------------------------------------------------------------ Kontextmenü */
const menuEl = document.createElement('div');
menuEl.className = 'ctx'; menuEl.hidden = true;
document.body.appendChild(menuEl);
let menuActions = [];

function openMenu(items, x, y) {
  menuActions = [];
  const render = arr => arr.filter(Boolean).map(it => {
    if (it.sep) return '<div class="ctx-sep"></div>';
    const i = menuActions.push(it.action || null) - 1;
    const sub = it.sub && it.sub.length ? `<div class="ctx-sub">${render(it.sub)}</div>` : '';
    return `<div class="ctx-item${it.disabled ? ' disabled' : ''}${it.danger ? ' danger' : ''}${sub ? ' has-sub' : ''}" data-i="${i}">
      <span class="ctx-ic">${it.checked ? ic('check') : it.icon ? ic(it.icon) : ''}</span>${it.color ? `<span class="dot" style="background:${it.color}"></span>` : ''}<span class="lbl">${esc(it.label)}</span>
      ${it.key ? `<span class="key">${esc(it.key)}</span>` : ''}${sub ? `<span class="key">${ic('chevron-right')}</span>` : ''}${sub}</div>`;
  }).join('');
  menuEl.innerHTML = render(items);
  menuEl.hidden = false;
  const r = menuEl.getBoundingClientRect();
  menuEl.style.left = Math.min(x, innerWidth - r.width - 8) + 'px';
  menuEl.style.top = Math.min(y, innerHeight - r.height - 8) + 'px';
  menuEl.classList.toggle('flip', x + r.width * 2 > innerWidth);
}
function closeMenu() { menuEl.hidden = true; }
menuEl.addEventListener('mousedown', e => e.stopPropagation());
menuEl.addEventListener('click', e => {
  const it = e.target.closest('.ctx-item');
  if (!it || it.classList.contains('disabled') || it.classList.contains('has-sub')) return; // Untermenü öffnet per Hover
  const fn = menuActions[+it.dataset.i];
  closeMenu();
  if (fn) fn();
});
document.addEventListener('mousedown', closeMenu);
window.addEventListener('blur', closeMenu);
window.addEventListener('resize', closeMenu);

const charItems = (fn, current = []) => S.p.characters.map((c, i) => ({
  label: c.name, checked: current.includes(c.id), key: i < 9 ? String(i + 1) : '', color: c.color,
  disabled: current.includes(c.id), action: () => fn(c.id),
}));

/** Menü für eine Zeile. laneChar = Sprecher-Spur, t = Zeitpunkt (fürs Teilen). */
function lineMenu(l, laneChar, t) {
  const inside = t > l.start + 0.1 && t < l.end - 0.1;
  const others = S.p.characters.filter(c => !l.chars.includes(c.id));
  return [
    { label: 'Abspielen', key: 'Enter', action: () => playRange(l.start, l.end) },
    { sep: true },
    { label: 'Hier teilen', key: 'S', disabled: !inside, action: () => splitLine(l, t) },
    { label: `Mit nächster Zeile von ${charById(l.chars[0])?.name || '…'} zusammenführen`, key: 'M', action: () => mergeLine(l) },
    { label: 'An Stimme anpassen', action: () => fitToVoice(l) },
    l.repeat_of
      ? { label: 'Wiederholung lösen (eigene Aufnahme)', icon: 'repeat', action: () => { snapshot(); delete l.repeat_of; afterChange(null); } }
      : repeatLinkMenu(l),
    S.p.lines.some(o => o.repeat_of === l.id)
      ? { label: 'Alle Wiederholungen dieser Aufnahme lösen', action: () => { snapshot(); S.p.lines.forEach(o => { if (o.repeat_of === l.id) delete o.repeat_of; }); afterChange(null); } }
      : null,
    { label: 'Text neu erkennen', action: () => retranscribe(l) },
    { sep: true },
    { label: 'Kopieren', key: 'Strg+C', action: () => copyLine(l) },
    { label: 'Ausschneiden', key: 'Strg+X', action: () => copyLine(l, true) },
    { label: 'Einfügen', key: 'Strg+V', disabled: !S.clip, action: () => pasteAt(video.currentTime) },
    { sep: true },
    {
      label: 'Sprecher ändern', sub: charItems(cid => {
        if (commit(x => { x.chars = x.chars.map(c => (c === laneChar ? cid : c)); }, l)) afterChange(l.id, true);
      }, l.chars),
    },
    others.length ? {
      label: 'Weiterer Sprecher (gleichzeitig)', sub: charItems(cid => {
        if (commit(x => { x.chars.push(cid); }, l)) afterChange(l.id);
      }, l.chars),
    } : null,
    others.length && S.p.characters.length > 2 ? { label: 'Alle Sprecher (Chor)', action: () => setAllChars(l) } : null,
    l.chars.length > 1 ? { label: `${charById(laneChar)?.name || 'Sprecher'} aus Zeile entfernen`, action: () => { snapshot(); l.chars = l.chars.filter(c => c !== laneChar); afterChange(l.id); } } : null,
    { sep: true },
    { label: 'Löschen', key: 'Entf', danger: true, action: () => lineAction(l.id, 'del') },
  ];
}

/** Wiederholung selbst verknüpfen: diese Zeile nutzt die Aufnahme einer anderen (ein Clip, mehrere Zeitpunkte).
    Vorschläge: gleicher Sprecher zuerst, dann ähnlichster Text. */
function repeatLinkMenu(l) {
  const norm = x => (x || '').toLowerCase().replace(/[^\p{L}\p{N} ]/gu, ' ').split(/\s+/).filter(Boolean);
  const mine = new Set(norm(l.text));
  const sim = o => { const w = norm(o.text); if (!w.length || !mine.size) return 0; const hit = w.filter(x => mine.has(x)).length; return 2 * hit / (w.length + mine.size); };
  const cands = S.p.lines.filter(o => o !== l && !o.repeat_of && o.repeat_of !== l.id && !S.p.lines.some(x => x.repeat_of === l.id && x === o))
    .map(o => ({ o, score: sim(o) + (o.chars[0] === l.chars[0] ? 1 : 0) }))
    .sort((a, b) => b.score - a.score).slice(0, 12);
  if (!cands.length) return null;
  const short = t => { t = (t || '…').trim(); return t.length > 42 ? t.slice(0, 40) + '…' : t; };
  return {
    label: 'Als Wiederholung verknüpfen mit …', icon: 'repeat',
    sub: cands.map(({ o }) => ({
      label: `#${lineNo(o)} ${short(o.text)}`, color: charById(o.chars[0])?.color,
      action: () => { snapshot(); l.repeat_of = o.id; S.p.lines.forEach(x => { if (x.repeat_of === l.id) x.repeat_of = o.id; }); afterChange(null); },
    })),
  };
}

/** Menü für eine leere Stelle. laneChar = Spur (oder null über Lineal/Wellenform). */
function emptyMenu(t, laneChar) {
  const c = laneChar && charById(laneChar);
  return [
    c ? { label: `Neue Zeile für ${c.name}`, key: 'Doppelklick', color: c.color, action: () => addLineAt(t, c.id) }
      : { label: 'Neue Zeile für …', sub: charItems(cid => addLineAt(t, cid)) },
    { label: c ? `Einfügen für ${c.name}` : 'Einfügen', key: 'Strg+V', disabled: !S.clip, action: () => pasteAt(t, c?.id) },
    { sep: true },
    { label: 'Ab hier abspielen', action: () => { S.playUntil = null; seek(t); startVideo(); } },
    { label: 'Playhead hierher setzen', action: () => seek(t) },
  ];
}

/* ------------------------------------------------------------ Eigene Mauszeiger
   Sehen aus wie der normale Windows-Pfeil, zeigen aber mit einem kleinen Abzeichen in der Akzentfarbe,
   was passiert: neue Zeile aufziehen (+), verschieben (Kreuzpfeile), Rand ziehen (Trimm-Leiste),
   Playhead setzen (Playhead-Zeiger). Werden bei jedem Designwechsel neu gebaut. */
const CUR = {};
function buildCursors() {
  const cs = getComputedStyle(document.documentElement);
  const acc = cs.getPropertyValue('--accent2').trim() || '#7c5cff';
  const red = cs.getPropertyValue('--tl-playhead').trim() || '#e5484d';
  const svg = (body, x, y, fb) => `url("data:image/svg+xml,${encodeURIComponent(
    `<svg xmlns='http://www.w3.org/2000/svg' width='32' height='32' viewBox='0 0 32 32'><defs><filter id='s' x='-30%' y='-30%' width='160%' height='160%'><feDropShadow dx='0' dy='1' stdDeviation='.7' flood-opacity='.5'/></filter></defs><g filter='url(#s)'>${body}</g></svg>`)}") ${x} ${y}, ${fb}`;
  const arrow = `<path d='M3 2.5v17.6l4.3-4 2.9 6.5 2.9-1.3-2.8-6.4h6z' fill='#fff' stroke='#111' stroke-width='1.2' stroke-linejoin='round'/>`;
  const badge = (inner, r = 6.8) => `<circle cx='23.2' cy='23.2' r='${r}' fill='${acc}' stroke='#fff' stroke-width='1.6'/>${inner}`;
  const plus = `<path d='M23.2 19.8v6.8M19.8 23.2h6.8' stroke='#fff' stroke-width='2' stroke-linecap='round'/>`;
  const cross = s => `<g transform='translate(23.2 23.2) scale(${s})' fill='none' stroke='#fff' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'><path d='M0-4.4v8.8M-4.4 0h8.8M-1.5-3 0-4.4 1.5-3M-1.5 3 0 4.4 1.5 3M-3-1.5-4.4 0-3 1.5M3-1.5 4.4 0 3 1.5'/></g>`;
  const sides = `<g fill='none' stroke='#fff' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'><path d='M19.6 23.2h7.2M21.4 21.4l-1.8 1.8 1.8 1.8M25 21.4l1.8 1.8-1.8 1.8'/></g>`;
  const trim = `<rect x='14.4' y='4.5' width='3.2' height='23' rx='1.6' fill='${acc}' stroke='#fff' stroke-width='1.2'/><path d='M3.5 16 8.6 10.9v3.3h4v3.6h-4v3.3zM28.5 16l-5.1-5.1v3.3h-4v3.6h4v3.3z' fill='#fff' stroke='#111' stroke-width='1.1' stroke-linejoin='round'/>`;
  CUR.create = svg(arrow + badge(plus), 3, 2, 'copy');
  CUR.move = svg(arrow + badge(cross(1)), 3, 2, 'grab');
  CUR.moving = svg(arrow + badge(cross(.85), 6), 3, 2, 'grabbing');
  CUR.pan = svg(arrow + badge(sides), 3, 2, 'pointer');
  CUR.trim = svg(trim, 16, 16, 'ew-resize');
  // eine Kante: Klammer zeigt, zu welcher Zeile sie gehört („[“ Anfang der rechten, „]“ Ende der linken)
  const bracket = dir => `<path d='M${16 - dir * 3} 5.5h${dir * 3}v21h${-dir * 3}' fill='none' stroke='#fff' stroke-width='5' stroke-linejoin='round'/><path d='M${16 - dir * 3} 5.5h${dir * 3}v21h${-dir * 3}' fill='none' stroke='${acc}' stroke-width='2.4' stroke-linejoin='round'/><path d='M3.5 16 8.6 10.9v3.3h3.4v3.6H8.6v3.3zM28.5 16l-5.1-5.1v3.3h-3.4v3.6h3.4v3.3z' fill='#fff' stroke='#111' stroke-width='1.1' stroke-linejoin='round'/>`;
  CUR.trimStart = svg(bracket(-1), 16, 16, 'ew-resize');
  CUR.trimEnd = svg(bracket(1), 16, 16, 'ew-resize');
  CUR.cut = svg(`<g transform='translate(4 4)' fill='none' stroke='#111' stroke-width='3.6' stroke-linecap='round'><circle cx='5' cy='18' r='3.2'/><circle cx='5' cy='6' r='3.2'/><path d='M7.6 16.2 22 4M7.6 7.8 22 20'/></g><g transform='translate(4 4)' fill='none' stroke='#fff' stroke-width='1.8' stroke-linecap='round'><circle cx='5' cy='18' r='3.2'/><circle cx='5' cy='6' r='3.2'/><path d='M7.6 16.2 22 4M7.6 7.8 22 20'/></g><circle cx='26' cy='16' r='1.6' fill='${red}'/>`, 26, 16, 'crosshair');
  CUR.rows = svg(`<g transform='rotate(90 16 16)'>${trim}</g>`, 16, 16, 'ns-resize');
  CUR.scrub = svg(`<path d='M16 9v19.5' stroke='#fff' stroke-width='3.6' stroke-linecap='round'/><path d='M16 9v19.5' stroke='${red}' stroke-width='1.7' stroke-linecap='round'/><path d='M10.6 2.8h10.8v4.3L16 11.8l-5.4-4.7z' fill='${red}' stroke='#fff' stroke-width='1.3' stroke-linejoin='round'/>`, 16, 10, 'crosshair');
  const root = document.documentElement.style;
  root.setProperty('--cur-trim', CUR.trim);
  root.setProperty('--cur-rows', CUR.rows);
  root.setProperty('--cur-move', CUR.move);
  root.setProperty('--cur-moving', CUR.moving);
  root.setProperty('--cur-scrub', CUR.scrub);
}
buildCursors();
document.addEventListener('vt-theme', buildCursors);

/* ------------------------------------------------------------ Timeline */
const cv = $('#timeline'), ctx = cv.getContext('2d');
const RULER = 18, OVERVIEW = 12;
const L = { wave: 44, laneH: 22, lanes: 1 };
let cvW = 0, cvH = 0;
try { S.tlH = +localStorage.getItem('vt.tlH') || 0; } catch { S.tlH = 0; }
try { S.waveH = +localStorage.getItem('vt.waveH') || 0; } catch { S.waveH = 0; }

function lanesCount() { return Math.max(1, S.p?.characters.length || 1); }
function resizeTimeline() {
  const w = $('#timelineWrap').clientWidth;
  const all = lanesCount();
  const h = Math.round(S.tlH || RULER + 44 + 24 * Math.min(all, 6) + OVERVIEW + 6);
  // Wellenform: eigene Höhe (Kante zur ersten Spur ziehen), aber alle Spuren behalten mindestens 14 px
  const waveMax = Math.max(26, h - RULER - OVERVIEW - 6 - 14 * all);
  L.wave = S.waveH ? clamp(S.waveH, 26, waveMax) : clamp(Math.round(h * 0.2), 26, 90);
  const avail = h - RULER - L.wave - OVERVIEW - 6;
  L.lanes = clamp(Math.floor(avail / 14), 1, all);
  L.laneH = clamp(avail / L.lanes, 14, 90);
  const dpr = window.devicePixelRatio || 1;
  if (w === cvW && h === cvH) return;
  cvW = w; cvH = h;
  cv.width = w * dpr; cv.height = h * dpr; cv.style.height = h + 'px';
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
window.addEventListener('resize', () => { resizeTimeline(); drawTimeline(); });

const x2t = x => S.viewStart + x / S.pxPerSec;
const t2x = t => (t - S.viewStart) * S.pxPerSec;
const laneTop = () => RULER + L.wave;
function laneOf(charId) { return clamp(S.p.characters.findIndex(c => c.id === charId), 0, L.lanes - 1); }
function laneY(i) { return laneTop() + i * L.laneH; }
function laneAt(y) { return clamp(Math.floor((y - laneTop()) / L.laneH), 0, L.lanes - 1); }
function charOfLane(i) { return S.p.characters[i]?.id; }

function clampView() {
  const vis = cvW / S.pxPerSec;
  S.viewStart = clamp(S.viewStart, 0, Math.max(0, (S.p?.duration || 0) - vis * 0.5));
}
function ensureVisible(a, b) {
  const vis = cvW / S.pxPerSec;
  if (a < S.viewStart || b > S.viewStart + vis) { S.viewStart = a - vis * 0.25; clampView(); }
}
function followView(t) {
  const vis = cvW / S.pxPerSec;
  if (t > S.viewStart + vis * 0.85 || t < S.viewStart) { S.viewStart = t - vis * 0.15; clampView(); }
}
function zoom(f, anchorX = cvW / 2) {
  const t = x2t(anchorX);
  S.pxPerSec = clamp(S.pxPerSec * f, Math.max(0.5, cvW / Math.max(1, S.p?.duration || 60)), 600);
  S.viewStart = t - anchorX / S.pxPerSec; clampView();
}
/* Zoom per Knopf oder +/−: gleitet in ~0,2 s zum Ziel (logarithmisch, wirkt gleichmäßig), die Mitte
   bleibt stehen. Mehrmals schnell drücken addiert sich. Strg+Mausrad zoomt weiter sofort. */
let zoomAnim = null;
function zoomSmooth(f) {
  if (!motionOK()) return zoom(f);
  const anchorX = cvW / 2, t = x2t(anchorX);
  const min = Math.max(0.5, cvW / Math.max(1, S.p?.duration || 60));
  const target = clamp((zoomAnim ? zoomAnim.to : S.pxPerSec) * f, min, 600);
  zoomAnim = { from: S.pxPerSec, to: target, t, t0: performance.now() };
  const a = zoomAnim;
  const step = now => {
    if (zoomAnim !== a) return;
    const p = Math.min(1, (now - a.t0) / 220), e = 1 - Math.pow(1 - p, 3);
    S.pxPerSec = a.from * Math.pow(a.to / a.from, e);
    S.viewStart = a.t - anchorX / S.pxPerSec; clampView();
    drawTimeline();
    if (p < 1) requestAnimationFrame(step); else zoomAnim = null;
  };
  requestAnimationFrame(step);
}
$('#btnZoomIn').onclick = () => zoomSmooth(1.5);
$('#btnZoomOut').onclick = () => zoomSmooth(1 / 1.5);
$('#btnCutMode').onclick = () => {
  setCutMode(!S.cutMode);
  if (S.cutMode) toast(tf('Video schneiden: Stelle in der Timeline aufziehen. Esc oder der Knopf beendet das Schneiden.'), false, 4500);
};
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && S.cutMode && !$('#editor').hidden && !document.querySelector('.dlg-overlay')) setCutMode(false);
});

/* Farben der Timeline kommen aus dem Design (hell/dunkel), neu gelesen bei jedem Wechsel */
const TLC = {};
function readTimelineColors() {
  const cs = getComputedStyle(document.documentElement);
  for (const k of ['bg', 'text', 'grid', 'wave', 'lane-a', 'lane-b', 'lane-target', 'label', 'overview', 'view', 'selected', 'playhead'])
    TLC[k] = cs.getPropertyValue('--tl-' + k).trim();
  TLC.danger = cs.getPropertyValue('--danger').trim();
}
readTimelineColors();
document.addEventListener('vt-theme', () => { readTimelineColors(); if (S.p) drawTimeline(); });

function drawBlock(x, y, w, h, color, { selected = false, alpha = 0.75, text = '', dashed = false, invalid = false, icon = null } = {}) {
  ctx.globalAlpha = alpha;
  ctx.fillStyle = color;
  ctx.fillRect(x, y, w, h);
  ctx.globalAlpha = 1;
  if (selected || dashed || invalid) {
    ctx.save();
    ctx.strokeStyle = invalid ? TLC.danger : TLC.selected; ctx.lineWidth = 2;
    if (dashed) ctx.setLineDash([4, 3]);
    ctx.strokeRect(x + 1, y + 1, w - 2, h - 2);
    ctx.restore();
  }
  if (w > 24 && text) {
    ctx.save(); ctx.beginPath(); ctx.rect(x + 3, y, w - 6, h); ctx.clip();
    const fs = Math.round(clamp(h - 8, 10, 14));
    ctx.fillStyle = '#0d0f14'; ctx.font = `${fs}px Segoe UI`;
    ctx.textBaseline = 'middle';
    let tx = x + 5;
    if (icon && window.icPaths) {   // eigenes Icon (z. B. Wiederholung) als Zeichenpfad
      const size = fs + 2;
      ctx.save();
      ctx.translate(tx, y + h / 2 - size / 2 + 1);
      ctx.scale(size / 24, size / 24);
      ctx.strokeStyle = '#0d0f14'; ctx.lineWidth = 2.2; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
      icPaths(icon).forEach(p => (p.fill ? ctx.fill(p.path) : ctx.stroke(p.path)));
      ctx.restore();
      tx += size + 4;
    }
    ctx.fillText(text, tx, y + h / 2 + 1); ctx.restore();
  }
}

const CUT_RED = '#e5484d';
let cutPat = null;
function cutPattern() {   // rote Schraffur für rausgeschnittene Stellen
  if (cutPat) return cutPat;
  const c = document.createElement('canvas');
  c.width = c.height = 10;
  const g = c.getContext('2d');
  g.fillStyle = 'rgba(10,11,15,.55)'; g.fillRect(0, 0, 10, 10);
  g.strokeStyle = 'rgba(229,72,77,.55)'; g.lineWidth = 2.2;
  g.beginPath(); g.moveTo(-2, 12); g.lineTo(12, -2); g.moveTo(-2, 2); g.lineTo(2, -2); g.moveTo(8, 12); g.lineTo(12, 8); g.stroke();
  cutPat = ctx.createPattern(c, 'repeat');
  return cutPat;
}

function setCutMode(on) {
  S.cutMode = !!on;
  $('#btnCutMode')?.classList.toggle('on', S.cutMode);
  if (S.p) drawTimeline();
}

function drawTimeline() {
  if (!S.p || $('#editor').hidden) return;
  resizeTimeline();
  const W = cvW, H = cvH, dur = S.p.duration || 1;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = TLC.bg; ctx.fillRect(0, 0, W, H);

  // Lineal
  const steps = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
  const step = steps.find(s => s * S.pxPerSec >= 70) || 600;
  ctx.fillStyle = TLC.text; ctx.font = '10px Segoe UI'; ctx.strokeStyle = TLC.grid; ctx.textBaseline = 'alphabetic';
  for (let t = Math.floor(S.viewStart / step) * step; t < x2t(W); t += step) {
    const x = Math.round(t2x(t)) + 0.5;
    ctx.beginPath(); ctx.moveTo(x, RULER - 6); ctx.lineTo(x, H - OVERVIEW - 4); ctx.stroke();
    ctx.fillText(step < 1 ? fmt(t) : fmt(t, false), x + 3, 11);
  }

  // Wellenform (Stimmen)
  if (S.peaks) {
    const pk = S.peaks.stimmen, fps = S.peaks.fps, mid = RULER + L.wave / 2;
    ctx.fillStyle = TLC.wave;
    for (let x = 0; x < W; x++) {
      const a = Math.floor(x2t(x) * fps), b = Math.max(a + 1, Math.floor(x2t(x + 1) * fps));
      if (a >= pk.length) break;
      let m = 0;
      for (let i = Math.max(0, a); i < Math.min(b, pk.length); i++) if (pk[i] > m) m = pk[i];
      const h = (m / 255) * (L.wave / 2 - 2);
      ctx.fillRect(x, mid - h, 1, h * 2 || 1);
    }
  }

  // Spuren
  const drag = S.drag;
  for (let i = 0; i < L.lanes; i++) {
    ctx.fillStyle = drag?.targetLane === i ? TLC['lane-target'] : (i % 2 ? TLC['lane-b'] : TLC['lane-a']);
    ctx.fillRect(0, laneY(i), W, L.laneH);
  }
  const t0 = S.viewStart, t1 = x2t(W), bh = L.laneH - 4;
  for (const l of S.p.lines) {
    if (l.end < t0 || l.start > t1) continue;
    for (const cid of l.chars) {
      const c = charById(cid); if (!c) continue;
      const x = t2x(l.start), w = Math.max(2, (l.end - l.start) * S.pxPerSec);
      const isDrag = drag?.line === l;
      drawBlock(x, laneY(laneOf(cid)) + 2, w, bh, c.color, {
        selected: isSel(l.id), alpha: l.repeat_of ? 0.4 : (isSel(l.id) ? 1 : 0.72),
        text: l.text || '…', icon: l.repeat_of ? 'repeat' : null, dashed: !!l.repeat_of,
        invalid: isDrag && drag.invalid,
      });
    }
  }
  // Vorschau beim Aufziehen einer neuen Zeile
  if (drag?.kind === 'create' && drag.moved) {
    const c = charById(charOfLane(drag.lane));
    const a = Math.min(drag.t0, drag.t1), b = Math.max(drag.t0, drag.t1);
    drawBlock(t2x(a), laneY(drag.lane) + 2, Math.max(2, (b - a) * S.pxPerSec), bh, c?.color || '#888',
      { alpha: 0.45, dashed: true, text: `${(b - a).toFixed(1)} s` });
  }
  // Rausgeschnittene Stellen: schraffiert über Wellenform und Spuren, Dauer oben im Lineal
  const cutBottom = H - OVERVIEW - 2, hatch = cutPattern();
  const drawCut = (a, b, alpha) => {
    const x0 = t2x(a), w = Math.max(2, (b - a) * S.pxPerSec);
    ctx.globalAlpha = alpha;
    ctx.fillStyle = hatch; ctx.fillRect(x0, RULER - 4, w, cutBottom - RULER + 4);
    ctx.fillStyle = CUT_RED; ctx.fillRect(x0, RULER - 4, 2, cutBottom - RULER + 4); ctx.fillRect(x0 + w - 2, RULER - 4, 2, cutBottom - RULER + 4);
    ctx.globalAlpha = 1;
    if (w > 46) {
      ctx.font = '10px Segoe UI'; ctx.textBaseline = 'middle';
      const txt = `✂ ${(b - a).toFixed(1).replace('.', ',')} s`, tw = ctx.measureText(txt).width;
      ctx.fillStyle = CUT_RED; ctx.fillRect(x0 + 4, RULER - 3, tw + 8, 13);
      ctx.fillStyle = '#fff'; ctx.fillText(txt, x0 + 8, RULER + 3.5);
      ctx.textBaseline = 'alphabetic';
    }
  };
  for (const c of S.p.cuts || []) if (c[1] >= t0 && c[0] <= t1) drawCut(c[0], c[1], 1);
  if (drag?.kind === 'cut' && drag.moved) drawCut(Math.min(drag.t0, drag.t1), Math.max(drag.t0, drag.t1), 0.75);

  // Kante Wellenform/Spuren: beim Überfahren oder Ziehen als Griff sichtbar
  if (S.waveHover || S.drag?.kind === 'wave') {
    ctx.fillStyle = TLC.view || '#fff';
    ctx.globalAlpha = 0.9;
    ctx.fillRect(0, laneTop() - 1, W, 2);
    ctx.fillRect(W / 2 - 16, laneTop() - 3, 32, 6);
    ctx.globalAlpha = 1;
  }

  // Namen der Spuren
  ctx.font = '10px Segoe UI'; ctx.textBaseline = 'middle';
  S.p.characters.slice(0, L.lanes).forEach((c, i) => {
    const txt = `${i < 9 ? i + 1 + ' ' : ''}${c.name}`; const tw = ctx.measureText(txt).width;
    ctx.fillStyle = TLC.label; ctx.fillRect(0, laneY(i) + 3, tw + 8, 13);
    ctx.fillStyle = c.color; ctx.fillText(txt, 4, laneY(i) + 10);
  });
  ctx.textBaseline = 'alphabetic';

  // Übersicht
  const oy = H - OVERVIEW - 2;
  ctx.fillStyle = TLC.overview; ctx.fillRect(0, oy, W, OVERVIEW);
  for (const l of S.p.lines) {
    const c = charById(l.chars[0]); ctx.fillStyle = c ? c.color : '#666';
    ctx.fillRect((l.start / dur) * W, oy + 3, Math.max(1, ((l.end - l.start) / dur) * W), OVERVIEW - 6);
  }
  ctx.fillStyle = CUT_RED;
  for (const c of S.p.cuts || []) ctx.fillRect((c[0] / dur) * W, oy, Math.max(2, ((c[1] - c[0]) / dur) * W), OVERVIEW);
  ctx.strokeStyle = TLC.view;
  ctx.strokeRect((S.viewStart / dur) * W + 0.5, oy + 0.5, Math.max(3, (W / S.pxPerSec / dur) * W) - 1, OVERVIEW - 1);

  // Playhead
  const px = Math.round(t2x(video.currentTime)) + 0.5;
  ctx.strokeStyle = TLC.playhead; ctx.beginPath(); ctx.moveTo(px, 0); ctx.lineTo(px, oy); ctx.stroke();
  ctx.fillStyle = TLC.playhead; ctx.beginPath(); ctx.moveTo(px - 5, 0); ctx.lineTo(px + 5, 0); ctx.lineTo(px, 7); ctx.fill();
  ctx.fillStyle = TLC.selected; ctx.fillRect((video.currentTime / dur) * W, oy, 1, OVERVIEW);
}

function hitTest(x, y) {
  if (y >= cvH - OVERVIEW - 2) return { kind: 'overview' };
  if (S.cutMode) return { kind: 'cut', cut: cutAt(x2t(x)) };
  if (Math.abs(y - laneTop()) <= 3) return { kind: 'wave-edge' };
  if (y < laneTop()) return { kind: 'scrub' };
  const laneIdx = laneAt(y);
  if (y >= laneY(L.lanes)) return { kind: 'scrub' };
  const t = x2t(x), tol = 6 / S.pxPerSec;
  const cands = S.p.lines.filter(l => l.chars.some(c => laneOf(c) === laneIdx) && t >= l.start - tol && t <= l.end + tol);
  cands.sort((a, b) => (a.id === S.sel ? -1 : b.id === S.sel ? 1 : 0));
  // Gemeinsame Kante zweier Zeilen derselben Spur: genau in der Mitte beide ziehen, ein Stück links nur das Ende
  // der linken, ein Stück rechts nur den Anfang der rechten Zeile (eigener Mauszeiger je Fall)
  for (const l of cands) {
    const nb = S.p.lines.find(o => o !== l && o.chars.some(c => laneOf(c) === laneIdx) && Math.abs(o.start - l.end) <= NEIGHBOUR_GAP);
    if (!nb) continue;
    const xb = t2x((l.end + nb.start) / 2);
    if (Math.abs(x - xb) > 7) continue;
    if (Math.abs(x - xb) <= 2) return { kind: 'end', line: l, laneIdx, shared: true };
    return x < xb ? { kind: 'end', line: l, laneIdx } : { kind: 'start', line: nb, laneIdx };
  }
  for (const l of cands) {
    const w = (l.end - l.start) * S.pxPerSec, edge = Math.min(6, w / 3);
    if (Math.abs(t2x(l.start) - x) <= edge) return { kind: 'start', line: l, laneIdx };
    if (Math.abs(t2x(l.end) - x) <= edge) return { kind: 'end', line: l, laneIdx };
  }
  const body = cands.find(l => t >= l.start && t <= l.end);
  if (body) return { kind: 'move', line: body, laneIdx };
  return { kind: 'empty', laneIdx };
}

/** Einrasten an Playhead und Rändern anderer Zeilen (7 px). */
function snapTime(t, excludeId, excludeId2) {
  if (setting('snap') === false) return t;   // Einrasten in den Einstellungen abgeschaltet
  let best = t, bestD = 7;
  const consider = c => { const d = Math.abs(t2x(c) - t2x(t)); if (d < bestD) { bestD = d; best = c; } };
  consider(video.currentTime);
  for (const o of S.p.lines) {
    if (o.id === excludeId || o.id === excludeId2 || o.end < S.viewStart || o.start > x2t(cvW)) continue;
    consider(o.start); consider(o.end);
  }
  return best;
}

cv.addEventListener('mousemove', e => {
  if (S.drag) return;
  const h = hitTest(e.offsetX, e.offsetY);
  cv.style.cursor = h.kind === 'cut' ? CUR.cut : h.shared ? CUR.trim : h.kind === 'start' ? CUR.trimStart : h.kind === 'end' ? CUR.trimEnd
    : h.kind === 'move' ? CUR.move : h.kind === 'empty' ? CUR.create : h.kind === 'wave-edge' ? CUR.rows : h.kind === 'overview' ? CUR.pan : CUR.scrub;
  const edge = h.kind === 'wave-edge';
  if (edge !== !!S.waveHover) { S.waveHover = edge; drawTimeline(); }
  cv.title = h.kind === 'cut' ? (h.cut ? 'Rausgeschnittene Stelle · Klick = Schnitt entfernen' : 'Ziehen = Stelle aus dem Video rausschneiden · Esc = fertig')
    : h.shared ? 'Gemeinsame Kante: beide Zeilen ziehen (eine wird länger, die andere kürzer)'
    : h.kind === 'wave-edge' ? 'Ziehen: Wellenform größer/kleiner · Doppelklick: Standardgröße'
    : h.kind === 'empty' ? 'Ziehen = neue Zeile · Doppelklick = neue Zeile an der Stimme · Rechtsklick = Menü'
    : h.kind === 'move' ? 'Ziehen = verschieben (auch in andere Sprecher-Spur) · Rechtsklick = Menü'
    : h.kind === 'start' ? 'Anfang dieser Zeile ziehen' : h.kind === 'end' ? 'Ende dieser Zeile ziehen'
    : cutAt(x2t(e.offsetX)) ? 'Rausgeschnittene Stelle · Rechtsklick = Schnitt entfernen' : '';
});

cv.addEventListener('mousedown', e => {
  if (!S.p || e.button !== 0) return;
  closeMenu();
  const x = e.offsetX, y = e.offsetY, h = hitTest(x, y);
  if (h.kind === 'cut') {
    const t0 = clamp(x2t(x), 0, S.p.duration);
    S.drag = {
      kind: 'cut', t0, t1: t0, moved: false,
      move: ev => {
        if (!S.drag.moved && Math.abs(ev.offsetX - x) < 4) return;
        S.drag.moved = true;
        S.drag.t1 = clamp(x2t(ev.offsetX), 0, S.p.duration);
      },
      up: async d => {
        if (!d.moved) {
          if (!h.cut) { S.playUntil = null; seek(t0); return; }
          const ok = await dialog({ title: 'Schnitt entfernen?', icon: 'scissors', ok: 'Schnitt entfernen',
            text: tf('Die Stelle von {} bis {} kommt wieder ins Video.', fmt(h.cut[0]), fmt(h.cut[1])) });
          if (ok) removeCut(h.cut);
          return;
        }
        const a = Math.min(d.t0, d.t1), b = Math.max(d.t0, d.t1);
        if (b - a < 0.2) { drawTimeline(); return; }
        snapshot(); addCut(a, b); scheduleSave(); drawTimeline();
        const n = S.p.lines.filter(l => l.start >= a && l.end <= b).length;
        toast(tf('{} s rausgeschnitten. Strg+Z macht es rückgängig.', (b - a).toFixed(1).replace('.', ','))
          + (n ? ' ' + tf('{} Zeilen darin fehlen im Export.', n) : ''), false, 5000);
      },
    };
  } else if (h.kind === 'wave-edge') {
    const startWave = L.wave, y0 = e.clientY;
    S.drag = {
      kind: 'wave',
      move: ev => { S.waveH = clamp(startWave + (ev.clientY - y0), 26, 400); resizeTimeline(); drawTimeline(); },
      up: () => { S.waveH = L.wave; try { localStorage.setItem('vt.waveH', String(S.waveH)); } catch { /* egal */ } },
    };
  } else if (h.kind === 'overview') {
    const jump = t => { S.viewStart = t - cvW / S.pxPerSec / 2; clampView(); };
    jump((x / cvW) * S.p.duration);
    S.drag = { kind: 'overview', move: ev => jump((ev.offsetX / cvW) * S.p.duration) };
  } else if (h.kind === 'scrub') {
    S.playUntil = null; seek(x2t(x));
    S.drag = { kind: 'scrub', move: ev => seek(x2t(ev.offsetX)) };
  } else if (h.kind === 'empty') {
    // Klick = Playhead setzen, Ziehen = neue Zeile aufziehen
    const t0 = snapTime(x2t(x));
    S.drag = {
      kind: 'create', lane: h.laneIdx, t0, t1: t0, moved: false,
      move: ev => {
        if (!S.drag.moved && Math.abs(ev.offsetX - x) < 4) return;
        S.drag.moved = true;
        S.drag.t1 = clamp(snapTime(x2t(ev.offsetX)), 0, S.p.duration);
      },
      up: d => {
        if (!d.moved) { S.playUntil = null; seek(t0); return; }
        const a = Math.min(d.t0, d.t1), b = Math.max(d.t0, d.t1);
        if (b - a < MIN_LEN) return;
        const cid = charOfLane(d.lane);
        const l = { id: newId(), start: r3(a), end: r3(b), text: '', chars: [cid] };
        if (insertLine(l, d.t0 < d.t1 ? a + 0.001 : b - 0.001)) autoText(l);
      },
    };
  } else {
    const l = h.line;
    if ((e.ctrlKey || e.metaKey) && h.kind === 'move') {   // Strg: Zeile zur Auswahl dazu oder weg
      if (!S.sel) selectLine(l.id, { reveal: false }); else toggleSel(l.id);
      return;
    }
    const dragChar = l.chars.find(c => laneOf(c) === h.laneIdx) || l.chars[0];
    const orig = { start: l.start, end: l.end, chars: [...l.chars] }, tDown = x2t(x);
    const snap = JSON.stringify({ lines: S.p.lines, characters: S.p.characters });
    const group = isSel(l.id) ? selectedLines().filter(o => o !== l) : [];
    const groupOrig = group.map(o => ({ o, start: o.start, end: o.end }));
    const groupIds = new Set([l.id, ...group.map(o => o.id)]);
    if (!groupOrig.length) selectLine(l.id, { reveal: false });
    // Grenzt direkt eine Zeile desselben Sprechers an? Dann wandert die gemeinsame Kante:
    // eine Zeile wird größer, die andere genauso viel kleiner.
    let buddy = null, buddyOrig = null;
    if (h.shared) {   // nur mittig auf der gemeinsamen Kante: beide Zeilen ziehen
      const near = o => (h.kind === 'start' ? Math.abs(o.end - l.start) : Math.abs(o.start - l.end));
      buddy = S.p.lines
        .filter(o => o !== l && o.chars.some(c => l.chars.includes(c) && laneOf(c) === h.laneIdx)
                  && near(o) <= NEIGHBOUR_GAP)
        .sort((p, q) => near(p) - near(q))[0] || null;
      if (buddy) buddyOrig = { start: buddy.start, end: buddy.end };
    }
    // Freier Bereich um die Zeile (für Ränder). Der Nachbar an der gemeinsamen Kante ist dabei kein Hindernis
    // (sonst ginge die Kante nur in eine Richtung), dafür darf auch er beim Größerwerden nirgends anstoßen.
    const skip = new Set([l.id, buddy?.id].filter(Boolean));
    const gap = freeGap(l.chars, (l.start + l.end) / 2, skip) || { lo: 0, hi: S.p.duration };
    const bgap = buddy ? freeGap(buddy.chars, (buddy.start + buddy.end) / 2, skip) || { lo: 0, hi: S.p.duration } : null;
    S.drag = {
      kind: h.kind, line: l, moved: false, invalid: false, targetLane: null,
      move: ev => {
        const d = S.drag;
        if (!d.moved && Math.abs(ev.offsetX - x) < 3 && Math.abs(ev.offsetY - y) < 3) return;
        d.moved = true;
        cv.style.cursor = h.kind === 'move' ? CUR.moving : CUR.trim;
        const dt = x2t(ev.offsetX) - tDown;
        if (h.kind === 'start') {
          const lo = buddy ? Math.max(gap.lo, buddyOrig.start + MIN_LEN + GAP) : gap.lo;
          const hi = buddy ? Math.min(l.end - MIN_LEN, bgap.hi + GAP) : l.end - MIN_LEN;
          l.start = r3(clamp(snapTime(orig.start + dt, l.id, buddy?.id), lo, hi));
          if (buddy) buddy.end = r3(l.start - GAP);   // Nachbar wird genau um dasselbe kürzer/länger
          seek(l.start);
        } else if (h.kind === 'end') {
          const hi = buddy ? Math.min(gap.hi, buddyOrig.end - MIN_LEN - GAP) : gap.hi;
          const lo = buddy ? Math.max(l.start + MIN_LEN, bgap.lo - GAP) : l.start + MIN_LEN;
          l.end = r3(clamp(snapTime(orig.end + dt, l.id, buddy?.id), lo, hi));
          if (buddy) buddy.start = r3(l.end + GAP);
          seek(l.end);
        } else if (groupOrig.length) {
          // Mehrere Zeilen zusammen: nur in der Zeit, der Sprecher bleibt bei allen
          const len = orig.end - orig.start;
          let s = clamp(snapTime(orig.start + dt, l.id), 0, S.p.duration - len);
          let shift = s - orig.start;
          for (const it of groupOrig) {   // keine darf über den Rand geschoben werden
            const ln = it.end - it.start;
            shift = clamp(shift, -it.start, S.p.duration - ln - it.start);
          }
          d.invalid = false;
          l.start = r3(orig.start + shift); l.end = r3(orig.start + shift + len);
          for (const it of groupOrig) {
            it.o.start = r3(it.start + shift);
            it.o.end = r3(it.end + shift);
          }
          const clash = ln => S.p.lines.some(o => o !== ln && !groupIds.has(o.id)
            && o.chars.some(c => ln.chars.includes(c))
            && ln.start < o.end + GAP && ln.end > o.start - GAP);
          if (clash(l) || groupOrig.some(it => clash(it.o))) d.invalid = true;
          seek(l.start);
        } else {
          // Verschieben, auch in eine andere Sprecher-Spur
          const lane = laneAt(ev.offsetY);
          // Passen nicht alle Sprecher in die Timeline, zeigt laneAt die letzte Spur. Dann bleibt der Sprecher,
          // sonst würde waagerechtes Verschieben die Zeile stillschweigend einem anderen Sprecher geben.
          const target = (laneOf(dragChar) === lane ? dragChar : charOfLane(lane)) || dragChar;
          l.chars = [...new Set(orig.chars.map(c => (c === dragChar ? target : c)))];
          d.targetLane = target !== dragChar ? lane : null;
          const len = orig.end - orig.start;
          let s = orig.start + dt;
          if (Math.abs(ev.offsetX - x) < 8) s = orig.start; // nur Spur gewechselt -> Zeit bleibt exakt
          else {
            const sEnd = snapTime(s + len, l.id) - len, sStart = snapTime(s, l.id);
            s = Math.abs(sStart - s) <= Math.abs(sEnd - s) ? sStart : sEnd;
          }
          s = clamp(s, 0, S.p.duration - len);
          const g = freeGap(l.chars, s + len / 2, l.id);
          if (!g) { d.invalid = true; l.start = r3(s); l.end = r3(s + len); return; }
          d.invalid = false;
          s = clamp(s, g.lo, Math.max(g.lo, g.hi - len));
          l.start = r3(s); l.end = r3(Math.min(s + len, g.hi));
          if (l.end - l.start < MIN_LEN) d.invalid = true;
        }
      },
      up: d => {
        if (!d.moved) { seek(l.start); return; }
        if (d.invalid) {
          Object.assign(l, orig);
          if (buddy) Object.assign(buddy, buddyOrig);
          for (const it of groupOrig) Object.assign(it.o, { start: it.start, end: it.end });
          toast(BLOCK_MSG, true);
          drawTimeline();
          return;
        }
        S.undo.push(snap); if (S.undo.length > 200) S.undo.shift(); S.redo = [];
        if (groupOrig.length && h.kind === 'move') {
          afterChange(null);
          toast(tf('{} Zeilen verschoben.', groupOrig.length + 1));
          return;
        }
        const trimmed = h.kind === 'move' && Math.abs((l.end - l.start) - (orig.end - orig.start)) > 0.001;
        if (trimmed) toast(TRIM_MSG);
        afterChange(l.id, l.chars.join() !== orig.chars.join());
      },
    };
  }
  const onMove = ev => {
    const r = cv.getBoundingClientRect();
    const ox = ev.clientX - r.left;
    // am Rand automatisch mitscrollen
    if (S.drag && S.drag.kind !== 'overview' && S.drag.kind !== 'wave') {
      if (ox < 24) { S.viewStart -= (24 - ox) / S.pxPerSec * 0.6; clampView(); }
      else if (ox > cvW - 24) { S.viewStart += (ox - cvW + 24) / S.pxPerSec * 0.6; clampView(); }
    }
    S.drag?.move({ offsetX: clamp(ox, 0, cvW), offsetY: ev.clientY - r.top, clientY: ev.clientY });
  };
  const onUp = () => {
    const d = S.drag;
    S.drag = null;
    d?.up?.(d);
    cv.style.cursor = '';
    window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp);
  };
  window.addEventListener('mousemove', onMove); window.addEventListener('mouseup', onUp);
});

cv.addEventListener('mouseleave', () => { if (S.waveHover && !S.drag) { S.waveHover = false; drawTimeline(); } });
cv.addEventListener('dblclick', e => {
  const h = hitTest(e.offsetX, e.offsetY);
  if (h.kind === 'wave-edge') {
    S.waveH = 0; try { localStorage.removeItem('vt.waveH'); } catch { /* egal */ }
    resizeTimeline(); drawTimeline(); return;
  }
  if (h.kind === 'empty') addLineAt(x2t(e.offsetX), charOfLane(h.laneIdx));
  else if (h.line) playRange(h.line.start, h.line.end);
});

cv.addEventListener('contextmenu', e => {
  e.preventDefault();
  if (!S.p) return;
  const h = hitTest(e.offsetX, e.offsetY), t = x2t(e.offsetX);
  const cut = cutAt(t);
  if (cut) {
    openMenu([{ label: 'Schnitt entfernen (Stelle wieder ins Video)', icon: 'scissors', action: () => removeCut(cut) },
      { sep: true }, { label: 'Playhead hierher setzen', action: () => seek(t) }], e.clientX, e.clientY);
    return;
  }
  if (h.line) {
    selectLine(h.line.id, { reveal: false });
    openMenu(lineMenu(h.line, h.line.chars.find(c => laneOf(c) === h.laneIdx) || h.line.chars[0], t), e.clientX, e.clientY);
  } else if (h.kind === 'empty') {
    openMenu(emptyMenu(t, charOfLane(h.laneIdx)), e.clientX, e.clientY);
  } else if (h.kind === 'scrub') {
    openMenu(emptyMenu(t, null), e.clientX, e.clientY);
  }
});

cv.addEventListener('wheel', e => {
  e.preventDefault();
  if (e.ctrlKey) zoom(e.deltaY < 0 ? 1.25 : 0.8, e.offsetX);
  else { S.viewStart += (e.deltaY + e.deltaX) / S.pxPerSec; clampView(); }
}, { passive: false });

/* ------------------------------------------------------------ Größe per Ziehen */
function dragResize(handle, onMove, onDone) {
  handle.addEventListener('mousedown', e => {
    e.preventDefault();
    const start = { x: e.clientX, y: e.clientY };
    document.body.classList.add('resizing');
    document.body.style.cursor = getComputedStyle(handle).cursor;
    const mm = ev => onMove(ev.clientX - start.x, ev.clientY - start.y);
    const mu = () => {
      document.body.classList.remove('resizing');
      document.body.style.cursor = '';
      window.removeEventListener('mousemove', mm); window.removeEventListener('mouseup', mu);
      onDone?.();
    };
    window.addEventListener('mousemove', mm); window.addEventListener('mouseup', mu);
  });
}
{
  let startH = 0;
  const h = $('#tlSplit');
  h.addEventListener('mousedown', () => { startH = cvH; });
  dragResize(h, (dx, dy) => {
    S.tlH = clamp(startH - dy, 90, innerHeight - 220);
    resizeTimeline(); drawTimeline();
  }, () => { try { localStorage.setItem('vt.tlH', String(S.tlH)); } catch { /* egal */ } });
  h.addEventListener('dblclick', () => { S.tlH = 0; try { localStorage.removeItem('vt.tlH'); } catch { /* egal */ } resizeTimeline(); drawTimeline(); });
}
{
  let startW = 0;
  const h = $('#sideSplit'), side = $('.right');
  try { const w = +localStorage.getItem('vt.sideW'); if (w) side.style.flexBasis = w + 'px'; } catch { /* egal */ }
  h.addEventListener('mousedown', () => { startW = side.getBoundingClientRect().width; });
  dragResize(h, dx => {
    side.style.flexBasis = clamp(startW - dx, 320, innerWidth - 420) + 'px';
    cvW = 0; resizeTimeline(); drawTimeline();
  }, () => { try { localStorage.setItem('vt.sideW', String(Math.round(side.getBoundingClientRect().width))); } catch { /* egal */ } });
}

/* ------------------------------------------------------------ Tastatur */
document.addEventListener('keydown', e => {
  if (!S.p || $('#editor').hidden) return;
  const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName);
  const sel = lineById(S.sel);
  const k = e.key;
  if (k === 'Escape') { if (!menuEl.hidden) return closeMenu(); if (typing) return document.activeElement.blur(); }
  if ((e.ctrlKey || e.metaKey) && !typing) {
    const kk = k.toLowerCase();
    if (kk === 'z') { e.preventDefault(); return e.shiftKey ? redo() : undo(); }
    if (kk === 'y') { e.preventDefault(); return redo(); }
    if (kk === 'c' && sel) { e.preventDefault(); return copyLine(sel); }
    if (kk === 'x' && sel) { e.preventDefault(); return copyLine(sel, true); }
    if (kk === 'v') { e.preventDefault(); return pasteAt(video.currentTime); }
    return;
  }
  if (typing || e.ctrlKey || e.metaKey || e.altKey) return;

  const idx = sel ? S.p.lines.indexOf(sel) : -1;
  if (k === ' ') { e.preventDefault(); togglePlay(); }
  else if (k === 'ArrowLeft') { e.preventDefault(); seek(video.currentTime - (e.shiftKey ? 5 : 1)); }
  else if (k === 'ArrowRight') { e.preventDefault(); seek(video.currentTime + (e.shiftKey ? 5 : 1)); }
  else if (k === 'ArrowUp' || k === 'ArrowDown' || k === 'j' || k === 'k') {
    e.preventDefault();
    const dir = k === 'ArrowUp' || k === 'k' ? -1 : 1;
    let next;
    if (idx < 0) next = dir > 0 ? S.p.lines.find(l => l.start >= video.currentTime) : [...S.p.lines].reverse().find(l => l.start < video.currentTime);
    else next = S.p.lines[clamp(idx + dir, 0, S.p.lines.length - 1)];
    if (next) selectLine(next.id, { seekTo: true });
  }
  else if (k === 'Enter' && sel) { e.preventDefault(); playRange(sel.start, sel.end); }
  else if ((k === 'i' || k === 'I') && sel) lineAction(sel.id, 'sP');
  else if ((k === 'o' || k === 'O') && sel) lineAction(sel.id, 'eP');
  else if ((k === 's' || k === 'S') && sel) splitLine(sel, video.currentTime);
  else if ((k === 'm' || k === 'M') && sel) mergeLine(sel);
  else if (k === 'n' || k === 'N') { e.preventDefault(); addLineAt(video.currentTime); }
  else if ((k === 'Delete' || k === 'Backspace') && sel) { e.preventDefault(); lineAction(sel.id, 'del'); }
  else if (/^[1-9]$/.test(k) && sel) {
    const c = S.p.characters[+k - 1];
    if (c && setPrimaryChar(sel, c.id)) afterChange(sel.id, true);
  }
  else if (k === '+') zoomSmooth(1.5);
  else if (k === '-') zoomSmooth(1 / 1.5);
});

/* ------------------------------------------------------------ Start
   Kurzer Auftritt: Logo springt herein, Schriftzug gleitet nach, die Karten und die ersten Projekte
   kommen nacheinander von unten. Mit „Bewegungen reduzieren“ steht alles sofort da. */
function intro() {
  if (!motionOK()) return;
  $('.brand img')?.animate([
    { opacity: 0, transform: 'scale(.35) rotate(-30deg)' },
    { opacity: 1, transform: 'scale(1.1) rotate(5deg)', offset: .62 },
    { opacity: 1, transform: 'none' }], { duration: 700, easing: 'cubic-bezier(.3, .8, .3, 1)', fill: 'backwards' });
  $('.brand-text')?.animate([{ opacity: 0, transform: 'translateX(-10px)' }, { opacity: 1, transform: 'none' }],
                            { duration: 450, delay: 180, easing: EASE_OUT, fill: 'backwards' });
  stagger($$('.topbar > :not(.brand):not(#btnBack)'), { delay: 220, step: 40, dy: -6, duration: 360 });
  if (!$('#home').hidden) {
    stagger([$('#dropZone'), $('.inbox-card'), $('.projects-card'), $('.home-foot')], { delay: 120, step: 80, dy: 16, duration: 520 });
    seenItems.set($('#projectList'), new Set());   // erste Projekte gestaffelt einblenden
  }
}
intro();
(async () => {
  await refreshState();
  const hash = decodeURIComponent(location.hash.slice(1));
  if (hash && lastState?.projects.some(p => p.id === hash && p.status === 'fertig')) openProject(hash);
})();
