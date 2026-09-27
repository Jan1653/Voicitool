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
import html
import re
import unicodedata

import numpy as np
from collections import Counter
from difflib import SequenceMatcher

LRC_TAG = re.compile(r"\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]")
# Eine Zeile, die nur aus einer Marke besteht: „[Verse 2: Natalia]“, „[TELEMACHUS]“, „(Chorus)“
MARK = re.compile(r"^\s*(?:\d{1,3}[.)]\s*)?[\[(]([^\])]{1,60})[\])]\s*$")
# Wörter, die einen Abschnitt oder eine Regieanweisung meinen, keinen Namen
SECTION_WORD = re.compile(r"\b(verse|chorus|refrain|bridge|intro|outro|hook|pre-chorus|post-chorus|strophe|vers|"
                          r"part|teil|couplet|estribillo|instrumental|break|breakdown|solo|interlude|reprise|coda|"
                          r"finale|spoken|skit|sample|drop|ad-?libs?|silence|applause|applaus|laughter|lachen|"
                          r"music|musik|cheering|crowd|whisper|flüster|singing|chant|sfx|sound|effects|"
                          # Genius-Übersetzungen und fremdsprachige Seiten (sonst wird „Puente“ zur Figur)
                          r"verso|estrofa|coro|puente|precoro|pre-coro|post-coro|introducción|interludio|"
                          r"refrão|ponte|ritornello|strofa|pont|zwischenteil|letra|paroles|testo)\b", re.I)
MANY = re.compile(r"\s(?:&|\+|,|und|and|con|with|feat\.?|x)\s", re.I)   # mehrere Namen: dann kein Hinweis
LIST_NO = re.compile(r"^\s*\d{1,3}[.)]\s+(?=\S)")           # „12. “ am Zeilenanfang (nummerierte Liedtexte)
BRACKETS = re.compile(r"\[[^\]]*\]")           # Regieanweisungen, Abschnitte: [Chorus], [lacht]
SPEAKER = re.compile(r"^\s*([\w .'\-]{1,30}):\s*(?=\S)")   # „Peter: Hallo“ im Drehbuch, auch ohne Leerzeichen
SRT_ARROW = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3}\s*-->")   # Untertitel-Datei eingefügt
BARE_NO = re.compile(r"^\s*\d{1,3}(?!\S)\s*")        # „12 Text“ ohne Punkt: Zeilennummer mancher Liedtext-Seiten
ONLY_NOTE = re.compile(r"^\s*\*[^*]*\*\s*$")          # ganze Zeile in Sternen: Regieanweisung
NOT_A_NAME = {"both", "all", "alle", "everyone", "everybody", "together", "zusammen", "chorus", "chor",
              "refrain", "ensemble", "cast", "group", "gruppe", "beide", "tutti", "todos", "coro",
              "everyone else", "alle zusammen", "all together"}
MIN_MATCH = 0.3        # so viel des Erkannten muss wiederzufinden sein, sonst passt der Text nicht zum Video
MIN_WORD_GAP = 0.12    # Mindestzeit je eingefügtem Wort, wenn Whisper dort nichts gehört hat (s)
MIN_VOICED = 0.3       # eingefügt wird nur, wo die Stimmen-Spur so viel Stimme zeigt (sonst steht das Wort nur im Text)
MIN_OF_REF = 0.6       # oder: so viel des vorgegebenen Texts wurde wiedergefunden (Teiltext, z. B. nur der Refrain)
# In Klammern: gesungener Hintergrund („(Merci)“) bleibt als Wort, echte Anmerkungen fallen weg
NOTE_WORD = re.compile(r"\b(?:laugh|lach|kicher|chuckl|giggl|sigh|seufz|scream|schrei|gasp|grunt|cough|hust|whisper|"
                       r"flüster|music|musik|applaus|applause|instrumental|singing|sings|singt)", re.I)
