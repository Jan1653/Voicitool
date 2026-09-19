"""Aus Wörtern (mit Sprecher) Pack-Zeilen bauen und Grenzen am Stimmsignal ausrichten."""
import re

import numpy as np

SENTENCE_END = re.compile(r"[.!?…]+[\"'“”»«)]*$")
CLAUSE_END = re.compile(r"[,;:–-]+[\"'“”»«)]*$")


def ends_sentence(text):
    """Echtes Satzende? (nicht „z.“, „B.“, „1975.“ stehen mitten im Satz)"""
    t = text.strip()
    if not SENTENCE_END.search(t):
        return False
    core = t.rstrip("\"'“”»«)").rstrip(".!?…")
    if len(core) <= 1:
        return False
    return not (core[-1].isdigit() and t.endswith("."))


# Wörter, die großgeschrieben fast nur am Satzanfang stehen. Liefert Whisper keine Satzzeichen
# („…Dunkin Donuts And you still went…“), zeigt die Großschreibung trotzdem den neuen Satz.
SENTENCE_STARTERS = set("""
and but so or oh well yes yeah no nope okay ok hey hi hello what why how who where when which
you your you're you've you'll we we're they they're he he's she she's it it's this that that's
there there's here here's the a an my our their his her do does did don't is are was were can
can't could would should will now then if because maybe please look listen come let let's wait
just also still even see thank thanks sorry uh um ah mm hmm huh wow alright right sure
und aber oder also ja nein nee doch nun dann jetzt wenn weil was wie wer wo warum wieso
ich du wir ihr er es das die der dieser diese mein meine dein deine hallo hey na oh ach tja gut
okay moment warte schau guck komm
""".split())


def starts_sentence(word, prev):
    """Beginnt mit word ein neuer Satz, obwohl davor kein Satzzeichen steht?"""
    w = word.strip()
    if not w or not w[0].isupper() or w in ("I", "I'm", "I'll", "I've", "I'd"):
        return False
    if CLAUSE_END.search(prev.strip()) or ends_sentence(prev):
        return False   # nach Komma: eher Aufzählung; nach Punkt: ohnehin getrennt
    core = w.lower().strip(".,!?\"'“”…")
    return core in SENTENCE_STARTERS and not (len(w) > 1 and w.isupper() and prev.strip().isupper())


def _best_cut(ws, min_part=1.2):
    """Beste Trennstelle in einer zu langen Wortfolge: Satzende > Komma > größte Pause."""
    start, end = ws[0]["s"], ws[-1]["e"]
    best, best_score = None, -1.0
    for i in range(1, len(ws)):
        left, right = ws[i - 1]["e"] - start, end - ws[i]["s"]
        if left < min_part or right < 0.4:
            continue
        prev = ws[i - 1]["w"].strip()
        score = min(ws[i]["s"] - ws[i - 1]["e"], 1.0)
        if ends_sentence(prev):
            score += 3
        elif CLAUSE_END.search(prev):
            score += 1.5
        score += 0.3 * (left / (end - start))  # bei Gleichstand lieber später teilen
        if score > best_score:
            best, best_score = i, score
    return best


def _boundary_strength(words, j):
    """Wie deutlich beginnt vor Wort j ein neuer Satz? 0 = gar nicht."""
    prev, w = words[j - 1], words[j]
    score = 0.0
    if ends_sentence(prev["w"]):
        score += 2.0
    elif starts_sentence(w["w"], prev["w"]):
        score += 1.5
    gap = w["s"] - prev["e"]
    if gap >= 0.25:
        score += min(gap, 1.0)
    return score


def snap_speaker_changes(words, max_shift=2):
    """Sprecherwechsel liegen oft ein Wort daneben (die Stimmprofile sind grob, Satzgrenzen genau).
    Liegt ein Satzanfang höchstens max_shift Wörter entfernt, den Wechsel dorthin verschieben."""
    words = [dict(w) for w in words]
    i = 1
    while i < len(words):
        if words[i]["spk"] == words[i - 1]["spk"]:
            i += 1
            continue
        old, new = words[i - 1]["spk"], words[i]["spk"]
        here = _boundary_strength(words, i)
        best, best_j = here, i
        for j in range(max(1, i - max_shift), min(len(words), i + max_shift + 1)):
            if j == i:
                continue
            seg = words[min(i, j):max(i, j)]
            # nur verschieben, wenn die übersprungenen Wörter kurz sind und zu einem der beiden Sprecher gehören
            if any(w["spk"] not in (old, new) for w in seg) or sum(w["e"] - w["s"] for w in seg) > 1.2:
                continue
            s = _boundary_strength(words, j) - 0.3 * abs(j - i)
            if s > best + 0.5:
                best, best_j = s, j
        if best_j < i:
            for w in words[best_j:i]:
                w["spk"] = new
        elif best_j > i:
            for w in words[i:best_j]:
                w["spk"] = old
        i = max(i, best_j) + 1
    return words


