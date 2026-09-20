"""Vorgegebener Text (Liedtext, Drehbuch, Untertitel) statt freier Erkennung.

Whisper erkennt wie immer mit Zeitstempeln. Danach werden seine Wörter mit dem vorgegebenen Text abgeglichen:
  1. beide Texte vereinfachen (klein, ohne Satzzeichen) und die längsten übereinstimmenden Stücke suchen
  2. Übereinstimmungen: Wort aus dem vorgegebenen Text übernehmen (richtige Schreibweise), Zeit von Whisper
  3. dazwischen abweichende Stellen: vorgegebene Wörter auf die Zeit der Whisper-Wörter verteilen, wenn die
     Stücke ungefähr gleich lang sind; Lücken, in denen Whisper nichts gehört hat, bekommen fehlende Wörter nur,
     wenn genug Zeit dafür ist
  4. Anfang und Ende des vorgegebenen Texts, die im Video nicht vorkommen (ganzer Song, Clip nur ein Teil),
     bleiben weg
Jede Zeile des vorgegebenen Texts endet mit einer Zeilengrenze (Liedzeile = eigene Zeile im Pack).
Steht im Text, wer singt oder spricht („[Verse 2: Natalia]“, „PETER: Hallo“), wird der Name gemerkt und
später als Name der Figur vorgeschlagen.
"""
import re
import unicodedata
from difflib import SequenceMatcher

LRC_TAG = re.compile(r"\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]")
# Eine Zeile, die nur aus einer Marke besteht: „[Verse 2: Natalia]“, „[TELEMACHUS]“, „(Chorus)“
MARK = re.compile(r"^\s*(?:\d{1,3}[.)]\s*)?[\[(]([^\])]{1,60})[\])]\s*$")
# Wörter, die einen Abschnitt oder eine Regieanweisung meinen, keinen Namen
SECTION_WORD = re.compile(r"\b(verse|chorus|refrain|bridge|intro|outro|hook|pre-chorus|post-chorus|strophe|vers|"
                          r"part|teil|couplet|estribillo|instrumental|break|breakdown|solo|interlude|reprise|coda|"
                          r"finale|spoken|skit|sample|drop|ad-?libs?|silence|applause|applaus|laughter|lachen|"
                          r"music|musik|cheering|crowd|whisper|flüster|singing|chant|sfx|sound|effects)\b", re.I)
MANY = re.compile(r"\s(?:&|\+|,|und|and|con|with|feat\.?|x)\s", re.I)   # mehrere Namen: dann kein Hinweis
LIST_NO = re.compile(r"^\s*\d{1,3}[.)]\s+(?=\S)")           # „12. “ am Zeilenanfang (nummerierte Liedtexte)
BRACKETS = re.compile(r"\[[^\]]*\]")           # Regieanweisungen, Abschnitte: [Chorus], [lacht]
SPEAKER = re.compile(r"^\s*([\w .'\-]{1,30}):\s+")   # „Peter: Hallo“ im Drehbuch
NOT_A_NAME = {"both", "all", "alle", "everyone", "everybody", "together", "zusammen", "chorus", "chor",
              "refrain", "ensemble", "cast", "group", "gruppe", "beide", "tutti", "todos", "coro",
              "everyone else", "alle zusammen", "all together"}
MIN_MATCH = 0.3        # so viel des Erkannten muss wiederzufinden sein, sonst passt der Text nicht zum Video
MIN_WORD_GAP = 0.12    # Mindestzeit je eingefügtem Wort, wenn Whisper dort nichts gehört hat (s)
MIN_VOICED = 0.5       # eingefügt wird nur, wo die Stimmen-Spur so viel Stimme zeigt (sonst steht das Wort nur im Text)
# In Klammern: gesungener Hintergrund („(Merci)“) bleibt als Wort, echte Anmerkungen fallen weg
NOTE = re.compile(r"\((?:[^)]*\b(?:laugh|lach|kicher|chuckl|giggl|sigh|seufz|scream|schrei|gasp|grunt|cough|hust|whisper|flüster|"
                  r"music|musik|applaus|applause|instrumental|singing|sings|singt)[^)]*)\)", re.I)
