"""Eigene Instrumental-Version als Hintergrund verwenden.

Die Datei wird automatisch auf das Video ausgerichtet:
  1. grober Versatz über die Lautstärke-Hüllkurve
  2. genauer Versatz und Auseinanderlaufen (Drift) über die Wellenform an mehreren Stellen
  3. Tempo-/Versatz-Korrektur und Lautstärke-Angleich mit ffmpeg
  4. Messung, wie gut es passt (und ob sich daraus sogar saubere Stimmen gewinnen lassen)
"""
import json

import numpy as np
import soundfile as sf

from app.pipeline import media

SR = 16000          # Analyse-Abtastrate
ENV_FPS = 100       # Hüllkurve
WIN = 10.0          # Länge der Prüffenster (s)
MAX_FINE = 1.0      # Suchbereich für die Feinsuche um den groben Versatz (s)


def _envelope(x, fps=ENV_FPS, sr=SR):
    hop = sr // fps
    n = len(x) // hop
    env = np.sqrt(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-10)
    env = 20 * np.log10(env)
    return env - env.mean()


def _best_lag(ref, sig):
    """Versatz von sig gegenüber ref (in Samples, positiv = sig liegt später) + Güte 0..1."""
    n = 1 << int(np.ceil(np.log2(len(ref) + len(sig))))
    a = ref - ref.mean()
    b = sig - sig.mean()
    cc = np.fft.irfft(np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n)), n)
    cc = np.concatenate([cc[-(len(b) - 1):], cc[:len(a)]])
    lags = np.arange(-(len(b) - 1), len(a))
    i = int(np.argmax(cc))
    norm = np.linalg.norm(a) * np.linalg.norm(b) + 1e-9
    return int(lags[i]), float(cc[i] / norm)


def measure(mix16, inst16):
    """Versatz (s), Drift (s über die ganze Länge) und Güte der Übereinstimmung bestimmen."""
    coarse_lag, coarse_score = _best_lag(_envelope(mix16), _envelope(inst16))
    offset = -coarse_lag / ENV_FPS  # Sekunden; positiv = Instrumental kommt zu spät

    # Feinmessung an mehreren Stellen (Wellenform)
    win = int(WIN * SR)
    search = int(MAX_FINE * SR)
    points, scores = [], []
    usable = min(len(inst16), len(mix16)) - win
    for frac in (0.15, 0.35, 0.55, 0.75, 0.92):
        s_inst = int(frac * max(1, usable))
        if s_inst + win > len(inst16):
            continue
        piece = inst16[s_inst:s_inst + win]
        center = s_inst + int(offset * SR)
        lo, hi = max(0, center - search), min(len(mix16), center + win + search)
        if hi - lo < win + 100 or np.abs(piece).max() < 1e-4:
            continue
        lag, score = _best_lag(mix16[lo:hi], piece)
        points.append((s_inst / SR, (s_inst - (lo + lag)) / SR))  # (Stelle im Instrumental, Versatz dort)
        scores.append(score)

    if len(points) >= 2 and max(scores) > 0.15:
        xs = np.array([p[0] for p in points])
        ys = np.array([p[1] for p in points])
        sc = np.array(scores)
        good = sc > max(0.15, sc.max() * 0.4)
        xs, ys, sc = xs[good], ys[good], sc[good]
        length = len(inst16) / SR
        slope, intercept, inl = _robust_line(xs, ys, sc, length)
        quality = float(np.median(sc[inl])) if inl.any() else float(sc.max())
        return {"offset": float(intercept), "drift": float(slope * length), "slope": float(slope),
                "quality": quality, "points": int(inl.sum())}
    return {"offset": float(offset), "drift": 0.0, "slope": 0.0, "quality": float(coarse_score), "points": 0}


TOL = 0.03          # Messstellen, die höchstens so weit von der Linie liegen, stimmen überein (s)
MIN_DRIFT = 0.03    # kleineres Auseinanderlaufen über die ganze Länge nicht ausgleichen (s)


def _robust_line(xs, ys, sc, length):
    """Versatz und Drift aus den Messstellen, unempfindlich gegen Ausreißer: Bei sich wiederholender Musik
    rastet eine Stelle gern auf einen gleich klingenden Takt daneben ein. Eine einfache Ausgleichsgerade machte
    daraus ein falsches Auseinanderlaufen (Test: nur verschobene Kopie, trotzdem 1,5 s „Drift“).
    Gewählt wird die Linie, auf der die meisten Stellen übereinstimmen (bei Gleichstand die mit der besseren Güte)."""
    cands = [(0.0, float(y)) for y in ys]   # fester Versatz
    for i in range(len(xs)):
        for j in range(i + 1, len(xs)):
            if abs(xs[j] - xs[i]) > 1e-6:
                s = (ys[j] - ys[i]) / (xs[j] - xs[i])
                cands.append((float(s), float(ys[i] - s * xs[i])))
    best = None
    for s, c in cands:
        inl = np.abs(ys - (c + s * xs)) < TOL
        key = (int(inl.sum()), float(sc[inl].sum()))
        if best is None or key > best[0]:
            best = (key, inl)
    inl = best[1]
    if inl.sum() >= 2 and np.ptp(xs[inl]) > 1e-6:
        slope, intercept = np.polyfit(xs[inl], ys[inl], 1)
    else:
        slope, intercept = 0.0, float(np.median(ys[inl]))
    if abs(slope * length) < MIN_DRIFT:
        slope, intercept = 0.0, float(np.median(ys[inl]))
    return float(slope), float(intercept), inl


