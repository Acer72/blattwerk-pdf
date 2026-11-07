@echo off
REM Startet Blattwerk PDF unter Windows (richtet beim ersten Mal alles automatisch ein)
cd /d "%~dp0"
if not exist ".venv-win\Scripts\pythonw.exe" (
  echo Erste Einrichtung - bitte kurz warten ...
  py -3 -m venv .venv-win || python -m venv .venv-win || (echo Python 3 fehlt. Bitte von https://www.python.org installieren ^(Haken bei "Add to PATH"^). & pause & exit /b 1)
  ".venv-win\Scripts\python.exe" -m pip install --upgrade pip
  ".venv-win\Scripts\python.exe" -m pip install -r requirements.txt
)
start "" ".venv-win\Scripts\pythonw.exe" blattwerk.py %*
