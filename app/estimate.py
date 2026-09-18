"""Wie lange dauert die Verarbeitung? Schätzung, die mit jedem Lauf genauer wird.

Grundmodell je Schritt: Grundzeit + Anteil pro Sekunde Video, gemessen auf einer RTX 4060 in Stufe
Standard. Dazu Faktoren für die Qualitätsstufe und für Prozessor statt Grafikkarte. Nach jedem Lauf
wird gespeichert, wie lange die Schritte wirklich gedauert haben; der Mittelwert (Median) aus
tatsächlich/geschätzt korrigiert danach die Schätzung für genau diesen PC.
"""
import json
import os
import threading
import time

from app import config

# (Grundzeit s, Sekunden pro Sekunde Video) auf RTX 4060, Stufe Standard
BASE = {
    "Vorbereiten": (1.0, 0.035),
    "Stimmen trennen": (3.0, 0.62),
    "Sprache erkennen": (5.0, 0.075),
    "Sprecher erkennen": (1.0, 0.01),
    "Zeilen bauen": (0.3, 0.001),
    "Lachen erkennen": (1.5, 0.045),
}
ORDER = list(BASE)

# relative Rechenzeit der Qualitätsstufen je Schritt (Trennung wächst mit der Überlappung,
# Erkennung mit Suchbreite und Modell, Sprecher mit dem zweiten Stimm-Modell)
QUALITY_FACTOR = {
    "schnell": {"Stimmen trennen": 0.5, "Sprache erkennen": 0.4},
    "standard": {},
    "maximal": {"Stimmen trennen": 2.5, "Sprache erkennen": 1.6, "Sprecher erkennen": 1.8},
    "extrem": {"Stimmen trennen": 4.0, "Sprache erkennen": 2.0, "Sprecher erkennen": 1.8},
}
# ohne NVIDIA-Grafikkarte (bei 8 Prozessorkernen; weniger Kerne = langsamer)
CPU_FACTOR = {"Stimmen trennen": 12.0, "Sprache erkennen": 8.0, "Sprecher erkennen": 4.0, "Lachen erkennen": 6.0}

HISTORY = config.DATA_DIR / "zeiten.json"
KEEP = 40
_lock = threading.Lock()
_device = None


def device():
    """'cuda' oder 'cpu' (einmal ermittelt, ohne PyTorch zu laden, falls möglich)."""
    global _device
    if _device is None:
        try:
            setup = json.loads((config.DATA_DIR / "setup.json").read_text(encoding="utf-8-sig"))
            _device = "cpu" if setup.get("variant") == "cpu" else "cuda"
        except Exception:
            try:
                import torch
                _device = "cuda" if torch.cuda.is_available() else "cpu"
            except Exception:
                _device = "cpu"
    return _device


def _cpu_scale():
    cores = os.cpu_count() or 8
    return (8 / max(2, cores)) ** 0.7


def _history():
    try:
        return json.loads(HISTORY.read_text(encoding="utf8"))
    except Exception:
        return []


def _corrections(dev):
    """Korrekturfaktor je Schritt aus den letzten Läufen auf diesem PC."""
    ratios = {}
    for run in _history():
        if run.get("device") != dev:
            continue
        for step, (actual, predicted) in run.get("steps", {}).items():
            if predicted > 0.5 and actual > 0:
                ratios.setdefault(step, []).append(actual / predicted)
    out = {}
    for step, r in ratios.items():
        r = sorted(r[-10:])
        out[step] = min(5.0, max(0.25, r[len(r) // 2]))
    return out


def _raw(step, seconds, quality, dev):
    base, per_s = BASE[step]
    t = base + per_s * seconds
    t *= QUALITY_FACTOR.get(quality, {}).get(step, 1.0)
    if dev == "cpu":
        t *= CPU_FACTOR.get(step, 1.0) * (_cpu_scale() if step in CPU_FACTOR else 1.0)
    return t


def estimate(seconds, quality="standard", laugh=True, dev=None):
    """Geschätzte Dauer je Schritt und gesamt (Sekunden)."""
    dev = dev or device()
    corr = _corrections(dev)
    steps = []
    for step in ORDER:
        if step == "Lachen erkennen" and not laugh:
            continue
        raw = _raw(step, seconds or 0, quality, dev)
        steps.append([step, round(raw * corr.get(step, 1.0), 1), round(raw, 1)])
    learned = sum(1 for r in _history() if r.get("device") == dev)
    return {"steps": [[s, t] for s, t, _ in steps], "raw": {s: r for s, _, r in steps},
            "total": round(sum(t for _, t, _ in steps), 1), "device": dev, "learned_runs": learned}


def record(seconds, quality, laugh, step_times, dev=None):
    """Tatsächliche Schrittzeiten eines fertigen Laufs speichern (für bessere Schätzungen)."""
    dev = dev or device()
    steps = {}
    for step, actual in step_times.items():
        if step in BASE and actual > 0:
            steps[step] = [round(actual, 2), round(_raw(step, seconds, quality, dev), 2)]
    if not steps:
        return
    with _lock:
        hist = _history()
        hist.append({"time": time.time(), "device": dev, "seconds": round(seconds, 1), "quality": quality,
                     "laugh": bool(laugh), "steps": steps})
        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        HISTORY.write_text(json.dumps(hist[-KEEP:], indent=1), encoding="utf8")
