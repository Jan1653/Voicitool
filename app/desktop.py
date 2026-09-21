"""Voicitool als eigenes Fenster (pywebview/Edge WebView2) statt im Browser."""
import json
import os
import queue
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402

LOG_DIR = config.DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Ohne Konsole (pythonw) gibt es kein stdout/stderr -> in Logdatei schreiben
if sys.stdout is None or sys.stderr is None or "--log" in sys.argv:
    _log = open(LOG_DIR / "voicitool.log", "a", encoding="utf8", buffering=1)
    sys.stdout = sys.stderr = _log
print(f"\n===== Start {time.strftime('%Y-%m-%d %H:%M:%S')} =====")

import uvicorn  # noqa: E402
import webview  # noqa: E402

URL = f"http://{config.HOST}:{config.PORT}"
ICON = config.STATIC_DIR / "icon.ico"

def ui_theme():
    """'light' oder 'dark' fürs Startfenster: Einstellung, sonst Windows-App-Modus, sonst dunkel."""
    choice = config.user_settings().get("theme", "system")
    if choice in ("light", "dark"):
        return choice
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 1 else "dark"
    except Exception:
        return "dark"


THEME = ui_theme()
BG = "#f3f5f9" if THEME == "light" else "#0e1016"
FG, MUTED, TRACK = ("#1b1f27", "#5f6878", "#e3e7ee") if THEME == "light" else ("#e7e9ee", "#8d94a3", "#20242d")
LANG = config.ui_lang()


def _texts():
    """Übersetzungen der Fenstertexte (app/setup/sprachen.json, Abschnitt desktop, Schlüssel = deutscher Text)."""
    try:
        return json.loads((config.APP_DIR / "setup" / "sprachen.json").read_text(encoding="utf8"))["desktop"]
    except Exception:
        return {}


TEXTS = _texts()


def tr(de):
    if LANG == "de":
        return de
    return TEXTS.get(LANG, {}).get(de) or TEXTS.get("en", {}).get(de) or de


LOADING = tr("Voicitool startet …")

SPLASH = f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;height:100%;background:{BG};color:{FG};font-family:"Segoe UI",system-ui,sans-serif;
  display:flex;align-items:center;justify-content:center;overflow:hidden}}
