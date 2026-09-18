"""Baut Voicitool.exe, die einzige Datei zum Weitergeben.

Die exe enthält die komplette App (app/, README) als eingebettetes ZIP und installiert, aktualisiert
und startet Voicitool selbst. Kompiliert wird mit dem csc.exe von .NET Framework 4, das in jedem
Windows 10/11 steckt. Es muss nichts zusätzlich installiert werden.

Vor jedem Bau (und jedem Hochladen auf GitHub) die Build-Nummer in app/version.json erhöhen.
"""
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
OUT = ROOT / "Voicitool.exe"
BUILD_DIR = ROOT / "daten" / "build"
SOURCE = APP / "setup" / "launcher" / "Voicitool.cs"
EXTRA_FILES = ["README.md", "LICENSE"]
SKIP_PARTS = {"__pycache__", ".pytest_cache"}
SKIP_SUFFIX = {".pyc", ".pyo"}


def find_csc():
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    for fw in ("Framework64", "Framework"):
        csc = windir / "Microsoft.NET" / fw / "v4.0.30319" / "csc.exe"
        if csc.exists():
            return csc
    raise SystemExit("csc.exe (.NET Framework 4) nicht gefunden.")


def make_zip(target):
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(APP.rglob("*")):
            if f.is_file() and not SKIP_PARTS & set(f.parts) and f.suffix not in SKIP_SUFFIX:
                z.write(f, f.relative_to(ROOT).as_posix())
                count += 1
        for name in EXTRA_FILES:
            if (ROOT / name).exists():
                z.write(ROOT / name, name)
                count += 1
    return count


def launcher_texts(target):
    """Übersetzungen fürs Startfenster (sprachen.json, Abschnitt launcher) als Tabelle: Sprache, Englisch, Text."""
    data = json.loads((APP / "setup" / "sprachen.json").read_text(encoding="utf8")).get("launcher", {})

    def esc(s):
        return s.replace("\\", "\\\\").replace("\t", " ").replace("\n", "\\n")

    lines = [f"{lang}\t{esc(en)}\t{esc(tr)}" for lang, table in data.items() for en, tr in table.items()]
    target.write_text("\n".join(lines), encoding="utf8")
    return len(data)


def ensure_bom(path):
    """Windows PowerShell 5.1 liest Skripte ohne BOM als ANSI: Umlaute und ✓ würden zu Zeichensalat."""
    data = path.read_bytes()
    if not data.startswith(b"\xef\xbb\xbf"):
        path.write_bytes(b"\xef\xbb\xbf" + data)
        print(f"BOM ergänzt: {path.relative_to(ROOT)}")


def main():
    for ps1 in APP.rglob("*.ps1"):
        ensure_bom(ps1)
    version = json.loads((APP / "version.json").read_text(encoding="utf8"))
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = BUILD_DIR / "app.zip"
    count = make_zip(zip_path)
    texts_path = BUILD_DIR / "texts.txt"
    langs = launcher_texts(texts_path)
    # Version der exe (für die Selbst-Aktualisierung): „launcher“ aus version.json, bei jeder Änderung am Launcher +1
    info_path = BUILD_DIR / "AssemblyInfo.cs"
    launcher = int(version.get("launcher", 1))
    info_path.write_text(f'[assembly: System.Reflection.AssemblyVersion("{launcher}.0.0.0")]\n'
                         f'[assembly: System.Reflection.AssemblyFileVersion("{launcher}.0.{version["build"]}.0")]\n',
                         encoding="utf8")

    csc = find_csc()
    args = [str(csc), "/nologo", "/target:winexe", "/optimize+", "/platform:anycpu",
            f"/out:{OUT}", f"/win32icon:{APP / 'static' / 'icon.ico'}",
            f"/resource:{zip_path},app.zip", f"/resource:{texts_path},texts.txt", "/codepage:65001",
            "/r:System.IO.Compression.dll", "/r:System.IO.Compression.FileSystem.dll",
            "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll",
            str(SOURCE), str(info_path)]
    r = subprocess.run(args, capture_output=True, text=True, encoding="cp850", errors="replace")
    if r.returncode != 0:
        print(r.stdout, r.stderr)
        sys.exit(r.returncode)
    size = OUT.stat().st_size / 1e6
    print(f"{OUT} gebaut: Version {version['version']}, Build {version['build']}, "
          f"Launcher {launcher}, {count} Dateien eingebettet, Startfenster in {langs + 2} Sprachen, {size:.1f} MB")
    if not version.get("repo"):
        print("Hinweis: In app/version.json ist noch kein GitHub-Repo eingetragen. Die exe sucht erst nach Updates, "
              "wenn dort z. B. \"repo\": \"Name/Voicitool\" steht.")


if __name__ == "__main__":
    main()
