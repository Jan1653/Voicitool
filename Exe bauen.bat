@echo off
rem Baut Voicitool.exe – die einzige Datei zum Weitergeben (vorher Build-Nummer in app\version.json erhoehen)
cd /d "%~dp0"
".venv\Scripts\python.exe" "app\setup\build_exe.py"
pause
