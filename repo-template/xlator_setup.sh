#!/bin/bash
# Run this script when opening the project in a new environment
# to set up the Xlator plugin and generate .xlator.local.env with CLAUDE_PLUGIN_ROOT.

set -euo pipefail

# Many install scripts add binaries to ~/.local/bin, so ensure it's on PATH from the start
export PATH="$HOME/.local/bin:$PATH"

# --- Resolve project root ---

if [ -f "xlator.conf" ]; then
    PROJECT_ROOT="$(pwd)"
elif git rev-parse --show-toplevel >/dev/null 2>&1; then
    PROJECT_ROOT="$(git rev-parse --show-toplevel)"
else
    echo "Error: Cannot find project root! Create xlator.conf in the project root." >&2
    exit 1
fi

if [ ! -f "$PROJECT_ROOT/xlator.conf" ]; then
    echo "Error: $PROJECT_ROOT/xlator.conf not found! Create xlator.conf in the project root." >&2
    exit 2
fi

# Load DOMAINS_DIR and other project variables
source "$PROJECT_ROOT/xlator.conf"
mkdir -p "$PROJECT_ROOT/$DOMAINS_DIR"

# --- Determine uv's base directory and clean up old files once CLAUDE_PLUGIN_DATA is set ---
# CLAUDE_PLUGIN_DATA is only available when called as a hook from Claude.
# Until then, we set up an initial uv base directory to be AI-vendor-independent,
# and then switch to using CLAUDE_PLUGIN_DATA once it's available.
UV_PROJECT_FILES="pyproject.toml .python-version uv.lock"

# Default uv project folder, until CLAUDE_PLUGIN_DATA is set
DEFAULT_XLATOR_UV_BASEDIR="$PROJECT_ROOT/$DOMAINS_DIR/.shared"

if [ "${CLAUDE_PLUGIN_DATA:-}" ]; then
    # This is specific to Claude
    # CLAUDE_PLUGIN_DATA is typically a subfolder under ~/.claude/plugins/data/...
    XLATOR_UV_BASEDIR="$CLAUDE_PLUGIN_DATA"

    # Clean up .venv and uv project files from the default base directory
    if [ -d "$DEFAULT_XLATOR_UV_BASEDIR/.venv" ]; then
        rm -rf "$DEFAULT_XLATOR_UV_BASEDIR/.venv"
    fi
    for F in $UV_PROJECT_FILES; do
        [ -f "$DEFAULT_XLATOR_UV_BASEDIR/$F" ] && rm -f "$DEFAULT_XLATOR_UV_BASEDIR/$F"
    done

    # Create a symlink to the new uv base directory for tools (VS Code) looking at the default location
    ln -snf "$XLATOR_UV_BASEDIR/.venv" "$DEFAULT_XLATOR_UV_BASEDIR/.venv"
else
    # Source .xlator.local.env to preserve XLATOR_UV_BASEDIR and avoid unnecessary changes to it
    if [ -f "$PROJECT_ROOT/.xlator.local.env" ]; then
        eval "$(grep XLATOR_UV_BASEDIR "$PROJECT_ROOT/.xlator.local.env")"
    fi
    if [ "${XLATOR_UV_BASEDIR:-}" ] && [ -d "$XLATOR_UV_BASEDIR/.venv" ]; then
        echo "Using preset XLATOR_UV_BASEDIR from .xlator.local.env: $XLATOR_UV_BASEDIR"
    else
        XLATOR_UV_BASEDIR="$DEFAULT_XLATOR_UV_BASEDIR"
        mkdir -p "$XLATOR_UV_BASEDIR"
    fi
fi

# --- Helpers ---

local_env_written_today() {
    local env_file="$PROJECT_ROOT/.xlator.local.env"
    [ -f "$env_file" ] || { echo "false"; return; }
    local ref
    ref=$(mktemp)
    trap 'rm -f "${ref:-}"' RETURN
    touch -t "$(date +%Y%m%d)0000" "$ref"
    if find "$env_file" -newer "$ref" | grep -q .; then
        echo "true"
    else
        echo "false"
    fi
}

get_xl_plugin_install_path() {
    claude plugin list --json | uv run --no-project python -c '
import sys, json
plugins = json.load(sys.stdin)
xl = next((p for p in plugins if p["id"].startswith("xl@")), None)
print(xl["installPath"] if xl else "", end="")
'
}

copy_if_diff() {
    local SRC_FILE="$1"
    local DST_FILE="$2"
    if [ -f "$DST_FILE" ] && cmp -s "$SRC_FILE" "$DST_FILE"; then
        echo "  Skipping (unchanged): $DST_FILE"
    else
        if [ -f "$DST_FILE" ]; then
            echo "  Overwriting: $DST_FILE"
        fi
        cp -v "$SRC_FILE" "$DST_FILE"
    fi
}

SETUP_TODAY=$(local_env_written_today)

