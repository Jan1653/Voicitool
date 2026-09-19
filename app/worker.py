"""Führt die KI-Verarbeitung in einem eigenen Prozess aus.

Vorteil: Abbrechen wirkt sofort (Prozess wird beendet, Grafikspeicher ist sofort frei).
Kommunikation: Zeilen mit Präfix @@VT@@ + JSON auf stdout.
"""
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402,F401  (setzt Modell-Pfade)
from app.pipeline import project  # noqa: E402

PREFIX = "@@VT@@"
# Eigener Kanal für Meldungen an den Server; alle anderen Ausgaben (Bibliotheken) gehen nach stderr
sys.stdout.flush()
_out = os.fdopen(os.dup(1), "w", encoding="utf8", buffering=1)
os.dup2(2, 1)
sys.stdout = sys.stderr


_emit_lock = threading.Lock()


def emit(**kw):
    line = PREFIX + json.dumps(kw, ensure_ascii=False) + "\n"
    with _emit_lock:   # Fortschritt und Lebenszeichen kommen aus zwei Threads
        _out.write(line)
        _out.flush()


def _watch_parent(parent_pid):
    """Endet der Server, beendet sich auch der Worker.

    Wartet direkt auf das Prozess-Handle – NICHT per stdin-Lesen: ein blockierender Lesezugriff
    auf eine Pipe lässt unter Windows andere Zugriffe auf Standard-Handles hängen.
    """
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x00100000, False, parent_pid)  # SYNCHRONIZE
    if not handle:
        return
    kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
    os._exit(3)


def main():
    kind, pid = sys.argv[1], sys.argv[2]
    if len(sys.argv) > 3 and sys.platform == "win32":
        threading.Thread(target=_watch_parent, args=(int(sys.argv[3]),), daemon=True).start()
    last = {"t": 0.0, "step": None}

    def heartbeat():   # Lebenszeichen für den Server: bleibt es aus, hängt der Prozess
        while True:
            time.sleep(5)
            emit(type="alive")
    threading.Thread(target=heartbeat, daemon=True).start()

    def report(step, pct, msg=""):
        now = time.time()
        if step != last["step"] or now - last["t"] > 0.25 or pct >= 1:
            last.update(t=now, step=step)
            emit(type="progress", step=step, pct=float(pct), message=msg)

    try:
        result = None
        if kind == "process":
            project.process(pid, report)
        elif kind == "laughs":
            result = project.find_laughs(pid, report)
        else:
            raise ValueError(f"Unbekannter Job: {kind}")
        emit(type="done", result=result)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        emit(type="error", error=f"{type(e).__name__}: {e}", code=getattr(e, "code", None))
        sys.exit(1)


if __name__ == "__main__":
    main()
