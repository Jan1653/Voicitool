/* Eigene Icons von Voicitool (keine Emojis): gezeichnet auf einem 24er-Raster, Linien 1,8 breit,
   abgerundet. Farbe kommt aus currentColor, passt sich also Hell/Dunkel und der Akzentfarbe an.
   Teile mit class="f" sind gefüllt. Nutzung: ic('name') liefert das SVG, <i data-ic="name"></i>
   in index.html wird beim Laden ersetzt. Für die Zeitleiste gibt es icPath('name') als Path2D. */
(function () {
  const I = {
    gear: '<path d="M18.95 10.14L21.49 10.58L21.49 13.42L18.95 13.86A7.2 7.2 0 0 1 18.24 15.6L19.72 17.71L17.71 19.72L15.6 18.24A7.2 7.2 0 0 1 13.86 18.95L13.42 21.49L10.58 21.49L10.14 18.95A7.2 7.2 0 0 1 8.4 18.24L6.29 19.72L4.28 17.71L5.76 15.6A7.2 7.2 0 0 1 5.05 13.86L2.51 13.42L2.51 10.58L5.05 10.14A7.2 7.2 0 0 1 5.76 8.4L4.28 6.29L6.29 4.28L8.4 5.76A7.2 7.2 0 0 1 10.14 5.05L10.58 2.51L13.42 2.51L13.86 5.05A7.2 7.2 0 0 1 15.6 5.76L17.71 4.28L19.72 6.29L18.24 8.4A7.2 7.2 0 0 1 18.95 10.14Z"/><circle cx="12" cy="12" r="3.1"/>',
    close: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    plus: '<path d="M12 5.5v13M5.5 12h13"/>',
    minus: '<path d="M5.5 12h13"/>',
    check: '<path d="M5 12.6l4.3 4.3L19 7.2"/>',
    'chevron-right': '<path d="M9.5 5.8l6.2 6.2-6.2 6.2"/>',
    'chevron-down': '<path d="M5.8 9.5l6.2 6.2 6.2-6.2"/>',
    'chevron-left': '<path d="M14.5 5.8L8.3 12l6.2 6.2"/>',
    play: '<path class="f" d="M8 5.9a1.2 1.2 0 0 1 1.8-1l9 5.7a1.6 1.6 0 0 1 0 2.8l-9 5.7A1.2 1.2 0 0 1 8 18.1z"/>',
    pause: '<rect class="f" x="6.3" y="5" width="4.2" height="14" rx="1.4"/><rect class="f" x="13.5" y="5" width="4.2" height="14" rx="1.4"/>',
    stop: '<rect class="f" x="6" y="6" width="12" height="12" rx="2.6"/>',
    pencil: '<path d="M15.2 4.9l3.9 3.9L8.8 19.1 4 20l.9-4.8z"/><path d="M13 7.1l3.9 3.9"/>',
    folder: '<path d="M3.5 7.3c0-1 .7-1.8 1.6-1.8h4.3l2 2.2h7.5c.9 0 1.6.7 1.6 1.6v8.6c0 1-.7 1.7-1.6 1.7H5.1c-.9 0-1.6-.7-1.6-1.7z"/>',
    'folder-plus': '<path d="M3.5 7.3c0-1 .7-1.8 1.6-1.8h4.3l2 2.2h7.5c.9 0 1.6.7 1.6 1.6v8.6c0 1-.7 1.7-1.6 1.7H5.1c-.9 0-1.6-.7-1.6-1.7z"/><path d="M12 10.8v5.6M9.2 13.6h5.6"/>',
    'folder-move': '<path d="M3.5 7.3c0-1 .7-1.8 1.6-1.8h4.3l2 2.2h7.5c.9 0 1.6.7 1.6 1.6v8.6c0 1-.7 1.7-1.6 1.7H5.1c-.9 0-1.6-.7-1.6-1.7z"/><path d="M8.8 13.6h6M12.9 11.5l2.1 2.1-2.1 2.1"/>',
    folders: '<path d="M7 8.3V6.8c0-.8.6-1.5 1.4-1.5h3.2l1.7 1.8h6c.8 0 1.4.6 1.4 1.4v6.6c0 .8-.6 1.4-1.4 1.4h-.8"/><path d="M3.3 10.5c0-.8.6-1.5 1.4-1.5h3.2l1.7 1.8h6c.8 0 1.4.6 1.4 1.4v6.3c0 .8-.6 1.5-1.4 1.5H4.7c-.8 0-1.4-.7-1.4-1.5z"/>',
    trash: '<path d="M4.5 7h15"/><path d="M9.4 7V5.3c0-.5.3-.8.8-.8h3.6c.5 0 .8.3.8.8V7"/><path d="M6.4 7l.9 11.5c.1.9.8 1.5 1.6 1.5h6.2c.8 0 1.5-.6 1.6-1.5L17.6 7"/><path d="M10.2 10.6v6M13.8 10.6v6"/>',
    share: '<path d="M12 14.4V4.2"/><path d="M8.2 7.9L12 4.1l3.8 3.8"/><path d="M8 10.5H6.3c-.9 0-1.7.8-1.7 1.7v6.1c0 .9.8 1.7 1.7 1.7h11.4c.9 0 1.7-.8 1.7-1.7v-6.1c0-.9-.8-1.7-1.7-1.7H16"/>',
    grip: '<circle class="f" cx="9" cy="6.5" r="1.45"/><circle class="f" cx="15" cy="6.5" r="1.45"/><circle class="f" cx="9" cy="12" r="1.45"/><circle class="f" cx="15" cy="12" r="1.45"/><circle class="f" cx="9" cy="17.5" r="1.45"/><circle class="f" cx="15" cy="17.5" r="1.45"/>',
    package: '<path d="M12 3.3l7.8 4.3v8.8L12 20.7l-7.8-4.3V7.6z"/><path d="M4.4 7.7L12 12l7.6-4.3M12 12v8.5"/><path d="M8.1 5.4l7.8 4.4"/>',
    clapper: '<path d="M4 10.2h16v8.1c0 .9-.7 1.7-1.6 1.7H5.6c-.9 0-1.6-.8-1.6-1.7z"/><path d="M3.6 7.4l14.8-3 .6 3.1-14.8 3z"/><path d="M7.9 6.5l2.3 2.6M12.3 5.6l2.3 2.6"/><path d="M4 13.5h16"/>',
    inbox: '<path d="M4 13.6L6.3 6.3c.2-.6.8-1 1.4-1h8.6c.6 0 1.2.4 1.4 1l2.3 7.3v4.7c0 1-.7 1.7-1.6 1.7H5.6c-.9 0-1.6-.7-1.6-1.7z"/><path d="M4 13.6h4.3l1.4 2.3h4.6l1.4-2.3H20"/>',
    warning: '<path d="M10.3 4.7c.8-1.3 2.6-1.3 3.4 0l7 12c.7 1.3-.2 2.9-1.7 2.9H5c-1.5 0-2.4-1.6-1.7-2.9z"/><path d="M12 9.4v4.1"/><circle class="f" cx="12" cy="16.4" r="1.05"/>',
    blocked: '<path d="M8.6 3.8h6.8l4.8 4.8v6.8l-4.8 4.8H8.6l-4.8-4.8V8.6z"/><path d="M8.4 12h7.2"/>',
    info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.2"/><circle class="f" cx="12" cy="7.9" r="1.05"/>',
    'check-circle': '<circle cx="12" cy="12" r="8.5"/><path d="M8.2 12.4l2.6 2.6 5-5.2"/>',
    help: '<circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.6a2.5 2.5 0 0 1 4.8 1c0 1.7-2.4 2.1-2.4 3.4"/><circle class="f" cx="12" cy="16.9" r="1.05"/>',
    gamepad: '<path d="M7.5 7.5h9c2.4 0 4.1 1.8 4.4 4.3l.5 4.2c.2 1.6-1.1 2.9-2.6 2.6-.8-.2-1.4-.7-1.8-1.4l-.9-1.7H7.9L7 17.2c-.4.7-1 1.2-1.8 1.4-1.5.3-2.8-1-2.6-2.6l.5-4.2c.3-2.5 2-4.3 4.4-4.3z"/><path d="M7.7 10.4v3.2M6.1 12h3.2"/><circle class="f" cx="15.6" cy="10.8" r="1"/><circle class="f" cx="17.5" cy="13" r="1"/>',
    user: '<circle cx="12" cy="8.3" r="3.8"/><path d="M4.8 19.6c.9-3.4 3.8-5.5 7.2-5.5s6.3 2.1 7.2 5.5"/>',
    merge: '<path d="M6 4c0 4.8 6 5.2 6 10.2v5.6"/><path d="M18 4c0 4.8-6 5.2-6 10.2"/><path d="M8.9 16.9l3.1 3.1 3.1-3.1"/>',
    pulse: '<path d="M3 12.5h4.2l2.1-5 3.6 10 2.5-5.6H21"/>',
    hourglass: '<path d="M6.8 3.8h10.4M6.8 20.2h10.4"/><path d="M8 3.8v2.7c0 1.5.8 2.9 2 3.7l1.3.8c.5.4.5 1.2 0 1.6L10 13.4c-1.2.8-2 2.2-2 3.7v3.1M16 3.8v2.7c0 1.5-.8 2.9-2 3.7l-1.3.8c-.5.4-.5 1.2 0 1.6l1.3.8c1.2.8 2 2.2 2 3.7v3.1"/>',
    palette: '<path d="M12 3.5c-4.8 0-8.5 3.6-8.5 8.2 0 4.6 3.8 8.8 8.2 8.8 1.3 0 2-.8 2-1.8 0-.5-.2-.9-.5-1.3-.4-.4-.5-.8-.5-1.3 0-.9.8-1.7 1.8-1.7H17c2.1 0 3.5-1.6 3.5-3.8 0-3.6-3.7-7.1-8.5-7.1z"/><circle class="f" cx="7.6" cy="11.4" r="1.15"/><circle class="f" cx="10" cy="7.7" r="1.15"/><circle class="f" cx="14.3" cy="7.7" r="1.15"/><circle class="f" cx="16.8" cy="11.1" r="1.15"/>',
    globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17"/><path d="M12 3.5c2.2 2.3 3.4 5.2 3.4 8.5s-1.2 6.2-3.4 8.5c-2.2-2.3-3.4-5.2-3.4-8.5s1.2-6.2 3.4-8.5z"/>',
    scissors: '<circle cx="6.6" cy="7" r="2.6"/><circle cx="6.6" cy="17" r="2.6"/><path d="M8.7 8.5l10.8 9M8.7 15.5l10.8-9"/>',
    sparkles: '<path d="M10 3.6l1.5 4.2c.3.8 1 1.5 1.8 1.8l4.2 1.5-4.2 1.5c-.8.3-1.5 1-1.8 1.8L10 18.6l-1.5-4.2c-.3-.8-1-1.5-1.8-1.8l-4.2-1.5 4.2-1.5c.8-.3 1.5-1 1.8-1.8z"/><path d="M18.2 3.2v3.6M16.4 5h3.6M18.4 15.6v3.4M16.7 17.3h3.4"/>',
    database: '<ellipse cx="12" cy="6.2" rx="7" ry="2.7"/><path d="M5 6.2v11.6c0 1.5 3.1 2.7 7 2.7s7-1.2 7-2.7V6.2"/><path d="M5 12c0 1.5 3.1 2.7 7 2.7s7-1.2 7-2.7"/>',
    monitor: '<rect x="3.4" y="4.4" width="17.2" height="11.6" rx="1.9"/><path d="M9 20h6M12 16v4"/>',
    refresh: '<path d="M19.3 12.4a7.4 7.4 0 1 1-2.2-5.7"/><path d="M19.6 4.6v4.1h-4.1"/>',
    keyboard: '<rect x="2.8" y="6" width="18.4" height="12" rx="2.1"/><path d="M6.6 9.6h.01M9.6 9.6h.01M12.6 9.6h.01M15.6 9.6h.01M17.4 12.4h.01M6.6 12.4h.01M9.6 12.4h.01M12.6 12.4h.01M15.2 12.4h.01M8 15.2h8"/>',
    undo: '<path d="M8.6 5.4L4.6 9.4l4 4"/><path d="M4.9 9.4h9.2c3 0 5.4 2.3 5.4 5.2S17.1 19.8 14.1 19.8h-3.6"/>',
    redo: '<path d="M15.4 5.4l4 4-4 4"/><path d="M19.1 9.4H9.9c-3 0-5.4 2.3-5.4 5.2S6.9 19.8 9.9 19.8h3.6"/>',
    music: '<path d="M9 17.4V5.9l10-2.3v11.5"/><path d="M9 9.6l10-2.3"/><ellipse cx="6.8" cy="17.4" rx="2.4" ry="2.2"/><ellipse cx="16.8" cy="15.1" rx="2.4" ry="2.2"/>',
    repeat: '<path d="M4.5 11.2V9.6c0-1.7 1.3-3.1 3-3.1h10.8"/><path d="M15.4 3.6l2.9 2.9-2.9 2.9"/><path d="M19.5 12.8v1.6c0 1.7-1.3 3.1-3 3.1H5.7"/><path d="M8.6 20.4l-2.9-2.9 2.9-2.9"/>',
    laugh: '<circle cx="12" cy="12" r="8.5"/><path d="M7.7 9.9c.5-.9 1.7-.9 2.2 0M14.1 9.9c.5-.9 1.7-.9 2.2 0"/><path d="M7.8 13.2h8.4c-.3 2.2-2.1 3.8-4.2 3.8s-3.9-1.6-4.2-3.8z"/>',
    camera: '<path d="M4 8.3c0-.9.7-1.6 1.6-1.6h2.3l1.4-2.1h5.4l1.4 2.1h2.3c.9 0 1.6.7 1.6 1.6v9.1c0 .9-.7 1.6-1.6 1.6H5.6c-.9 0-1.6-.7-1.6-1.6z"/><circle cx="12" cy="12.7" r="3.4"/>',
    sound: '<path d="M4 9.5h3.1l4.4-3.8v12.6l-4.4-3.8H4z"/><path d="M15.2 9.2c.8.8 1.2 1.8 1.2 2.8s-.4 2-1.2 2.8M17.9 6.5c1.5 1.5 2.3 3.4 2.3 5.5s-.8 4-2.3 5.5"/>',
    'to-start': '<path d="M5.2 5v14"/><path d="M19 12H8.6"/><path d="M12.4 8.2L8.6 12l3.8 3.8"/>',
    'to-end': '<path d="M18.8 5v14"/><path d="M5 12h10.4"/><path d="M11.6 8.2l3.8 3.8-3.8 3.8"/>',
    'zoom-in': '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5M10.5 8v5M8 10.5h5"/>',
    'zoom-out': '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5M8 10.5h5"/>',
    'arrow-left': '<path d="M19 12H5.5M11 6.5L5.5 12l5.5 5.5"/>',
    'arrow-right': '<path d="M5 12h13.5M13 6.5l5.5 5.5-5.5 5.5"/>',
    'arrow-up': '<path d="M12 19V5.5M6.5 11L12 5.5l5.5 5.5"/>',
    'arrow-down': '<path d="M12 5v13.5M6.5 13l5.5 5.5 5.5-5.5"/>',
    copy: '<rect x="8.6" y="8.6" width="11" height="11" rx="1.8"/><path d="M15.4 8.6V6.3c0-1-.8-1.8-1.8-1.8H6.3c-1 0-1.8.8-1.8 1.8v7.3c0 1 .8 1.8 1.8 1.8h2.3"/>',
    search: '<circle cx="10.6" cy="10.6" r="6.1"/><path d="M15.2 15.2l4.8 4.8"/>',
    retranscribe: '<path d="M4.5 7h8M4.5 11h6M4.5 15h4.5"/><path d="M19.6 13.4a4.6 4.6 0 1 1-1.4-3.4"/><path d="M19.8 7.9v2.6h-2.6"/>',
    image: '<rect x="3.6" y="4.6" width="16.8" height="14.8" rx="2"/><circle cx="9" cy="9.6" r="1.7"/><path d="M4 17l4.6-4.4 3.3 3 3.1-3.1 5.2 5.2"/>',
    'user-plus': '<circle cx="10" cy="8.3" r="3.6"/><path d="M3.8 19.6c.8-3.2 3.3-5.2 6.2-5.2 1.3 0 2.5.4 3.5 1"/><path d="M18 13.2v6M15 16.2h6"/>',
    'arrow-back': '<path d="M10.2 6.2L4.4 12l5.8 5.8"/><path d="M4.8 12h14.8"/>',
    download: '<path d="M12 4v10.4"/><path d="M8.2 10.8l3.8 3.8 3.8-3.8"/><path d="M4.6 16.6v1.7c0 1 .8 1.7 1.7 1.7h11.4c1 0 1.7-.7 1.7-1.7v-1.7"/>',
  };

  function ic(name, cls = '') {
    const body = I[name];
    if (!body) return '';
    return `<svg class="ic ic-${name}${cls ? ' ' + cls : ''}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${body}</svg>`;
  }
  /** Icons in HTML nachrüsten: <i data-ic="name"></i> wird zum SVG. */
  function hydrate(root = document) {
    root.querySelectorAll('i[data-ic]').forEach(el => {
      const tmp = document.createElement('span');
      tmp.innerHTML = ic(el.dataset.ic, el.className);
      if (tmp.firstChild) el.replaceWith(tmp.firstChild);
    });
  }
  /** Für Canvas: Liste von Path2D-Teilen ({path, fill}) eines Icons. */
  const cache = {};
  function icPaths(name) {
    if (cache[name]) return cache[name];
    const doc = new DOMParser().parseFromString(`<svg xmlns="http://www.w3.org/2000/svg">${I[name] || ''}</svg>`, 'image/svg+xml');
    const parts = [];
    doc.documentElement.querySelectorAll('*').forEach(el => {
      const fill = el.getAttribute('class') === 'f';
      let d = '';
      if (el.tagName === 'path') d = el.getAttribute('d');
      else if (el.tagName === 'circle') {
        const cx = +el.getAttribute('cx'), cy = +el.getAttribute('cy'), r = +el.getAttribute('r');
        d = `M${cx - r} ${cy}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0`;
      } else if (el.tagName === 'rect') {
        const x = +el.getAttribute('x'), y = +el.getAttribute('y'), w = +el.getAttribute('width'), h = +el.getAttribute('height');
        d = `M${x} ${y}h${w}v${h}h${-w}z`;
      }
      if (d) parts.push({ path: new Path2D(d), fill });
    });
    return (cache[name] = parts);
  }
  window.ICONS = I;
  window.ic = ic;
  window.icPaths = icPaths;
  window.VT_ICONS_HYDRATE = hydrate;
  document.addEventListener('DOMContentLoaded', () => hydrate());
})();
