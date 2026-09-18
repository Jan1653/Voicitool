"""KI-Modelle und Sprachpakete: was es gibt, was installiert ist, laden, löschen, auswählen.

Sprachpakete sind Whisper-Modelle für die Texterkennung:
  * Englisch (Standard)             distil-large-v3.5, nur Englisch, 1,5 GB
  * Alle Sprachen, schnell          large-v3-turbo, 1,6 GB
  * Alle Sprachen, beste Qualität   large-v3, 3,1 GB
Welches Modell eine Verarbeitung nutzt, entscheidet pick_whisper(): gewünschtes Modell der
Qualitätsstufe, sonst das beste installierte, das die Sprache kann.
"""
import json
import os
import shutil
import threading
import time
from pathlib import Path

from app import config

WHISPER_REPOS = {
    "distil-large-v3.5": "distil-whisper/distil-large-v3.5-ct2",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "large-v3": "Systran/faster-whisper-large-v3",
}
ENGLISH_ONLY = {"distil-large-v3.5"}
LAUGH_REPO = "MIT/ast-finetuned-audioset-10-10-0.4593"

# group, name, desc: Deutsch (Quelle für die Übersetzung der Oberfläche)
REGISTRY = [
    {"id": "whisper-en", "group": "Sprachpakete", "name": "Englisch",
     "desc": "Texterkennung nur für Englisch, schnell und genau.", "mb": 1520, "kind": "whisper",
     "ref": "distil-large-v3.5", "default": True},
    {"id": "whisper-turbo", "group": "Sprachpakete", "name": "Alle Sprachen (schnell)",
     "desc": "Deutsch, Spanisch, Französisch und fast 100 weitere Sprachen. Für die Stufe Schnell.",
     "mb": 1620, "kind": "whisper", "ref": "large-v3-turbo"},
    {"id": "whisper-large", "group": "Sprachpakete", "name": "Alle Sprachen (beste Qualität)",
     "desc": "Genaueste Texterkennung in allen Sprachen, auch bei Gesang. Für Standard, Maximal und Extrem.",
     "mb": 3090, "kind": "whisper", "ref": "large-v3"},
    {"id": "separator", "group": "Pflicht", "name": "Stimmen-Trennung (BS-RoFormer)",
     "desc": "Trennt Stimmen vom Hintergrund.", "mb": 640, "kind": "separator", "required": True},
    {"id": "ecapa", "group": "Pflicht", "name": "Sprecher-Erkennung (ECAPA)",
     "desc": "Erkennt, wer spricht.", "mb": 80, "kind": "speechbrain", "ref": config.SPEAKER_MODEL,
     "dir": "ecapa", "required": True},
    {"id": "resnet", "group": "Zusätze", "name": "Zweites Stimm-Modell (ResNet)",
     "desc": "Ordnet Sprecher deutlich besser zu, in allen Stufen. Ohne es geht es auch, nur ungenauer.", "mb": 100, "kind": "speechbrain",
     "ref": config.SPEAKER_MODEL_2, "dir": "resnet", "default": True},
    {"id": "laugh", "group": "Zusätze", "name": "Lach-Erkennung (AST)",
     "desc": "Findet Lacher und legt sie als eigene Zeilen an.", "mb": 350, "kind": "laugh", "default": True},
]
BY_ID = {m["id"]: m for m in REGISTRY}
SETTINGS = config.SETTINGS_FILE
_lock = threading.Lock()


class MissingLanguagePack(RuntimeError):
    pass


# ------------------------------------------------------------------ installiert?

def _hf_dir(repo, cache):
    return Path(cache) / ("models--" + repo.replace("/", "--"))


def _whisper_ok(ref):
    d = _hf_dir(WHISPER_REPOS[ref], config.MODELS_DIR / "whisper")
    return any(d.glob("snapshots/*/model.bin"))


def model_path(mid):
    m = BY_ID[mid]
    if m["kind"] == "whisper":
        return _hf_dir(WHISPER_REPOS[m["ref"]], config.MODELS_DIR / "whisper")
    if m["kind"] == "separator":
        return config.MODELS_DIR / "separator" / config.SEPARATOR_MODEL
    if m["kind"] == "speechbrain":
        return config.MODELS_DIR / m["dir"]
    if m["kind"] == "laugh":
        return _hf_dir(LAUGH_REPO, config.MODELS_DIR / "huggingface" / "hub")
    raise KeyError(mid)