def build_lines(words, pause_split=0.8, target_len=6.0, max_len=10.0, sentence_pause=None,
                join_short=None, join_sounds=0.5):
    """sentence_pause: Sätze desselben Sprechers nur trennen, wenn dazwischen mehr Pause liegt (None = jeder Satz).
    join_short: (Länge, Pause) sehr kurze Sätze („What? What? What?“) nicht trennen, solange die Zeile kürzer und
                die Pause kleiner ist. join_sounds: aufeinanderfolgende Laute „(…)“ bis zu dieser Pause verbinden."""
    words = snap_speaker_changes(words)
    lines, cur = [], None

    for w in words:
        if cur is None:
            cur = {"spk": w["spk"], "words": [w]}
            continue
        last = cur["words"][-1]
        length = last["e"] - cur["words"][0]["s"]
        gap = w["s"] - last["e"]
        ended = ends_sentence(last["w"])
        new_sentence = (ended or starts_sentence(w["w"], last["w"])   # Satzzeichen oder Großschreibung
                        or bool(last.get("eol")))   # Zeilenende im vorgegebenen Text (Liedzeile)
        split = (
            w["spk"] != cur["spk"]
            or bool(w.get("laugh")) != bool(last.get("laugh"))
            or (bool(w.get("sound")) != bool(last.get("sound")))   # Laute ohne Worte: eigene Zeile
            or (w.get("sound") and last.get("sound") and (join_sounds is None or gap > join_sounds))
            or (new_sentence and (sentence_pause is None or gap > sentence_pause)
                and not (join_short and length < join_short[0] and gap < join_short[1]))
            or gap > pause_split * 2  # mitten im Satz nur bei deutlicher Pause
            or (w.get("seg") != last.get("seg") and length >= target_len * 0.5)
        )
        if split:
            lines.append(cur)
            cur = {"spk": w["spk"], "words": [w]}
            continue
        cur["words"].append(w)
        # zu lang -> an sinnvoller Stelle innerhalb der Zeile teilen
        while cur["words"][-1]["e"] - cur["words"][0]["s"] > max_len:
            cut = _best_cut(cur["words"])
            if cut is None:
                break
            lines.append({"spk": cur["spk"], "words": cur["words"][:cut]})
            cur = {"spk": cur["spk"], "words": cur["words"][cut:]}
    if cur:
        lines.append(cur)

    out = []
    for ln in lines:
        text = "".join(w["w"] for w in ln["words"]).strip()
        text = re.sub(r"\s+", " ", text)
        out.append({"start": ln["words"][0]["s"], "end": ln["words"][-1]["e"], "text": text, "spk": ln["spk"]})
    return out


def envelope_db(audio16k, hop=160):
    n = len(audio16k) // hop
    frames = audio16k[: n * hop].reshape(n, hop)
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-10)
    return 20 * np.log10(rms)  # 10 ms pro Wert


