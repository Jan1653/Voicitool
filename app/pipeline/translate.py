"""Zeilentexte in eine andere Sprache bringen (für Packs in mehreren Sprachen).

Übersetzt wird nur der Text, den die Spieler beim Nachsprechen lesen. Der Ton bleibt, wie er ist.
Drei Wege, je nachdem, was für das Projekt da ist:

  YouTube    Untertitel des Videos in der Zielsprache, auch die von YouTube automatisch übersetzten.
             Kostenlos und ohne Konto, geht nur bei Videos, die von YouTube geladen wurden.
  Online     Gemini oder Groq mit dem eigenen Gratis-Schlüssel (siehe online.py). Beste Qualität, weil
             der Dienst alle Zeilen im Zusammenhang sieht.
  Kostenlos  MyMemory, ohne Konto. Zeile für Zeile, mit Tageslimit, dafür überall verfügbar.

Der Originaltext bleibt in der Zeile als „text_src“ stehen: eine zweite Übersetzung geht dadurch
wieder vom Original aus, und das Original lässt sich jederzeit zurückholen.
"""
import html
import json
import re
import time
import urllib.parse
import urllib.request

from app import config
from app.pipeline import online, textsources

# Sprachen für die Auswahl. Der Schlüssel ist der Sprachcode, wie ihn YouTube und die Dienste kennen.
LANGS = {
    "en": "Englisch", "de": "Deutsch", "es": "Spanisch", "fr": "Französisch", "it": "Italienisch",
    "pt": "Portugiesisch", "nl": "Niederländisch", "pl": "Polnisch", "ru": "Russisch", "uk": "Ukrainisch",
    "tr": "Türkisch", "sv": "Schwedisch", "da": "Dänisch", "no": "Norwegisch", "fi": "Finnisch",
    "cs": "Tschechisch", "hu": "Ungarisch", "ro": "Rumänisch", "el": "Griechisch", "ar": "Arabisch",
    "he": "Hebräisch", "hi": "Hindi", "id": "Indonesisch", "vi": "Vietnamesisch", "th": "Thailändisch",
    "ja": "Japanisch", "ko": "Koreanisch", "zh": "Chinesisch",
}

BATCH = 30            # so viele Zeilen gehen in eine Anfrage an den Online-Dienst
MYMEMORY_GAP = 0.4    # Pause zwischen zwei Anfragen an den kostenlosen Dienst (s)
MYMEMORY_MAX = 480    # längere Zeilen werden dort abgeschnitten
UA = {"User-Agent": f"Voicitool/{config.APP_BUILD} (+https://github.com/{config.UPDATE_REPO or 'Jan1653/Voicitool'})"}

# „latest“ zeigt immer auf das aktuelle Flash-Modell; die anderen sind Rückfallebenen,
# falls Google die Namen wieder umstellt
GEMINI_MODELS = ("gemini-flash-latest", "gemini-3.5-flash", "gemini-2.5-flash")
GROQ_MODEL = "llama-3.3-70b-versatile"


class TranslateError(RuntimeError):
    pass


def lang_name(code):
    return LANGS.get((code or "").split("-")[0].lower(), code or "?")


# ------------------------------------------------------------------ Wege


def methods(data):
    """Welche Wege es für dieses Projekt gibt. -> [{id, name, hint, ready}]"""
    keys = online.load()
    service = "gemini" if online.configured("gemini", keys) else ("groq" if online.configured("groq", keys) else None)
    url = _source_url(data)
    return [
        {"id": "youtube", "name": "Untertitel von YouTube", "ready": bool(url),
         "hint": "Die Untertitel des Videos in der Zielsprache, auch die von YouTube übersetzten. Kostenlos, "
                 "klappt aber nicht immer: YouTube gibt sie nicht jedem heraus."
                 if url else "Nur für Videos, die aus YouTube geladen wurden."},
        {"id": "online", "name": "Online übersetzen", "service": online.NAMES[service] if service else "",
         "ready": bool(service),
         "hint": "Beste Qualität: der Dienst sieht alle Zeilen im Zusammenhang." if service
                 else "Dafür erst unter Einstellungen einen Gratis-Schlüssel für Gemini oder Groq eintragen."},
        {"id": "free", "name": "Kostenlos ohne Konto", "ready": True,
         "hint": "MyMemory übersetzt Zeile für Zeile. Braucht kein Konto, hat aber ein Tageslimit."},
    ]


def _source_url(data):
    """Adresse, von der das Video geladen wurde (steht beim Anlegen im Projekt, nur bei Downloads)."""
    url = str(data.get("source_url") or "")
    return url if re.search(r"youtu\.?be", url, re.I) else ""


# ------------------------------------------------------------------ Untertitel von YouTube


