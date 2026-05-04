## Environment variables

The `.xlator.local.env` file exports the `$DOMAINS_DIR` and other environment variables, used by shell scripts and AI skills.

If `$DOMAINS_DIR` is unknown, read it from `.xlator.local.env` in the project root folder.

`$DOMAINS_DIR` is relative the project root. The Xlator Claude Code plugin modifies files only under the `$DOMAINS_DIR` folder.

## Running Python scripts under the tools folder

To run Python scripts under the `tools/` folder, use the `xlator` shell script as a shim so that required environment variables are correctly set.

## Running arbitrary Python code

Do not use the system Python; use `uv run python <args>`.

## Project Terminology

Use the project's exact terminology: 'ruleset module' (not 'sub-ruleset', not 'submodule'), 'ruleset group' (not 'workflow stage'), 'CIVIL' for the DSL name. Ask for clarification if domain terminology is ambiguous rather than guessing.

## Output Fencing

All skill output MUST be wrapped in semantic fence blocks so a web UI harness can parse and route it without AI or heuristics. Always include the fencing syntax around the text blocks in the output, such as `:::important` and `:::next_step`.

**Syntax:** `:::type` on its own line to open, `:::` on its own line to close. No nesting. Multiple blocks per response are allowed.

| Fence type | When to use |
|------------|-------------|
| `:::important` | Primary result, written confirmation, summary verdict |
| `:::error` | Pre-flight failure — always paired with a stop |
| `:::next_step` | Suggested follow-on skills after successful completion |
| `:::detail` | Skeleton, YAML, rule tables, coverage maps, verbatim relay output |
| `:::progress` | In-flight status lines, scan progress, step checklist mid-run |
| `:::user_input` | Any prompt requiring a user response before continuing |

Unfenced output defaults to `detail`.

**`progress` vs `detail`:** `:::progress` = transient, in-flight (still running). `:::detail` = complete, available for inspection.

**Verbatim-relay:** Open `:::detail` before beginning relay; close `:::` after relay completes. One fence per program — do not wrap multiple programs in a single fence.

Before executing any skill, read `core/output-fencing.md` for the full authoring reference.

## Skills Next steps

After completion of a `xl` skill, suggest possible next steps based on the following workflows:

Typical steps:
  1. `/new-domain <domain>` to set up the folder scaffold for a new domain
  2. User adds `.md` policy documents to `$DOMAINS_DIR/<domain>/input/policy_docs/`
  3. `/index-inputs <domain>` to build a document index
  4. Write an AI prompt to extract a ruleset in the `guidance.yaml` file — two options:
      * **Monolithic (original):** `/refine-guidance <domain>`
      * **Step-by-step (for UI-driven or incremental workflows):**
        - `/suggest-target-ruleset <domain>` — analyze the document index and write candidate target rulesets to files for user selection
        - `/declare-target-ruleset <domain>` — write `guidance.yaml` from a specified target ruleset file (one of the files created by `/suggest-target-ruleset`)
        - `/create-skeleton <domain>` — extract doc signals from the document index and build the computation skeleton
        - `/create-ruleset-groups <domain>` — propose ruleset groups that group related computations in the skeleton; these groups will help with ruleset visualizations
        - `/create-ruleset-modules <domain>` — apply heuristics to detect ruleset modules to further consolidate computations within groups; these modules will become reusable ruleset modules in the target ruleset language
        - `/extract-sample-rules <domain>` — generate sample CIVIL rules from the index (best after create-ruleset-modules) for the user to become familiar, revise, and gain confidence in the anticipated results
        - `/tag-vars-to-include-with-output <domain>` — auto-detect intermediate computed variables to be exposed along with the final output (best after extract-sample-rules); the selected variables are intended to be useful for explaining the computations used to derive the final output
        - `/create-sample-tests <domain>` — generate sample test cases to measure the accuracy of the generated ruleset; this gives the AI a metric to assess and correct the generated ruleset
  5. `/extract-ruleset <domain>` to extract the CIVIL ruleset
  6. `/review-ruleset <domain>` to review the extracted ruleset, finalize graph artifacts, and capture guidance learnings

### Skill dependency diagram

Enable/disable each skill in the UI based on which file-state prerequisites are satisfied. Dashed edges indicate optional steps — the downstream skill is enabled independently, not gated on them.

