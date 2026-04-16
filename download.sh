#!/usr/bin/env bash
# Usage: curl -s https://raw.githubusercontent.com/navapbc/lockpicks-xlator-plugin/main/download.sh | bash -s -- [new_repo_folder_name] [domains_subfolder_name]
#
# This script downloads the Xlator repo template and sets up a new git repository with it.
# The new repository will be created in a folder named [new_repo_folder_name] with
# a subfolder named [domains_subfolder_name] (defaults to 'domains').
# If arguments are not provided, the template will be downloaded and left in a folder named 'xlator-repo-creator' for manual use.

set -euo pipefail

GIT_REPO=https://github.com/navapbc/lockpicks-xlator-plugin.git

CURR_DIR=$(pwd)
TARGET_DIR="$CURR_DIR/xlator-repo-creator"
if [ -e "$TARGET_DIR" ]; then
    echo "Error: $TARGET_DIR already exists. Remove it and re-run." >&2
    exit 1
fi

TEMP_DIR=$(mktemp -d)
CLONE_DIR="$TEMP_DIR/lockpicks-xlator-plugin"
# Ensure TEMP_DIR is cleaned up on exit
trap 'rm -rf "$TEMP_DIR"' EXIT

echo "## 1. Downloading Xlator repo-template folder..."
git clone --no-checkout --depth=1 --filter=tree:0 "$GIT_REPO" "$CLONE_DIR"
cd "$CLONE_DIR"
git sparse-checkout set --no-cone /repo-template
git checkout

echo ""
echo "## 2. Installing to $TARGET_DIR..."
mv -v repo-template "$TARGET_DIR"

# Return to original directory to create new repo from there
cd "$CURR_DIR"

echo ""
if [ "${1:-}" ]; then
    echo "## 3. Running create_git_repo.sh with arguments: $*"
    "$TARGET_DIR/create_git_repo.sh" "$@"

    # Cleanup by removing the downloaded template repo after setup
    # Don't need to keep it; better to grab the latest version from GitHub
    echo ""
    echo "## 4. Cleaning up temporary files..."
    rm -rf "$TARGET_DIR"
else
    # If no arguments provided, just leave the template in TARGET_DIR for manual use to create multiple repos
    echo "Usage: $TARGET_DIR/create_git_repo.sh <new_repo_folder_name> [domains_subfolder_name]"
fi
