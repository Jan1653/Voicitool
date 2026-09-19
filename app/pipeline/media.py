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

    if has_encoder("h264_nvenc"):
        try:
            return run_with_progress(build(["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "26"]), duration, on_progress)
        except RuntimeError:
            pass  # keine NVIDIA-Karte / NVENC nicht nutzbar -> CPU
    run_with_progress(build(["-c:v", "libx264", "-preset", "veryfast", "-crf", "24"]), duration, on_progress)


def encode_opus(src, dst, bitrate="96k"):
    run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-c:a", "libopus", "-b:a", bitrate, str(dst)])


def count_decode_errors(path):
    """Video testweise dekodieren und Fehlermeldungen zählen (0 = sauber)."""
    res = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"],
                         capture_output=True, creationflags=_NO_WINDOW)
    return res.stderr.decode("utf8", "replace").lower().count("error")


def encode_ogv(src, dst, duration, max_height, max_fps, quality, on_progress, audio=None, tag=None, cuts=None):
    """Video für Choicer Voicer (Godot) als Ogg Theora + Vorbis kodieren.

    Konstante Bildrate (variable Bildraten mag der Godot-Player nicht).
    tag: Credit-Text für die Metadaten, None = keine Kennzeichnung.
    cuts: rausgeschnittene Stellen [[start, ende], …] (Bild; den passenden Ton liefert audio schon geschnitten).
    """
    info = probe(src)
    target_fps = min(int(max_fps or 60), max(1, round(info["fps"] or 30)))
    vf = f"scale=-2:'trunc(min({int(max_height)},ih)/2)*2',fps={target_fps}"
    if cuts:   # nach fps: feste Bildrate, dann Bilder in den Schnitten verwerfen und lückenlos neu nummerieren
        drop = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in cuts)
        vf += f",select='not({drop})',setpts=N/FRAME_RATE/TB"
    cmd = [FFMPEG, "-y", "-v", "error", "-i", str(src)]
    if audio and Path(audio).exists():
        cmd += ["-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
                "-c:a", "libvorbis", "-q:a", "5", "-ar", "44100", "-ac", "2"]
    else:
        cmd += ["-map", "0:v:0"]
    cmd += ["-vf", vf, "-pix_fmt", "yuv420p", "-c:v", "libtheora", "-q:v", str(quality), "-map_metadata", "-1"]
    if tag:
        cmd += ["-metadata", f"comment={tag}"]
    cmd.append(str(dst))
    run_with_progress(cmd, duration, on_progress)


def grab_frame(src, t, dst, width=640):
    run([FFMPEG, "-y", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(src), "-frames:v", "1",
         "-vf", f"scale={int(width)}:-2", "-q:v", "3", str(dst)])


def load_mono(path, sr=16000, start=None, dur=None):
    cmd = [FFMPEG, "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    return np.frombuffer(run(cmd).stdout, dtype=np.float32).copy()
