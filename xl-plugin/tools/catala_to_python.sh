#!/usr/bin/env bash
# catala_to_python.sh — pre-flight step 3a from /create-demo
# Usage (via xlator CLI): xlator catala-to-python <domain> <module>
# Builds the Catala Python package for a domain/module and places it in
# $DOMAINS_FULLPATH/<domain>/output/demo-catala-<module>/python/.

set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <domain> <module>" >&2
  exit 1
fi

DOMAIN="$1"
MODULE="$2"

if [[ -z "$DOMAINS_FULLPATH" ]]; then
  echo "Error: DOMAINS_FULLPATH environment variable not set." >&2
  exit 1
fi

# This script expects to be run from the PROJECT_ROOT directory and
# that DOMAINS_FULLPATH is set to the path of the domains directory.
if [[ ! -d "$DOMAINS_FULLPATH" ]]; then
  echo "Error: DOMAINS_FULLPATH directory not found at ${DOMAINS_FULLPATH}" >&2
  exit 1
fi

OUTPUT_DIR="$DOMAINS_FULLPATH/${DOMAIN}/output"
DEMO_DIR="${OUTPUT_DIR}/demo-catala-${MODULE}"
PYTHON_DIR="${DEMO_DIR}/python"

# Derive the capitalized module name from clerk.toml
CLERK_TOML="${OUTPUT_DIR}/clerk.toml"
if [[ ! -f "$CLERK_TOML" ]]; then
  echo "Error: ${CLERK_TOML} not found." >&2
  exit 1
fi

# Extract the first module name from modules = ["ModuleName", ...]
MODULE_NAME=$(grep 'modules' "$CLERK_TOML" | sed 's/.*\["\([^"]*\)".*/\1/' | head -1)
if [[ -z "$MODULE_NAME" ]]; then
  echo "Error: Could not parse modules field from ${CLERK_TOML}." >&2
  exit 1
fi

COMPILED_PY="${PYTHON_DIR}/${MODULE_NAME}.py"

# Check if Python package already built
if [[ -f "$COMPILED_PY" ]]; then
  echo "Python package already present: ${COMPILED_PY}"
else
  echo "Python package not found — building..."

  # 1. Ensure destination exists
  mkdir -p "$PYTHON_DIR"

  # 2. Run clerk build
  echo "Running clerk build..."
  (cd "$OUTPUT_DIR" && clerk build)

  # 3. Move compiled Python package into demo folder
  SRC_PYTHON="${OUTPUT_DIR}/targets/${MODULE}/python"
  if [[ ! -d "$SRC_PYTHON" ]]; then
    echo "Error: clerk build did not produce ${SRC_PYTHON}" >&2
    exit 1
  fi
  # Move contents (not the directory itself, since PYTHON_DIR already exists)
  mv "${SRC_PYTHON}/"* "${PYTHON_DIR}/"

  # 4. Move dates.py from libcatala build output
  DATES_SRC="${OUTPUT_DIR}/_build/libcatala/python/dates.py"
  if [[ -f "$DATES_SRC" ]]; then
    mv "$DATES_SRC" "${PYTHON_DIR}/dates.py"
  else
    echo "Warning: dates.py not found at ${DATES_SRC} — skipping." >&2
  fi

  echo "Build complete: ${PYTHON_DIR}"
fi

# Ensure __init__.py exists so Python treats this as a package
INIT_PY="${PYTHON_DIR}/__init__.py"
if [[ ! -f "$INIT_PY" ]]; then
  touch "$INIT_PY"
  echo "Created ${INIT_PY}"
else
  echo "__init__.py already present."
fi

# Copy <module>_meta.py sidecar unconditionally — runs even when Eligibility.py already
# exists so the demo copy stays in sync whenever CIVIL changes and the pipeline reruns.
META_PY="${OUTPUT_DIR}/${MODULE}_meta.py"
if [[ -f "$META_PY" ]]; then
  mkdir -p "$PYTHON_DIR"
  cp "$META_PY" "${PYTHON_DIR}/${MODULE}_meta.py"
  echo "Copied ${META_PY} → ${PYTHON_DIR}/${MODULE}_meta.py"
else
  echo "Warning: ${META_PY} not found — run ./xlator catala-pipeline first to generate sidecar." >&2
fi
