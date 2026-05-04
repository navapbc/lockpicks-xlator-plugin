#!/bin/bash
# Run this script when opening the project in a new environment
# to set up the Xlator plugin and generate .xlator.local.env with CLAUDE_PLUGIN_ROOT.

set -euo pipefail

# Many install scripts add binaries to ~/.local/bin, so ensure it's on PATH from the start
export PATH="$HOME/.local/bin:$PATH"

# --- Resolve project root and $DOMAINS_FULLPATH ---
PROJECT_ROOT="$(git rev-parse --show-toplevel)"
cd $PROJECT_ROOT
echo "Changing directory to PROJECT_ROOT=$PROJECT_ROOT"

find_dot_xlator_conf(){
    # Look for a .xlator.conf dir/symlink somewhere under the PROJECT_ROOT
    local found_python_version_files
    IFS=$'\n' read -d '' -r -a found_python_version_files < <(find "$PROJECT_ROOT" -name ".xlator.conf" 2>/dev/null)
    if [ ${#found_python_version_files[@]} -eq 0 ]; then
        echo "Error: No .xlator.conf found under any folder in $PROJECT_ROOT. Create .xlator.conf in the \$DOMAINS_DIR and run ./xlator_setup.sh in the project root folder." >&2
        exit 5
    elif [ ${#found_python_version_files[@]} -gt 1 ]; then
        echo "Error: Multiple .xlator.conf found under $PROJECT_ROOT: ${found_python_version_files[*]}" >&2
        exit 6
    fi
    # Return the folder that contains the .xlator.conf dir/symlink
    dirname "${found_python_version_files[0]}"
}

if [ -z "${DOMAINS_DIR:-}" ]; then
    FOUND_DIR="$(find_dot_xlator_conf)"
    DOMAINS_DIR="${FOUND_DIR#$PROJECT_ROOT/}"
    export DOMAINS_DIR
    echo "Setting DOMAINS_DIR to '$DOMAINS_DIR'"
    if [ -z "${DOMAINS_DIR:-}" ]; then
        echo "Error: Failed to determine DOMAINS_DIR from .xlator.conf path." >&2
        exit 7
    fi
fi

export DOMAINS_FULLPATH="$PROJECT_ROOT/$DOMAINS_DIR"
if [ ! -d "$DOMAINS_FULLPATH" ]; then
    echo "Error: DOMAINS_FULLPATH directory not found at $DOMAINS_FULLPATH. Run ./xlator_setup.sh in the project root." >&2
    exit 3
fi

# Setting DOMAINS_FULLPATH avoids scripts having to know about both PROJECT_ROOT and DOMAINS_DIR
if [[ "$DOMAINS_FULLPATH" != /* ]]; then
    echo "Error: DOMAINS_FULLPATH is not an absolute path: $DOMAINS_FULLPATH" >&2
    exit 4
fi

# --- Helpers ---

uninstall_xlator() {
    # Removing the marketplace also removes the plugin, so we don't need to uninstall separately
    claude plugin marketplace remove lockpicks-marketplace || true
    rm -rf "$HOME/.claude/plugins/cache/lockpicks-marketplace/xl"

    [ -d "${VIRTUAL_ENV:-}" ] && rm -vf "${VIRTUAL_ENV}/bin/xlator" || true
    rm -vf "$HOME/.local/bin/xlator" || true

    rm -vf "$DOMAINS_FULLPATH/.shared/.plugin" || true

    rm -vf "$PROJECT_ROOT/.xlator.local.env"
}

get_xl_plugin_install_path() {
    claude plugin list --json | uv run --no-project python -c '
import sys, json
plugins = json.load(sys.stdin)
project_root = sys.argv[1]
xl = next((p for p in plugins if p["id"].startswith("xl@") and p.get("projectPath") == project_root), None)
print(xl["installPath"] if xl else "", end="")
' "$PROJECT_ROOT"
}

copy_if_diff() {
    local SRC_FILE="$1"
    local DST_FILE="$2"
    if [ -f "$DST_FILE" ] && cmp -s "$SRC_FILE" "$DST_FILE"; then
        echo "  Skipping (unchanged): $(basename "$DST_FILE")"
        return 1
    else
        if [ -f "$DST_FILE" ]; then
            echo "  Overwriting: $DST_FILE"
        fi
        cp -v "$SRC_FILE" "$DST_FILE"
    fi
}

: ${BRANCH_OR_TAG:="main"}

update_this_script(){
    echo "0. Checking for updates to xlator_setup.sh..."
    local ref
    ref=$(mktemp)
    trap 'rm -f "${ref:-}"' RETURN
    curl -sSL -o "$ref" "https://raw.githubusercontent.com/navapbc/lockpicks-xlator-plugin/${BRANCH_OR_TAG}/repo-template/xlator_setup.sh"
    THIS_SCRIPT="$(realpath "$0")"
    copy_if_diff "$ref" "$THIS_SCRIPT"
}

setup_xlator_plugin() {
    echo "🙂 1. Configuring Xlator plugin (writing .xlator.local.env)"

    if ! command -v claude >/dev/null 2>&1; then
        curl -fsSL https://claude.ai/install.sh | bash
    fi

    # Fortunately, we don't need to authenticate claude to add plugins
    if claude plugins marketplace list --json | grep -q '"lockpicks-marketplace"'; then
        # Update git repo at ~/.claude/plugins/marketplaces/lockpicks-marketplace
        claude plugin marketplace update lockpicks-marketplace
    else
        # Append '#tagOrBranch' to the URL to pin a version, e.g. 'main' or 'v1.2.3'
        claude plugin marketplace add --scope project "https://github.com/navapbc/lockpicks-xlator-plugin.git#${BRANCH_OR_TAG}"
    fi
    echo "  Clearing lockpicks-marketplace cache"
    # Uninstall first to remove the registry entry, then delete cached files.
    # rm -rf alone only removes files; the claude CLI registry retains the old
    # entry, causing plugin list to return a stale path after reinstall.
    claude plugin uninstall xl@lockpicks-marketplace --scope project 2>/dev/null || true
    rm -rf "$HOME/.claude/plugins/cache/lockpicks-marketplace/xl"
    # Install the plugin from the marketplace
    claude plugin install --scope project xl@lockpicks-marketplace

    if ! command -v uv >/dev/null 2>&1; then
        curl -fsSL https://astral.sh/uv/install.sh | sh
    fi

    # CLAUDE_PLUGIN_ROOT is set by Claude Code for hook commands but not other contexts.
    # Persist it so shell scripts can use it too.
    CLAUDE_PLUGIN_ROOT=$(get_xl_plugin_install_path)
    if [ -z "$CLAUDE_PLUGIN_ROOT" ]; then
        echo "Error: Failed to determine CLAUDE_PLUGIN_ROOT from 'claude plugin list'" >&2
        exit 10
    fi

    if [ ! -d "$CLAUDE_PLUGIN_ROOT" ]; then
        echo "Error: CLAUDE_PLUGIN_ROOT directory does not exist: $CLAUDE_PLUGIN_ROOT" >&2
        exit 11
    fi

    cat "$CLAUDE_PLUGIN_ROOT/.claude-plugin/plugin.json"

    {
        echo "# Auto-generated by $0 script. Required for Xlator to function properly."
        echo "export DOMAINS_DIR='${DOMAINS_DIR}'"
        echo "export DOMAINS_FULLPATH='${DOMAINS_FULLPATH}'"
        echo "export CLAUDE_PLUGIN_ROOT='${CLAUDE_PLUGIN_ROOT}'"
    } > "$PROJECT_ROOT/.xlator.local.env"
    cat "$PROJECT_ROOT/.xlator.local.env"
}

setup_tooling() {
    echo "😀 2. Checking opam"
    if [ ! -d "$HOME/.opam" ]; then
        echo "  Initializing opam (this can take 15 minutes)"
        # Catala supports OCaml versions from 4.14.0 up to 5.4.x
        opam init -y -a -c 5.4.1
        date
    fi
    eval "$(opam env)"

    echo "😄 3. Checking for catala/clerk"
    if ! command -v clerk >/dev/null 2>&1; then
        echo "  Installing catala/clerk (this can take 10 minutes) ..."
        opam update && opam install -y catala.1.1.0
        date
    fi
}

ensure_xlator_symlink() {
    local target_dir="$1"
    local dest="$target_dir/xlator"
    local src="$CLAUDE_PLUGIN_ROOT/bin/xlator"
    if [ ! -L "$dest" ] || [ "$(readlink "$dest")" != "$src" ]; then
        ln -vsnf "$src" "$dest"
    fi
}

setup_misc() {
    echo "😊 4. Wrapping up: VS Code settings, put xlator in PATH, symlinks"
    mkdir -p "$PROJECT_ROOT/.vscode"
    copy_if_diff "$CLAUDE_PLUGIN_ROOT/core/ruleset.schema.json" "$PROJECT_ROOT/.vscode/ruleset.schema.json" || true

    # Claude's PATH should have $CLAUDE_PLUGIN_ROOT/bin included, so xlator should be available for Claude.
    # For other contexts (VS Code terminal, user shell), create a symlink to the xlator script in a folder that's on the PATH.
    # .venv/bin is on the PATH, so create a symlink to the xlator script there
    [ -d "${VIRTUAL_ENV:-}" ] && ensure_xlator_symlink "$VIRTUAL_ENV/bin"
    # as well as ~/.local/bin for the user shell (and VS Code terminal if it's launched from the user shell)
    ensure_xlator_symlink "$HOME/.local/bin"

    # Provides easy access to the plugin folder for reference
    [ -e "$DOMAINS_FULLPATH/.shared/.plugin" ] || ln -vsnf "$CLAUDE_PLUGIN_ROOT" "$DOMAINS_FULLPATH/.shared/.plugin"
}

# --- Main ---

if [ "$1" = "uninstall" ]; then
    echo "⚠️  Uninstalling Xlator marketplace and plugin..."
    uninstall_xlator
    exit 0
fi

# If shell is interactive, check for updates to this script before proceeding with the setup
if [ -t 1 ] && [ ! "${SKIP_SCRIPT_UPDATE:-}" = "true" ] && update_this_script; then
    echo "xlator-setup.sh was updated; aborting current run. Please re-run."
    exit 10
fi

setup_xlator_plugin
setup_tooling
setup_misc
echo "🤩 Setup complete. Remember to commit updated files to git."