.box{{text-align:center}}
.logo{{width:96px;height:96px;border-radius:22px;background:linear-gradient(135deg,#3f7cf0,#a855f7);
  display:flex;align-items:center;justify-content:center;font-size:52px;margin:0 auto 18px;
  box-shadow:0 12px 40px #3f7cf055;animation:pulse 2s ease-in-out infinite}}
h1{{margin:0;font-size:30px;letter-spacing:.5px}}
p{{margin:6px 0 26px;color:{MUTED}}}
.bar{{width:260px;height:6px;border-radius:3px;background:{TRACK};overflow:hidden;margin:0 auto}}
.bar div{{height:100%;width:40%;border-radius:3px;background:linear-gradient(90deg,#3f7cf0,#a855f7);
  animation:slide 1.2s ease-in-out infinite}}
@keyframes slide{{0%{{transform:translateX(-110%)}}100%{{transform:translateX(260%)}}}}
@keyframes pulse{{0%,100%{{transform:scale(1)}}50%{{transform:scale(1.04)}}}}
</style></head><body><div class="box"><div class="logo">🎙️</div><h1>Voicitool</h1><p id="msg">{LOADING}</p>
<div class="bar"><div></div></div></div></body></html>"""


def server_state():
    """(läuft, Version) des Servers auf dem Port."""
    try:
        with urllib.request.urlopen(URL + "/api/state", timeout=1) as r:
            import json
            return True, json.loads(r.read().decode("utf8")).get("version")
    except Exception:
        return False, None


def server_up():
    return server_state()[0]


class App:
    def __init__(self):
        self.server = None
        self.own_server = False
        self.old_version = False
        self.window = None
        self.quitting = False

    def start_server(self):
        up, version = server_state()
        if up:  # läuft schon (z. B. zweites Fenster) -> mitbenutzen
            self.old_version = version != config.APP_VERSION
            print(f"Server läuft bereits (Version {version}), verbinde nur das Fenster")
            return
        from app import server as srv
        srv.project.mark_interrupted()
        srv.QUIT_HOOK = self.quit   # z. B. „Jetzt aktualisieren“: Fenster schließen, exe übernimmt
        cfg = uvicorn.Config(srv.app, host=config.HOST, port=config.PORT, log_level="warning",
                             proxy_headers=False, access_log=False)
        self.server = uvicorn.Server(cfg)
        self.own_server = True
        threading.Thread(target=self.server.run, daemon=True).start()

    def wait_and_load(self):
        threading.Thread(target=set_window_icon, args=(self.window.title,), daemon=True).start()
        if self.old_version:
            self.show_msg(tr("Es läuft noch eine ältere Voicitool-Version auf diesem PC. Bitte deren Fenster "
                             "schließen und Voicitool neu starten."))
            return
        for _ in range(600):
            if server_up():
                self.window.load_url(URL + "/?" + ui_query())
                return
            time.sleep(0.1)
        self.show_msg(tr("Start fehlgeschlagen. Details: daten/logs/voicitool.log"))

    def show_msg(self, text):
        self.window.evaluate_js(f"document.getElementById('msg').textContent={json.dumps(text)}")

    def busy_jobs(self):
        if not self.own_server:
            return []
        from app import server as srv
        st = srv.jobs.state()
        return st["running"] + st["pending"]

    def quit(self):
        self.quitting = True
        self.window.destroy()

    def on_closing(self):
        if self.quitting:
            return True
        busy = self.busy_jobs()
        if busy:
            names = ", ".join(j["label"] for j in busy[:3])
            ok = self.window.create_confirmation_dialog(
                tr("Voicitool beenden?"), tr("Es läuft noch: {0}.\nBeim Beenden wird das abgebrochen.").format(names))
            return bool(ok)  # False = Fenster bleibt offen
        return True

    def shutdown(self):
        if not self.own_server:
            return
        from app import server as srv
        for j in self.busy_jobs():
            try:
                srv.jobs.cancel(j["id"])
            except Exception:
                traceback.print_exc()
        self.server.should_exit = True
        time.sleep(0.5)


class Taskbar:
    """Fortschritt im Taskleisten-Symbol (ITaskbarList3), wie beim Download im Browser.

    COM über ctypes in einem eigenen Thread; set(): None = aus, < 0 = läuft ohne bekannten Fortschritt, 0..1 = Anteil."""
    CLSID = "{56FDF344-FD6D-11d0-958A-006097C9A090}"
    IID = "{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}"

    def __init__(self, title):
        self.title = title
        self.q = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def set(self, frac):
        self.q.put(frac)

    def _run(self):
        import ctypes
        from ctypes import wintypes
        try:
            ole32, user32 = ctypes.windll.ole32, ctypes.windll.user32
            ole32.CoInitializeEx(None, 2)   # COINIT_APARTMENTTHREADED

            class GUID(ctypes.Structure):
                _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort), ("d3", ctypes.c_ushort),
                            ("d4", ctypes.c_ubyte * 8)]

            def guid(s):
                g = GUID()
                ole32.CLSIDFromString(ctypes.c_wchar_p(s), ctypes.byref(g))
                return g

            obj = ctypes.c_void_p()
            if ole32.CoCreateInstance(ctypes.byref(guid(self.CLSID)), None, 1, ctypes.byref(guid(self.IID)),
                                      ctypes.byref(obj)) != 0 or not obj:
                return
            vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]

            def method(i, *args):
                return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *args)(vtbl[i])

            method(3)(obj)   # HrInit
            set_value = method(9, wintypes.HWND, ctypes.c_ulonglong, ctypes.c_ulonglong)
            set_state = method(10, wintypes.HWND, ctypes.c_int)
        except Exception:
            traceback.print_exc()
            return
        hwnd, last = 0, "aus"
        while True:
            frac = self.q.get()
            while not self.q.empty():   # nur der neueste Stand zählt
                frac = self.q.get_nowait()
            try:
                if not hwnd or not user32.IsWindow(hwnd):
                    hwnd = self._own_window(user32)
                    if not hwnd:
                        continue
                if frac is None:
                    if last != "aus":
                        set_state(obj, hwnd, 0)   # TBPF_NOPROGRESS
                    last = "aus"
                elif frac < 0:
                    set_state(obj, hwnd, 1)   # TBPF_INDETERMINATE
                    last = "unbestimmt"
                else:
                    if last != "normal":
                        set_state(obj, hwnd, 2)   # TBPF_NORMAL
                    set_value(obj, hwnd, int(min(max(frac, 0.0), 1.0) * 1000), 1000)
                    last = "normal"
            except Exception:
                traceback.print_exc()


    def _own_window(self, user32):
        """Sichtbares Fenster dieses Prozesses mit dem Titel (nicht die gleichnamige Konsole)."""
        import ctypes
        from ctypes import wintypes
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(h, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value == os.getpid() and user32.IsWindowVisible(h):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(h, buf, 256)
                cls = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(h, cls, 256)
                if buf.value == self.title and cls.value != "ConsoleWindowClass":
                    found.append(h)
                    return False
            return True

        user32.EnumWindows(cb, 0)
        return found[0] if found else 0


class Bridge:
    """Für die Oberfläche als window.pywebview.api.* (Attribute mit _ sieht pywebview nicht)."""

    def __init__(self, taskbar):
        self._taskbar = taskbar

    def taskbar_progress(self, frac=None):
        self._taskbar.set(None if frac is None else float(frac))
        return True

    def pick_game_exe(self, directory=""):
        """Windows-Dialog „Datei öffnen“ für die Spiel-exe (voicigame). Ergebnis: Pfad oder None."""
        win = webview.windows[0] if webview.windows else None
        if win is None:
            return None
        res = win.create_file_dialog(webview.FileDialog.OPEN, directory=str(directory or ""),
                                     file_types=("The Choicer Voicer (*.exe)", "All files (*.*)"))
        return str(res[0]) if res else None


def ui_query():
    """Sprache für die Oberfläche: lang = eigene Wahl (falls schon getroffen), sys = Windows-Sprache."""
    q = {"sys": config.system_lang()}
    if config.user_settings().get("ui_lang") in config.UI_LANGS:
        q["lang"] = config.user_settings()["ui_lang"]
    return urllib.parse.urlencode(q)


def set_window_icon(title):
    """Fenster- und Taskleisten-Icon setzen (pywebview setzt es unter Windows nicht selbst)."""
    import ctypes
    try:
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        hwnd = 0
        for _ in range(100):
            hwnd = user32.FindWindowW(None, title)
            if hwnd:
                break
            time.sleep(0.1)
        if not hwnd or not ICON.exists():
            return
        for size, msg in ((32, 1), (16, 0)):  # ICON_BIG / ICON_SMALL
            h = user32.LoadImageW(0, str(ICON), 1, size, size, 0x10)  # IMAGE_ICON | LR_LOADFROMFILE
            if h:
                user32.SendMessageW(hwnd, 0x0080, msg, h)
        kernel32.SetConsoleTitleW(title)
        user32.SetForegroundWindow(hwnd)   # klappt, wenn Voicitool.exe es erlaubt hat (Start aus dem Startfenster)
    except Exception:
        traceback.print_exc()


def main():
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Jan1653.Voicitool")
    except Exception:
        pass
    app = App()
    try:
        app.start_server()
    except Exception:
        traceback.print_exc()
    app.window = webview.create_window(
        config.APP_NAME, html=SPLASH, width=1480, height=920,
        min_size=(1040, 660), background_color=BG, text_select=True, js_api=Bridge(Taskbar(config.APP_NAME)))
    app.window.events.closing += app.on_closing
    webview.start(app.wait_and_load, gui="edgechromium", private_mode=False,
                  storage_path=str(config.DATA_DIR / "fenster"), icon=str(ICON) if ICON.exists() else None)
    app.shutdown()
    os._exit(0)  # Hintergrund-Threads (Server, Jobs) sicher beenden


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