def _apply(src, dst, offset, slope, gain, target_len, sr=44100):
    """Instrumental mit ffmpeg passend machen: Tempo, Versatz, Länge, Lautstärke."""
    filters = []
    if abs(slope) > 1e-6:
        tempo = 1.0 / (1.0 - slope)  # gleicht unterschiedliche Geschwindigkeit aus
        tempo = min(max(tempo, 0.9), 1.1)
        filters.append(f"atempo={tempo:.9f}")
    if offset > 0:  # Instrumental beginnt zu spät -> vorne abschneiden
        filters.append(f"atrim=start={offset:.4f}")
    elif offset < 0:  # zu früh -> Stille voranstellen
        filters.append(f"adelay=delays={abs(offset) * 1000:.1f}:all=1")
    filters.append("asetpts=N/SR/TB")
    if abs(gain - 1.0) > 0.01:
        filters.append(f"volume={gain:.4f}")
    filters.append(f"apad,atrim=end={target_len:.4f}")
    media.run([media.FFMPEG, "-y", "-v", "error", "-i", str(src), "-af", ",".join(filters),
               "-ac", "2", "-ar", str(sr), "-c:a", "pcm_s16le", str(dst)])


def _nonvocal_mask(project_dir, n, fps=ENV_FPS, guard=0.25):
    """Zeitbereiche ganz ohne Gesang (aus der getrennten Stimmen-Spur) als Maske.

    Streng: leise Gesangsreste würden sonst die Lautstärke-Messung verfälschen. Zusätzlich wird
    ein Sicherheitsabstand um jede Gesangsstelle freigehalten.
    """
    env_path = project_dir / "stimmen_env.npy"
    if not env_path.exists():
        return np.ones(n, dtype=bool)
    env = np.load(env_path)  # 100 fps, dB
    quiet = env < max(np.percentile(env, 99.5) - 45.0, -58.0)
    k = int(guard * fps)
    if k > 1:  # Randbereiche neben Gesang ebenfalls ausschließen
        eroded = np.convolve(quiet.astype(float), np.ones(2 * k + 1), mode="same") >= (2 * k + 1) - 0.5
        quiet = eroded
    m = np.zeros(n, dtype=bool)
    j = min(n, len(quiet))
    m[:j] = quiet[:j]
    if m.sum() < 20:  # kaum stille Stellen -> weniger streng
        m[:j] = (env < max(np.percentile(env, 99.5) - 35.0, -50.0))[:j]
    return m


def _frames(x, fps=ENV_FPS, sr=44100):
    hop = sr // fps
    n = len(x) // hop
    return x[: n * hop].reshape(n, hop)


def _level_factor(project_dir, inst, sr):
    """Faktor, mit dem das Instrumental so laut wird wie der KI-Hintergrund, also wie die Musik im Video.

    Gemessen über alle Stellen, an denen beide klingen. Früher war der ganze Mix samt Gesang das Maß: Bei Liedern
    gibt es kaum gesangsfreie Stellen, dann wurde das Instrumental hörbar lauter als die Musik im Video."""
    ref, rsr = sf.read(project_dir / "hintergrund.wav", dtype="float32", always_2d=True)
    a = np.sqrt((_frames(ref.mean(axis=1), sr=rsr) ** 2).mean(axis=1))
    b = np.sqrt((_frames(inst.mean(axis=1), sr=sr) ** 2).mean(axis=1))
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    on = (a > a.max() * 0.03) & (b > b.max() * 0.03)   # beide höchstens 30 dB unter ihrer lautesten Stelle
    if on.sum() < 50:
        return 1.0
    return float(np.clip(np.sqrt(np.mean(a[on] ** 2) / (np.mean(b[on] ** 2) + 1e-12)), 0.2, 5.0))


