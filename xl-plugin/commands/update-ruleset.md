# Update Ruleset from Changed Policy Documents

Update an existing CIVIL DSL ruleset for a domain when input policy documents have changed.

## Input

```
/update-ruleset <domain>                          # auto-detect program or prompt if ambiguous
/update-ruleset <domain> <program>                # target a specific <program>.civil.yaml
/update-ruleset <domain> <program> <filename>     # scope update to one input file
```

`<filename>` is the basename of a `.md` file in `$DOMAINS_DIR/<domain>/input/policy_docs/` (e.g., `APA.md`). The `.md` extension is appended automatically if omitted. When given, `<filename>` scopes the full pipeline: only that file is read as the policy corpus, and only its manifest entry is updated.

If `<domain>` is not provided, list all `$DOMAINS_DIR/*/input/policy_docs/` directories and prompt the user to choose.

---

Read `$CLAUDE_PLUGIN_ROOT/core/ruleset-shared.md` now. It contains shared pre-flight logic (checks 3–6),
the scoring rubric, CIVIL reference, shared procedures (SP-Validate, SP-ComputeGraph, SP-GuidanceCapture, and others), and common mistakes.

---

## Pre-flight

Run these checks before doing anything else:

1. **Domain folder exists?**
   - NO → Print:
     :::error
     domain not found at `$DOMAINS_DIR/<domain>/`, suggest running `/xl:new-domain <domain>`.
     :::
     Stop.

2. **CIVIL file exists?**
   - NO → Print:
     :::error
     no ruleset found for this domain, suggest `/xl:extract-ruleset <domain>`.
     :::
     Stop.

Run shared pre-flight checks 3–6 from `core/ruleset-shared.md`.

---

## Process

### Step 0: Load Naming Manifest, Check for Divergence, and Ruleset Module Resolution

**If `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml` exists:**

1. Run **SP-LoadNamingManifest** (from `core/ruleset-shared.md`). Collect the variable names from the resulting map.
2. Read all fact, computed, and output field names from `$DOMAINS_DIR/<domain>/specs/<program>.civil.yaml`
3. Compare the two sets. If any field name exists in the CIVIL file but not the manifest, or exists in both but with a different spelling, **halt** and list every mismatch:

   :::error
   ⚠️ Naming manifest divergence detected:
   - CIVIL has `income` under `Household`, but manifest expects `gross_monthly_income`

   Resolve by either:
   a) Editing the CIVIL file to restore the manifest name, or
   b) Editing `naming-manifest.yaml` to acknowledge the rename

   Then re-run `/xl:update-ruleset <domain>`.
   :::

   Do not continue until there are no mismatches.

**If the manifest does not exist** (domain was extracted before this feature was added):

:::important
⚠️ No naming manifest found. Field names will not be enforced this run. A manifest will be created after this UPDATE completes.
:::

**Multi-file validation (if `extraction-manifest.yaml` exists):**

1. Read `$DOMAINS_DIR/<domain>/specs/extraction-manifest.yaml`.
2. Verify that every path listed under `programs: <program>: civil_file:` and every path listed under `programs: <program>: sub_modules: [].civil_file:` exists on disk. If any file is missing, stop:
   :::error
   ⚠️  Missing CIVIL files listed in extraction-manifest.yaml:
     - $DOMAINS_DIR/<domain>/specs/<missing_file>.civil.yaml
   Restore the missing file(s) or re-run /xl:extract-ruleset <domain>.
   :::
3. Run **SP-ResolveRulesetModules** (from `core/ruleset-shared.md`) with context `update`.
   - If SP-ResolveRulesetModules emits an abort signal (new `ruleset_modules:` entries not in manifest): stop with SP-ResolveRulesetModules's message.
   - Otherwise, store the returned work-list for use in Steps 2 and 9.

Proceed to Step 1.

### Step 1: Load Baseline

Read `$DOMAINS_DIR/<domain>/specs/extraction-manifest.yaml` to get the git SHA for each source doc.

**Fallback chain (if manifest absent):**
1. Get the CIVIL file's last commit SHA: `git log -1 --format="%H" -- $DOMAINS_DIR/<domain>/specs/<program>.civil.yaml`
2. If no git history at all → treat as CREATE mode (full re-extraction); run `/xl:extract-ruleset <domain>` instead.

### Step 1b: Reconcile Manifest

Before change detection, remove stale entries from `extraction-manifest.yaml` for files that no longer exist on disk. For each `source_docs` path under both `programs: <program>:` and `programs: <program>: sub_modules: []:`, check if the file exists; if absent, remove that entry and print `Removed stale manifest entry: <path>`. Runs on every UPDATE invocation — ensures deleted or renamed input files don't cause change detection failures.

### Step 2: Detect Changes

**Multi-file (SP-ResolveRulesetModules work-list has more than one entry):**

For each entry in the SP-ResolveRulesetModules work-list, run change detection against that entry's `source_docs:` from the manifest:

```bash
# Per work-list entry:
git diff <entry_source_sha>..HEAD -- <entry_source_doc_path>
git status <entry_source_doc_path>
```

- If changes are detected for a sub-module entry: include it in the change report with label `[sub-module: <name>]` and add it to the set of files requiring re-extraction.
- If no changes are detected for a sub-module entry: skip re-extraction for that sub-module (source-provenance-based scoping — only files whose source docs changed are re-extracted).

