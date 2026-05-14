---
name: update-ruleset
description: Update Ruleset from Changed Policy Documents
---

# Update Ruleset from Changed Policy Documents

Update an existing CIVIL DSL ruleset for a domain when input policy documents have changed.

## Input

```
/update-ruleset <domain>                          # auto-detect program or prompt if ambiguous
/update-ruleset <domain> <program>                # target a specific <program>.civil.yaml
```

If `<domain>` is not provided, list all `$DOMAINS_DIR/*/input/policy_docs/` directories and prompt the user to choose.

---

Read `../../core/ruleset-shared.md` now. It contains shared pre-flight logic (checks 3–5),
the scoring rubric, CIVIL reference, shared procedures (SP-Validate, SP-ComputeGraph, SP-GuidanceCapture, and others), and common mistakes.

---

Read `../../core/output-fencing.md` now.

## Pre-flight

Run these checks before doing anything else:

1. **Domain folder exists?**
   - NO → Print:
     :::error
     domain not found at `$DOMAINS_DIR/<domain>/`, suggest running `/new-domain <domain>`.
     :::
     Stop.

2. **CIVIL file exists?**
   - NO → Print:
     :::error
     no ruleset found for this domain, suggest `/extract-ruleset <domain>`.
     :::
     Stop.

3. **Load extraction context (deterministic).**

   Run:
   ```bash
   xlator load-extraction-context <domain> [<program>] --mode update
   ```

   This tool subsumes pre-flight checks 3–5 from `../../core/ruleset-shared.md`, plus `SP-LoadInputIndex` and `SP-LoadGuidanceShas`. It reads every guidance file + `naming-manifest.yaml` + `policy_facets/input-index.yaml` + `extraction-manifest.yaml`, runs the working-tree drift check on `input-index.yaml`'s recorded SHAs, computes `git hash-object` for every `specs/guidance/*.yaml` + `specs/naming-manifest.yaml`, and emits a single JSON payload to stdout.

   On non-zero exit: relay the tool's stderr in `:::error` and stop.

   Parse the JSON payload. Bind:
   - `input_index_shas` — `{path → sha}` for every eligible source doc (used in Step 2 change detection and Step 7 manifest refresh).
   - `guidance_shas` — `{path → sha}` for every `specs/guidance/*.yaml` + `specs/naming-manifest.yaml` (used in Step 7 to refresh `consumed_guidance[]`).
   - `naming_manifest` — the full parsed manifest (used in Step 0 divergence check, Step 5 name binding, Step 9 inventory build).
   - `existing_extraction_manifest` — the parsed `extraction-manifest.yaml` (used in Step 1 baseline, Step 2 change detection, Step 1b reconcile).
   - `program` — resolved program name (from `ruleset-modules.yaml`'s `role: main` entry or auto-detected).
   - `work_list` — drives multi-file iteration (sub-modules first, main module last).

---

## Process

### Step 0: Naming Manifest Divergence Check + Ruleset Module Resolution

**Naming manifest divergence check.** Use the `naming_manifest` from the pre-flight JSON payload:

1. Collect the variable names from the manifest (entries from `inputs.<Entity>.<field>`, `computed.<field>`, and `outputs.<field>`).
2. Read all fact, computed, and outputs field names from `$DOMAINS_DIR/<domain>/specs/<program>.civil.yaml`.
3. Compare the two sets. If any field name exists in the CIVIL file but not the manifest, or exists in both but with a different spelling, **halt** and list every mismatch:

   :::error
   ⚠️ Naming manifest divergence detected:
   - CIVIL has `income` under `Household`, but manifest expects `gross_monthly_income`

   Resolve by either:
   a) Editing the CIVIL file to restore the manifest name, or
   b) Editing `naming-manifest.yaml` to acknowledge the rename

   Then re-run `/update-ruleset <domain>`.
   :::

   Do not continue until there are no mismatches.

**Multi-file validation (when `existing_extraction_manifest` is non-null and has multiple modules):**

1. Verify that every path listed under `programs: <program>: civil_file:` and every path listed under `programs: <program>: sub_modules: [].civil_file:` exists on disk. If any file is missing, stop:
   :::error
   ⚠️  Missing CIVIL files listed in extraction-manifest.yaml:
     - $DOMAINS_DIR/<domain>/specs/<missing_file>.civil.yaml
   Restore the missing file(s) or re-run /extract-ruleset <domain>.
   :::
