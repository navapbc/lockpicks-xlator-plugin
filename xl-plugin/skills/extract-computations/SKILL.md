---
name: extract-computations
description: Extract Per-File Section/Computation Data Into policy_facets/computations/
---

# Extract Per-File Section/Computation Data

Read one policy doc under `<domain>/input/policy_docs/`, parse its H1–H3 sections, and write a YAML list of `{heading, summary, tags, computations}` blocks to the mirrored destination at `<domain>/policy_facets/computations/<rel>.md.yaml`.

This skill is invoked per file by `/index-inputs` (in a loop over REINDEX entries) and may also be invoked standalone by the analyst against a single source file. The non-AI half (file enumeration, manifest sync, mirror-deletes, plan/finalize handoff) lives in `xlator extract-computations <domain> --plan` and `--finalize`; this skill is the AI half that does the per-file content generation.

## Input

```
/extract-computations <path_to_policy_file>
```

`<path_to_policy_file>` must resolve to a path under `<DOMAINS_DIR>/<domain>/input/policy_docs/`. If not, pre-flight emits `:::error` and stops.

Read `../../core/output-fencing.md` now.

## Pre-flight

Run these checks before doing anything else:

1. **Argument provided?**
   - NO →
     :::error
     Usage: /extract-computations <path_to_policy_file>
     :::
     Then stop.

2. **Path resolves under `<DOMAINS_DIR>/<domain>/input/policy_docs/`?**
   - Resolve `<path_to_policy_file>` to an absolute path. Accept absolute paths, paths relative to the project root, or paths relative to `$DOMAINS_DIR`.
   - Walk the resolved path's ancestors to find `input/policy_docs/`. The directory three levels up from `input/policy_docs/<rel>` (i.e., the ancestor whose child `input/policy_docs/` exists) is `<domain>`. The grandparent is `$DOMAINS_DIR`.
   - If the resolved path is not under `<DOMAINS_DIR>/<domain>/input/policy_docs/<rel>` for any valid `<domain>` →
     :::error
     Path must be under <domain>/input/policy_docs/. Got: <resolved-path>
     :::
     Then stop.

3. **Source file exists and is readable?**
   - File missing or unreadable →
     :::error
     Source file not found: <path>
     :::
     Then stop.

4. **Source has `.md` extension?**
   - Non-`.md` →
     :::error
     Only .md sources are supported in v1; got: <path>
     :::
     Then stop.

