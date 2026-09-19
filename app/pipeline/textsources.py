"""Quellen für „Text vorgeben“: Liedtexte, Transkripte und Untertitel suchen und als Klartext holen.

  Datei     Untertitel, die in der Videodatei selbst stecken (MKV, MP4): passen exakt zum Video
  YouTube   Untertitel des Uploaders (Adresse wird beim Herunterladen gemerkt), automatische nur nachrangig
  LRCLIB    freie Liedtext-Datenbank (lrclib.net), viele Sprachen, oft mit Zeitstempeln je Zeile
  lyrics.ovh  Liedtexte über „Interpret - Titel“
  Fandom    Transkripte von Serien aus den Fandom-Wikis (z. B. familyguy.fandom.com), über deren API
Alle Quellen werden gleichzeitig abgefragt; fällt eine aus, kommen die anderen trotzdem.
"""
import html
import json
import re
import subprocess
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from app import config
from app.pipeline import media

UA = {"User-Agent": f"Voicitool/{config.APP_BUILD} (https://github.com/{config.UPDATE_REPO or 'Jan1653/Voicitool'})"}
TIMEOUT = 15
SOURCES_FILE = config.DATA_DIR / "eingang_quellen.json"   # Dateiname im Eingang -> Adresse, Titel (YouTube-Downloads)
TEXT_SUBS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}


def _get(url, timeout=TIMEOUT):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode("utf8", errors="replace")


def _preview(text, n=2):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return " / ".join(lines[:n])[:140]


def _fmt_len(sec):
    return f"{int(sec // 60)}:{int(sec % 60):02d}" if sec else ""


# ------------------------------------------------------------------ Untertitel-Formate
TIME_LINE = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3}\s*-->")


def subs_to_text(raw):
    """SRT/VTT/ASS in Klartext: eine Zeile je Einblendung, ohne Zeiten, Nummern und Formatierung.

    Mehrere Zeilen einer Einblendung sind nur umbrochen und werden eine Zeile. Ausnahme: Dialog-Striche
    („- Hi.“ / „- Hallo.“), das sind zwei Sprecher. YouTube wiederholt beim Weiterrollen die vorige Zeile, die fällt weg."""
    out, cue, prev = [], [], set()

    def flush():
        nonlocal cue, prev
        if not cue:
            return
        lines = [x for x in cue if x not in prev]   # rollende Untertitel: schon gezeigte Zeilen nicht noch einmal
        prev = set(cue)
        cue = []
        if not lines:
            return
        if len(lines) > 1 and all(re.match(r"^[-–—]\s*\S", x) for x in lines):
            parts = [re.sub(r"^[-–—]\s*", "", x) for x in lines]
        else:
            parts = [" ".join(re.sub(r"^[-–—]\s+", "", x) if len(lines) == 1 else x for x in lines)]
        for x in parts:
            if x and (not out or out[-1] != x):
                out.append(x)

    for line in raw.replace("\r", "").split("\n"):
        s = line.strip()
        if not s or TIME_LINE.search(s):
            flush()
            continue
        if s.isdigit() or s.startswith(("WEBVTT", "NOTE", "Kind:", "Language:", "STYLE")):
            continue
        if s.startswith("Dialogue:"):          # ASS: Text steht nach dem 9. Komma, jede Zeile eine Einblendung
            s = s.split(",", 9)[-1]
            s = html.unescape(re.sub(r"\{[^}]*\}", "", s).replace("\\N", " ").replace("\\n", " ")).strip()
            flush()
            cue = [s] if s else []
            flush()
            continue
        if s.startswith(("[Script", "[V4", "[Events", "Format:", "Style:", "ScriptType", "PlayRes")):
            continue
        s = html.unescape(re.sub(r"\{[^}]*\}|<[^>]+>", "", s)).strip()
        if s:
            cue.append(s)
    flush()
    return "\n".join(out)


