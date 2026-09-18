"""Spracherkennung mit faster-whisper (Wort-Zeitstempel).

Für Gesang: Sprach-Erkenner (Silero-VAD) alleine verwirft gesungene Stellen oft. Deshalb werden
zusätzlich laute Stellen der sauberen Stimmen-Spur als Sprachbereiche genommen und in Häppchen
(max. 25 s) stapelweise erkannt. Das verhindert auch übermäßig gedehnte Segmente.
"""
import gc
import os
import re
import threading
from pathlib import Path

import numpy as np

from app import config

SR = 16000
MAX_CHUNK = 25.0      # längster Abschnitt, der am Stück erkannt wird (s)
MERGE_GAP = 0.5       # Lücken bis hier zusammenfassen (s)
MIN_REGION = 0.25     # kürzere Bereiche verwerfen (s)

# Typische Whisper-Halluzinationen auf Stille/Geräuschen
HALLUCINATIONS = {
    "untertitel von stephanie geiges", "untertitelung des zdf 2020", "untertitel im auftrag des zdf 2020",
    "untertitel der deutschen welle", "vielen dank für die aufmerksamkeit", "vielen dank", "das war's",
    "subtitles by the amaraorg community", "thanks for watching", "thank you for watching", "bye",
}

_models = {}
_model_lock = threading.Lock()

# Stilvorgabe für Whisper: kurzer Beispieltext mit Satzzeichen und Großschreibung. Ohne sie liefert
# Whisper bei Gesang und schnellem Sprechen manchmal ganze Abschnitte klein und ohne Punkt; dann
# fehlen die Satzgrenzen, an denen die Zeilen geteilt werden.
STYLE_PROMPT = {
    "en": "Hello. Yes, I know! Are you sure? Well, let's go.",
    "de": "Hallo. Ja, ich weiß! Bist du sicher? Na gut, los geht's.",
    "es": "Hola. Sí, lo sé. ¿Estás seguro? Bueno, vamos.",
    "fr": "Bonjour. Oui, je sais ! Tu es sûr ? Bon, allons-y.",
    "it": "Ciao. Sì, lo so! Sei sicuro? Va bene, andiamo.",
    "pt": "Olá. Sim, eu sei! Tem certeza? Bem, vamos lá.",
}
USE_STYLE_PROMPT = False   # für den ganzen Film: verschlechtert die Worterkennung (gemessen), nur gezielt nutzen


def _add_cuda_dlls():
    # CTranslate2 braucht cuBLAS/cuDNN, die liefert PyTorch mit
    try:
        import torch
        lib = Path(torch.__file__).parent / "lib"
        if lib.exists():
            os.add_dll_directory(str(lib))
            os.environ["PATH"] = str(lib) + os.pathsep + os.environ.get("PATH", "")
        return torch.cuda.is_available()
    except Exception:
        return False


def get_model(name):
    with _model_lock:
        if name not in _models:
            _models.clear()  # nur ein Modell gleichzeitig im Grafikspeicher
            gc.collect()
            cuda = _add_cuda_dlls()
            try:   # Rechnen auf dem Prozessor eingestellt oder Grafikkarte nicht nutzbar
                from app import system
                cuda = cuda and system.gpu_allowed()
            except Exception:
                pass
            from faster_whisper import WhisperModel
            root = str(config.MODELS_DIR / "whisper")
            ct = _gpu_compute_type(name) if cuda else "cpu"
            if ct != "cpu":
                _models[name] = WhisperModel(name, device="cuda", compute_type=ct, download_root=root)
            else:
                ct = "int8"
                # alle physischen Kerne (faster-whisper nimmt sonst fest 4): auf 6 Kernen 14 % schneller
                import torch
                _models[name] = WhisperModel(name, device="cpu", compute_type=ct, download_root=root,
                                             cpu_threads=max(1, torch.get_num_threads()))
            _compute[name] = ct
        return _models[name]


# Grafikspeicher (GB) je Modell in float16 mit Stapel 4, gemessen auf RTX 4060, plus Reserve.
# Ist weniger frei (andere Programme, Spiele), lagert Windows sonst still in den Arbeitsspeicher aus
# und alles wird extrem langsam. int8 braucht rund 40 % weniger bei fast gleicher Genauigkeit.
VRAM_FP16 = {"large-v3": 5.8, "large-v3-turbo": 4.2, "distil-large-v3.5": 3.8}
VRAM_CPU = 2.0   # noch weniger frei: auf dem Prozessor ist es schneller als stark ausgelagert
# Reserve eingerechnet: Spiele und der Windows-Desktop belegen während der Erkennung oft noch mehr
_compute = {}


