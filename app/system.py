"""Systemprüfung: Was hat dieser PC, und reicht das für die geplante Arbeit?

info()          Hardware: Grafikkarte, Grafikspeicher, Arbeitsspeicher, Prozessor, freier Platz
health()        allgemeine Hinweise zum PC (für Einstellungen → System und den Start)
preflight()     Prüfung vor Verarbeitung, Export oder Download. Stufen: info (zur Kenntnis),
                warn (klappt vermutlich, kann langsam werden oder Probleme machen),
                block (klappt so sehr wahrscheinlich nicht). Weitermachen geht trotzdem.
friendly_error()  technische Fehler (Grafikspeicher voll, Platte voll …) als klare Sätze
Texte sind Deutsch (Quelle der Übersetzung, siehe static/lang/strings.js); Zahlen stehen an {}.
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from app import config

GB = 1024 ** 3

# Grafikspeicher in GB, gemessen auf RTX 4060 (Spitze je Stufe, meist die Texterkennung) plus Reserve
VRAM_NEED = {"schnell": 3.6, "standard": 5.2, "maximal": 5.6, "extrem": 6.0}
VRAM_MIN = 2.0          # darunter klappt es auf der Grafikkarte praktisch nicht
RAM_BASE = {"gpu": 4.0, "cpu": 6.0}   # Arbeitsspeicher in GB ohne Video
RAM_PER_S = 0.0015      # GB je Sekunde Video (Audio in mehreren Kopien während der Trennung)
DISK_PER_S = 0.0012     # GB je Sekunde Video im Projektordner (Ton, Stimmen, Hintergrund, Vorschau)
EXPORT_PER_S = 0.002    # GB je Sekunde Video beim Export (Clips, Video, ZIP)
QUALITY_NAME = {"schnell": "Schnell", "standard": "Standard", "maximal": "Maximal", "extrem": "Extrem"}


def fmt_gb(v):
    return f"{v:.1f}".replace(".", ",") if v < 10 else f"{v:.0f}"


def fmt_dur(s):
    s = int(round(s))
    if s < 90:
        return f"{s} s"
    if s < 3600:
        return f"{round(s / 60)} min"
    h, m = divmod(round(s / 60), 60)
    return f"{h} h {m} min" if m else f"{h} h"


def issue(level, text, sub=None, id=None, **extra):
    d = {"level": level, "text": text, "id": id or text[:40]}
    if sub:
        d["sub"] = sub
    d.update(extra)
    return d


# ------------------------------------------------------------------ Hardware

def _run(cmd, timeout=6):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _smi():
    p = shutil.which("nvidia-smi") or os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "nvidia-smi.exe")
    return p if os.path.exists(p) else None


def nvidia():
    """Erste NVIDIA-Grafikkarte: Name, Grafikspeicher gesamt/belegt/frei (GB), Treiber, Rechenfähigkeit."""
    smi = _smi()
    if not smi:
        return None
    out = _run([smi, "--query-gpu=name,memory.total,memory.used,driver_version,compute_cap",
                "--format=csv,noheader,nounits"])
    if not out.strip():   # ältere Treiber kennen compute_cap nicht
        out = _run([smi, "--query-gpu=name,memory.total,memory.used,driver_version", "--format=csv,noheader,nounits"])
    line = out.strip().splitlines()[0] if out.strip() else ""
    p = [x.strip() for x in line.split(",")]
    if len(p) < 4:
        return None
    try:
        total, used = float(p[1]) / 1024, float(p[2]) / 1024
    except ValueError:
        return None
    return {"name": p[0], "vram_total": round(total, 2), "vram_used": round(used, 2),
            "vram_free": round(max(0.0, total - used), 2), "driver": p[3],
            "compute_cap": p[4] if len(p) > 4 and p[4] not in ("", "[N/A]") else None}


def other_gpus():
    """Namen und Grafikspeicher aller Grafikkarten aus der Registry (auch AMD/Intel)."""
    out = []
    try:
        import winreg
        base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            for i in range(64):
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                if not sub.isdigit():
                    continue
                try:
                    with winreg.OpenKey(root, sub) as k:
                        name = winreg.QueryValueEx(k, "DriverDesc")[0]
                        mem = 0
                        for val in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                            try:
                                v = winreg.QueryValueEx(k, val)[0]
                                mem = int.from_bytes(v, "little") if isinstance(v, bytes) else int(v)
                                break
                            except OSError:
                                continue
                except OSError:
                    continue
                if any(x in name for x in ("Basic", "Remote", "Virtual", "Parsec", "Meta")):
                    continue
                out.append({"name": name, "vram_total": round(mem / GB, 2) if mem else None})
    except Exception:
        pass
    return out


class _MEMSTAT(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def ram():
    try:
        st = _MEMSTAT()
        st.dwLength = ctypes.sizeof(_MEMSTAT)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return {"total": round(st.ullTotalPhys / GB, 1), "free": round(st.ullAvailPhys / GB, 1)}
    except Exception:
        return {"total": None, "free": None}


def cpu():
    name = ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            name = winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
    except Exception:
        pass
    return {"name": name or "Prozessor", "cores": os.cpu_count() or 0}


def disk(path=None):
    try:
        u = shutil.disk_usage(path or config.ROOT)
        return {"free": round(u.free / GB, 1), "total": round(u.total / GB, 1), "drive": Path(path or config.ROOT).anchor}
    except Exception:
        return {"free": None, "total": None, "drive": ""}


def setup_variant():
    try:
        return json.loads((config.DATA_DIR / "setup.json").read_text(encoding="utf-8-sig")).get("variant") or ""
    except Exception:
        return ""


# ------------------------------------------------------------------ passt PyTorch zur Grafikkarte?
# Neuere PyTorch-Versionen unterstützen sehr alte Grafikkarten nicht mehr („no kernel image“).
# Einmal in einem eigenen Prozess prüfen (PyTorch im Server zu laden kostet Zeit und Speicher).
_gpu_check = {"state": "offen", "result": None}
_gpu_lock = threading.Lock()
GPU_CHECK_FILE = config.DATA_DIR / "grafikkarte.json"
_PROBE = (
    "import json, torch\n"
    "d = {'cuda': torch.cuda.is_available()}\n"
    "if d['cuda']:\n"
    "    c = torch.cuda.get_device_capability(0)\n"
    "    d.update(name=torch.cuda.get_device_name(0), cap=list(c), arch=torch.cuda.get_arch_list())\n"
    "print(json.dumps(d))\n"
)


def _arch_ok(cap, arch):
    maj, mi = cap
    for a in arch:
        kind, _, num = a.partition("_")
        try:
            n = int(num.rstrip("af"))
        except ValueError:
            continue
        am, ami = divmod(n, 10)
        if kind == "sm" and am == maj and ami <= mi:
            return True
        if kind == "compute" and n <= maj * 10 + mi:
            return True
    return False


def gpu_check(wait=False):
    """{'cuda': bool, 'supported': bool, ...} oder None, solange die Prüfung läuft."""
    with _gpu_lock:
        if _gpu_check["state"] == "fertig":
            return _gpu_check["result"]
        start = _gpu_check["state"] == "offen"
        if start:
            _gpu_check["state"] = "läuft"
    if start:
        t = threading.Thread(target=_run_gpu_check, daemon=True)
        t.start()
        if wait:
            t.join(40)
    elif wait:
        for _ in range(80):
            if _gpu_check["state"] == "fertig":
                break
            time.sleep(0.5)
    return _gpu_check["result"]


def _run_gpu_check():
    gpu = nvidia()
    key = f"{setup_variant()}|{gpu['driver'] if gpu else '-'}|{config.APP_BUILD}"
    result = None
    try:
        cached = json.loads(GPU_CHECK_FILE.read_text(encoding="utf8"))
        if cached.get("key") == key:
            result = cached["result"]
    except Exception:
        pass
    if result is None:
        if not gpu or setup_variant() == "cpu":
            result = {"cuda": False, "supported": False}
        else:
            out = _run([sys.executable, "-c", _PROBE], timeout=60)
            try:
                d = json.loads(out.strip().splitlines()[-1])
            except Exception:
                d = {"cuda": False}
            d["supported"] = bool(d.get("cuda")) and _arch_ok(d.get("cap", [0, 0]), d.get("arch", []))
            result = d
        try:
            GPU_CHECK_FILE.write_text(json.dumps({"key": key, "result": result}), encoding="utf8")
        except Exception:
            pass
    with _gpu_lock:
        _gpu_check.update(state="fertig", result=result)


def device(project_settings=None):
    """'gpu' oder 'cpu' für eine Verarbeitung: Projektwahl > Einstellung > was der PC kann."""
    if (project_settings or {}).get("device") == "cpu":
        return "cpu"
    if config.user_settings().get("compute_device") == "cpu":
        return "cpu"
    if setup_variant() == "cpu":
        return "cpu"
    chk = _gpu_check["result"] if _gpu_check["state"] == "fertig" else None
    if chk is not None and not chk.get("supported"):
        return "cpu"
    return "gpu"


def gpu_allowed():
    """Darf dieser Prozess die Grafikkarte nutzen? (Server-seitige Texterkennung einzelner Zeilen)"""
    return os.environ.get("CUDA_VISIBLE_DEVICES") not in ("", "-1") and device() == "gpu"


def info():
    gpu = nvidia()
    chk = _gpu_check["result"] if _gpu_check["state"] == "fertig" else None
    return {"gpu": gpu, "gpus": other_gpus(), "ram": ram(), "cpu": cpu(), "disk": disk(),
            "variant": setup_variant(), "device": device(), "gpu_check": chk,
            "gpu_check_running": _gpu_check["state"] == "läuft",
            "compute_device": config.user_settings().get("compute_device") or "auto"}


# ------------------------------------------------------------------ allgemeine Hinweise

def health(inf=None):
    inf = inf or info()
    out = []
    gpu, r, d = inf["gpu"], inf["ram"], inf["disk"]
    chk = inf.get("gpu_check")
    if inf["variant"] and inf["variant"] != "cpu" and not gpu:
        out.append(issue("warn", "Keine NVIDIA-Grafikkarte gefunden. Voicitool rechnet solange auf dem Prozessor.",
                         "Ist der Grafiktreiber installiert? Nach einem Treiber-Update hilft Installation prüfen.", id="gpu-missing"))
    elif gpu and inf["variant"] == "cpu":
        out.append(issue("info", "Deine NVIDIA-Grafikkarte wird noch nicht genutzt.",
                         "Einstellungen → Über Voicitool → Installation prüfen richtet sie ein (viel schneller).", id="gpu-unused"))
    if chk and gpu and chk.get("cuda") is False and inf["variant"] not in ("", "cpu"):
        out.append(issue("warn", "Die Grafikkarte lässt sich gerade nicht nutzen. Voicitool rechnet auf dem Prozessor.",
                         "Grafiktreiber aktualisieren und danach Installation prüfen.", id="gpu-cuda"))
    elif chk and chk.get("cuda") and not chk.get("supported"):
        out.append(issue("warn", "Diese Grafikkarte ist zu alt für die eingebaute KI-Version. Voicitool rechnet auf dem Prozessor.",
                         f"{chk.get('name', '')}", id="gpu-arch"))
    if gpu and gpu["vram_total"] < 4:
        out.append(issue("warn", f"Wenig Grafikspeicher ({fmt_gb(gpu['vram_total'])} GB). Qualität Schnell ist am sichersten.",
                         id="vram-low"))
    if inf["compute_device"] == "cpu" and gpu:
        out.append(issue("info", "Rechnen auf dem Prozessor ist eingestellt. Mit der Grafikkarte geht es viel schneller.",
                         id="cpu-forced"))
    if r["total"]:
        if r["total"] < 4:
            out.append(issue("block", f"Nur {fmt_gb(r['total'])} GB Arbeitsspeicher. Voicitool braucht mindestens 4 GB, besser 8 GB.",
                             id="ram-tiny"))
        elif r["total"] < 8:
            out.append(issue("warn", f"Nur {fmt_gb(r['total'])} GB Arbeitsspeicher. Lange Videos können sehr langsam werden.",
                             id="ram-low"))
    if d["free"] is not None:
        if d["free"] < 1:
            out.append(issue("block", f"Fast kein Speicherplatz mehr auf {d['drive']} ({fmt_gb(d['free'])} GB frei).",
                             "Unter Einstellungen → Speicher lässt sich Platz freigeben.", id="disk-full"))
        elif d["free"] < 5:
            out.append(issue("warn", f"Wenig Speicherplatz auf {d['drive']} ({fmt_gb(d['free'])} GB frei).",
                             "Unter Einstellungen → Speicher lässt sich Platz freigeben.", id="disk-low"))
    if inf["cpu"]["cores"] and inf["cpu"]["cores"] < 4 and device() == "cpu":
        out.append(issue("warn", f"Nur {inf['cpu']['cores']} Prozessorkerne: das Rechnen dauert sehr lange.", id="cpu-few"))
    try:
        from app import models
        missing = [m for m in models.status() if m.get("required") and not m.get("installed")]
        if missing:
            out.append(issue("warn", "Es fehlen KI-Modelle: " + ", ".join(m["name"] for m in missing) + ".",
                             "Sie werden beim ersten Verarbeiten geladen (Internet nötig).", id="models-missing"))
    except Exception:
        pass
    return out


# ------------------------------------------------------------------ vor dem Start

def _missing_models_mb(quality, laugh, language):
    from app import models
    need, mb = ["separator", "ecapa"], 0
    q = config.quality(quality)
    if q.get("speaker2"):
        need.append("resnet")
    if laugh:
        need.append("laugh")
    st = {m["id"]: m for m in models.status()}
    for mid in need:
        if mid in st and not st[mid].get("installed"):
            mb += st[mid].get("mb", 0)
    return mb


def preflight_process(seconds, quality, laugh=True, language="auto", has_audio=True, dev=None, gpu_busy=False):
    from app import estimate, models
    quality = quality if quality in config.QUALITY else config.DEFAULT_QUALITY
    dev = dev or device()
    out = []
    # sicher kaputt
    if not has_audio:
        out.append(issue("block", "Das Video hat keine Tonspur. Ohne Ton gibt es nichts zu erkennen.", id="no-audio"))
    if seconds and seconds < 1:
        out.append(issue("block", "Das Video ist kürzer als eine Sekunde.", id="too-short"))
    if not seconds:
        out.append(issue("warn", "Die Länge des Videos ließ sich nicht lesen. Vielleicht ist die Datei beschädigt.", id="no-length"))
    try:
        models.pick_whisper(quality, language)
    except models.MissingLanguagePack as e:
        out.append(issue("block", str(e), id="no-langpack", action="models"))
    # Speicherplatz
    d = disk()
    model_gb = _missing_models_mb(quality, laugh, language) / 1024
    need_disk = 0.4 + DISK_PER_S * (seconds or 0) + model_gb
    if d["free"] is not None:
        if d["free"] < need_disk:
            out.append(issue("block", f"Zu wenig Speicherplatz: frei {fmt_gb(d['free'])} GB, gebraucht etwa {fmt_gb(need_disk)} GB.",
                             "Unter Einstellungen → Speicher lässt sich Platz freigeben.", id="disk", action="storage"))
        elif d["free"] < need_disk + 2:
            out.append(issue("warn", f"Wenig Speicherplatz: nur noch {fmt_gb(d['free'])} GB frei.", id="disk-low", action="storage"))
    if model_gb > 0:
        out.append(issue("info", f"Beim Start werden noch KI-Modelle geladen (etwa {round(model_gb * 1024)} MB, Internet nötig).",
                         id="models-download"))
    # Arbeitsspeicher
    r = ram()
    need_ram = RAM_BASE[dev] + RAM_PER_S * (seconds or 0)
    if r["total"]:
        if r["total"] < 4:
            out.append(issue("block", f"Nur {fmt_gb(r['total'])} GB Arbeitsspeicher. Das reicht für die KI-Modelle nicht.", id="ram-tiny"))
        elif r["free"] is not None and r["free"] < need_ram:
            out.append(issue("warn", f"Gerade sind nur {fmt_gb(r['free'])} GB Arbeitsspeicher frei, gebraucht werden etwa {fmt_gb(need_ram)} GB.",
                             "Schließe andere Programme, sonst kann es sehr langsam werden.", id="ram"))
    # Grafikkarte oder Prozessor
    est = estimate.estimate(seconds or 0, quality, laugh, "cuda" if dev == "gpu" else "cpu")
    if dev == "gpu":
        gpu = nvidia()
        need = VRAM_NEED.get(quality, 5.0)
        if gpu:
            if gpu["vram_total"] < VRAM_MIN:
                out.append(issue("block", f"Die Grafikkarte hat nur {fmt_gb(gpu['vram_total'])} GB Grafikspeicher, das reicht nicht.",
                                 "Auf dem Prozessor klappt es, dauert aber länger.", id="vram-tiny", cpu=True))
            elif gpu["vram_total"] < need:
                out.append(issue("warn", f"Für {QUALITY_NAME[quality]} braucht es etwa {fmt_gb(need)} GB Grafikspeicher, die Grafikkarte hat {fmt_gb(gpu['vram_total'])} GB.",
                                 "Das kann sehr langsam werden oder abbrechen. Tipp: eine niedrigere Qualität.", id="vram-total", cpu=True))
            elif gpu["vram_free"] < need:
                out.append(issue("warn", f"Andere Programme belegen gerade {fmt_gb(gpu['vram_used'])} GB Grafikspeicher. Frei sind {fmt_gb(gpu['vram_free'])} GB, gebraucht werden etwa {fmt_gb(need)} GB.",
                                 "Schließe Spiele und Videoprogramme, sonst kann es sehr langsam werden.", id="vram-busy", cpu=True))
    else:
        c = cpu()
        out.append(issue("info", f"Voicitool rechnet auf dem Prozessor. Geschätzte Dauer: {fmt_dur(est['total'])}.", id="cpu-mode"))
        if quality in ("maximal", "extrem"):
            out.append(issue("warn", f"{QUALITY_NAME[quality]} auf dem Prozessor dauert sehr lange (geschätzt {fmt_dur(est['total'])}).",
                             "Tipp: Schnell oder Standard.", id="cpu-quality"))
        if c["cores"] and c["cores"] < 4:
            out.append(issue("warn", f"Nur {c['cores']} Prozessorkerne: das dauert sehr lange.", id="cpu-few"))
    if seconds and seconds > 45 * 60:
        out.append(issue("warn", f"Sehr langes Video ({fmt_dur(seconds)}). Das dauert lange und braucht viel Speicher.",
                         "Tipp: das Video vorher kürzen.", id="long"))
    if gpu_busy:
        out.append(issue("info", "Es läuft schon eine Verarbeitung. Diese startet danach.", id="queued"))
    return {"issues": out, "device": dev, "estimate": est["total"],
            "cpu_option": dev == "gpu" and any(i.get("cpu") for i in out)}


def preflight_export(seconds, install=False):
    out = []
    d = disk()
    need = 0.2 + EXPORT_PER_S * (seconds or 0)
    if d["free"] is not None:
        if d["free"] < need:
            out.append(issue("block", f"Zu wenig Speicherplatz: frei {fmt_gb(d['free'])} GB, gebraucht etwa {fmt_gb(need)} GB.",
                             "Unter Einstellungen → Speicher lässt sich Platz freigeben.", id="disk", action="storage"))
        elif d["free"] < need + 1:
            out.append(issue("warn", f"Wenig Speicherplatz: nur noch {fmt_gb(d['free'])} GB frei.", id="disk-low", action="storage"))
    if install:
        game = config.game_packs_dir()
        if not game.exists() and not game.parent.exists():
            out.append(issue("block", f"Der Pack-Ordner des Spiels wurde nicht gefunden: {game}",
                             "Ist The Choicer Voicer installiert? Der Ordner lässt sich unter Einstellungen → Export ändern.",
                             id="game-dir", action="export"))
        else:
            gd = disk(game)
            if gd["free"] is not None and gd["free"] < need and gd["drive"] != d["drive"]:
                out.append(issue("block", f"Zu wenig Speicherplatz auf {gd['drive']} für das Spiel ({fmt_gb(gd['free'])} GB frei).",
                                 id="game-disk"))
    return {"issues": out}


def preflight_download(size_mb=None):
    out = []
    d = disk()
    need = (size_mb or 1024) / 1024 * 1.1 + 0.2
    if d["free"] is not None:
        if d["free"] < need:
            out.append(issue("block", f"Zu wenig Speicherplatz: frei {fmt_gb(d['free'])} GB, gebraucht etwa {fmt_gb(need)} GB.",
                             "Unter Einstellungen → Speicher lässt sich Platz freigeben.", id="disk", action="storage"))
        elif d["free"] < need + 1:
            out.append(issue("warn", f"Wenig Speicherplatz: nur noch {fmt_gb(d['free'])} GB frei.", id="disk-low", action="storage"))
    return {"issues": out}


# ------------------------------------------------------------------ Fehler verständlich machen

CRASH_CODES = {3221225477: "0xC0000005", 3221226505: "0xC0000409", 3221225725: "0xC00000FD",
               3221225786: "0xC000013A", 3221226356: "0xC0000374"}


def friendly_error(text, returncode=None):
    """(Code, verständlicher Satz) oder (None, None), wenn nichts Bekanntes erkannt wurde."""
    t = (text or "").lower()
    if ("out of memory" in t and any(x in t for x in ("cuda", "cublas", "cudnn", "gpu", "device"))) \
            or "outofmemoryerror" in t or "cuda_error_out_of_memory" in t:
        return "vram", ("Zu wenig Grafikspeicher. Schließe andere Programme (z. B. Spiele), wähle eine niedrigere Qualität "
                        "oder rechne auf dem Prozessor.")
    if "no kernel image" in t or "cuda driver version is insufficient" in t or "cudnn_status" in t \
            or "cuda error: unknown error" in t or "cublas_status" in t:
        return "gpu", "Die Grafikkarte macht Probleme (Treiber zu alt?). Rechne auf dem Prozessor oder aktualisiere den NVIDIA-Treiber."
    if "memoryerror" in t or "unable to allocate" in t or "bad_alloc" in t or "not enough memory" in t \
            or "defaultcpuallocator" in t or "paging file is too small" in t or "auslagerungsdatei" in t:
        return "ram", "Zu wenig Arbeitsspeicher. Schließe andere Programme oder wähle die Qualität Schnell."
    if "no space left" in t or "errno 28" in t or "winerror 112" in t or "nicht genügend speicherplatz" in t \
            or "not enough space on the disk" in t:
        return "disk", "Die Festplatte ist voll. Gib Speicherplatz frei (Einstellungen → Speicher) und versuche es erneut."
    if any(x in t for x in ("getaddrinfo failed", "max retries exceeded", "connectionerror", "name resolution",
                            "urlopen error", "connection aborted", "remote end closed")):
        return "net", "Keine Verbindung zum Internet. Fehlende KI-Modelle konnten nicht geladen werden."
    if returncode in CRASH_CODES:
        return "crash", (f"Die Verarbeitung ist abgestürzt (Fehlercode {CRASH_CODES[returncode]}). "
                         "Oft hilft eine niedrigere Qualität oder Rechnen auf dem Prozessor.")
    return None, None


# ------------------------------------------------------------------ Grafikspeicher gerade voll?

def vram_full():
    gpu = nvidia()
    return bool(gpu and gpu["vram_total"] and gpu["vram_used"] / gpu["vram_total"] > 0.96)


def gpu_process_memory(pid):
    """(Grafikspeicher GB, in den Arbeitsspeicher ausgelagert GB) eines Prozesses laut Windows, sonst None.

    Reicht der Grafikspeicher nicht, lagert Windows still in den Arbeitsspeicher aus: kein Fehler, aber
    alles wird extrem langsam. Die Leistungsindikatoren zeigen das je Prozess (WMI, sprachunabhängig).
    """
    ps = ("Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUProcessMemory "
          f"-Filter \"Name LIKE 'pid_{int(pid)}_%'\" | ForEach-Object {{ \"$($_.DedicatedUsage) $($_.SharedUsage)\" }}")
    out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], timeout=25)
    ded = shared = 0
    found = False
    for line in out.splitlines():
        p = line.split()
        if len(p) == 2 and p[0].isdigit() and p[1].isdigit():
            ded += int(p[0])
            shared += int(p[1])
            found = True
    return (ded / GB, shared / GB) if found else None


# Hinweistext der Verknüpfungen (Desktop, Startmenü) in der Sprache der Oberfläche
SHORTCUT_DESC = {
    "de": "Voicitool: Dub Packs für Choicer Voicer",
    "en": "Voicitool: dub packs for Choicer Voicer",
    "es": "Voicitool: packs de doblaje para Choicer Voicer",
    "fr": "Voicitool : packs de doublage pour Choicer Voicer",
    "pt": "Voicitool: pacotes de dublagem para Choicer Voicer",
    "it": "Voicitool: pacchetti di doppiaggio per Choicer Voicer",
    "ru": "Voicitool: пакеты озвучки для Choicer Voicer",
    "pl": "Voicitool: paczki dubbingu do Choicer Voicer",
    "tr": "Voicitool: Choicer Voicer için dublaj paketleri",
    "nl": "Voicitool: dubpacks voor Choicer Voicer",
    "uk": "Voicitool: пакети озвучення для Choicer Voicer",
    "id": "Voicitool: paket dubbing untuk Choicer Voicer",
    "ja": "Voicitool: Choicer Voicer 用の吹き替えパック",
    "zh": "Voicitool：Choicer Voicer 配音包",
    "ko": "Voicitool: Choicer Voicer용 더빙 팩",
    "hi": "Voicitool: Choicer Voicer के लिए डबिंग पैक",
    "cs": "Voicitool: dabingové balíčky pro Choicer Voicer",
    "sk": "Voicitool: dabingové balíčky pre Choicer Voicer",
    "sr": "Voicitool: paketi sinhronizacije za Choicer Voicer",
    "sv": "Voicitool: dubbningspaket för Choicer Voicer",
    "da": "Voicitool: dubbingpakker til Choicer Voicer",
    "ro": "Voicitool: pachete de dublaj pentru Choicer Voicer",
    "hu": "Voicitool: szinkroncsomagok a Choicer Voicerhez",
    "el": "Voicitool: πακέτα μεταγλώττισης για το Choicer Voicer",
    "vi": "Voicitool: gói lồng tiếng cho Choicer Voicer",
    "th": "Voicitool: แพ็กพากย์เสียงสำหรับ Choicer Voicer",
}
_SHORTCUT_PS = """
$d = $env:VT_DESC; $root = [IO.Path]::GetFullPath($env:VT_ROOT).TrimEnd('\')
$sh = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
  $p = Join-Path $dir 'Voicitool.lnk'
  if (-not (Test-Path $p)) { continue }
  $l = $sh.CreateShortcut($p)
  if (-not $l.WorkingDirectory) { continue }
  if ([IO.Path]::GetFullPath($l.WorkingDirectory).TrimEnd('\') -ne $root) { continue }
  if ($l.Description -ne $d) { $l.Description = $d; $l.Save() }
}
"""


def update_shortcuts(lang):
    """Verknüpfungen dieser Installation bekommen den Hinweistext in der gewählten Sprache (im Hintergrund)."""
    desc = SHORTCUT_DESC.get(lang or "")
    if not desc or sys.platform != "win32":
        return

    def work():
        try:
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _SHORTCUT_PS],
                           env={**os.environ, "VT_DESC": desc, "VT_ROOT": str(config.ROOT)},
                           capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            pass
    threading.Thread(target=work, daemon=True).start()
