"""Pfade und Grundeinstellungen von Voicitool."""
import json
import os
import subprocess
from pathlib import Path


def _hide_child_windows():
    """Unter Windows jeden Unterprozess ohne Konsolenfenster starten.

    Voicitool läuft ohne Konsole (pythonw). Startet eine Bibliothek (yt-dlp, audio-separator, pydub …)
    ein Konsolenprogramm wie ffmpeg, öffnet Windows dafür sonst jedes Mal ein Terminalfenster.
    Wer creationflags selbst setzt, behält seine Werte.
    """
    if os.name != "nt" or getattr(subprocess.Popen, "_vt_hidden", False):
        return
    original = subprocess.Popen.__init__

    def init(self, *args, **kwargs):
        if not kwargs.get("creationflags") and len(args) < 13:
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        original(self, *args, **kwargs)

    subprocess.Popen.__init__ = init
    subprocess.Popen._vt_hidden = True


_hide_child_windows()

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
STATIC_DIR = APP_DIR / "static"
INBOX_DIR = ROOT / "eingang"
PROJECTS_DIR = ROOT / "projekte"
EXPORT_DIR = ROOT / "export"
# Der Export-Ordner ist in drei Teile sortiert: fertige ZIPs, die Pack-Ordner und die Videos aus Aufnahmen
EXPORT_ZIPS = EXPORT_DIR / "Zips"
EXPORT_PACKS = EXPORT_DIR / "Ordner"
EXPORT_VIDEOS = EXPORT_DIR / "Exportierte Videos"
MODELS_DIR = ROOT / "modelle"
DATA_DIR = ROOT / "daten"      # Einrichtung, Fenster-Speicher, Logs
TOOLS_DIR = ROOT / "tools"     # uv, Python, ffmpeg