NOTE_PAREN = re.compile(r"\(([^()]{0,200})\)")   # feste Grenze: sonst rechnet der Ausdruck bei offenen Klammern ewig
# Beiwerk, das beim Kopieren von Genius mitkommt: Werbeblock mitten im Text, Kopfzeile, „123Embed“ am Ende
GENIUS_ADS = re.compile(r"^\s*you might also like\s*$", re.I)
GENIUS_HEAD = re.compile(r"^\s*\d+\s*contributors?", re.I)   # „3 ContributorsUnder Pressure Lyrics“
GENIUS_EMBED = re.compile(r"(?<=[^\s\d])\d*Embed\s*$")
DUET = " & "           # mehrere Namen in einer Marke („[Chorus: Bowie & Mercury]“): alle singen die Zeile
HARD_EOL = 2          # Zeilenende aus einer LRC-Datei: build_lines trennt dort immer
MIN_TIMED = 5         # so viele Zeilen mit LRC-Zeit (und ohne) braucht es, damit beide Teile zusammengeführt werden
MAX_LRC_SPREAD = 0.6   # so weit dürfen die LRC-Zeiten um den gemeinsamen Versatz streuen (s), sonst keine Zeiten


def _drop_notes(line):
    """Anmerkungen in Klammern entfernen, gesungenes in Klammern („(Merci)“) behalten."""
    return NOTE_PAREN.sub(lambda m: " " if NOTE_WORD.search(m.group(1)) else m.group(0), line)
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


def names_of(who):
    """„David Bowie & Freddie Mercury“ -> ["David Bowie", "Freddie Mercury"]."""
    return [n for n in (who or "").split(DUET) if n]


def _who(name, strict=False):
    """Namen aus einer Abschnittsmarke oder einem Sprecher-Vorsatz säubern.
    Mehrere Namen („Bowie & Mercury“): Duett, sortiert mit „ & “ verbunden; ist einer davon kein Name, kein Hinweis."""
    name = re.sub(r"\s+", " ", (name or "").replace("_", " ").replace("*", " ").strip(" .:-–—'\"“”„"))
    if name and MANY.search(name):
        parts = [_who(p, strict) for p in MANY.split(name)]
        if len(parts) < 2 or not all(parts):
            return None
        # sortiert: „Mercury & Bowie“ und „Bowie & Mercury“ sind dasselbe Duett
        return DUET.join(sorted(set(parts), key=str.casefold)) if len(set(parts)) > 1 else parts[0]
    if not name or not any(ch.isalpha() for ch in name):
        return None
    if len(name) > 30 or len(name.split()) > 4:
        return None
    if name.casefold() in NOT_A_NAME:
        return None   # „Both“, „Alle“: sagt nicht, wer singt
    if strict and not (name.isupper() or all(w[:1].isupper() for w in name.split())):
        return None   # „(door slams)“ ist eine Regieanweisung, kein Name
    return name.title() if name.isupper() else name   # „TELEMACHUS“ -> „Telemachus“


def clean(raw):
    """Nur die Zeilen des vorgegebenen Texts (ohne Namen)."""
    return [line for line, _ in clean_lines(raw)]


def prepare(raw):
    """Eingefügten Text geradeziehen, bevor er zerlegt wird: Untertitel-Dateien, HTML-Zeichen, Zeilennummern."""
    raw = (raw or "").replace("\r", "")
    if len(SRT_ARROW.findall(raw)) >= 2:
        # Ganze .srt oder .vtt eingefügt: ohne Aufbereitung landen Zeiten und Nummern als Wörter im Pack
        try:
            from app.pipeline import textsources
            raw = textsources.subs_to_text(raw)
        except Exception:
            pass
    if "&" in raw:
        raw = html.unescape(html.unescape(raw))   # manche Seiten kodieren zweimal (&amp;#39;)
    body = [l for l in raw.split("\n") if l.strip()]
    nums = [int(m.group(0)) for l in body for m in [re.match(r"\s*(\d{1,3})(?!\S)", l)] if m]
    rising = len(nums) >= 3 and sum(1 for x, y in zip(nums, nums[1:]) if y > x) >= 0.8 * (len(nums) - 1)
    if body and rising and sum(1 for l in body if BARE_NO.match(l)) >= 0.6 * len(body):
        raw = "\n".join(BARE_NO.sub("", l) if l.strip() else l for l in raw.split("\n"))
    return raw


