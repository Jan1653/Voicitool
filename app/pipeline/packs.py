"""Packs aus dem Spiel: fertige Packs importieren und bearbeiten, Aufnahmen als fertiges Video zusammenbauen.

Ordner des Spiels (…\\YeahMaybe\\ChoicerVoicer\\game):
  packs_voice\\<Pack>\\          _pack_info.ini, dub_video.ogv, _backing_track.ogg, _icon.jpg und je Zeile
                               NNN_Name.ini (caption, image, dub_timestamps, dub_characters), NNN_Name.ogg, .jpg
  recordings\\dub_recordings\\<Pack>\\<Zeitpunkt>\\_dubrecord_NNN_Name.wav     (allein aufgenommen)
  multiplayer_takes\\<Kennung>\\<Sitzung>\\<Clipname>.wav                      (zusammen aufgenommen)

Beides braucht keine KI: Import und Zusammenbau laufen mit ffmpeg und ein paar Rechnungen, also auch auf
schwachen PCs in Sekunden.
"""
import json
import re
import shutil
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from app import config
from app.pipeline import media, project

SR = 44100
TAKE_PREFIX = "_dubrecord_"


def game_root():
    """Ordner des Spiels (eine Ebene über den Packs)."""
    return config.game_packs_dir().parent


def _split_list(text):
    out, cur, quote = [], "", None
    for ch in text:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote, cur = ch, cur + ch
        elif ch == ",":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return out


def _value(v):
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [_value(x) for x in _split_list(inner)] if inner else []
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if re.fullmatch(r"-?\d+(\.\d+)?", v):
        return float(v) if "." in v else int(v)
    return v


