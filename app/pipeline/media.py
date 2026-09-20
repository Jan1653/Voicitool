"""ffmpeg-/PyAV-Hilfsfunktionen (nutzt das mitgelieferte ffmpeg aus tools/ffmpeg)."""
import re
import subprocess
import threading
from pathlib import Path

import numpy as np

from app import config

FFMPEG = config.FFMPEG
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(cmd, **kw):
    res = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW, **kw)
    if res.returncode != 0:
        err = res.stderr.decode("utf8", "replace")[-1500:]
        raise RuntimeError(f"{Path(cmd[0]).name} fehlgeschlagen:\n{err}")
    return res


# PyAV nennt den Decoder, nicht immer den Codec (z. B. libdav1d statt av1)
CODEC_ALIASES = {"libdav1d": "av1", "libaom-av1": "av1", "libvpx-vp9": "vp9", "libvpx": "vp8",
                 "libopus": "opus", "libvorbis": "vorbis", "libmp3lame": "mp3"}


def probe(path):
    """Video-Eigenschaften über PyAV (kein ffprobe nötig)."""
    import av

    with av.open(str(path)) as c:
        v = next((s for s in c.streams if s.type == "video"), None)
        a = next((s for s in c.streams if s.type == "audio"), None)
        duration = c.duration / 1e6 if c.duration else 0.0
        if not duration:
            for s in (v, a):
                if s is not None and s.duration and s.time_base:
                    duration = max(duration, float(s.duration * s.time_base))
        fps = float(v.average_rate) if v is not None and v.average_rate else 0.0
        name = lambda s: CODEC_ALIASES.get(s.codec_context.name, s.codec_context.name)
        return {
            "duration": duration,
            "container": c.format.name,
            "video_codec": name(v) if v is not None else None,
            "width": v.codec_context.width if v is not None else 0,
            "height": v.codec_context.height if v is not None else 0,
            "fps": round(fps, 3),
            "audio_codec": name(a) if a is not None else None,
        }


def browser_playable(info, path):
    """Kann das Editor-Fenster die Datei direkt abspielen (sonst wird eine Vorschau erzeugt)?"""
    ext = Path(path).suffix.lower()
    if info["video_codec"] not in {"h264", "vp8", "vp9", "av1"}:
        return False
    if ext in {".mp4", ".m4v", ".mov"}:
        return info["audio_codec"] in {"aac", "mp3", None}
    if ext == ".webm":
        return info["audio_codec"] in {"opus", "vorbis", None}
    return False


def _drain(stream, sink):
    def loop():
        for chunk in iter(lambda: stream.read(4096), b""):
            sink.append(chunk)
            if len(sink) > 50:
                del sink[:25]
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


def run_with_progress(cmd, duration, on_progress):
    """ffmpeg mit -progress ausführen und Fortschritt (0..1) melden. Abbruch beendet ffmpeg sofort."""
    cmd = [cmd[0], "-progress", "pipe:1", "-nostats"] + cmd[1:]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW)
    err_chunks = []
    t = _drain(proc.stderr, err_chunks)
    try:
        for line in proc.stdout:
            m = re.match(rb"out_time_us=(\d+)", line)
            if m and duration > 0:
                on_progress(min(1.0, int(m.group(1)) / 1e6 / duration))
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    proc.wait()
    t.join(timeout=2)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg fehlgeschlagen:\n" + b"".join(err_chunks).decode("utf8", "replace")[-1500:])


def has_encoder(name):
    res = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, creationflags=_NO_WINDOW)
    return name in res.stdout.decode("utf8", "replace")


def extract_audio(src, dst, sr=44100, channels=2):
    run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-vn", "-ac", str(channels), "-ar", str(sr),
         "-c:a", "pcm_s16le", str(dst)])


def make_preview(src, dst, duration, on_progress):
    """Abspielbare Vorschau (H.264/AAC) fürs Editor-Fenster erzeugen."""
    def build(venc):
        return [FFMPEG, "-y", "-v", "error", "-i", str(src),
                "-vf", "scale=-2:'trunc(min(720,ih)/2)*2'", *venc, "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-movflags", "+faststart", str(dst)]

    _encode_h264(build, dst, duration, on_progress)