def _mark_speakers(body):
    """Welche eckigen Marken nennen wirklich einen Sprecher? Nur solche, die mehrfach vorkommen.

    Sonst wird aus „[Door Slams]“ oder „(Ooh)“ eine Figur, und schlimmer: der echte Name davor geht verloren."""
    seen = {}
    for line in body:
        m = MARK.match(line)
        if not m or line.strip()[:1] != "[" or SECTION_WORD.search(m.group(1)) or ":" in m.group(1):
            continue
        name = _who(m.group(1), strict=True)
        if name:
            seen[name] = seen.get(name, 0) + 1
    return {n for n, k in seen.items() if k >= 2}


def clean_lines(raw):
    """Vorgegebenen Text in Zeilen zerlegen: Zeitmarken, Abschnittsnamen, Klammer-Anweisungen und
    Sprechernamen am Zeilenanfang fallen weg, leere Zeilen auch.
    -> [(Zeile, wer sie singt oder spricht oder None)]"""
    return [(line, who) for line, who, _ in timed_lines(raw)]


def _lrc_time(tag):
    m = re.match(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]", tag)
    frac = m.group(3) or "0"
    return int(m.group(1)) * 60 + int(m.group(2)) + int(frac) / 10 ** len(frac)


def timed_lines(raw):
    """Wie clean_lines, dazu die LRC-Zeit der Zeile (s, oder None). -> [(Zeile, wer, Zeit)]

    Stehen eine LRC-Datei (genaue Zeiten) und ein Liedtext mit Sängern (Genius: „[Verse 1: Bowie]“) zusammen
    im Feld, werden beide getrennt gelesen und die Namen auf die LRC-Zeilen übertragen."""
    body = prepare(raw).split("\n")
    timed = [l for l in body if LRC_TAG.match(l.strip()) and LRC_TAG.sub("", l).strip()]
    plain = [l for l in body if l.strip() and not LRC_TAG.match(l.strip())]
    if len(timed) >= MIN_TIMED and len(plain) >= MIN_TIMED:
        with_times = _parse(timed)
        named = _parse(plain)
        if any(w for _, w, _ in named) and not any(w for _, w, _ in with_times):
            return _carry_names(with_times, named)
    return _parse(body)


def _carry_names(target, source):
    """Namen aus source (Liedtext mit Sängern) auf die Zeilen von target (LRC) übertragen: beide Wortfolgen
    abgleichen, jede Zeile bekommt den häufigsten Namen ihrer wiedergefundenen Wörter."""
    t_words = [(k, _norm(w)) for k, (line, _, _) in enumerate(target) for w in line.split() if _norm(w)]
    s_words = [(who, _norm(w)) for line, who, _ in source for w in line.split() if _norm(w)]
    votes = {}
    ops = SequenceMatcher(None, [n for _, n in t_words], [n for _, n in s_words], autojunk=False).get_opcodes()
    for tag, i1, i2, j1, j2 in ops:
        if tag in ("equal", "replace"):
            for a, b in zip(range(i1, i2), range(j1, j2)):
                votes.setdefault(t_words[a][0], []).append(s_words[b][0])
    out = []
    for k, (line, _, t) in enumerate(target):
        names = [w for w in votes.get(k, []) if w]
        out.append((line, Counter(names).most_common(1)[0][0] if names else None, t))
    return out


ITALIC_LINE = re.compile(r"^\s*_(.+)_\s*$")      # ganze Zeile kursiv (Genius, von fetch_genius so markiert)
BOLD_LINE = re.compile(r"^\s*\*\*(.+)\*\*\s*$")   # ganze Zeile fett: bei Genius singen meist alle
ITALIC_NAME = re.compile(r"_([^_\]]+)_")


