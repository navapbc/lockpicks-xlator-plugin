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

[[ -z "$DOMAINS_FULLPATH" ]] && DOMAINS_FULLPATH=$(pwd)

if [[ ! -d "$DOMAINS_FULLPATH/${DOMAIN}" ]]; then
  echo "Error: directory not found: ${DOMAINS_FULLPATH}/${DOMAIN}" >&2
  exit 1
fi

OUTPUT_DIR="$DOMAINS_FULLPATH/${DOMAIN}/output"
SPECS_DIR="$DOMAINS_FULLPATH/${DOMAIN}/specs"
DEMO_DIR="${OUTPUT_DIR}/demo-catala-${MODULE}"
PYTHON_DIR="${DEMO_DIR}/python"

# Ensure output/clerk.toml exists and has a [[target]] block for this module,
# and capture the target_dir clerk will write artifacts into. The helper
# creates clerk.toml from clerk_toml_defaults.SPEC_TIER when absent and
# appends a [[target]] block parsed from the specs/ > Module / > Using
# directives when one isn't already present for this target name.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR=$(uv run python "${SCRIPT_DIR}/clerk_target_inject.py" \
  "$OUTPUT_DIR" "$MODULE" "$MODULE" "$SPECS_DIR")

# Catala module names are snake_case with the first letter uppercased
# (e.g., `passes_income` → `Passes_income`). Substring + `tr` is portable
# across macOS system bash (3.2, no `${MODULE^}`) and Linux bash 4+.
MODULE_NAME="$(printf '%s' "${MODULE:0:1}" | tr '[:lower:]' '[:upper:]')${MODULE:1}"

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
  SRC_PYTHON="${OUTPUT_DIR}/${TARGET_DIR}/${MODULE}/python"
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
# exists so the demo copy stays in sync whenever the source changes and the pipeline reruns.
META_PY="${OUTPUT_DIR}/${MODULE}_meta.py"
if [[ -f "$META_PY" ]]; then
  mkdir -p "$PYTHON_DIR"
  cp "$META_PY" "${PYTHON_DIR}/${MODULE}_meta.py"
  echo "Copied ${META_PY} → ${PYTHON_DIR}/${MODULE}_meta.py"
else
  echo "Warning: ${META_PY} not found — run ./xlator catala-pipeline first to generate sidecar." >&2
fi
