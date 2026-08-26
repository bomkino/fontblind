#!/bin/zsh
set -euo pipefail

APP_DIR="${0:A:h}"
cd "$APP_DIR"

if [[ ! -x .venv/bin/fontblind-local ]]; then
  echo "Run install-fontblind.command once, then open this file again."
  read -r "?Press Return to close."
  exit 1
fi

exec .venv/bin/fontblind-local "$@"
