"""voicigame: Spiel-Mod für The Choicer Voicer (Freunde spielen am Handy oder im Browser mit).

Voicitool installiert, aktualisiert und entfernt ihn. Sichtbar nur, wenn config.voicigame_enabled()
an ist oder der Mod schon installiert ist. Sonst ändert sich für niemanden etwas.

  app/voicigame_mod/           mitgelieferte Mod-Dateien (kommen mit jedem Voicitool-Update,
                               erneuern mit app/setup/voicigame_mod.py)
  daten/voicigame/             installierte Kopie, auf die das Spiel zeigt; beim Start still aktualisiert
  daten/voicigame_sicherung/   override.cfg des Spiels vor jeder Änderung
  <Spielordner>/override.cfg   [autoload] Voicigame="*<…>/daten/voicigame/main.gd"

Das Spiel liest override.cfg neben seiner exe beim Start. Andere Einträge darin bleiben unverändert.
Der Spielordner ist nicht der Nutzerdatenordner (%APPDATA%\\YeahMaybe\\ChoicerVoicer): Er wird gesucht
(Steam-Bibliotheken, itch, übliche Ordner) oder vom Nutzer gewählt und in den Einstellungen gemerkt
(voicigame_game_dir). voicigame_dirs merkt sich alle Spielordner, in die Voicitool den Eintrag geschrieben
hat, damit Entfernen auch dort aufräumt.

Aufruf bei der Deinstallation von Voicitool: python app\\voicigame.py --entfernen
(nimmt nur die eigenen Einträge aus override.cfg, die Dateien löscht die Deinstallation selbst).
"""
import filecmp
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402

KEY = "Voicigame"
SECTION = "autoload"
CFG_NAME = "override.cfg"
BUNDLED = config.APP_DIR / "voicigame_mod"
MOD_DIR = config.DATA_DIR / "voicigame"
BACKUP_DIR = config.DATA_DIR / "voicigame_sicherung"
KEEP_BACKUPS = 20
SEARCH_SECONDS = 3.0
_lock = threading.RLock()


class VoicigameError(Exception):
    """Meldung für die Oberfläche (deutsch, die Oberfläche übersetzt sie)."""


def _trash(path):
    from send2trash import send2trash
    send2trash(str(path))


def _norm(p):
    return os.path.normcase(os.path.normpath(str(p)))


def _same_path(a, b):
    return bool(a) and bool(b) and _norm(a) == _norm(b)


def _settings():
    return config.user_settings()


def _save(values):
    from app import models
    models.save_settings(values)


# ------------------------------------------------------------------ Mod-Dateien
def _is_mod_file(p):
    return p.is_file() and (p.suffix.lower() == ".gd" or p.name.lower() == "lang.json")


def mod_files(folder):
    try:
        return {p.name: p for p in sorted(Path(folder).iterdir()) if _is_mod_file(p)}
    except OSError:
        return {}


def read_version(folder):
    """VERSION aus main.gd (const VERSION := "0.1.0"), sonst None."""
    try:
        text = (Path(folder) / "main.gd").read_text(encoding="utf8", errors="replace")
    except OSError:
        return None
    m = re.search(r'^const\s+VERSION\s*(?::\s*String\s*)?:?=\s*"([^"]*)"', text, re.M)
    return m.group(1) if m else None


def installed_copy():
    return (MOD_DIR / "main.gd").is_file()


def up_to_date():
    """Gleicht die installierte Kopie dem mitgelieferten Stand?"""
    src, dst = mod_files(BUNDLED), mod_files(MOD_DIR)
    if not src or set(src) != set(dst):
        return False
    return all(filecmp.cmp(src[n], dst[n], shallow=False) for n in src)


