#!/bin/bash
# Baut eine eigenständige "Blattwerk PDF.app" im Ordner dist/
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; }
.venv/bin/pip install pyinstaller
.venv/bin/pyinstaller --noconfirm --windowed --name "Blattwerk PDF" blattwerk.py
echo; echo "Fertig: dist/Blattwerk PDF.app"
