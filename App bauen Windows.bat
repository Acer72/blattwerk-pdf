@echo off
REM Baut eine eigenstaendige "Blattwerk PDF.exe" im Ordner dist\
cd /d "%~dp0"
if not exist ".venv-win\Scripts\python.exe" (
  py -3 -m venv .venv-win || python -m venv .venv-win
  ".venv-win\Scripts\python.exe" -m pip install -r requirements.txt
)
".venv-win\Scripts\python.exe" -m pip install pyinstaller
".venv-win\Scripts\pyinstaller.exe" --noconfirm --windowed --name "Blattwerk PDF" blattwerk.py
echo.
echo Fertig: dist\Blattwerk PDF\Blattwerk PDF.exe
pause