# Kopierschutz mancher Liedtext-Seiten: einzelne Buchstaben als gleich aussehende kyrillische/griechische Zeichen
HOMOGLYPHS = str.maketrans("аеорсухіјѕԁɡАВЕКМНОРСТХІЈЅοαΑΒΕΗΙΚΜΝΟΡΤΧΥ",
                           "aeopcyxijsdgABEKMHOPCTXIJSoaABEHIKMNOPTXY")


def _fold(word):
    """Wort mit lateinischen Buchstaben und einzelnen Doppelgängern aus anderen Schriften: Doppelgänger ersetzen."""
    if any("a" <= ch.lower() <= "z" for ch in word) and any(ord(ch) > 0x36F for ch in word):
        return word.translate(HOMOGLYPHS)
    return word


def _norm(tok):
    t = unicodedata.normalize("NFKC", tok).lower().replace("’", "'")
    return re.sub(r"[^\w']", "", t).strip("'")


def _who(name):
    """Namen aus einer Abschnittsmarke oder einem Sprecher-Vorsatz säubern. Mehrere Namen: kein Hinweis."""
    name = re.sub(r"\s+", " ", (name or "").strip(" .:-–—'\"“”„"))
    if not name or MANY.search(name) or not any(ch.isalpha() for ch in name):
        return None
    if len(name) > 30 or len(name.split()) > 4:
        return None
    if name.casefold() in NOT_A_NAME:
        return None   # „Both“, „Alle“: sagt nicht, wer singt
    return name.title() if name.isupper() else name   # „TELEMACHUS“ -> „Telemachus“


def clean(raw):
    """Nur die Zeilen des vorgegebenen Texts (ohne Namen)."""
    return [line for line, _ in clean_lines(raw)]


def clean_lines(raw):
    """Vorgegebenen Text in Zeilen zerlegen: Zeitmarken, Abschnittsnamen, Klammer-Anweisungen und
    Sprechernamen am Zeilenanfang fallen weg, leere Zeilen auch.
    -> [(Zeile, wer sie singt oder spricht oder None)]"""
    lines = []
    who = None
    for line in (raw or "").replace("\r", "").split("\n"):
        line = LRC_TAG.sub("", line)
        if not line.strip():
            continue
        mark = MARK.match(line)
        if mark:
            inside = mark.group(1)
            if SECTION_WORD.search(inside):
                # „[Verse 2: Natalia]“ nennt den Namen hinter dem Doppelpunkt, „[Bridge]“ nennt keinen
                who = _who(inside.split(":", 1)[1]) if ":" in inside else None
            else:
                who = _who(inside.split(":", 1)[-1])   # „[TELEMACHUS]“ ist selbst der Name
            continue
        line = LIST_NO.sub("", line)
        spk = SPEAKER.match(line)
        here = _who(spk.group(1)) if spk else who
        line = BRACKETS.sub(" ", line)
        line = NOTE.sub(" ", line)
        line = line.replace("(", " ").replace(")", " ")
        line = SPEAKER.sub("", line)
        line = " ".join(_fold(w) for w in line.split())
        line = re.sub(r"\s+", " ", line).strip().strip('"“”„«» ')
        if line and any(ch.isalnum() for ch in line):
            lines.append((line, here))
    return lines


def tokens(lines):
    """[(Wort wie geschrieben, vereinfacht, Zeilenende?, wer)]. Nimmt Zeilen oder (Zeile, wer)."""
    out = []
    for item in lines:
        line, who = item if isinstance(item, tuple) else (item, None)
        words = [w for w in line.split(" ") if _norm(w)]
        for i, w in enumerate(words):
            out.append((w, _norm(w), i == len(words) - 1, who))
    return out