The main module is re-extracted if its own source docs changed. If only sub-module source docs changed, the main module is not re-extracted (its `invoke:` fields reference sub-module names, which don't change).

**Single-file (SP-ResolveRulesetModules work-list has one entry):**

If `<filename>` is given, scope change detection to that file only:

```bash
# Scoped (when <filename> is given):
git diff <baseline-sha>..HEAD -- $DOMAINS_DIR/<domain>/input/policy_docs/<filename>
git status $DOMAINS_DIR/<domain>/input/policy_docs/<filename>

# Full (when <filename> is not given):
git diff <baseline-sha>..HEAD -- $DOMAINS_DIR/<domain>/input/policy_docs/
git status $DOMAINS_DIR/<domain>/input/policy_docs/
```

Collect the list of changed/added/deleted input docs.

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

**If `guidance.yaml` was loaded in pre-flight**, recall the extraction goal before re-reading any policy sections:

```
---
[guidance.yaml content — paste verbatim as loaded]
---

Apply these constraints and standards when re-extracting the affected CIVIL sections.
```

For each affected section, re-read the relevant parts of the changed policy doc and re-extract only that section. Do not touch sections not identified in Step 4.

When re-extracting any section that contains `inputs:` or `computed:` fields, inject the frozen names from `naming-manifest.yaml` into your extraction reasoning: "These fields must keep their exact current names: [list all names from manifest]. Only introduce new field names for policy concepts not in this list, using the 4-step algorithm: (1) exact noun phrase, (2) strip entity-name words, (3) snake_case, (4) disambiguate if needed." **Never rename an existing field.**

After re-extracting, run **SP-OrchestrationFilter** (from `core/ruleset-shared.md`) on the newly extracted rule components (not the full existing CIVIL file — only rules and computed fields identified in Step 4):
- Remove flagged components; display the SP-OrchestrationFilter summary table if any were flagged.
- Continue to Step 6 with the filtered set. Re-included components will have a YAML comment added when merged.

### Step 6: Merge into Existing CIVIL File

Update the existing `$DOMAINS_DIR/<domain>/specs/<program>.civil.yaml` at section granularity:
- Replace only the affected top-level sections (`tables:`, `constants:`, `rules:`, etc.)
- Preserve all unchanged sections verbatim (including comments and formatting)
- Preserve any hand-edits in unchanged sections

### Step 6b: Maintainability Self-Review (CIVIL v6)

*Runs after Step 6 (Merge), before Step 7 (Update Manifest).*

Run **SP-MaintainabilityReview** (from `core/ruleset-shared.md`) on the CIVIL file, **scoped to only the rules and computed fields that were added or modified in this update** (identified in Step 4). Unchanged rules and computed fields are not re-checked.

- SP-MaintainabilityReview applies in-place fixes for non-blocking items (M1–M4) where the fix is mechanical.
- If blocking item **M5** (duplicate priority within a `mutex_group`) fails:
  1. Display the conflicting rules and their priorities.
  2. Ask:
     :::user_input
     Two or more rules in `mutex_group '<name>'` share the same priority. Please assign unique priorities, then type [c]ontinue.
     :::
  3. Apply the user's corrections to the merged file.
  4. Re-run SP-MaintainabilityReview on the affected `mutex_group` to confirm M5 is resolved.
- On SP-MaintainabilityReview completion: display the summary table.

Proceed to Step 7 only after SP-MaintainabilityReview passes (no blocking failures).

### Step 7: Update Manifest

Update `$DOMAINS_DIR/<domain>/specs/extraction-manifest.yaml`:

**Multi-file:** After successful re-extraction, update `extracted_at` and source doc SHAs for each regenerated file (main module and sub-modules). Files that were not re-extracted (no source doc changes) retain their existing manifest entries verbatim. Sub-modules with `referenced: true` in the manifest retain their entry unchanged (they were not regenerated).

**Single-file, `<filename>` given (partial update):**
- Update `extracted_at` to today's date
- In `source_docs:`, find the entry for `<filename>` and update its `git_sha` and `last_extracted`
- If no entry exists yet for `<filename>`, add one
- Preserve all other `source_docs:` entries verbatim (files not processed this run keep their existing SHA)

**Single-file, `<filename>` not given (full update):**
- Update `git_sha` for each changed source doc
- Update `extracted_at` to today's date

### Step 8: Validate

Run **SP-Validate**.

### Step 9: Update Naming Manifest

If any new fact, computed, or outputs fields were added: derive canonical names using the 4-step algorithm from `/xl:extract-ruleset` Step 3b, then append them to `naming-manifest.yaml` under the appropriate `inputs:` entity, `computed:`, or `outputs:` section. Preserve all existing entries unchanged.

If no manifest exists yet, create it now from all current CIVIL field names. No user confirmation needed — this runs automatically after validation.

### Step 10: Write Stale-Cases Hint

Write `$DOMAINS_DIR/<domain>/specs/.stale-cases.yaml` for use by `/xl:create-tests`:

```yaml
# Written by /xl:update-ruleset. Consumed and deleted by /xl:create-tests.
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
/xl:review-ruleset <domain> <program>
```
:::

---

## Output

Files created or modified by this command:

| File | Action |
|------|--------|
| `$DOMAINS_DIR/<domain>/specs/<program>.civil.yaml` | Updated (affected sections only) |
| `$DOMAINS_DIR/<domain>/specs/extraction-manifest.yaml` | Updated |
| `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml` | Updated (Step 9, after validation) |
| `$DOMAINS_DIR/<domain>/specs/.stale-cases.yaml` | Created (Step 10; consumed by `/xl:create-tests`) |
| `$DOMAINS_DIR/<domain>/specs/input-index.yaml` | Read-only (if present) |
| `$DOMAINS_DIR/<domain>/specs/guidance.yaml` | Read (required — run `/xl:refine-guidance <domain>` first) |

Graph artifacts (`.graph.yaml`, `.mmd`) and guidance updates are written by `/xl:review-ruleset`. Tests and transpilation are handled by `/xl:create-tests` and `/xl:transpile-and-test`.
