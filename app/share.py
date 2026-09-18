"""Download-Link für eine fertige ZIP: Upload zu Litterbox (catbox.moe), ohne Anmeldung.

upload(path, report)   lädt die ZIP hoch (mit Fortschritt, abbrechbar), gibt Link und Ablaufzeit zurück
existing(path)         noch gültiger Link für genau diese Datei (sonst None), damit nichts doppelt hochgeht
Der Link gilt HOURS Stunden, danach löscht der Dienst die Datei. Höchstens MAX_BYTES.
Texte sind Deutsch (Quelle der Übersetzung, siehe static/lang/strings.js).
"""
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from app import config

UPLOAD_URL = os.environ.get("VOICITOOL_SHARE_URL", "https://litterbox.catbox.moe/resources/internals/api.php")
LINK_HOSTS = ("litter.catbox.moe", "litterbox.catbox.moe")   # nur solche Links öffnet die App im Browser
HOURS = 72
MAX_BYTES = 1000 * 1000 * 1000
LINKS_FILE = config.DATA_DIR / "links.json"


def _zip_path(path):
    """Nur ZIPs aus dem Export-Ordner (nichts anderes vom PC hochladen)."""
    p = Path(path).resolve()
    export = config.EXPORT_DIR.resolve()
    if p.suffix.lower() != ".zip" or export not in p.parents or not p.is_file():
        raise ValueError("Nur fertige ZIPs aus dem Export-Ordner lassen sich teilen.")
    return p


def _links():
    try:
        return json.loads(LINKS_FILE.read_text(encoding="utf8"))
    except Exception:
        return {}


def _stamp(p):
    st = p.stat()
    return [st.st_size, int(st.st_mtime)]


def existing(path):
    """Noch mindestens eine Stunde gültiger Link für genau diese Datei (gleiche Größe und Zeit)."""
    p = _zip_path(path)
    rec = _links().get(str(p))
    if rec and rec.get("stamp") == _stamp(p) and rec.get("expires", 0) > time.time() + 3600:
        return rec
    return None


class _Body:
    """Multipart-Inhalt zum Lesen in Stücken: zählt mit (Fortschritt) und bricht bei Abbruch ab."""

    def __init__(self, head, path, tail, on_read):
        self.parts = [head, path, tail]
        self.total = len(head) + path.stat().st_size + len(tail)
        self.sent = 0
        self.on_read = on_read
        self._f = None
        self._i = 0

    def __len__(self):
        return self.total

    def read(self, n=-1):
        n = 256 * 1024 if n is None or n < 0 else max(n, 64 * 1024)
        while self._i < 3:
            part = self.parts[self._i]
            if isinstance(part, bytes):
                if part:
                    self.parts[self._i] = b""
                    chunk = part
                else:
                    self._i += 1
                    continue
            else:
                if self._f is None:
                    self._f = open(part, "rb")
                chunk = self._f.read(n)
                if not chunk:
                    self._f.close()
                    self._i += 1
                    continue
            self.sent += len(chunk)
            self.on_read(self.sent, self.total)
            return chunk
        return b""

    def close(self):
        if self._f and not self._f.closed:
            self._f.close()


def upload(path, report):
    p = _zip_path(path)
    size = p.stat().st_size
    if size > MAX_BYTES:
        raise ValueError(f"Die ZIP ist {size / 1e9:.1f} GB groß. Der Dienst nimmt höchstens 1 GB.".replace(".", ",", 1))
    boundary = "----Voicitool" + uuid.uuid4().hex
    fields = {"reqtype": "fileupload", "time": f"{HOURS}h"}
    head = "".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in fields.items())
    head += (f'--{boundary}\r\nContent-Disposition: form-data; name="fileToUpload"; filename="{p.name}"\r\n'
             f"Content-Type: application/zip\r\n\r\n")
    tail = f"\r\n--{boundary}--\r\n".encode()
    mb = lambda b: f"{b / 1e6:.0f} MB"   # noqa: E731
    report("Hochladen", 0.0, f"0 / {mb(size)}")
    body = _Body(head.encode("utf8"), p, tail, lambda s, t: report("Hochladen", min(0.99, s / t), f"{mb(s)} / {mb(t)}"))
    version = json.loads((config.APP_DIR / "version.json").read_text(encoding="utf8")).get("version", "")
    req = urllib.request.Request(UPLOAD_URL, data=body, method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
        "User-Agent": f"Voicitool/{version}",
    })
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            text = r.read(4096).decode("utf8", "replace").strip()
    except urllib.error.HTTPError as e:
        if e.code == 413:
            raise RuntimeError("Die ZIP ist zu groß für den Dienst (höchstens 1 GB).") from None
        raise RuntimeError(f"Der Upload-Dienst hat abgelehnt (HTTP {e.code}). Versuche es später noch mal.") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"Upload fehlgeschlagen: {getattr(e, 'reason', e)}", flush=True)   # Technik nur ins Protokoll
        raise RuntimeError("Der Upload-Dienst ist gerade nicht erreichbar. Prüfe das Internet oder versuche es "
                           "später noch mal.") from None
    finally:
        body.close()
    if not text.startswith("https://"):
        raise RuntimeError(f"Der Dienst hat keinen Link geliefert: {text[:200]}")
    rec = {"url": text, "expires": time.time() + HOURS * 3600, "hours": HOURS, "size": size, "stamp": _stamp(p)}
    links = {k: v for k, v in _links().items() if v.get("expires", 0) > time.time()}   # abgelaufene vergessen
    links[str(p)] = rec
    tmp = LINKS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(links, ensure_ascii=False, indent=1), encoding="utf8")
    tmp.replace(LINKS_FILE)
    report("Hochladen", 1.0, mb(size))
    return rec