def _styled_duet(inside, who):
    """Duett-Marke mit kursivem Namen („Verse 1: Bowie & _Mercury_“): -> (kursiver Name, anderer Name) oder None.
    Genius-Brauch: kursive Zeilen singt der kursive Name, normale der andere, fette alle."""
    names = names_of(who)
    ital = [_who(n, strict=False) for n in ITALIC_NAME.findall(inside.split(":", 1)[-1])]
    if len(names) == 2 and len(ital) == 1 and ital[0] in names:
        return ital[0], next(n for n in names if n != ital[0])
    return None


def _parse(body):
    lines = []
    who = None
    styled = None   # (kursiver Name, anderer Name) im aktuellen Duett-Abschnitt
    speakers = _mark_speakers(body)
    ads = False
    for line in body:
        tag = LRC_TAG.match(line.strip())
        t = _lrc_time(tag.group(0)) if tag else None
        line = LRC_TAG.sub("", line)
        if not line.strip() or GENIUS_HEAD.match(line):
            continue
        if GENIUS_ADS.match(line):
            ads = True   # „You might also like“ + Titel und Künstler anderer Lieder bis zur nächsten Marke
            continue
        if ads and not MARK.match(line):
            continue
        ads = False
        line = GENIUS_EMBED.sub("", line)
        mark = MARK.match(line)
        if mark:
            inside = mark.group(1)
            square = line.strip()[:1] == "["
            styled = None
            if SECTION_WORD.search(inside):
                # „[Verse 2: Natalia]“ nennt den Namen hinter dem Doppelpunkt, „[Bridge]“ nennt keinen
                who = _who(inside.split(":", 1)[1]) if ":" in inside else None
                styled = _styled_duet(inside, who) if who and DUET in who else None
            elif square and ":" in inside:
                who = _who(inside.split(":", 1)[0], strict=True) or who
            elif square:
                name = _who(inside, strict=True)   # „[TELEMACHUS]“ ist selbst der Name
                if name in speakers:
                    who = name
            # runde Klammern („(Ooh)“, „(2x)“) sind Beiwerk: Zeile weg, Sprecher bleibt
            continue
        italic, bold = ITALIC_LINE.match(line), BOLD_LINE.match(line)
        if italic or bold:
            line = (italic or bold).group(1)
        if ONLY_NOTE.match(line):
            continue
        line = LIST_NO.sub("", line)
        spk = SPEAKER.match(line)
        # „Und dann sagte er: Komm her“ ist kein Sprechername: höchstens drei Wörter, und ein Name muss es sein
        name = _who(spk.group(1), strict=True) if spk and len(spk.group(1).split()) <= 3 else None
        here = name or who
        if styled and not name and not bold:
            here = styled[0] if italic else styled[1]
        if name:
            line = line[spk.end():]
        line = BRACKETS.sub(" ", line)
        line = _drop_notes(line)
        line = line.replace("(", " ").replace(")", " ")
        line = " ".join(_fold(w) for w in line.split())
        line = re.sub(r"\s+", " ", line).strip().strip('"“”„«» ')
        if line and any(ch.isalnum() for ch in line):
            lines.append((line, here, t))
    return lines


def tokens(lines):
    """[(Wort wie geschrieben, vereinfacht, Zeilenende?, wer, LRC-Zeit am ersten Wort der Zeile oder None)].
    Nimmt Zeilen, (Zeile, wer) oder (Zeile, wer, Zeit)."""
    out = []
    for item in lines:
        line, who, t = (tuple(item) + (None, None))[:3] if isinstance(item, tuple) else (item, None, None)
        words = [w for w in line.split(" ") if _norm(w)]
        for i, w in enumerate(words):
            # Zeilenende: True, bei LRC-Zeilen HARD_EOL (dort ist die Zeile sicher zu Ende, auch bei kurzer Pause)
            eol = (HARD_EOL if t is not None else True) if i == len(words) - 1 else False
            out.append((w, _norm(w), eol, who, t if i == 0 else None))
    return out


