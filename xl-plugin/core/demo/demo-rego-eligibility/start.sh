#!/usr/bin/env bash
# Start the Xlator SNAP Eligibility Demo
# Launches OPA REST server + FastAPI backend
#
# Usage (from the repo root or this directory):
#   bash $DOMAINS_DIR/snap/output/demo-eligibility/start.sh
#
# Prerequisites:
#   - opa CLI installed (brew install opa)
#   - Python deps installed (uv venv && source .venv/bin/activate && uv pip install -r $DOMAINS_DIR/snap/output/demo-eligibility/requirements.txt)
#   - Rego policy generated (./xlator rego-transpile snap eligibility)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
REGO_FILE="$SCRIPT_DIR/../eligibility.rego"
OPA_PORT=8181
FASTAPI_PORT=8000

# Check prerequisites
if ! command -v opa &>/dev/null; then
  echo "ERROR: opa CLI not found. Install with: brew install opa"
  exit 1
fi

if [ ! -f "$REGO_FILE" ]; then
  echo "ERROR: Rego policy not found at $REGO_FILE"
  echo "Generate it first:"
  echo "  ./xlator rego-transpile snap eligibility"
  exit 1
fi

# Start OPA REST server in background
echo "Starting OPA server on port $OPA_PORT..."
opa run --server --addr ":$OPA_PORT" "$REGO_FILE" &
OPA_PID=$!

# Wait for OPA to be ready
for i in $(seq 1 10); do
  if curl -sf "http://localhost:$OPA_PORT/health" &>/dev/null; then
    echo "OPA server ready (PID $OPA_PID)"
    break
  fi
  sleep 0.5
  if [ $i -eq 10 ]; then
    echo "ERROR: OPA server failed to start after 5 seconds"
    kill $OPA_PID 2>/dev/null
    exit 1
  fi
done

echo ""
echo "Starting FastAPI backend on port $FASTAPI_PORT..."
echo ""
echo "  Demo: http://localhost:$FASTAPI_PORT/static/index.html"
echo "  API:  http://localhost:$FASTAPI_PORT/api/snap/eligibility"
echo "  Docs: http://localhost:$FASTAPI_PORT/docs"
echo ""
echo "Press Ctrl+C to stop."
echo ""

cleanup() {
  echo ""
  echo "Stopping OPA server (PID $OPA_PID)..."
  kill $OPA_PID 2>/dev/null
}
trap cleanup EXIT

cd "$SCRIPT_DIR"
uvicorn main:app --host 0.0.0.0 --port "$FASTAPI_PORT" --reload
