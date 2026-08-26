#!/bin/zsh
set -euo pipefail

APP_DIR="${0:A:h}"
cd "$APP_DIR"

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
.venv/bin/python -m pip install --disable-pip-version-check --no-deps -e .
echo "FontBlind is installed locally."
