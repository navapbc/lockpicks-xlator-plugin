## Shell Commands

On macOS, do not use `grep -P` (PCRE). Use `grep -E` (extended regex) or `perl -ne` instead.

## Shell scripts

When writing or modifying shell scripts, ensure commands are portable such that they work on MacOS and Linux.
Use bash-specific built-in commands and features, rather than `sed`, `grep`, and `awk`.

## Skill development

Any signficant non-AI work (Bash, CLI commands, enumerate, copy, manifest, delete) should run as a shell or Python script for determinism and manual execution.
Any AI work will be described in the `SKILL.md` file.

## Naming

* By convention, skill names should use `-` (kebab case) instead of `_` (snake case).
* Generally, folder and file names should be use snake case. Do not use `-` to avoid misinterpretation as a substraction symbol in calculation expressions.

## Don't migrate old files

Do not assume code is needed to migrate or handle old files. Ask the user if they want migration code or code that looks in old locations. Reason: this code is not yet in production and still in experimental stages. Such migration code complicates the logic and adds extraneous behavior.

## Testing

When fixing transpiler bugs, always run the full test suite (all domains/programs) after each fix — not just the specific failing test. Transpiler changes frequently cause regressions in other modules.

## Project Terminology

For brainstorm and plan documents, use the project's exact terminology: 'ruleset module' (not 'sub-ruleset', not 'submodule'), 'ruleset group' (not 'workflow stage'), 'CIVIL' for the DSL name. Ask for clarification if domain terminology is ambiguous rather than guessing.

## Git

Never commit directly to the `main` or `dev` branches.

## CIVIL DSL versioning

When the CIVIL DSL is updated, bump the version number and in `README-dev.md` "Architecture Notes" section, append a bullet describing the new version.

## Xlator plugin versioning

Increment the version number in `xl-plugin/.claude-plugin/plugin.json` when a git commit is made or PR is created. Use Semantic Versioning guidelines:
* increment the MAJOR version number for new features that break backwards compatibility
* increment the MINOR version number for new features that do not break backwards compatibility
* increment the PATCH version number for all other changes in behavior
* Do not increment version numbers if documentation or file formatting is changed. CLAUDE.md is considered non-documentation code since it affects how the AI behaves.
