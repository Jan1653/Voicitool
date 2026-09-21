"""Holt den Spiel-Mod voicigame nach app/voicigame_mod (mitgeliefert, kommt mit jedem Voicitool-Update).

Quelle: ../voicigame/mod/voicigame neben dem Projektordner, oder ein anderer Ordner als Argument.
Kopiert nur die Mod-Dateien (*.gd und lang.json), keine Testdateien. Was es in der Quelle nicht mehr
gibt, wandert in den Papierkorb.

Aufruf: .venv\\Scripts\\python.exe app\\setup\\voicigame_mod.py [Quellordner]
"""
import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "app" / "voicigame_mod"
DEFAULT_SOURCE = ROOT.parent / "voicigame" / "mod" / "voicigame"


def is_mod_file(p):
    if not p.is_file():
        return False
    name = p.name.lower()
    if name.startswith("test") or p.stem.lower().endswith("_test"):
        return False
    return p.suffix.lower() == ".gd" or name == "lang.json"


def main():
    source = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else DEFAULT_SOURCE
    if not (source / "main.gd").is_file():
        raise SystemExit(f"Kein Mod gefunden (main.gd fehlt): {source}")
    files = {p.name: p for p in sorted(source.iterdir()) if is_mod_file(p)}
    TARGET.mkdir(parents=True, exist_ok=True)
    changed = 0
    for name, src in files.items():
        dst = TARGET / name
        if dst.exists() and filecmp.cmp(src, dst, shallow=False):
            continue
        shutil.copy2(src, dst)
        changed += 1
    stale = [p for p in TARGET.iterdir() if p.name not in files]
    if stale:
        from send2trash import send2trash
        for p in stale:
            send2trash(str(p))
    print(f"{TARGET}: {len(files)} Dateien aus {source}, {changed} neu oder geändert, {len(stale)} in den Papierkorb")


if __name__ == "__main__":
    main()