# Grafikkarte kodieren lassen, wenn sie kann: NVIDIA, sonst Intel (viele Notebooks), sonst AMD, sonst Prozessor
H264_CHAIN = (("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "26"]),
              ("h264_qsv", ["-c:v", "h264_qsv", "-preset", "faster", "-global_quality", "26"]),
              ("h264_amf", ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp", "-qp_i", "26", "-qp_p", "26"]),
              ("libx264", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "24"]))


def _encode_h264(build, dst, duration, on_progress):
    """build(venc) -> ffmpeg-Befehl. Der erste Encoder, der läuft, gewinnt; am Ende der Prozessor."""
    for name, venc in H264_CHAIN:
        if name != "libx264" and not has_encoder(name):
            continue
        try:
            return run_with_progress(build(venc), duration, on_progress)
        except RuntimeError:
            Path(dst).unlink(missing_ok=True)   # halbe Datei aus dem Fehlversuch
            if name == "libx264":
                raise


def mux_video(video_src, audio_src, dst, duration, on_progress=None):
    """Video mit einer neuen Tonspur als MP4 schreiben (H.264/AAC, überall abspielbar)."""
    def build(venc):
        return [FFMPEG, "-y", "-v", "error", "-i", str(video_src), "-i", str(audio_src),
                "-map", "0:v:0", "-map", "1:a:0", "-vf", "scale=-2:'trunc(min(1080,ih)/2)*2'", *venc,
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest",
                "-movflags", "+faststart", str(dst)]

    _encode_h264(build, dst, duration, on_progress or (lambda p: None))


def encode_opus(src, dst, bitrate="96k"):
    run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-c:a", "libopus", "-b:a", bitrate, str(dst)])


def count_decode_errors(path):
    """Video testweise dekodieren und Fehlermeldungen zählen (0 = sauber)."""
    res = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"],
                         capture_output=True, creationflags=_NO_WINDOW)
    return res.stderr.decode("utf8", "replace").lower().count("error")


def target_fps(src, max_fps):
    """Bildrate, mit der exportiert wird (nie höher als die Quelle). Danach richten sich auch die Schnittgrenzen."""
    return min(int(max_fps or 60), max(1, round(probe(src)["fps"] or 30)))


def encode_ogv(src, dst, duration, max_height, max_fps, quality, on_progress, audio=None, tag=None, cuts=None):
    """Video für Choicer Voicer (Godot) als Ogg Theora + Vorbis kodieren.

    Konstante Bildrate (variable Bildraten mag der Godot-Player nicht).
    tag: Credit-Text für die Metadaten, None = keine Kennzeichnung.
    cuts: rausgeschnittene Stellen [[start, ende], …] (Bild; den passenden Ton liefert audio schon geschnitten).
    """
    fps = target_fps(src, max_fps)
    vf = f"scale=-2:'trunc(min({int(max_height)},ih)/2)*2',fps={fps}"
    if cuts:   # nach fps: feste Bildrate, dann Bilder in den Schnitten verwerfen und lückenlos neu nummerieren
        # „between“ schließt beide Enden ein; das Bild genau am Schnittende gehört schon zum erhaltenen Teil
        drop = "+".join(f"between(t,{s:.3f},{e - 0.5 / fps:.3f})" for s, e in cuts)
        vf += f",select='not({drop})',setpts=N/FRAME_RATE/TB"
    cmd = [FFMPEG, "-y", "-v", "error", "-i", str(src)]
    if audio and Path(audio).exists():
        cmd += ["-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
                "-c:a", "libvorbis", "-q:a", "5", "-ar", "44100", "-ac", "2"]
    else:
        cmd += ["-map", "0:v:0"]
    cmd += ["-vf", vf, "-r", str(fps), "-pix_fmt", "yuv420p", "-c:v", "libtheora", "-q:v", str(quality), "-map_metadata", "-1"]
    if tag:
        cmd += ["-metadata", f"comment={tag}"]
    cmd.append(str(dst))
    run_with_progress(cmd, duration, on_progress)


def grab_frame(src, t, dst, width=640):
    run([FFMPEG, "-y", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(src), "-frames:v", "1",
         "-vf", f"scale={int(width)}:-2", "-q:v", "3", str(dst)])


# Standbild je Zeile. Das Bild kurz nach dem Zeilenanfang passt fast immer (in 12 Testprojekten
# hatten 97,3 % der Bilder keinen Mangel). Nur bei einem sichtbaren Mangel wird ausgewichen, und
# dann nur innerhalb derselben Einstellung: ein Bild aus der nächsten Einstellung würde zum Satz
# nicht mehr passen (falscher eingebrannter Untertitel, falsche Person im Bild).
FRAME_WIN = (-0.12, 0.50)   # Fenster um den Zeilenanfang, das dekodiert wird
FRAME_PICK = (0.0, 0.50)    # daraus darf gewählt werden
CUT_H, CUT_D = 0.25, 0.12   # Schnitt im Fenster: Histogramm- bzw. Bildabstand
BLACK_P98, WHITE_P02, FLAT_STD = 0.10, 0.88, 0.020   # schwarz, weiß, flau
BLUR_FACTOR = 2.0           # vermeidbar unscharf: im selben Bild gibt es ein doppelt so scharfes


