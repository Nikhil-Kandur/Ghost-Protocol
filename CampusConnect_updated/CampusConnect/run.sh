#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install -r requirements.txt
exec python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
