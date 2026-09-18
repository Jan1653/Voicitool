"""Prüft nach der Einrichtung, ob alle Bausteine laden (vom Einrichtungs-Assistenten aufgerufen)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

print("Pakete werden geladen …", flush=True)
import torch  # noqa: E402
import faster_whisper  # noqa: E402,F401
import audio_separator  # noqa: E402,F401
import speechbrain  # noqa: E402,F401
import transformers  # noqa: E402,F401
import webview  # noqa: E402,F401
import fastapi  # noqa: E402,F401
from app import config  # noqa: E402
from app.pipeline import media  # noqa: E402

print(f"ffmpeg: {Path(config.FFMPEG).name}, Theora: {media.has_encoder('libtheora')}", flush=True)
if torch.cuda.is_available():
    print(f"GPU bereit: {torch.cuda.get_device_name(0)} (CUDA {torch.version.cuda})", flush=True)
else:
    print("Läuft auf dem Prozessor (keine GPU erkannt)", flush=True)
print("OK", flush=True)