for _d in (INBOX_DIR, PROJECTS_DIR, EXPORT_DIR, EXPORT_ZIPS, EXPORT_PACKS, EXPORT_VIDEOS, MODELS_DIR, DATA_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Modelle landen im Tool-Ordner statt im Benutzerprofil
os.environ.setdefault("HF_HOME", str(MODELS_DIR / "huggingface"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TORCH_HOME", str(MODELS_DIR / "torch"))

APP_NAME = "Voicitool"
AUTHOR = "Jan1653"   # Entwickler (nur im Programm und in der Credit-Variante „by Jan1653“)
# Version und Build-Nummer stehen in app/version.json (die Build-Nummer steigt mit jeder Änderung;
# Voicitool.exe vergleicht sie mit GitHub und mit der letzten Einrichtung)
_VERSION = json.loads((APP_DIR / "version.json").read_text(encoding="utf8"))
APP_VERSION = _VERSION["version"]
APP_BUILD = int(_VERSION["build"])
UPDATE_REPO = _VERSION.get("repo", "")
# Anonyme Zählung aktiver Installationen (GoatCounter-Seitenname, leer = aus). Siehe app.js countActive()
STATS_SITE = _VERSION.get("stats", "")
# Credits im Pack: pro Projekt einstellbar, bei jedem neuen Projekt an
#   text:       made_with | made_with_by | made_with_link (Link nur, wenn ein GitHub-Repo eingetragen ist)
#   in_authors: als Eintrag in der Autorenliste · in_readme: Zeile in der Beschreibung
#   in_files:   Kennzeichnung in Dateien (INI-Kommentare, Audio/Video-Tags, ZIP-Kommentar, Hinweisdatei)
DEFAULT_CREDITS = {"enabled": True, "text": "made_with", "in_authors": True, "in_readme": False, "in_files": True}


def credit_text(credits):
    """Der Credit-Text für ein Projekt, oder None, wenn Credits ausgeschaltet sind."""
    c = dict(DEFAULT_CREDITS, **(credits or {}))
    if not c["enabled"]:
        return None
    if c["text"] == "made_with_by":
        return f"Made with {APP_NAME} by {AUTHOR}"
    if c["text"] == "made_with_link" and UPDATE_REPO:
        return f"Made with {APP_NAME} (github.com/{UPDATE_REPO})"
    return f"Made with {APP_NAME}"


# Leise Kennzeichnung in den Metadaten jeder erzeugten Datei (Video, Ton, Bilder). Sie steht nur in
# den Eigenschaften der Datei, nicht im Pack, und ist unabhängig vom Credits-Schalter: so lässt sich
# später noch nachsehen, womit eine Datei gemacht wurde. Kein Name, kein Pfad, nichts Persönliches.
SIGNATURE = f"{APP_NAME} {APP_VERSION}"


def file_tags(credit=None):
    """Metadaten für eine erzeugte Datei. credit: der sichtbare Credit-Text, wenn er eingeschaltet ist."""
    return {"software": SIGNATURE, "comment": f"{credit} · {SIGNATURE}" if credit else SIGNATURE}


def ffmpeg_tags(credit=None):
    """Dieselben Metadaten als ffmpeg-Argumente."""
    out = []
    for key, value in file_tags(credit).items():
        out += ["-metadata", f"{key}={value}"]
    return out


def _setup_ffmpeg():
    """Mitgeliefertes ffmpeg 7.1 (imageio-ffmpeg) als tools/ffmpeg/ffmpeg.exe bereitstellen.

    Kein System-ffmpeg nötig. Aktuelle gyan.dev-Builds (ffmpeg 8) erzeugen außerdem defekte Theora-Videos.
    audio-separator ruft „ffmpeg“ über PATH auf -> Ordner vorne in PATH eintragen.
    """
    target = TOOLS_DIR / "ffmpeg" / "ffmpeg.exe"
    if not target.exists():
        try:
            import shutil
            import imageio_ffmpeg
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(imageio_ffmpeg.get_ffmpeg_exe(), target)
        except Exception:
            import shutil
            return shutil.which("ffmpeg") or "ffmpeg"
    os.environ["PATH"] = str(target.parent) + os.pathsep + os.environ.get("PATH", "")
    return str(target)


FFMPEG = _setup_ffmpeg()

HOST = "127.0.0.1"
PORT = int(os.environ.get("VOICITOOL_PORT", "7863"))

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aiff", ".aif"}
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".ogv", ".flv", ".wmv", ".ts", ".mpg", ".mpeg"}

# Choicer Voicer speichert Packs hier (Godot-User-Ordner des Spiels)
GAME_PACKS_DIR = Path(os.environ.get("APPDATA", "")) / "YeahMaybe" / "ChoicerVoicer" / "game" / "packs_voice"
SETTINGS_FILE = DATA_DIR / "einstellungen.json"


def user_settings():
    """Einstellungen aus daten/einstellungen.json (leer, wenn es noch keine gibt)."""
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf8"))
    except Exception:
        return {}


# Sprachen der Oberfläche (wie in static/i18n.js)
UI_LANGS = ("en", "de", "es", "fr", "pt", "it", "ru", "pl", "tr", "nl", "uk", "id", "ja", "zh", "ko", "hi",
            "cs", "sk", "sr", "sv", "da", "ro", "hu", "el", "vi", "th")


def system_lang():
    """Anzeigesprache von Windows, falls Voicitool sie kann, sonst Englisch."""
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        buf = ctypes.create_unicode_buffer(85)
        k32.LCIDToLocaleName(k32.GetUserDefaultUILanguage(), buf, 85, 0)   # z. B. "de-DE", "sr-Latn-RS"
        code = buf.value
    except Exception:
        code = ""
    code = code.replace("_", "-").split("-")[0].lower()
    return code if code in UI_LANGS else "en"


def ui_lang():
    """Sprache der Oberfläche: eigene Wahl aus den Einstellungen, sonst die von Windows."""
    lang = user_settings().get("ui_lang")
    return lang if lang in UI_LANGS else system_lang()


def tidy_export():
    """Einmalig: alte Ausgaben aus dem Export-Ordner in Zips, Ordner und Exportierte Videos einsortieren."""
    import shutil
    moved = 0
    for item in list(EXPORT_DIR.iterdir()):
        if item in (EXPORT_ZIPS, EXPORT_PACKS, EXPORT_VIDEOS) or item.name.endswith(".tmp"):
            continue
        if item.is_dir() and (item / "_pack_info.ini").exists():
            target = EXPORT_PACKS / item.name
        elif item.is_file() and item.suffix.lower() == ".zip":
            target = EXPORT_ZIPS / item.name
        elif item.is_file() and item.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov"):
            target = EXPORT_VIDEOS / item.name
        else:
            continue
        try:
            if target.exists():
                continue
            shutil.move(str(item), str(target))
            moved += 1
        except OSError:
            pass
    return moved


def game_packs_dir():
    """Pack-Ordner des Spiels; in den Einstellungen änderbar (z. B. bei einer anderen Installation)."""
    custom = (user_settings().get("game_dir") or "").strip()
    return Path(custom) if custom else GAME_PACKS_DIR


# Spiel-Mod voicigame (Mitspielen am Handy und im Browser, siehe app/voicigame.py) installieren.
# An, seit der Server voicigame.duckdns.org läuft. Auf False gesetzt sieht niemand etwas davon
# (außer "voicigame": true in daten/einstellungen.json oder VOICITOOL_VOICIGAME=1).
VOICIGAME = True


def voicigame_enabled():
    if VOICIGAME or os.environ.get("VOICITOOL_VOICIGAME") == "1":
        return True
    return user_settings().get("voicigame") is True

# Stimmen-Trennung (BS-RoFormer, sehr gute Trennung Stimme/Rest)
SEPARATOR_MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"

# Qualitätsstufen („KI-Mühe“) für Stimmen-Trennung und Spracherkennung
#   whisper:  Modell (turbo = schneller, large-v3 = genauer)
#   beam:     Suchbreite der Texterkennung (mehr = gründlicher, langsamer)
#   overlap:  Überlappung der Stimmen-Trennung (mehr = sauberer, Rechenzeit steigt linear)
#   speaker2: zweites Stimm-Modell zusätzlich (ResNet neben ECAPA). Kostet nur Sekunden und ordnet deutlich
#             besser zu (22 Referenz-Packs: Dialog 79,8 -> 82,6 %, Lieder 70,7 -> 75,6 %), deshalb in allen Stufen.
#             Ist das Modell nicht installiert, wird ohne es gerechnet.
QUALITY = {
    "schnell": {"label": "Schnell", "whisper": "large-v3-turbo", "beam": 1, "best_of": 1, "patience": 1.0,
                "vad_threshold": 0.4, "overlap": 2, "speaker2": True},
    "standard": {"label": "Standard", "whisper": "large-v3", "beam": 5, "best_of": 5, "patience": 1.0,
                 "vad_threshold": 0.35, "overlap": 4, "speaker2": True},
    "maximal": {"label": "Maximal", "whisper": "large-v3", "beam": 10, "best_of": 5, "patience": 1.5,
                "vad_threshold": 0.25, "overlap": 10, "speaker2": True},
    "extrem": {"label": "Extrem (sehr langsam)", "whisper": "large-v3", "beam": 12, "best_of": 8, "patience": 2.0,
               "vad_threshold": 0.2, "overlap": 16, "speaker2": True},
}
DEFAULT_QUALITY = "standard"


def quality(name):
    return QUALITY.get(name or DEFAULT_QUALITY, QUALITY[DEFAULT_QUALITY])
# Sprecher-Embeddings (Stimmprofile). Das zweite Modell kommt in den hohen Stufen dazu:
# es erkennt zusätzlich seltene Nebensprecher, die ECAPA allein zusammenwirft.
SPEAKER_MODEL = "speechbrain/spkrec-ecapa-voxceleb"
SPEAKER_MODEL_2 = "speechbrain/spkrec-resnet-voxceleb"

CHARACTER_COLORS = [
    "#4f8cff", "#ff6b6b", "#3ecf8e", "#f5a524", "#b57bff", "#ff8fd1",
    "#22c3d6", "#c8d64a", "#ff9f5a", "#8fa3bf", "#e05ce0", "#5ad19a",
]