def _cues(raw):
    """Untertitel mit Zeiten lesen (VTT oder SRT). -> [(start, ende, text)]"""
    out = []
    start = end = None
    buf = []

    def flush():
        nonlocal start, end, buf
        text = " ".join(x for x in buf if x).strip()
        if start is not None and text:
            out.append((start, end, text))
        buf = []

    for line in str(raw).replace("\r", "").split("\n"):
        s = line.strip()
        m = re.match(r"(\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3})", s)
        if m:
            flush()
            start, end = _secs(m.group(1)), _secs(m.group(2))
            continue
        if not s or s.startswith(("WEBVTT", "NOTE", "Kind:", "Language:", "STYLE")) or s.isdigit():
            continue
        buf.append(html.unescape(re.sub(r"<[^>]+>", "", s)).strip())
    flush()
    return out


def _secs(t):
    parts = t.replace(",", ".").split(":")
    parts = ["0"] * (3 - len(parts)) + parts
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def youtube_track(url, lang):
    """Untertitelspur in der Zielsprache holen. -> (Cues, Beschreibung) oder (None, Grund)"""
    # Über yt-dlp selbst holen: YouTube lehnt einfache Anfragen auf die Untertitel oft ab (429)
    import yt_dlp
    from app.pipeline import download
    opts = download.ytdlp_base_options()
    opts.update({"skip_download": True, "quiet": True, "retries": 2})
    try:
        ydl = yt_dlp.YoutubeDL(opts)
        info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise TranslateError(f"Das Video ließ sich bei YouTube nicht abfragen: {e}")
    subs = info.get("subtitles") or {}
    caps = info.get("automatic_captions") or {}

    def pick(store):
        if lang in store:
            return store[lang], lang
        for key in store:
            if key.split("-")[0].lower() == lang:
                return store[key], key
        return None, None

    fmts, key = pick(subs)
    kind = "vom Uploader"
    if not fmts:
        fmts, key = pick(caps)
        kind = "von YouTube übersetzt"
    if not fmts:
        return None, f"Für {lang_name(lang)} gibt es dort keine Untertitel."
    f = next((x for x in fmts if x.get("ext") == "vtt"), None) or next((x for x in fmts if x.get("ext") in ("srv1", "ttml")), None)
    if not f:
        return None, "Die Untertitel liegen in einem unbekannten Format vor."
    try:
        raw = ydl.urlopen(f["url"]).read().decode("utf8", "replace")
    except Exception as e:
        if "429" in str(e):
            raise TranslateError("YouTube gibt die Untertitel gerade nicht heraus (zu viele Anfragen von dieser "
                                 "Adresse). Nimm so lange einen der anderen Wege.")
        raise TranslateError(f"Die Untertitel ließen sich nicht laden: {e}")
    cues = _cues(raw)
    if not cues:
        return None, "Die Untertitel waren leer."
    return cues, f"Untertitel {kind} ({key})"


def from_cues(lines, cues):
    """Zu jeder Zeile den Untertiteltext aus demselben Zeitraum. Leer, wo nichts passt."""
    out = []
    for ln in lines:
        a, b = float(ln["start"]), float(ln["end"])
        hits = []
        for s, e, text in cues:
            if min(b, e) - max(a, s) > 0.15 * min(1.0, max(0.2, b - a)):   # deutliche Überlappung
                if not hits or hits[-1] != text:
                    hits.append(text)
        out.append(" ".join(hits).strip())
    return out


# ------------------------------------------------------------------ Online übersetzen (Gemini, Groq)


def _prompt(target):
    return (f"Übersetze die Zeilen in {lang_name(target)}. Es sind Untertitel aus einem Video, die später "
            "nachgesprochen werden: übersetze natürlich und ungefähr gleich lang, behalte Eigennamen, Ausrufe "
            "und die Anrede bei. Antworte nur mit JSON im Format {\"1\": \"…\", \"2\": \"…\"} mit genau denselben "
            "Nummern wie in der Eingabe, ohne weiteren Text.")


