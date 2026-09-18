"""Lachen erkennen (Audio-Klassifikation mit AST, trainiert auf AudioSet)."""
import gc

import numpy as np

SR = 16000
MODEL = "MIT/ast-finetuned-audioset-10-10-0.4593"
LAUGH_LABELS = {"Laughter", "Baby laughter", "Giggle", "Snicker", "Belly laugh", "Chuckle, chortle"}
WIN, HOP = 2.0, 0.5   # Analysefenster (s)
FPS = 10              # Auflösung der Lach-Kurve
THRESHOLD = 0.3       # ab welcher Wahrscheinlichkeit Lachen zählt
MIN_DUR = 0.4         # kürzere Lacher ignorieren (s)
MERGE_GAP = 0.4       # Lücken bis hier zusammenfassen (s)


def laugh_curve(audio16k, on_progress=None, batch=24):
    """Lach-Wahrscheinlichkeit pro 0,1 s."""
    import torch
    from transformers import ASTFeatureExtractor, ASTForAudioClassification

    device = "cuda" if torch.cuda.is_available() else "cpu"
    fe = ASTFeatureExtractor.from_pretrained(MODEL)
    model = ASTForAudioClassification.from_pretrained(MODEL).to(device).eval()
    if device == "cuda":
        model = model.half()
    ids = [int(i) for i, name in model.config.id2label.items() if name in LAUGH_LABELS]

    n = len(audio16k)
    win, hop = int(WIN * SR), int(HOP * SR)
    starts = list(range(0, max(1, n - win) + 1, hop))
    if starts[-1] + win < n:
        starts.append(max(0, n - win))
    curve = np.zeros(int(np.ceil(n / SR * FPS)) + 1, dtype=np.float32)
    try:
        for bi in range(0, len(starts), batch):
            chunk = starts[bi:bi + batch]
            segs = [audio16k[s:s + win] for s in chunk]
            feats = fe(segs, sampling_rate=SR, return_tensors="pt")["input_values"].to(device)
            if device == "cuda":
                feats = feats.half()
            with torch.no_grad():
                probs = torch.sigmoid(model(input_values=feats).logits.float())[:, ids].max(dim=1).values.cpu().numpy()
            for s, p in zip(chunk, probs):
                a, b = int(s / SR * FPS), int((s + win) / SR * FPS)
                curve[a:b] = np.maximum(curve[a:b], p)
            if on_progress:
                on_progress(min(1.0, (bi + batch) / len(starts)))
    finally:
        del model
        gc.collect()
        if device == "cuda":
            torch.cuda.empty_cache()
    return curve


def regions_from_curve(curve, threshold=THRESHOLD):
    on = curve >= threshold
    regions, start = [], None
    for i, v in enumerate(np.append(on, False)):
        if v and start is None:
            start = i
        elif not v and start is not None:
            regions.append([start / FPS, i / FPS, float(curve[start:i].max())])
            start = None
    merged = []
    for r in regions:
        if merged and r[0] - merged[-1][1] <= MERGE_GAP:
            merged[-1][1] = r[1]
            merged[-1][2] = max(merged[-1][2], r[2])
        else:
            merged.append(r)
    return [r for r in merged if r[1] - r[0] >= MIN_DUR]


def tighten(regions, env_db, fps=100):
    """Ränder an die tatsächliche Lautstärke der Stimmen-Spur anpassen (Fenster sind 2 s grob)."""
    out = []
    for s, e, p in regions:
        a, b = max(0, int(s * fps)), min(len(env_db), int(e * fps))
        if b - a < 3:
            continue
        seg = env_db[a:b]
        loud = np.where(seg >= max(seg.max() - 25.0, -50.0))[0]
        if len(loud) == 0:
            continue
        ns, ne = (a + loud[0]) / fps - 0.05, (a + loud[-1]) / fps + 0.1
        if ne - ns >= MIN_DUR * 0.75:
            out.append([round(float(max(0.0, ns)), 3), round(float(ne), 3), round(float(p), 3)])
    return out


def speech_overlap(s, e, words):
    """Anteil des Bereichs, in dem erkannte Wörter liegen."""
    tot = 0.0
    for w in words:
        ov = min(e, w["e"]) - max(s, w["s"])
        if ov > 0:
            tot += ov
    return tot / max(1e-6, e - s)


def detect(audio16k, words, env_db, on_progress=None):
    """Lach-Bereiche, die nicht schon Teil gesprochener Wörter sind."""
    curve = laugh_curve(audio16k, on_progress)
    regions = tighten(regions_from_curve(curve), env_db)
    return [r for r in regions if speech_overlap(r[0], r[1], words) < 0.5]