def _match_level(project_dir, path):
    """Datei auf die Lautstärke des KI-Hintergrunds bringen. -> angewandter Faktor"""
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    factor = _level_factor(project_dir, x, sr)
    if abs(factor - 1.0) > 0.02:
        x = x * factor
        peak = float(np.abs(x).max())
        if peak > 0.99:   # nicht übersteuern
            factor *= 0.99 / peak
            x = x * (0.99 / peak)
        sf.write(path, x, sr, subtype="PCM_16")
    return factor


def align(project_dir, source, on_progress=None):
    """Instrumental ausrichten -> instrumental.wav (+ Bericht). Gibt den Bericht zurück."""
    d = project_dir
    report = lambda p, msg: on_progress and on_progress(p, msg)

    report(0.05, "Dateien werden geladen …")
    mix16 = media.load_mono(d / "audio.wav")
    inst16 = media.load_mono(source)
    if len(inst16) < SR:
        raise RuntimeError("Die Instrumental-Datei ist zu kurz oder enthält keinen Ton.")

    report(0.25, "Versatz wird gemessen …")
    m = measure(mix16, inst16)

    report(0.55, "Lautstärke wird angeglichen …")
    # grobe Vorab-Angleichung der Lautstärke über die Effektivwerte
    gain = float(np.sqrt(np.mean(mix16 ** 2) / (np.mean(inst16 ** 2) + 1e-12)))
    gain = min(max(gain, 0.2), 5.0)

    target_len = len(mix16) / SR
    out = d / "instrumental.wav"
    report(0.65, "Instrumental wird angepasst …")
    _apply(source, out, m["offset"], m["slope"], gain, target_len)

    report(0.8, "Ergebnis wird geprüft …")
    mix, sr = sf.read(d / "audio.wav", dtype="float32", always_2d=True)

    def check_file():
        """Lautstärke in gesangsfreien Stellen angleichen und messen, wie gut es passt."""
        inst, _ = sf.read(out, dtype="float32", always_2d=True)
        n = min(len(mix), len(inst))
        a, b = mix[:n], inst[:n]
        fr_mix, fr_inst = _frames(a.mean(axis=1), sr=sr), _frames(b.mean(axis=1), sr=sr)
        quiet = _nonvocal_mask(d, min(len(fr_mix), len(fr_inst)))
        fr_mix, fr_inst = fr_mix[:len(quiet)], fr_inst[:len(quiet)]
        factor = _level_factor(d, b, sr)   # so laut wie die Musik im Video (KI-Hintergrund)
        if abs(factor - 1.0) > 0.02:
            b = b * factor
            peak_b = float(np.abs(b).max())
            if peak_b > 0.99:   # nicht übersteuern
                factor *= 0.99 / peak_b
                b = b * (0.99 / peak_b)
            sf.write(out, b, sr, subtype="PCM_16")
        # bestmögliche Auslöschung (nur zur Messung): passende Skalierung suchen
        scale = float(np.sum(a * b) / (np.sum(b * b) + 1e-9))
        scale = min(max(scale, 0.2), 3.0)
        residual = a - scale * b
        fr_res = _frames(residual.mean(axis=1), sr=sr)[:len(quiet)]
        if quiet.sum() > 10:
            e_mix = float(np.mean(fr_mix[quiet] ** 2)) + 1e-12
            e_res = float(np.mean(fr_res[quiet] ** 2)) + 1e-12
            rest = 10 * np.log10(e_res / e_mix)
        else:
            rest = 0.0
        return factor, float(rest), residual, scale

    factor, rest_db, diff, _ = check_file()
    gain *= factor

    verify16 = media.load_mono(out)
    check = measure(mix16, verify16)
    if abs(check["offset"]) > 0.02:  # Nachkorrektur, falls noch ein Rest bleibt
        report(0.85, "Feinkorrektur …")
        m["offset"] += check["offset"]
        _apply(source, out, m["offset"], m["slope"], gain, target_len)
        factor, rest_db, diff, _ = check_file()
        gain *= factor
        verify16 = media.load_mono(out)
        check = measure(mix16, verify16)
    quality = max(m["quality"], check["quality"])
    aligned = abs(check["offset"]) < 0.05

    if aligned and quality > 0.4:
        rating, note = "sehr gut", "Instrumental sitzt genau auf dem Video."
    elif aligned and quality > 0.2:
        rating, note = "gut", "Instrumental passt zum Video."
    elif aligned and (quality > 0.1 or rest_db < -3):
        rating, note = "mäßig", "Instrumental passt ungefähr, vermutlich eine andere Abmischung."
    else:
        rating, note = "passt nicht", "Instrumental ließ sich nicht sauber ausrichten (anderer Song oder Schnitt?)."
    if rest_db < -10 and aligned:
        note += " Es lassen sich sogar saubere Stimmen daraus gewinnen."

    info = {
        "datei": source.name,
        "versatz": round(m["offset"], 3),
        "drift": round(m["drift"], 3),
        "tempo_slope": m["slope"],   # für das Ausrichten von Hand (Tempo bleibt, nur der Versatz ändert sich)
        "lautstaerke": round(gain, 3),
        "guete": round(quality, 3),
        "restpegel_db": round(float(rest_db), 1),
        "rest_versatz": round(abs(check["offset"]), 3),
        "bewertung": rating,
        "hinweis": note,
        "stimmen_moeglich": bool(rest_db < -10 and aligned),
        "laenge": round(target_len, 2),
    }

    if info["stimmen_moeglich"]:
        report(0.9, "Stimmen werden aus der Differenz gewonnen …")
        peak = float(np.abs(diff).max())
        if peak > 1.0:
            diff = diff / peak * 0.99
        sf.write(d / "stimmen_diff.wav", diff, sr, subtype="PCM_16")
    else:
        (d / "stimmen_diff.wav").unlink(missing_ok=True)

    (d / "instrumental.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf8")
    media.encode_opus(out, d / "instrumental.ogg")   # zum Anhören im Editor („Hintergrund“)
    report(1.0, f"{rating}: {note}")
    return info


