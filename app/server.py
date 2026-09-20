"""Voicitool: lokaler Server (FastAPI) mit Job-Warteschlange."""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402  (setzt HF_HOME usw. vor allen ML-Imports)

import uvicorn  # noqa: E402
from fastapi import Body, FastAPI, File, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app.pipeline import media, project  # noqa: E402

app = FastAPI(title="Voicitool")


@app.on_event("startup")
def _background_maintenance():
    """Beim Start im Hintergrund: yt-dlp höchstens einmal am Tag aktualisieren (YouTube ändert sich ständig)."""
    def work():
        try:
            from app.pipeline import download
            download.update_ytdlp()
        except Exception:
            traceback.print_exc()
    threading.Thread(target=work, daemon=True).start()
    from app import system
    system.gpu_check()   # passt PyTorch zur Grafikkarte? (eigener Prozess, Ergebnis wird gemerkt)
    system.update_shortcuts(config.ui_lang())


@app.middleware("http")
async def no_cache_static(request, call_next):
    # Oberfläche nie aus dem Browser-Cache laden (sonst alte Version nach Updates)
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# ---------------------------------------------------------------- Jobs
class Cancelled(Exception):
    """Job wurde vom Nutzer abgebrochen."""


def _kill_tree(proc):
    # Worker samt Kindprozessen (ffmpeg) beenden
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


class JobRunner:
    """Drei Warteschlangen, die nebeneinander laufen: 'gpu' (KI, immer nacheinander), 'cpu' (Export) und 'net'
    (Downloads und Uploads: ein YouTube-Video lädt, während ein Export läuft)."""

    def __init__(self):
        self.queues = {"gpu": queue.Queue(), "cpu": queue.Queue(), "net": queue.Queue()}
        self.running = []
        self.pending = []
        self.finished = []  # letzte Ergebnisse
        self.lock = threading.Lock()
        self.counter = 0
        for lane in self.queues:
            threading.Thread(target=self._loop, args=(lane,), daemon=True).start()
        threading.Thread(target=self._watch, daemon=True).start()

    def submit(self, kind, pid, fn, label, lane="gpu"):
        with self.lock:
            self.counter += 1
            job = {"id": f"{int(time.time())}-{self.counter}", "kind": kind, "project": pid, "label": label,
                   "step": "In Warteschlange", "pct": 0.0, "message": "", "state": "wartet", "result": None,
                   "error": None, "error_code": None, "warning": None, "lane": lane, "started": None,
                   "_fn": fn, "_cancel": False, "_proc": None}
            self.pending.append(job)
        self.queues[lane].put(job)
        return job["id"]

    def _finish(self, job):
        with self.lock:
            if job in self.running:
                self.running.remove(job)
            if job in self.pending:
                self.pending.remove(job)
            self.finished.insert(0, job)
            del self.finished[20:]

    def _loop(self, lane):
        while True:
            job = self.queues[lane].get()
            with self.lock:
                if job["_cancel"]:  # schon vor dem Start abgebrochen
                    continue
                self.pending.remove(job)
                self.running.append(job)
            job["state"] = "läuft"
            job["started"] = job["_changed"] = time.time()
            job["_steps"] = []   # [Schritt, Startzeit] für die Zeitschätzung

            def report(step, pct, msg="", job=job):
                if job["_cancel"]:
                    raise Cancelled()
                if step != job["step"] or not job["_steps"]:
                    job["_steps"].append([step, time.time()])
                    job["step_started"] = time.time()
                if (step, round(float(pct), 3), msg) != (job["step"], round(job["pct"], 3), job["message"]):
                    job["_changed"] = time.time()
                    if job.get("warning") and job["warning"]["code"] != "vram_full":
                        job["warning"] = None   # geht wieder voran
                job["step"], job["pct"], job["message"] = step, float(pct), msg

            try:
                job["result"] = job["_fn"](job, report)
                if job["_cancel"]:
                    raise Cancelled()
                job["state"] = "fertig"
                job["pct"] = 1.0
            except Cancelled:
                job["state"] = "abgebrochen"
                job["step"] = "Abgebrochen"
                job["result"] = None
            except FileExistsError as e:
                job["state"] = "fehler"
                job["error"] = "EXISTS:" + str(e)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                job["state"] = "fehler"
                code, text = getattr(e, "code", None), None
                if not code:
                    from app import system
                    code, text = system.friendly_error(f"{type(e).__name__}: {e}")
                    if code == "net" and job["kind"] in ("download", "instrumental", "upload"):
                        text = "Keine Verbindung: Die Seite ist nicht erreichbar. Prüfe den Link und das Internet."
                job["error_code"] = code
                job["error"] = text or (str(e) if isinstance(e, WorkerError) else f"{type(e).__name__}: {e}")
                job["error_detail"] = getattr(e, "detail", None) or f"{type(e).__name__}: {e}"
            job["warning"] = None
            if job["kind"] == "process" and job["state"] in ("fehler", "abgebrochen"):
                _set_status(job["project"], job["state"], job["error"], job.get("error_code"), job.get("error_detail"))
            if job["kind"] == "process" and job["state"] == "fertig":
                _record_times(job)
            self._finish(job)

    def cancel(self, jid):
        with self.lock:
            job = next((j for j in self.running + self.pending if j["id"] == jid), None)
            if not job:
                return False
            job["_cancel"] = True
            waiting = job in self.pending
        if waiting:
            job["state"] = "abgebrochen"
            job["step"] = "Abgebrochen"
            if job["kind"] == "process":
                _set_status(job["project"], "abgebrochen", None)
            self._finish(job)
        elif job["_proc"] is not None:
            _kill_tree(job["_proc"])
        return True

    def busy(self, pid):
        with self.lock:
            return any(j["project"] == pid for j in self.running + self.pending)

    def gpu_busy(self):
        with self.lock:
            return any(j.get("lane") == "gpu" for j in self.running + self.pending)

    def _watch(self):
        """Hängt etwas? Alle 5 s laufende Aufträge prüfen und dann eine Warnung an den Auftrag hängen."""
        while True:
            time.sleep(5)
            with self.lock:
                running = list(self.running)
            for job in running:
                try:
                    _check_stall(job)
                except Exception:
                    traceback.print_exc()

    @staticmethod
    def public(job):
        return {k: v for k, v in job.items() if not k.startswith("_")} if job else None

    def state(self):
        with self.lock:
            running = [self.public(j) for j in self.running]
            return {
                "running": running,
                "current": running[0] if running else None,
                "pending": [self.public(j) for j in self.pending],
                "finished": [self.public(j) for j in self.finished[:10]],
            }


