"""Sprecher erkennen.

Ablauf:
1. Wörter zu kurzen Phrasen gruppieren (Pausen / Whisper-Segmente, höchstens 4 s)
2. Stimmprofil je Phrase (ECAPA, dazu ResNet, falls installiert: ordnet in allen Stufen besser zu)
3. Phrasen clustern (feste Anzahl oder automatisch per Schwelle)
4. Lange Phrasen auf Sprecherwechsel prüfen (Fenster-Embeddings) und ggf. teilen
"""
import gc

import numpy as np

from app import config

SR = 16000
PHRASE_GAP = 0.35     # Pause, ab der eine neue Phrase beginnt (s)
MAX_PHRASE = 4.0      # längere Phrasen an der größten inneren Pause teilen (s)
MIN_PART = 1.0        # dabei entstehende Teile nicht kürzer als (s)
MIN_CLUSTER_DUR = 0.8  # kürzere Phrasen gründen keine eigene Figur, sie werden der ähnlichsten zugeordnet (s).
#                        Seit die Phrasen auch an Satzgrenzen geteilt werden, gibt es viele kurze Teile;
#                        mit 0,8 s bleiben Nebenstimmen erkennbar, ohne dass Einwürfe Extra-Figuren erzeugen
#                        (gemessen an 7 Referenz-Packs und 48 künstlichen Dialogen)
MIN_CLUSTER_DUR_LONG = 1.2  # bei viel Sprache (ab LONG_SPEECH s) gründen erst Phrasen ab 1,2 s eine Figur: jede echte
LONG_SPEECH = 200.0         # Figur hat dann genug lange Abschnitte, kurze bilden sonst Sammel-Figuren aus vielen Stimmen
#                             (Among Us, 2 Folgen mit je ~450 s Sprache: 1:1 71,4 -> 73,2 %, 24 -> 9 überzählige
#                             Figuren; kürzere Packs unverändert, Osama 94 -> 96 %)
THRESHOLD = 0.78      # Cosinus-Distanz für automatisches Clustering
FIXED_K_EXTRA = 2     # bei vorgegebener Sprecher-Anzahl: so viele Gruppen mehr bilden, dann kleinste einsortieren
WIN, HOP = 1.5, 0.5   # Fenster für Sprecherwechsel in langen Phrasen
MAX_EMBED = 15.0      # längere Phrasen: nur mittlere 15 s fürs Profil


# Auch an Satzgrenzen teilen (Satzzeichen oder großgeschriebener Satzanfang): Kurze Einwürfe im
# schnellen Wechsel („Yes.“, „I see.“) hängen sonst ohne Pause am Satz des anderen Sprechers.
SPLIT_SENTENCES = True
MIN_SENTENCE = 0.4    # kürzere Teile bleiben am Nachbarn (zu kurz für ein Stimmprofil)


def _split_at_sentences(words, rough):
    from app.pipeline.segment import ends_sentence, starts_sentence
    out = []
    for ph in rough:
        cuts = [i for i in range(ph["w0"] + 1, ph["w1"])
                if ends_sentence(words[i - 1]["w"]) or starts_sentence(words[i]["w"], words[i - 1]["w"])]
        start = ph["w0"]
        for i in cuts:
            left = words[i - 1]["e"] - words[start]["s"]
            right = words[ph["w1"] - 1]["e"] - words[i]["s"]
            if left >= MIN_SENTENCE and right >= MIN_SENTENCE:
                out.append({"s": words[start]["s"], "e": words[i - 1]["e"], "w0": start, "w1": i})
                start = i
        out.append({"s": words[start]["s"], "e": words[ph["w1"] - 1]["e"], "w0": start, "w1": ph["w1"]})
    return out


