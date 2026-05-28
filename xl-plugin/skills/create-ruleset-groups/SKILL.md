---
name: create-ruleset-groups
description: Propose and Write Ruleset Groups for a Domain
---

# Propose and Write Ruleset Groups for a Domain

Scan the per-file files under `policy_facets/computations/` for stage signals, propose `ruleset_groups`, and write them to `guidance/ruleset-groups.yaml`. The deterministic (1a) explicit-`stage:` scan, the UPDATE-m merge precedence, and the `display_name`-derived catch-all all run in `xlator scan-ruleset-groups`; this skill owns the domain menu, the UPDATE-mode `[a/r/m]` prompt, the optional AI heading-text top-up (1b) when the tool flags it, and the manifest record call.

A "Ruleset Group" is synonymous with a "ruleset group".

## Input

```
/create-ruleset-groups <domain>
```

Read `../../core/output-fencing.md` now.

## Pre-flight

1. **Domain argument provided?**
   - NO → List all directories matching `$DOMAINS_DIR/*/` as a numbered menu and prompt:
     :::user_input
     Available domains:
       1. snap
       2. ak_doh
     Which domain? Enter a number or domain name:
     :::
     Await the user's response and use it as `<domain>`. Then continue.

The tool re-validates every other pre-flight condition (domain folder, `metadata.yaml`, `skeleton.yaml`, and a non-empty `policy_facets/computations/`) and exits 2 with a clear stderr message if any required file is missing. Relay the stderr verbatim inside `:::error` and stop.

## Mode Detection

Check whether `$DOMAINS_DIR/<domain>/specs/guidance/ruleset-groups.yaml` exists:

- **Absent** → CREATE mode. Tool mode: `create`. Proceed to Process.
- **Present** → UPDATE mode. Display the existing list and prompt:
  :::user_input
  ruleset_groups already defined:
    1. <name> — <description>
    2. <name> — <description>

  [a]ccept / [r]eplace / [m]erge?
  :::
  - `a` → Exit without invoking the tool. Emit:
    :::next_step
    Next: Run /create-ruleset-modules <domain> to detect ruleset module candidates.
    :::
  - `r` → Tool mode: `replace`. Proceed to Process.
  - `m` → Tool mode: `merge`. Proceed to Process.

## Process

1. Run the deterministic scan tool:

   ```bash
   xlator scan-ruleset-groups <domain> --mode <create|replace|merge>
   ```

2. Parse the tool's stdout. The first line is a single-line JSON header; the line `--- SCAN-RULESET-GROUPS-HEADER-END ---` divides the header from the human-readable proposal table.

3. **Optional heuristic (1b) heading-text top-up.** When the JSON header has `heading_text_fallback_recommended == true`, the tool has produced nothing the skill can use:
   - Glob `$DOMAINS_DIR/<domain>/policy_facets/computations/**/*.md.yaml`. For each section, inspect its `heading:` for stage-naming patterns ("Income Test", "Household Size Verification", "Categorical Eligibility").
   - Convert each detected heading to `{name: <snake_case>, description: <heading text>}`. Examples: `"Income Test"` → `{name: "income_test", description: "Income Test"}`.
   - Write the candidate list as a JSON array to a tmpfile (e.g. via `tempfile.NamedTemporaryFile(suffix='.json')`).
   - Re-invoke the tool with the same mode and the candidate file path:
     ```bash
     xlator scan-ruleset-groups <domain> --mode <same-mode> --heading-derived-candidates <tmpfile>
     ```
   - Re-parse the new stdout for relay below.

4. Relay the proposal table (everything after the sentinel divider) verbatim inside `:::detail`.

5. Print:
   :::important
   $DOMAINS_DIR/<domain>/specs/guidance/ruleset-groups.yaml [CREATED]
   :::

6. Record the guidance-tier manifest so `/check-freshness` can later detect drift between `policy_facets/` and this skill's outputs:

   ```bash
   xlator record-tier-manifest <domain> --tier guidance
   ```

   If the command exits non-zero, emit `:::error` with the captured stderr and stop — do not proceed to `:::next_step`.

7. Suggest next steps:
   :::next_step
   Next: Run /create-ruleset-modules <domain> to detect ruleset module candidates.
   :::

## Output

| File | Action |
|------|--------|
| `$DOMAINS_DIR/<domain>/specs/guidance/ruleset-groups.yaml` | Created (first run) or merged (subsequent runs) |

## Common Mistakes to Avoid

- The tool enforces the suffix-normalization rule (`stage: income_test` and `stage: income` collapse to one canonical `income` group) and analyst-edit stickiness on `merge` collisions for stage-derived candidates — do not bypass either by hand-editing the file mid-run.
- The optional (1b) heading-text AI top-up only fires when `heading_text_fallback_recommended == true` in the JSON header. Do not AI-scan headings when the tool has already produced candidates.
- In UPDATE mode `[a]ccept`, exit without invoking the tool — do not overwrite existing `ruleset-groups.yaml` content.
