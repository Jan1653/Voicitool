"""Online rechnen: Stimmen trennen (MVSEP) und Sprache erkennen (Groq, Cloudflare oder Gemini) bei Gratis-Diensten.

Für PCs ohne passende Grafikkarte, dort dauern beide Schritte lokal sehr lange. Jeder Nutzer braucht eigene
Gratis-Konten. Die Schlüssel liegen nur auf diesem PC (daten/online.json), die Oberfläche sieht sie nur gekürzt.
Sprecher, Lachen und alles andere bleibt lokal.

  MVSEP       BS-RoFormer wie lokal, Gratiskonto: 50 Trennungen am Tag, höchstens 10 min je Datei
  Groq        Whisper large-v3, Gratis: 8 Stunden Ton am Tag, Wortzeiten
  Cloudflare  Whisper large-v3-turbo über Workers AI, Gratis: etwa 3,5 Stunden am Tag, Wortzeiten
  Gemini      Gemini 3.5 Transcribe, Wortzeiten und Sprecher; Gratis-Daten darf Google auswerten (außer EU/UK/CH)
"""
import base64
import json
import math
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from difflib import SequenceMatcher

import numpy as np
import soundfile as sf

from app import config
from app.pipeline import media

KEYS_FILE = config.DATA_DIR / "online.json"
UA = f"Voicitool/{config.APP_BUILD} (+https://github.com/{config.UPDATE_REPO or 'Jan1653/Voicitool'})"
NAMES = {"mvsep": "MVSEP", "groq": "Groq", "cloudflare": "Cloudflare", "gemini": "Gemini"}
FIELDS = {"mvsep": ("token",), "groq": ("key",), "cloudflare": ("account", "token"), "gemini": ("key",)}
ASR = ("groq", "cloudflare", "gemini")
_lock = threading.Lock()

MVSEP_API = "https://mvsep.com/api"
MVSEP_MODEL = "81"          # BS Roformer ver 2025.07 (Standard bei MVSEP, besser als unser lokales Modell)
MVSEP_MAX = 9.5 * 60        # Gratiskonto: höchstens 10 min je Datei, längere Tonspuren in Teilen
MVSEP_OVERLAP = 6.0         # Überlappung der Teile, dort wird überblendet (s)
MVSEP_TIMEOUT = 45 * 60     # so lange höchstens auf ein Ergebnis warten (Warteschlange)

GROQ_API = "https://api.groq.com/openai/v1"
GROQ_MODEL = "whisper-large-v3"
CF_API = "https://api.cloudflare.com/client/v4"
CF_MODEL = "@cf/openai/whisper-large-v3-turbo"
GEMINI_API = "https://generativelanguage.googleapis.com"
GEMINI_MODEL = "gemini-3.5-transcribe"
CHUNK = {"groq": 60, "cloudflare": 60, "gemini": 25 * 60}   # längster Abschnitt je Anfrage (s): kurze Stücke erkennen genauer
#   (Among Us, 12 min: Groq am Stück 18,2 %, 4 min 16,6 %, 1 min 15,4 % Wortfehler; Cloudflare 4 min 16,4 %, 1 min 15,4 %)
SR = 16000


class OnlineError(RuntimeError):
    def __init__(self, text, code="online"):
        super().__init__(text)
        self.code = code


# ------------------------------------------------------------------ Schlüssel und Einstellungen
def load():
    try:
        return json.loads(KEYS_FILE.read_text(encoding="utf8"))
    except Exception:
        return {}


def save(values):
    """Werte übernehmen. Dienste: {"groq": {"key": "…"}}, leerer Text entfernt einen Schlüssel."""
    with _lock:
        cur = load()
        for k, v in values.items():
            if k in FIELDS and isinstance(v, dict):
                svc = cur.setdefault(k, {})
                for f in FIELDS[k]:
                    if f in v:
                        val = "".join(str(v[f] or "").split())
                        if val:
                            svc[f] = val
                        else:
                            svc.pop(f, None)
                        svc.pop("ok", None)   # neuer Schlüssel: Prüfung gilt nicht mehr
                if k == "gemini" and "accepted" in v:
                    svc["accepted"] = bool(v["accepted"])
            elif k == "asr" and v in ASR:
                cur["asr"] = v
            elif k == "prompted":
                cur["prompted"] = bool(v)
        KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEYS_FILE.write_text(json.dumps(cur, indent=1, ensure_ascii=False), encoding="utf8")
        return cur