def sync_copy():
    """Installierte Kopie auf den mitgelieferten Stand bringen. Ergebnis: Anzahl geänderter Dateien."""
    src = mod_files(BUNDLED)
    if "main.gd" not in src:
        raise VoicigameError("Die Mod-Dateien fehlen in Voicitool. Bitte unter Über Voicitool die Installation prüfen.")
    MOD_DIR.mkdir(parents=True, exist_ok=True)
    changed = 0
    for name, p in src.items():
        dst = MOD_DIR / name
        if dst.exists() and filecmp.cmp(p, dst, shallow=False):
            continue
        tmp = dst.with_name(dst.name + ".tmp")
        shutil.copyfile(p, tmp)
        os.replace(tmp, dst)
        changed += 1
    for name, p in mod_files(MOD_DIR).items():
        if name not in src:   # gibt es im neuen Stand nicht mehr
            _trash(p)
            changed += 1
    return changed


def write_export_cfg():
    """voicitool.cfg neben den Mod: dorthin legt der Mod exportierte Dub-Videos zusätzlich zu Videos\\Voicigame.
    Godot-ConfigFile, Pfad mit / (Windows-Pfade enthalten keine Anführungszeichen)."""
    if not MOD_DIR.is_dir():
        return
    text = '[export]\n\ndir="%s"\n' % config.EXPORT_VIDEOS.as_posix()
    p = MOD_DIR / "voicitool.cfg"
    try:
        if not p.is_file() or p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8")
    except OSError:
        pass   # Videos landen dann nur unter Videos/Voicigame


def startup_sync():
    """Beim Start (also auch nach jedem Voicitool-Update): ist der Mod installiert, still nachziehen."""
    if not installed_copy() or not (BUNDLED / "main.gd").is_file():
        return 0
    with _lock:
        write_export_cfg()
        return 0 if up_to_date() else sync_copy()


# ------------------------------------------------------------------ Spielordner finden
_SKIP = {"windows", "programdata", "appdata", "node_modules", "__pycache__", "site-packages", "venv", "recovery",
         "perflogs", "msocache", "config.msi", "winsxs", "system volume information", "steamapps"}


def is_game_exe(name):
    """The Choicer Voicer.exe und Varianten (z. B. TheChoicerVoicer_0-5-3.exe), nicht die .console.exe."""
    n = name.lower()
    if not n.endswith(".exe") or n.endswith(".console.exe"):
        return False
    return "choicervoicer" in re.sub(r"[^a-z]", "", n[:-4])


def _exe_names(folder):
    try:
        with os.scandir(folder) as it:
            return sorted(e.name for e in it if e.is_file() and e.name.lower().endswith(".exe")
                          and not e.name.lower().endswith(".console.exe"))
    except OSError:
        return []


def game_exe(folder):
    """Die Spiel-exe im Ordner. Heißt sie anders (umbenannt, selbst gewählt), die erste andere exe."""
    names = _exe_names(folder)
    for n in names:
        if n == "The Choicer Voicer.exe":
            return n
    for n in names:
        if is_game_exe(n):
            return n
    return names[0] if names else None


def _registry(hive, key, value):
    try:
        import winreg
        with winreg.OpenKey(hive, key) as k:
            return winreg.QueryValueEx(k, value)[0]
    except Exception:
        return None


