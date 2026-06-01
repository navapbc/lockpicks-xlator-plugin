---
name: update-ruleset
description: Update Ruleset from Changed Policy Documents
---

# Update Ruleset from Changed Policy Documents

Update an existing Catala ruleset for a domain when input policy documents have changed.

## Input

```
/update-ruleset <domain>                          # auto-detect program or prompt if ambiguous
/update-ruleset <domain> <program>                # target a specific <program>.catala_en
```

If `<domain>` is not provided, list all `$DOMAINS_DIR/*/input/policy_docs/` directories and prompt the user to choose.

---

Read `../../core/ruleset-shared.md` now. It contains shared pre-flight logic (checks 3–5),
the scoring rubric, shared procedures (SP-ComputeGraph, SP-GuidanceCapture, SP-OrchestrationFilter, SP-MaintainabilityReview, SP-ResolveRulesetModules, SP-LoadNamingManifest), and common mistakes.

Read `../../core/catala-authoring-quickref.md` now. It is the authoritative AI-targeted Catala reference — grammar excerpts, project idioms (literate Markdown citations, `catala-metadata` vs `catala` fence discipline, denial-reasons accumulation), and the six "AI failure modes to preempt" categories. Apply its idioms whenever editing `.catala_en` content in Steps 5–6.

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