2. Run **SP-ResolveRulesetModules** (from `../../core/ruleset-shared.md`) with context `update`.
   - If SP-ResolveRulesetModules emits an abort signal (new `ruleset_modules:` entries not in manifest): stop with SP-ResolveRulesetModules's message.
   - Otherwise, use the returned work-list in Steps 2 and 9 (it overrides the `work_list` from the pre-flight payload when SP-ResolveRulesetModules produces additional binding-confirmation context).

Proceed to Step 1.

### Step 1: Load Baseline

Use `existing_extraction_manifest` from the pre-flight JSON payload to get the recorded blob SHA for each source doc. Each entry's `git_sha` is the source doc's blob SHA at the time of the last extraction. The drift check inside `xlator load-extraction-context` (run in pre-flight) guarantees the current `input_index_shas` values reflect the source doc's working-tree bytes — so comparing the manifest's `git_sha` against `input_index_shas` is equivalent to comparing the prior extracted bytes against the current bytes.

**Fallback (if `existing_extraction_manifest` is null):** there is no baseline to compare against, so re-extraction must be unconditional — stop and run `/extract-ruleset <domain>` instead.

### Step 1b: Reconcile Manifest

Before change detection, remove stale entries from `extraction-manifest.yaml` for files that no longer exist on disk:

