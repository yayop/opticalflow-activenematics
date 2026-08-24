#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ ! -x .venv/bin/python ]]; then
  python -m virtualenv .venv
fi

.venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements-cluster.txt
.venv/bin/python scripts/check_environment.py
