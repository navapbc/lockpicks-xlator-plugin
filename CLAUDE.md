## Catala Conventions
When working with Catala code, always use Catala semantics and syntax — never Rego. Double-check that generated tests, transpiler output, and examples use Catala conventions (e.g., `Using` not `Include`, correct module/entity prefixes).

## Shell Commands

On macOS, do not use `grep -P` (PCRE). Use `grep -E` (extended regex) or `perl -ne` instead.

## Shell scripts

When writing or modifying shell scripts, ensure commands are portable such that they work on MacOS and Linux.
Use bash-specific built-in commands and features, rather than `sed`, `grep`, and `awk`.

## Testing

When fixing transpiler bugs, always run the full test suite (all domains/programs) after each fix — not just the specific failing test. Transpiler changes frequently cause regressions in other modules.

## Project Terminology

For brainstorm and plan documents, use the project's exact terminology: 'ruleset module' (not 'sub-ruleset', not 'submodule'), 'ruleset group' (not 'workflow stage'), 'CIVIL' for the DSL name. Ask for clarification if domain terminology is ambiguous rather than guessing.

## Git

Never commit to the `main` branch.

## CIVIL DSL versioning

When the CIVIL DSL is updated, bump the version number and in `README-dev.md` "Architecture Notes" section, append a bullet describing the new version.