```mermaid
flowchart TD
    DOCS["policy docs in input/policy_docs/"]
    IDX_CMD["/index-inputs"]
    IDX_META(["input-index.yaml\n(files block: SHAs, md_quality)"])
    IDX_SECTS(["input-sections.yaml\n(sections block)"])

    DOCS --> IDX_CMD --> IDX_META
    IDX_CMD --> IDX_SECTS

    SUG["/suggest-target-ruleset\nenabled: input-sections.yaml exists"]
    SUG_F(["suggested_targets/*.yaml"])
    DECL["/declare-target-ruleset\nenabled: suggested_targets/ has ≥1 file"]
    GY(["guidance.yaml"])

    IDX_SECTS --> SUG --> SUG_F --> DECL --> GY

    SKEL["/create-skeleton\nenabled: guidance.yaml exists"]
    GY_SKEL(["guidance.yaml\nwith skeleton:"])
    GROUPS["/create-ruleset-groups\nenabled: skeleton: present"]
    GY_GROUPS(["guidance.yaml\nwith ruleset_groups:"])
    MODS["/create-ruleset-modules\nenabled: ruleset_groups: present"]

    GY --> SKEL --> GY_SKEL --> GROUPS --> GY_GROUPS --> MODS

    GY_MODS(["guidance.yaml\nwith ruleset_modules:"])
    SAMPLERULES["/extract-sample-rules\nbest: ruleset_modules: present\nmin: skeleton: present\nenabled: guidance.yaml + input-sections.yaml exist"]
    GY_RULES(["guidance.yaml\nwith sample_rules"])
    TAGVARS["/tag-vars-to-include-with-output\nenabled: guidance.yaml exists\n(best after extract-sample-rules)"]
    SAMPLETESTS["/create-sample-tests\nenabled: sample_rules or sample_rules present"]
    GY_SAMPLETESTS(["guidance.yaml\nwith sample_tests:"])

    MODS --> GY_MODS --> SAMPLERULES
    IDX_SECTS --> SAMPLERULES
    SAMPLERULES --> GY_RULES --> TAGVARS
    GY_RULES --> SAMPLETESTS --> GY_SAMPLETESTS

    EXTRACT["/extract-ruleset\nenabled: ruleset_groups: + ruleset_modules: present"]
    REVIEW["/review-ruleset\nenabled: <program>.civil.yaml + naming-manifest.yaml exist"]

    GY_MODS --> EXTRACT
    TAGVARS --> EXTRACT
    EXTRACT --> REVIEW
```

**`/tag-vars-to-include-with-output` is required before `/extract-ruleset`** in a UI-driven workflow — it populates `include_with_output` so SP-TagOutputs has pre-selections and doesn't block for interactive input. Skipping it causes `/extract-ruleset` to prompt mid-run.

**`/create-sample-tests` is optional** — `/extract-ruleset` does not read `sample_tests:`. These are planning scaffolding only.

- `extract-sample-rules` can run earlier (after `create-skeleton` minimum) but produces flat, ungrouped output without `ruleset_modules:`
- `tag-vars` can run earlier but misses invoke-derived variables only visible in CIVIL snippets
- `create-sample-tests` always follows `extract-sample-rules`

Once `/review-ruleset` completes (or whenever the ruleset changes), the user can choose to:
  * `/create-demo <domain>` to generate a web-based ruleset demo
  * `/create-tests <domain>` to create an initial set of test cases

After test cases are created or modified, the user can choose to:
  * `/transpile-and-test <domain>` to transpile to default output language (Catala) and run the test cases
  * `/expand-tests <domain>` to increase test coverage
  * Add manually-created tests to the `$DOMAINS_DIR/<domain>/specs/tests` folder

After the user adds/updates .md policy documents in `$DOMAINS_DIR/<domain>/input/policy_docs/`, they should:
  1. `/index-inputs <domain>` to update the document index
  2. `/update-ruleset <domain>` to update the CIVIL ruleset

## Multi-step Skills

When a skill has more than 3 steps, show a checklist of the steps at the completion of each step to help the user track their progress.

## AskUserQuestion

When asking the user a question, never present the option of "Press Enter to ...".
Instead, if the question expects a boolean response, then show "[y/n]".
If there are multiple response options, present it as:

```
[a] Option one
[b] Option two
[c] Option three
(or type in difference response)
```

If the user responds with more than 1 character, then use the user's response as the answer.

## Catala Conventions

When working with Catala code, always use Catala semantics and syntax — never Rego. Double-check that generated tests, transpiler output, and examples use Catala conventions (e.g., `Using` not `Include`, correct module/entity prefixes).

## Shell Commands

On macOS, do not use `grep -P` (PCRE). Use `grep -E` (extended regex) or `perl -ne` instead.