def _win_rgb(src, a, dur, w, h):
    """Kurzes Fenster als rohe RGB-Bilder holen. Leeres Feld, wenn ffmpeg nichts liefert."""
    try:
        p = run([FFMPEG, "-v", "error", "-ss", f"{max(0.0, a):.3f}", "-t", f"{dur:.3f}", "-i", str(src),
                 "-vf", f"scale={w}:{h}", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
    except RuntimeError:
        return np.zeros((0, h, w, 3), np.uint8)
    n = len(p.stdout) // (w * h * 3)
    if n < 1:
        return np.zeros((0, h, w, 3), np.uint8)
    return np.frombuffer(p.stdout[:n * w * h * 3], np.uint8).reshape(n, h, w, 3)


def _frame_marks(fr):
    """Je Bild: Kennzahlen, Schnitt zum Vorbild, Übergangsbild (gehört zu keiner Einstellung)."""
    marks = []
    prev = None
    for f in fr:
        g = (f.mean(axis=2) / 255.0).astype(np.float32)[::2, ::2]
        p02, p98 = (float(x) for x in np.percentile(g, [2, 98]))
        lap = 4.0 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
        cut = False
        if prev is not None:
            ha = np.histogram(g, bins=32, range=(0.0, 1.0))[0] / g.size
            hb = np.histogram(prev, bins=32, range=(0.0, 1.0))[0] / prev.size
            cut = bool(np.abs(ha - hb).sum() / 2.0 > CUT_H or np.abs(g - prev).mean() > CUT_D)
        marks.append({"p02": p02, "p98": p98, "std": float(g.std()), "sharp": float(lap.var()), "cut": cut})
        prev = g
    for i, m in enumerate(marks):
        nxt = marks[i + 1] if i + 1 < len(marks) else None
        m["blend"] = bool(m["cut"] and nxt is not None and nxt["cut"])
        m["empty"] = bool(m["p98"] < BLACK_P98 or m["p02"] > WHITE_P02 or m["std"] < FLAT_STD)
    return marks


def _shot(marks, i):
    """Anfang und Ende der Einstellung, in der Bild i liegt."""
    a = i
    while a > 0 and not marks[a]["cut"]:
        a -= 1
    b = i + 1
    while b < len(marks) and not marks[b]["cut"]:
        b += 1
    return a, b


def pick_frame(src, start, end, dst, size, fps, width=640):
    """Standbild für eine Zeile speichern. Weicht nur bei einem Mangel vom Zeilenanfang ab."""
    from PIL import Image
    t0 = start + min(0.3, max(0.0, end - start) / 2)
    w, h = int(size[0] or 0), int(size[1] or 0)
    fps = float(fps or 0)
    if w < 2 or h < 2 or fps <= 0:
        return grab_frame(src, t0, dst, width)
    ow = int(width)
    oh = max(2, int(round(ow * h / w / 2)) * 2)
    a = max(0.0, start + FRAME_WIN[0])
    dur = min(FRAME_WIN[1] - FRAME_WIN[0], max(0.10, end + 0.05 - a))
    fr = _win_rgb(src, a, dur, ow, oh)
    if len(fr) < 2:
        return grab_frame(src, t0, dst, width)
    times = [a + k / fps for k in range(len(fr))]
    marks = _frame_marks(fr)
    base = next((i for i, t in enumerate(times) if t >= t0 - 1e-6), len(fr) - 1)
    lo = start + FRAME_PICK[0]
    hi = min(start + FRAME_PICK[1], max(end - 0.05, lo + 0.03))
    s0, s1 = _shot(marks, base)
    cand = [i for i, t in enumerate(times) if lo - 1e-6 <= t <= hi + 1e-6 and s0 <= i < s1] or [base]
    top = max(marks[i]["sharp"] for i in cand) or 1e-9
    m = marks[base]
    bad = m["empty"] or m["blend"] or top >= BLUR_FACTOR * max(m["sharp"], 1e-9)
    take = base
    if bad:
        take = max(cand, key=lambda i: (marks[i]["sharp"] / top
                                        - (1.0 if marks[i]["empty"] or marks[i]["blend"] else 0.0)
                                        - 0.5 * abs(times[i] - t0), -abs(times[i] - t0)))
    Image.fromarray(fr[take]).save(dst, quality=82)


def load_mono(path, sr=16000, start=None, dur=None):
    cmd = [FFMPEG, "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    return np.frombuffer(run(cmd).stdout, dtype=np.float32).copy()