def _spread(ref_toks, s, e, seg, p=0.95, spk=None):
    """Wörter gleichmäßig nach Zeichenzahl auf [s, e] verteilen."""
    lens = [max(1, len(t[1])) for t in ref_toks]
    total, t, out = float(sum(lens)), s, []
    for (word, _, eol, who, *_), n in zip(ref_toks, lens):
        d = (e - s) * n / total
        w = {"w": " " + word, "s": round(t, 3), "e": round(t + d, 3), "p": p, "seg": seg, "ref": True, "eol": eol}
        if who:
            w["who"] = who
        if spk is not None:
            w["spk"] = spk
        out.append(w)
        t += d
    return out


def _voice_threshold(env):
    """Ab wann gilt die Stimmen-Spur als „da“. Einmal je Lauf, nicht je Einfügung: das Sortieren der
    ganzen Hüllkurve kostete bei langen Videos Sekunden pro Aufruf."""
    if env is None or not len(env):
        return None
    arr = np.asarray(env)
    k = min(len(arr) - 1, int(len(arr) * 0.99))
    return max(float(np.partition(arr, k)[k]) - 30.0, -55.0)


def _voiced(env, a, b, thr, fps=100):
    """Anteil der Zeit in [a, b] mit Stimme in der Stimmen-Spur (Hüllkurve in dB, 100 je Sekunde)."""
    if env is None or thr is None or b <= a:
        return 1.0
    seg = env[int(a * fps):max(int(a * fps) + 1, int(b * fps))]
    if not len(seg):
        return 0.0
    return float((seg > thr).mean())


def _lrc_offset(ops, speech, ref):
    """Versatz Video gegen LRC (s): Songdatei und Video beginnen selten gleich. Nur wenn die wiedergefundenen
    Zeilenanfänge alle ungefähr denselben Versatz zeigen, sonst None (Zeiten dann nicht benutzen)."""
    diffs = [w["s"] - r[4] for tag, i1, i2, j1, j2 in ops if tag == "equal"
             for w, r in zip(speech[i1:i2], ref[j1:j2]) if r[4] is not None]
    if len(diffs) < 3:
        return None
    off = float(np.median(diffs))
    if float(np.median([abs(d - off) for d in diffs])) > MAX_LRC_SPREAD:
        return None
    return off


def _place_timed(R, offset, lo, hi, env, thr, seg, spk):
    """Überhörte Zeilen an ihre LRC-Zeit (plus Versatz) legen, begrenzt auf [lo, hi]. Jede Zeile reicht bis zur
    nächsten Zeile, höchstens 1 s je Wort. Zeilen ohne Platz oder ohne Stimme fallen weg."""
    groups = []
    for r in R:
        if r[4] is not None or not groups:
            groups.append([r])
        else:
            groups[-1].append(r)
    out = []
    for k, g in enumerate(groups):
        if g[0][4] is None:
            continue
        s = max(lo, g[0][4] + offset)
        nxt = groups[k + 1][0][4] + offset if k + 1 < len(groups) and groups[k + 1][0][4] is not None else hi
        e = min(hi, nxt, s + 1.0 * len(g)) - 0.05
        if out:
            s = max(s, out[-1]["e"])
        if e - s < MIN_WORD_GAP * len(g) or _voiced(env, s, e, thr) < MIN_VOICED:
            continue
        out += _spread(g, s, e, seg, p=0.6, spk=spk)
    return out


