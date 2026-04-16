
This repo was created using `create_git_repo.sh`.

## Have an existing repo?

You can copy the files in this repo into your own repo and be able to use the Xlator Claude Code plugin. Remember to merge the root-level CLAUDE.md file with your own.

## create_git_repo.sh

The `create_git_repo.sh` script performed the following to create this repo:
- Create `xlator.conf` based on the specified `DOMAINS_DIR`
- Copy files from the plugin's `repo-template` and customize the following files:
    - Customize `.devcontainer/devcontainer.json` for running as a container in VSCode or on the web in GitHub Codespaces
    - Customize `.vscode/settings.json` to configure CIVIL ruleset schema, enable auto-approve and `bypassPermissions` for Claude Code, and set the Python virtual environment path
    - Customize the root-level `CLAUDE.md` file to provide Xlator-specific instructions

## Open repo in an IDE to complete setup

Next, open the repo in GitHub Codespaces or in VSCode as a devcontainer.
A `postStartCommand` devcontainer configuration will run `xlator_setup.sh`, which does the following:
- Copy/update code-setup files (`.gitignore`, `pyproject.toml`, `.python-version`, `uv.lock`) to the `DOMAINS_DIR` (specified in `xlator.conf`)
    - TODO: Should .venv and other files be set up in $CLAUDE_PLUGIN_DATA?
- Run `uv sync` to install Python and dependencies in a virtual environment `.venv` for Xlator scripts
- Initialize `opam` and install `catala`
- Create `.xlator.local.env` to set `CLAUDE_PLUGIN_ROOT`, which is used by `xlator` scripts and slash commands
- Create symlink to `xlator` shim script, which empowers users to run scripts directly

## Authenticate with Claude

To use the Xlator plugin, Claude Code must be authenticated. For reliable authentication, do exactly as follows:
- Open the Claude Code panel in the IDE
- Click `Claude.ai Subscription`. A pop-up window will show a URL -- ignore it. Close the window.
- In the Claude Code panel, click the copy icon to copy the provided URL
- Paste the URL into a web browser
    - Follow the instructions and click `Authorize`
    - Click `Copy Code` to copy the long authentication code provided
    - (This browser tab can be closed once you've authenticated Claude Code)
- Back the Claude Code panel in the IDE, paste the authentication code and click `Continue`

You should now be able to interact with Claude Code. Test by pasting any of the following in the input field:
- `What is today's date?`
- `What is the value of $CLAUDE_PLUGIN_ROOT`
- `/xl:new-domain`

### Xlator Observability feature creates session.jsonl

To capture user interactions, `logs/session.jsonl` files are created under `_global` and `<domain>` subfolders.
These logs can be useful for user support when needed.
