"""Projekte anlegen, verarbeiten, speichern."""
import bisect
import json
import re
import shutil
import threading
import time
import unicodedata
import uuid
from collections import Counter
from pathlib import Path

import numpy as np

from app import config
from app.pipeline import media


def slugify(name):
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    return name[:80] or "Projekt"


def project_dir(pid):
    d = (config.PROJECTS_DIR / pid).resolve()
    if config.PROJECTS_DIR.resolve() not in d.parents:
        raise ValueError("Ungültige Projekt-ID")
    return d


def load(pid):
    return json.loads((project_dir(pid) / "project.json").read_text(encoding="utf8"))


# ------------------------------------------------------------------ Video schneiden (im Editor)
# Rausgeschnittene Stellen werden nur gespeichert ("cuts": [[start, ende], …] in Sekunden des Originals).
# Das Original bleibt unverändert; der Editor springt beim Abspielen darüber, erst der Export lässt sie weg.
MIN_CUT = 0.05


def normalize_cuts(cuts, duration):
    """Gültige, sortierte, zusammengefasste Schnitte innerhalb des Videos."""
    out = []
    for c in cuts or []:
        try:
            s, e = sorted((float(c[0]), float(c[1])))
        except (TypeError, ValueError, IndexError):
            continue
        s, e = max(0.0, s), min(float(duration or e), e)
        if e - s >= MIN_CUT:
            out.append([round(s, 3), round(e, 3)])
    out.sort()
    merged = []
    for s, e in out:
        if merged and s <= merged[-1][1] + 0.01:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def keep_segments(cuts, duration):
    """Die Teile des Videos, die bleiben: [(start, ende), …]."""
    segs, t = [], 0.0
    for s, e in cuts:
        if s > t:
            segs.append((t, s))
        t = max(t, e)
    if t < duration:
        segs.append((t, duration))
    return segs


def snap_cuts(cuts, fps):
    """Schnittgrenzen aufs Bildraster runden: Ton wird probengenau geschnitten, Bild nur bildweise. Ohne das
    laufen Bild und Ton je Schnitt bis zu eine Bildlänge auseinander."""
    if not cuts or not fps:
        return cuts
    return [[round(s * fps) / fps, round(e * fps) / fps] for s, e in cuts]


def cut_time(t, cuts):
    """Zeitpunkt im Original -> Zeitpunkt im geschnittenen Video (in einem Schnitt: dessen Anfang)."""
    shift = 0.0
    for s, e in cuts:
        if t >= e:
            shift += e - s
        elif t > s:
            return s - shift
    return t - shift


def kept_part(start, end, cuts):
    """Längster Teil von [start, ende], der nicht rausgeschnitten ist, oder None."""
    parts, a = [], start
    for s, e in cuts:
        if e <= a or s >= end:
            continue
        if s > a:
            parts.append((a, s))
        a = max(a, e)
    if a < end:
        parts.append((a, end))
    parts = [p for p in parts if p[1] - p[0] >= MIN_CUT]
    return max(parts, key=lambda p: p[1] - p[0]) if parts else None


def save(pid, data, own_category=False):
    """Speichern. Die Kategorie gehört der Übersicht: Eine laufende Verarbeitung mit älterer Kopie
    darf eine inzwischen geänderte Zuordnung nicht überschreiben (nur set_category ändert sie)."""
    d = project_dir(pid)
    target = d / "project.json"
    tmp = d / "project.json.tmp"
    if not own_category and target.exists():
        try:
            data["category"] = json.loads(target.read_text(encoding="utf8")).get("category")
        except Exception:
            pass
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf8")
    if target.exists():
        shutil.copyfile(target, d / "project.backup.json")
    tmp.replace(target)