def _steam_libraries():
    """steamapps/common aller Steam-Bibliotheken (Steam-Ordner und libraryfolders.vdf)."""
    try:
        import winreg
        roots = [_registry(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                 _registry(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                 _registry(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")]
    except ImportError:
        roots = []
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(env):
            roots.append(os.path.join(os.environ[env], "Steam"))
    libs = []
    for r in filter(None, roots):
        libs.append(r)
        try:
            text = (Path(r) / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf8", errors="replace")
        except OSError:
            continue
        libs += [m.group(1).replace("\\\\", "\\") for m in re.finditer(r'"path"\s+"((?:[^"\\]|\\.)*)"', text)]
    out = {}
    for lib in libs:
        out.setdefault(_norm(lib), Path(lib) / "steamapps" / "common")
    return list(out.values())


def _user_folders():
    """Desktop, Downloads, Dokumente, Bilder, Videos (auch umgeleitet, z. B. in OneDrive) und Spiele-Ordner."""
    out = []
    try:
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        for name in ("Desktop", "{374DE290-123F-4565-9164-39C4925E467B}", "Personal", "My Pictures", "My Video"):
            v = _registry(winreg.HKEY_CURRENT_USER, key, name)
            if v:
                out.append(os.path.expandvars(v))
    except ImportError:
        pass
    home = Path.home()
    out += [home / n for n in ("Desktop", "Downloads", "Documents", "Pictures", "Videos", "Games", "Spiele")]
    return out


def _fixed_drives():
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        mask = k32.GetLogicalDrives()
        drives = [f"{chr(65 + i)}:\\" for i in range(26) if mask >> i & 1]
        return [d for d in drives if k32.GetDriveTypeW(d) == 3]   # DRIVE_FIXED: keine Netz- und Wechsellaufwerke
    except Exception:
        return []


def _search_roots():
    """(Ordner, Tiefe) in der Reihenfolge, in der gesucht wird."""
    roots = [(p, 1) for p in _steam_libraries()]
    if os.environ.get("APPDATA"):
        roots.append((Path(os.environ["APPDATA"]) / "itch" / "apps", 2))
    roots += [(p, 2) for p in _user_folders()]
    for env in ("ProgramFiles", "ProgramFiles(x86)"):
        if os.environ.get(env):
            roots.append((Path(os.environ[env]), 1))
    if os.environ.get("LOCALAPPDATA"):
        roots.append((Path(os.environ["LOCALAPPDATA"]) / "Programs", 1))
    for d in _fixed_drives():
        roots += [(Path(d), 1), (Path(d) / "Games", 1), (Path(d) / "Spiele", 1)]
    return roots


def _scan(root, depth, add, budget, deadline):
    if budget[0] <= 0 or time.monotonic() > deadline:
        return
    budget[0] -= 1
    try:
        with os.scandir(root) as it:
            entries = list(it)
    except OSError:
        return
    subdirs = []
    for e in entries:
        try:
            if e.is_file():
                if is_game_exe(e.name):
                    add(root)
            elif depth > 0 and e.is_dir() and not e.name.startswith((".", "$")) and e.name.lower() not in _SKIP:
                subdirs.append(e.path)
        except OSError:
            pass
    for d in subdirs:
        _scan(d, depth - 1, add, budget, deadline)


_found = {"time": 0.0, "dirs": []}


def find_game_dirs(refresh=False):
    """Ordner mit der Spiel-exe an bekannten Orten (höchstens ein paar Sekunden, Ergebnis 2 min gemerkt)."""
    with _lock:
        if not refresh and time.time() - _found["time"] < 120:
            return list(_found["dirs"])
        out, seen = [], set()
        deadline = time.monotonic() + SEARCH_SECONDS
        budget = [5000]

        def add(folder):
            k = _norm(folder)
            if k not in seen:
                seen.add(k)
                out.append(Path(os.path.normpath(str(folder))))

        done = set()
        for root, depth in _search_roots():
            key = (_norm(root), depth)
            if key in done or not os.path.isdir(root):
                continue
            done.add(key)
            _scan(root, depth, add, budget, deadline)
        out.sort(key=_exe_time, reverse=True)   # mehrere Fassungen: die zuletzt geänderte zuerst
        _found.update(time=time.time(), dirs=out)
        return list(out)


def _exe_time(folder):
    try:
        return (Path(folder) / game_exe(folder)).stat().st_mtime
    except (OSError, TypeError):
        return 0.0


def game_dir(refresh=False):
    """(Spielordner, Herkunft): gemerkt ('saved'), gefunden ('found') oder (None, None)."""
    saved = str(_settings().get("voicigame_game_dir") or "").strip()
    if saved:
        return Path(saved), "saved"
    found = find_game_dirs(refresh)
    return (found[0], "found") if found else (None, None)


def set_game_dir(raw):
    """Spielordner merken. raw: Ordner oder die exe darin (auch mit Anführungszeichen eingefügt)."""
    s = str(raw or "").strip().strip('"').strip()
    p = Path(os.path.expandvars(s)).expanduser() if s else None
    if p is None or not p.is_absolute():
        raise VoicigameError("Bitte einen vollständigen Ordnerpfad angeben.")
    if p.is_file():
        if p.suffix.lower() != ".exe":
            raise VoicigameError("Bitte die exe des Spiels oder ihren Ordner wählen.")
        folder = p.parent
    elif p.is_dir():
        folder = p
        if not any(is_game_exe(n) for n in _exe_names(folder)):
            raise VoicigameError(f"In diesem Ordner liegt keine Spiel-exe: {folder}")
    else:
        raise VoicigameError(f"Diesen Ordner gibt es nicht: {p}")
    folder = Path(os.path.normpath(str(folder)))
    _save({"voicigame_game_dir": str(folder)})
    return folder


# ------------------------------------------------------------------ override.cfg
# Format wie Godots ConfigFile: [abschnitt], schluessel=wert, Kommentare mit ;. Werte können über mehrere
# Zeilen gehen (Listen, Wörterbücher). Nur der Eintrag Voicigame in [autoload] wird angefasst, alles andere
# bleibt Zeichen für Zeichen erhalten (auch Zeilenenden und BOM).
_SECTION_RX = re.compile(r"^\s*\[([^\]]*)\]\s*(;.*)?$")
_KEY_RX = re.compile(r"^\s*([^=;\[\s][^=]*?)\s*=(.*)$")


def _split(text, default_nl="\r\n"):
    bom = text.startswith("\ufeff")
    if bom:
        text = text[1:]
    nl = "\r\n" if "\r\n" in text else "\n" if "\n" in text else default_nl
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines, nl, bom


def _join(lines, nl, bom):
    return ("\ufeff" if bom else "") + "".join(line + nl for line in lines)


def _depth(s):
    """Offene Klammern außerhalb von Zeichenketten (für Werte über mehrere Zeilen)."""
    d, quoted, escaped = 0, False, False
    for ch in s:
        if quoted:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = False
        elif ch == '"':
            quoted = True
        elif ch == ";":
            break
        elif ch in "[{(":
            d += 1
        elif ch in "]})":
            d -= 1
    return d


def _parse(lines):
    """Einträge: {kind: section|key|other, start, end (exklusiv), section, key, value}."""
    items, section, i = [], "", 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        m = None if not s or s[0] in ";#" else _SECTION_RX.match(line)
        if m:
            section = m.group(1).strip()
            items.append({"kind": "section", "start": i, "end": i + 1, "section": section})
            i += 1
            continue
        k = None if not s or s[0] in ";#" else _KEY_RX.match(line)
        if not k:
            items.append({"kind": "other", "start": i, "end": i + 1, "section": section})
            i += 1
            continue
        depth, j = _depth(k.group(2)), i + 1
        while depth > 0 and j < len(lines):
            depth += _depth(lines[j])
            j += 1
        items.append({"kind": "key", "start": i, "end": j, "section": section, "key": k.group(1).strip(),
                      "value": k.group(2).strip()})
        i = j
    return items


def _ours(items):
    return [it for it in items if it["kind"] == "key" and it["section"] == SECTION and it["key"] == KEY]


def _entry_path(value):
    """'"*C:/x/main.gd"' -> 'C:/x/main.gd'"""
    v = value.strip()
    if v.startswith('"'):
        end = v.find('"', 1)
        while end > 0 and v[end - 1] == "\\":
            end = v.find('"', end + 1)
        v = v[1:end] if end > 0 else v[1:]
        v = v.replace("\\\\", "\\")
    return v.lstrip("*").strip()


def entry_line(main_gd):
    return f'{KEY}="*{Path(main_gd).as_posix()}"'


def cfg_entry(text):
    """Pfad aus dem Eintrag Voicigame in [autoload], sonst None."""
    found = _ours(_parse(_split(text or "")[0]))
    return _entry_path(found[0]["value"]) if found else None


def cfg_with_entry(text, main_gd):
    """override.cfg mit unserem Eintrag: vorhandenen ersetzen, sonst in [autoload] ergänzen, sonst anhängen."""
    lines, nl, bom = _split(text or "")
    entry = entry_line(main_gd)
    items = _parse(lines)
    ours = _ours(items)
    if ours:
        for it in reversed(ours[1:]):   # doppelte Einträge: nur einer bleibt
            del lines[it["start"]:it["end"]]
        lines[ours[0]["start"]:ours[0]["end"]] = [entry]
        return _join(lines, nl, bom)
    heads = [it["start"] for it in items if it["kind"] == "section" and it["section"] == SECTION]
    if heads:
        h = heads[0]
        nxt = next((it["start"] for it in items if it["kind"] == "section" and it["start"] > h), len(lines))
        filled = [k for k in range(h + 1, nxt) if lines[k].strip()]
        if filled:
            lines.insert(filled[-1] + 1, entry)   # ans Ende des Abschnitts
        elif h + 1 < nxt:
            lines.insert(h + 2, entry)            # leerer Abschnitt: nach der Leerzeile unter der Überschrift
        else:
            lines[h + 1:h + 1] = ["", entry]
        return _join(lines, nl, bom)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines:
        lines.append("")
    lines += [f"[{SECTION}]", "", entry]
    return _join(lines, nl, bom)


def cfg_without_entry(text, only=None):
    """Eintrag Voicigame aus [autoload] entfernen -> (neuer Text, Anzahl).
    only: nur Einträge, die auf diese main.gd zeigen. Bleibt [autoload] leer, geht die Überschrift mit."""
    lines, nl, bom = _split(text or "")
    ours = [it for it in _ours(_parse(lines)) if only is None or _same_path(_entry_path(it["value"]), only)]
    if not ours:
        return text, 0
    for it in reversed(ours):
        del lines[it["start"]:it["end"]]
    items = _parse(lines)
    heads = [it["start"] for it in items if it["kind"] == "section" and it["section"] == SECTION]
    for h in reversed(heads):
        nxt = next((it["start"] for it in items if it["kind"] == "section" and it["start"] > h), len(lines))
        if any(lines[k].strip() for k in range(h + 1, nxt)):
            continue
        start = h
        while start > 0 and not lines[start - 1].strip():
            start -= 1
        del lines[start:nxt]
        if 0 < start < len(lines):
            lines.insert(start, "")   # Leerzeile zwischen den Nachbarn behalten
    while lines and not lines[-1].strip():
        lines.pop()
    return _join(lines, nl, bom), len(ours)


def cfg_is_empty(text):
    """Nichts mehr drin außer Leerzeilen und leeren Abschnitten? (Kommentare zählen als Inhalt.)"""
    lines = _split(text or "")[0]
    return all(it["kind"] == "section" or not lines[it["start"]].strip() for it in _parse(lines))


def _read_cfg(folder):
    """(Text, Rohdaten) der override.cfg, ("", None) wenn es keine gibt."""
    try:
        data = (Path(folder) / CFG_NAME).read_bytes()
    except FileNotFoundError:
        return "", None
    except OSError as e:
        raise VoicigameError(f"override.cfg lässt sich nicht lesen: {e}")
    return data.decode("utf8", "surrogateescape"), data


def _backup(folder, data):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^\w.-]+", "_", Path(folder).name)[:40] or "spiel"
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    target, i = BACKUP_DIR / f"override_{name}_{stamp}.cfg", 2
    while target.exists():
        target, i = BACKUP_DIR / f"override_{name}_{stamp}_{i}.cfg", i + 1
    target.write_bytes(data)
    old = sorted(BACKUP_DIR.glob("override_*.cfg"), key=lambda p: p.stat().st_mtime, reverse=True)[KEEP_BACKUPS:]
    for p in old:
        try:
            _trash(p)
        except Exception:
            pass
    return target


def _write_cfg(folder, text, old_data):
    """Sicherung der alten Datei, dann über eine Zwischendatei ersetzen (nie halb geschrieben)."""
    folder = Path(folder)
    if old_data is not None:
        _backup(folder, old_data)
    tmp = folder / (CFG_NAME + ".voicitool.tmp")
    try:
        tmp.write_bytes(text.encode("utf8", "surrogateescape"))
        os.replace(tmp, folder / CFG_NAME)
    except PermissionError:
        if tmp.exists():
            tmp.unlink()   # eigene Zwischendatei
        raise VoicigameError(f"Keine Schreibrechte im Spielordner: {folder}")


# ------------------------------------------------------------------ Installieren, Entfernen, Status
def _recorded():
    return [Path(p) for p in (_settings().get("voicigame_dirs") or []) if isinstance(p, str) and p]


def install(folder=None):
    """Mod-Kopie auf den neuen Stand bringen und im Spielordner eintragen (auch zum Aktualisieren)."""
    with _lock:
        folder = Path(folder) if folder else game_dir()[0]
        if folder is None:
            raise VoicigameError("Bitte zuerst den Spielordner wählen.")
        if not game_exe(folder):
            raise VoicigameError(f"In diesem Ordner liegt keine Spiel-exe: {folder}")
        sync_copy()
        write_export_cfg()
        text, data = _read_cfg(folder)
        new = cfg_with_entry(text, MOD_DIR / "main.gd")
        if data is None or new != text:
            _write_cfg(folder, new, data)
        dirs = _recorded()
        if not any(_same_path(d, folder) for d in dirs):
            dirs.append(folder)
        _save({"voicigame_game_dir": str(folder), "voicigame_dirs": [str(d) for d in dirs]})
        return status()


def remove(keep_files=False, only_ours=False):
    """Eintrag aus override.cfg nehmen (leere Datei in den Papierkorb), Mod-Kopie in den Papierkorb.
    Im aktuellen Spielordner jeden Voicigame-Eintrag, in früher benutzten nur den eigenen.
    keep_files/only_ours: für die Deinstallation von Voicitool."""
    with _lock:
        main = MOD_DIR / "main.gd"
        current = game_dir()[0]
        folders = []
        for f in _recorded() + ([current] if current else []):
            if not any(_same_path(f, x) for x in folders):
                folders.append(f)
        changed = []
        for folder in folders:
            try:
                text, data = _read_cfg(folder)
            except VoicigameError:
                if _same_path(folder, current):
                    raise
                continue   # alter Spielordner nicht mehr lesbar (z. B. Laufwerk weg)
            if data is None:
                continue
            only = None if _same_path(folder, current) and not only_ours else main
            new, n = cfg_without_entry(text, only)
            if not n:
                continue
            if cfg_is_empty(new):
                _backup(folder, data)
                _trash(Path(folder) / CFG_NAME)
            else:
                _write_cfg(folder, new, data)
            changed.append(str(folder))
        if not keep_files and MOD_DIR.exists():
            _trash(MOD_DIR)
        _save({"voicigame_dirs": []})
        return changed


def visible():
    """Abschnitt zeigen? Mit dem Schalter, oder wenn der Mod schon installiert ist (dann auch entfernbar)."""
    return config.voicigame_enabled() or installed_copy() or bool(_recorded())


def status(refresh=False):
    folder, source = game_dir(refresh)
    exe = game_exe(folder) if folder and folder.is_dir() else None
    entry = None
    if exe:
        try:
            entry = cfg_entry(_read_cfg(folder)[0])
        except VoicigameError:
            entry = None
    copy = installed_copy()
    ours = entry is not None and _same_path(entry, MOD_DIR / "main.gd")
    installed = ours and copy
    other = entry if entry is not None and not ours else ""
    return {
        "visible": True,
        "enabled": config.voicigame_enabled(),
        "bundled": (BUNDLED / "main.gd").is_file(),
        "version": read_version(BUNDLED),
        "installed_version": read_version(MOD_DIR) if copy else None,
        "game_dir": str(folder) if folder else "",
        "source": source,
        "exe": exe or "",
        "found": [str(p) for p in find_game_dirs()],   # „Auch gefunden“: weitere Spielordner zur Auswahl
        "installed": installed,
        "missing_files": ours and not copy,
        "other_entry": other,
        "up_to_date": installed and up_to_date(),
        "removable": bool(installed or ours or other or copy or _recorded()),
        "mod_dir": str(MOD_DIR),
    }


if __name__ == "__main__":
    if "--entfernen" in sys.argv:
        try:
            done = remove(keep_files=True, only_ours=True)
            print("Voicigame-Eintrag entfernt:", ", ".join(done) if done else "nichts zu tun")
        except Exception as e:  # noqa: BLE001
            print("Voicigame:", e)
