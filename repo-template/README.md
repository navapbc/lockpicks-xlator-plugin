
This repo was created using `create_git_repo.sh`.

## Have an existing repo?

You can merge the files in this repo with your own repo and still be able to use the Xlator Claude Code plugin. Just remember to merge the root-level CLAUDE.md file.

## create_git_repo.sh

The script performed the following to create this repo:
- Create `xlator.conf` based on the specified `DOMAINS_DIR`
- Copy over files from the plugin's `repo-template` and customize the following files:
    - Customize `.devcontainer/devcontainer.json` for running as a container in VSCode or on the web in GitHub Codespaces
    - Customize `.vscode/settings.json` to configure CIVIL ruleset schema, enable auto-approve and `bypassPermissions` for Claude Code, and set the Python virtual environment path
    - Customize the root-level `CLAUDE.md` file to provide Xlator-specific instructions

## Open repo
Next, open the repo in VSCode or GitHub Codespaces.
A `postStartCommand` devcontainer configuration will run `xlator_setup.sh`, which does the following:
    - Copy/update code-setup files (`.gitignore`, `CLAUDE.md`, `mise.toml`, `pyproject.toml`, `.python-version`, `uv.lock`) to the specified `DOMAINS_DIR`
        - TODO: Should .venv and other files be set up in $CLAUDE_PLUGIN_DATA?
    - Run `uv sync` to install Python in a virtual environment `.venv` for Xlator scripts
    - Initialize `opam` and install `catala`
    - Create `.xlator.local.env` to set `CLAUDE_PLUGIN_ROOT`, which is used by `xlator` scripts and slash commands
    - Create symlink to `xlator` shim script, which empowers users to run scripts directly

