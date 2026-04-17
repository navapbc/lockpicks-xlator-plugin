
## CLAUDE_PLUGIN_ROOT variable

If `.xlator.local.env` is not found in the project root → run `./xlator_setup.sh` (to create the file), then try again.

Source `.xlator.local.env` to set the `$CLAUDE_PLUGIN_ROOT` variable, used by shell scripts and slash commands.

## xlator.conf

If `xlator.conf` is not found in the project root → print: "File `xlator.conf` not found! Create `xlator.conf` in the project root, then try again." and stop.

## DOMAINS_DIR

Set the `$DOMAINS_DIR` environment variable to `$DOMAINS_DIR_VALUE`, which is relative the project root.

## Xlator Claude Code Plugin

Read `$DOMAINS_DIR_VALUE/CLAUDE.md` when a slash command from the `xl` (Xlator) Claude Code plugin runs.