2. **Catala file exists?**
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
   - `input_index_shas` — `{path → sha}` for every eligible source doc (used in Step 2 change detection and Step 8 manifest refresh).
   - `guidance_shas` — `{path → sha}` for every `specs/guidance/*.yaml` + `specs/naming-manifest.yaml` (used in Step 8 to refresh `consumed_guidance[]`).
   - `naming_manifest` — the full parsed manifest (used in Step 5 name binding and Step 10 inventory build; the divergence check itself is delegated to the U2 clerk loop in Step 0).
   - `existing_extraction_manifest` — the parsed `extraction-manifest.yaml` (used in Step 1 baseline, Step 2 change detection, Step 1b reconcile).
   - `program` — resolved program name (from `ruleset-modules.yaml`'s `role: main` entry or auto-detected).
   - `work_list` — drives multi-file iteration (sub-modules first, main module last; each entry carries `catala_file: specs/<name>.catala_en`).

---

## Process

### Step 0: Naming Manifest Divergence Check + Ruleset Module Resolution

**Naming manifest divergence check.** Run the aggregated check across every module in the work-list (not per-file — the monolithic `specs/naming-manifest.yaml` covers identifiers across siblings, so a per-file set-diff structurally cannot converge on multi-module domains).

**Invocation:**

```bash
xlator clerk-loop-multi <domain> --check-only
```

`--check-only` skips the per-module `clerk typecheck` + `clerk test` pass entirely; only the aggregated naming-manifest divergence check fires. The script emits a JSON header line on stdout followed by `--- CLERK-LOOP-MULTI-HEADER-END ---` and a human-readable summary. Parse the JSON header (first stdout line); verify the sentinel on the second line. The header carries `mode: "check_only"` so the relay can distinguish it from the full pass /update-ruleset Step 6 invokes.

Branch on `header.status`:

- **`status="ok"`** — the aggregated divergence check found no mismatch. Continue to the multi-file validation paragraph below, then Step 1.

- **`status="unresolved"`** — one or more diagnostics surfaced. Each diagnostic's message body already carries both resolution options; surface them in a single `:::error` fence:

  :::error
  ⚠️ Naming manifest divergence detected:
  <for each diagnostic emitted under the sentinel: file:line — message>

  Resolve by either:
  a) Editing the Catala source so the identifier matches the manifest entry's `name:`, OR
  b) Editing `naming-manifest.yaml` to acknowledge the rename (the prior key is appended to that entry's `synonyms:` list per the v10.1.0 rename-anchor convention — see Step 10).

  Then re-run `/update-ruleset <domain>`.
  :::

  Do not continue.

- **`status="error"`** (exit 2) — pre-flight failure (missing domain, missing `specs/naming-manifest.yaml`, or a `load-extraction-context` failure). Surface the human summary in `:::error` and stop.

**Multi-file validation (when `existing_extraction_manifest` is non-null and has multiple modules):**

1. Verify that every path listed under `programs: <program>: catala_file:` and every path listed under `programs: <program>: sub_modules: [].catala_file:` exists on disk. If any file is missing, stop:
   :::error
   ⚠️  Missing Catala files listed in extraction-manifest.yaml:
     - $DOMAINS_DIR/<domain>/specs/<missing_file>.catala_en
   Restore the missing file(s) or re-run /extract-ruleset <domain>.
   :::
2. Run **SP-ResolveRulesetModules** (from `../../core/ruleset-shared.md`) with context `update`.
   - If SP-ResolveRulesetModules emits an abort signal (new `ruleset_modules:` entries not in manifest): stop with SP-ResolveRulesetModules's message.
   - Otherwise, use the returned work-list in Steps 2, 5, and 10 (it overrides the `work_list` from the pre-flight payload when SP-ResolveRulesetModules produces additional binding-confirmation context).

Proceed to Step 1.

### Step 1: Load Baseline

Use `existing_extraction_manifest` from the pre-flight JSON payload to get the recorded blob SHA for each source doc. Each entry's `git_sha` is the source doc's blob SHA at the time of the last extraction. The drift check inside `xlator load-extraction-context` (run in pre-flight) guarantees the current `input_index_shas` values reflect the source doc's working-tree bytes — so comparing the manifest's `git_sha` against `input_index_shas` is equivalent to comparing the prior extracted bytes against the current bytes.

**Fallback (if `existing_extraction_manifest` is null):** there is no baseline to compare against, so re-extraction must be unconditional — stop and run `/extract-ruleset <domain>` instead.

### Step 1b: Reconcile Manifest

Before change detection, remove stale entries from `extraction-manifest.yaml` for files that no longer exist on disk:

- For each `source_docs` path under both `programs: <program>:` and `programs: <program>: sub_modules: []:`, check if the file exists; if absent, remove that entry and print `Removed stale manifest entry: <path>`.
- For each `consumed_guidance` path under the same scopes, check if the file exists at `$DOMAINS_DIR/<domain>/<path>`; if absent, remove that entry and print `Removed stale guidance manifest entry: <path>`. The `consumed_guidance[]` block is otherwise preserved verbatim during this step — its SHA values are not refreshed here (Step 8 handles refresh for the program being updated; other programs' blocks remain untouched).

Runs on every UPDATE invocation — ensures deleted or renamed input files don't cause change detection failures.

### Step 2: Detect Changes

For every source doc to be checked, look up its current blob SHA in `input_index_shas` (from the pre-flight JSON payload). Compare against the SHA stored in the extraction manifest. A mismatch (or a source doc absent from the manifest) means the file changed and must be re-extracted.

`input_index_shas` is the canonical SHA source — do not run `git hash-object` here. The drift check inside the pre-flight tool guarantees the index value reflects current working-tree bytes, so committed AND uncommitted edits are caught by the same lookup.

**Multi-file (work_list has more than one entry):**

For each entry in the work-list, run the comparison above against every path under that entry's `source_docs:`. If any source doc's current blob SHA differs from the stored `git_sha`, the entry is added to the set of files requiring re-extraction. Sub-module entries are reported with label `[sub-module: <name>]`. If no source doc for the entry changed, skip re-extraction for that entry (source-provenance-based scoping).

The main module is re-extracted if its own source docs changed. If only sub-module source docs changed, the parent main module is **also** re-checked when a sub-module's exported type changes — that propagation surfaces deterministically via the U2 clerk loop's `clerk typecheck` step in Step 6, not as a separate dedicated check in this skill. (Per U2's findings, `clerk typecheck` catches sub-module exported-type contract mismatches in the importing module; the `cross_module_contract` walker is therefore implicit in the loop's Step 1.)

**Single-file (work_list has one entry):**

Compare every entry in the manifest's `source_docs:` against `input_index_shas`, and additionally enumerate `input_index_shas`'s keys for paths that are present in the index but absent from the manifest (treat them as added). The pre-flight tool already filtered out entries with `md_quality.score < 40`, so rejected source files (moved to `input/rejected/`) do not surface as false additions. Collect the list of changed/added/deleted input docs.

### Step 3: No Changes — Exit Early

If no changes detected:
:::important
All input docs are up to date. Nothing to extract.
To re-extract anyway, delete or rename $DOMAINS_DIR/<domain>/specs/extraction-manifest.yaml and re-run.
:::
Stop. Do not modify any files.

### Step 4: Identify Affected Catala Sections

For each changed doc, read the diff and determine which Catala constructs need updating. The Catala source is literate Markdown with fenced blocks; updates target the relevant `## <Heading>` section and its adjacent `catala-metadata` / `catala` fenced blocks. See `../../core/catala-authoring-quickref.md` Parts 1–3 for the full mapping; the table below is the high-level guide.

| Type of Change in Input Doc | Affected Catala Constructs |
|---|---|
| Dollar thresholds by household size | `definition <table_name> equals [<TableRow>, ...]` lookups (with the `structure <TableRow>` declaration in a `catala-metadata` fence); possibly `definition` formulas for size 9+ extrapolation |
| Fixed rates or percentages | Scope-level `definition <NAME> equals <literal>` (named constant) |
| New applicant fields added | `declaration structure <Entity>` (data fields); possibly new `rule`s referencing them |
| New eligibility test or condition | `rule <condition> under condition <expr> consequence ...` (exception-default form for deny rules); possibly new `definition`s for intermediate values |
| Effective date change | `## Effective date` heading and the corresponding date literal `|YYYY-MM-DD|` in a `definition` |
| Jurisdiction change | `## Jurisdiction` heading prose + any enum/string definition encoding the jurisdiction |
| Deduction formula change | `definition <var> equals <expression>` for the affected computed variable; possibly `structure`/`enum` updates if a new category was added |

### Step 5: Re-edit Affected Sections

Recall the extraction goal from the pre-flight JSON payload (`metadata`, `prompt_context`, `output_variables`, `input_variables`, `guidance_output_set`, `constants_tables_seed`, plus `naming_manifest`) before re-reading any policy sections:

```
---
[content of metadata, prompt_context, output_variables, input_variables,
 guidance_output_set, constants_tables_seed, and naming_manifest from the
 pre-flight JSON payload]
---

Apply these constraints and standards when re-editing the affected Catala sections.
```

For each affected section, re-read the relevant parts of the changed policy doc — read the caveman-compressed copy at `$DOMAINS_DIR/<domain>/policy_facets/compressed/<rel>.md` rather than the raw source under `input/policy_docs/`. The compressed copy is the canonical content for AI consumption (see "Index path keys vs content reads" in `xl-plugin/CLAUDE.md`). Re-edit only the section(s) identified in Step 4. Do not touch sections not flagged.

**Name binding.** When the affected section's edit introduces or references a field, scope variable (`input`/`internal`/`output`/`context`), `definition`, or `rule` consequence-binding, use **only** the canonical names from `naming_manifest`. Inject the frozen names into your editing reasoning: "These identifiers must keep their exact current spelling: [list every name from manifest, organised by section]. Only introduce a new identifier for a policy concept not in this list, using the 4-step algorithm: (1) exact noun phrase, (2) strip entity-name words, (3) snake_case, (4) disambiguate if needed." **Never rename an existing identifier in this step** — renames are surfaced and anchored via the synonyms-append flow in Step 10, not by free-form rewrites here.

**Catala authoring discipline.** Apply the quickref idioms when emitting the patched fenced block(s):
- Literate Markdown structure — keep the `## <Heading>` line above each fenced block and the `*Source: input/policy_docs/<rel>.md — <§ citation> — <heading>*` italic-prose citation between the heading and the fence. Update the citation if the policy section moved.
- `catala-metadata` vs `catala` fence discipline — cross-module exports (structures, enums, scope declarations with `output` variables) belong in `catala-metadata` fences; internal `definition`s and `rule`s belong in `catala` fences. See quickref Part 3 "Fence visibility".
- Money and date literals follow `$N,NNN.NN` and `|YYYY-MM-DD|` forms (quickref Part 2.3).
- Enum constructor references use the full `Enum.Constructor` qualifier (quickref Part 2.2).
- Deny rules use the exception-default boilerplate (quickref Part 2.6).
- Denial-reasons accumulation uses the `list of Reason` idiom (quickref Part 3.1).

After re-editing, run **SP-OrchestrationFilter** (from `../../core/ruleset-shared.md`) on the newly edited rule components (not the full existing Catala file — only the rules and computed-value definitions touched in Step 4):
- Remove flagged components; display the SP-OrchestrationFilter summary table if any were flagged.
- Continue to Step 6 with the filtered set. Re-included components will have a Markdown comment added when merged.

### Step 6: Verify Catala source with the clerk loop

After the AI lands the in-place edits, run the multi-module clerk-loop orchestrator over every module in the work-list. The orchestrator drives `clerk typecheck` + `clerk test` per module (with the per-iteration in-loop naming-manifest check bypassed — see Step 0 for the rationale), then runs a single **aggregated** naming-manifest divergence check across the union of identifiers from every module. Single-file and multi-file paths both go through this script.

**Invocation:**

```bash
xlator clerk-loop-multi <domain> [<program>]
```

The script emits a JSON header line on stdout followed by `--- CLERK-LOOP-MULTI-HEADER-END ---` and a human-readable summary. Parse the JSON header (first stdout line); verify the sentinel on the second line. Header shape:

```json
{
  "status": "ok" | "unresolved" | "error",
  "mode": "full",
  "modules_checked": <int>,
  "modules_generated": <int>,
  "iterations_per_module": [{"module": "<name>", "iterations": <int>}, ...],
  "failed_module": null | "<name>",
  "verified_modules": ["<name>", ...],
  "diagnostic_count": <int>,
  "warnings": ["<msg>", ...]
}
```

This initial wiring re-verifies all modules in the work-list, not just the ones touched in Step 5. A future `--touched <name>,<name>...` flag on `clerk-loop-multi` could limit the per-module clerk-loop pass to touched modules while still aggregating across the full work-list; deferred to follow-up work.

Branch on `header.status`:

- **`status="ok"`** — every per-module clerk loop passed and the aggregated divergence check reported no mismatch. Surface a `:::important` summary that names the modules verified and the per-module iteration counts (from `iterations_per_module`), then continue to Step 6b.

- **`status="unresolved"`** — either a per-module loop halted (`header.failed_module` is set; surface the diagnostics listed under the sentinel) or the aggregated divergence check flagged one or more mismatches. Emit a `:::user_input` fence containing the human summary and the diagnostic block. Ask the analyst how to proceed:
  - **Naming divergence**: each diagnostic carries both resolution options in its message body — (a) hand-edit the affected Catala source(s) to restore the manifest spelling, or (b) edit `specs/naming-manifest.yaml` to acknowledge the rename (Step 10 anchors it via `synonyms:`). Apply the chosen resolution, then re-run Step 6. (Per-fenced-block re-edits scoped to a single diagnostic no longer apply — divergence is detected as a post-pass after every per-module loop has completed.)
  - **Cross-module contract failure** (typecheck error in the importing module after a sub-module exported-type change): the diagnostic surfaces under the sentinel with `category="type"` or `category="module"`. Re-edit the parent module's `> Using <SubModule>` directive consumers or the sub-module's `output` declaration in a `catala-metadata` fence, then re-run Step 6.
  - **Per-module clerk failure** (`header.failed_module` is set): the listed diagnostics come from the failing module's last `clerk typecheck` / `clerk test` iteration. Hand-edit the source, then re-run Step 6. `header.verified_modules` lists the modules whose per-module loops completed before the halt.

  Do not proceed to Step 6b until the script returns `status="ok"`.

- **`status="error"`** (exit 2) — pre-flight failure (missing domain, missing `specs/naming-manifest.yaml`, missing `clerk` on PATH, or a `load-extraction-context` failure). Surface the human summary in `:::error` and stop.

Operational note: each per-module loop calls `catala_runtime.reset_log()` between iterations by default (PR #45 prevention); the orchestrator inherits this behavior unchanged.

### Step 6b: Maintainability Self-Review

*Runs after Step 6 (clerk loop), before Step 7 (Merge).*

Run **SP-MaintainabilityReview** (from `../../core/ruleset-shared.md`) on the Catala file, **scoped to only the rules and computed-value definitions that were added or modified in this update** (identified in Step 4). Unchanged rules and definitions are not re-checked.

- SP-MaintainabilityReview applies in-place fixes for non-blocking items (M1–M4) where the fix is mechanical.
- If blocking item **M5** (duplicate priority within an exception-chain `mutex_group`-equivalent set of labeled definitions) fails:
  1. Display the conflicting `definition <var> under condition ...` labels and their priorities.
  2. Ask:
     :::user_input
     Two or more labeled definitions in the same priority-chain share the same explicit priority. Please assign unique priorities, then type 'continue'.
     :::
  3. Apply the user's corrections to the merged file.
  4. Re-run SP-MaintainabilityReview on the affected chain to confirm M5 is resolved.
- On SP-MaintainabilityReview completion: display the summary table.

Proceed to Step 7 only after SP-MaintainabilityReview passes (no blocking failures).

### Step 7: Merge into Existing Catala File

The re-edits from Step 5 are applied in-place to `$DOMAINS_DIR/<domain>/specs/<program>.catala_en`:
- Replace only the affected `## <Heading>` sections (heading line + `*Source: ...*` line + adjacent fenced blocks).
- Preserve all unchanged sections verbatim (including HTML-comment `<!-- review: ... -->` blocks and any hand-edits in unchanged sections).
- Preserve the file's overall literate-Markdown structure and `> Module <ModuleName>` / `> Using <SubModule>` preamble verbatim.

### Step 8: Update Manifest

Update `$DOMAINS_DIR/<domain>/specs/extraction-manifest.yaml`. Each `git_sha:` value is the source doc's blob SHA — read it from `input_index_shas` (already loaded in pre-flight; do not run `git hash-object` here). Field-name translation: the index field is `sha:`, the manifest field is `git_sha:`; the value is identical.

Refresh the `consumed_guidance[]` block for the program being updated, using `guidance_shas` from the pre-flight JSON payload. Enumerate every path in `guidance_shas` and write one `{path, sha}` entry per file. When `guidance_shas` is empty, write `consumed_guidance: []`. The same refresh rule applies to every sub-module of the program being updated. Other programs' `consumed_guidance[]` blocks (programs not touched by this run) are preserved verbatim — do not refresh them; their entries reflect provenance from their last `/extract-ruleset` or `/update-ruleset` run.

**Multi-file:** After successful re-edit, update `extracted_at` and source doc SHAs for each regenerated file (main module and sub-modules). Files that were not re-edited (no source doc changes and no cross-module propagation) retain their existing manifest entries verbatim. Sub-modules with `referenced: true` in the manifest retain their entry unchanged (they were not regenerated). Refresh `consumed_guidance[]` for every regenerated entry per the rule above.

**Single-file:**
- Update `git_sha` for each changed source doc
- Refresh `consumed_guidance[]` for this program using `guidance_shas`
- Update `extracted_at` to today's date

### Step 9: (reserved)

Validation is handled inside Step 6's clerk loop (`clerk typecheck` + `clerk test` + naming-manifest divergence check). No separate validate step is needed; this number is reserved for parity with `/extract-ruleset`'s numbering.

### Step 10: Update Naming Manifest

If any new fact, computed, or outputs fields were added during this update, OR if Step 6's divergence check surfaced an analyst-acknowledged rename: derive canonical names using the 4-step algorithm from `/extract-ruleset` Step 3b. Build an inventory JSON containing **one entry per new field and one entry per acknowledged rename** (and only those), then call:

```bash
xlator merge-naming-manifest <domain> <program> --inventory <tmpfile> --preserve-unmentioned
```

The `--preserve-unmentioned` flag is what differentiates this call from `/extract-ruleset` Step 7: `/update-ruleset` adds new fields without re-presenting the full inventory, so existing entries not referenced by this run's inventory must survive verbatim. (`/extract-ruleset` Step 7 presents the full inventory and the merge tool drops unmentioned entries by default.)

Inventory entry shape (same as `/extract-ruleset` Step 7):

```json
{
  "name": "<new snake_case identifier name>",
  "section": "inputs.<Entity>" | "computed" | "outputs",
  "policy_phrase": "<verbatim noun phrase from policy doc>" | null,
  "source_doc": "input/policy_docs/<rel>.md" | null,
  "section_text": "<§ citation> — <heading>" | null,
  "prior_name": "<previous specs key>" | null,
  "description": "<AI-inferred>" | null,
  "type": "<money|bool|int|float|string|enum|list|set|date|object>" | null,
  "values": null,
  "observed_synonyms": null
}
```

Rules:
- **Add only new fields and acknowledged renames.** Other existing entries are preserved by the `--preserve-unmentioned` flag.
- **`policy_phrase`** is the verbatim noun phrase from the source policy doc, scoped to the section where the new field was introduced. Read the caveman-compressed source at `policy_facets/compressed/<rel>.md`.
- **`prior_name` — rename-anchor convention (v10.1.0).** Set `prior_name` to the previous Catala identifier when the analyst acknowledged a rename in Step 6's divergence resolution and chose to update the manifest (resolution option (b)). The merge tool drops the old key and appends `{name: <prior_name>}` to the new entry's `synonyms:` list with no `source_doc:` or `section:` (rename-anchor synonyms carry no observation provenance, by `README-dev.md` v10.1.0). The append is idempotent on re-runs: the carry-forward synonyms list survives every rename round, so the full rename chain accumulates and any historical name resolves to the current canonical via `synonyms[].name`. See `core/ruleset-shared.md` SP-LoadNamingManifest "Rename lookup via `synonyms:`" for the consumer-side contract.
- **Optional fields** (`description`, `type`) are AI-inferred from policy text where the signal is unambiguous; otherwise omit.

If no new fields were added and no renames were acknowledged, skip the `merge-naming-manifest` call entirely — no inventory needed.

On non-zero exit from the tool: relay stderr in `:::error` and stop.

If no manifest exists yet (this should not happen because Step 0 requires it, but defensive case), `merge-naming-manifest` initializes it.

### Step 11: Write Stale-Cases Hint

Write `$DOMAINS_DIR/<domain>/specs/.stale-cases.yaml` for use by `/create-tests`:

```yaml
# Written by /update-ruleset. Consumed and deleted by /create-tests.
stale_cases:
  - case_id: "<case_id>"
    reason: "<what changed — e.g., 'gross_limit for household_size 3 changed from X to Y'>"
```

Include any test case whose `inputs` contain a value that was a table boundary or named-constant value in the prior Catala file but has changed in the updated version. If no cases are stale, write an empty list:
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
| `$DOMAINS_DIR/<domain>/specs/<program>.catala_en` | Updated (affected `## <Heading>` sections only) |
| `$DOMAINS_DIR/<domain>/specs/<sub_module>.catala_en` | Updated (multi-file: each re-edited sub-module) |
| `$DOMAINS_DIR/<domain>/specs/extraction-manifest.yaml` | Updated |
| `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml` | Updated by `xlator merge-naming-manifest --preserve-unmentioned` (Step 10, after Step 6 verification) |
| `$DOMAINS_DIR/<domain>/specs/.stale-cases.yaml` | Created (Step 11; consumed by `/create-tests`) |
| `$DOMAINS_DIR/<domain>/policy_facets/computations/<rel>.md.yaml` | Read-only (per-file section data; if present) |
| `$DOMAINS_DIR/<domain>/policy_facets/compressed/<rel>.md` | Read-only (canonical content for AI consumption) |
| `$DOMAINS_DIR/<domain>/specs/guidance/*.yaml` | Read (via `xlator load-extraction-context`) |
| `$DOMAINS_DIR/<domain>/policy_facets/input-index.yaml` | Read (via `xlator load-extraction-context`) |

Graph artifacts (`.graph.yaml`, `.mmd`) and guidance updates are written by `/review-ruleset`. Tests are handled by `/create-tests`.
