# Welcome to Xlator

This repo was created using `create_git_repo.sh`.

> **Want to add Xlator to an existing repo instead?**
>
> You can copy the files in this repo into your own repo and be able to use the Xlator Claude Code plugin. Remember to merge the root-level CLAUDE.md file with your own.
>
> ```
> .
> ├── .devcontainer/
> ├── .vscode/
> ├── domains/ - This folder name is named by the domains_subfolder_name argument to `download.sh,` default `domains.`
> ├── xlator.conf
> ├── CLAUDE.md
> └── xlator_setup.sh
> ```

## What did `create_git_repo.sh` do?

The `create_git_repo.sh` script performed the following to create this repo:
1. Created `xlator.conf` based on the specified `DOMAINS_DIR`
2. Copied the following files from the plugin's `repo-template`:
    1. `.devcontainer/devcontainer.json` for running as a devcontainer in VS Code or on the web in GitHub Codespaces
    2. `.vscode/settings.json` to configure CIVIL ruleset schema and enable auto-approve and `bypassPermissions` for Claude Code
    3. the root-level `CLAUDE.md` file to provide Xlator-specific instructions

## Open repo in an IDE to complete setup

Now that the repo is created, you can open the repo in GitHub Codespaces (recommended) or in VS Code as a devcontainer.

> In VS Code, a few toaster notifications will pop up on the lower-right. Click on the "Reopen in Container" button. Alternatively, use the command palette (`Cmd-Shift-p`) to run "Dev Containers: Reopen in Container".

A `postStartCommand` devcontainer configuration will run `xlator_setup.sh`, which does the following:

1. Setup `uv` virtual environment under `$XLATOR_UV_BASEDIR` to install Python and dependencies for Xlator scripts
2. Initialize `opam` and install `catala`
3. Create `.xlator.local.env` to set `CLAUDE_PLUGIN_ROOT`, which is used by `xlator` scripts and slash commands
4. Create symlink to the `xlator` plugin installation folder for reference

## Authenticate Claude

To use the Xlator plugin, Claude Code must be authenticated. For reliable authentication, do exactly as follows:

1. Open the Claude Code panel in the IDE
2. Click `Claude.ai Subscription`. A pop-up window will show a URL -- ignore it since it will try to automatically open `http://localhost...` after authorizing, and the authentication code isn't accessible. Instead, close the window (by clicking the 'Copy' button), and in the Claude Code panel, click the copy icon next to the URL to copy *this URL*.
3. Paste the URL into a web browser
    1. Follow the instructions and click `Authorize`
    2. Click `Copy Code` to copy the long authentication code provided
    3. (This browser tab can be closed once you've authenticated Claude Code)
4. Back in the Claude Code panel in the IDE, paste the authentication code and click `Continue`

You should now be able to interact with Claude Code. Test by pasting any of the following in the input field:
- `What is today's date?`
- `What are the values of $CLAUDE_PLUGIN_ROOT and $DOMAINS_DIR`
- `Which python version is installed?` -- This is relevant for your repo
- `Which python version is being used by the 'xl' Claude plugin?`

## Confirm setup by creating a new domain

Run `/xl:new-domain` in Claude Code and follow the instructions.

## Xlator Observability

To capture user interactions, `logs/session.jsonl` files are created under `.shared` and `<domain>` subfolders. These logs can be useful for debugging and user support.
To disable these logs, add `export OBSERVE_HOOK_DISABLED=true` to `xlator.conf`.