def make_phrases(words, gap=PHRASE_GAP, max_len=MAX_PHRASE, min_part=MIN_PART):
    """Wörter zu Phrasen gruppieren, kurz genug, dass eine Phrase nur einen Sprecher enthält.

    Lange Phrasen (Rap, Streitgespräch ohne Pause) werden an der größten inneren Pause geteilt;
    sonst landen zwei Sprecher in einem Stimmprofil und werden zusammengeworfen.
    """
    rough, start = [], 0
    for i in range(1, len(words) + 1):
        if i == len(words) or words[i]["s"] - words[i - 1]["e"] > gap or words[i].get("seg") != words[i - 1].get("seg"):
            rough.append({"s": words[start]["s"], "e": words[i - 1]["e"], "w0": start, "w1": i})
            start = i

    if SPLIT_SENTENCES:
        rough = _split_at_sentences(words, rough)

    phrases, queue = [], list(rough)
    while queue:
        ph = queue.pop(0)
        if ph["e"] - ph["s"] <= max_len or ph["w1"] - ph["w0"] < 4:
            phrases.append(ph)
            continue
        best, best_gap = None, -1.0
        for i in range(ph["w0"] + 1, ph["w1"]):
            if words[i - 1]["e"] - ph["s"] < min_part or ph["e"] - words[i]["s"] < min_part:
                continue
            g = words[i]["s"] - words[i - 1]["e"]
            if g > best_gap:
                best, best_gap = i, g
        if best is None:
            phrases.append(ph)
            continue
        queue.insert(0, {"s": words[best]["s"], "e": ph["e"], "w0": best, "w1": ph["w1"]})
        queue.insert(0, {"s": ph["s"], "e": words[best - 1]["e"], "w0": ph["w0"], "w1": best})
    return sorted(phrases, key=lambda p: p["s"])


def _encoder(device, source, savedir):
    from speechbrain.inference.speaker import EncoderClassifier
    try:
        from speechbrain.utils.fetching import LocalStrategy
        extra = {"local_strategy": LocalStrategy.COPY}
    except Exception:
        extra = {}
    return EncoderClassifier.from_hparams(
        source=source, savedir=str(config.MODELS_DIR / savedir),
        run_opts={"device": device}, **extra)


class Embedder:
    """Stimmprofile. Mit `second=True` kommt ein zweites Modell dazu; beide Profile werden
    normiert aneinandergehängt. Die Ähnlichkeit ist dann der Mittelwert aus beiden Modellen."""

    def __init__(self, second=False):
        import torch
        self.torch = torch
        self.device = "cuda:0" if torch.cuda.is_available() else "cpu"
        self.encs = [_encoder(self.device, config.SPEAKER_MODEL, "ecapa")]
        if second:
            self.encs.append(_encoder(self.device, config.SPEAKER_MODEL_2, "resnet"))

    def close(self):
        self.encs.clear()
        gc.collect()
        if self.device.startswith("cuda"):
            self.torch.cuda.empty_cache()

    def embed(self, audio16k, spans, on_progress=None, batch=32):
        """Ein Embedding pro (start, ende)-Bereich."""
        torch = self.torch
        out = None
        order = np.argsort([e - s for s, e in spans])  # ähnliche Längen zusammen -> wenig Padding
        for bi in range(0, len(order), batch):
            idx = order[bi:bi + batch]
            segs = []
            for j in idx:
                s, e = spans[j]
                if e - s > MAX_EMBED:
                    mid = (s + e) / 2
                    s, e = mid - MAX_EMBED / 2, mid + MAX_EMBED / 2
                x = audio16k[int(s * SR):int(e * SR)]
                if len(x) < int(0.4 * SR):  # sehr kurz -> wiederholen
                    x = np.tile(x, int(np.ceil(0.4 * SR / max(1, len(x)))))
                peak = np.abs(x).max() if len(x) else 0
                segs.append(x / peak * 0.9 if peak > 1e-4 else x)
            maxlen = max(len(x) for x in segs)
            wav = np.zeros((len(segs), maxlen), dtype=np.float32)
            lens = np.zeros(len(segs), dtype=np.float32)
            for k, x in enumerate(segs):
                wav[k, :len(x)] = x
                lens[k] = len(x) / maxlen
            wav_t, lens_t = torch.from_numpy(wav).to(self.device), torch.from_numpy(lens).to(self.device)
            parts = []
            for enc in self.encs:
                with torch.no_grad():
                    e = enc.encode_batch(wav_t, lens_t).squeeze(1).float().cpu().numpy()
                e = e / (np.linalg.norm(e, axis=1, keepdims=True) + 1e-9)
                parts.append(e * np.sqrt(1.0 / len(self.encs)))  # beide Modelle gleich gewichtet
            vec = np.concatenate(parts, axis=1)
            if out is None:
                out = np.zeros((len(spans), vec.shape[1]), dtype=np.float32)
            out[idx] = vec
            if on_progress:
                on_progress(min(1.0, (bi + batch) / len(order)))
        return out if out is not None else np.zeros((0, 192), dtype=np.float32)


def _norm(X):
    return X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)


def _centroids(X, labels, ids):
    return _norm(np.stack([X[labels == k].mean(0) for k in ids]))


