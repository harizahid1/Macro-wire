#!/bin/bash
# Double-click on a Mac: refreshes the data and opens the page.
# First time only: right-click this file > Open > Open (macOS asks once because it came from a download).
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 was not found. Type python3 in Terminal and macOS offers to install it, or get it from python.org."
  read -n 1 -s -r -p "Press any key to close"
  exit 1
fi
python3 update.py --open
echo
read -n 1 -s -r -p "Press any key to close"