# ------------------------------------------------------------------ Datei
def embedded_subs(path):
    """Text-Untertitel aus der Videodatei (Liste von Treffern mit Text)."""
    try:
        info = subprocess.run([media.FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                              encoding="utf8", errors="replace", timeout=30).stderr
    except Exception:
        return []
    found, k = [], 0
    for m in re.finditer(r"Stream #\d+:\d+(?:\((\w+)\))?[^:]*: Subtitle: (\w+)(.*)", info):
        lang, codec, rest = m.group(1) or "?", m.group(2).lower(), m.group(3)
        idx, k = k, k + 1
        if codec not in TEXT_SUBS:
            continue
        try:
            raw = subprocess.run([media.FFMPEG, "-v", "error", "-i", str(path), "-map", f"0:s:{idx}", "-f", "srt", "-"],
                                 capture_output=True, text=True, encoding="utf8", errors="replace", timeout=60).stdout
        except Exception:
            continue
        text = subs_to_text(raw)
        if text:
            found.append({"source": "Datei", "id": f"sub{idx}", "title": f"Untertitel in der Videodatei ({lang})",
                          "subtitle": codec + (" · erzwungen" if "forced" in rest.lower() else ""), "text": text})
    return found


# ------------------------------------------------------------------ YouTube
def remember_source(filename, url, title):
    """Beim Herunterladen merken, woher eine Datei im Eingang kommt (für Untertitel und Suchvorschlag)."""
    try:
        data = json.loads(SOURCES_FILE.read_text(encoding="utf8")) if SOURCES_FILE.exists() else {}
    except Exception:
        data = {}
    data[filename] = {"url": url, "title": title}
    SOURCES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf8")


def remember_reftext(filename, text, source):
    """Beim Herunterladen mitgeholte Untertitel an die Datei im Eingang hängen (landen als „Text vorgeben“ im Formular)."""
    try:
        data = json.loads(SOURCES_FILE.read_text(encoding="utf8")) if SOURCES_FILE.exists() else {}
    except Exception:
        data = {}
    data.setdefault(filename, {})["reftext"] = {"text": text, "source": source}
    SOURCES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf8")


def files_with_reftext(files):
    """Welche dieser Dateien im Eingang haben mitgeholte Untertitel?"""
    try:
        data = json.loads(SOURCES_FILE.read_text(encoding="utf8")) if SOURCES_FILE.exists() else {}
    except Exception:
        return []
    return [f for f in files if (data.get(f) or {}).get("reftext")]


def uploader_subs(url):
    """Beste Untertitel vom Uploader (keine automatisch erzeugten: die sind oft schlechter als unsere eigene Erkennung
    und würden richtige Wörter überschreiben). Sprache: die Originalsprache des Videos, sonst die einzige Spur.
    -> {"text", "source", "lang"} oder None"""
    import yt_dlp
    from app.pipeline import download
    opts = download.ytdlp_base_options()
    opts.update({"skip_download": True, "quiet": True})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    subs = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}
    if not subs:
        return None
    orig = (info.get("language") or "").split("-")[0].lower()
    if not orig:   # YouTube kennzeichnet die Originalsprache bei den automatischen Untertiteln mit „-orig“
        orig = next((k[:-5].split("-")[0] for k in (info.get("automatic_captions") or {}) if k.endswith("-orig")), "")
    langs = sorted(subs, key=lambda k: (k.split("-")[0].lower() != orig, k))
    for lang in langs:
        fmts = subs[lang]
        f = next((x for x in fmts if x.get("ext") == "vtt"), None) or next((x for x in fmts if x.get("ext") in ("srv1", "ttml")), None)
        if not f:
            continue
        try:
            text = subs_to_text(_get(f["url"]))
        except Exception:
            continue
        if text:
            return {"text": text, "source": f"YouTube-Untertitel ({lang})", "lang": lang}
    return None


def source_info(filename):
    try:
        return json.loads(SOURCES_FILE.read_text(encoding="utf8")).get(filename)
    except Exception:
        return None


def youtube_subs(url):
    import yt_dlp
    from app.pipeline import download
    opts = download.ytdlp_base_options()
    opts.update({"skip_download": True, "quiet": True})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    out = []
    for kind, subs in (("vom Uploader", info.get("subtitles") or {}), ("automatisch erzeugt", info.get("automatic_captions") or {})):
        for lang, fmts in subs.items():
            if kind == "automatisch erzeugt" and ("-" in lang and not lang.endswith("-orig")):
                continue   # automatische Übersetzungen in andere Sprachen weglassen
            f = next((x for x in fmts if x.get("ext") == "vtt"), None) or next((x for x in fmts if x.get("ext") in ("srv1", "ttml")), None)
            if not f:
                continue
            try:
                text = subs_to_text(_get(f["url"]))
            except Exception:
                continue
            if text:
                out.append({"source": "YouTube", "id": f"{kind}:{lang}", "title": f"YouTube-Untertitel ({lang})",
                            "subtitle": kind, "text": text, "auto": kind != "vom Uploader"})
    return sorted(out, key=lambda r: r.get("auto", False))


# ------------------------------------------------------------------ Liedtexte
def lrclib(query):
    data = json.loads(_get("https://lrclib.net/api/search?q=" + urllib.parse.quote(query)))
    out = []
    for r in data[:10]:
        text = r.get("plainLyrics") or re.sub(r"\[\d+:\d+(?:\.\d+)?\]\s?", "", r.get("syncedLyrics") or "")
        if not text or r.get("instrumental"):
            continue
        out.append({"source": "LRCLIB", "id": str(r.get("id")), "title": r.get("trackName") or "?",
                    "subtitle": " · ".join(x for x in (r.get("artistName"), r.get("albumName"), _fmt_len(r.get("duration") or 0)) if x),
                    "text": text.strip()})
    return out


