"""Choicer-Voicer-Dub-Pack schreiben."""
import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path

import numpy as np
import soundfile as sf

from app import config
from app.pipeline import media, project

MAX_CLIP = 59.5  # Spiel-Limit: Clips < 60 s

# Lautstaerke der Clips. Gemessen an handgemachten Packs: die liegen im Mittel bei -17,2 LUFS
# und streuen innerhalb eines Packs nur um gut 3 dB. Ohne Lautheits-Abgleich sind es bei uns 5 bis 8 dB.
LOUD_TARGET = -18.0    # LUFS, Ziel je Clip
LOUD_MAX_GAIN = 24.0   # dB, mehr wuerde bei fehlsegmentierten Clips den Trennungsrest hochziehen
LOUD_FLOOR = -45.0     # LUFS, praktisch stille Clips bleiben unveraendert
PEAK = 0.89            # Spitzendeckel; bei 0,93 uebersteuern nach dem OGG-Kodieren 2,7 % der Clips


_K_CACHE = {}


def _k_filters(sr):
    """Filter nach ITU-R BS.1770: Shelving plus Hochpass, auf die Abtastrate umgerechnet."""
    import math
    if sr in _K_CACHE:
        return _K_CACHE[sr]
    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    K = math.tan(math.pi * f0 / sr)
    Vh = 10 ** (G / 20.0)
    Vb = Vh ** 0.4996667741545416
    a0 = 1.0 + K / Q + K * K
    b1 = [(Vh + Vb * K / Q + K * K) / a0, 2.0 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0]
    a1 = [1.0, 2.0 * (K * K - 1.0) / a0, (1.0 - K / Q + K * K) / a0]
    f0, Q = 38.13547087602444, 0.5003270373238773
    K = math.tan(math.pi * f0 / sr)
    d = 1.0 + K / Q + K * K
    b2 = [1.0, -2.0, 1.0]
    a2 = [1.0, 2.0 * (K * K - 1.0) / d, (1.0 - K / Q + K * K) / d]
    _K_CACHE[sr] = (np.array(b1), np.array(a1), np.array(b2), np.array(a2))
    return _K_CACHE[sr]


def _lufs(x, sr):
    """Empfundene Lautheit eines Clips in LUFS (BS.1770-4, mit Sperre fuer stille Stellen)."""
    from scipy import signal
    if len(x) < sr // 10:
        return -70.0
    b1, a1, b2, a2 = _k_filters(sr)
    y = signal.lfilter(b2, a2, signal.lfilter(b1, a1, x.astype(np.float64)))
    bs, step = max(1, int(0.4 * sr)), max(1, int(0.1 * sr))
    n = (len(y) - bs) // step + 1
    if n <= 0:
        return float(-0.691 + 10 * np.log10(np.mean(y ** 2) + 1e-12))
    p = np.lib.stride_tricks.sliding_window_view(y ** 2, bs)[::step].mean(axis=1)
    lv = -0.691 + 10 * np.log10(p + 1e-12)
    keep = lv > -70.0
    if not keep.any():
        return -70.0
    rel = -0.691 + 10 * np.log10(np.mean(p[keep]) + 1e-12) - 10.0
    keep &= lv > rel
    if not keep.any():
        return -70.0
    return float(-0.691 + 10 * np.log10(np.mean(p[keep]) + 1e-12))


def _match_loudness(clips, sr):
    """Jeden Clip auf dieselbe Lautheit bringen, ohne die Spitzen anzuheben."""
    out = []
    for c in clips:
        if not len(c):
            out.append(c)
            continue
        peak = float(np.abs(c).max())
        loud = _lufs(c, sr) if peak > 1e-4 else -70.0
        if peak <= 1e-4 or loud <= LOUD_FLOOR:
            out.append(c)  # still: so lassen, sonst wird nur das Rauschen laut
            continue
        gain = min(10 ** ((LOUD_TARGET - loud) / 20.0), 10 ** (LOUD_MAX_GAIN / 20.0), PEAK / peak)
        out.append(c * gain if abs(gain - 1.0) > 0.005 else c)
    return out