def refine_bounds(lines, env_db, pre=0.35, post=0.5, fps=100,
                  lead_s=0.10, tail_s=0.10, pre_pad=0.05, post_pad=0.10, follow_s=1.0):
    """Start/Ende an den tatsächlichen Sprechbeginn anpassen.

    Die Schwelle richtet sich nach dem Rauschteppich der Umgebung (nicht nur nach dem Lautesten).
    Der Einsatz muss kurz anhalten (kein Knacken), und weiche Anlaute davor werden mitgenommen.
      lead_s:   so weit darf der weiche Anlaut vor dem Einsatz zurückreichen
      tail_s:   so weit darf das Ausklingen nach dem letzten lauten Punkt nachlaufen
      pre_pad:  Sicherheitsvorlauf vor dem Einsatz
      post_pad: Sicherheitsnachlauf nach dem Ende
      follow_s: klingt die Stimme am Ende durchgehend laut weiter (gehaltener Ton, gezogener Ruf),
                geht das Ende bis zu so viele Sekunden mit (Whisper setzt das Wortende dort oft zu früh)
    """
    n = len(env_db)
    floor_all = float(np.percentile(env_db, 10))
    hold = max(2, int(0.03 * fps))     # 30 ms müssen laut bleiben
    lead = int(lead_s * fps)
    tail = int(tail_s * fps)
    for ln in lines:
        a = max(0, int((ln["start"] - pre) * fps))
        b = min(n, int((ln["end"] + post) * fps))
        if b - a < 3:
            continue
        seg = env_db[a:b]
        peak = float(seg.max())
        floor = max(floor_all, float(np.percentile(seg, 10)))
        if peak - floor < 6.0:
            continue  # nichts Deutliches zu finden
        # sicherer Sprech-Pegel: über dem Rauschen, aber nicht in den Trennungs-Resten
        thr = min(max(min(floor + 12.0, peak - 12.0), peak - 34.0, -58.0), peak - 8.0)
        soft = max(floor + 7.0, thr - 8.0)                      # weiche Anlaute/Ausklang

        # Einsatz = deutlichste Flanke in der Nähe der Schätzung (nicht der erste leise Anstieg)
        s_lo, s_hi = a, min(n - hold, int((ln["start"] + 0.35) * fps))
        ctx = int(0.15 * fps)
        onset, best = None, -1e9
        for i in range(s_lo, max(s_lo, s_hi)):
            if not np.all(env_db[i:i + hold] >= thr):
                continue
            after = float(np.mean(env_db[i:min(n, i + ctx)]))
            before = float(np.mean(env_db[max(0, i - ctx):i])) if i else after
            sc = (after - before) - 12.0 * abs(i / fps - ln["start"])  # nah an der Schätzung bevorzugen
            if sc > best:
                onset, best = i, sc
        if onset is not None:
            back, limit = onset, max(s_lo, onset - lead)
            while back > limit and env_db[back - 1] >= soft:
                back -= 1
            ln["start"] = max(0.0, back / fps - pre_pad)

        e_lo, e_hi = max(0, int((ln["end"] - 0.3) * fps)), b
        loud = np.where(env_db[e_lo:e_hi] >= thr)[0]
        if len(loud):
            end_i = e_lo + int(loud[-1])
            if follow_s and end_i >= b - 2:
                # noch laut am Rand des Suchfensters: mitgehen, solange die Stimme anhält (kurze Lücken ok)
                lim, gap, k = min(n - 1, end_i + int(follow_s * fps)), 0, end_i
                while k < lim:
                    k += 1
                    if env_db[k] >= thr:
                        end_i, gap = k, 0
                    else:
                        gap += 1
                        if gap > int(0.06 * fps):
                            break
                b = min(n, end_i + tail + 2)
            limit = min(b - 1, end_i + tail)
            while end_i < limit and env_db[end_i + 1] >= soft:
                end_i += 1
            ln["end"] = (end_i + 1) / fps + post_pad
    # Erweiterungen dürfen nicht in die Nachbarzeile ragen -> an der leisesten Stelle trennen
    for i in range(1, len(lines)):
        prev, cur = lines[i - 1], lines[i]
        if cur["start"] >= prev["end"]:
            continue
        lo = int(max(prev["start"] + 0.15, cur["start"]) * fps)
        hi = int(min(cur["end"] - 0.15, prev["end"]) * fps) + 1
        if hi - lo >= 2 and hi <= n:
            cut = (lo + int(np.argmin(env_db[lo:hi]))) / fps   # Senke zwischen den beiden
        else:
            cut = (prev["end"] + cur["start"]) / 2
        prev["end"] = max(prev["start"] + 0.1, min(prev["end"], cut))
        cur["start"] = max(cur["start"], prev["end"])
    for ln in lines:
        ln["start"] = round(ln["start"], 3)
        ln["end"] = round(max(ln["end"], ln["start"] + 0.2), 3)
    return lines


def waveform_peaks(audio16k, per_second=100):
    hop = 16000 // per_second
    n = len(audio16k) // hop
    frames = np.abs(audio16k[: n * hop].reshape(n, hop)).max(axis=1)
    peak = frames.max() if n else 1.0
    scaled = np.clip(frames / (peak + 1e-9) * 255, 0, 255).astype(np.uint8)
    return scaled.tolist()
