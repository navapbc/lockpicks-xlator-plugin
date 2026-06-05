## Shell Commands

On macOS, do not use `grep -P` (PCRE). Use `grep -E` (extended regex) or `perl -ne` instead.

## Clerk / Catala invocation directory

All `clerk` and `catala` commands MUST run from the folder containing the `clerk.toml` file — by convention that is `domains/<domain>/specs/` (source authoring) or `domains/<domain>/output/` (legacy rego/civil-transpile pipeline). Reason: raw `catala` subcommands (`catala dependency-graph`, `catala interpret`) resolve the stdlib via `./_build/libcatala` relative to CWD and do not walk parent directories — running them from a higher directory fails with `Stdlib_en could not be found at "_build/libcatala"`. `clerk` itself is more forgiving but still benefits from the consistent CWD.

When writing a script or skill step that invokes `clerk` or `catala`:
1. `cd` (or `subprocess.run(..., cwd=...)`) into the `specs/` or `output/` folder before invoking.
2. Call `clerk_loop.ensure_catala_bootstrap(<cwd>)` first — it writes a tier-correct `clerk.toml` (if missing) and runs `clerk start` (if `_build/libcatala` is absent) so the stdlib is in place. Idempotent.
3. `clerk.toml` is created **lazily and per-tier**, at the point a directory first needs one — not eagerly by `xlator new-domain` (which now scaffolds empty parent dirs only). `ensure_catala_bootstrap` infers the tier from the directory basename via `clerk_toml_defaults.clerk_toml_for`: a `tests/` dir gets `include_dirs = [".", ".."]` (so fixtures resolve their parent module); every other dir (`specs/`, `output/`) gets `include_dirs = ["."]`. The two default literals live only in `xl-plugin/tools/clerk_toml_defaults.py`.
4. The ensure-step writes `clerk.toml` only when **absent** — it never rewrites an existing file. Existing domains are **not** auto-migrated: a domain whose git-ignored, per-developer `clerk.toml` predates this behavior and carries stale `include_dirs` (e.g. `[]`) must be hand-fixed once. The failing skill (`Required module not found: <X>`) is the signal.
5. The `output/` tier's `[[target]]` block is lazy-injected by `xl-plugin/tools/clerk_target_inject.py` (called from `catala_to_python.sh`) on first `xlator catala-to-python <domain> <module>` invocation. Same lazy + never-rewrite discipline as `clerk.toml` itself: helper parses `> Module`/`> Using` directives across `specs/*.catala_en`, topo-sorts (leaves first), and appends a `[[target]] name = "<module>" modules = [...] backends = ["python"]` block. Existing `[[target]]` blocks with the same `name` are left byte-identical. The same helper also prints the `[project] target_dir` value (defaulting to `_targets`) so the shell script can locate the build output regardless of clerk's per-domain config.

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