def _free_vram_gb():
    try:
        import torch
        free, _ = torch.cuda.mem_get_info()
        return free / 1024 ** 3
    except Exception:
        return None


def _gpu_compute_type(name):
    free = _free_vram_gb()
    need = VRAM_FP16.get(name, 5.0)
    if free is None or free >= need:
        print(f"Grafikspeicher frei: {free if free is not None else '?'} GB, {name} rechnet mit float16", flush=True)
        return "float16"
    if free < VRAM_CPU:
        print(f"Sehr wenig freier Grafikspeicher ({free:.1f} GB): {name} rechnet auf dem Prozessor", flush=True)
        return "cpu"
    print(f"Wenig freier Grafikspeicher ({free:.1f} GB, gebraucht {need:.1f} GB): {name} rechnet sparsamer (int8)", flush=True)
    return "int8_float16"


def unload():
    with _model_lock:
        _models.clear()
    gc.collect()


def _envelope_db(audio16k, hop=160):
    n = len(audio16k) // hop
    frames = audio16k[: n * hop].reshape(n, hop)
    return 20 * np.log10(np.sqrt(np.mean(frames ** 2, axis=1) + 1e-10))


def voice_regions(audio16k, vad_threshold=0.35, max_len=MAX_CHUNK):
    """Bereiche mit Stimme: Silero-VAD (Sprache) plus laute Stellen (fängt Gesang mit ein)."""
    fps = 100
    env = _envelope_db(audio16k)
    loud = env > max(np.percentile(env, 99.5) - 32.0, -48.0)
    smooth = np.convolve(loud.astype(float), np.ones(25) / 25, mode="same") > 0.2
    regions, start = [], None
    for i, v in enumerate(np.append(smooth, False)):
        if v and start is None:
            start = i
        elif not v and start is not None:
            regions.append([start / fps, i / fps])
            start = None
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        vad = get_speech_timestamps(audio16k, VadOptions(threshold=vad_threshold, min_silence_duration_ms=300,
                                                         speech_pad_ms=200))
        regions += [[v["start"] / SR, v["end"] / SR] for v in vad]
    except Exception:
        pass

    total = len(audio16k) / SR
    limit = max(0.0, total - 0.05)  # Sicherheitsabstand: sonst rundet faster-whisper über das Ende hinaus
    regions = [[max(0.0, a - 0.15), min(limit, b + 0.15)] for a, b in regions if b - a >= MIN_REGION]
    regions = [[a, b] for a, b in regions if b - a >= MIN_REGION]
    regions.sort()
    merged = []
    for a, b in regions:
        if merged and a - merged[-1][1] < MERGE_GAP:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])

    out = []  # zu lange Bereiche an der leisesten Stelle teilen
    for a, b in merged:
        stack = [(a, b)]
        while stack:
            x, y = stack.pop()
            if y - x <= max_len:
                out.append((round(x, 3), round(y, 3)))
                continue
            lo, hi = int((x + (y - x) * 0.3) * fps), int((x + (y - x) * 0.7) * fps)
            cut = (lo + int(np.argmin(env[lo:hi]))) / fps
            stack += [(cut, y), (x, cut)]
    return sorted(out)


def _clean(text):
    return re.sub(r"[^\w\s]", "", (text or "").lower()).strip()


def _drop_segment(seg):
    """Offensichtliche Halluzinationen auf Stille/Geräuschen aussortieren."""
    text = _clean(seg.text)
    if not text:
        return True
    dur = seg.end - seg.start
    if text in HALLUCINATIONS and dur < 4.0:
        return True
    if getattr(seg, "no_speech_prob", 0) > 0.85 and getattr(seg, "avg_logprob", 0) < -1.0:
        return True
    return False


