#!/bin/zsh
cd "$(dirname "$0")"
./.venv/bin/python login.py
echo
echo "Press any key to close..."
read -k 1
