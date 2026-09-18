"""Baut den Ordner, der auf GitHub hochgeladen wird: Voicitool-GitHub neben dem Projektordner.

Hinein kommt nur, was ins Repo gehört: app/ ohne Caches, README.md, LICENSE, .gitignore, Exe bauen.bat.
Nutzerdaten (projekte, eingang, export, modelle, daten, tools, .venv) und die exe bleiben draußen.
Der Ordner ist ein Git-Repo. Dateien, die es im Projekt nicht mehr gibt, wandern in den Papierkorb.
"""
import filecmp
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT.parent / "Voicitool-GitHub"
FILES = ["README.md", "LICENSE", ".gitignore", "Exe bauen.bat"]
SKIP_PARTS = {"__pycache__", ".pytest_cache"}
SKIP_SUFFIX = {".pyc", ".pyo"}


def wanted():
    out = {name: ROOT / name for name in FILES if (ROOT / name).exists()}
    for f in (ROOT / "app").rglob("*"):
        if f.is_file() and not SKIP_PARTS & set(f.parts) and f.suffix not in SKIP_SUFFIX:
            out[f.relative_to(ROOT).as_posix()] = f
    return out


def main():
    TARGET.mkdir(exist_ok=True)
    files = wanted()
    changed = 0
    for rel, src in files.items():
        dst = TARGET / rel
        if dst.exists() and filecmp.cmp(src, dst, shallow=False):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        changed += 1
    stale = [f for f in TARGET.rglob("*") if f.is_file() and ".git" not in f.relative_to(TARGET).parts[:1]
             and f.relative_to(TARGET).as_posix() not in files]
    if stale:
        from send2trash import send2trash
        for f in stale:
            send2trash(str(f))
    for d in sorted((p for p in TARGET.rglob("*") if p.is_dir() and ".git" not in p.relative_to(TARGET).parts[:1]),
                    key=lambda p: len(p.parts), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()   # leere Ordner (ohne Inhalt, nichts geht verloren)
    print(f"{TARGET}: {len(files)} Dateien, {changed} neu oder geändert, {len(stale)} in den Papierkorb")


if __name__ == "__main__":
    main()