def _collapse_loops(words, max_repeat=6, max_gap=1.0):
    """Endlos-Wiederholungen eines Wortes („what what what …“ über Stille) auf wenige kürzen.

    Echte Wiederholungen bleiben stehen (Lieder, Sprechchöre: „What, what, what, what?“ mehrmals
    hintereinander). Gezählt wird deshalb nur innerhalb eines Abschnitts, ohne Pause dazwischen, und
    gekürzt erst ab dem (max_repeat + 1)-ten gleichen Wort am Stück."""
    from app.pipeline.segment import ends_sentence
    out, run, prev = [], 0, None
    for w in words:
        same = (prev is not None and _clean(prev["w"]) and _clean(prev["w"]) == _clean(w["w"])
                and prev.get("seg") == w.get("seg") and w["s"] - prev["e"] < max_gap
                and not ends_sentence(prev["w"]))   # „What, what, what, what? What, …“: Satzende beginnt neu
        run = run + 1 if same else 0
        prev = w
        if run >= max_repeat:
            continue
        out.append(w)
    return out


def transcribe(audio16k, language, on_progress, quality=None, vocals=None):
    """audio16k: float32-Mono-Array (16 kHz). language: 'de' | 'en' | None (auto) | 'mixed'.
    vocals: optional die getrennte Stimmen-Spur. Dann hört Whisper audio16k (z. B. den Originalton),
    wo Stimme ist und wie lange Wörter dauern, kommt aber aus der Stimmen-Spur."""
    vocals = audio16k if vocals is None else vocals
    from faster_whisper import BatchedInferencePipeline
    from app import models

    q = config.quality(quality)
    ref = models.pick_whisper(quality, language)
    model = get_model(ref)
    if models.is_english_only(ref):
        language = "en"   # Englisch-Paket kann nur Englisch
    duration = len(audio16k) / SR
    regions = voice_regions(vocals, q["vad_threshold"])
    if not regions:
        return {"language": language or "de", "language_probability": 0.0, "segments": [], "words": []}

    if not language or language == "auto":
        language, lang_p = detect_language(model, audio16k, regions)
    else:
        lang_p = None

    kwargs = dict(
        beam_size=q["beam"],
        best_of=q["best_of"],
        patience=q["patience"],
        word_timestamps=True,
        vad_filter=False,
        clip_timestamps=[{"start": a, "end": b} for a, b in regions],
        condition_on_previous_text=False,
        repetition_penalty=1.1,
        no_speech_threshold=0.6,
        log_prob_threshold=-1.0,
        batch_size=4 if _compute.get(ref, "float16") in ("float16", "int8") else 2,   # knapper Speicher: kleinere Stapel
    )
    if language == "mixed":
        kwargs["multilingual"] = True
    elif language and language != "auto":
        kwargs["language"] = language
        if USE_STYLE_PROMPT and language in STYLE_PROMPT:
            kwargs["initial_prompt"] = STYLE_PROMPT[language]
    try:
        segments, info = BatchedInferencePipeline(model=model).transcribe(audio16k, **kwargs)
        segments = list(segments)
    except Exception as e:  # noqa: BLE001
        # Rückfall: klassische Erkennung mit Sprach-Erkenner (z. B. wenn die Abschnitts-Erkennung streikt)
        print(f"Abschnitts-Erkennung fehlgeschlagen ({type(e).__name__}: {e}), nutze Standardverfahren", flush=True)
        for key in ("clip_timestamps", "batch_size"):
            kwargs.pop(key, None)
        kwargs.update(vad_filter=True, vad_parameters=dict(min_silence_duration_ms=400, speech_pad_ms=250,
                                                           threshold=q["vad_threshold"]),
                      hallucination_silence_threshold=2.0)
        segments, info = model.transcribe(audio16k, **kwargs)

    words, seg_list = [], []
    for seg in segments:
        if on_progress and duration:
            on_progress(min(1.0, seg.end / duration))
        if _drop_segment(seg):
            continue
        si = len(seg_list)
        # float(): die Batch-Erkennung liefert NumPy-Zahlen, die sich nicht als JSON speichern lassen
        seg_list.append({"start": float(seg.start), "end": float(seg.end), "text": seg.text.strip()})
        for w in seg.words or []:
            if not w.word.strip():
                continue
            start, end = float(w.start), float(w.end)
            words.append({"w": w.word, "s": round(start, 3), "e": round(max(end, start + 0.02), 3),
                          "p": round(float(w.probability), 3), "seg": si})
    words = _collapse_loops(words)
    words = repunctuate(model, audio16k, words, language if language not in (None, "auto", "mixed") else info.language)
    words = fill_voice_gaps(model, audio16k, words, language, env=_envelope_db(vocals))   # vor der Wortzeit-Korrektur
    words = drop_foreign_script(words, language if language not in (None, "auto", "mixed") else info.language)
    words = fix_stretched_words(words, vocals)
    return {"language": info.language,
            "language_probability": round(lang_p if lang_p is not None else (info.language_probability or 0), 3),
            "segments": seg_list, "words": words}