setup_xlator_plugin() {
    echo "🙂 1. Configuring Xlator plugin (writing .xlator.local.env)..."

    if ! command -v claude >/dev/null 2>&1; then
        curl -fsSL https://claude.ai/install.sh | bash
    fi

    # Skip plugin install if .xlator.local.env was already written today
    if [ "$SETUP_TODAY" = "true" ] && claude plugin list --json | grep -q '"xl@lockpicks-marketplace"'; then
        echo "  (Skipping Xlator plugin update since .xlator.local.env was already written today)"
    else
        # Fortunately, we don't need to authenticate claude to add plugins
        # Append '#tagOrBranch' to the URL to pin a version, e.g. 'main' or 'v1.2.3'
        claude plugin marketplace add https://github.com/navapbc/lockpicks-xlator-plugin.git
        claude plugin install xl@lockpicks-marketplace --scope project

        # Helpful for code generation
        claude plugin marketplace add https://github.com/EveryInc/every-marketplace.git
        claude plugin install compound-engineering@compound-engineering-plugin --scope project
    fi

    if ! command -v uv >/dev/null 2>&1; then
        curl -fsSL https://astral.sh/uv/install.sh | sh
    fi

    # CLAUDE_PLUGIN_ROOT is set by Claude Code for hook commands but not other contexts.
    # Persist it so shell scripts and slash commands can use it too.
    CLAUDE_PLUGIN_ROOT=$(get_xl_plugin_install_path)
    if [ -z "$CLAUDE_PLUGIN_ROOT" ]; then
        echo "Error: Failed to determine CLAUDE_PLUGIN_ROOT from 'claude plugin list'" >&2
        exit 10
    fi

    if [ ! -d "$CLAUDE_PLUGIN_ROOT" ]; then
        echo "Error: CLAUDE_PLUGIN_ROOT directory does not exist: $CLAUDE_PLUGIN_ROOT" >&2
        exit 11
    fi

    {
        echo "# Auto-generated by $0 script. Required for Xlator to function properly."
        echo "export CLAUDE_PLUGIN_ROOT='${CLAUDE_PLUGIN_ROOT}'"
        echo "# Make xlator script easily accessible for Claude so it doesn't have to search for it"
        # Intentionally don't resolve variables; echo exactly this to the file
        echo 'export PATH="$CLAUDE_PLUGIN_ROOT:$PATH"'
        echo "export XLATOR_UV_BASEDIR=\"$XLATOR_UV_BASEDIR\""
    } > "$PROJECT_ROOT/.xlator.local.env"
}

setup_tooling() {
    echo "🙂 2. Copying uv project files to $XLATOR_UV_BASEDIR ..."
    RAW_GIT_REPO=https://raw.githubusercontent.com/navapbc/lockpicks-xlator-plugin/main
    for F in $UV_PROJECT_FILES; do
        if [ -f "$XLATOR_UV_BASEDIR/$F" ]; then
            echo "  Skipping existing file (Delete file to update it): $XLATOR_UV_BASEDIR/$F"
        else
            echo "  Downloading $F to $XLATOR_UV_BASEDIR"
            curl -sSL -o "$XLATOR_UV_BASEDIR/$F" "$RAW_GIT_REPO/$F"
        fi
    done

    echo "😀 3. Creating Python virtual environment in $XLATOR_UV_BASEDIR and installing dependencies..."
    # Remove old .venv symlink if it doesn't exist so that uv can create a new one
    [ -L "$XLATOR_UV_BASEDIR/.venv" ] && [ ! -e "$XLATOR_UV_BASEDIR/.venv" ] && rm -f "$XLATOR_UV_BASEDIR/.venv"
    # 'uv sync' installs the version pinned in .python-version into a local .venv
    uv sync --directory "$XLATOR_UV_BASEDIR"
    . "$XLATOR_UV_BASEDIR/.venv/bin/activate"

    echo "😅 4. Initializing opam (if needed, this can take 10 minutes)..."
    if [ ! -d "$HOME/.opam" ]; then
        # Catala supports OCaml versions from 4.14.0 up to 5.4.x
        opam init -y -a -c 5.4.1
        date
    fi
    eval "$(opam env)"

    echo "😄 5. Installing Catala/clerk (if missing, this can take 10 minutes)..."
    if ! command -v clerk >/dev/null 2>&1; then
        opam update && opam install -y catala.1.1.0
        date
    fi
}

setup_misc() {
    echo "😊 6. Wrapping up: VS Code settings, put xlator in PATH, .plugin symlink, ..."
    mkdir -p "$PROJECT_ROOT/.vscode"
    copy_if_diff "$CLAUDE_PLUGIN_ROOT/core/ruleset.schema.json" "$PROJECT_ROOT/.vscode/ruleset.schema.json"

    # Make the xlator script easily accessible for Claude so it doesn't have to search for it
    # .venv/bin is on the PATH, so create a symlink to the xlator script there
    [ -e "$XLATOR_UV_BASEDIR/.venv/bin/xlator" ] || ln -snf "$CLAUDE_PLUGIN_ROOT/xlator" "$XLATOR_UV_BASEDIR/.venv/bin/xlator"
    # If running in container, also create the symlink in a PATH folder commonly used for the bash terminal
    [ -e /.dockerenv ] && [ -e "$HOME/.local/bin/xlator" ] || ln -snf "$CLAUDE_PLUGIN_ROOT/xlator" "$HOME/.local/bin/xlator"

    # Provides easy access to the plugin folder for reference
    [ -e "$PROJECT_ROOT/$DOMAINS_DIR/.shared/.plugin" ] || ln -snf "$CLAUDE_PLUGIN_ROOT" "$PROJECT_ROOT/$DOMAINS_DIR/.shared/.plugin"
}

# --- Main ---

date
setup_xlator_plugin
setup_tooling
setup_misc
echo "🤩 Setup complete. Remember to commit updated files to git."
