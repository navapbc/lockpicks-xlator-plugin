---
name: review-ruleset
description: Review Extracted Ruleset
---

# Review Extracted Ruleset

Present a computation graph preview and human review gate for an already-extracted Catala ruleset, then refresh graph artifacts and capture guidance learnings.

## Input

```
/review-ruleset <domain>                          # auto-detect program or prompt if ambiguous
/review-ruleset <domain> <program>                # target a specific <program>.catala_en
```

If `<domain>` is not provided, list all `$DOMAINS_DIR/*/specs/*.catala_en` files and prompt the user to choose.

---

Read `../../core/ruleset-shared.md` now. It contains shared procedures (SP-ComputeGraph, SP-TagOutputs, SP-GuidanceCapture, SP-CompleteExtraction, SP-ResolveRulesetModules) referenced in the steps below.

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

2. **Catala source exists?**
   - **If `<program>` was given:** check if `$DOMAINS_DIR/<domain>/specs/<program>.catala_en` exists. If not:
     :::error
     No Catala source found for <program>. Run /extract-ruleset <domain> <program> first.
     :::
     Stop.
   - **If `<program>` was not given:** check `$DOMAINS_DIR/<domain>/specs/*.catala_en`:
     - 0 files →
       :::error
       No Catala source found for this domain. Run /extract-ruleset <domain> first.
       :::
       Stop.
     - 1 file → use it; set `<program>` to the filename stem.
     - 2+ files → list them and prompt:
       :::user_input
       Multiple Catala sources found:
         - <program1>
         - <program2>
         ...
       Which program would you like to review?
       :::

3. **Input docs present?** — Run shared pre-flight check 3 from `../../core/ruleset-shared.md`.

4. **Load guidance files** — Run shared pre-flight check 5 from `../../core/ruleset-shared.md`.

**After Check 4 (guidance files loaded):** Run **SP-ResolveRulesetModules** with context `extract`. Store the returned work-list for use in Steps 1–3 below.
- If SP-ResolveRulesetModules emits an abort signal → stop with the message SP-ResolveRulesetModules printed.
- If the work-list has exactly one entry (ruleset_modules: empty) → proceed as single-file path throughout.

---

## Process

### Step 1: Generate Computation Graph (Preview)

**Multi-file:** Run for each `generate` entry in the work-list, in work-list order.

```bash
xlator graph <domain> <program>
```

`xlator graph` routes to the canonical Catala graph generator (`tools/catala_depgraph.py`),
which reads `$DOMAINS_DIR/<domain>/specs/<program>.catala_en` and writes
`<program>.graph.yaml` and `<program>.mmd` next to the source.

Always run unconditionally — regenerates even if graph files already exist from a prior run.
Capture stdout. Do not echo verbatim.

**On success (exit 0):**
Read `$DOMAINS_DIR/<domain>/specs/<program>.graph.yaml`. Extract all nodes where `kind == "computed"`.
Then embed the generated diagram inline:

````mermaid
[contents of $DOMAINS_DIR/<domain>/specs/<program>.mmd]
````

**On failure (exit 1):**
:::important
Warning: computation graph preview could not be generated — continuing to review.
:::
Proceed to Step 2 without showing graph content.

### Step 2: Human Review Gate

**Multi-file:** Run the review gate sequentially per file in work-list order. For each `generate` entry, present a full review gate (computation graph + review summary) with the header:
```
Review Gate (File N of M): <module_name>  [$DOMAINS_DIR/<domain>/specs/<module_name>.catala_en]
```
On rejection within a file's gate: re-extract only that file (see **On rejection** below). Proceed to the next file only after the current file's gate passes. Skip `reference` entries (no review gate for files not regenerated).

After all per-file gates pass, continue to Step 3.

**Single-file:** no per-file header; existing review gate behavior.

**If Step 1 succeeded**, show the following block before the `Review summary:` header:

:::detail
✓ Mermaid computation graph: $DOMAINS_DIR/<domain>/specs/<program>.mmd

Computation graph (computed fields):
  <field_1>    ← <dep_1>, <dep_2>    → <used_by_1>
  <field_2>    ← <dep_1>             → [rule: <rule_id>]
  ...
:::

Format each line as: `<node_key>  ← <depends_on list>  → <used_by list>`
- `depends_on`: join with `, ` — raw field names; no decoration
- `used_by`: prefix rule nodes with `[rule: ]`; plain names for computed refs
- Empty `depends_on`: show `← [no deps]`; empty `used_by`: show `→ [unused]` (potential dead-code)
- Zero computed fields: replace the table with `(No computed fields in this module.)` but still show the Mermaid block