def _foreign(word):
    """Enthält das Wort Buchstaben außerhalb der lateinischen Schrift?"""
    import unicodedata
    for ch in word:
        if ch.isalpha():
            try:
                if not unicodedata.name(ch).startswith("LATIN"):
                    return True
            except ValueError:
                return True
    return False


def drop_foreign_script(words, language):
    """In Sprachen mit lateinischer Schrift: Wörter in fremder Schrift (Whisper schreibt Applaus schon mal als
    „박수“) werden zu einem Laut „(…)“; aufeinanderfolgende werden zusammengefasst."""
    if language not in LATIN_LANGS:
        return words
    out = []
    for w in words:
        if not w.get("sound") and _foreign(w["w"]):
            if out and out[-1].get("sound") and out[-1].get("foreign") and w["s"] - out[-1]["e"] < 0.3:
                out[-1]["e"] = w["e"]
            else:
                out.append({"w": " " + SOUND_TEXT, "s": w["s"], "e": w["e"], "p": 0.0, "seg": w.get("seg", -1),
                            "sound": True, "foreign": True})
            continue
        out.append(w)
    return out


def detect_language(model, audio16k, regions, windows=5):
    """Sprache aus mehreren Stellen mit Stimme bestimmen statt nur aus den ersten 30 Sekunden.

    Whisper entscheidet sonst nach dem Anfang; ein Akzent oder ein fremdsprachiges Intro kippt dann das
    ganze Video in eine falsche Sprache (erkannt und übersetzt statt abgeschrieben). Hier stimmen bis zu
    `windows` Abschnitte à 30 s aus den stimmhaften Bereichen ab, gewichtet nach ihrer Sicherheit.
    Ergebnis: (Sprache, gemittelte Wahrscheinlichkeit)."""
    voiced = np.concatenate([audio16k[int(a * SR): int(b * SR)] for a, b in regions]) if regions else audio16k
    win = 30 * SR
    if len(voiced) <= win:
        starts = [0]
    else:
        k = min(windows, max(1, int(len(voiced) // win)))
        starts = [int(i * (len(voiced) - win) / max(1, k - 1)) for i in range(k)] if k > 1 else [0]
    score = {}
    for s in starts:
        try:
            _, _, probs = model.detect_language(audio=voiced[s: s + win], language_detection_segments=1,
                                                language_detection_threshold=1.01)
        except Exception as e:  # noqa: BLE001
            print(f"Spracherkennung an {s / SR:.0f}s fehlgeschlagen: {e}", flush=True)
            continue
        for code, p in probs[:8]:
            score[code] = score.get(code, 0.0) + float(p)
    if not score:
        return None, 0.0
    lang = max(score, key=score.get)
    prob = score[lang] / len(starts)
    top = sorted(score.items(), key=lambda x: -x[1])[:3]
    print("Sprache: " + ", ".join(f"{c} {v / len(starts):.2f}" for c, v in top), flush=True)
    return lang, prob


def _word_diff(a, b):
    """Anteil unterschiedlicher Wörter (Levenshtein auf Wortebene, bezogen auf a)."""
    a = [_clean(x) for x in a if _clean(x)]
    b = [_clean(x) for x in b if _clean(x)]
    if not a:
        return 1.0
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
        prev = cur
    return prev[-1] / len(a)


def repunctuate(model, audio16k, words, language, min_words=8):
    """Abschnitte ganz ohne Satzzeichen (Whisper liefert sie bei Gesang oder schnellem Sprechen
    manchmal klein und ohne Punkt) noch einmal mit Stilvorgabe erkennen. Übernommen wird das nur,
    wenn fast dieselben Wörter herauskommen, jetzt aber mit Satzzeichen: die Satzgrenzen werden
    für die Zeilen gebraucht, die Worterkennung selbst soll nicht schlechter werden."""
    prompt = STYLE_PROMPT.get(language or "")
    if not prompt:
        return words
    by_seg = {}
    for i, w in enumerate(words):
        by_seg.setdefault(w.get("seg"), []).append(i)
    out = list(words)
    replaced = {}
    for seg, idx in by_seg.items():
        ws = [words[i] for i in idx]
        if seg is None or seg < 0 or len(ws) < min_words or any(re.search(r"[.!?…,;:]", w["w"]) for w in ws):
            continue
        a, b = max(0.0, ws[0]["s"] - 0.2), min(len(audio16k) / SR, ws[-1]["e"] + 0.2)
        try:
            segs, _ = model.transcribe(audio16k[int(a * SR): int(b * SR)], language=language, beam_size=5,
                                       word_timestamps=True, condition_on_previous_text=False, vad_filter=False,
                                       initial_prompt=prompt)
            new = [{"w": w.word, "s": round(a + float(w.start), 3), "e": round(a + max(float(w.end), float(w.start) + 0.02), 3),
                    "p": round(float(w.probability), 3), "seg": seg}
                   for s in segs if not _drop_segment(s) for w in (s.words or []) if w.word.strip()]
        except Exception as ex:  # noqa: BLE001
            print(f"Satzzeichen-Nacherkennung fehlgeschlagen: {ex}", flush=True)
            continue
        if not new or not any(re.search(r"[.!?…]", w["w"]) for w in new):
            continue
        if _word_diff([w["w"] for w in ws], [w["w"] for w in new]) > 0.25:
            continue   # zu viel anders erkannt: lieber die ursprünglichen Wörter behalten
        replaced[seg] = new
    if not replaced:
        return words
    out = [w for w in words if w.get("seg") not in replaced]
    for new in replaced.values():
        out += new
    print(f"Satzzeichen ergänzt in {len(replaced)} Abschnitt(en)", flush=True)
    return sorted(out, key=lambda w: w["s"])


def fix_stretched_words(words, audio16k):
    """Whisper dehnt Wörter an Pausen oft über die Stille (z. B. „You“ 1,5 s lang, gesprochen nur 0,3 s).
    Dann das Wort auf den Stimmteil kürzen, zu dem es gehört: Liegt im Wort eine Pause, gehört ein
    Satzanfang zum Teil danach, ein Satzende zum Teil davor."""
    from app.pipeline.segment import ends_sentence, starts_sentence
    env = _envelope_db(audio16k)   # 10 ms je Wert
    fps = 100
    for i, w in enumerate(words):
        dur = w["e"] - w["s"]
        letters = len(re.sub(r"\W", "", w["w"]))
        if dur < max(0.7, 0.09 * letters + 0.4):
            continue
        a, b = int(w["s"] * fps), min(len(env), int(w["e"] * fps))
        seg = env[a:b]
        if len(seg) < 10:
            continue
        voiced = seg > seg.max() - 20.0
        # Stimmteile: Lücken unter 150 ms überbrücken, Teile unter 60 ms verwerfen
        runs, start, silent = [], None, 0
        for k, v in enumerate(voiced):
            if v:
                if start is None:
                    start = k
                silent = 0
            elif start is not None:
                silent += 1
                if silent >= 15:
                    runs.append((start, k - silent + 1))
                    start, silent = None, 0
        if start is not None:
            runs.append((start, len(voiced) - silent))
        runs = [r for r in runs if r[1] - r[0] >= 6]
        if not runs:
            continue
        if len(runs) == 1:
            pick = runs[0]
        else:
            prev = words[i - 1]["w"] if i else ""
            sentence_start = not prev or ends_sentence(prev) or starts_sentence(w["w"], prev)
            pick = runs[-1] if sentence_start and not ends_sentence(w["w"]) else runs[0]
        on, off = (a + pick[0]) / fps, (a + pick[1]) / fps
        if on - w["s"] > 0.15:
            w["s"] = round(on - 0.05, 3)
        if w["e"] - off > 0.25:
            w["e"] = round(max(w["s"] + 0.05, off + 0.08), 3)
    return words


SOUND_TEXT = "(…)"   # Laut ohne erkennbare Worte (Kichern, Keuchen, Schrei); im Editor ergänzbar
CONTINUE_GAP = 0.35   # so nah an einem Wort gilt ein Laut als dessen Verlängerung (s)
# Nacherkannte Worte zählen nur, wenn Whisper sich halbwegs sicher ist (geometrisches Mittel der Wort-
# Wahrscheinlichkeiten). Gemessen an 22 Referenz-Packs: darunter waren 19 von 21 Gruppen erfunden.
GAPFILL_MIN_P = 0.4
GAPFILL_CONTEXT = 1.0    # so viel Ton davor/danach hört die Nacherkennung mit (s)
SOUND_MIN_REL_DB = 9.0   # Laut-Zeile nur, wenn höchstens so viel leiser als der typische Sprechpegel
LAUGH_WORD = re.compile(r"[^\w]*(?:[hk]+[aeiouäöü]+){2,}h*[^\w]*", re.I)   # HAHAHA, hehehe, Hihi!
LAUGH_SYL = re.compile(r"[^\w]*[hk][aeiouäöü]+h?[^\w]*", re.I)              # einzelne Silbe: „Ha“, „he!“
# Geräusch-Anmerkungen, die Whisper schreibt, wenn man sie nicht unterdrückt: *Gelächter*, [Lachen], (laughs)
NOTE_WORD = re.compile(r"^[*\[(（♪].*|.*[*\])）♪]$")
LAUGH_NOTE = re.compile(r"lach|laugh|gel[äa]chter|kicher|giggl|chuckl|rire|risa|ríe|ride|smie|směj|сме", re.I)
# Sprachen mit lateinischer Schrift: Wörter in anderer Schrift (Koreanisch, Arabisch …) sind dort Erfindungen
LATIN_LANGS = {"af", "az", "br", "bs", "ca", "cs", "cy", "da", "de", "en", "es", "et", "eu", "fi", "fo", "fr", "gl",
               "ha", "haw", "hr", "ht", "hu", "id", "is", "it", "jw", "la", "lb", "ln", "lt", "lv", "mg", "mi", "ms",
               "mt", "nl", "nn", "no", "oc", "pl", "pt", "ro", "sk", "sl", "sn", "so", "sq", "su", "sv", "sw", "tk",
               "tl", "tr", "uz", "vi", "yo"}


def voice_gaps(env, words, rel_peak=24.0, min_len=0.3, margin=0.15, fps=100):
    """Stellen mit deutlicher Stimme, die kein erkanntes Wort abdeckt: [(start, ende)] in s."""
    if not len(env):
        return []
    peak = float(np.percentile(env, 99.5))
    thr = max(peak - rel_peak, float(np.percentile(env, 20)) + 18.0)
    covered = np.zeros(len(env), bool)
    for w in words:
        covered[max(0, int((w["s"] - margin) * fps)): int((w["e"] + margin) * fps)] = True
    cand = (env > thr) & ~covered
    out, s, gap = [], None, 0
    for i, v in enumerate(np.append(cand, False)):
        if v:
            if s is None:
                s = i
            gap = 0
        elif s is not None:
            gap += 1
            if gap > 20 or i == len(cand):
                e = i - gap + 1
                if (e - s) / fps >= min_len:
                    out.append((s / fps, e / fps))
                s, gap = None, 0
    return out


def fill_voice_gaps(model, audio16k, words, language, env=None):
    """Lücken mit Stimme einzeln nachträglich erkennen. Findet Whisper Worte (z. B. abgeschnittene
    Rufe), kommen sie dazu; sonst entsteht ein Laut-Wort SOUND_TEXT, das eine eigene Zeile bekommt."""
    env = _envelope_db(audio16k) if env is None else env
    gaps = voice_gaps(env, words)
    if not gaps:
        return words
    # Sprechpegel des Videos: typische Spitze eines erkannten Abschnitts. Leisere Laute ohne Worte sind meist
    # Hintergrundstimmen, die in Packs niemand als Zeile anlegt (gemessen: 10 von 17 unnötig, 2 von 18 echt).
    seg_peak = {}
    for w in words:
        a_, b_ = int(w["s"] * 100), int(w["e"] * 100) + 1
        if b_ > a_ and a_ < len(env):
            seg_peak[w.get("seg")] = max(seg_peak.get(w.get("seg"), -120.0), float(env[a_:b_].max()))
    level = float(np.median(list(seg_peak.values()))) if seg_peak else -30.0
    extra = []
    for s, e in gaps:
        # mit Zusammenhang davor/danach erkennen (kurze Schnipsel allein werden oft Unsinn: „Sabot!“),
        # übernommen werden aber nur Wörter, deren Mitte in der Lücke liegt
        a, b = max(0.0, s - GAPFILL_CONTEXT), min(len(audio16k) / SR, e + GAPFILL_CONTEXT)
        found = []
        try:
            # Anmerkungen erlaubt: so schreibt Whisper bei Lachern „*Gelächter*“ statt erfundener Wörter
            kw = dict(beam_size=5, word_timestamps=True, condition_on_previous_text=False, vad_filter=False,
                      suppress_tokens=[])
            if language and language not in ("auto", "mixed"):
                kw["language"] = language
            segs, _ = model.transcribe(audio16k[int(a * SR): int(b * SR)], **kw)
            for seg in segs:
                # wie Whisper selbst: nur verwerfen, wenn „keine Sprache“ UND der Text unsicher ist.
                # Bei Gesang mit Musik ist no_speech oft hoch, obwohl der Text stimmt (Shrek 2: 0,85 bei -0,46).
                if _drop_segment(seg) or (getattr(seg, "no_speech_prob", 0) > 0.6
                                          and getattr(seg, "avg_logprob", 0) < -1.0):
                    continue
                for w in seg.words or []:
                    if w.word.strip() and _clean(w.word):
                        found.append({"w": w.word, "s": round(a + float(w.start), 3),
                                      "e": round(a + max(float(w.end), float(w.start) + 0.02), 3),
                                      "p": round(float(w.probability), 3), "seg": -1})
        except Exception as ex:  # noqa: BLE001
            print(f"Nacherkennung {s:.1f}s fehlgeschlagen: {ex}", flush=True)
        # nur Worte behalten, die wirklich in der Lücke liegen (keine Dopplung mit Nachbarzeilen)
        found = [w for w in found if s - 0.05 <= (w["s"] + w["e"]) / 2 <= e + 0.05]
        notes = [w for w in found if NOTE_WORD.match(w["w"].strip())]
        if notes and any(LAUGH_NOTE.search(w["w"]) for w in notes):
            found = [dict(w, w="haha", p=max(w["p"], 0.5)) for w in found]   # als Lacher behandeln (unten)
        elif notes:
            found = [w for w in found if w not in notes]   # [Musik], *Applaus* … sind keine Zeilen
        texts = [w["w"].strip() for w in found]
        laughish = found and (all(LAUGH_WORD.fullmatch(t) for t in texts)
                              or (len(texts) >= 2 and all(LAUGH_WORD.fullmatch(t) or LAUGH_SYL.fullmatch(t) for t in texts)))
        if laughish:
            # nur Lachlaute („HAHAHA“, „hehehe“): als Lacher-Zeile, das Lach-Modell überhört sie oft
            from app.pipeline.laugh import laugh_text
            extra.append({"w": " " + laugh_text(language), "s": found[0]["s"], "e": found[-1]["e"],
                          "p": max(w["p"] for w in found), "seg": -1, "laugh": True})
            continue
        if found and float(np.exp(np.mean([np.log(max(1e-4, w["p"])) for w in found]))) < GAPFILL_MIN_P:
            found = []   # zu unsicher: meist erfunden („Go team!“ in fröhlichem Gequietsche) -> Laut-Zeile
        if found:
            extra += found
            continue
        # direkt an ein Wort anschließend: lang gezogenes Wort („Smaaack“, „Heeelp“) statt eigener Laut-Zeile
        before = max((w for w in words if w["e"] <= s + 0.01), key=lambda w: w["e"], default=None)
        after = min((w for w in words if w["s"] >= e - 0.01), key=lambda w: w["s"], default=None)
        if before is not None and s - before["e"] <= CONTINUE_GAP:
            before["e"] = round(e, 3)
        elif after is not None and after["s"] - e <= CONTINUE_GAP:
            after["s"] = round(s, 3)
        elif float(env[int(s * 100): int(e * 100) + 1].max()) >= level - SOUND_MIN_REL_DB:
            extra.append({"w": " " + SOUND_TEXT, "s": round(s, 3), "e": round(e, 3), "p": 0.0, "seg": -1, "sound": True})
    return sorted(words + extra, key=lambda w: w["s"])


def transcribe_range(audio16k, language, quality=None):
    """Kurzen Ausschnitt neu erkennen, gibt nur den Text zurück."""
    from app import models
    q = config.quality(quality)
    ref = models.pick_whisper(quality, language)
    model = get_model(ref)
    if models.is_english_only(ref):
        language = "en"
    kwargs = dict(beam_size=max(5, q["beam"]), patience=q["patience"], vad_filter=False,
                  condition_on_previous_text=False)
    if language and language not in ("auto", "mixed"):
        kwargs["language"] = language
    segments, _ = model.transcribe(audio16k, **kwargs)
    return " ".join(s.text.strip() for s in segments).strip()