def _spread(ref_toks, s, e, seg, p=0.95, spk=None):
    """Wörter gleichmäßig nach Zeichenzahl auf [s, e] verteilen."""
    lens = [max(1, len(t[1])) for t in ref_toks]
    total, t, out = float(sum(lens)), s, []
    for (word, _, eol, who), n in zip(ref_toks, lens):
        d = (e - s) * n / total
        w = {"w": " " + word, "s": round(t, 3), "e": round(t + d, 3), "p": p, "seg": seg, "ref": True, "eol": eol}
        if who:
            w["who"] = who
        if spk is not None:
            w["spk"] = spk
        out.append(w)
        t += d
    return out


def _voiced(env, a, b, fps=100):
    """Anteil der Zeit in [a, b] mit Stimme in der Stimmen-Spur (Hüllkurve in dB, 100 je Sekunde)."""
    if env is None or b <= a:
        return 1.0
    seg = env[int(a * fps):max(int(a * fps) + 1, int(b * fps))]
    if not len(seg):
        return 0.0
    thr = max(float(sorted(env)[int(len(env) * 0.99)]) - 30.0, -55.0)
    return float((seg > thr).mean())


def apply(words, raw_text, env=None):
    """Whisper-Wörter mit dem vorgegebenen Text abgleichen. env: Hüllkurve der Stimmen-Spur (für Einfügungen).
    -> (neue Wörter, Bericht)"""
    ref = tokens(clean_lines(raw_text))
    speech = [w for w in words if not w.get("sound") and not w.get("laugh") and _norm(w["w"])]
    ids = {id(w) for w in speech}
    others = [w for w in words if id(w) not in ids]
    report = {"ref_words": len(ref), "heard": len(speech), "matched": 0, "replaced": 0, "inserted": 0, "kept": 0, "used": False}
    if not ref or not speech:
        return words, report
    wn, rn = [_norm(w["w"]) for w in speech], [t[1] for t in ref]
    ops = SequenceMatcher(None, wn, rn, autojunk=False).get_opcodes()
    matched = sum(i2 - i1 for tag, i1, i2, _, _ in ops if tag == "equal")
    report["matched"] = matched
    if matched < MIN_MATCH * len(speech):
        report["reason"] = "Der Text passt nicht zu dem, was im Video gesprochen wird."
        return words, report
    eq = [k for k, op in enumerate(ops) if op[0] == "equal"]
    first, last = eq[0], eq[-1]
    out = []
    for k, (tag, i1, i2, j1, j2) in enumerate(ops):
        W, R = speech[i1:i2], ref[j1:j2]
        edge = k < first or k > last
        if tag == "equal":
            for w, r in zip(W, R):
                new = dict(w, w=(" " if w["w"].startswith(" ") else "") + r[0], ref=True, eol=r[2])
                if r[3]:
                    new["who"] = r[3]
                out.append(new)
        elif tag == "replace":
            ratio = len(R) / len(W)
            lo, hi = (0.6, 1.7) if edge else (0.4, 2.5)
            if lo <= ratio <= hi:
                out += _spread(R, W[0]["s"], W[-1]["e"], W[0].get("seg"), spk=W[0].get("spk"))
                report["replaced"] += len(R)
            else:
                out += W
                report["kept"] += len(W)
        elif tag == "delete":
            out += W
            report["kept"] += len(W)
        elif tag == "insert" and not edge:
            prev_e = out[-1]["e"] if out else 0.0
            nxt = speech[i1]["s"] if i1 < len(speech) else prev_e
            if nxt - prev_e >= MIN_WORD_GAP * len(R) and _voiced(env, prev_e, nxt) >= MIN_VOICED:
                pad = min(0.05, (nxt - prev_e) * 0.1)
                out += _spread(R, prev_e + pad, nxt - pad, out[-1].get("seg") if out else None, p=0.6,
                               spk=out[-1].get("spk") if out else None)
                report["inserted"] += len(R)
    report["used"] = True
    return sorted(out + others, key=lambda w: w["s"]), report
