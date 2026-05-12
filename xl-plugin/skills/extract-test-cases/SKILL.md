---
name: extract-test-cases
description: Extract Test Cases from Policy Documents
---

# Extract Test Cases from Policy Documents

Scan policy input documents for concrete numerical examples and write them to `extracted-tests.yaml` for use by `/create-tests` and `/expand-tests`.

## Input

```
/extract-test-cases [<domain>]                  # auto-detect program or prompt if ambiguous
/extract-test-cases [<domain> <program>]        # target a specific <program>.civil.yaml
```

If `<domain>` is not provided, list all `$DOMAINS_DIR/*/specs/*.civil.yaml` files and prompt the user to choose.

Read `../../core/output-fencing.md` now.

## Pre-flight

1. **Domain folder exists?** — NO → Print:
   :::error
   Domain `<domain>` not found. Run `/extract-ruleset <domain>` first.
   :::
   Stop.
2. **CIVIL file exists?**
   - `$DOMAINS_DIR/<domain>/specs/<program>.civil.yaml` missing → Print:
     :::error
     No CIVIL file found. Run `/extract-ruleset <domain>` first.
     :::
     Stop.
3. **`input/` has documents?** — If `$DOMAINS_DIR/<domain>/input/` is empty or absent → Print:
   :::error
   No input documents found in `$DOMAINS_DIR/<domain>/input/`. Add policy documents and re-run.
   :::
   Stop.

## Check State

```bash
ls $DOMAINS_DIR/<domain>/policy_facets/extracted-tests.yaml 2>/dev/null
```

**If `extracted-tests.yaml` already exists:**
:::user_input
Found `extracted-tests.yaml` with N cases (last extracted from M documents). Re-extract from input docs, or keep existing?
Options: `[r]e-extract / [k]eep`
:::

If `[keep]`, stop.

## Extraction Algorithm

### Step 1: Build the source map

Read `$DOMAINS_DIR/<domain>/specs/<program>.civil.yaml` and collect:

- **Program vocabulary** — all field names from `inputs:`, `computed:`, and `outputs:`
- **Source citations** — all `source:` strings present on any `FactField`, `ComputedField`, `TableDef`, or `Rule`. These are free-text CFR section strings (e.g., `"7 CFR § 273.9(a)(1) — Gross Income Test"`). Collect them into a **source citation list**.

### Step 2: Identify relevant files

For each compressed mirror under `$DOMAINS_DIR/<domain>/policy_facets/compressed/` (scanned recursively for `*.md`), check relevance using the source citation list first, then fall back to vocabulary. Path-relative mirroring: a mirror at `policy_facets/compressed/<rel>.md` corresponds to source doc `input/policy_docs/<rel>.md` — record `<rel>` so it can be reconstituted into the canonical `source.file` path in the output.

1. **Citation match (preferred):** does the mirror's path or content contain a section identifier that appears in any source citation (e.g., "273.9", "441.2")? If yes, mark the file as relevant.
2. **Vocabulary match (fallback):** if no citation match, does the mirror reference at least one term from the program vocabulary? If yes, mark the file as relevant.
3. **No match:** skip the file and note: "Skipped `<rel>.md` — no terms matching `<program>` source citations or vocabulary."

If `policy_facets/compressed/` is absent or empty, fall back to scanning `$DOMAINS_DIR/<domain>/input/policy_docs/` directly and read source docs throughout — log: `⚠ Compressed mirrors missing — run /index-inputs <domain> to enable token-efficient reads.`

### Step 3: Extract examples

Read content from the caveman-compressed mirror at `$DOMAINS_DIR/<domain>/policy_facets/compressed/<rel>.md` by default — these mirrors are token-reduced derivatives of the source docs and are sufficient for most example extraction. Only fall back to the source doc at `$DOMAINS_DIR/<domain>/input/policy_docs/<rel>.md` when the compressed text is ambiguous, unclear, or questionable for the specific example you are trying to extract (e.g., concrete numerical values appear truncated or elided, a referenced table is mentioned but its rows are not reproduced in the mirror, or the calculation walk-through has been compressed past the point of being usable). The compressed mirror is the default; the source read is the escape hatch.

Within each relevant file:

1. Focus on sections whose headings or content match a source citation from the source citation list — these are the highest-priority areas for examples.
2. Look for: numbered examples ("Example 1:", "Ex. 1"), "Illustration:", tables with named rows, paragraphs that give a specific scenario with concrete input values and walk through a calculation step-by-step.
3. For each found example, confirm it exercises the `<program>` ruleset (i.e., its inputs or conclusions reference the program vocabulary or a cited section). Discard examples that belong to a different program or benefit type.
4. Map the stated values to CIVIL input fact field names using the program vocabulary as a dictionary. If a needed value is missing or unclear in the compressed mirror, re-read the same section from the source doc per the fallback rule above before giving up on the example.
5. If a value cannot be mapped to a known input fact field (e.g., an intermediate value like "20% of gross earnings" that is a `computed:` field, not a fact input), record it in the `notes:` YAML key and omit from `inputs` — do not guess or coerce.
6. Infer all `expected.*` output decision fields (e.g., `expected.eligible`, `expected.reasons`, `expected.adjusted_income`) from the document's stated conclusion, **not** from running the rules.
7. Tag as `["extracted"]` plus any applicable rule tags (e.g., `"allow"`, `"deny"`, `"earned_income"`).
8. If a relevant file contains no concrete numerical examples (neither in the compressed mirror nor in the source doc when consulted as a fallback), **do not fabricate them** — report: "No concrete examples found in `<rel>.md`." and continue to the next file.

Regardless of which mirror was read, write `source.file` in the output as the canonical source path under `input/policy_docs/<rel>.md` (see Output Format below). The compressed mirror is an implementation detail of the read; the canonical citation is always the source doc.

## Output Format

Write to `$DOMAINS_DIR/<domain>/policy_facets/extracted-tests.yaml` (create the `policy_facets/` directory if absent):

:::important
Created $DOMAINS_DIR/<domain>/policy_facets/extracted-tests.yaml
:::

Then record the tests-tier manifest so `/check-freshness` can later detect drift between `specs/*.civil.yaml` and the freshly-written `extracted-tests.yaml`:

```bash
xlator record-tier-manifest <domain> --tier tests
```

If the command exits non-zero, emit `:::error` with the captured stderr and stop.

**Note on the `[keep]` path:** when the analyst chose `[keep]` at the pre-flight prompt (line 52 above) the skill exits before reaching this output step, and the manifest is **not** rewritten. This is intentional: the prior manifest reflects the civil SHA at the time `extracted-tests.yaml` was actually written. If civil has since changed, the subsequent `/check-freshness` run will correctly report `tier4 tests_stale` against that recorded SHA, which is the desired drift signal.

:::detail
# Auto-generated by /extract-test-cases. Do not edit directly —
# re-run /extract-test-cases and choose "re-extract" to refresh.
extracted_tests:
  - case_id: "ext_001"
    description: "Example 1 from §441.2: household of 3, earned income $2,100/mo"
    source:
      file: "$DOMAINS_DIR/<domain>/input/policy_docs/apa_manual/441/441.2-earned-income.md"  # repo-root-relative
      section: "Example 1"
    inputs:
      household_size: 3
      gross_earned_income: 2100
      # flat key-value only — same rules as <program>_tests.yaml
    expected:
      eligible: true
      reasons: []
    tags: ["extracted", "allow", "earned_income"]
    notes: "Document also states gross_earned_exclusion=$<amount> (<percentage> of gross) — computed field, excluded from inputs"
:::

Rules:
- `case_id` values are `ext_001`, `ext_002`, … — assigned sequentially across all input files
- `source.file` is always relative to repo root
- `notes:` is optional — include only when unmappable values need recording
- Inputs are always flat key-value, never nested

## Common Mistakes to Avoid

- **Default to the compressed mirror; read source only as a fallback** — Step 3 reads `policy_facets/compressed/<rel>.md` first. Only fall back to `input/policy_docs/<rel>.md` when the compressed text for a specific example is ambiguous, unclear, or questionable (e.g., truncated numerical values, missing table rows, elided calculation steps). Do not pre-emptively read source docs to save a second pass — the mirror is sufficient for the majority of examples.
- **`source.file` always cites the source doc, not the mirror** — write `input/policy_docs/<rel>.md` in the output regardless of which file was actually read.
- **Don't fabricate examples** — if a document has no concrete numerical examples, report it and move on
- **Don't coerce unmappable values** — intermediate computed values belong in `notes:`, not `inputs:`
- **Don't nest inputs** — inputs are always flat key-value, never nested by entity name
