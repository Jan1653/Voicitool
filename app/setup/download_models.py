"""Lädt die gewählten KI-Modelle vorab herunter. Fortschritt als JSON-Zeilen (für den Einrichtungs-Assistenten).

Aufruf: download_models.py [--models id,id,...]
Ohne Angabe: alle Pflicht-Modelle und alle, die als Standard markiert sind (Englisch, Zusätze).
"""
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app import config, models  # noqa: E402,F401


def emit(**kw):
    print(json.dumps(kw, ensure_ascii=False), flush=True)


def main():
    wanted = None
    if "--models" in sys.argv:
        wanted = [m for m in sys.argv[sys.argv.index("--models") + 1].split(",") if m]
    ids = [m["id"] for m in models.REGISTRY
           if m.get("required") or (m["id"] in wanted if wanted is not None else m.get("default"))]
    if wanted is None and models.whisper_installed():
        ids = [i for i in ids if i != "whisper-en"]   # vorhandenes Sprachpaket kann Englisch schon
    todo = [i for i in ids if not models.installed(i)]
    total_mb = sum(models.BY_ID[i]["mb"] for i in todo) or 1
    done_mb = 0.0
    for n, mid in enumerate(todo, 1):
        m = models.BY_ID[mid]
        label = f"{m['name']} (~{m['mb']} MB)"
        emit(type="step", key=mid, label=label, step=n, steps=len(todo))

        def progress(p, msg="", base=done_mb, mb=m["mb"], label=label):
            emit(type="progress", pct=round(min(0.99, (base + p * mb) / total_mb), 4), label=label,
                 step=n, steps=len(todo), mb=round(base + p * mb))

        models.download(mid, progress)
        done_mb += m["mb"]
    emit(type="done", mb=round(done_mb), installed=[m["id"] for m in models.status() if m["installed"]])


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        emit(type="error", error=f"{type(e).__name__}: {e}")
        sys.exit(1)
