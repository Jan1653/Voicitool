"""Video von einer Web-Adresse (YouTube u. a.) in den Eingang laden, mit yt-dlp.

YouTube ändert laufend etwas. Damit Downloads schnell bleiben:
  * yt-dlp wird höchstens einmal täglich automatisch aktualisiert (update_ytdlp)
  * deno (tools/deno) löst YouTubes JavaScript-Aufgaben, sonst drosselt YouTube manche Videos
  * kurze Zeitgrenze: hängt ein YouTube-Server, wird nach 8 s der nächste Weg probiert statt nach 20 s
"""
import json
import re
import subprocess
import threading
import time
from pathlib import Path

from app import config

MAX_HEIGHT = 1080
UPDATE_EVERY = 24 * 3600
_update_lock = threading.Lock()
DENO = config.TOOLS_DIR / "deno" / "deno.exe"
UV = config.TOOLS_DIR / "uv" / "uv.exe"


def update_ytdlp(force=False):
    """yt-dlp (mit JavaScript-Löser) aktuell halten. Läuft beim Start im Hintergrund."""
    stamp = config.DATA_DIR / "ytdlp_update.json"
    with _update_lock:
        try:
            last = json.loads(stamp.read_text(encoding="utf8")).get("time", 0)
        except Exception:
            last = 0
        if not force and time.time() - last < UPDATE_EVERY:
            return False
        if not UV.exists():
            return False
        import sys
        res = subprocess.run([str(UV), "pip", "install", "--python", sys.executable.replace("pythonw.exe", "python.exe"),
                              "-U", "yt-dlp[default]"], capture_output=True, timeout=300)
        if res.returncode == 0:
            stamp.write_text(json.dumps({"time": time.time()}), encoding="utf8")
        return res.returncode == 0


def ytdlp_base_options():
    """Gemeinsame yt-dlp-Einstellungen für Videos und Instrumentals."""
    opts = {
        "ffmpeg_location": str(Path(config.FFMPEG).parent),
        "paths": {"temp": str(config.DATA_DIR / "download")},
        "windowsfilenames": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 5,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "socket_timeout": 8,
    }
    if DENO.exists():
        opts["js_runtimes"] = {"deno": {"path": str(DENO)}}
    return opts


def valid_url(url):
    return bool(re.match(r"^https?://[^\s]+$", (url or "").strip(), re.I))


def _cleanup_partials():
    for f in config.INBOX_DIR.glob("*"):
        if f.suffix.lower() in (".part", ".ytdl", ".temp") or f.name.endswith(".part"):
            f.unlink(missing_ok=True)


def download_audio(url, target_dir, on_progress):
    """Nur den Ton von einer Web-Adresse laden (z. B. ein Instrumental von YouTube)."""
    import yt_dlp

    if not valid_url(url):
        raise ValueError("Das ist keine gültige Web-Adresse (muss mit http:// oder https:// beginnen).")
    target_dir = Path(target_dir)
    for old in target_dir.glob("instrumental_quelle.*"):
        old.unlink()

    def hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            on_progress(min(0.97, done / total if total else 0.0),
                        f"{done / 1e6:.1f} von {total / 1e6:.1f} MB" if total else f"{done / 1e6:.1f} MB")

    opts = ytdlp_base_options()
    opts.update({"outtmpl": str(target_dir / "instrumental_quelle.%(ext)s"), "format": "bestaudio/best",
                 "overwrites": True, "progress_hooks": [hook]})
    with _update_lock:
        pass
    on_progress(0.0, "Ton wird gesucht …")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    files = sorted(target_dir.glob("instrumental_quelle.*"), key=lambda f: f.stat().st_mtime)
    files = [f for f in files if f.suffix not in (".part", ".ytdl")]
    if not files:
        raise RuntimeError("Der Download hat keine Datei erzeugt.")
    on_progress(1.0, info.get("title") or files[-1].name)
    return files[-1], info.get("title") or ""


def download(url, on_progress):
    """Lädt Bild+Ton in bester Qualität (max. 1080p) nach eingang/. Gibt den Dateinamen zurück."""
    import yt_dlp

    if not valid_url(url):
        raise ValueError("Das ist keine gültige Web-Adresse (muss mit http:// oder https:// beginnen).")
    result = {}

    def hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            speed = d.get("speed") or 0
            msg = f"{done / 1e6:.0f} von {total / 1e6:.0f} MB" if total else f"{done / 1e6:.0f} MB"
            if speed:
                msg += f" · {speed / 1e6:.1f} MB/s"
            on_progress(min(0.97, done / total if total else 0.0), msg)
        elif d["status"] == "finished":
            on_progress(0.98, "Bild und Ton werden zusammengefügt …")

    def pp_hook(d):
        path = (d.get("info_dict") or {}).get("filepath")
        if d["status"] == "finished" and path:
            result["path"] = path

    opts = ytdlp_base_options()
    opts.update({
        "outtmpl": str(config.INBOX_DIR / "%(title).70s.%(ext)s"),
        # H.264 + AAC bevorzugen: spielt im Editor-Fenster ohne Umwandlung
        "format": (f"bv*[height<={MAX_HEIGHT}][vcodec^=avc1]+ba[acodec^=mp4a]/"
                   f"bv*[height<={MAX_HEIGHT}]+ba/b[height<={MAX_HEIGHT}]/bv*+ba/b"),
        "merge_output_format": "mp4",
        "progress_hooks": [hook],
        "postprocessor_hooks": [pp_hook],
        "overwrites": False,
    })
    with _update_lock:  # läuft gerade das tägliche yt-dlp-Update, kurz darauf warten
        pass
    on_progress(0.0, "Video wird gesucht …")
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = result.get("path")
            if not path:
                downloads = info.get("requested_downloads") or []
                path = downloads[0].get("filepath") if downloads else ydl.prepare_filename(info)
    except BaseException:
        _cleanup_partials()
        raise

    file = Path(path)
    if not file.exists():
        raise RuntimeError("Der Download hat keine Datei erzeugt.")
    if file.parent != config.INBOX_DIR:  # z. B. wenn yt-dlp anders benannt hat
        target = config.INBOX_DIR / file.name
        file.replace(target)
        file = target
    on_progress(1.0, file.name)
    return {"filename": file.name, "title": info.get("title") or file.stem,
            "duration": info.get("duration"), "mb": round(file.stat().st_size / 1e6)}
