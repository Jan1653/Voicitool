/* Eigene Hinweise (Tooltips) statt der eckigen Windows-Kästchen.
   Beim Überfahren wird aus title ein data-tip (dann zeigt der Browser nichts Eigenes) und es
   erscheint eine Blase im Voicitool-Stil. Kleine Elemente: Blase darunter mit Pfeil, große
   Flächen (z. B. Zeitleiste): Blase folgt dem Mauszeiger. Übersetzt wird data-tip von i18n.js. */
(function () {
  const DELAY = 480;      // bis zum ersten Hinweis
  const QUICK = 90;       // wenn gerade schon einer zu sehen war
  const GAP = 8;
  let tip = null, target = null, timer = 0, lastShown = 0, suppressed = null, follow = false;
  let px = 0, py = 0, watch = null, alive = 0;

  const motion = () => !document.documentElement.classList.contains('reduce-motion');

  function el() {
    if (tip) return tip;
    tip = document.createElement('div');
    tip.className = 'vt-tip';
    tip.setAttribute('popover', 'manual');   // oberste Ebene: auch über Popovern und Menüs
    tip.setAttribute('role', 'tooltip');
    tip.setAttribute('data-nolang', '');     // Text kommt schon übersetzt aus data-tip
    tip.innerHTML = '<span class="vt-tip-text"></span>';
    document.body.appendChild(tip);
    return tip;
  }

  // title -> data-tip; Knöpfe ohne Text behalten ihren Namen für Screenreader
  function convert(node) {
    const v = node.getAttribute('title');
    if (v === null) return;
    node.removeAttribute('title');
    if (v) node.setAttribute('data-tip', v); else node.removeAttribute('data-tip');
    if (v && /^(BUTTON|A)$/.test(node.tagName) && !node.hasAttribute('aria-label') && !node.textContent.trim()) {
      node.setAttribute('aria-label', v);
    }
  }

  const text = node => (node && node.getAttribute('data-tip')) || '';

  function place() {
    if (!tip || !target) return;
    const w = tip.offsetWidth, h = tip.offsetHeight;
    const r = target.getBoundingClientRect();
    let ax, top, below = true;
    if (follow) {
      ax = px;
      top = py + 20;
      if (top + h > innerHeight - GAP) { top = py - h - 12; below = false; }
    } else {
      ax = r.left + r.width / 2;
      top = r.bottom + GAP;
      if (top + h > innerHeight - GAP) { top = r.top - h - GAP; below = false; }
    }
    const left = Math.min(Math.max(GAP, ax - w / 2), innerWidth - w - GAP);
    tip.style.left = `${Math.round(left)}px`;
    tip.style.top = `${Math.round(Math.max(GAP, top))}px`;
    tip.style.setProperty('--ax', `${Math.round(Math.min(Math.max(12, ax - left), w - 12))}px`);
    tip.classList.toggle('above', !below);
  }

  function show() {
    const s = text(target);
    if (!target || !s || !target.isConnected) { hide(); return; }
    el();
    tip.querySelector('.vt-tip-text').textContent = s;
    const wasOpen = tip.matches(':popover-open');
    if (wasOpen) tip.hidePopover();          // neu öffnen = wieder ganz oben
    tip.showPopover();
    place();
    tip.getAnimations().forEach(a => a.cancel());
    if (motion() && !wasOpen) {
      const dy = tip.classList.contains('above') ? -4 : 4;
      tip.animate([{ opacity: 0, transform: `translateY(${dy}px) scale(.96)` }, { opacity: 1, transform: 'none' }],
                  { duration: 170, easing: 'cubic-bezier(.2, .85, .25, 1)' });
    }
    lastShown = performance.now();
    clearInterval(alive);
    alive = setInterval(() => { if (!target || !target.isConnected) hide(); }, 300);
  }

  function hide() {
    clearTimeout(timer);
    clearInterval(alive);
    if (watch) { watch.disconnect(); watch = null; }
    if (target && tip && tip.matches(':popover-open')) lastShown = performance.now();
    target = null;
    if (!tip || !tip.matches(':popover-open')) return;
    tip.getAnimations().forEach(a => a.cancel());
    if (!motion()) { tip.hidePopover(); return; }
    const a = tip.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 110, easing: 'ease-in' });
    a.onfinish = () => { if (!target && tip.matches(':popover-open')) tip.hidePopover(); };
  }

  function open(node, fromPointer) {
    if (node === target) return;
    hide();
    if (!node || node === suppressed) return;
    convert(node);
    if (!text(node)) return;
    target = node;
    const r = node.getBoundingClientRect();
    follow = fromPointer && (r.width > 320 || r.height > 90);
    // Text ändert sich, während der Hinweis offen ist (z. B. Zeitleiste): mitziehen
    watch = new MutationObserver(() => {
      if (!target) return;
      if (target.hasAttribute('title')) convert(target);
      if (!text(target)) { hide(); return; }
      if (tip && tip.matches(':popover-open')) {
        tip.querySelector('.vt-tip-text').textContent = text(target);
        place();
      }
    });
    watch.observe(node, { attributes: true, attributeFilter: ['title', 'data-tip'] });
    const quick = performance.now() - lastShown < 350;
    timer = setTimeout(show, quick ? QUICK : DELAY);
  }

  // Aufgeklappte Auswahlliste: Die Einträge liegen im select, dessen Hinweis würde sie verdecken
  const inOpenPicker = n => {
    if (n.closest?.('option, optgroup')) return true;
    const sel = n.closest?.('select');
    try { return !!sel && sel.matches(':open'); } catch (e) { return false; }
  };

  document.addEventListener('pointerover', e => {
    if (inOpenPicker(e.target)) { const s = e.target.closest('select'); if (s) { convert(s); suppressed = s; } hide(); return; }
    const node = e.target.closest?.('[title], [data-tip]');
    if (suppressed && !suppressed.contains(e.target)) suppressed = null;
    open(node, true);
  }, true);
  document.addEventListener('pointerout', e => {
    if (!target) return;
    const to = e.relatedTarget;
    if (!to || !target.contains(to)) {
      const next = to && to.closest?.('[title], [data-tip]');
      if (!next) hide();
    }
  }, true);
  document.addEventListener('pointermove', e => {
    px = e.clientX; py = e.clientY;
    if (follow && tip && target && tip.matches(':popover-open')) place();
  }, { capture: true, passive: true });
  // Klicken, Tippen, Scrollen: Hinweis weg und erst wieder, wenn die Maus das Element verlässt
  const dismiss = () => { if (target) suppressed = target; hide(); };
  document.addEventListener('pointerdown', dismiss, true);
  document.addEventListener('wheel', dismiss, { capture: true, passive: true });
  document.addEventListener('scroll', () => { if (target) hide(); }, true);
  document.addEventListener('keydown', e => { if (!['Shift', 'Control', 'Alt', 'Meta'].includes(e.key)) dismiss(); }, true);
  window.addEventListener('blur', () => hide());
  document.addEventListener('change', e => { if (e.target.tagName === 'SELECT') dismiss(); }, true);
  // Tastatur: Hinweis zum fokussierten Element
  document.addEventListener('focusin', e => {
    const node = e.target;
    if (node.matches?.(':focus-visible')) open(node.closest('[title], [data-tip]'), false);
  });
  document.addEventListener('focusout', () => { if (target && !follow) hide(); });
})();