def _process_estimate(pid, dev=None):
    """Geschätzte Dauer einer Verarbeitung (für Fortschrittsbalken und Restzeit)."""
    from app import estimate
    data = project.load(pid)
    seconds = data.get("duration") or 0
    if not seconds:
        try:
            seconds = media.probe(project.project_dir(pid) / data["source"])["duration"] or 0
        except Exception:
            seconds = 0
    s = data.get("settings") or {}
    est = estimate.estimate(seconds, s.get("quality") or config.DEFAULT_QUALITY, s.get("laugh", True),
                            {"cpu": "cpu", "gpu": "cuda"}.get(dev))
    if s.get("online"):   # Online rechnen: Hochladen, Warteschlange und Rechenzeit beim Dienst (grob)
        from app.pipeline import online
        rd = online.ready(asr=s.get("online_asr"))
        est["steps"] = [[n, 300 + 0.3 * seconds if n == "Stimmen trennen" and rd["separate"] else
                         15 + 0.05 * seconds if n == "Sprache erkennen" and rd["transcribe"] else t]
                        for n, t in est["steps"]]
        est["total"] = round(sum(t for _, t in est["steps"]), 1)
        est["online"] = True
    est["seconds"] = seconds
    return est


def _record_times(job):
    """Echte Schrittzeiten speichern, damit die nächste Schätzung besser wird."""
    try:
        from app import estimate
        steps = job.get("_steps") or []
        ends = [t for _, t in steps[1:]] + [time.time()]
        times = {}
        for (step, start), end in zip(steps, ends):
            times[step] = times.get(step, 0.0) + (end - start)
        est = job.get("estimate") or {}
        if est.get("online"):
            return   # Online-Zeiten sagen nichts über diesen PC
        data = project.load(job["project"])
        s = data.get("settings") or {}
        estimate.record(est.get("seconds") or data.get("duration") or 0, s.get("quality") or config.DEFAULT_QUALITY,
                        s.get("laugh", True), times)
    except Exception:
        traceback.print_exc()


def _set_status(pid, status, error, code=None, detail=None):
    try:
        data = project.load(pid)
        data["status"] = status
        data["error"] = error
        data["error_code"] = code
        data["error_detail"] = detail if detail and detail != error else None
        project.save(pid, data)
    except Exception:
        pass


class WorkerError(RuntimeError):
    """Fehler aus dem Verarbeitungsprozess: verständlicher Text, Code (vram, ram, disk …) und Technik-Details."""
    def __init__(self, text, code=None, detail=None):
        super().__init__(text)
        self.code, self.detail = code, detail


# Zeitgrenzen ohne Fortschritt (s), ab denen ein Auftrag als hängend gilt
STALL_LIMIT = {"download": 90, "model": 120, "export": 600, "instrumental": 600, "recluster": 900, "retranscribe": 300}


def _check_stall(job):
    from app import system
    now = time.time()
    since = now - job.get("_changed", job["started"] or now)
    warn = None
    if job.get("_proc") is not None:   # KI-Verarbeitung im eigenen Prozess
        alive = job.get("_alive") or job["started"]
        # lagert Windows Grafikspeicher in den Arbeitsspeicher aus? (alle 45 s nachsehen)
        if job.get("device") != "cpu" and now - job.get("_spill_t", 0) > 45:
            job["_spill_t"] = now
            mem = system.gpu_process_memory(job["_proc"].pid)
            job["_spill_gb"] = mem[1] if mem else 0.0
        spill = job.get("_spill_gb") or 0.0
        if spill >= 0.5:
            warn = {"code": "vram_full", "text": f"Der Grafikspeicher ist voll: {system.fmt_gb(spill)} GB mussten in den Arbeitsspeicher "
                                                 "ausgelagert werden. Das macht alles sehr langsam. Schließe andere Programme "
                                                 "(z. B. Spiele) oder brich ab und rechne auf dem Prozessor."}
        elif now - alive > 60:
            warn = {"code": "frozen", "text": f"Die Verarbeitung reagiert seit {system.fmt_dur(now - alive)} nicht mehr."}
        elif (job.get("estimate") or {}).get("online") and job["step"] in ("Stimmen trennen", "Sprache erkennen"):
            warn = None   # Warteschlange beim Online-Dienst: dauert schwankend lange, der PC rechnet dabei nicht
        else:
            expected = dict((job.get("estimate") or {}).get("steps") or []).get(job["step"])
            in_step = now - (job.get("step_started") or job["started"])
            if expected and in_step > max(3 * expected, expected + 120) or not expected and since > 900:
                warn = {"code": "slow", "text": f"Der Schritt „{job['step']}“ dauert viel länger als erwartet "
                                                f"({system.fmt_dur(in_step)} statt etwa {system.fmt_dur(expected or 60)})."}
                if job.get("device") != "cpu" and system.vram_full():
                    warn = {"code": "vram_full", "text": "Der Grafikspeicher ist voll. Windows lagert dann in den Arbeitsspeicher aus, "
                                                         "das macht alles extrem langsam. Schließe andere Programme (z. B. Spiele) "
                                                         "oder brich ab und rechne auf dem Prozessor."}
    else:
        limit = STALL_LIMIT.get(job["kind"], 900)
        if since > limit:
            text = (f"Der Download kommt seit {system.fmt_dur(since)} nicht voran. Ist das Internet verbunden?"
                    if job["kind"] in ("download", "model") else f"Seit {system.fmt_dur(since)} gibt es keinen Fortschritt.")
            warn = {"code": "stuck", "text": text}
    if warn:
        warn["since"] = round(since)
    job["warning"] = warn


def _unload_gpu_models():
    # Im Server geladenes Whisper (von „Text neu erkennen“) freigeben, bevor der Worker startet
    if "app.pipeline.transcribe" in sys.modules:
        sys.modules["app.pipeline.transcribe"].unload()
    if "torch" in sys.modules:
        try:
            sys.modules["torch"].cuda.empty_cache()
        except Exception:
            pass