5. **md_quality gate.**
   - If `<DOMAINS_DIR>/<domain>/policy_facets/input-index.yaml` exists, look up `input/policy_docs/<rel>.md` in its `files:` block. If found and `md_quality.score < 40` (the project's REJECTED threshold) →
     :::error
     Source rejected by index (md_quality.score=<N>). Fix or remove from input/policy_docs/.
     :::
     Then stop.
   - If the file is not in the index, or no `input-index.yaml` exists yet (analyst running this skill before `/index-inputs`), proceed without the gate. The gate only fails on a known-low-quality source.

## Process Checklist

This skill has 4 steps:
- [ ] Step 1: Read source and parse H1–H3 sections
- [ ] Step 2: Generate per-section `{heading, summary, tags, computations}`
- [ ] Step 3: Write YAML list to `policy_facets/computations/<rel>.md.yaml`
- [ ] Step 4: Print summary

## Step 1: Read source and parse sections

Read the full file content at `<DOMAINS_DIR>/<domain>/input/policy_docs/<rel>.md`.

Extract all H1 (`#`), H2 (`##`), and H3 (`###`) headings in document order. For each heading, collect the section body — the text between this heading and the next heading of equal or higher level.

If the file has **no H1–H3 headings**, treat the whole file as a single section. The heading for that single section is the filename stem (without `.md`) prefixed with `#`.

## Step 2: Generate per-section data

For each section produce:

- **`heading:`** — verbatim heading text including the `#` / `##` / `###` prefix. The prefix encodes the level; do NOT strip it.
- **`summary:`** — one sentence describing what this section covers, in the policy's own terminology.
- **`tags:`** — 3–5 short noun-phrase tags (lowercase, hyphenated or single-word). These are downstream filtering signals.
- **`phase:`** — *optional* snake_case identifier naming the phase or stage of analysis the section belongs to. Populate ONLY when the source doc surfaces an explicit phase or stage signal — examples that justify a `phase:`:
  - A heading like `# Phase 1 — Initial Screening` (the heading itself is the signal).
  - A parent heading several levels above the current section (e.g., the section's H3 sits under an H1 `Phase 2: Detailed Eligibility` — the phase label is attributable to the ancestor).
  - A body sentence like *"the computations below run as Phase 2 of the eligibility test"* — explicit phase wording in prose, anchored to the section.

  Convert the surfaced label to a snake_case identifier (`Phase 1 — Initial Screening` → `initial_screening`). Omit the field entirely when no such signal exists in or above the section. **Inventing a `phase:` when the source has no signal degrades downstream defaults — an absent field is stronger than a hallucinated one.**
- **`phase_source:`** — required when `phase:` is present, omitted when `phase:` is omitted. Value is the **verbatim source-text phrase** that justified the `phase:` identifier — copied character-for-character from the source `.md`, no paraphrasing, no truncation that breaks substring matching. Downstream consumers run `grep -F "<phase_source>" <input/policy_docs/<rel>.md>` to verify the AI honored the explicit-signal rule. If you cannot find a verbatim quote in the source, the signal is not explicit — omit `phase:` entirely rather than invent or paraphrase.
- **`computations:`** — *optional* list. Include only if the section contains identifiable rule logic (formulas, arithmetic, table lookups, thresholds, conditional assignments). Each entry has:
  - `description:` — one sentence describing the computation in plain language.
  - `variables:` — all variable names involved, **inputs first, computed output last**, snake_case, inferred from policy terminology.
  - `preconditions:` — *optional* boolean expression describing when the `expr_hint:` applies, derived from the section's own heading, its parent headings, and the surrounding text. The value is a list of **terms** joined by implicit AND at the top level. Each term is one of:
    - a string clause — a self-contained predicate in plain language; reference variable names from `variables:` where possible (e.g., `"household contains a working adult"`, `"var2 > 0"`).
    - `{all_of: [<term>, ...]}` — an explicit AND group; useful for nesting inside `any_of`.
    - `{any_of: [<term>, ...]}` — an OR group; terms inside may themselves be string clauses or further `all_of` / `any_of` groups, so arbitrary nesting is allowed.

    Example: a parent H2 "Working Adults" plus a phrase "if the applicant is over 65, or is married to an employed spouse" yields
    ```yaml
    preconditions:
      - "household contains a working adult"
      - any_of:
          - "applicant is over 65"
          - all_of:
              - "applicant is married"
              - "spouse is employed"
    ```
    Omit the field when the computation applies unconditionally within the section.
  - `expr_hint:` — *optional* short formula or expression fragment. Include when a formula or condition is stated or clearly implied; omit when the logic is descriptive only.

  **Omit the `computations:` field entirely** when no rule logic is present. Do not emit `computations: []` — an empty list is never correct.

## Step 3: Write the YAML list

Compute the destination: `<DOMAINS_DIR>/<domain>/policy_facets/computations/<rel>.md.yaml` (where `<rel>.md` mirrors the source filename verbatim, with `.yaml` appended so the file's content type is unambiguous to editors and tooling). Create intermediate directories as needed.

Write a YAML file with this preamble and shape (use Bash `printf`/heredocs; do not hand-format YAML — use the YAML writer pattern from `xlator print-sections` if writing programmatically):

```yaml
# Auto-generated by /extract-computations — do not edit manually
# Source: input/policy_docs/<rel>.md

- heading: "# Section Title"
  summary: "One sentence describing what this section covers."
  tags: [tag1, tag2, tag3]
  phase: initial_screening
  phase_source: "Phase 1 — Initial Screening"
  computations:
    - description: "..."
      variables: [var1, var2, output_var]
      preconditions:
        - "household contains a working adult"
        - any_of:
            - "applicant is over 65"
            - all_of:
                - "applicant is married"
                - "spouse is employed"
      expr_hint: "var1 * 0.20"

- heading: "## Subsection"
  summary: "..."
  tags: [tag4, tag5]
  # phase / phase_source omitted — no explicit phase signal in or above this section
  # computations omitted — no rule logic in this section
```

YAML conventions:
- Top-level value is a YAML list. There is no wrapping `sections:` or `path:` key — the on-disk filename encodes the source path. Strip the trailing `.yaml` suffix from the destination's relative path under `policy_facets/computations/` to recover `<rel>.md`, then prefix with `input/policy_docs/` to reach the source.
- Two-space indentation throughout.
- `heading:` quoted (the value contains `#` characters).
- `tags:` as an inline bracket list.
- `phase:` as an unquoted snake_case identifier; `phase_source:` quoted (preserves whitespace and punctuation needed for `grep -F` to match the source verbatim).
- **List order in `computations:` reflects source order.** Downstream consumers (notably `/create-ruleset-modules`'s `sequential_chain` heuristic) rely on this — within a section, the first computation in the list is the first in document order, the second is next, and so on.
- One blank line between section entries for readability.
- **No `path:` field** — the destination filename mirrors `<rel>` exactly.

Always rewrite the destination file in full; this skill is idempotent at the file level. Per-file caching is the manifest's job (handled by `xlator extract-computations --finalize`), not the skill's.

## Step 4: Print summary

:::important
✓ Wrote policy_facets/computations/<rel>.md.yaml (<K> section(s)).
:::

Do NOT emit `:::next_step` from this skill — it is per-file and is normally invoked from a parent loop. The parent (e.g., `/index-inputs`) emits the workflow's next-step suggestion.

## Common Mistakes to Avoid

- **Don't include a `path:` field** — the destination filename encodes the source path; `path:` is redundant and was removed in this version.
- **Don't omit the heading prefix** — `heading: "# Title"` not `heading: "Title"`; the `#` characters encode the level.
- **Don't merge all sections from a file into one entry** — each H1/H2/H3 heading is its own entry.
- **Don't emit `computations: []`** for sections with no rule logic — omit the field entirely.
- **Don't update the manifest from this skill** — the manifest is the single responsibility of `xlator extract-computations --finalize`. When invoked standalone (outside `/index-inputs`), the per-file file is written but the manifest is not updated; the next `--plan` will simply re-extract this file (matching destination + missing manifest entry → `to_extract`). This is acceptable best-effort behavior for the standalone path.
- **Don't write `policy_facets/input-sections.yaml`** — that artifact is removed in v3.0.0. All section data lives in per-file `policy_facets/computations/<rel>.md.yaml` files.
- **Don't read or mutate any pre-existing `input-sections.yaml`** — leave it on disk untouched. Maintainers delete it manually.
- **Don't run this skill on a low-md_quality source** — the pre-flight gate refuses files whose `md_quality.score < 40`. If the gate fires, fix the source or remove it from `input/policy_docs/`.
- **Don't invent a `phase:` value when the source has no explicit signal** — phases must be anchored to a heading, body sentence, or attributable ancestor heading. An absent `phase:` is the safe default; hallucinated phases flow through `/create-ruleset-groups` into `guidance/ruleset-groups.yaml` and ultimately produce `validate_civil.py` rejection at the `/extract-ruleset` stage.
- **Don't paraphrase `phase_source:`** — it must be a verbatim substring of the source `.md` so `grep -F "<phase_source>" <source>` matches. If you cannot find a verbatim quote in the source, the signal is not explicit — omit `phase:` entirely rather than invent or paraphrase.
- **Don't emit `phase:` without `phase_source:`** (or vice versa) — the two fields ship together. The quote is the proof that the AI honored the explicit-signal rule.
