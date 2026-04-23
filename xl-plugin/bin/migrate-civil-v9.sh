#!/usr/bin/env bash
# migrate-civil-v9.sh — Migrate domain files from CIVIL DSL v8 → v9
#
# Changes applied:
#   .civil.yaml files:
#     - top-level key  facts:      → inputs:
#     - top-level key  decisions:  → outputs:
#     - tag value      tags: [output]  → tags: [expose]   (all list-form variants)
#   naming-manifest.yaml files:
#     - top-level key  entities:   → inputs:
#
# Usage:
#   ./migrate-civil-v9.sh [DOMAINS_DIR]
#
#   If DOMAINS_DIR is not supplied the script reads it from .xlator.local.env in the
#   git repo root (same resolution logic as xlator).
#
# The script is idempotent: re-running on already-migrated files makes no changes.

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve DOMAINS_DIR
# ---------------------------------------------------------------------------

if [[ $# -ge 1 ]]; then
    DOMAINS_DIR="$1"
else
    # Locate the git repo root
    if git rev-parse --show-toplevel >/dev/null 2>&1; then
        PROJECT_ROOT="$(git rev-parse --show-toplevel)"
    else
        echo "ERROR: not inside a git repo and no DOMAINS_DIR argument supplied." >&2
        exit 1
    fi

    ENV_FILE="$PROJECT_ROOT/.xlator.local.env"
    if [[ ! -f "$ENV_FILE" ]]; then
        echo "ERROR: $ENV_FILE not found. Pass DOMAINS_DIR as an argument or run from a project with .xlator.local.env." >&2
        exit 1
    fi

    # Source only the DOMAINS_DIR variable; avoid executing arbitrary exports
    DOMAINS_DIR="$(grep -E '^[[:space:]]*DOMAINS_DIR=' "$ENV_FILE" | head -1 | sed 's/^[[:space:]]*DOMAINS_DIR=//' | tr -d '"'"'")"
    if [[ -z "$DOMAINS_DIR" ]]; then
        echo "ERROR: DOMAINS_DIR not found in $ENV_FILE." >&2
        exit 1
    fi

    # Resolve relative path against project root
    if [[ "$DOMAINS_DIR" != /* ]]; then
        DOMAINS_DIR="$PROJECT_ROOT/$DOMAINS_DIR"
    fi
fi

if [[ ! -d "$DOMAINS_DIR" ]]; then
    echo "ERROR: DOMAINS_DIR '$DOMAINS_DIR' is not a directory." >&2
    exit 1
fi

echo "Migrating domain files in: $DOMAINS_DIR"
echo ""

civil_files_modified=0
manifest_files_modified=0

# ---------------------------------------------------------------------------
# Helper: in-place substitution portable across macOS and Linux
#   perl_inplace FILE PATTERN REPLACEMENT
# ---------------------------------------------------------------------------
perl_inplace() {
    local file="$1"
    local pattern="$2"
    local replacement="$3"
    perl -pi -e "s${pattern}${replacement}" "$file"
}

# ---------------------------------------------------------------------------
# Migrate .civil.yaml files
# ---------------------------------------------------------------------------

while IFS= read -r -d '' civil_file; do
    original_content="$(cat "$civil_file")"
    modified=false

    # 1. Rename top-level key  facts:  →  inputs:
    #    Match only at the start of a line (no leading whitespace)
    if grep -q '^facts:' "$civil_file"; then
        perl_inplace "$civil_file" '|^facts:|' 'inputs:'
        modified=true
    fi

    # 2. Rename top-level key  decisions:  →  outputs:
    if grep -q '^decisions:' "$civil_file"; then
        perl_inplace "$civil_file" '|^decisions:|' 'outputs:'
        modified=true
    fi

    # 3. Rename tag value  output  →  expose  in tags: lists
    #    Handles all common forms:
    #      tags: [output]
    #      tags: [output, ...]
    #      tags: [..., output]
    #      tags: [..., output, ...]
    #      - output        (block-style list items)
    if grep -qE 'tags:.*\boutput\b|- output$' "$civil_file"; then
        # Block-style list item:  "- output"
        perl_inplace "$civil_file" '|(^|\s)- output($|\s)|' '${1}- expose${2}'
        # Inline list — standalone [output]:  [output]
        perl_inplace "$civil_file" '|\[output\]|' '[expose]'
        # Inline list — output at start followed by comma:  [output,
        perl_inplace "$civil_file" '|\[output,|' '[expose,'
        # Inline list — output after comma:  , output]  or  , output,
        perl_inplace "$civil_file" '|(,\s*)output(\s*[\],])|' '${1}expose${2}'
        modified=true
    fi

    new_content="$(cat "$civil_file")"
    if [[ "$original_content" != "$new_content" ]]; then
        echo "  [civil.yaml]  $civil_file"
        civil_files_modified=$((civil_files_modified + 1))
    fi
done < <(find "$DOMAINS_DIR" -name "*.civil.yaml" -print0)

# ---------------------------------------------------------------------------
# Migrate naming-manifest.yaml files
# ---------------------------------------------------------------------------

while IFS= read -r -d '' manifest_file; do
    original_content="$(cat "$manifest_file")"

    # Rename top-level key  entities:  →  inputs:
    if grep -q '^entities:' "$manifest_file"; then
        perl_inplace "$manifest_file" '|^entities:|' 'inputs:'
    fi

    new_content="$(cat "$manifest_file")"
    if [[ "$original_content" != "$new_content" ]]; then
        echo "  [manifest]    $manifest_file"
        manifest_files_modified=$((manifest_files_modified + 1))
    fi
done < <(find "$DOMAINS_DIR" -name "naming-manifest.yaml" -print0)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
total=$((civil_files_modified + manifest_files_modified))
if [[ $total -eq 0 ]]; then
    echo "No files needed migration (already up to date)."
else
    echo "Migration complete:"
    echo "  .civil.yaml files modified:        $civil_files_modified"
    echo "  naming-manifest.yaml files modified: $manifest_files_modified"
fi