def cluster_phrases(E, durations, n_speakers=None, threshold=THRESHOLD):
    """Phrasen-Embeddings clustern -> Label je Phrase (0..k-1, nach erstem Auftreten)."""
    from sklearn.cluster import AgglomerativeClustering

    n = len(E)
    if n == 0:
        return np.zeros(0, dtype=int)
    X = _norm(E)
    durations = np.asarray(durations)
    min_dur = MIN_CLUSTER_DUR_LONG if durations.sum() > LONG_SPEECH else MIN_CLUSTER_DUR
    core = np.where(durations >= min_dur)[0]
    if len(core) < 2:
        core = np.arange(n)
    labels = np.full(n, -1)
    if len(core) == 1:
        labels[:] = 0
        return labels
    if n_speakers:
        # Anzahl vorgegeben: erst in etwas mehr Gruppen teilen, dann die kleinsten (nach Sprechzeit) in die
        # ähnlichste große schieben. Direkt k Gruppen bildet sonst oft eine eigene „Figur“ aus zwei kurzen
        # Ausreißern (gemessen an 22 Packs: 77,9 % -> 82,5 % richtig zugeordnet, los & spongebob 69 -> 100 %)
        k = min(int(n_speakers), len(core))
        kk = min(len(core), k + FIXED_K_EXTRA)
        labels[core] = AgglomerativeClustering(n_clusters=kk, metric="cosine", linkage="average").fit_predict(X[core])
        while len(np.unique(labels[core])) > k:
            ids = np.unique(labels[core])
            small = min(ids, key=lambda c: durations[labels == c].sum())
            others = [c for c in ids if c != small]
            cents = _centroids(X, labels, others)
            labels[labels == small] = others[int(np.argmax(X[labels == small].mean(0) @ cents.T))]
    else:
        ac = AgglomerativeClustering(n_clusters=None, distance_threshold=threshold, metric="cosine", linkage="average")
        labels[core] = ac.fit_predict(X[core])

    if not n_speakers:
        # Einzelne kurze Ausreißer (Lachen, Schrei) dem ähnlichsten Cluster geben
        for _ in range(20):
            ids = np.unique(labels[core])
            if len(ids) <= 1:
                break
            tot = {k: durations[core][labels[core] == k].sum() for k in ids}
            small = [k for k in ids if tot[k] < 2.5 and (labels[core] == k).sum() == 1]
            big = [k for k in ids if k not in small]
            if not small or not big:
                break
            cents = _centroids(X, labels, big)
            k = small[0]
            idx = labels == k
            labels[idx] = big[int(np.argmax(X[idx].mean(0) @ cents.T))]

    # kurze Phrasen dem nächsten Zentrum zuordnen
    ids = np.unique(labels[labels >= 0])
    cents = _centroids(X, labels, ids)
    rest = np.where(labels < 0)[0]
    if len(rest):
        labels[rest] = ids[np.argmax(X[rest] @ cents.T, axis=1)]

    mapping = {}
    for lab in labels:
        mapping.setdefault(int(lab), len(mapping))
    return np.array([mapping[int(l)] for l in labels], dtype=int)


def split_by_speaker_change(phrases, labels, words, win_embs, windows, win_phrase, E):
    """Lange Phrasen teilen, wenn ein Teil deutlich zu einem anderen Sprecher passt."""
    if len(windows) == 0:
        return phrases, labels
    X = _norm(E)
    ids = np.unique(labels)
    cents = _centroids(X, labels, ids)
    W = _norm(win_embs)
    sims = W @ cents.T  # Fenster x Sprecher
    new_phrases, new_labels = [], []
    for pi, ph in enumerate(phrases):
        widx = np.where(win_phrase == pi)[0]
        own = int(np.where(ids == labels[pi])[0][0])
        if len(widx) < 6 or len(ids) < 2:
            new_phrases.append(ph)
            new_labels.append(labels[pi])
            continue
        best = np.argmax(sims[widx], axis=1)
        margin = sims[widx, best] - sims[widx, own]
        wl = np.where((best != own) & (margin > 0.12), best, own)
        # Läufe bestimmen, nur Läufe >= 3 Fenster zählen als Wechsel
        runs, start = [], 0
        for i in range(1, len(wl) + 1):
            if i == len(wl) or wl[i] != wl[start]:
                runs.append([start, i, wl[start]])
                start = i
        for r in runs:
            if r[1] - r[0] < 3:
                r[2] = own
        merged = []
        for r in runs:
            if merged and merged[-1][2] == r[2]:
                merged[-1][1] = r[1]
            else:
                merged.append(r)
        if len(merged) == 1:
            new_phrases.append(ph)
            new_labels.append(labels[pi])
            continue
        # Wechselzeitpunkte -> an nächster Wortgrenze teilen
        parts = [[ph["w0"], merged[0][2]]]  # (erstes Wort, Sprecher-Index)
        for r in merged[1:]:
            t = windows[widx[r[0]]][0] + WIN / 2
            best_w = min(range(ph["w0"] + 1, ph["w1"]), key=lambda k: abs(words[k]["s"] - t), default=None)
            if best_w is not None and best_w > parts[-1][0]:
                parts.append([best_w, r[2]])
        for j, (w0, lab_idx) in enumerate(parts):
            w1 = parts[j + 1][0] if j + 1 < len(parts) else ph["w1"]
            new_phrases.append({"s": words[w0]["s"], "e": words[w1 - 1]["e"], "w0": w0, "w1": w1})
            new_labels.append(ids[lab_idx])
    return new_phrases, np.array(new_labels, dtype=int)


