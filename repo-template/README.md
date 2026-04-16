
This repo was created using `create_git_repo.sh`.

## Want to add Xlator to an existing repo?

You can copy the files in this repo into your own repo and be able to use the Xlator Claude Code plugin. Remember to merge the root-level CLAUDE.md file with your own.

## What did `create_git_repo.sh` do?

The `create_git_repo.sh` script performed the following to create this repo:
- Created `xlator.conf` based on the specified `DOMAINS_DIR`
- Copied files from the plugin's `repo-template` and customized the following files:
    - `.devcontainer/devcontainer.json` for running as a devcontainer in VSCode or on the web in GitHub Codespaces
    - `.vscode/settings.json` to configure CIVIL ruleset schema, enable auto-approve and `bypassPermissions` for Claude Code, and set the Python virtual environment path
    - the root-level `CLAUDE.md` file to provide Xlator-specific instructions

### How can I create a repo like this one?

Use the `download.sh` script as follows:

```bash
curl -s https://raw.githubusercontent.com/navapbc/lockpicks-xlator-plugin/main/download.sh | bash -s -- [new_repo_path] [domains_subfolder_name]
```

This script downloads the Xlator repo template and sets up a new git repository by running `create_git_repo.sh` if arguments are provided. The new repository is created at `new_repo_path` with a subfolder named `domains_subfolder_name` (defaults to 'domains').
If arguments are not provided, the template will be downloaded and left in a folder named `xlator-repo-creator` for manual execution of `create_git_repo.sh`.

After repo creation, follow the instructions in the new repo's `README.md` (same as this file) to open the repo in an IDE and complete the setup.

## Open repo in an IDE to complete setup

Now that the repo is created, you can open the repo in GitHub Codespaces or in VSCode as a devcontainer.
A `postStartCommand` devcontainer configuration will run `xlator_setup.sh`, which does the following:
- Copy/update code-setup files (`.gitignore`, `pyproject.toml`, `.python-version`, `uv.lock`) to the `DOMAINS_DIR` (specified in `xlator.conf`)
    - TODO: Should .venv and other files be set up in $CLAUDE_PLUGIN_DATA?
- Run `uv sync` to install Python and dependencies in a virtual environment `.venv` for Xlator scripts
- Initialize `opam` and install `catala`
- Create `.xlator.local.env` to set `CLAUDE_PLUGIN_ROOT`, which is used by `xlator` scripts and slash commands
- Create symlink to the `xlator` plugin installation folder for reference

## Authenticate Claude

To use the Xlator plugin, Claude Code must be authenticated. For reliable authentication, do exactly as follows:
- Open the Claude Code panel in the IDE
- Click `Claude.ai Subscription`. A pop-up window will show a URL -- ignore it since it will try to automatically open `http://localhost...` after authorizing, and the authentication code isn't accessible. Instead, close the window and click the copy icon next to the URL in the Claude Code panel to copy *this URL*.
- Paste the URL into a web browser
    - Follow the instructions and click `Authorize`
    - Click `Copy Code` to copy the long authentication code provided
    - (This browser tab can be closed once you've authenticated Claude Code)
- Back in the Claude Code panel in the IDE, paste the authentication code and click `Continue`

You should now be able to interact with Claude Code. Test by pasting any of the following in the input field:
- `What is today's date?`
- `What are the values of $CLAUDE_PLUGIN_ROOT and $DOMAINS_DIR`
- `Which python version is installed?` -- This is relevant for your repo
- `Which python version is being used by the 'xl' Claude plugin?`
- `/xl:new-domain`

## Xlator Observability

To capture user interactions, `logs/session.jsonl` files are created under `_global` and `<domain>` subfolders. These logs can be useful for debugging and user support.
To disable these logs, add `export OBSERVE_HOOK_DISABLED=true` to `xlator.conf`.
