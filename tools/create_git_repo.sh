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

XLATOR_MARKETPLACE_REPO="./"
CLAUDE_PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CORE_DIR="$CLAUDE_PLUGIN_ROOT/core"

# --- 1. Initialize repo from template ---
mkdir -p "$NEW_REPO_PATH"
NEW_REPO_PATH="$(cd "$NEW_REPO_PATH" && pwd)"
cd "$NEW_REPO_PATH"
cp -a "$CORE_DIR/repo-template/." .

# Replace "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python" in devcontainer.json
perl -i -pe "s|\"python.defaultInterpreterPath\": \"[^\"]*\"|\"python.defaultInterpreterPath\": \"\${workspaceFolder}/${DOMAINS_DIR}/.venv/bin/python\"|" .devcontainer/devcontainer.json

git init -b main
git add .
git commit -m "Initial commit from Xlator create_git_repo.sh"
echo "Git repository created at $NEW_REPO_PATH"

# --- 2. Configure and set up Xlator (just like /xl:setup does) ---
cat > xlator.conf << EOF
export DOMAINS_DIR="${DOMAINS_DIR}"
EOF

"${CLAUDE_PLUGIN_ROOT}/xlator" setup

git add .
git commit -m "Installed Xlator plugin and set up '$DOMAINS_DIR' folder for Xlator"
echo "Installed Xlator plugin and set up '$DOMAINS_DIR' folder for Xlator"

# --- 3. Install Xlator plugin ---
# curl -fsSL https://claude.ai/install.sh | bash

# if ! command -v claude >/dev/null 2>&1; then
#     echo "Error: 'claude' CLI not found. Install Claude Code and try again." >&2
#     exit 3
# fi

# if ! claude auth status >/dev/null 2>&1; then
#     echo "Error: Not logged in to Claude. Run 'claude auth login' and try again." >&2
#     exit 4
# fi

# sudo apt update
# sudo apt install gh -y

# claude plugin marketplace add "$XLATOR_MARKETPLACE_REPO"
# claude plugin install --scope local xl@xlator-marketplace