def _ini_str(s):
    s = re.sub(r"\s*\n\s*", " ", str(s)).strip()
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _ini_multiline(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _file_part(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", "_", name.strip())
    return name[:40] or "Clip"


def write_ogg(path, data, sr, tag=None, block=16384):
    """OGG Vorbis blockweise schreiben (libsndfile stürzt unter Windows bei großen Blöcken ab).

    tag: Credit-Text für die Vorbis-Metadaten, None = keine Kennzeichnung.
    """
    channels = 1 if data.ndim == 1 else data.shape[1]
    with sf.SoundFile(path, "w", sr, channels, format="OGG", subtype="VORBIS") as f:
        if tag:
            f.comment = tag
            f.software = config.APP_NAME
        for i in range(0, len(data), block):
            f.write(data[i:i + block])


def pack_authors(author_field, credit=None):
    """Autorenliste fürs Pack: eigene Namen, danach (falls eingeschaltet) der Credit-Eintrag."""
    names = list(dict.fromkeys(a.strip() for a in (author_field or "").split(",") if a.strip()))
    if credit and credit.lower() not in (n.lower() for n in names):
        names.append(credit)
    return names


def credit_plan(data):
    """Was an Credits geschrieben wird: Text (oder None) und wohin."""
    c = dict(config.DEFAULT_CREDITS, **(data.get("credits") or {}))
    text = config.credit_text(c)
    return {"text": text, "authors": bool(text and c["in_authors"]), "readme": bool(text and c["in_readme"]),
            "files": bool(text and c["in_files"])}


def _fade(x, sr, ms=8):
    n = min(len(x) // 2, int(sr * ms / 1000))
    if n > 0:
        ramp = np.linspace(0, 1, n, dtype=np.float32)
        x[:n] *= ramp
        x[-n:] *= ramp[::-1]
    return x


def _coverage_mask(lines, total, sr, ramp=0.04):
    mask = np.zeros(total, dtype=np.float32)
    r = int(ramp * sr)
    for ln in lines:
        a, b = int(ln["start"] * sr), min(total, int(ln["end"] * sr))
        if b <= a:
            continue
        mask[a:b] = 1.0
        if r:
            lo = max(0, a - r)
            mask[lo:a] = np.maximum(mask[lo:a], np.linspace(0, 1, a - lo, dtype=np.float32))
            hi = min(total, b + r)
            mask[b:hi] = np.maximum(mask[b:hi], np.linspace(1, 0, hi - b, dtype=np.float32))
    return mask


def _cut_array(x, sr, segs):
    """Nur die bleibenden Teile eines Audio-Arrays (Zeilen = Samples) aneinanderhängen."""
    parts = [x[int(a * sr):int(b * sr)] for a, b in segs]
    return np.concatenate(parts) if parts else x[:0]


def ensure_video(pid, report):
    """dub_video.ogv erzeugen (oder aus dem Cache holen) und auf Dekodierfehler prüfen."""
    d = project.project_dir(pid)
    data = project.load(pid)
    opts = data["export"]
    report("Video kodieren", 0, "dub_video.ogv erzeugen")
    # "theora3": funktionierender Encoder (ältere Caches waren teils defekt); Kennzeichnung gehört zum Schlüssel
    plan = credit_plan(data)
    tag = plan["text"] if plan["files"] else None
    cuts = project.normalize_cuts(data.get("cuts"), data["duration"])
    key_parts = ["theora3", data["source"], opts["video_height"], opts["video_fps"], opts["video_quality"], tag]
    if cuts:
        key_parts.append(cuts)   # andere Schnitte = anderes Video
    key = hashlib.md5(json.dumps(key_parts).encode()).hexdigest()[:10]
    cached = d / f"dub_video_{key}.ogv"
    if not cached.exists():
        for old in d.glob("dub_video_*.ogv"):
            old.unlink()
        part = d / "dub_video.part.ogv"
        audio, duration = d / "audio.wav", data["duration"]
        cuts = project.snap_cuts(cuts, media.target_fps(d / data["source"], opts["video_fps"]))
        if cuts:   # Ton für das geschnittene Video vorbereiten (Bild schneidet ffmpeg beim Kodieren)
            mix, sr = sf.read(audio, dtype="float32", always_2d=True)
            segs = project.keep_segments(cuts, data["duration"])
            audio = d / "audio_geschnitten.wav"
            sf.write(audio, _cut_array(mix, sr, segs), sr, subtype="PCM_16")
            duration = sum(b - a for a, b in segs)
        try:
            media.encode_ogv(d / data["source"], part, duration, opts["video_height"], opts["video_fps"],
                             opts["video_quality"], lambda p: report("Video kodieren", p, "dub_video.ogv erzeugen"),
                             audio=audio, tag=tag, cuts=cuts)
        finally:
            if cuts:
                (d / "audio_geschnitten.wav").unlink(missing_ok=True)
        report("Video prüfen", 0, "dub_video.ogv testweise abspielen")
        errors = media.count_decode_errors(part)
        if errors:
            raise RuntimeError(f"Das erzeugte Video ist fehlerhaft ({errors} Dekodierfehler). Export abgebrochen, "
                               "damit kein kaputtes Pack ins Spiel kommt.")
        part.replace(cached)
    return cached


def export_pack(pid, report, install=False, overwrite_game=False):
    folder = project.slugify(project.load(pid)["name"])
    try:
        return _export_pack(pid, report, install, overwrite_game)
    except BaseException:
        # Abbruch/Fehler: halbfertige Dateien entfernen
        shutil.rmtree(config.EXPORT_PACKS / (folder + ".tmp"), ignore_errors=True)
        for f in (config.EXPORT_ZIPS / f"{folder}.zip.tmp", project.project_dir(pid) / "dub_video.part.ogv"):
            f.unlink(missing_ok=True)
        raise


def _export_pack(pid, report, install=False, overwrite_game=False):
    d = project.project_dir(pid)
    data = project.load(pid)
    opts = data["export"]
    pack = data["pack"]
    chars = {c["id"]: c for c in data["characters"]}
    all_lines = sorted([ln for ln in data["lines"] if ln["end"] > ln["start"]], key=lambda l: l["start"])
    by_id = {ln["id"]: ln for ln in all_lines}
    # Wiederholungen: eine Aufnahme, mehrere Zeitpunkte im Spiel
    extra_times = {}
    for ln in all_lines:
        master = by_id.get(ln.get("repeat_of"))
        if master is not None and master is not ln:
            extra_times.setdefault(master["id"], []).append(ln["start"])
    lines = [ln for ln in all_lines if not (ln.get("repeat_of") and ln["repeat_of"] in by_id)]
    # Video geschnitten: Zeilen in rausgeschnittenen Stellen fallen weg, angeschnittene behalten ihren längsten Teil,
    # alle Zeitpunkte rücken um die Schnitte davor nach vorne
    cuts = project.normalize_cuts(data.get("cuts"), data["duration"])
    if cuts:
        cuts = project.snap_cuts(cuts, media.target_fps(d / data["source"], opts["video_fps"]))
    span = {}
    for ln in lines:
        dur = ln["end"] - ln["start"]
        starts = [ln["start"]] + sorted(extra_times.get(ln["id"], []))
        # Jeder Zeitpunkt der Aufnahme wird geprüft. Fällt der erste weg, liefert die erste erhaltene
        # Wiederholung den Clip; die übrigen rücken um dieselbe Strecke mit wie der Clipanfang.
        kept = [(t, project.kept_part(t, t + dur, cuts) if cuts else (t, t + dur)) for t in starts]
        kept = [(t, p) for t, p in kept if p]
        if not kept:
            extra_times.pop(ln["id"], None)
            continue
        first_t, first_part = kept[0]
        span[ln["id"]] = first_part
        shift = first_part[0] - first_t
        extra_times[ln["id"]] = [t + shift for t, _ in kept[1:]]
    cut_away = len(lines) - len(span)
    lines = [ln for ln in lines if ln["id"] in span]
    if not lines:
        raise RuntimeError("Keine Zeilen zum Exportieren.")

    title = data["name"]  # Projektname = Pack-Titel = Ordnername
    folder = project.slugify(title)
    game_target = config.game_packs_dir() / folder
    if install and game_target.exists() and not overwrite_game:
        raise FileExistsError(str(game_target))

    out = config.EXPORT_PACKS / folder
    tmp = config.EXPORT_PACKS / (folder + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    src = d / data["source"]
    warnings = []
    plan = credit_plan(data)
    tag = plan["text"] if plan["files"] else None

    # 1) Video (gecacht, weil langsam)
    shutil.copyfile(ensure_video(pid, report), tmp / "dub_video.ogv")

    # 2) Audio laden
    report("Clips schneiden", 0, "Audio laden")
    voc, sr = sf.read(d / "stimmen.wav", dtype="float32", always_2d=True)
    source_file = {"original": d / "audio.wav", "differenz": d / "stimmen_diff.wav"}.get(opts["clip_source"])
    if source_file and source_file.exists():
        clip_src = sf.read(source_file, dtype="float32", always_2d=True)[0]
    else:
        clip_src = voc  # getrennte Stimmen (Standard)
    mono = clip_src.mean(axis=1)

    clips = []
    if cut_away:
        warnings.append(f"{cut_away} Zeilen liegen in rausgeschnittenen Stellen und fehlen im Pack.")
    for ln in lines:
        s0, e0 = span[ln["id"]]
        a, b = int(s0 * sr), int(min(e0, s0 + MAX_CLIP) * sr)
        if e0 - s0 > MAX_CLIP:
            warnings.append(f"Zeile bei {ln['start']:.1f}s war länger als 60 s und wurde gekürzt.")
        clips.append(_fade(mono[a:b].copy(), sr))
    if opts["normalize"] == "lautheit":
        report("Clips schneiden", 0, "Lautstärke angleichen")
        clips = _match_loudness(clips, sr)
    elif opts["normalize"] == "clip":
        clips = [c / (np.abs(c).max() + 1e-9) * PEAK if np.abs(c).max() > 1e-4 else c for c in clips]
    elif opts["normalize"] == "gemeinsam":
        peak = max((np.abs(c).max() for c in clips), default=1.0)
        clips = [c / (peak + 1e-9) * PEAK for c in clips]

    # 3) Clips, Bilder, INIs
    width = len(str(len(lines))) if len(lines) >= 1000 else 3
    for i, (ln, clip) in enumerate(zip(lines, clips)):
        report("Clips schneiden", i / len(lines), f"Clip {i + 1}/{len(lines)}")
        names = [chars[c]["name"] for c in ln["chars"] if c in chars] or ["Unbekannt"]
        base = f"{i + 1:0{width}d}_" + "_".join(_file_part(n) for n in names)
        write_ogg(tmp / f"{base}.ogg", clip, sr, tag)

        image = None
        mode = opts["image_mode"]
        img_char = next((chars[c] for c in ln["chars"] if c in chars and chars[c].get("image")), None)
        if mode == "charakter" and img_char and (d / "bilder" / img_char["image"]).exists():
            image = f"Bild_{_file_part(img_char['name'])}.jpg"  # ein Bild pro Charakter, von allen Zeilen genutzt
            if not (tmp / image).exists():
                shutil.copyfile(d / "bilder" / img_char["image"], tmp / image)
        elif mode in ("frame", "charakter"):
            image = f"{base}.jpg"
            try:
                media.pick_frame(src, ln["start"], ln["end"], tmp / image,
                                 (data.get("width"), data.get("height")), data.get("fps"))
            except (RuntimeError, OSError, ValueError):
                image = None

        ini = ([f"; {tag}"] if tag else []) + ["[data]", "", f"caption={_ini_str(ln['text'])}"]
        if image:
            ini.append(f"image={_ini_str(image)}")
        times = sorted(project.cut_time(t, cuts) for t in [span[ln["id"]][0]] + extra_times.get(ln["id"], []))
        ini.append("dub_timestamps=[" + ", ".join(f"{t:.3f}" for t in times) + "]")
        ini.append("dub_characters=[" + ", ".join(_ini_str(n) for n in names) + "]")
        # Das Spiel liest die Zeilen-Dateien als .ini und als .txt (gleicher Inhalt), wählbar im Export
        ext = ".txt" if opts.get("line_format") == "txt" else ".ini"
        (tmp / f"{base}{ext}").write_text("\n".join(ini) + "\n", encoding="utf8")

    # 4) Backing-Track
    if extra_times:
        warnings.append(f"{sum(len(v) for v in extra_times.values())} Wiederholungen nutzen dieselbe Aufnahme "
                        "(ein Clip mit mehreren Zeitpunkten).")

    report("Backing-Track", 0, "_backing_track.ogg erzeugen")
    own = d / "instrumental.wav"
    use_own = opts.get("backing_source") == "eigene" and own.exists()
    back, bsr = sf.read(own if use_own else d / "hintergrund.wav", dtype="float32", always_2d=True)
    if use_own:
        warnings.append("Hintergrund: eigene Instrumental-Datei verwendet.")
    try:
        vol = min(max(float(opts.get("backing_volume", 1.0)), 0.0), 2.0)
    except (TypeError, ValueError):
        vol = 1.0
    if abs(vol - 1.0) > 0.005:   # Lautstärke des Hintergrunds (Export, auch im Player zu hören)
        back = back * vol
    if opts.get("keep_unused_voices", True):
        # Stimmen ohne Zeile beimischen, bei eigener Instrumental-Datei aus der Differenz (sauberer)
        diff = d / "stimmen_diff.wav"
        extra = sf.read(diff, dtype="float32", always_2d=True)[0] if (use_own and diff.exists()) else voc
        n = min(len(back), len(extra))
        mask = _coverage_mask(all_lines, n, bsr)
        back = back[:n] + extra[:n] * (1.0 - mask)[:, None]
    if cuts:
        back = _cut_array(back, bsr, project.keep_segments(cuts, data["duration"]))
    peak = np.abs(back).max()
    if peak > 0.99:
        back = back / peak * 0.99
    write_ogg(tmp / "_backing_track.ogg", back, bsr, tag)

    # 5) Pack-Infos
    report("Pack-Infos", 0, "_pack_info.ini schreiben")
    icon_name = None
    if pack.get("icon") and (d / "bilder" / pack["icon"]).exists():
        icon_name = "_icon.jpg"
        shutil.copyfile(d / "bilder" / pack["icon"], tmp / icon_name)
    else:
        icon_name = "_icon.jpg"
        media.grab_frame(src, min(data["duration"] * 0.1, lines[0]["start"] + 0.3), tmp / icon_name, width=640)
    info = ([f"; {tag}"] if tag else []) + ["[data]", "", f"title={_ini_str(title)}", f"icon={_ini_str(icon_name)}"]
    authors = pack_authors(pack.get("author"), plan["text"] if plan["authors"] else None)
    info.append("authors=[" + ", ".join(_ini_str(a) for a in authors) + "]")
    readme = (pack.get("readme") or "").strip()
    if plan["readme"]:
        readme = (readme + "\n\n" + plan["text"]).strip()
    if readme:
        info.append(f"readme={_ini_multiline(readme)}")
    (tmp / "_pack_info.ini").write_text("\n".join(info) + "\n", encoding="utf8")
    if tag:
        (tmp / f"_{config.APP_NAME}.txt").write_text(
            f"{tag}.\nThis Choicer Voicer dub pack was created with {config.APP_NAME}.\n", encoding="utf8")
    if (pack.get("subtitle") or "").strip():
        (tmp / "_subtitle.txt").write_text(pack["subtitle"].strip(), encoding="utf8")

    # 6) Ordner tauschen + ZIP
    shutil.rmtree(out, ignore_errors=True)
    tmp.replace(out)
    report("ZIP packen", 0, "ZIP erstellen")
    zip_path = config.EXPORT_ZIPS / f"{folder}.zip"
    zip_tmp = zip_path.with_suffix(".zip.tmp")
    files = sorted(out.iterdir())
    with zipfile.ZipFile(zip_tmp, "w") as z:
        if tag:
            z.comment = tag.encode("utf8")
        for i, f in enumerate(files):
            comp = zipfile.ZIP_DEFLATED if f.suffix in (".ini", ".txt") else zipfile.ZIP_STORED
            z.write(f, f"{folder}/{f.name}", compress_type=comp)
            report("ZIP packen", (i + 1) / len(files), "ZIP erstellen")
    zip_tmp.replace(zip_path)

    installed = None
    if install:
        report("Installieren", 0, "Ins Spiel kopieren")
        config.game_packs_dir().mkdir(parents=True, exist_ok=True)
        if game_target.exists():
            shutil.rmtree(game_target)
        shutil.copytree(out, game_target)
        installed = str(game_target)

    report("Fertig", 1, "Export abgeschlossen")
    return {"folder": str(out), "zip": str(zip_path), "installed": installed, "clips": len(lines),
            "warnings": warnings}


INSTALL_NOTE = """So installierst du diese Dub Packs / How to install these dub packs

1. The Choicer Voicer schließen. / Close The Choicer Voicer.
2. Alle Ordner aus dieser ZIP hierhin entpacken / Extract all folders from this ZIP to:
   %APPDATA%\\YeahMaybe\\ChoicerVoicer\\game\\packs_voice\\
3. Spiel starten, die Packs erscheinen in der Liste. / Start the game, the packs show up in the list.
"""


def export_category(cid, report, install=False, overwrite_game=False, only=None):
    """Alle fertigen Projekte einer Kategorie exportieren und zusammen in eine ZIP packen (zum Teilen).

    Jedes Projekt wird wie gewohnt exportiert (Videos kommen aus dem Zwischenspeicher, wenn unverändert).
    Gibt es ein Pack schon im Spiel und ist Ersetzen nicht erlaubt, wird es übersprungen und gemeldet.
    """
    cat = next((c for c in project.categories() if c["id"] == cid), None)
    if cat is None:
        raise RuntimeError("Kategorie nicht gefunden.")
    todo = [p for p in project.category_projects(cid) if p["status"] == "fertig" and (only is None or p["id"] in only)]
    if not todo:
        raise RuntimeError("In dieser Kategorie ist noch kein fertiges Projekt.")
    n = len(todo)
    done, exists, failed = [], [], []
    for i, p in enumerate(todo):
        def sub(step, pct, msg="", i=i):
            report(f"Pack {i + 1}/{n}", (i + min(max(pct, 0.0), 1.0)) / n, step)
        try:
            done.append(export_pack(p["id"], sub, install, overwrite_game))
        except FileExistsError:
            exists.append({"id": p["id"], "name": p["name"]})
        except RuntimeError as e:
            failed.append({"name": p["name"], "error": str(e)})
    if not done and not exists:
        raise RuntimeError("Kein Pack ließ sich exportieren: " + "; ".join(f"{f['name']}: {f['error']}" for f in failed))
    zip_path = None
    if done:
        report("ZIP packen", 0, "ZIP erstellen")
        name = project.slugify(cat["name"])
        zip_path = config.EXPORT_ZIPS / f"{name}.zip"
        zip_tmp = zip_path.with_suffix(".zip.tmp")
        with zipfile.ZipFile(zip_tmp, "w") as z:
            z.writestr("_So installieren.txt", INSTALL_NOTE)
            for k, r in enumerate(done):
                folder = Path(r["folder"])
                for f in sorted(folder.iterdir()):
                    comp = zipfile.ZIP_DEFLATED if f.suffix in (".ini", ".txt") else zipfile.ZIP_STORED
                    z.write(f, f"{folder.name}/{f.name}", compress_type=comp)
                report("ZIP packen", (k + 1) / len(done), "ZIP erstellen")
        zip_tmp.replace(zip_path)
    report("Fertig", 1, "Export abgeschlossen")
    return {"zip": str(zip_path) if zip_path else None, "packs": len(done), "clips": sum(r["clips"] for r in done),
            "installed": sum(1 for r in done if r.get("installed")), "exists": exists, "failed": failed,
            "warnings": [w for r in done for w in r.get("warnings", [])]}
