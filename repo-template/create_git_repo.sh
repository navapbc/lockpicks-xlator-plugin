#!/usr/bin/env bash
set -euo pipefail

NEW_REPO_PATH="$1"
DOMAINS_DIR="$2"

if [ -z "$NEW_REPO_PATH" ] || [ -z "$DOMAINS_DIR" ]; then
    echo "Usage: $0 <new_repo_path> <domains_dir>" >&2
    exit 1
fi

if [ -d "$NEW_REPO_PATH" ]; then
    echo "Error: $NEW_REPO_PATH already exists." >&2
    exit 2
fi

# CLAUDE_PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# CORE_DIR="$CLAUDE_PLUGIN_ROOT/core"

# --- 1. Initialize repo from template ---
mkdir -p "$NEW_REPO_PATH"
NEW_REPO_PATH="$(cd "$NEW_REPO_PATH" && pwd)"
cp -a "." "$NEW_REPO_PATH"
cd "$NEW_REPO_PATH"

export DOMAINS_DIR="$DOMAINS_DIR"
echo "export DOMAINS_DIR='${DOMAINS_DIR}'" > xlator.conf 

# Replace only the '$DOMAINS_DIR' variable in these template files
envsubst '$DOMAINS_DIR' < .devcontainer/devcontainer.tmpl.json > .devcontainer/devcontainer.json
envsubst '$DOMAINS_DIR' < .vscode/settings.tmpl.json > .vscode/settings.json
envsubst '$DOMAINS_DIR' < CLAUDE.tmpl.md > CLAUDE.md
rm -f .devcontainer/devcontainer.tmpl.json .vscode/settings.tmpl.json CLAUDE.tmpl.md

echo "Creating git repository at $NEW_REPO_PATH"
git init -b main
git add .
git commit -m "Initial commit from Xlator create_git_repo.sh"

# --- 2. Configure and set up Xlator (just like /xl:setup does) ---
# # CLAUDE_PLUGIN_ROOT and DOMAINS_DIR will be used by the setup script
# export CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"
# "${CLAUDE_PLUGIN_ROOT}/xlator" setup

# git add .
# git commit -m "Set up '$DOMAINS_DIR' folder for Xlator"
# echo "Done setting up '$DOMAINS_DIR' folder for Xlator"

# --- 3. Install Xlator plugin ---

# gh repo create my-x-repo --public --source=. --push

# sudo apt update
# sudo apt install gh -y