- For each `source_docs` path under both `programs: <program>:` and `programs: <program>: sub_modules: []:`, check if the file exists; if absent, remove that entry and print `Removed stale manifest entry: <path>`.
- For each `consumed_guidance` path under the same scopes, check if the file exists at `$DOMAINS_DIR/<domain>/<path>`; if absent, remove that entry and print `Removed stale guidance manifest entry: <path>`. The `consumed_guidance[]` block is otherwise preserved verbatim during this step — its SHA values are not refreshed here (Step 7 handles refresh for the program being updated; other programs' blocks remain untouched).

Runs on every UPDATE invocation — ensures deleted or renamed input files don't cause change detection failures.

### Step 2: Detect Changes

For every source doc to be checked, look up its current blob SHA in `input_index_shas` (from the pre-flight JSON payload). Compare against the SHA stored in the extraction manifest. A mismatch (or a source doc absent from the manifest) means the file changed and must be re-extracted.

`input_index_shas` is the canonical SHA source — do not run `git hash-object` here. The drift check inside the pre-flight tool guarantees the index value reflects current working-tree bytes, so committed AND uncommitted edits are caught by the same lookup.

**Multi-file (work_list has more than one entry):**

For each entry in the work-list, run the comparison above against every path under that entry's `source_docs:`. If any source doc's current blob SHA differs from the stored `git_sha`, the entry is added to the set of files requiring re-extraction. Sub-module entries are reported with label `[sub-module: <name>]`. If no source doc for the entry changed, skip re-extraction for that entry (source-provenance-based scoping).

The main module is re-extracted if its own source docs changed. If only sub-module source docs changed, the main module is not re-extracted (its `invoke:` fields reference sub-module names, which don't change).

**Single-file (work_list has one entry):**

Compare every entry in the manifest's `source_docs:` against `input_index_shas`, and additionally enumerate `input_index_shas`'s keys for paths that are present in the index but absent from the manifest (treat them as added). The pre-flight tool already filtered out entries with `md_quality.score < 40`, so rejected source files (moved to `input/rejected/`) do not surface as false additions. Collect the list of changed/added/deleted input docs.

### Step 3: No Changes — Exit Early

If no changes detected:
:::important
All input docs are up to date. Nothing to extract.
To re-extract anyway, delete or rename $DOMAINS_DIR/<domain>/specs/extraction-manifest.yaml and re-run.
:::
Stop. Do not modify any files.

### Step 4: Identify Affected CIVIL Sections

For each changed doc, read the diff and determine which CIVIL sections need updating:

| Type of Change in Input Doc | Affected CIVIL Sections |
|---|---|
| Dollar thresholds by household size | `tables:`, possibly `computed:` (size 9+ formulas) |
| Fixed rates or percentages | `constants:` |
| New applicant fields added | `inputs:`, possibly `rules:` |
| New eligibility test or condition | `rules:`, possibly `computed:` |
| Effective date change | `effective:` |
| Jurisdiction change | `jurisdiction:` |
| Deduction formula change | `computed:`, `constants:`, `rules:` |

### Step 5: Re-extract Affected Sections

Recall the extraction goal from the pre-flight JSON payload (`metadata`, `prompt_context`, `output_variables`, `input_variables`, `guidance_output_set`, `constants_tables_seed`, plus `naming_manifest`) before re-reading any policy sections:

```
---
[content of metadata, prompt_context, output_variables, input_variables,
 guidance_output_set, constants_tables_seed, and naming_manifest from the
 pre-flight JSON payload]
---

Apply these constraints and standards when re-extracting the affected CIVIL sections.
```

For each affected section, re-read the relevant parts of the changed policy doc — read the caveman-compressed copy at `$DOMAINS_DIR/<domain>/policy_facets/compressed/<rel>.md` rather than the raw source under `input/policy_docs/`. The compressed copy is the canonical content for AI consumption (see "Index path keys vs content reads" in `xl-plugin/CLAUDE.md`). Re-extract only that section. Do not touch sections not identified in Step 4.

When re-extracting any section that contains `inputs:` or `computed:` fields, inject the frozen names from `naming_manifest` into your extraction reasoning: "These fields must keep their exact current names: [list all names from manifest]. Only introduce new field names for policy concepts not in this list, using the 4-step algorithm: (1) exact noun phrase, (2) strip entity-name words, (3) snake_case, (4) disambiguate if needed." **Never rename an existing field.**

After re-extracting, run **SP-OrchestrationFilter** (from `../../core/ruleset-shared.md`) on the newly extracted rule components (not the full existing CIVIL file — only rules and computed fields identified in Step 4):
- Remove flagged components; display the SP-OrchestrationFilter summary table if any were flagged.
- Continue to Step 6 with the filtered set. Re-included components will have a YAML comment added when merged.

### Step 6: Merge into Existing CIVIL File

Update the existing `$DOMAINS_DIR/<domain>/specs/<program>.civil.yaml` at section granularity:
- Replace only the affected top-level sections (`tables:`, `constants:`, `rules:`, etc.)
- Preserve all unchanged sections verbatim (including comments and formatting)
- Preserve any hand-edits in unchanged sections

### Step 6b: Maintainability Self-Review (CIVIL v6)

*Runs after Step 6 (Merge), before Step 7 (Update Manifest).*

Run **SP-MaintainabilityReview** (from `../../core/ruleset-shared.md`) on the CIVIL file, **scoped to only the rules and computed fields that were added or modified in this update** (identified in Step 4). Unchanged rules and computed fields are not re-checked.

- SP-MaintainabilityReview applies in-place fixes for non-blocking items (M1–M4) where the fix is mechanical.
- If blocking item **M5** (duplicate priority within a `mutex_group`) fails:
  1. Display the conflicting rules and their priorities.
  2. Ask:
     :::user_input
     Two or more rules in `mutex_group '<name>'` share the same priority. Please assign unique priorities, then type 'continue'.
     :::
  3. Apply the user's corrections to the merged file.
  4. Re-run SP-MaintainabilityReview on the affected `mutex_group` to confirm M5 is resolved.
- On SP-MaintainabilityReview completion: display the summary table.

Proceed to Step 7 only after SP-MaintainabilityReview passes (no blocking failures).

### Step 7: Update Manifest

Update `$DOMAINS_DIR/<domain>/specs/extraction-manifest.yaml`. Each `git_sha:` value is the source doc's blob SHA — read it from `input_index_shas` (already loaded in pre-flight; do not run `git hash-object` here). Field-name translation: the index field is `sha:`, the manifest field is `git_sha:`; the value is identical.

Refresh the `consumed_guidance[]` block for the program being updated, using `guidance_shas` from the pre-flight JSON payload. Enumerate every path in `guidance_shas` and write one `{path, sha}` entry per file. When `guidance_shas` is empty, write `consumed_guidance: []`. The same refresh rule applies to every sub-module of the program being updated. Other programs' `consumed_guidance[]` blocks (programs not touched by this run) are preserved verbatim — do not refresh them; their entries reflect provenance from their last `/extract-ruleset` or `/update-ruleset` run.

**Multi-file:** After successful re-extraction, update `extracted_at` and source doc SHAs for each regenerated file (main module and sub-modules). Files that were not re-extracted (no source doc changes) retain their existing manifest entries verbatim. Sub-modules with `referenced: true` in the manifest retain their entry unchanged (they were not regenerated). Refresh `consumed_guidance[]` for every regenerated entry per the rule above.

**Single-file:**
- Update `git_sha` for each changed source doc
- Refresh `consumed_guidance[]` for this program using `guidance_shas`
- Update `extracted_at` to today's date

### Step 8: Validate

Run **SP-Validate**.

### Step 9: Update Naming Manifest

If any new fact, computed, or outputs fields were added during this update: derive canonical names using the 4-step algorithm from `/extract-ruleset` Step 3b. Build an inventory JSON containing **one entry per new field** (and only the new fields), then call:

```bash
xlator merge-naming-manifest <domain> <program> --inventory <tmpfile> --preserve-unmentioned
```

The `--preserve-unmentioned` flag is what differentiates this call from `/extract-ruleset` Step 7: `/update-ruleset` adds new fields without re-presenting the full inventory, so existing entries not referenced by this run's inventory must survive verbatim. (`/extract-ruleset` Step 7 presents the full inventory and the merge tool drops unmentioned entries by default.)

Inventory entry shape (same as `/extract-ruleset` Step 7):

```json
{
  "name": "<new snake_case field name>",
  "section": "inputs.<Entity>" | "computed" | "outputs",
  "policy_phrase": "<verbatim noun phrase from policy doc>" | null,
  "source_doc": "input/policy_docs/<rel>.md" | null,
  "section_text": "<§ citation> — <heading>" | null,
  "prior_name": null,           // /update-ruleset rarely renames; leave null
  "description": "<AI-inferred>" | null,
  "type": "<money|bool|int|float|string|enum|list|set|date|object>" | null,
  "values": null,
  "observed_synonyms": null
}
```

Rules:
- **Add only new fields.** Existing entries are preserved by the `--preserve-unmentioned` flag.
- **`policy_phrase`** is the verbatim noun phrase from the source policy doc, scoped to the section where the new field was introduced. Read the caveman-compressed source at `policy_facets/compressed/<rel>.md`.
- **`prior_name`** is almost always `null` in `/update-ruleset` — fields aren't normally renamed during an update. If a rename does occur, set `prior_name` and the merge tool will append the rename-anchor synonym (idempotent on subsequent runs).
- **Optional fields** (`description`, `type`) are AI-inferred from policy text where the signal is unambiguous; otherwise omit.

If no new fields were added, skip the `merge-naming-manifest` call entirely — no inventory needed.

On non-zero exit from the tool: relay stderr in `:::error` and stop.

If no manifest exists yet (this should not happen because Step 0 requires it, but defensive case), `merge-naming-manifest` initializes it.

### Step 10: Write Stale-Cases Hint

Write `$DOMAINS_DIR/<domain>/specs/.stale-cases.yaml` for use by `/create-tests`:

```yaml
# Written by /update-ruleset. Consumed and deleted by /create-tests.
stale_cases:
  - case_id: "<case_id>"
    reason: "<what changed — e.g., 'gross_limit for household_size 3 changed from X to Y'>"
```

Include any test case whose `inputs` contain a value that was a table boundary or constant value in the old CIVIL file but has changed in the updated version. If no cases are stale, write an empty list:
```yaml
stale_cases: []
```

---

:::important
Update complete.
:::

:::next_step
Run the review gate to validate and finalize:

```
/review-ruleset <domain> <program>
```
:::

---

## Output

Files created or modified by this command:

| File | Action |
|------|--------|
| `$DOMAINS_DIR/<domain>/specs/<program>.civil.yaml` | Updated (affected sections only) |
| `$DOMAINS_DIR/<domain>/specs/extraction-manifest.yaml` | Updated |
| `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml` | Updated by `xlator merge-naming-manifest --preserve-unmentioned` (Step 9, after validation) |
| `$DOMAINS_DIR/<domain>/specs/.stale-cases.yaml` | Created (Step 10; consumed by `/create-tests`) |
| `$DOMAINS_DIR/<domain>/policy_facets/computations/<rel>.md.yaml` | Read-only (per-file section data; if present) |
| `$DOMAINS_DIR/<domain>/policy_facets/compressed/<rel>.md` | Read-only (canonical content for AI consumption) |
| `$DOMAINS_DIR/<domain>/specs/guidance/*.yaml` | Read (via `xlator load-extraction-context`) |
| `$DOMAINS_DIR/<domain>/policy_facets/input-index.yaml` | Read (via `xlator load-extraction-context`) |

Graph artifacts (`.graph.yaml`, `.mmd`) and guidance updates are written by `/review-ruleset`. Tests and transpilation are handled by `/create-tests` and `/transpile-and-test`.