def _log_worker_errors(proc, kind, pid):
    """Fehlerausgabe des Workers in daten/logs/worker.log schreiben und die letzten Zeilen merken."""
    tail = []
    log_path = config.DATA_DIR / "logs" / "worker.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def drain():
        with open(log_path, "a", encoding="utf8", buffering=1) as f:
            f.write(f"\n===== {kind} · {pid} · {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            for raw in proc.stderr:
                line = raw.decode("utf8", "replace").rstrip()
                f.write(line + "\n")
                tail.append(line)
                del tail[:-40]

    threading.Thread(target=drain, daemon=True).start()
    return tail


def _job_device(pid):
    from app import system
    try:
        settings = project.load(pid).get("settings") or {}
    except Exception:
        return system.device()
    dev = system.device(settings)
    if dev == "gpu" and settings.get("online"):
        # Online rechnen: Sprecher und Lachen laufen lokal und brauchen zusammen etwa 1 GB Grafikspeicher.
        # Nur wenn der knapp wird (z. B. ein Spiel läuft), auf den Prozessor ausweichen. Dort ist die
        # Lachererkennung rund 20 Mal langsamer, deshalb erst bei wirklich vollem Speicher.
        gpu = system.nvidia()
        if gpu and gpu.get("vram_free") is not None and gpu["vram_free"] < 1.5:
            dev = "cpu"
    return dev


def run_worker(kind, pid):
    """Job-Funktion: KI-Verarbeitung im eigenen Prozess (sofort abbrechbar)."""
    def fn(job, report):
        from app import system
        dev = _job_device(pid)
        job["device"] = dev
        if kind == "process":
            try:
                job["estimate"] = _process_estimate(pid, dev)
            except Exception:
                traceback.print_exc()
        _unload_gpu_models()
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        if dev == "cpu":
            env["CUDA_VISIBLE_DEVICES"] = "-1"   # PyTorch und CTranslate2 sehen dann keine Grafikkarte (leer wirkt unter Windows nicht)
        proc = subprocess.Popen(
            [sys.executable, str(config.APP_DIR / "worker.py"), kind, pid, str(os.getpid())],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(config.ROOT), env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        tail = _log_worker_errors(proc, kind, pid)
        job["_proc"] = proc
        if job["_cancel"]:
            _kill_tree(proc)
        error, error_code, result = None, None, {"ok": True}
        job["_alive"] = time.time()
        for raw in proc.stdout:
            line = raw.decode("utf8", "replace").rstrip("\r\n")
            if not line.startswith("@@VT@@"):
                print(line)
                continue
            msg = json.loads(line[6:])
            job["_alive"] = time.time()
            if job.get("warning") and job["warning"]["code"] == "frozen":
                job["warning"] = None
            if msg["type"] == "progress" and not job["_cancel"]:
                report(msg["step"], msg["pct"], msg.get("message", ""))
            elif msg["type"] == "error":
                error = msg["error"]
                error_code = msg.get("code")
            elif msg["type"] == "done" and msg.get("result") is not None:
                result = msg["result"]
        proc.wait()
        job["_proc"] = None
        if job["_cancel"]:
            raise Cancelled()
        if proc.returncode != 0:
            detail = error or "\n".join(tail[-12:]).strip() or f"Code {proc.returncode}"
            if detail.startswith("OnlineError: "):   # Meldungen von Online rechnen sind schon verständlich
                raise WorkerError(detail.split(": ", 1)[1], error_code or "online", detail)
            code, text = system.friendly_error(detail + "\n" + "\n".join(tail[-30:]), proc.returncode)
            if text:
                raise WorkerError(text, code, detail)
            # eigene Meldungen (z. B. „Das Video hat keine Tonspur.“) ohne technischen Vorsatz zeigen
            plain = detail.split(": ", 1)[1] if detail.startswith(("RuntimeError: ", "ValueError: ")) else detail
            raise WorkerError(f"Verarbeitung abgebrochen: {plain}\n(Einzelheiten: daten/logs/worker.log)", None, detail)
        return result
    return fn


jobs = JobRunner()


def _name(pid):
    try:
        return project.load(pid)["name"]
    except Exception:
        return pid


# ---------------------------------------------------------------- API
_duration_cache = {}


@app.get("/api/estimate")
def get_estimate(file: str, laugh: bool = True):
    """Geschätzte Verarbeitungsdauer eines Videos im Eingang, für jede Qualitätsstufe."""
    from app import estimate
    src = project.inbox_file(file)
    key = (src.name, src.stat().st_mtime)
    if key not in _duration_cache:
        _duration_cache[key] = media.probe(src)["duration"] or 0
    seconds = _duration_cache[key]
    levels = {q: estimate.estimate(seconds, q, laugh)["total"] for q in config.QUALITY}
    any_est = estimate.estimate(seconds, config.DEFAULT_QUALITY, laugh)
    return {"seconds": seconds, "levels": levels, "device": any_est["device"], "learned_runs": any_est["learned_runs"]}


@app.get("/api/state")
def state():
    inbox = sorted(p.name for p in config.INBOX_DIR.iterdir()
                   if p.is_file() and p.suffix.lower() in config.VIDEO_EXTS)
    from app import models
    from app.pipeline import textsources
    return {"version": config.APP_VERSION, "build": config.APP_BUILD, "repo": config.UPDATE_REPO,
            "inbox_subs": textsources.files_with_reftext(inbox),
            "whisper_installed": models.whisper_installed(), "settings": models.settings(),
            "projects": project.list_projects(), "categories": project.categories(), "order": project.project_order(), "inbox": inbox, "jobs": jobs.state(),
            "quality": {k: v["label"] for k, v in config.QUALITY.items()}, "default_quality": config.DEFAULT_QUALITY,
            "quality_info": {k: {"whisper": v["whisper"], "beam": v["beam"], "overlap": v["overlap"],
                                 "speaker2": v["speaker2"]} for k, v in config.QUALITY.items()},
            "game_dir": str(config.game_packs_dir()), "game_dir_exists": config.game_packs_dir().exists()}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    name = Path(file.filename or "video.mp4").name
    if Path(name).suffix.lower() not in config.VIDEO_EXTS:
        raise HTTPException(400, "Kein unterstütztes Videoformat")
    target = config.INBOX_DIR / name
    i = 2
    while target.exists():
        target = config.INBOX_DIR / f"{Path(name).stem} ({i}){Path(name).suffix}"
        i += 1
    with open(target, "wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)
    return {"filename": target.name}


@app.post("/api/download")
def download_video(body: dict = Body(...)):
    """Video von einer Web-Adresse (YouTube u. a.) in den Eingang laden."""
    from app.pipeline import download as dl

    url = (body.get("url") or "").strip()
    if not dl.valid_url(url):
        raise HTTPException(400, "Bitte eine Adresse einfügen, die mit http:// oder https:// beginnt.")

    want_subs = bool(body.get("subs"))

    def run(job, report):
        result = dl.download(url, lambda p, msg="": report("Video laden", p, msg))
        if want_subs:   # Haken „Untertitel mitholen“: gleich als „Text vorgeben“ an das Video hängen
            from app.pipeline import textsources
            report("Video laden", 1.0, "Untertitel holen")
            try:
                subs = textsources.uploader_subs(url)
            except Exception:
                traceback.print_exc()
                subs = None
            if subs:
                textsources.remember_reftext(result["filename"], subs["text"], subs["source"])
                result["subs"] = {"source": subs["source"], "lines": len([x for x in subs["text"].splitlines() if x.strip()])}
            else:
                result["subs"] = None
        return result

    return {"job": jobs.submit("download", "", run, "Video laden", lane="net")}


@app.get("/api/textsources/inbox")
def textsources_inbox(file: str):
    """Beim Herunterladen mitgeholte Untertitel einer Datei im Eingang."""
    from app.pipeline import textsources
    ref = (textsources.source_info(file) or {}).get("reftext")
    if not ref:
        raise HTTPException(404, "Keine Untertitel gespeichert")
    return ref


@app.delete("/api/inbox/{filename}")
def delete_inbox(filename: str):
    """Video aus dem Eingang in den Papierkorb verschieben."""
    from send2trash import send2trash
    try:
        src = project.inbox_file(filename)
    except FileNotFoundError:
        raise HTTPException(404, "Datei nicht im Eingang gefunden")
    try:
        send2trash(str(src))
    except OSError as e:
        raise HTTPException(409, f"Löschen nicht möglich (Datei in Benutzung?): {e}")
    return {"ok": True}


def _int_or_none(v):
    return int(v) if v not in (None, "", 0, "0") else None


@app.get("/api/system")
def get_system(recheck: bool = False):
    """Hardware und allgemeine Hinweise (Einstellungen → System, Hinweis beim Start)."""
    from app import system
    if recheck:
        system.GPU_CHECK_FILE.unlink(missing_ok=True)
        system._gpu_check.update(state="offen", result=None)
        system.gpu_check(wait=True)
    inf = system.info()
    return {"info": inf, "health": system.health(inf)}


@app.post("/api/preflight")
def preflight(body: dict = Body(...)):
    """Vorher prüfen, ob etwas auf diesem PC klappt. Ergebnis: Hinweise mit Stufe info/warn/block."""
    from app import models, system
    kind = body.get("kind")
    if kind == "process":
        seconds, has_audio, settings = 0.0, True, {}
        try:
            if body.get("pid"):
                data = project.load(body["pid"])
                settings = data.get("settings") or {}
                seconds = data.get("duration") or 0
                src = project.project_dir(body["pid"]) / data["source"]
            else:
                src = project.inbox_file(body["file"])
            key = (str(src), src.stat().st_mtime)
            if key not in _duration_cache:
                _duration_cache[key] = media.probe(src)
            info = _duration_cache[key]
            if isinstance(info, dict):
                seconds = info.get("duration") or seconds
                has_audio = bool(info.get("audio_codec"))
            else:
                seconds = info or seconds
        except Exception:
            traceback.print_exc()
        dev = "cpu" if body.get("device") == "cpu" else system.device(
            {k: v for k, v in settings.items() if k != "device"} if body.get("device") == "auto" else settings)
        quality = body.get("quality") or settings.get("quality") or config.DEFAULT_QUALITY
        laugh = body.get("laugh", settings.get("laugh", True))
        language = body.get("language") or settings.get("language") or "auto"
        return system.preflight_process(seconds, quality, laugh, language, has_audio, dev, jobs.gpu_busy())
    if kind == "export":
        data = project.load(body["pid"])
        return system.preflight_export(data.get("duration") or 0, bool(body.get("install")))
    if kind == "category":
        total = sum(p.get("duration") or 0 for p in project.category_projects(body.get("cid")) if p["status"] == "fertig")
        return system.preflight_export(total * 1.5, bool(body.get("install")))   # Einzel-Packs plus Sammel-ZIP
    if kind == "model":
        m = models.BY_ID.get(body.get("mid"))
        return system.preflight_download(m["mb"] if m else None)
    if kind == "download":
        return system.preflight_download(None)
    raise HTTPException(400, "Unbekannter Bereich")


@app.get("/api/models")
def get_models():
    from app import models
    return {"models": models.status(), "settings": models.settings(), "whisper_installed": models.whisper_installed()}


@app.post("/api/models/{mid}/download")
def download_model(mid: str):
    from app import models
    if mid not in models.BY_ID:
        raise HTTPException(404, "Unbekanntes Modell")
    name = models.BY_ID[mid]["name"]

    def run(job, report):
        models.download(mid, lambda p, msg="": report("Modell laden", p, msg))
        return {"id": mid}

    return {"job": jobs.submit("model", "", run, f"Laden: {name}", lane="net")}


@app.delete("/api/models/{mid}")
def delete_model(mid: str):
    from app import models
    if mid not in models.BY_ID:
        raise HTTPException(404, "Unbekanntes Modell")
    try:
        models.delete(mid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


def _launcher(arg):
    exe = config.ROOT / "Voicitool.exe"
    if not exe.exists():
        raise HTTPException(404, "Voicitool.exe fehlt im Programmordner. Bitte Voicitool neu herunterladen.")
    subprocess.Popen([str(exe), arg], cwd=str(config.ROOT), close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))


@app.post("/api/system/setup")
def system_setup():
    """Einrichtungs-Assistent öffnen (Installation prüfen und reparieren)."""
    _launcher("--setup")
    return {"ok": True}


@app.post("/api/system/uninstall")
def system_uninstall():
    """Deinstallations-Assistent öffnen (er schließt Voicitool selbst)."""
    _launcher("--uninstall")
    return {"ok": True}


SETTING_KEYS = {
    # Darstellung
    "theme", "accent", "custom_color", "reduce_motion", "ui_scale", "compute_device", "export_line_format",
    # Neue Projekte
    "default_quality", "default_language", "default_laugh",
    # Editor
    "snap", "follow", "auto_text", "playback_rate", "confirm_delete",
    # Export
    "export_video_height", "export_video_fps", "export_video_quality", "export_normalize", "export_image_mode",
    "export_keep_voices", "game_dir",
    # KI und Updates
    "whisper_model", "check_updates", "ui_lang", "usage_stats",
}


def _online_times():
    """Geschätzte Dauer für einen 3-Minuten-Clip: auf diesem PC und mit Online rechnen (für die Einrichtung)."""
    from app import estimate, system
    dev = system.device()
    est = estimate.estimate(180, config.DEFAULT_QUALITY, True, {"cpu": "cpu", "gpu": "cuda"}.get(dev))
    local = sum(t for _, t in est["steps"])
    online = sum(354 if n == "Stimmen trennen" else 24 if n == "Sprache erkennen" else t for n, t in est["steps"])
    return {"device": dev, "weak": dev == "cpu", "local_3min": round(local), "online_3min": round(online)}


@app.get("/api/online")
def online_status():
    """Online rechnen: was eingerichtet ist (Schlüssel nur gekürzt) und wie schnell dieser PC ist."""
    from app.pipeline import online
    out = online.status()
    try:
        out.update(_online_times())
    except Exception:
        traceback.print_exc()
    return out


@app.put("/api/online")
def online_save(body: dict = Body(...)):
    from app.pipeline import online
    online.save(body)
    return online_status()


@app.post("/api/online/check")
def online_check(body: dict = Body(...)):
    """Schlüssel eines Dienstes prüfen (kleine Anfrage, verbraucht kein Kontingent)."""
    from app.pipeline import online
    service = body.get("service")
    if service not in online.FIELDS:
        raise HTTPException(400, "Unbekannter Dienst")
    return online.check(service)


@app.get("/api/settings")
def get_settings():
    return {"settings": config.user_settings(), "default_game_dir": str(config.GAME_PACKS_DIR),
            "stats_site": config.STATS_SITE, "build": config.APP_BUILD}


@app.put("/api/settings")
def put_settings(body: dict = Body(...)):
    from app import models
    values = {k: v for k, v in body.items() if k in SETTING_KEYS}
    if "game_dir" in values and values["game_dir"]:
        p = Path(values["game_dir"]).expanduser()
        if not p.is_absolute():
            raise HTTPException(400, "Bitte einen vollständigen Ordnerpfad angeben.")
        values["game_dir"] = str(p)
    if "ui_lang" in values:
        from app import system
        system.update_shortcuts(values["ui_lang"])   # Hinweistext der Desktop-Verknüpfung mitziehen
    return models.save_settings(values)


def _dir_size(p):
    total = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _cache_files():
    """Zwischendateien, die Voicitool bei Bedarf neu erzeugt (Export-Videos, Download-Reste)."""
    files = list(config.PROJECTS_DIR.glob("*/dub_video_*.ogv"))
    dl = config.DATA_DIR / "download"
    if dl.exists():
        files += [f for f in dl.rglob("*") if f.is_file()]
    return files


@app.get("/api/storage")
def get_storage():
    """Speicherbedarf je Bereich (MB)."""
    mb = lambda b: round(b / 1e6)  # noqa: E731
    return {
        "projects": mb(_dir_size(config.PROJECTS_DIR)), "export": mb(_dir_size(config.EXPORT_DIR)),
        "inbox": mb(_dir_size(config.INBOX_DIR)), "models": mb(_dir_size(config.MODELS_DIR)),
        "cache": mb(sum(f.stat().st_size for f in _cache_files() if f.exists())),
        "packages": mb(_dir_size(config.TOOLS_DIR / "uv-cache")),
        "root": str(config.ROOT),
    }


@app.post("/api/storage/clear")
def clear_storage(body: dict = Body(default={})):
    """Zwischenspeicher leeren. what: 'cache' (Export-Videos, Download-Reste) oder 'packages' (Paket-Downloads)."""
    what = body.get("what", "cache")
    freed = 0
    if what == "cache":
        for f in _cache_files():
            try:
                freed += f.stat().st_size
                f.unlink()
            except OSError:
                pass
    elif what == "packages":
        d = config.TOOLS_DIR / "uv-cache"
        freed = _dir_size(d)
        shutil.rmtree(d, ignore_errors=True)
    else:
        raise HTTPException(400, "Unbekannter Bereich")
    return {"freed_mb": round(freed / 1e6)}


@app.post("/api/open-folder")
def open_named_folder(body: dict = Body(...)):
    """Einen der Voicitool-Ordner im Explorer öffnen."""
    folders = {"projects": config.PROJECTS_DIR, "export": config.EXPORT_DIR, "inbox": config.INBOX_DIR,
               "models": config.MODELS_DIR, "logs": config.DATA_DIR / "logs", "root": config.ROOT,
               "game": config.game_packs_dir()}
    target = folders.get(body.get("which"))
    if target is None:
        raise HTTPException(400, "Unbekannter Ordner")
    target.mkdir(parents=True, exist_ok=True)
    os.startfile(target)
    return {"ok": True}


QUIT_HOOK = None   # setzt das Fenster (desktop.py): beendet Voicitool sauber, z. B. für ein Update


def _changelog(text):
    """Einträge aus changelog.json: [{build, date, changes: [...]}], neueste zuerst."""
    try:
        return [e for e in json.loads(text) if isinstance(e, dict) and isinstance(e.get("build"), int)]
    except Exception:
        return []


@app.get("/api/changelog")
def changelog():
    """Was ist neu (mitgelieferte Liste)."""
    p = config.APP_DIR / "changelog.json"
    return {"current": config.APP_BUILD, "entries": _changelog(p.read_text(encoding="utf8")) if p.exists() else []}


@app.get("/api/update-check")
def update_check():
    """Gibt es auf GitHub eine neuere Build-Nummer? Mit den Änderungen seit der installierten Version."""
    can_apply = QUIT_HOOK is not None and (config.ROOT / "Voicitool.exe").exists()
    if not config.UPDATE_REPO:
        return {"configured": False, "current": config.APP_BUILD}
    import urllib.request
    base = f"https://raw.githubusercontent.com/{config.UPDATE_REPO}/main/app"
    try:
        with urllib.request.urlopen(f"{base}/version.json", timeout=8) as r:
            remote = json.loads(r.read().decode("utf8"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"GitHub nicht erreichbar: {e}")
    latest = int(remote.get("build", 0))
    changes = []
    if latest > config.APP_BUILD:
        try:
            with urllib.request.urlopen(f"{base}/changelog.json", timeout=8) as r:
                changes = [e for e in _changelog(r.read().decode("utf8")) if e["build"] > config.APP_BUILD]
        except Exception:  # noqa: BLE001
            pass   # ohne Liste geht das Update trotzdem
    return {"configured": True, "current": config.APP_BUILD, "latest": latest, "latest_version": remote.get("version"),
            "update_available": latest > config.APP_BUILD, "changes": changes, "can_apply": can_apply}


@app.post("/api/update/apply")
def update_apply(body: dict = Body(default={})):
    """Voicitool beenden und über Voicitool.exe aktualisieren. Die exe wartet, bis das Fenster zu ist,
    holt das Update, prüft die Installation und startet Voicitool neu."""
    exe = config.ROOT / "Voicitool.exe"
    if QUIT_HOOK is None or not exe.exists():
        raise HTTPException(400, "Bitte Voicitool neu starten, dann wird das Update installiert.")
    st = jobs.state()
    busy = st["running"] + st["pending"]
    if busy and not body.get("force"):
        raise HTTPException(409, ", ".join(j["label"] for j in busy[:3]))
    try:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)   # Startfenster der exe darf nach vorne
    except Exception:  # noqa: BLE001
        pass
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(exe), "--after-exit"], cwd=str(config.ROOT), creationflags=flags, close_fds=True)
    threading.Timer(0.6, QUIT_HOOK).start()   # erst antworten, dann schließen
    return {"ok": True}


@app.post("/api/projects")
def create_project(body: dict = Body(...)):
    from app import models
    from app.pipeline import online
    try:   # fehlt das Sprachpaket, gleich hier melden statt erst nach der Stimmen-Trennung
        if not (body.get("online") and online.ready(asr=body.get("online_asr"))["transcribe"]):   # online erkannt: kein Sprachpaket nötig
            models.pick_whisper(body.get("quality"), body.get("language", "auto"))
    except models.MissingLanguagePack as e:
        raise HTTPException(400, str(e))
    try:
        pid = project.create(body["filename"], body.get("name"), body.get("language", "auto"),
                             _int_or_none(body.get("speakers")), body.get("quality"), body.get("laugh", True),
                             body.get("ui_lang"), body.get("category"),
                             reftext=body.get("reftext") if isinstance(body.get("reftext"), dict) else None)
    except FileNotFoundError:
        raise HTTPException(404, "Datei nicht im Eingang gefunden")
    if body.get("device") == "cpu":
        project.set_settings(pid, device="cpu")
    if body.get("online"):
        project.set_settings(pid, online=True)
        if body.get("online_asr") in online.ASR + ("local",):
            project.set_settings(pid, online_asr=body["online_asr"])
    jobs.submit("process", pid, run_worker("process", pid), f"Verarbeiten: {_name(pid)}")
    return {"id": pid}


@app.post("/api/projects/{pid}/reprocess")
def reprocess(pid: str, body: dict = Body(default={})):
    project.project_dir(pid)
    if jobs.busy(pid):
        raise HTTPException(409, "Projekt wird gerade verarbeitet")
    q = body.get("quality")
    project.set_settings(pid, quality=q if q in config.QUALITY else None)
    if body.get("device") in ("cpu", "auto"):
        project.set_settings(pid, device=body["device"])
    if "online" in body:
        project.set_settings(pid, online=bool(body["online"]))
    from app.pipeline import online
    if body.get("online_asr") in online.ASR + ("local",):
        project.set_settings(pid, online_asr=body["online_asr"])
    lang = str(body.get("language") or "")
    if lang in ("auto", "mixed") or (2 <= len(lang) <= 3 and lang.isalpha()):
        project.set_settings(pid, language=lang)   # z. B. nach unsicher erkannter Sprache
    _set_status(pid, "wartet", None)
    jobs.submit("process", pid, run_worker("process", pid), f"Verarbeiten: {_name(pid)}")
    return {"ok": True}


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    try:
        return project.load(pid)
    except FileNotFoundError:
        raise HTTPException(404, "Projekt nicht gefunden")


@app.put("/api/projects/order")
def set_project_order(body: dict = Body(...)):
    project.set_project_order(body.get("ids") or [])
    return {"ok": True}


@app.put("/api/projects/{pid}")
def put_project(pid: str, body: dict = Body(...)):
    current = project.load(pid)
    for key in ("characters", "lines", "pack", "export", "credits"):
        if key in body:
            current[key] = body[key]
    if "cuts" in body:   # rausgeschnittene Stellen (Video schneiden im Editor)
        current["cuts"] = project.normalize_cuts(body["cuts"], current.get("duration"))
    project.save(pid, current)
    return {"ok": True, "saved": time.strftime("%H:%M:%S")}


@app.post("/api/projects/{pid}/rename")
def rename_project(pid: str, body: dict = Body(...)):
    try:
        # Ordner nur umbenennen, wenn gerade kein Job darauf zugreift
        new_id = project.rename(pid, body.get("name"), allow_folder=not jobs.busy(pid))
        return {"id": new_id, "name": project.load(new_id)["name"]}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    if jobs.busy(pid):
        raise HTTPException(409, "Projekt wird gerade verarbeitet. Bitte erst abbrechen.")
    project.delete(pid)
    return {"ok": True}


@app.get("/api/projects/{pid}/media/{name}")
def media_file(pid: str, name: str):
    d = project.project_dir(pid)
    f = (d / name).resolve()
    if f.parent != d:
        raise HTTPException(403)
    wav = f.with_suffix(".wav")
    if not f.exists() and f.name in ("instrumental.ogg", "hintergrund.ogg") and wav.exists():
        media.encode_opus(wav, f)   # ältere Projekte: Hörfassung für den Editor nachträglich anlegen
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f)


