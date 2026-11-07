#!/bin/bash
# Startet Blattwerk PDF auf dem Mac (richtet beim ersten Mal alles automatisch ein)
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Erste Einrichtung – bitte kurz warten …"
  python3 -m venv .venv || { echo "Python 3 fehlt. Bitte von https://www.python.org installieren."; read; exit 1; }
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi
.venv/bin/python blattwerk.py "$@"