def apply(words, raw_text, env=None):
    """Whisper-Wörter mit dem vorgegebenen Text abgleichen. env: Hüllkurve der Stimmen-Spur (für Einfügungen).
    -> (neue Wörter, Bericht)"""
    ref = tokens(timed_lines(raw_text))
    speech = [w for w in words if not w.get("sound") and not w.get("laugh") and _norm(w["w"])]
    ids = {id(w) for w in speech}
    others = [w for w in words if id(w) not in ids]
    report = {"ref_words": len(ref), "heard": len(speech), "matched": 0, "replaced": 0, "inserted": 0, "kept": 0, "used": False}
    if not ref or not speech:
        return words, report
    wn, rn = [_norm(w["w"]) for w in speech], [t[1] for t in ref]
    thr = _voice_threshold(env)
    ops = SequenceMatcher(None, wn, rn, autojunk=False).get_opcodes()
    matched = sum(i2 - i1 for tag, i1, i2, _, _ in ops if tag == "equal")
    report["matched"] = matched
    report["share"] = round(matched / max(1, len(speech)), 3)        # so viel des Gehörten stand im Text
    report["share_ref"] = round(matched / max(1, len(ref)), 3)       # so viel des Texts kam im Video vor
    if matched < MIN_MATCH * len(speech) and not (len(ref) >= 30 and matched >= MIN_OF_REF * len(ref)):
        report["reason"] = "Der Text passt nicht zu dem, was im Video gesprochen wird."
        return words, report
    eq = [k for k, op in enumerate(ops) if op[0] == "equal"]
    first, last = eq[0], eq[-1]
    offset = _lrc_offset(ops, speech, ref)
    if offset is not None:
        report["lrc_offset"] = round(offset, 2)
    end_of_audio = len(env) / 100.0 if env is not None and len(env) else None
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
            timed = []
            if not lo <= ratio <= hi and offset is not None and any(
                    r[4] is not None and W[0]["s"] - 1.0 <= r[4] + offset <= W[-1]["e"] for r in R):
                # Whisper hat hier anderes gehört („Boomba that“ statt „Mmm, num, ba, de“), die LRC-Zeiten
                # liegen aber genau auf dieser Stelle: Text mit LRC-Zeiten nehmen
                prev_e = out[-1]["e"] if out else 0.0
                nxt = speech[i2]["s"] if i2 < len(speech) else end_of_audio
                if nxt is not None:
                    timed = _place_timed(R, offset, max(prev_e, W[0]["s"] - 1.0), nxt, env, thr,
                                         W[0].get("seg"), W[0].get("spk"))
            if timed:
                out += timed
                report["replaced"] += len(timed)
                report["timed"] = report.get("timed", 0) + len(timed)
            elif lo <= ratio <= hi:
                out += _spread(R, W[0]["s"], W[-1]["e"], W[0].get("seg"), spk=W[0].get("spk"))
                report["replaced"] += len(R)
            else:
                out += W
                report["kept"] += len(W)
        elif tag == "delete":
            out += W
            report["kept"] += len(W)
        elif tag == "insert" and offset is not None and any(r[4] is not None for r in R):
            # Zeilen, die Whisper überhört hat (z. B. „Mmm num ba de“): an ihre LRC-Zeit setzen, auch am Anfang
            # und Ende des Texts, aber nur in die Lücke zwischen den Nachbarwörtern und nur, wo Stimme ist
            prev_e = out[-1]["e"] if out else 0.0
            nxt = speech[i1]["s"] if i1 < len(speech) else end_of_audio
            if nxt is not None:
                placed = _place_timed(R, offset, prev_e, nxt, env, thr, out[-1].get("seg") if out else None,
                                      out[-1].get("spk") if out else speech[0].get("spk"))
                out += placed
                report["inserted"] += len(placed)
                report["timed"] = report.get("timed", 0) + len(placed)
        elif tag == "insert" and not edge:
            prev_e = out[-1]["e"] if out else 0.0
            nxt = speech[i1]["s"] if i1 < len(speech) else prev_e
            if nxt - prev_e >= MIN_WORD_GAP * len(R) and _voiced(env, prev_e, nxt, thr) >= MIN_VOICED:
                pad = min(0.05, (nxt - prev_e) * 0.1)
                out += _spread(R, prev_e + pad, nxt - pad, out[-1].get("seg") if out else None, p=0.6,
                               spk=out[-1].get("spk") if out else None)
                report["inserted"] += len(R)
    report["used"] = True
    return sorted(out + others, key=lambda w: w["s"]), report
