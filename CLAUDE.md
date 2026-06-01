## Shell Commands

On macOS, do not use `grep -P` (PCRE). Use `grep -E` (extended regex) or `perl -ne` instead.

## Shell scripts

When writing or modifying shell scripts, ensure commands are portable such that they work on MacOS and Linux.
Use bash-specific built-in commands and features, rather than `sed`, `grep`, and `awk`.

## Skill development

Any skill steps that require multi-operation deterministic non-AI work (e.g., Bash, CLI commands, enumerate, copy, manifest, delete) should run as a shell or Python script for determinism and manual execution.
Any skill steps that require AI should be described in the `SKILL.md` file.

## Naming

* By convention, skill names should use `-` (kebab case) instead of `_` (snake case).
* Generally, folder and file names should be use snake case. Do not use `-` to avoid misinterpretation as a substraction symbol in calculation expressions.

## Don't migrate old files

Do not assume code is needed to migrate or handle old files. Ask the user if they want migration code or code that looks in old locations. Reason: this code is not yet in production and still in experimental stages. Such migration code complicates the logic and adds extraneous behavior.

## Testing

When fixing AI-emitted Catala bugs (typecheck failures, clerk-loop divergences, `/catala-emit-tests` test-fixture issues), always run the full test suite (all domains/programs) after each fix — not just the specific failing test. Changes to shared tooling frequently cause regressions in other modules.

## Project Terminology

For brainstorm and plan documents, use the project's exact terminology: 'ruleset module' (not 'sub-ruleset', not 'submodule'), 'ruleset group' (not 'workflow stage'). 'Catala' is the source spec language; see [xl-plugin/core/catala-authoring-quickref.md](xl-plugin/core/catala-authoring-quickref.md) for the AI authoring reference. Ask for clarification if domain terminology is ambiguous rather than guessing.

## Git

Never commit directly to the `main` or `dev` branches.

## Xlator plugin versioning

Increment the version number in `xl-plugin/.claude-plugin/plugin.json` when a git commit is made or PR is created. Use Semantic Versioning guidelines:
* increment the MAJOR version number for new features that break backwards compatibility
* increment the MINOR version number for new features that do not break backwards compatibility
* increment the PATCH version number for all other changes in behavior
* Do not increment version numbers if documentation or file formatting is changed. CLAUDE.md is considered non-documentation code since it affects how the AI behaves.

## Naming-manifest versioning

Bump the `version:` field in every `domains/*/specs/naming-manifest.yaml` when the manifest schema changes. "Schema" here means: the set of values the validator (`xl-plugin/tools/merge_naming_manifest.py`) accepts for `type:`, the set of allowed top-level or per-entry keys, or the semantics of any existing field. Use Semantic Versioning guidelines:
* increment the MAJOR version number when the change rejects manifests that the prior version accepted (e.g., removing a `type:` value, removing an allowed key, tightening a value constraint).
* increment the MINOR version number when the change accepts manifests the prior version would have rejected but still accepts every prior manifest (e.g., adding a new `type:` value, adding a new optional key).
* increment the PATCH version number for behavior changes that don't alter the accept/reject boundary (e.g., error-message wording, internal validator refactors that preserve identical behavior).
* Do not bump `version:` for analyst content edits — `description:` prose, `synonyms:` entries, `policy_phrase:` updates, new field rows, renamed identifiers. Those are routine authoring, not schema changes.

When you edit the schema, update three things together: the validator's accept set, the `version:` field on every in-tree manifest, and this section's MAJOR/MINOR/PATCH bullets if the rules for bumping themselves change.