def installed(mid):
    m = BY_ID[mid]
    p = model_path(mid)
    if m["kind"] == "whisper":
        return _whisper_ok(m["ref"])
    if m["kind"] == "separator":
        return p.exists() and p.stat().st_size > 100e6
    if m["kind"] == "speechbrain":
        return (p / "embedding_model.ckpt").exists()
    if m["kind"] == "laugh":
        return any(p.glob("snapshots/*/model.safetensors")) or any(p.glob("snapshots/*/pytorch_model.bin"))
    return False


def _size(p):
    p = Path(p)
    if p.is_file():
        return p.stat().st_size
    total = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def status():
    out = []
    for m in REGISTRY:
        ok = installed(m["id"])
        out.append({k: m[k] for k in ("id", "group", "name", "desc", "mb")} |
                   {"required": bool(m.get("required")), "default": bool(m.get("default")), "installed": ok,
                    "disk_mb": round(_size(model_path(m["id"])) / 1e6) if ok else 0,
                    "english_only": m.get("ref") in ENGLISH_ONLY})
    return out


# ------------------------------------------------------------------ laden und löschen

def download(mid, on_progress=None):
    """Modell herunterladen. on_progress(Anteil 0..1, Text)."""
    m = BY_ID[mid]
    target = model_path(mid)
    start = _size(target) if target.exists() else 0
    done = {"stop": False}

    def watch():   # Fortschritt über die wachsende Größe auf der Platte
        while not done["stop"]:
            now = _size(target) if target.exists() else 0
            if on_progress:
                on_progress(min(0.99, (now - start) / (m["mb"] * 1e6)), f"{now / 1e6:.0f} von {m['mb']} MB")
            time.sleep(0.5)

    threading.Thread(target=watch, daemon=True).start()
    try:
        if m["kind"] == "whisper":
            from faster_whisper.utils import download_model
            download_model(m["ref"], cache_dir=str(config.MODELS_DIR / "whisper"))
        elif m["kind"] == "separator":
            from audio_separator.separator import Separator
            sep = Separator(model_file_dir=str(config.MODELS_DIR / "separator"), output_dir=str(config.DATA_DIR))
            sep.download_model_files(config.SEPARATOR_MODEL)
        elif m["kind"] == "speechbrain":
            from app.pipeline import diarize
            diarize._encoder("cpu", m["ref"], m["dir"])
        elif m["kind"] == "laugh":
            from transformers import ASTFeatureExtractor, ASTForAudioClassification
            ASTFeatureExtractor.from_pretrained(LAUGH_REPO)
            ASTForAudioClassification.from_pretrained(LAUGH_REPO)
    finally:
        done["stop"] = True
    if not installed(mid):
        raise RuntimeError(f"{m['name']} konnte nicht geladen werden.")
    if on_progress:
        on_progress(1.0, m["name"])


def delete(mid):
    m = BY_ID[mid]
    if m.get("required"):
        raise ValueError(f"{m['name']} wird immer gebraucht und kann nicht gelöscht werden.")
    p = model_path(mid)
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    elif p.exists():
        p.unlink()


# ------------------------------------------------------------------ Einstellungen und Auswahl

def settings():
    try:
        return json.loads(SETTINGS.read_text(encoding="utf8"))
    except Exception:
        return {}


def save_settings(values):
    with _lock:
        cur = settings()
        cur.update(values)
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(json.dumps(cur, indent=1, ensure_ascii=False), encoding="utf8")
        return cur


def whisper_installed():
    return [ref for ref in WHISPER_REPOS if _whisper_ok(ref)]


def pick_whisper(quality, language):
    """Welches Whisper-Modell? (Wunsch der Qualitätsstufe → beste installierte Alternative)."""
    english = language == "en"
    have = whisper_installed()
    usable = [r for r in have if english or r not in ENGLISH_ONLY]
    choice = settings().get("whisper_model", "auto")
    if choice in usable:
        return choice
    wanted = config.quality(quality)["whisper"]
    order = [wanted, "large-v3", "large-v3-turbo"] + (["distil-large-v3.5"] if english else [])
    for ref in order:
        if ref in usable:
            return ref
    if language in (None, "auto") and "distil-large-v3.5" in have:
        return "distil-large-v3.5"   # nur das Englisch-Paket da: „automatisch“ heißt dann Englisch
    if not have:
        raise MissingLanguagePack("Es ist noch kein Sprachpaket installiert. Bitte unter Einstellungen → "
                                  "KI-Modelle eins laden.")
    raise MissingLanguagePack("Für diese Sprache fehlt das Sprachpaket »Alle Sprachen«. Bitte unter "
                              "Einstellungen → KI-Modelle laden oder als Sprache Englisch wählen.")


def is_english_only(ref):
    return ref in ENGLISH_ONLY