def lyrics_ovh(query):
    parts = re.split(r"\s+[-–—]\s+", query, maxsplit=1)
    if len(parts) != 2:
        return []
    for artist, title in (parts, parts[::-1]):   # „Interpret - Titel“ oder „Titel - Interpret“
        try:
            data = json.loads(_get(f"https://api.lyrics.ovh/v1/{urllib.parse.quote(artist)}/{urllib.parse.quote(title)}"))
        except Exception:
            continue
        text = (data.get("lyrics") or "").strip()
        if text:
            return [{"source": "lyrics.ovh", "id": f"{artist}|{title}", "title": title, "subtitle": artist, "text": text}]
    return []


# ------------------------------------------------------------------ Transkripte (Fandom-Wikis)
def _wiki_slugs(query):
    words = [w for w in re.findall(r"[A-Za-z0-9]+", query.lower()) if w not in ("the", "der", "die", "das", "folge", "episode")]
    return list(dict.fromkeys("".join(words[:n]) for n in (2, 1, 3) if len(words) >= n))[:3]


def fandom(query, lang=None):
    out = []
    for slug in _wiki_slugs(query):
        for path in ([f"/{lang}"] if lang and lang != "en" else []) + [""]:
            api = f"https://{slug}.fandom.com{path}/api.php"
            try:
                data = json.loads(_get(api + "?action=query&list=search&format=json&srlimit=8&srsearch="
                                       + urllib.parse.quote(query + " transcript"), timeout=8))
            except Exception:
                continue
            for r in data.get("query", {}).get("search", []):
                t = r["title"]
                full = re.search(r"transcript|script|drehbuch|skript", t, re.I)
                if full or re.search(r"/quotes|/zitate", t, re.I):   # manche Wikis haben nur Zitat-Seiten (Auszüge)
                    out.append({"source": "Fandom", "id": f"{api}|{t}", "title": t, "partial": not full,
                                "subtitle": f"{slug}.fandom.com{path}" + ("" if full else " · nur Auszüge"), "text": None})
        if out:
            break
    return sorted(out, key=lambda r: r["partial"])[:8]


def _wikitext_to_text(w):
    w = re.sub(r"<ref[^>]*>.*?</ref>|<!--.*?-->", "", w, flags=re.S)
    # Dialog-Bausteine wie {{L|SpongeBob|Text}} (SpongeBob-Wiki) -> „SpongeBob: Text“, reine Regie {{L|[…]}} fällt weg
    w = re.sub(r"\{\{\s*(?:L|Line|Dialogue|Dialog|Quote|T)\s*\|([^|{}\[\]]{1,40})\|([^{}]*)\}\}", r"\1: \2", w)
    w = re.sub(r"\{\{[^{}]*\}\}", "", w)
    w = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", w)
    w = re.sub(r"'{2,}", "", w)
    w = re.sub(r"<[^>]+>", "", w)
    lines = []
    for line in w.split("\n"):
        s = line.strip()
        if not s or s.startswith(("=", "{|", "|", "!", "[[Category", "__")):
            continue
        s = s.lstrip(":*# ").strip()
        if s:
            lines.append(html.unescape(s))
    return "\n".join(lines)


def fetch_fandom(ident):
    api, title = ident.split("|", 1)
    data = json.loads(_get(api + "?action=parse&prop=wikitext&format=json&page=" + urllib.parse.quote(title)))
    return _wikitext_to_text(data.get("parse", {}).get("wikitext", {}).get("*", ""))


# ------------------------------------------------------------------ Suche
def search(query, filename=None, lang=None):
    """Alle Quellen gleichzeitig fragen. -> {"results": [...], "failed": [Quelle, …]}"""
    query = (query or "").strip()
    jobs, results, failed = {}, [], []
    with ThreadPoolExecutor(max_workers=6) as ex:
        if filename:
            from app.pipeline import project
            try:
                path = project.inbox_file(filename)
                jobs["Datei"] = ex.submit(embedded_subs, path)
            except Exception:
                pass
            info = source_info(filename)
            if info and info.get("url"):
                jobs["YouTube"] = ex.submit(youtube_subs, info["url"])
        if query:
            jobs["LRCLIB"] = ex.submit(lrclib, query)
            jobs["lyrics.ovh"] = ex.submit(lyrics_ovh, query)
            jobs["Fandom"] = ex.submit(fandom, query, lang)
        for name, fut in jobs.items():
            try:
                results += fut.result(timeout=TIMEOUT + 20)
            except Exception:
                failed.append(name)
    for r in results:
        if r.get("text"):
            r["preview"] = _preview(r["text"])
            r["lines"] = len([l for l in r["text"].splitlines() if l.strip()])
    return {"results": results, "failed": failed}


def fetch(source, ident):
    if source == "Fandom":
        return fetch_fandom(ident)
    raise ValueError("Unbekannte Quelle")