def source_file(project_dir):
    return next(iter(sorted(project_dir.glob("instrumental_quelle.*"))), None)


def _env_list(x, fps, sr=SR):
    hop = sr // fps
    n = len(x) // hop
    if n == 0:
        return []
    rms = np.sqrt(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-10)
    db = np.clip(20 * np.log10(rms), -60, 0)
    return [round(float(v), 1) for v in db]


def waves(project_dir, fps=50):
    """Hüllkurven zum Ausrichten von Hand: KI-Hintergrund (Video-Zeitachse) und das eigene Instrumental (eigene Zeitachse)."""
    d = project_dir
    src = source_file(d)
    if src is None:
        raise RuntimeError("Es ist keine eigene Instrumental-Datei gesetzt.")
    info = json.loads((d / "instrumental.json").read_text(encoding="utf8")) if (d / "instrumental.json").exists() else {}
    ref = media.load_mono(d / "hintergrund.wav")
    own = media.load_mono(src)
    return {"fps": fps, "ref": _env_list(ref, fps), "own": _env_list(own, fps), "source": src.name,
            "offset": info.get("versatz", 0.0), "slope": info.get("tempo_slope", 0.0),
            "gain": info.get("lautstaerke", 1.0)}


PREVIEW_SR = 24000   # Hörfassungen fürs Ausrichten: mono, genau springbar (MP3 springt im Browser nur ungefähr)


def preview(project_dir, which):
    """WAV-Hörfassung für das Ausrichten von Hand: 'ref' = KI-Hintergrund, 'own' = eigenes Instrumental (roh)."""
    d = project_dir
    src = d / "hintergrund.wav" if which == "ref" else source_file(d)
    if src is None or not src.exists():
        raise RuntimeError("Es ist keine eigene Instrumental-Datei gesetzt.")
    dst = d / f"ausrichten_{which}.wav"
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        media.run([media.FFMPEG, "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(PREVIEW_SR),
                   "-c:a", "pcm_s16le", str(dst)])
    return dst


def manual(project_dir, offset):
    """Versatz von Hand setzen (Tempo und Lautstärke bleiben wie bei der automatischen Messung)."""
    d = project_dir
    src = source_file(d)
    if src is None:
        raise RuntimeError("Es ist keine eigene Instrumental-Datei gesetzt.")
    info = json.loads((d / "instrumental.json").read_text(encoding="utf8"))
    slope = float(info.get("tempo_slope", 0.0))
    if info.get("bewertung") == "passt nicht":
        slope = 0.0   # die Messung war unbrauchbar, dann auch ihr Tempo nicht übernehmen
    target_len = sf.info(d / "audio.wav").duration
    out = d / "instrumental.wav"
    gain = float(info.get("lautstaerke", 1.0))
    _apply(src, out, float(offset), slope, gain, target_len)
    gain *= _match_level(d, out)   # so laut wie die Musik im Video
    media.encode_opus(out, d / "instrumental.ogg")
    (d / "stimmen_diff.wav").unlink(missing_ok=True)   # passte zur alten Ausrichtung
    info.update({"versatz": round(float(offset), 3), "tempo_slope": slope, "lautstaerke": round(gain, 3), "bewertung": "von Hand",
                 "hinweis": "Von Hand ausgerichtet.", "stimmen_moeglich": False})
    (d / "instrumental.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf8")
    return info


def remove(project_dir):
    for name in ("instrumental.wav", "instrumental.ogg", "instrumental.json", "stimmen_diff.wav", "ausrichten_ref.wav",
                 "ausrichten_own.wav"):
        (project_dir / name).unlink(missing_ok=True)
    for f in project_dir.glob("instrumental_quelle.*"):
        f.unlink(missing_ok=True)
