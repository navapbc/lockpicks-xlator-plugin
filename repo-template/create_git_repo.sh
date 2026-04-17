#!/usr/bin/env bash
set -euo pipefail

if ! command -v envsubst >/dev/null 2>&1; then
    echo "Error: envsubst not found. Install gettext (e.g. 'brew install gettext')." >&2
    exit 1
fi

SOURCE="${BASH_SOURCE[0]}"
SCRIPT_DIR="$(cd -P "$(dirname -- "$SOURCE")" >/dev/null 2>&1 && pwd)"

NEW_REPO_PATH="$1"
DOMAINS_DIR="${2:-domains}"

if [ -z "$NEW_REPO_PATH" ]; then
    echo "Usage: $0 <new_repo_path> [domains_dirname]" >&2
    echo "  domains_dirname will be a subfolder in the new repo; defaults to 'domains' if not provided." >&2
    exit 1
fi

if [ -d "$NEW_REPO_PATH" ]; then
    echo "Error: $NEW_REPO_PATH already exists." >&2
    exit 2
fi

# Resolve NEW_REPO_PATH to an absolute path without requiring it to exist yet
NEW_REPO_ABS="$(cd "$(dirname "$NEW_REPO_PATH")" 2>/dev/null && pwd)/$(basename "$NEW_REPO_PATH")"
case "$NEW_REPO_ABS" in
    "$SCRIPT_DIR"/*|"$SCRIPT_DIR")
        echo "Error: NEW_REPO_PATH ($NEW_REPO_ABS) must not be inside $SCRIPT_DIR." >&2
        exit 3
        ;;
esac

echo "Creating new git repo at $NEW_REPO_PATH (with Xlator subfolder '$DOMAINS_DIR')..."
mkdir -p "$NEW_REPO_PATH"
NEW_REPO_PATH="$(cd "$NEW_REPO_PATH" && pwd)"

cd "$NEW_REPO_PATH"
git init -b main

echo "  Copying template files..."
cp -av "$SCRIPT_DIR/." .
# Remove this script from the new repo since it's only needed for bootstrapping
rm -f "$(basename "$0")"

echo "  Writing xlator.conf..."
export DOMAINS_DIR="$DOMAINS_DIR"
echo "export DOMAINS_DIR=\"${DOMAINS_DIR}\"" > xlator.conf

echo "  Expanding variables in template files..."
# Replace only the '$DOMAINS_DIR_VALUE' variables in these template files
export DOMAINS_DIR_VALUE="$DOMAINS_DIR"
envsubst '$DOMAINS_DIR_VALUE' < .devcontainer/devcontainer.tmpl.json > .devcontainer/devcontainer.json
envsubst '$DOMAINS_DIR_VALUE' < CLAUDE.tmpl.md > CLAUDE.md
rm -f .devcontainer/devcontainer.tmpl.json CLAUDE.tmpl.md

echo "  Adding files to git..."
git add .
git commit -m "Initial commit created by Xlator $(basename "$0")"

echo "Done. Repo created at $NEW_REPO_PATH"