def _mark(service, ok):
    with _lock:
        cur = load()
        if service in cur:
            cur[service]["ok"] = bool(ok)
            KEYS_FILE.write_text(json.dumps(cur, indent=1, ensure_ascii=False), encoding="utf8")


def configured(service, data=None):
    data = data if data is not None else load()
    svc = data.get(service) or {}
    if not all(svc.get(f) for f in FIELDS[service]):
        return False
    return service != "gemini" or bool(svc.get("accepted"))


def asr_service(data=None):
    """Welcher Dienst die Sprache erkennt: die Wahl, sonst der erste eingerichtete (Gemini nie von selbst)."""
    data = data if data is not None else load()
    want = data.get("asr")
    if want in ASR and configured(want, data):
        return want
    return next((s for s in ("groq", "cloudflare") if configured(s, data)), None)


def ready(data=None, asr=None):
    """Was online läuft. asr: Wahl für ein Video (ein Dienst oder "local" = Text auf diesem PC), sonst der Standard."""
    data = data if data is not None else load()
    if asr == "local":
        tr = None
    elif asr in ASR and configured(asr, data):
        tr = asr
    else:
        tr = asr_service(data)
    return {"separate": configured("mvsep", data), "transcribe": tr}


def _mask(v):
    v = str(v or "")
    return "" if not v else ("…" + v[-4:] if len(v) > 8 else "gesetzt")


def status():
    """Für die Oberfläche: was eingerichtet ist, Schlüssel nur gekürzt."""
    data = load()
    out = {"services": {}, "asr": data.get("asr") or asr_service(data), "prompted": bool(data.get("prompted")),
           "ready": ready(data)}
    for s, fields in FIELDS.items():
        svc = data.get(s) or {}
        out["services"][s] = {"set": configured(s, data), "fields": {f: _mask(svc.get(f)) for f in fields},
                              "ok": svc.get("ok"), "accepted": bool(svc.get("accepted")) if s == "gemini" else None}
    return out


def _key(service, field):
    v = (load().get(service) or {}).get(field)
    if not v:
        raise OnlineError(f"{NAMES[service]} ist nicht eingerichtet (Einstellungen → Online rechnen).", "online_setup")
    return v


# ------------------------------------------------------------------ HTTP
def _retry_after(head):
    try:
        return float(head.get("retry-after"))
    except (TypeError, ValueError):
        return None


def _http(service, method, url, headers=None, data=None, timeout=120):
    """Anfrage; bei 429 (Minutenlimit, z. B. Groq 20 Anfragen je Minute) kurz warten und erneut versuchen."""
    for attempt in range(4):
        status, body, head = _http_once(service, method, url, headers, data, timeout)
        if status != 429 or attempt == 3:
            return status, body, head
        wait = _retry_after(head)
        if wait is None:
            wait = 5 * (attempt + 1)
        wait = max(0.0, wait)
        if wait > 60:   # Tageslimit: Warten lohnt nicht
            return status, body, head
        time.sleep(wait + 0.5)


def _http_once(service, method, url, headers=None, data=None, timeout=120):
    req = urllib.request.Request(url, data=data, method=method, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except Exception:
            body = b""
        return e.code, body, {k.lower(): v for k, v in (e.headers or {}).items()}
    except (urllib.error.URLError, TimeoutError, OSError):
        raise OnlineError(f"{NAMES[service]} ist nicht erreichbar. Prüfe die Internetverbindung.", "net")


def _message(body):
    try:
        j = json.loads(body)
    except Exception:
        return body[:160].decode("utf8", "replace").strip()
    for path in (("error", "message"), ("data", "message"), ("errors", 0, "message"), ("message",), ("error",)):
        cur = j
        try:
            for p in path:
                cur = cur[p]
            if isinstance(cur, str):
                return cur[:200]
        except (KeyError, IndexError, TypeError):
            continue
    return ""


def _check(service, status, body):
    """HTTP-Status in eine verständliche Meldung umsetzen."""
    if 200 <= status < 300:
        return
    name = NAMES[service]
    if status in (401, 403):
        _mark(service, False)
        raise OnlineError(f"{name} nimmt den Schlüssel nicht an. Prüfe ihn unter Einstellungen → Online rechnen.", "online_key")
    if status == 429:
        raise OnlineError(f"Das Gratis-Kontingent bei {name} ist gerade aufgebraucht. Versuche es später noch einmal "
                          "oder rechne auf diesem PC.", "online_quota")
    if status == 413:
        raise OnlineError(f"Die Tonspur ist für {name} zu groß.", "online")
    msg = _message(body)
    raise OnlineError(f"{name} meldet einen Fehler ({status}){': ' + msg if msg else ''}.", "online")


def _json(service, method, url, headers=None, payload=None, timeout=120):
    data = None
    headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf8")
        headers["Content-Type"] = "application/json"
    status, body, _ = _http(service, method, url, headers, data, timeout)
    _check(service, status, body)
    try:
        return json.loads(body or b"{}")
    except Exception:
        raise OnlineError(f"{NAMES[service]} hat eine unerwartete Antwort geschickt.", "online")


def _multipart(fields, files):
    """fields: [(name, wert)], files: [(name, dateiname, bytes, typ)] -> (body, content-type)"""
    boundary = "----Voicitool" + uuid.uuid4().hex
    out = bytearray()
    for name, value in fields:
        out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n').encode("utf8")
    for name, filename, data, ctype in files:
        out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f'Content-Type: {ctype}\r\n\r\n').encode("utf8")
        out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode("utf8")
    return bytes(out), f"multipart/form-data; boundary={boundary}"