LAUGH_TEXT = {"de": "(lacht)", "en": "(laughs)", "es": "(se ríe)", "fr": "(rit)", "pt": "(ri)", "it": "(ride)",
              "nl": "(lacht)", "pl": "(śmieje się)", "tr": "(güler)", "ru": "(смеётся)", "uk": "(сміється)",
              "cs": "(směje se)", "sk": "(smeje sa)", "sr": "(smeje se)", "sv": "(skrattar)", "da": "(griner)",
              "no": "(ler)", "fi": "(nauraa)", "hu": "(nevet)", "ro": "(râde)", "el": "(γελάει)", "ja": "（笑）",
              "ko": "(웃음)", "zh": "（笑）", "ar": "(يضحك)", "hi": "(हँसी)", "id": "(tertawa)", "vi": "(cười)",
              "th": "(หัวเราะ)", "he": "(צוחק)"}


def laugh_text(language):
    """Beschriftung einer Lach-Zeile in der Sprache des Videos (sonst Englisch)."""
    return LAUGH_TEXT.get((language or "en")[:2], "(laughs)")


def _overlap_share(s, e, spans):
    tot = sum(max(0.0, min(e, b) - max(s, a)) for a, b in spans)
    return tot / max(1e-6, e - s)


def assign_to_characters(audio16k, regions, lines):
    """Lacher dem Charakter mit der ähnlichsten Stimme zuordnen; bei Unsicherheit dem zeitlich nächsten."""
    from app.pipeline import diarize

    by_char = {}
    for ln in lines:
        if ln["end"] - ln["start"] >= 0.8 and ln.get("chars"):
            by_char.setdefault(ln["chars"][0], []).append(ln)
    char_ids = list(by_char)
    nearest_char = lambda mid: min(lines, key=lambda ln: min(abs(ln["start"] - mid), abs(ln["end"] - mid)))["chars"][0]
    if len(char_ids) < 2:
        return [char_ids[0] if char_ids else nearest_char((r[0] + r[1]) / 2) for r in regions]

    spans, owner = [], []
    for cid in char_ids:
        for ln in sorted(by_char[cid], key=lambda l: l["start"] - l["end"])[:25]:  # längste Zeilen
            spans.append((ln["start"], ln["end"]))
            owner.append(cid)
    emb = diarize.Embedder()
    try:
        E = emb.embed(audio16k, spans)
        R = emb.embed(audio16k, [(r[0], r[1]) for r in regions])
    finally:
        emb.close()
    E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-9
    R /= np.linalg.norm(R, axis=1, keepdims=True) + 1e-9
    owner = np.array(owner)
    cents = np.stack([E[owner == cid].mean(0) for cid in char_ids])
    cents /= np.linalg.norm(cents, axis=1, keepdims=True) + 1e-9
    out = []
    for r, v in zip(regions, R):
        sims = cents @ v
        order = np.argsort(-sims)
        if sims[order[0]] - sims[order[1]] > 0.08:
            out.append(char_ids[order[0]])
        else:
            out.append(nearest_char((r[0] + r[1]) / 2))
    return out


def find_laughs(audio16k, lines, words, env_db, language, on_progress=None):
    """Vorschläge für Lach-Zeilen: nicht schon Teil gesprochener Wörter oder bestehender Zeilen."""
    regions = detect(audio16k, words, env_db, on_progress)
    spans = [(ln["start"], ln["end"]) for ln in lines]
    regions = [r for r in regions if _overlap_share(r[0], r[1], spans) < 0.5]
    if not regions:
        return []
    if not lines:
        chars = [None] * len(regions)
    else:
        chars = assign_to_characters(audio16k, regions, lines)
    text = laugh_text(language)
    return [{"start": r[0], "end": r[1], "text": text, "chars": [c] if c else [], "laugh": True, "score": r[2]}
            for r, c in zip(regions, chars)]


def fit_into(lines, new, duration, gap=0.02, min_len=0.2):
    """Wie im Editor: Zeile desselben Sprechers nicht überlappen lassen (kürzen oder verwerfen)."""
    lo, hi = 0.0, duration
    mid = (new["start"] + new["end"]) / 2
    for o in lines:
        if not set(o["chars"]) & set(new["chars"]):
            continue
        if o["end"] <= mid:
            lo = max(lo, o["end"] + gap)
        elif o["start"] >= mid:
            hi = min(hi, o["start"] - gap)
        else:
            return None
    s, e = max(new["start"], lo), min(new["end"], hi)
    if e - s < min_len:
        return None
    return dict(new, start=round(s, 3), end=round(e, 3))
