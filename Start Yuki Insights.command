#!/bin/bash
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python is not installed. Please install it from https://www.python.org/downloads/"
  read -p "Press Enter to close"; exit 1
fi
python3 -m pip install --quiet --disable-pip-version-check --user openpyxl==3.1.5 >/dev/null 2>&1
python3 radar/app.py