# ------------------------------------------------------------------ Schlüssel prüfen
def check(service):
    """Kleine Anfrage, die nichts vom Kontingent verbraucht. -> {"ok", "text"}"""
    try:
        if service == "mvsep":
            j = _json(service, "GET", f"{MVSEP_API}/app/user?" + urllib.parse.urlencode({"api_token": _key(service, "token")}))
            if not j.get("success"):
                raise OnlineError("MVSEP nimmt den Schlüssel nicht an. Prüfe ihn unter Einstellungen → Online rechnen.", "online_key")
            name = (j.get("data") or {}).get("name") or ""
            text = f"Verbunden als {name}." if name else "Verbunden."
        elif service == "groq":
            _json(service, "GET", f"{GROQ_API}/models", {"Authorization": f"Bearer {_key(service, 'key')}"})
            text = "Verbunden."
        elif service == "cloudflare":
            acc, tok = _key(service, "account"), _key(service, "token")
            _json(service, "GET", f"{CF_API}/accounts/{urllib.parse.quote(acc)}/ai/models/search?per_page=1",
                  {"Authorization": f"Bearer {tok}"})
            text = "Verbunden."
        elif service == "gemini":
            _json(service, "GET", f"{GEMINI_API}/v1beta/models?pageSize=1", {"x-goog-api-key": _key(service, "key")})
            text = "Verbunden."
        else:
            raise OnlineError("Unbekannter Dienst.")
    except OnlineError as e:
        if e.code == "online_key":
            _mark(service, False)
        return {"ok": False, "text": str(e)}
    _mark(service, True)
    return {"ok": True, "text": text}


