
## CLAUDE_PLUGIN_ROOT variable

If `.xlator.local.env` is not found in the project root → print: "File `.xlator.local.env` not found! Please run `/xl:setup` (to create the file), then try again." and stop.

If `.xlator.local.env` exists in the project root, then source it to set the `$CLAUDE_PLUGIN_ROOT` environment variable, used by shell scripts and slash commands.

## Xlator plugin for Claude Code

To run `/xl:setup`, the Xlator plugin must be installed: `claude plugin install --scope local xl@xlator-marketplace`" and stop.


## xlator.conf

A `xlator.conf` file should exist in the project root folder. If not, stop.
`xlator.conf` was created by `create_git_repo.sh` or `/xl:setup`.

## DOMAINS_DIR

Set the `DOMAINS_DIR` variable to `$DOMAINS_DIR`, which is relative the project root.

## Xlator Claude Code Plugin

Read $DOMAINS_DIR/CLAUDE.md when a slash command from the `xl` (xlator) Claude Code plugin runs.
