#!/usr/bin/env bash
# This script is intended to be copied into a demo folder and run from there.
# Start a Xlator Demo (Catala-Python mode)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CORE_CATALA_DIR="$(cd "$SCRIPT_DIR/../../../../core/catala" && pwd)"

if [ ! -f "requirements.txt" ]; then
  cp -v "$CORE_CATALA_DIR/requirements.txt" .
  uv pip install -r requirements.txt || { echo "ERROR: Failed to install requirements"; exit 1; }
fi

for F in "$CORE_CATALA_DIR"/python/*.py; do
  FN=$(basename "$F")
  [ -e "python/$FN" ] || ln -vs "$F" python/ || { echo "ERROR: Failed to link $FN"; exit 1; }
done

# Two PYTHONPATH entries required:
#   SCRIPT_DIR:      enables "from python.Earned_income import ..." (package import, supports relative imports)
#   PYTHON_PKG_DIR:  enables "from catala_runtime import *" (bare import in Catala-generated files)
PYTHON_PKG_DIR="$SCRIPT_DIR/python"
export PYTHONPATH="$SCRIPT_DIR:$PYTHON_PKG_DIR${PYTHONPATH:+:$PYTHONPATH}"

FASTAPI_PORT=8000
exec uv run uvicorn main:app --host 0.0.0.0 --port "$FASTAPI_PORT" --reload