@app.get("/api/projects/{pid}/bilder/{name}")
def image_file(pid: str, name: str):
    d = project.project_dir(pid) / "bilder"
    f = (d / name).resolve()
    if f.parent != d.resolve() or not f.exists():
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "no-cache"})


@app.post("/api/projects/{pid}/frame")
def grab_frame(pid: str, body: dict = Body(...)):
    from app.pipeline import media
    d = project.project_dir(pid)
    data = project.load(pid)
    (d / "bilder").mkdir(exist_ok=True)
    name = f"{body.get('target', 'bild')}_{int(time.time() * 1000)}.jpg"
    media.grab_frame(d / data["source"], float(body["time"]), d / "bilder" / name, width=640)
    return {"image": name}


@app.post("/api/projects/{pid}/instrumental")
async def upload_instrumental(pid: str, file: UploadFile = File(...)):
    """Eigene Instrumental-Version hochladen und automatisch auf das Video ausrichten."""
    d = project.project_dir(pid)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in config.AUDIO_EXTS | config.VIDEO_EXTS:
        raise HTTPException(400, "Bitte eine Audio- oder Videodatei wählen (MP3, WAV, FLAC, M4A, MP4 …)")
    for old in d.glob("instrumental_quelle.*"):
        old.unlink()
    target = d / f"instrumental_quelle{ext}"
    with open(target, "wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)

    def run(job, report):
        return _align_instrumental(pid, target, report)

    return {"job": jobs.submit("instrumental", pid, run, f"Instrumental angleichen: {_name(pid)}", lane="cpu")}


@app.post("/api/projects/{pid}/instrumental_url")
def instrumental_from_url(pid: str, body: dict = Body(...)):
    """Instrumental direkt von einer Web-Adresse (YouTube u. a.) laden und ausrichten."""
    from app.pipeline import download
    url = (body.get("url") or "").strip()
    if not download.valid_url(url):
        raise HTTPException(400, "Bitte eine Adresse einfügen, die mit http:// oder https:// beginnt.")
    d = project.project_dir(pid)

    def run(job, report):
        target, title = download.download_audio(url, d, lambda p, msg="": report("Instrumental laden", p, msg))
        info = _align_instrumental(pid, target, report)
        if title:
            info["datei"] = title
            data = project.load(pid)
            data["instrumental"] = info
            project.save(pid, data)
        return info

    return {"job": jobs.submit("instrumental", pid, run, f"Instrumental laden: {_name(pid)}", lane="net")}


def _align_instrumental(pid, target, report):
    from app.pipeline import instrumental
    d = project.project_dir(pid)
    info = instrumental.align(d, target, lambda p, msg="": report("Instrumental angleichen", p, msg))
    data = project.load(pid)
    data["instrumental"] = info
    data["export"]["backing_source"] = "eigene"
    if info.get("stimmen_moeglich"):
        data["export"]["clip_source"] = "differenz"
    project.save(pid, data)
    return info


@app.get("/api/textsources/search")
def textsources_search(q: str = "", file: str = "", lang: str = ""):
    """„Text vorgeben“: Liedtexte, Transkripte und Untertitel in allen Quellen suchen."""
    from app.pipeline import textsources
    return textsources.search(q[:200], file or None, lang or None)


@app.get("/api/textsources/fetch")
def textsources_fetch(source: str, id: str):
    from app.pipeline import textsources
    try:
        return {"text": textsources.fetch(source, id)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Text konnte nicht geladen werden: {e}")


@app.get("/api/textsources/suggest")
def textsources_suggest(file: str):
    """Suchvorschlag für eine Datei im Eingang: Titel vom Download, sonst der Dateiname ohne Zusätze."""
    from app.pipeline import textsources
    info = textsources.source_info(file) or {}
    title = info.get("title") or Path(file).stem
    title = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", title)   # „[Official Video]“, „(Lyrics)“ usw. stören die Suche
    return {"query": re.sub(r"[_\s]+", " ", title).strip(), "youtube": bool(info.get("url"))}


@app.get("/api/projects/{pid}/instrumental/waves")
def instrumental_waves(pid: str):
    """Hüllkurven von KI-Hintergrund und eigenem Instrumental zum Ausrichten von Hand."""
    from app.pipeline import instrumental
    try:
        return instrumental.waves(project.project_dir(pid))
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@app.get("/api/projects/{pid}/instrumental/preview/{which}")
def instrumental_preview(pid: str, which: str):
    """Hörfassung zum Ausrichten von Hand (mono WAV, springt genau): ref = KI-Hintergrund, own = eigenes Instrumental."""
    from app.pipeline import instrumental
    if which not in ("ref", "own"):
        raise HTTPException(404)
    try:
        return FileResponse(instrumental.preview(project.project_dir(pid), which), headers={"Cache-Control": "no-cache"})
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@app.post("/api/projects/{pid}/instrumental/manual")
def instrumental_manual(pid: str, body: dict = Body(...)):
    """Versatz von Hand übernehmen: Instrumental neu anlegen und als Hintergrund nutzen."""
    from app.pipeline import instrumental
    try:
        offset = float(body.get("offset", 0.0))
    except (TypeError, ValueError):
        raise HTTPException(400, "Ungültiger Versatz")
    if abs(offset) > 3600:
        raise HTTPException(400, "Ungültiger Versatz")
    try:
        info = instrumental.manual(project.project_dir(pid), offset)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    data = project.load(pid)
    data["instrumental"] = info
    data["export"]["backing_source"] = "eigene"
    if data["export"].get("clip_source") == "differenz":
        data["export"]["clip_source"] = "stimmen"
    project.save(pid, data)
    return info


@app.delete("/api/projects/{pid}/instrumental")
def delete_instrumental(pid: str):
    from app.pipeline import instrumental
    instrumental.remove(project.project_dir(pid))
    data = project.load(pid)
    data.pop("instrumental", None)
    data["export"]["backing_source"] = "auto"
    if data["export"].get("clip_source") == "differenz":
        data["export"]["clip_source"] = "stimmen"
    project.save(pid, data)
    return {"ok": True}


# ---------------------------------------------------------------- Kategorien
@app.post("/api/categories")
def create_category(body: dict = Body(...)):
    try:
        return {"id": project.create_category(body.get("name"))}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.put("/api/categories/order")
def reorder_categories(body: dict = Body(...)):
    project.reorder_categories(body.get("ids") or [])
    return {"ok": True}


@app.put("/api/categories/{cid}")
def update_category(cid: str, body: dict = Body(...)):
    try:
        project.update_category(cid, body.get("name"), body.get("collapsed"))
    except KeyError:
        raise HTTPException(404, "Kategorie nicht gefunden.")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/categories/{cid}")
def delete_category(cid: str):
    project.delete_category(cid)
    return {"ok": True}


@app.post("/api/projects/{pid}/category")
def set_project_category(pid: str, body: dict = Body(...)):
    try:
        project.set_category(pid, body.get("category"))
    except KeyError:
        raise HTTPException(404, "Kategorie nicht gefunden.")
    except FileNotFoundError:
        raise HTTPException(404, "Projekt nicht gefunden")
    return {"ok": True}


@app.post("/api/categories/{cid}/export")
def export_category(cid: str, body: dict = Body(default={})):
    """Alle fertigen Projekte der Kategorie als Packs exportieren und in eine ZIP packen (zum Teilen)."""
    from app.pipeline import export as exporter
    cat = next((c for c in project.categories() if c["id"] == cid), None)
    if cat is None:
        raise HTTPException(404, "Kategorie nicht gefunden.")
    install, overwrite, only = bool(body.get("install")), bool(body.get("overwrite")), body.get("only")
    jid = jobs.submit("export", "", lambda job, report: exporter.export_category(cid, report, install, overwrite, only),
                      f"Kategorie: {cat['name']}", lane="cpu")
    return {"job": jid}


@app.get("/api/projects/{pid}/voices")
def voices(pid: str):
    """Ähnlichkeit der Stimmen je Charakter (für Vorschläge beim Zusammenführen)."""
    project.project_dir(pid)
    return project.voice_similarity(pid)


@app.post("/api/projects/{pid}/recluster")
def recluster(pid: str, body: dict = Body(...)):
    n = _int_or_none(body.get("speakers"))

    def run(job, report):
        report("Sprecher neu zuordnen", 0.3, "Clustern")
        project.recluster(pid, n)
        return {"ok": True}

    return {"job": jobs.submit("recluster", pid, run, f"Sprecher neu: {_name(pid)}")}


@app.post("/api/projects/{pid}/resegment")
def resegment(pid: str, body: dict = Body(...)):
    target = float(body.get("target_len", 6.0))
    pause = float(body.get("pause_split", 0.8))
    return project.resegment(pid, target, pause)


@app.post("/api/projects/{pid}/repeats")
def find_repeats(pid: str):
    count = project.find_repeats(pid)
    return {"count": count, "project": project.load(pid)}


@app.post("/api/projects/{pid}/retranscribe")
def retranscribe(pid: str, body: dict = Body(...)):
    start, end = float(body["start"]), float(body["end"])
    lang = body.get("language")

    def run(job, report):
        report("Text neu erkennen", 0.2, "Whisper")
        text = project.retranscribe_line(pid, start, end, lang)
        report("Text neu erkennen", 1.0, "")
        return {"text": text, "line": body.get("line")}

    return {"job": jobs.submit("retranscribe", pid, run, f"Text neu erkennen: {_name(pid)}")}


@app.post("/api/projects/{pid}/laughs")
def find_laughs(pid: str):
    project.project_dir(pid)
    return {"job": jobs.submit("laughs", pid, run_worker("laughs", pid), f"Lachen suchen: {_name(pid)}")}


@app.post("/api/projects/{pid}/export")
def export(pid: str, body: dict = Body(default={})):
    from app.pipeline import export as exporter
    install = bool(body.get("install"))
    overwrite = bool(body.get("overwrite"))
    jid = jobs.submit("export", pid, lambda job, report: exporter.export_pack(pid, report, install, overwrite),
                      f"Export: {_name(pid)}", lane="cpu")
    return {"job": jid}


@app.get("/api/jobs/{jid}")
def job(jid: str):
    st = jobs.state()
    for j in st["running"] + st["pending"] + st["finished"]:
        if j and j["id"] == jid:
            return j
    raise HTTPException(404)


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str):
    if not jobs.cancel(jid):
        raise HTTPException(404, "Job läuft nicht mehr")
    return {"ok": True}


@app.get("/api/share-link")
def share_link_existing(path: str):
    """Noch gültiger Download-Link für diese ZIP (sonst null)."""
    from app import share
    try:
        return {"link": share.existing(path)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/share-link")
def share_link(body: dict = Body(...)):
    """ZIP aus dem Export-Ordner hochladen (Litterbox), Ergebnis: Link und Ablaufzeit."""
    from app import share
    path = str(body.get("path") or "")
    try:
        share._zip_path(path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"job": jobs.submit("upload", "", lambda job, report: share.upload(path, report),
                               f"Link erstellen: {Path(path).stem}", lane="net")}


@app.post("/api/open-link")
def open_link(body: dict = Body(...)):
    """Einen erstellten Download-Link im Browser öffnen (nur Links des Upload-Dienstes)."""
    from urllib.parse import urlparse
    from app import share
    url = str(body.get("url") or "")
    u = urlparse(url)
    if u.scheme != "https" or u.hostname not in share.LINK_HOSTS:
        raise HTTPException(403)
    webbrowser.open(url)
    return {"ok": True}


@app.post("/api/open")
def open_folder(body: dict = Body(...)):
    target = Path(body["path"]).resolve()
    allowed = [config.ROOT.resolve(), config.game_packs_dir().resolve()]
    if not any(target == a or a in target.parents for a in allowed):
        raise HTTPException(403)
    if target.is_file():
        os.startfile(target.parent)
    elif target.exists():
        os.startfile(target)
    return {"ok": True}


app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True), name="static")


def _already_running(url):
    import urllib.request
    try:
        with urllib.request.urlopen(url + "/api/state", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


if __name__ == "__main__":
    url = f"http://{config.HOST}:{config.PORT}"
    if _already_running(url):
        print(f"\n  Voicitool läuft schon, öffne {url}\n")
        webbrowser.open(url)
        sys.exit(0)
    project.mark_interrupted()
    print(f"\n  Voicitool läuft: {url}\n  (Fenster offen lassen, zum Beenden schließen)\n")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")