def read_ini(path):
    """Die einfachen .ini-Dateien des Spiels lesen (Abschnitte werden nicht gebraucht)."""
    out = {}
    try:
        text = Path(path).read_text(encoding="utf8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith((";", "#", "[")):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = _value(v)
    return out


def _as_list(v):
    return v if isinstance(v, list) else ([] if v in (None, "") else [v])


def read_pack(path):
    """Ein Pack einlesen. -> {"title", "authors", "icon", "video", "backing", "lines": [...]}"""
    path = Path(path)
    info = read_ini(path / "_pack_info.ini")
    lines = []
    for ini in sorted(path.glob("*.ini")):
        if ini.name.startswith("_"):
            continue
        d = read_ini(ini)
        times = [float(t) for t in _as_list(d.get("dub_timestamps")) if isinstance(t, (int, float))]
        audio = next((ini.with_suffix(e) for e in (".ogg", ".wav", ".mp3") if ini.with_suffix(e).exists()), None)
        lines.append({"base": ini.stem, "text": str(d.get("caption") or ""), "image": d.get("image"),
                      "times": sorted(times), "chars": [str(c) for c in _as_list(d.get("dub_characters"))],
                      "audio": audio})
    video = next((path / n for n in ("dub_video.ogv", "dub_video.mp4", "dub_video.webm") if (path / n).exists()), None)
    backing = next((path / f"_backing_track{e}" for e in (".ogg", ".wav", ".mp3") if (path / f"_backing_track{e}").exists()), None)
    icon = next((path / f"_icon{e}" for e in (".jpg", ".png", ".jpeg") if (path / f"_icon{e}").exists()), None)
    return {"title": str(info.get("title") or path.name), "authors": [str(a) for a in _as_list(info.get("authors"))],
            "icon": icon, "video": video, "backing": backing, "lines": lines, "path": path}


def list_packs():
    """Alle Packs im Spiel mit Kurzinfo (für die Auswahl in der Oberfläche)."""
    root = config.game_packs_dir()
    out = []
    if not root.exists():
        return out
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        p = read_pack(d)
        if not p["lines"]:
            continue
        out.append({"name": d.name, "title": p["title"], "lines": len(p["lines"]), "video": bool(p["video"]),
                    "authors": p["authors"], "modified": d.stat().st_mtime})
    return out


# ------------------------------------------------------------------ Importieren
def _read_audio(path, frames=None, channels=2):
    """Datei als 44,1-kHz-Stereo lesen (über ffmpeg, damit jedes Format geht)."""
    tmp = config.DATA_DIR / "tmp" / f"pack_{abs(hash(str(path)))}.wav"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    media.run([media.FFMPEG, "-y", "-v", "error", "-i", str(path), "-ar", str(SR), "-ac", str(channels),
               "-c:a", "pcm_f32le", str(tmp)])
    x, _ = sf.read(str(tmp), dtype="float32", always_2d=True)
    tmp.unlink(missing_ok=True)
    if frames is not None:
        if len(x) < frames:
            x = np.vstack([x, np.zeros((frames - len(x), x.shape[1]), dtype=np.float32)])
        x = x[:frames]
    return x


def _mix_into(buf, clip, at):
    """Clip an der Stelle at (Sekunden) dazumischen, ohne über das Ende hinaus."""
    s = int(round(at * SR))
    if s >= len(buf) or s + len(clip) <= 0:
        return
    a = max(0, s)
    part = clip[max(0, -s):len(buf) - s]
    buf[a:a + len(part)] += part


def import_pack(pack_path, name=None, report=None, category=None, ui_lang=None):
    """Fertiges Pack als Projekt anlegen: Video, Zeilen, Figuren, Stimmen- und Hintergrund-Spur. -> Projekt-Kennung"""
    say = report or (lambda step, pct, msg="": None)
    pack = read_pack(pack_path)
    if not pack["video"]:
        raise RuntimeError("In diesem Pack fehlt das Video (dub_video.ogv).")
    if not pack["lines"]:
        raise RuntimeError("In diesem Pack sind keine Zeilen.")

    say("Pack lesen", 0.05, pack["title"])
    pid = project._free_id((name or pack["title"]).strip())
    d = config.PROJECTS_DIR / pid
    d.mkdir(parents=True)
    src = d / ("quelle" + pack["video"].suffix.lower())
    shutil.copy(pack["video"], src)

    say("Video lesen", 0.15, "Video prüfen")
    info = media.probe(src)
    frames = int(round((info["duration"] or 0) * SR))
    media.extract_audio(src, d / "audio.wav")

    say("Ton zusammensetzen", 0.3, "Hintergrund und Stimmen trennen")
    back = _read_audio(pack["backing"], frames) if pack["backing"] else np.zeros((frames, 2), dtype=np.float32)
    voc = np.zeros((frames, 2), dtype=np.float32)
    for i, ln in enumerate(pack["lines"]):
        if ln["audio"] and ln["times"]:
            clip = _read_audio(ln["audio"])
            for t in ln["times"]:
                _mix_into(voc, clip, t)
            ln["dur"] = len(clip) / SR
        else:
            ln["dur"] = 1.0
        if i % 10 == 0:
            say("Ton zusammensetzen", 0.3 + 0.3 * i / max(1, len(pack["lines"])), "Clips einsetzen")
    peak = float(np.abs(voc).max())
    if peak > 0.99:
        voc *= 0.99 / peak
    sf.write(str(d / "stimmen.wav"), voc, SR, subtype="PCM_16")
    sf.write(str(d / "hintergrund.wav"), np.clip(back, -1, 1), SR, subtype="PCM_16")
    media.encode_opus(d / "stimmen.wav", d / "stimmen.ogg")
    media.encode_opus(d / "hintergrund.wav", d / "hintergrund.ogg")

    say("Zeilen übernehmen", 0.65, f"{len(pack['lines'])} Zeilen")
    names, chars = [], []
    for ln in pack["lines"]:
        for c in ln["chars"]:
            if c and c not in names:
                names.append(c)
    if not names:
        names = ["Sprecher 1"]
    for k, nm in enumerate(names):
        chars.append({"id": f"c{k + 1}", "name": nm, "color": config.CHARACTER_COLORS[k % len(config.CHARACTER_COLORS)],
                      "image": None})
    by_name = {c["name"]: c["id"] for c in chars}
    lines = []
    for ln in pack["lines"]:
        ids = [by_name[c] for c in ln["chars"] if c in by_name] or [chars[0]["id"]]
        master = None
        for t in ln["times"] or []:
            line = {"id": project.new_id(),
                    "start": round(float(t), 3), "end": round(float(t) + float(ln.get("dur") or 1.0), 3),
                    "text": ln["text"], "chars": list(ids)}
            if master:   # weitere Zeitpunkte derselben Aufnahme: als Wiederholung verknüpfen
                line["repeat_of"] = master
            else:
                master = line["id"]
            lines.append(line)
    lines.sort(key=lambda l: l["start"])

    say("Wellenform", 0.8, "Wellenform berechnen")
    from app.pipeline import segment
    voc16 = media.load_mono(d / "stimmen.wav")
    np.save(d / "stimmen_env.npy", segment.envelope_db(voc16).astype(np.float32))
    (d / "wellenform.json").write_text(json.dumps({"stimmen": segment.waveform_peaks(voc16), "fps": 100}), encoding="utf8")

    icon_name = None
    if pack["icon"]:
        (d / "bilder").mkdir(exist_ok=True)
        icon_name = "icon" + pack["icon"].suffix.lower()
        shutil.copy(pack["icon"], d / "bilder" / icon_name)

    data = {
        "version": 1, "name": pid, "created": time.strftime("%Y-%m-%d %H:%M"), "source": src.name, "preview": None,
        "status": "fertig", "error": None,
        "settings": {"language": "auto", "speakers": None, "quality": config.DEFAULT_QUALITY, "laugh": False,
                     "ui_lang": ui_lang if ui_lang in project.SPEAKER_WORD else "en"},
        "language": None, "duration": info["duration"], "width": info["width"], "height": info["height"],
        "fps": info["fps"], "characters": chars, "lines": lines,
        "pack": {"author": ", ".join(pack["authors"]), "subtitle": "", "readme": "", "icon": icon_name},
        "credits": dict(config.DEFAULT_CREDITS), "export": project._export_defaults(),
        "category": category, "cuts": [],
        "imported": {"pack": Path(pack_path).name, "when": time.strftime("%Y-%m-%d %H:%M")},
    }
    project.save(pid, data, own_category=True)

    if media.browser_playable(info, src):
        data["preview"] = src.name
    else:
        say("Vorschau", 0.85, "Vorschau-Video erzeugen")
        media.make_preview(src, d / "vorschau.mp4", info["duration"],
                           lambda p: say("Vorschau", 0.85 + 0.14 * p, "Vorschau-Video erzeugen"))
        data["preview"] = "vorschau.mp4"
    project.save(pid, data, own_category=True)
    say("Fertig", 1.0, f"{len(lines)} Zeilen übernommen")
    return pid


# ------------------------------------------------------------------ Aufnahmen als Video
def _take_base(name):
    n = Path(name).stem
    return n[len(TAKE_PREFIX):] if n.startswith(TAKE_PREFIX) else n


def _match_pack(bases):
    """Welches Pack gehört zu diesen Aufnahme-Dateien? (Clipnamen vergleichen)"""
    best, score = None, 0
    for entry in list_packs():
        p = read_pack(config.game_packs_dir() / entry["name"])
        names = {l["base"] for l in p["lines"]}
        hit = len(names & bases)
        if hit > score:
            best, score = entry["name"], hit
    return best if score >= max(1, len(bases) // 3) else None


def list_sessions():
    """Aufnahme-Sitzungen im Spiel: allein aufgenommene und gemeinsame. Neueste zuerst."""
    root = game_root()
    out = []
    solo = root / "recordings" / "dub_recordings"
    if solo.exists():
        for pack_dir in sorted(solo.iterdir()):
            if not pack_dir.is_dir():
                continue
            for take_dir in sorted(pack_dir.iterdir()):
                waves = sorted(take_dir.glob("*.wav")) if take_dir.is_dir() else []
                if waves:
                    out.append({"id": f"solo|{pack_dir.name}|{take_dir.name}", "kind": "solo", "pack": pack_dir.name,
                                "label": take_dir.name.replace("T", " ").replace("_", ":"), "takes": len(waves),
                                "when": take_dir.stat().st_mtime})
    multi = root / "multiplayer_takes"
    if multi.exists():
        for key_dir in sorted(multi.iterdir()):
            if not key_dir.is_dir():
                continue
            for ses in sorted(key_dir.iterdir()):
                waves = sorted(ses.glob("*.wav")) if ses.is_dir() else []
                if not waves:
                    continue
                pack = _match_pack({_take_base(w.name) for w in waves})
                out.append({"id": f"multi|{key_dir.name}|{ses.name}", "kind": "multi", "pack": pack,
                            "label": ses.name if ses.name != "_unsessioned" else "", "takes": len(waves),
                            "when": ses.stat().st_mtime})
    out.sort(key=lambda s: s["when"], reverse=True)
    # Das Spiel schreibt dieselbe Aufnahme oft in beide Ordner. Gleiches Pack, gleiche Anzahl und fast gleiche
    # Zeit: nur einmal zeigen, und zwar die Fassung mit lesbarem Datum (allein aufgenommen).
    seen, unique = [], []
    for s in out:
        twin = next((o for o in seen if o["pack"] and o["pack"] == s["pack"] and o["takes"] == s["takes"]
                     and abs(o["when"] - s["when"]) < 90), None)
        if twin:
            continue
        seen.append(s)
        unique.append(s)
    return unique


def session_dir(sid):
    kind, a, b = str(sid).split("|", 2)
    if kind == "solo":
        return game_root() / "recordings" / "dub_recordings" / a / b
    return game_root() / "multiplayer_takes" / a / b


def render_session(sid, pack_name=None, report=None, out_dir=None):
    """Aufnahmen einer Sitzung über den Pack-Hintergrund legen und als Video ausgeben. -> Pfad der Datei"""
    say = report or (lambda step, pct, msg="": None)
    d = session_dir(sid)
    waves = sorted(d.glob("*.wav"))
    if not waves:
        raise RuntimeError("In dieser Aufnahme sind keine Dateien.")
    bases = {_take_base(w.name) for w in waves}
    if not pack_name:
        pack_name = str(sid).split("|")[1] if str(sid).startswith("solo|") else _match_pack(bases)
    if not pack_name or not (config.game_packs_dir() / pack_name).exists():
        raise RuntimeError("Das Pack zu dieser Aufnahme wurde nicht gefunden. Wähle es selbst aus.")
    pack = read_pack(config.game_packs_dir() / pack_name)
    if not pack["video"]:
        raise RuntimeError("In diesem Pack fehlt das Video (dub_video.ogv).")

    say("Pack lesen", 0.05, pack["title"])
    info = media.probe(pack["video"])
    frames = int(round((info["duration"] or 0) * SR))
    mix = _read_audio(pack["backing"], frames) if pack["backing"] else np.zeros((frames, 2), dtype=np.float32)
    times = {l["base"]: l["times"] for l in pack["lines"]}

    used = 0
    for i, w in enumerate(waves):
        base = _take_base(w.name)
        for t in times.get(base) or []:
            clip = _read_audio(w)
            _mix_into(mix, clip, t)
            used += 1
        say("Aufnahmen einsetzen", 0.1 + 0.6 * (i + 1) / len(waves), f"{i + 1} von {len(waves)}")
    if not used:
        raise RuntimeError("Die Aufnahmen passen zu keiner Zeile dieses Packs.")
    peak = float(np.abs(mix).max())
    if peak > 0.99:
        mix *= 0.99 / peak

    out_dir = Path(out_dir or config.EXPORT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H-%M", time.localtime(d.stat().st_mtime))
    out = out_dir / f"{project.slugify(pack['title'])} {stamp}.mp4"
    tmp_audio = out_dir / "_aufnahme_ton.wav"
    sf.write(str(tmp_audio), mix, SR, subtype="PCM_16")
    say("Video schreiben", 0.75, out.name)
    try:
        media.mux_video(pack["video"], tmp_audio, out, info["duration"],
                        lambda p: say("Video schreiben", 0.75 + 0.24 * p, out.name))
    finally:
        tmp_audio.unlink(missing_ok=True)
    say("Fertig", 1.0, out.name)
    return {"file": str(out), "lines": used, "pack": pack["title"]}