def phrase_windows(phrases, min_len=3.0):
    windows, owner = [], []
    for pi, ph in enumerate(phrases):
        if ph["e"] - ph["s"] < min_len:
            continue
        n = int(np.floor((ph["e"] - ph["s"] - WIN) / HOP)) + 1
        for k in range(max(n, 0)):
            s = ph["s"] + k * HOP
            windows.append((s, s + WIN))
            owner.append(pi)
    return windows, np.array(owner, dtype=int)


def apply_labels(words, phrases, labels):
    for ph, lab in zip(phrases, labels):
        for i in range(ph["w0"], ph["w1"]):
            words[i]["spk"] = int(lab)
    return words


def diarize(audio16k, words, n_speakers=None, on_progress=None, quality=None):
    """Komplette Sprechererkennung. Gibt Analyse-Daten (für spätere Neuzuordnung) zurück."""
    phrases = make_phrases(words)
    if not phrases:
        return {"phrases": [], "labels": [], "embeddings": np.zeros((0, 192), np.float32),
                "windows": [], "win_owner": [], "win_embeddings": np.zeros((0, 192), np.float32)}
    from app import models
    second = bool(config.quality(quality).get("speaker2"))
    try:
        second = second and models.installed("resnet")   # nicht installiert: nur ECAPA, kein Download mitten drin
    except Exception:
        pass
    emb = Embedder(second=second)
    try:
        E = emb.embed(audio16k, [(p["s"], p["e"]) for p in phrases],
                      lambda p: on_progress and on_progress(0.5 * p))
        windows, owner = phrase_windows(phrases)
        WE = emb.embed(audio16k, windows, lambda p: on_progress and on_progress(0.5 + 0.45 * p)) if windows else np.zeros((0, E.shape[1]), np.float32)
    finally:
        emb.close()
    result = {"phrases": phrases, "embeddings": E, "windows": windows, "win_owner": owner, "win_embeddings": WE}
    result.update(assign(words, result, n_speakers))
    return result


def assign(words, analysis, n_speakers=None):
    """Clustern + Wechsel prüfen + Wörtern Sprecher geben (auch für Neuzuordnung)."""
    phrases, E = analysis["phrases"], analysis["embeddings"]
    durs = [p["e"] - p["s"] for p in phrases]
    labels = cluster_phrases(E, durs, n_speakers)
    final_phrases, final_labels = split_by_speaker_change(
        phrases, labels, words, analysis["win_embeddings"], analysis["windows"],
        np.asarray(analysis["win_owner"]), E)
    apply_labels(words, final_phrases, final_labels)
    return {"labels": labels.tolist(), "final_phrases": final_phrases, "final_labels": final_labels.tolist()}


def speaker_for_range(final_phrases, final_labels, s, e):
    """Sprecher für einen Zeitbereich (größte Überlappung mit erkannten Phrasen)."""
    best, best_ov = None, 0.0
    score = {}
    for ph, lab in zip(final_phrases, final_labels):
        ov = min(e, ph["e"]) - max(s, ph["s"])
        if ov > 0:
            score[lab] = score.get(lab, 0.0) + ov
    if score:
        return max(score, key=score.get)
    for ph, lab in zip(final_phrases, final_labels):  # nächstgelegene Phrase
        dist = min(abs(ph["s"] - e), abs(ph["e"] - s))
        if best is None or dist < best_ov:
            best, best_ov = lab, dist
    return best if best is not None else 0
