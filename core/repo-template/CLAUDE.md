## CLAUDE_PLUGIN_ROOT variable

If `XLATOR_REPO_PATH` is set, then set `CLAUDE_PLUGIN_ROOT` to the value of `XLATOR_REPO_PATH`.

Otherwise set `${CLAUDE_PLUGIN_ROOT}` to be the user's plugin cached install path for the `xl` (Xlator) plugin, commonly under `~/.claude/plugins/cache/xlator-marketplace/xl/<version>`. Verify `${CLAUDE_PLUGIN_ROOT}`:

```bash
ls "${CLAUDE_PLUGIN_ROOT}/xlator.py"
```

If not found → print: "Cannot locate Xlator's plugin root folder. Please reinstall the xlator plugin: `claude plugin install --scope local xl@xlator-marketplace`" and stop.


## Xlator Claude Code Plugin

Read rules/CLAUDE.md when an `xl` (xlator) Claude Code plugin command runs.