def list_projects():
    out = []
    for d in sorted(config.PROJECTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        f = d / "project.json"
        if not f.exists():
            continue
        try:
            p = json.loads(f.read_text(encoding="utf8"))
        except Exception:
            continue
        out.append({
            "id": d.name, "name": p.get("name"), "status": p.get("status"), "error": p.get("error"),
            "duration": p.get("duration"), "lines": len(p.get("lines", [])),
            "characters": len(p.get("characters", [])), "language": p.get("language"),
            "quality": (p.get("settings") or {}).get("quality"),
            "device": (p.get("settings") or {}).get("device"),
            "online": bool((p.get("settings") or {}).get("online")),
            "online_asr": (p.get("settings") or {}).get("online_asr"),
            "error_code": p.get("error_code"), "error_detail": p.get("error_detail"),
            "category": p.get("category"),
        })
    return out


# ------------------------------------------------------------------ Kategorien
# Projekte lassen sich in Kategorien sortieren (z. B. 30 Family-Guy-Clips). Die Liste der Kategorien
# (in ihrer Reihenfolge) und die eigene Reihenfolge der Projekte liegen in daten/kategorien.json,
# die Zuordnung im jeweiligen project.json (übersteht Umbenennen).
CATEGORIES_FILE = config.DATA_DIR / "kategorien.json"


def _read_categories():
    try:
        return json.loads(CATEGORIES_FILE.read_text(encoding="utf8"))
    except Exception:
        return {}


def _write_categories(data):
    CATEGORIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CATEGORIES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf8")
    tmp.replace(CATEGORIES_FILE)


def categories():
    return _read_categories().get("list", [])


def _save_categories(items):
    data = _read_categories()
    data["list"] = items
    _write_categories(data)


def project_order():
    """Eigene Reihenfolge der Projekte (IDs, per Ziehen festgelegt). Fehlende stehen vorne."""
    return _read_categories().get("order", [])


def set_project_order(ids):
    data = _read_categories()
    data["order"] = [str(i) for i in ids if isinstance(i, str)][:5000]
    _write_categories(data)


def reorder_categories(ids):
    """Kategorien in die gegebene Reihenfolge bringen; unbekannte IDs zählen nicht, fehlende kommen ans Ende."""
    items = categories()
    pos = {cid: i for i, cid in enumerate(ids)}
    items.sort(key=lambda c: pos.get(c["id"], len(pos)))
    _save_categories(items)


def create_category(name):
    name = (name or "").strip()[:80]
    if not name:
        raise ValueError("Der Name darf nicht leer sein.")
    items = categories()
    cid = "k" + uuid.uuid4().hex[:8]
    items.append({"id": cid, "name": name, "collapsed": False})
    _save_categories(items)
    return cid


def update_category(cid, name=None, collapsed=None):
    items = categories()
    cat = next((c for c in items if c["id"] == cid), None)
    if cat is None:
        raise KeyError(cid)
    if name is not None:
        name = name.strip()[:80]
        if not name:
            raise ValueError("Der Name darf nicht leer sein.")
        cat["name"] = name
    if collapsed is not None:
        cat["collapsed"] = bool(collapsed)
    _save_categories(items)


def delete_category(cid):
    """Kategorie entfernen; ihre Projekte bleiben erhalten und sind danach ohne Kategorie."""
    _save_categories([c for c in categories() if c["id"] != cid])
    for p in list_projects():
        if p.get("category") == cid:
            set_category(p["id"], None)


def set_category(pid, cid):
    if cid and not any(c["id"] == cid for c in categories()):
        raise KeyError(cid)
    data = load(pid)
    data["category"] = cid or None
    save(pid, data, own_category=True)


def category_projects(cid):
    return [p for p in list_projects() if p.get("category") == cid]


def new_id():
    return "l" + uuid.uuid4().hex[:10]


def inbox_file(filename):
    src = (config.INBOX_DIR / filename).resolve()
    if config.INBOX_DIR.resolve() != src.parent or not src.is_file():
        raise FileNotFoundError(filename)
    return src


def _free_id(name, own=None):
    base = slugify(name)
    pid, i = base, 2
    while (config.PROJECTS_DIR / pid).exists() and pid != own:
        pid, i = f"{base} ({i})", i + 1
    return pid


def _export_defaults():
    """Export-Einstellungen neuer Projekte: Grundwerte, überschrieben durch die Einstellungen."""
    s = config.user_settings()
    return {
        "clip_source": "stimmen", "backing_source": "auto", "backing_volume": 1.0,
        "normalize": s.get("export_normalize", "lautheit"),
        "image_mode": s.get("export_image_mode", "frame"),
        "keep_unused_voices": bool(s.get("export_keep_voices", True)),
        "video_height": int(s.get("export_video_height", 720)),
        "video_fps": int(s.get("export_video_fps", 30)),
        "video_quality": int(s.get("export_video_quality", 7)),
        "line_format": s.get("export_line_format", "ini") if s.get("export_line_format") in ("ini", "txt") else "ini",
    }


def create(filename, name=None, language="auto", speakers=None, quality=None, laugh=True, ui_lang=None, category=None,
           reftext=None):
    src = inbox_file(filename)
    name = (name or "").strip() or src.stem
    pid = _free_id(name)
    d = config.PROJECTS_DIR / pid
    d.mkdir(parents=True)
    dst = d / ("quelle" + src.suffix.lower())
    shutil.move(str(src), dst)  # Video wandert aus "eingang" ins Projekt
    data = {
        "version": 1,
        "name": name,
        "created": time.strftime("%Y-%m-%d %H:%M"),
        "source": dst.name,
        "preview": None,
        "status": "wartet",
        "error": None,
        "settings": {"language": language, "speakers": speakers,
                     "quality": quality if quality in config.QUALITY else config.DEFAULT_QUALITY,
                     "laugh": bool(laugh), "ui_lang": ui_lang if ui_lang in SPEAKER_WORD else "en"},
        "language": None,
        "duration": 0,
        "characters": [],
        "lines": [],
        "pack": {"author": "", "subtitle": "", "readme": "", "icon": None},
        "credits": dict(config.DEFAULT_CREDITS),   # bei jedem neuen Projekt an, pro Projekt abschaltbar
        "export": _export_defaults(),
        "category": category if category and any(c["id"] == category for c in categories()) else None,
    }
    if reftext and (reftext.get("text") or "").strip():   # vorgegebener Text (Liedtext, Drehbuch, Untertitel)
        data["reftext"] = {"text": reftext["text"][:400000], "source": str(reftext.get("source") or "")[:200]}
    try:   # Herkunft aus dem Download (YouTube-Adresse) ins Projekt übernehmen
        from app.pipeline import textsources
        info = textsources.source_info(filename)
        if info:
            data["source_url"] = info.get("url")
    except Exception:
        pass
    save(pid, data)
    return pid


SPEAKER_WORD = {"de": "Sprecher", "en": "Speaker", "es": "Hablante", "fr": "Locuteur", "pt": "Falante", "it": "Parlante",
                "ru": "Спикер", "pl": "Mówca", "tr": "Konuşmacı", "nl": "Spreker", "ja": "話者", "zh": "说话人",
                "ko": "화자", "uk": "Мовець", "id": "Pembicara", "hi": "वक्ता", "cs": "Mluvčí", "sk": "Hovoriaci",
                "sr": "Govornik", "sv": "Talare", "da": "Taler", "ro": "Vorbitor", "hu": "Beszélő", "el": "Ομιλητής",
                "vi": "Người nói", "th": "ผู้พูด"}


def _character_list(n, lang=None):
    """Standard-Figuren „Speaker 1, 2 …“ in der Sprache der Oberfläche (landen so auch im Pack)."""
    word = SPEAKER_WORD.get(lang or "en", "Speaker")
    return [{"id": f"c{k + 1}", "name": f"{word} {k + 1}",
             "color": config.CHARACTER_COLORS[k % len(config.CHARACTER_COLORS)], "image": None}
            for k in range(n)]


def process(pid, report):
    """Komplette Verarbeitung. report(step, pct, msg)."""
    from app.pipeline import separate, transcribe, diarize, segment

    d = project_dir(pid)
    data = load(pid)
    data["status"] = "verarbeitet"
    data["error"] = None
    save(pid, data)
    src = d / data["source"]
    quality = data["settings"].get("quality")

    # 1) Vorbereiten
    report("Vorbereiten", 0, "Video analysieren")
    info = media.probe(src)
    data.update(duration=info["duration"], width=info["width"], height=info["height"], fps=info["fps"])
    if info["audio_codec"] is None:
        raise RuntimeError("Das Video hat keine Tonspur.")
    media.extract_audio(src, d / "audio.wav")

    # Online rechnen: Trennung und/oder Spracherkennung bei Gratis-Diensten (je nachdem, was eingerichtet ist)
    online_ready = None
    if data["settings"].get("online"):
        from app.pipeline import online
        online_ready = online.ready(asr=data["settings"].get("online_asr"))
        if not online_ready["separate"] and not online_ready["transcribe"]:
            raise online.OnlineError("Online rechnen ist nicht eingerichtet. Richte es unter Einstellungen → Online rechnen "
                                     "ein oder rechne auf diesem PC.", "online_setup")
        data["online"] = {"separate": "mvsep" if online_ready["separate"] else None, "transcribe": online_ready["transcribe"]}

    preview_thread, preview_error = None, []
    preview_tmp = d / "vorschau.part.mp4"
    if media.browser_playable(info, src):
        data["preview"] = data["source"]
    elif online_ready and online_ready["separate"]:
        # Die Vorschau entsteht, während MVSEP wartet und trennt: Der PC hat in der Zeit sonst nichts zu tun
        def _preview():
            try:
                media.make_preview(src, preview_tmp, info["duration"], lambda p: None)
            except Exception as e:  # noqa: BLE001
                preview_error.append(e)
        preview_thread = threading.Thread(target=_preview, daemon=True)
        preview_thread.start()
    else:
        report("Vorbereiten", 0.1, "Vorschau-Video erzeugen")
        media.make_preview(src, d / "vorschau.mp4", info["duration"],
                           lambda p: report("Vorbereiten", 0.1 + 0.9 * p, "Vorschau-Video erzeugen"))
        data["preview"] = "vorschau.mp4"
    save(pid, data)

    # 2) Stimmen trennen
    try:
        if online_ready and online_ready["separate"]:
            report("Stimmen trennen", 0, "Tonspur wird zu MVSEP hochgeladen")
            online.separate(d / "audio.wav", d, lambda p, msg: report("Stimmen trennen", p, msg))
        else:
            report("Stimmen trennen", 0, "Modell laden (beim ersten Mal Download ~600 MB)")
            transcribe.unload()
            separate.separate(d / "audio.wav", d, lambda p: report("Stimmen trennen", p, "Stimmen vom Hintergrund trennen"),
                              overlap=config.quality(quality)["overlap"])
    finally:
        if preview_thread and preview_thread.is_alive():
            report("Stimmen trennen", 0.99, "Vorschau-Video erzeugen")
            preview_thread.join(timeout=1800)   # nie ohne: sonst schreibt ffmpeg weiter, wenn hier etwas schiefgeht
    media.encode_opus(d / "stimmen.wav", d / "stimmen.ogg")
    media.encode_opus(d / "hintergrund.wav", d / "hintergrund.ogg")
    if preview_thread:
        if preview_error:
            raise preview_error[0]
        preview_tmp.replace(d / "vorschau.mp4")   # erst fertig, dann sichtbar
        data["preview"] = "vorschau.mp4"
        save(pid, data)

    # 3) Sprache erkennen
    voc16 = media.load_mono(d / "stimmen.wav")
    lang = data["settings"].get("language") or "auto"
    if online_ready and online_ready["transcribe"]:
        report("Sprache erkennen", 0, "Tonspur wird hochgeladen")
        asr = online.transcribe(voc16, None if lang == "auto" else lang, lambda p, msg: report("Sprache erkennen", p, msg),
                                vad_threshold=config.quality(quality)["vad_threshold"], service=online_ready["transcribe"])
    else:
        report("Sprache erkennen", 0, "Whisper laden (beim ersten Mal Download bis 3 GB)")
        asr = transcribe.transcribe(voc16, None if lang == "auto" else lang,
                                    lambda p: report("Sprache erkennen", p, "Text erkennen"), quality=quality)
    data["language"] = asr["language"]
    data["language_probability"] = asr.get("language_probability")   # unsicher? -> Hinweis im Editor
    words = asr["words"]
    if (data.get("reftext") or {}).get("text"):
        # 3b) vorgegebenen Text zuordnen: richtige Schreibweise, fehlende Wörter, Liedzeilen als Zeilen
        from app.pipeline import reftext
        report("Sprache erkennen", 0.99, "Vorgegebenen Text zuordnen")
        words, data["reftext"]["report"] = reftext.apply(words, data["reftext"]["text"], env=segment.envelope_db(voc16))
        save(pid, data)

    # 4) Sprecher erkennen
    report("Sprecher erkennen", 0, "Stimmen analysieren")
    transcribe.unload()
    dia = diarize.diarize(voc16, words, n_speakers=data["settings"].get("speakers"),
                          on_progress=lambda p: report("Sprecher erkennen", p, "Stimmen analysieren"),
                          quality=quality)
    _save_analysis(d, words, asr["segments"], dia)

    # 5) Zeilen bauen
    report("Zeilen bauen", 0, "Zeilen erstellen")
    env = segment.envelope_db(voc16)
    np.save(d / "stimmen_env.npy", env.astype(np.float32))
    lines = segment.refine_bounds(segment.build_lines(words), env)
    _apply_lines(data, lines)
    named = _names_from_text(data, words)   # „[Verse 2: Natalia]“ im vorgegebenen Text benennt die Figur
    if named and (data.get("reftext") or {}).get("report"):
        data["reftext"]["report"]["named"] = named

    # 6) Lachen erkennen (optional)
    if data["settings"].get("laugh", True):
        report("Lachen erkennen", 0, "Lach-Modell laden (beim ersten Mal Download ~350 MB)")
        _add_laughs(data, voc16, words, env, lambda p: report("Lachen erkennen", p, "Lachen suchen"))
    peaks = {"stimmen": segment.waveform_peaks(voc16), "fps": 100}
    (d / "wellenform.json").write_text(json.dumps(peaks), encoding="utf8")
    data["status"] = "fertig"
    save(pid, data)
    report("Fertig", 1, "Verarbeitung abgeschlossen")


def _add_laughs(data, voc16, words, env, on_progress):
    from app.pipeline import laugh

    from app.pipeline.transcribe import SOUND_TEXT
    # Laut-Zeilen „(…)“ zählen hier nicht als belegt: ist der Laut ein Lacher, wird die Zeile zu „(lacht)“
    sounds = [ln for ln in data["lines"] if ln.get("text") == SOUND_TEXT]
    others = [ln for ln in data["lines"] if ln.get("text") != SOUND_TEXT]
    found = laugh.find_laughs(voc16, others, words, env, data.get("language"), on_progress)
    rest = []
    for item in found:
        hit = next((ln for ln in sounds if min(ln["end"], item["end"]) - max(ln["start"], item["start"])
                    > 0.3 * (ln["end"] - ln["start"])), None)
        if hit:
            hit["text"] = item["text"]
        else:
            rest.append(item)
    found = rest
    for item in found:
        if not item["chars"]:
            if not data["characters"]:
                data["characters"] = _character_list(1, data["settings"].get("ui_lang"))
            item["chars"] = [data["characters"][0]["id"]]
        fitted = laugh.fit_into(data["lines"], item, data["duration"])
        if fitted:
            fitted.pop("score", None)
            data["lines"].append(dict(fitted, id=new_id()))
    data["lines"].sort(key=lambda ln: ln["start"])
    return len(found)


def find_laughs(pid, report):
    """Lach-Vorschläge für ein bestehendes Projekt (werden im Editor eingefügt)."""
    from app.pipeline import laugh

    d = project_dir(pid)
    data = load(pid)
    report("Lachen erkennen", 0, "Lach-Modell laden (beim ersten Mal Download ~350 MB)")
    voc16 = media.load_mono(d / "stimmen.wav")
    words = json.loads((d / "analyse.json").read_text(encoding="utf8")).get("words", [])
    env = np.load(d / "stimmen_env.npy")
    found = laugh.find_laughs(voc16, data["lines"], words, env, data.get("language"),
                              lambda p: report("Lachen erkennen", p, "Lachen suchen"))
    report("Lachen erkennen", 1, f"{len(found)} gefunden")
    return {"laughs": found}


def _save_analysis(d, words, segments, dia):
    np.save(d / "sprecher_embeddings.npy", dia["embeddings"])
    np.save(d / "fenster_embeddings.npy", dia["win_embeddings"])
    out = {k: v for k, v in dia.items() if k not in ("embeddings", "win_embeddings")}
    out["win_owner"] = [int(x) for x in out["win_owner"]]
    out.update(words=words, segments=segments)
    (d / "analyse.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf8")


def _load_analysis(d):
    an = json.loads((d / "analyse.json").read_text(encoding="utf8"))
    an["embeddings"] = np.load(d / "sprecher_embeddings.npy")
    an["win_embeddings"] = np.load(d / "fenster_embeddings.npy")
    return an


def norm_text(text):
    """Text für den Wiederholungs-Vergleich vereinheitlichen."""
    t = re.sub(r"[^\w\s]", "", (text or "").lower(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def mark_repeats(lines, tolerance=0.35, min_chars=4):
    """Gleiche Texte desselben Sprechers verketten: spätere Zeilen werden Wiederholung der ersten.

    Im Pack wird daraus ein Clip mit mehreren dub_timestamps: einmal aufnehmen, überall abgespielt.
    """
    groups = {}
    count = 0
    for ln in sorted(lines, key=lambda x: x["start"]):
        ln.pop("repeat_of", None)
        text = norm_text(ln.get("text"))
        dur = ln["end"] - ln["start"]
        if len(text) < min_chars or not ln.get("chars"):
            continue
        key = (ln["chars"][0], text)
        master = groups.get(key)
        if master is None:
            groups[key] = ln
            continue
        mdur = master["end"] - master["start"]
        if abs(dur - mdur) <= tolerance * max(dur, mdur):
            ln["repeat_of"] = master["id"]
            count += 1
        else:
            groups[key] = ln  # zu unterschiedlich lang -> eigene Aufnahme
    return count


def repeats_of(lines, master_id):
    return [ln for ln in lines if ln.get("repeat_of") == master_id]


def find_repeats(pid):
    data = load(pid)
    count = mark_repeats(data["lines"])
    save(pid, data)
    return count


def _names_from_text(data, words):
    """Figuren nach den Namen benennen, die im vorgegebenen Text stehen. -> Anzahl benannter Figuren"""
    hints = sorted((w for w in words if w.get("who")), key=lambda w: w["s"])
    if not hints:
        return 0
    starts = [w["s"] for w in hints]
    per = {}
    for ln in data["lines"]:
        lo = bisect.bisect_left(starts, ln["start"] - 0.2)
        hi = bisect.bisect_right(starts, ln["end"] + 0.2)
        names = [hints[k]["who"] for k in range(lo, hi)]
        if not names:
            continue
        top = Counter(names).most_common(1)[0][0]
        for cid in ln["chars"]:
            per.setdefault(cid, []).append(top)
    taken, n = set(), 0
    for cid, names in per.items():
        top, cnt = Counter(names).most_common(1)[0]
        # nur bei klarer Mehrheit, und jeder Name nur einmal: sonst heißen zwei Figuren gleich
        if len(names) >= 2 and cnt >= 0.6 * len(names) and top.casefold() not in taken:
            ch = next((c for c in data["characters"] if c["id"] == cid), None)
            if ch:
                ch["name"] = top
                taken.add(top.casefold())
                n += 1
    return n


def _apply_lines(data, lines):
    used = sorted({ln["spk"] for ln in lines})
    remap = {spk: i for i, spk in enumerate(used)}
    data["characters"] = _character_list(len(used), data["settings"].get("ui_lang"))
    data["lines"] = [{"id": new_id(), "start": ln["start"], "end": ln["end"], "text": ln["text"],
                      "chars": [f"c{remap[ln['spk']] + 1}"]} for ln in lines]
    mark_repeats(data["lines"])


def voice_similarity(pid):
    """Wie ähnlich klingen die Charaktere? {Charakter: [[anderer, Ähnlichkeit 0..1], …]} absteigend.

    Nutzt die gespeicherten Stimmprofile der Phrasen; jede Phrase zählt für den Charakter der Zeile,
    die sie am meisten überdeckt (also auch nach Änderungen im Editor). Hilft beim Zusammenführen:
    Voicitool trennt lieber zu viele Sprecher, die ähnlichste Stimme ist oft dieselbe Person.
    """
    d = project_dir(pid)
    f = d / "sprecher_embeddings.npy"
    if not f.exists() or not (d / "analyse.json").exists():
        return {}
    data = load(pid)
    E = np.load(f)
    phrases = json.loads((d / "analyse.json").read_text(encoding="utf8")).get("phrases") or []
    if len(phrases) != len(E):
        return {}
    E = E / (np.linalg.norm(E, axis=1, keepdims=True) + 1e-9)
    acc, weight = {}, {}
    for p, v in zip(phrases, E):
        best, ov = None, 0.0
        for ln in data["lines"]:
            o = min(ln["end"], p["e"]) - max(ln["start"], p["s"])
            if o > ov and ln.get("chars"):
                best, ov = ln["chars"][0], o
        if best is None:
            continue
        w = min(p["e"] - p["s"], 6.0)
        acc[best] = acc.get(best, 0) + v * w
        weight[best] = weight.get(best, 0) + w
    cents = {c: acc[c] / (np.linalg.norm(acc[c]) + 1e-9) for c in acc if weight[c] >= 0.5}
    out = {}
    for a, ca in cents.items():
        sims = [[b, round(float(max(0.0, ca @ cb)), 3)] for b, cb in cents.items() if b != a]
        out[a] = sorted(sims, key=lambda x: -x[1])
    return out


def recluster(pid, n_speakers):
    """Sprecher neu zuordnen, Zeilen/Texte bleiben erhalten."""
    from app.pipeline import diarize

    d = project_dir(pid)
    data = load(pid)
    an = _load_analysis(d)
    an.update(diarize.assign(an["words"], an, n_speakers or None))
    _save_analysis(d, an["words"], an["segments"], an)

    spk_of_line = [diarize.speaker_for_range(an["final_phrases"], an["final_labels"], ln["start"], ln["end"])
                   for ln in data["lines"]]
    used = []
    for s in spk_of_line:
        if s not in used:
            used.append(s)
    data["characters"] = _character_list(len(used), data["settings"].get("ui_lang"))
    for ln, s in zip(data["lines"], spk_of_line):
        ln["chars"] = [f"c{used.index(s) + 1}"]
    data["settings"]["speakers"] = n_speakers or None
    save(pid, data)
    return data


def resegment(pid, target_len, pause_split):
    """Zeilen komplett neu aus der Analyse aufbauen (verwirft manuelle Änderungen)."""
    from app.pipeline import segment

    d = project_dir(pid)
    data = load(pid)
    analyse = json.loads((d / "analyse.json").read_text(encoding="utf8"))
    env = np.load(d / "stimmen_env.npy")
    old_names = {c["id"]: c for c in data["characters"]}
    lines = segment.refine_bounds(
        segment.build_lines(analyse["words"], pause_split=pause_split, target_len=target_len,
                            max_len=max(target_len * 1.7, target_len + 3)), env)
    _apply_lines(data, lines)
    # Namen/Farben/Bilder bestehender Charaktere mit gleicher ID übernehmen
    for c in data["characters"]:
        if c["id"] in old_names:
            c.update({k: old_names[c["id"]][k] for k in ("name", "color", "image")})
    save(pid, data)
    return data


def retranscribe_line(pid, start, end, language):
    from app.pipeline import transcribe

    d = project_dir(pid)
    quality = load(pid)["settings"].get("quality")
    start = max(0.0, start - 0.1)
    audio = media.load_mono(d / "stimmen.wav", start=start, dur=(end - start) + 0.2)
    return transcribe.transcribe_range(audio, None if language in (None, "auto", "mixed") else language, quality)


def rename(pid, name, allow_folder=True):
    """Projekt umbenennen. Ordner wird mit umbenannt, falls möglich. Gibt die (neue) ID zurück."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    if not re.sub(r'[<>:"/\\|?*\x00-\x1f.\s]', "", name):
        raise ValueError("Name darf nicht leer sein")
    data = load(pid)
    data["name"] = name
    save(pid, data)
    new_pid = _free_id(name, own=pid)
    if allow_folder and new_pid != pid:
        try:
            project_dir(pid).rename(config.PROJECTS_DIR / new_pid)
            return new_pid
        except OSError:
            pass  # Datei gerade in Benutzung -> Ordner behält alten Namen
    return pid


def set_settings(pid, **kw):
    data = load(pid)
    data["settings"].update({k: v for k, v in kw.items() if v is not None})
    save(pid, data)


def mark_interrupted():
    """Beim Serverstart: Projekte, deren Verarbeitung nicht zu Ende lief, als abgebrochen markieren."""
    for d in config.PROJECTS_DIR.iterdir():
        f = d / "project.json"
        if not f.exists():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf8"))
        except Exception:
            continue
        if data.get("status") in ("wartet", "verarbeitet"):
            data["status"] = "abgebrochen"
            save(d.name, data)


def delete(pid):
    """Projekt löschen: Das Quellvideo wandert zurück nach 'eingang'."""
    d = project_dir(pid)
    data = load(pid)
    src = d / data["source"]
    if src.exists():
        target = config.INBOX_DIR / (slugify(data["name"]) + src.suffix)
        i = 2
        while target.exists():
            target = config.INBOX_DIR / f"{slugify(data['name'])} ({i}){src.suffix}"
            i += 1
        shutil.move(str(src), target)
    shutil.rmtree(d)