---

Run the deterministic bucket-partitioning tool:

```bash
xlator review-buckets <domain> <program>
```

**On non-zero exit:** relay stderr in `:::error` and stop.

**On exit 0:** stdout has the shape
```
<single-line JSON header>
--- REVIEW-BUCKETS-HEADER-END ---
<formatted body>
```

Parse the JSON header. It carries `summary.{uncertain, complex, verified, unscored, total}` (item counts) and `item_ids.{uncertain, complex, verified, unscored}` (raw IDs, no display prefix). Relay the body verbatim inside `:::detail`:

:::detail
<contents of body, from after the sentinel line to end of stdout>
:::

The body is one of:
- The summary header line (`Review summary: X uncertain, Y complex, Z verified  (N items total)`), followed by per-bucket sections in fixed order: Uncertain → Complex → Verified → Unscored. Empty sections are omitted.
- The string `All items verified — no uncertain or complex items.` when every item is Verified.

The `Unscored` bucket surfaces entries whose `review:` block is absent. Legacy domains may surface items here; back-fill the scores by re-running `/extract-ruleset` Step 4's scoring pass for those entries.

Ask:
:::user_input
Does this translation correctly capture the policy intent? Any rules missing or incorrect?
:::

**On rejection:** Read the relevant policy document section for the disputed rule or computed field. Re-draft only that item from the policy text, applying the naming and scoring conventions from the extraction rubric. Re-run the clerk-loop (`xlator clerk-loop <domain> <module>`) to verify the re-drafted item typechecks and tests pass. Recompute the `review:` scores for the re-drafted item. Re-invoke `xlator review-buckets` and re-present the full review gate. Do not proceed until the user confirms.

### Step 3: Finalize Outputs

**SP-TagOutputs (output tagging):** Run **SP-TagOutputs** once per `generate` entry in the work-list, in work-list order.

**Multi-file SP-TagOutputs behavior:** For sub-module files, before displaying the ranked list, pre-select and lock any output fields whose names appear in the main module's Catala scope-call dot-access expressions (e.g., if the main module accesses `client_result.net_income` from `client_result scope ClientResult.ClientResult`, then `net_income` in the sub-module's output set is locked as `[REQUIRED for parent scope-call]` and cannot be deselected). Display locked fields at the top of the ranked list marked `[REQUIRED]`. Fields in the guidance output set (`[GUIDANCE]`) follow, then remaining fields in standard SP-TagOutputs rank order. See SP-TagOutputs in `../../core/ruleset-shared.md` for the full tier logic.

Run **SP-ComputeGraph** after all files in the work-list have been reviewed and approved. In multi-file context, run SP-ComputeGraph for each `generate` entry separately.

### Step 4: Capture Learnings

Run **SP-GuidanceCapture** once per per-file review gate that passed (after each file's gate, not after all files). In multi-file context, call SP-GuidanceCapture with the current module name so it can prefix candidates with `[module: <name>]`.

**SP-CompleteExtraction (multi-file footer):** Run **SP-CompleteExtraction**. In multi-file context, prepend to the footer:
:::important
Files reviewed:
  - $DOMAINS_DIR/<domain>/specs/<sub_module_name>.catala_en  [generated]
  - $DOMAINS_DIR/<domain>/specs/<sub_module_name2>.catala_en  [referenced]
  - $DOMAINS_DIR/<domain>/specs/<program>.catala_en  [generated]

extraction-manifest.yaml now tracks N files.
:::
Then show the standard SP-CompleteExtraction footer (next steps).

---

## Output

Files created or modified by this command:

| File | Action |
|------|--------|
| `$DOMAINS_DIR/<domain>/specs/<program>.graph.yaml` | Generated (Step 1) / Refreshed (Step 3) |
| `$DOMAINS_DIR/<domain>/specs/<program>.mmd` | Generated (Step 1) / Refreshed (Step 3) |
| `$DOMAINS_DIR/<domain>/specs/guidance/metadata.yaml` | Read (required) |
| `$DOMAINS_DIR/<domain>/specs/guidance/prompt-context.yaml` | Read (required) / Updated by SP-GuidanceCapture (Step 4) if guidance items accepted |
| `$DOMAINS_DIR/<domain>/specs/guidance/output-variables.yaml` | Read (required) |
| `$DOMAINS_DIR/<domain>/specs/guidance/include-with-output.yaml` | Read (if present) |
| `$DOMAINS_DIR/<domain>/specs/guidance/constants-and-tables.yaml` | Read (if present) |
| `$DOMAINS_DIR/<domain>/specs/guidance/ruleset-modules.yaml` | Read (if present) |
