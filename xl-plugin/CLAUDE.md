## Environment variables

The `.xlator.local.env` file exports `$DOMAINS_DIR` and other environment variables used by shell scripts and AI skills.

If `$DOMAINS_DIR` is unknown, read it from `.xlator.local.env` in the project root. `$DOMAINS_DIR` is relative to the project root. The Xlator plugin modifies files only under the `$DOMAINS_DIR` folder.

**`XLATOR_AI_CONCURRENCY`** (optional, default `3`) — bounds per-batch parallelism for `/index-inputs`'s per-file fan-out. Raise once your tier's rate-limit headroom is confirmed; lower to `1` if 429s surface.

## Running Python

- To run a Python script under `tools/`, use the `xlator` shell shim so env vars are set correctly.
- For arbitrary Python on the CLI, run `uv run python <args>` (never `python` / `python3`).
- To run `pytest`, use `uv run pytest`.
- To install Python dependencies, use `uv pip install`.

## Index path keys vs content reads

`policy_facets/input-index.yaml` keys are `input/policy_docs/<rel>.md` (source paths — the canonical citation written back to artifacts as `source_doc:` / `source.file`). Skills that need:

- **content** read `policy_facets/compressed/<rel>.md` (caveman-compressed mirror)
- **structured section data** glob `policy_facets/computations/**/*.md.yaml` (per-file `{sections: [...]}` maps)
- **source SHAs** read the `sha:` field from `input-index.yaml` via `SP-LoadInputIndex` (do not call `git hash-object` directly)

Full conventions live in [core/policy_facets_claude.md](core/policy_facets_claude.md). Shared procedures (`SP-LoadInputIndex`, `SP-LoadGuidanceShas`, etc.) live in [core/ruleset-shared.md](core/ruleset-shared.md).

Manifests for per-tier drift detection (`specs/guidance/.facets-manifest.yaml`, `specs/extraction-manifest.yaml`, `specs/tests/.catala-manifest.yaml`) are written by their owning skills and audited by `/check-freshness` — see [skills/check-freshness/SKILL.md](skills/check-freshness/SKILL.md).

## Output Fencing

All skill output MUST be wrapped in semantic fence blocks so a web UI harness can parse and route it without AI or heuristics.

**Syntax:** `:::type` on its own line to open, `:::` on its own line to close. No nesting. Multiple blocks per response are allowed. Unfenced output defaults to `detail`.

| Fence type | When to use |
|------------|-------------|
| `:::important` | Primary result, written confirmation, summary verdict |
| `:::error` | Pre-flight failure — always paired with a stop |
| `:::next_step` | Suggested follow-on skills after successful completion |
| `:::detail` | Skeleton, YAML, rule tables, coverage maps, verbatim relay output |
| `:::progress` | In-flight status (still running) |
| `:::user_input` | Any prompt requiring a user response before continuing |

Read [core/output-fencing.md](core/output-fencing.md) for the full authoring reference before executing any skill.

## Skill workflow

After completion of an `xl` skill, suggest possible next steps based on these workflows. Each skill's `SKILL.md` frontmatter documents its own prerequisites — consult those for enable/disable logic in UIs.

**New domain → ruleset:**
1. `/new-domain <domain>` — folder scaffold
2. User adds `.md` policy docs to `$DOMAINS_DIR/<domain>/input/policy_docs/`
3. `/index-inputs <domain>` — build the document index; for each indexable source file, fans out to two per-file AI skills in parallel:
  * `/compress-input <path>` — runs `/caveman:compress` on each input policy file to create `policy_facets/compressed/<rel>.md` so downstream skills can read a concise token-reduced version of the content
  * `/extract-computations <path>` — parse the input policy file's sections and infer computations from the text to `policy_facets/computations/<rel>.md.yaml`
4. Either `/refine-guidance <domain>` (orchestrated), or run step-by-step:
  * **Orchestrated:** `/refine-guidance <domain>` — runs the step-by-step skills below in sequence (bootstraps via `/suggest-target-ruleset` + `/declare-target-ruleset` on first run)
  * **Step-by-step (for UI-driven or incremental workflows):**
    - `/suggest-target-ruleset <domain>` — analyze the policies and write candidate target rulesets for user selection
    - `/declare-target-ruleset <domain>` — write initial `guidance/` files from the specified target ruleset file
    - `/create-skeleton <domain>` — extract doc signals from the policies and build the computation skeleton
    - `/create-ruleset-groups <domain>` — propose ruleset groups that group related computations in the skeleton; these groups will help with ruleset visualizations
    - `/create-ruleset-modules <domain>` — apply heuristics to detect ruleset modules to further consolidate computations within groups; these modules will become reusable ruleset modules in the target ruleset language
    - `/extract-sample-rules <domain>` — generate sample Catala rules from the index (best after create-ruleset-modules) for the user to become familiar, revise, and gain confidence in the anticipated results
    - `/tag-vars-to-include-with-output <domain>` — auto-detect intermediate computed variables to be exposed along with the final output (best after extract-sample-rules); the selected variables are intended to be useful for explaining the computations used to derive the final output
    - `/create-sample-tests <domain>` — generate sample test cases to measure the accuracy of the generated ruleset; this gives the AI a metric to assess and correct the generated ruleset
5. `/extract-ruleset <domain>` — emit the Catala source (`specs/<module>.catala_en`) via the clerk-loop
6. `/review-ruleset <domain>` — review, finalize graph artifacts, capture guidance learnings

**Notes on ordering:**
- `/tag-vars-to-include-with-output` is required before `/extract-ruleset` in UI-driven workflows (it populates `include_with_output`; skipping it causes `/extract-ruleset` to prompt mid-run).
- `/check-freshness <domain>` can run at any point to detect drift of input data on previously-created downstream artifacts.

**After ruleset exists:**
- `/create-demo <domain>` — generate a web-based ruleset demo
- `/create-tests <domain>` — initial test cases
- `/transpile-and-test <domain>` — transpile to Catala and run tests
- `/expand-tests <domain>` — increase coverage

**After policy docs change:**
1. `/index-inputs <domain>` to refresh the index
2. `/update-ruleset <domain>` to update the Catala source

## Multi-step Skills

When a skill has more than 3 steps, show a checklist of the steps at the completion of each step to help the user track progress.

## AskUserQuestion

Never present "Press Enter to ..." as an option. For boolean responses, show `[y/n]`. For multiple options:

```
[a] Option one
[b] Option two
[c] Option three
(or type in different response)
```

If the user responds with more than 1 character, use the user's response as the answer.

## Catala Conventions

When working with Catala code, always use Catala semantics and syntax — never Rego. Double-check that generated tests, transpiler output, and examples use Catala conventions (e.g., `Using` not `Include`, correct module/entity prefixes).