def _llm_batch(service, target, chunk):
    """chunk: [(nummer, text)] -> {nummer: übersetzt}"""
    payload_in = json.dumps({str(n): t for n, t in chunk}, ensure_ascii=False)
    keys = online.load()
    if service == "gemini":
        key = (keys.get("gemini") or {}).get("key", "")
        body = {"contents": [{"parts": [{"text": _prompt(target) + "\n\n" + payload_in}]}],
                "generationConfig": {"temperature": 0.2, "response_mime_type": "application/json"}}
        data = None
        for i, model in enumerate(GEMINI_MODELS):
            url = f"{online.GEMINI_API}/v1beta/models/{model}:generateContent?key={urllib.parse.quote(key)}"
            try:
                data = online._json("gemini", "POST", url, {}, body, timeout=180)
                break
            except Exception:
                if i == len(GEMINI_MODELS) - 1:   # auch das letzte Modell gibt es nicht
                    raise
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            raise TranslateError("Gemini hat nichts zurückgeschickt.")
    else:
        key = (keys.get("groq") or {}).get("key", "")
        body = {"model": GROQ_MODEL, "temperature": 0.2, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": _prompt(target)},
                             {"role": "user", "content": payload_in}]}
        data = online._json("groq", "POST", f"{online.GROQ_API}/chat/completions",
                            {"Authorization": f"Bearer {key}"}, body, timeout=180)
        try:
            text = data["choices"][0]["message"]["content"]
        except Exception:
            raise TranslateError("Groq hat nichts zurückgeschickt.")
    try:
        got = json.loads(re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip())
    except Exception:
        raise TranslateError("Die Antwort des Dienstes war kein JSON.")
    return {str(k): str(v) for k, v in got.items()} if isinstance(got, dict) else {}


def online_translate(texts, target, on_progress=None):
    service = "gemini" if online.configured("gemini") else ("groq" if online.configured("groq") else None)
    if not service:
        raise TranslateError("Dafür fehlt der Gratis-Schlüssel für Gemini oder Groq.")
    todo = [(i + 1, t) for i, t in enumerate(texts) if t.strip()]
    out = dict.fromkeys(range(1, len(texts) + 1), "")
    for k in range(0, len(todo), BATCH):
        chunk = todo[k:k + BATCH]
        got = _llm_batch(service, target, chunk)
        for n, _ in chunk:
            out[n] = got.get(str(n), "")
        if on_progress:
            on_progress(min(1.0, (k + len(chunk)) / max(1, len(todo))))
    return [out[i + 1] for i in range(len(texts))]


# ------------------------------------------------------------------ Kostenlos ohne Konto (MyMemory)


def free_translate(texts, source, target, on_progress=None):
    out = []
    pair = f"{(source or 'en').split('-')[0]}|{target}"
    for i, t in enumerate(texts):
        s = t.strip()
        if not s:
            out.append("")
            continue
        q = urllib.parse.urlencode({"q": s[:MYMEMORY_MAX], "langpair": pair, "de": "voicitool@example.com"})
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    f"https://api.mymemory.translated.net/get?{q}", headers=UA), timeout=25) as r:
                data = json.loads(r.read().decode("utf8", "replace"))
        except Exception as e:
            raise TranslateError(f"Der kostenlose Dienst antwortet nicht mehr: {e}")
        status = data.get("responseStatus")
        if str(status) not in ("200", "OK"):
            detail = str(data.get("responseDetails") or "")
            if "LIMIT" in detail.upper() or str(status) == "429":
                raise TranslateError("Das Tageslimit des kostenlosen Dienstes ist erreicht. "
                                     "Morgen wieder, oder online mit eigenem Schlüssel übersetzen.")
            raise TranslateError(f"Der kostenlose Dienst meldet: {detail or status}")
        out.append(html.unescape(str(data.get("responseData", {}).get("translatedText") or "")).strip())
        if on_progress:
            on_progress((i + 1) / max(1, len(texts)))
        time.sleep(MYMEMORY_GAP)
    return out


# ------------------------------------------------------------------ Alles zusammen


def run(data, target, method, on_progress=None):
    """Alle Zeilen übersetzen. -> {"texts": [...], "source": Beschreibung, "leer": Anzahl ohne Ergebnis}"""
    target = (target or "").split("-")[0].lower()
    if target not in LANGS:
        raise TranslateError("Diese Sprache kenne ich nicht.")
    lines = data.get("lines") or []
    if not lines:
        raise TranslateError("Das Projekt hat noch keine Zeilen.")
    # Immer vom Original ausgehen, nicht von einer schon übersetzten Fassung
    texts = [str(ln.get("text_src") or ln.get("text") or "") for ln in lines]
    prog = (lambda p: on_progress(p)) if on_progress else None

    if method == "youtube":
        url = _source_url(data)
        if not url:
            raise TranslateError("Dieses Video kam nicht von YouTube.")
        cues, note = youtube_track(url, target)
        if not cues:
            raise TranslateError(note)
        got = from_cues(lines, cues)
        source = note
    elif method == "online":
        got = online_translate(texts, target, prog)
        source = f"Online übersetzt ({online.NAMES['gemini' if online.configured('gemini') else 'groq']})"
    else:
        got = free_translate(texts, data.get("language") or "auto", target, prog)
        source = "MyMemory"

    empty = sum(1 for i, t in enumerate(got) if texts[i].strip() and not t.strip())
    return {"texts": got, "source": source, "leer": empty}