# ------------------------------------------------------------------ Stimmen trennen (MVSEP)
def _encode(src, dst, start=None, length=None, args=("-c:a", "flac")):
    cmd = [media.FFMPEG, "-y", "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src)]
    if length is not None:
        cmd += ["-t", f"{length:.3f}"]
    media.run(cmd + list(args) + [str(dst)])


def _parts(duration):
    """Abschnitte (Anfang, Ende) für MVSEP: höchstens MVSEP_MAX lang, mit Überlappung."""
    if duration <= MVSEP_MAX:
        return [(0.0, duration)]
    n = math.ceil((duration - MVSEP_OVERLAP) / (MVSEP_MAX - MVSEP_OVERLAP))
    step = (duration - MVSEP_OVERLAP) / n
    return [(i * step, min(duration, i * step + step + MVSEP_OVERLAP)) for i in range(n)]


def _mvsep_one(path, token, report, part_label):
    size = path.stat().st_size
    body, ctype = _multipart([("api_token", token), ("sep_type", "40"), ("add_opt1", MVSEP_MODEL), ("output_format", "2")],
                             [("audiofile", path.name, path.read_bytes(), "audio/flac" if path.suffix == ".flac" else "audio/mpeg")])
    report(0.02, f"Tonspur wird zu MVSEP hochgeladen{part_label}")
    status, raw, _ = _http("mvsep", "POST", f"{MVSEP_API}/separation/create", {"Content-Type": ctype}, body,
                           timeout=max(120, size / 200_000))
    _check("mvsep", status, raw)
    j = json.loads(raw or b"{}")
    if not j.get("success"):
        msg = _message(raw)
        low = msg.lower()
        if "limit" in low or "daily" in low or "quota" in low:
            raise OnlineError("Das Gratis-Kontingent bei MVSEP ist für heute aufgebraucht. Versuche es morgen noch einmal "
                              "oder rechne auf diesem PC.", "online_quota")
        raise OnlineError(f"MVSEP meldet einen Fehler{': ' + msg if msg else ''}.", "online")
    job = j["data"]["hash"]
    began = time.time()
    started = None
    queue_t, wait_msg = 0.0, None
    while True:
        if time.time() - began > MVSEP_TIMEOUT:
            raise OnlineError("MVSEP hat nach 45 Minuten noch kein Ergebnis geliefert. Versuche es später noch einmal.", "online")
        time.sleep(4)
        st = _json("mvsep", "GET", f"{MVSEP_API}/separation/get?" + urllib.parse.urlencode({"hash": job}), timeout=60)
        state = st.get("status")
        data = st.get("data") or {}
        if state == "done":
            return data.get("files") or []
        if state in ("failed", "not_found"):
            msg = data.get("message") or ""
            raise OnlineError(f"MVSEP konnte die Tonspur nicht trennen{': ' + msg if msg else ''}.", "online")
        if state == "waiting":
            if time.time() - queue_t > 20:   # Platz und Wartezeit höchstens alle 20 s abfragen
                queue_t = time.time()
                try:
                    q = _json("mvsep", "GET", f"{MVSEP_API}/app/queue/summary?" + urllib.parse.urlencode({"api_token": token}),
                              timeout=30).get("data") or {}
                    wait_msg = (f"In der Warteschlange bei MVSEP: noch etwa {max(1, round(q['estimated_wait_seconds'] / 60))} min "
                                f"({q['ahead']} vor dir){part_label}") if q.get("estimated_wait_seconds") is not None and q.get("ahead") is not None else None
                except (OnlineError, KeyError, TypeError, ValueError):
                    wait_msg = None
            report(0.05, wait_msg or f"In der Warteschlange bei MVSEP{part_label}")
        else:   # processing, distributing, merging
            started = started or time.time()
            report(min(0.9, 0.1 + (time.time() - started) / 150 * 0.8), f"MVSEP trennt die Stimmen{part_label}")


def _pick(files):
    """(Stimmen-Datei, Hintergrund-Datei) aus der Ergebnisliste."""
    voc = next((f for f in files if "vocal" in (f.get("name") or "").lower() and "instrum" not in (f.get("name") or "").lower()), None)
    ins = next((f for f in files if "instrum" in (f.get("name") or "").lower()), None)
    if not voc and files:
        voc = files[0]
    if not ins and len(files) > 1:
        ins = files[1]
    if not voc or not ins:
        raise OnlineError("MVSEP hat nicht beide Spuren geliefert.", "online")
    return voc, ins


def _download(url, dst):
    status, raw, _ = _http("mvsep", "GET", url, timeout=600)
    _check("mvsep", status, raw)
    dst.write_bytes(raw)


def separate(audio_path, out_dir, on_progress):
    """Wie separate.separate, aber bei MVSEP: erzeugt stimmen.wav und hintergrund.wav (44,1 kHz, Stereo)."""
    token = _key("mvsep", "token")
    info = sf.info(str(audio_path))
    sr, total = info.samplerate, info.frames
    parts = _parts(info.duration)
    tmp = out_dir / "_online"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    try:
        voc_all = np.zeros((total, info.channels), dtype=np.float32)
        ins_all = np.zeros((total, info.channels), dtype=np.float32)
        weight = np.zeros(total, dtype=np.float32)
        for i, (a, b) in enumerate(parts):
            label = f" (Teil {i + 1} von {len(parts)})" if len(parts) > 1 else ""

            def report(p, msg, i=i):
                on_progress((i + p) / len(parts), msg)

            src = tmp / f"teil{i}.flac"
            _encode(audio_path, src, a, b - a)
            if src.stat().st_size > 95_000_000:   # Gratiskonto: höchstens 100 MB je Datei
                src.unlink()
                src = tmp / f"teil{i}.mp3"
                _encode(audio_path, src, a, b - a, ("-c:a", "libmp3lame", "-b:a", "320k"))
            files = _mvsep_one(src, token, report, label)
            voc, ins = _pick(files)
            report(0.92, f"Ergebnis wird geladen{label}")
            got = []
            for f, name in ((voc, "stimmen"), (ins, "hintergrund")):
                raw = tmp / f"{name}{i}_roh"
                _download(f["url"], raw)
                wav = tmp / f"{name}{i}.wav"
                _encode(raw, wav, args=("-ar", str(sr), "-ac", str(info.channels), "-c:a", "pcm_f32le"))
                x, _ = sf.read(str(wav), dtype="float32", always_2d=True)
                got.append(x)
            # an die richtige Stelle legen, in der Überlappung überblenden
            s0 = int(round(a * sr))
            n = min(len(got[0]), len(got[1]), total - s0)
            w = np.ones(n, dtype=np.float32)
            fade = int(MVSEP_OVERLAP * sr)
            if i > 0:
                w[:fade] = np.linspace(0, 1, min(fade, n), dtype=np.float32)[:len(w[:fade])]
            if i < len(parts) - 1:
                k = min(fade, n)
                w[n - k:] = np.minimum(w[n - k:], np.linspace(1, 0, k, dtype=np.float32))
            voc_all[s0:s0 + n] += got[0][:n] * w[:, None]
            ins_all[s0:s0 + n] += got[1][:n] * w[:, None]
            weight[s0:s0 + n] += w
        weight = np.maximum(weight, 1e-3)[:, None]
        for buf, name in ((voc_all, "stimmen.wav"), (ins_all, "hintergrund.wav")):
            buf /= weight                       # an Ort und Stelle: sonst liegt der Puffer doppelt im Speicher
            np.clip(buf, -1, 1, out=buf)
            sf.write(str(out_dir / name), buf, sr, subtype="PCM_16")
        on_progress(1.0, "Stimmen getrennt")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ Sprache erkennen
LANG_NAMES = {
    "en": "english", "zh": "chinese", "de": "german", "es": "spanish", "ru": "russian", "ko": "korean", "fr": "french",
    "ja": "japanese", "pt": "portuguese", "tr": "turkish", "pl": "polish", "ca": "catalan", "nl": "dutch",
    "ar": "arabic", "sv": "swedish", "it": "italian", "id": "indonesian", "hi": "hindi", "fi": "finnish",
    "vi": "vietnamese", "he": "hebrew", "uk": "ukrainian", "el": "greek", "ms": "malay", "cs": "czech",
    "ro": "romanian", "da": "danish", "hu": "hungarian", "ta": "tamil", "no": "norwegian", "th": "thai", "ur": "urdu",
    "hr": "croatian", "bg": "bulgarian", "lt": "lithuanian", "la": "latin", "mi": "maori", "ml": "malayalam",
    "cy": "welsh", "sk": "slovak", "te": "telugu", "fa": "persian", "lv": "latvian", "bn": "bengali", "sr": "serbian",
    "az": "azerbaijani", "sl": "slovenian", "kn": "kannada", "et": "estonian", "mk": "macedonian", "br": "breton",
    "eu": "basque", "is": "icelandic", "hy": "armenian", "ne": "nepali", "mn": "mongolian", "bs": "bosnian",
    "kk": "kazakh", "sq": "albanian", "sw": "swahili", "gl": "galician", "mr": "marathi", "pa": "punjabi",
    "si": "sinhala", "km": "khmer", "sn": "shona", "yo": "yoruba", "so": "somali", "af": "afrikaans", "oc": "occitan",
    "ka": "georgian", "be": "belarusian", "tg": "tajik", "sd": "sindhi", "gu": "gujarati", "am": "amharic",
    "yi": "yiddish", "lo": "lao", "uz": "uzbek", "fo": "faroese", "ht": "haitian creole", "ps": "pashto",
    "tk": "turkmen", "nn": "nynorsk", "mt": "maltese", "sa": "sanskrit", "lb": "luxembourgish", "my": "myanmar",
    "bo": "tibetan", "tl": "tagalog", "mg": "malagasy", "as": "assamese", "tt": "tatar", "haw": "hawaiian",
    "ln": "lingala", "ha": "hausa", "ba": "bashkir", "jw": "javanese", "su": "sundanese", "yue": "cantonese",
}
_CODES = {v: k for k, v in LANG_NAMES.items()}


def _lang_code(v):
    v = str(v or "").strip().lower()
    if not v:
        return None
    if v in LANG_NAMES:
        return v
    if v.split("-")[0] in LANG_NAMES:
        return v.split("-")[0]
    return _CODES.get(v)


def _chunks(regions, total, limit):
    """Stimm-Bereiche zu Abschnitten zusammenfassen, bevorzugt in Pausen geteilt.

    Lange Stellen ohne Pause (Lied, Vortrag) werden hart geschnitten: zu lange Abschnitte erkennen die Dienste
    messbar schlechter (Among Us, 12 min: am Stück 18,2 %, in 1-Minuten-Stücken 15,4 % Wortfehler). -> [(a, b)]"""
    out, a, b = [], None, None
    for x, y in regions:
        if a is None:
            a, b = max(0.0, x - 0.3), y
        elif y - a > limit:
            out.append((a, min(total, (b + x) / 2)))
            a, b = (b + x) / 2, y
        else:
            b = y
        while b - a > limit:
            out.append((a, a + limit))
            a = a + limit
    if a is not None:
        out.append((a, min(total, b + 0.3)))
    return out


def _opus(voc16, a, b, tmp):
    """Abschnitt als Ogg Opus (16 kHz mono, klein genug für alle Dienste)."""
    wav, ogg = tmp / f"a{a:.2f}.wav", tmp / f"a{a:.2f}.ogg"
    sf.write(str(wav), voc16[int(a * SR):int(b * SR)], SR, subtype="PCM_16")
    _encode(wav, ogg, args=("-c:a", "libopus", "-b:a", "32k", "-ac", "1"))
    data = ogg.read_bytes()
    wav.unlink(missing_ok=True)
    ogg.unlink(missing_ok=True)
    return data


def _groq(data, language):
    fields = [("model", GROQ_MODEL), ("response_format", "verbose_json"), ("temperature", "0"),
              ("timestamp_granularities[]", "word"), ("timestamp_granularities[]", "segment")]
    if language:
        fields.append(("language", language))
    body, ctype = _multipart(fields, [("file", "stimmen.ogg", data, "audio/ogg")])
    status, raw, _ = _http("groq", "POST", f"{GROQ_API}/audio/transcriptions",
                           {"Authorization": f"Bearer {_key('groq', 'key')}", "Content-Type": ctype}, body, timeout=600)
    _check("groq", status, raw)
    j = json.loads(raw)
    segs = [{"start": s.get("start", 0.0), "end": s.get("end", 0.0), "text": s.get("text", ""),
             "avg_logprob": s.get("avg_logprob"), "no_speech_prob": s.get("no_speech_prob")} for s in j.get("segments") or []]
    words = [{"word": w.get("word", ""), "start": w.get("start", 0.0), "end": w.get("end", 0.0)} for w in j.get("words") or []]
    for w in words:   # Groq liefert die Wörter getrennt von den Abschnitten: zuordnen
        mid = (w["start"] + w["end"]) / 2
        hit = next((i for i, s in enumerate(segs) if s["start"] - 0.05 <= mid <= s["end"] + 0.05), None)
        if hit is None and segs:   # dazwischen: der zeitlich nächste Abschnitt, nicht blind der letzte
            hit = min(range(len(segs)), key=lambda i: min(abs(segs[i]["start"] - mid), abs(segs[i]["end"] - mid)))
        w["seg"] = hit or 0
    return segs, words, _lang_code(j.get("language")), None


def _cloudflare(data, language):
    payload = {"audio": base64.b64encode(data).decode("ascii"), "task": "transcribe", "vad_filter": False,
               "condition_on_previous_text": False}
    if language:
        payload["language"] = language
    acc = urllib.parse.quote(_key("cloudflare", "account"))
    j = _json("cloudflare", "POST", f"{CF_API}/accounts/{acc}/ai/run/{CF_MODEL}",
              {"Authorization": f"Bearer {_key('cloudflare', 'token')}"}, payload, timeout=600)
    if j.get("success") is False:
        raise OnlineError(f"Cloudflare meldet einen Fehler: {_message(json.dumps(j).encode())}.", "online")
    r = j.get("result") or j
    segs, words = [], []
    for s in r.get("segments") or []:
        si = len(segs)
        segs.append({"start": s.get("start", 0.0), "end": s.get("end", 0.0), "text": s.get("text", ""),
                     "avg_logprob": s.get("avg_logprob"), "no_speech_prob": s.get("no_speech_prob")})
        for w in s.get("words") or []:
            words.append({"word": w.get("word", ""), "start": w.get("start", 0.0), "end": w.get("end", 0.0), "seg": si})
    ti = r.get("transcription_info") or {}
    return segs, words, _lang_code(ti.get("language")), ti.get("language_probability")


def _gemini_upload(data):
    key = _key("gemini", "key")
    status, raw, head = _http("gemini", "POST", f"{GEMINI_API}/upload/v1beta/files",
                              {"x-goog-api-key": key, "X-Goog-Upload-Protocol": "resumable",
                               "X-Goog-Upload-Command": "start", "X-Goog-Upload-Header-Content-Length": str(len(data)),
                               "X-Goog-Upload-Header-Content-Type": "audio/ogg", "Content-Type": "application/json"},
                              json.dumps({"file": {"display_name": "voicitool"}}).encode("utf8"))
    _check("gemini", status, raw)
    url = head.get("x-goog-upload-url")
    if not url:
        raise OnlineError("Gemini hat den Upload nicht angenommen.", "online")
    status, raw, _ = _http("gemini", "POST", url, {"X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize",
                                                   "Content-Length": str(len(data))}, data, timeout=600)
    _check("gemini", status, raw)
    f = json.loads(raw).get("file") or {}
    return f.get("uri"), f.get("mimeType") or f.get("mime_type") or "audio/ogg"


def _gemini_offset(v):
    try:
        return float(str(v).rstrip("s"))
    except (TypeError, ValueError):
        return None


def _gemini(data, language):
    key = _key("gemini", "key")
    uri, mime = _gemini_upload(data)
    cfg = {"mode": {"type": "verbatim", "diarization_mode": "speaker", "timestamp_granularities": ["word"]}}
    if language:
        cfg["language_codes"] = [language]
    j = _json("gemini", "POST", f"{GEMINI_API}/v1beta/interactions", {"x-goog-api-key": key},
              {"model": GEMINI_MODEL, "input": [{"type": "audio", "uri": uri, "mime_type": mime}],
               "generation_config": {"transcription_config": cfg}}, timeout=900)
    began = time.time()
    while j.get("status") not in (None, "completed"):
        jid = j.get("id") or j.get("name")
        if j.get("status") in ("failed", "cancelled") or not jid:
            raise OnlineError("Gemini konnte die Tonspur nicht erkennen.", "online")
        if time.time() - began > 900:
            raise OnlineError("Gemini hat nach 15 Minuten kein Ergebnis geliefert. Versuche es später noch einmal.", "online")
        time.sleep(3)
        j = _json("gemini", "GET", f"{GEMINI_API}/v1beta/{jid}", {"x-goog-api-key": key})
    words, segs, last_spk = [], [], None
    for step in j.get("steps") or []:
        for content in step.get("content") or []:
            for a in content.get("annotations") or []:
                if a.get("type") != "word_info":
                    continue
                s, e = _gemini_offset(a.get("start_offset")), _gemini_offset(a.get("end_offset"))
                if s is None or e is None:
                    continue
                spk = a.get("speaker")
                # neuer Abschnitt bei Sprecherwechsel oder längerer Pause (die Sprechererkennung trennt Abschnitte)
                if not segs or spk != last_spk or s - segs[-1]["end"] > 1.0:
                    segs.append({"start": s, "end": e, "text": "", "avg_logprob": None, "no_speech_prob": None})
                last_spk = spk
                segs[-1]["end"] = e
                segs[-1]["text"] += " " + (a.get("text") or "")
                words.append({"word": a.get("text") or "", "start": s, "end": e, "seg": len(segs) - 1})
    text = j.get("output_text") or ""
    if text and words:   # Satzzeichen stehen im Gesamttext, nicht unbedingt an den Wörtern
        _attach_punct(words, text)
        for si, s in enumerate(segs):
            s["text"] = " ".join(w["word"] for w in words if w["seg"] == si)
    return segs, words, _lang_code(language), None


def _norm(t):
    return "".join(ch for ch in t.lower() if ch.isalnum())


def _attach_punct(words, text):
    """Schreibweise mit Satzzeichen aus dem Abschnittstext an die Wörter hängen (Groq liefert Wörter ohne)."""
    toks = text.split()
    a, b = [_norm(w["word"]) for w in words], [_norm(t) for t in toks]
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for k in range(i2 - i1):
                if a[i1 + k] == b[j1 + k] or tag == "equal":
                    words[i1 + k]["word"] = toks[j1 + k]


def _drop(seg):
    from app.pipeline import transcribe as T
    text = T._clean(seg["text"])
    if not text:
        return True
    dur = seg["end"] - seg["start"]
    lp = seg.get("avg_logprob")
    if text in T.HALLUCINATIONS and dur < 4.0:
        return True
    if text in T.SUSPECT_PHRASES and lp is not None and math.exp(lp) < T.SUSPECT_MIN_P:
        return True
    if (seg.get("no_speech_prob") or 0) > 0.85 and lp is not None and lp < -1.0:
        return True
    return False


def transcribe(voc16, language, on_progress, vad_threshold=0.35, service=None):
    """Wie transcribe.transcribe, aber beim gewählten Dienst. Ergebnis im selben Format (words, segments, language)."""
    from app.pipeline import transcribe as T
    service = service if service in ASR and configured(service) else asr_service()
    if not service:
        raise OnlineError("Für Online rechnen ist kein Dienst zur Spracherkennung eingerichtet.", "online_setup")
    call = {"groq": _groq, "cloudflare": _cloudflare, "gemini": _gemini}[service]
    lang = None if language in (None, "auto", "mixed") else language
    total = len(voc16) / SR
    regions = T.voice_regions(voc16, vad_threshold)
    if not regions:
        return {"language": lang or "en", "language_probability": 0.0, "segments": [], "words": [], "service": service}
    chunks = _chunks(regions, total, CHUNK[service])
    tmp = config.DATA_DIR / "tmp" / f"online_{uuid.uuid4().hex[:8]}"
    tmp.mkdir(parents=True, exist_ok=True)
    words, seg_list, langs = [], [], []
    try:
        for ci, (a, b) in enumerate(chunks):
            on_progress(ci / len(chunks), f"Text wird bei {NAMES[service]} erkannt")
            data = _opus(voc16, a, b, tmp)
            segs, ws, code, prob = call(data, lang)
            if code:
                langs.append((code, prob, b - a))
            if service != "gemini":
                for si, s in enumerate(segs):
                    _attach_punct([w for w in ws if w["seg"] == si], s["text"])
            keep, span = {}, b - a
            for si, s in enumerate(segs):
                if _drop(s):
                    continue
                st = min(max(0.0, float(s["start"])), span)
                en = min(max(st, float(s["end"])), span)
                keep[si] = len(seg_list)
                seg_list.append({"start": round(st + a, 3), "end": round(en + a, 3), "text": s["text"].strip()})
            for w in ws:
                if w["seg"] not in keep or not str(w["word"]).strip():
                    continue
                ws_, we_ = float(w["start"]), float(w["end"])
                if ws_ > span + 0.2:
                    continue   # Zeit außerhalb des Abschnitts: der Dienst hat sich verschätzt
                ws_ = min(max(0.0, ws_), span)
                we_ = min(max(we_, ws_ + 0.02), span)
                s, e = ws_ + a, we_ + a
                mid = (s + e) / 2
                if not any(x - 0.25 <= mid <= y + 0.25 for x, y in regions):
                    continue   # Wort in der Stille: erfunden
                lp = segs[w["seg"]].get("avg_logprob")
                p = 0.9 if lp is None else float(min(1.0, max(0.0, math.exp(lp))))
                words.append({"w": " " + str(w["word"]).strip(), "s": round(s, 3), "e": round(max(e, s + 0.02), 3),
                              "p": round(p, 3), "seg": keep[w["seg"]]})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    on_progress(1.0, f"Text wird bei {NAMES[service]} erkannt")
    words.sort(key=lambda w: w["s"])
    if lang:
        found, prob = lang, None
    elif langs:
        found = max({c for c, _, _ in langs}, key=lambda c: sum(d for x, _, d in langs if x == c))
        prob = next((p for c, p, _ in langs if c == found and p is not None), None)
    else:
        found, prob = None, None
    words = T._collapse_loops(words)
    if found:
        words = T.drop_foreign_script(words, found)
    words = T.fix_stretched_words(words, voc16)
    # Stimmhafte Stellen ohne erkanntes Wort (Luftholen, Stöhnen, Seufzen) als Laut-Zeile „(…)“ merken. Lokal macht das
    # fill_voice_gaps mit Whisper; online wäre je Lücke eine eigene Anfrage nötig, deshalb nur der Hinweis.
    # An Among Us Folge 6 gemessen: 18 verpasste Referenz-Zeilen (alles Laute) werden 0, dafür 1 erfundene mehr.
    env = T._envelope_db(voc16)
    for gap_a, gap_b in T.voice_gaps(env, words):
        words.append({"w": " " + T.SOUND_TEXT, "s": round(gap_a, 3), "e": round(gap_b, 3), "p": 0.0,
                      "seg": -1, "sound": True})
    words.sort(key=lambda w: w["s"])
    return {"language": found, "language_probability": round(prob, 3) if prob is not None else None,
            "segments": seg_list, "words": words, "service": service}
