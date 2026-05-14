---
name: create-ruleset-modules
description: Detect Ruleset Modules for a Domain
---

# Detect Ruleset Modules for a Domain

Detect ruleset modules — reusable sub-rulesets within a ruleset group — for a domain. The deterministic detection runs in `xlator detect-ruleset-modules`; this skill owns the domain menu, the optional AI top-up for heuristic 1c (cross-source comparison language), the main-module-name prompt when no primary output is declared, and the manifest record call.

A ruleset module is a subset of rules within a ruleset group. Ruleset modules must not cross ruleset group boundaries — the tool enforces the R21 stage-boundary constraint.

## Input

```
/create-ruleset-modules <domain>
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

The tool re-validates every other pre-flight condition (domain folder, `metadata.yaml`, `skeleton.yaml`, `ruleset-groups.yaml`, `naming-manifest.yaml`, and a non-empty `policy_facets/computations/`) and exits 2 with a clear stderr message if any required file is missing. Relay the stderr verbatim inside `:::error` and stop.

## Process

1. Run the deterministic detection tool:

   ```bash
   xlator detect-ruleset-modules <domain>
   ```

2. Parse the tool's stdout. The first line is a single-line JSON header; the line `--- DETECT-RULESET-MODULES-HEADER-END ---` divides the header from the human-readable body.

3. Relay the body (the human-readable detection table) in `:::detail`.

4. **Main-module-name fallback.** If the JSON header has `main_module_name == null`, prompt the analyst:

   :::user_input
   No primary output found in guidance/output-variables.yaml. What should the main module be named? (e.g., `eligibility`, `income_test`)
   :::

   Re-invoke the tool with the analyst's response so the main entry is written:

   ```bash
   xlator detect-ruleset-modules <domain> --main-module-name <name>
   ```

5. **Optional heuristic 1c AI top-up.** When the JSON header has `cross_source_language_scan_recommended == true`, scan the per-file files under `$DOMAINS_DIR/<domain>/policy_facets/computations/` for cross-source comparison phrases ("apply X to both A and B", "reasonably compatible", "compare A against B"). If candidate sub-modules surface, present them in a `:::detail` block and ask the analyst whether to append them. On confirmation, append the entries directly to `specs/guidance/ruleset-modules.yaml` (preserving everything the tool wrote). This step is optional — the analyst may skip.

6. If the tool's `dropped_candidates` array is non-empty, relay the dropped names and reasons in a `:::progress` block so the analyst sees what R21 split-or-drop removed.

7. Record the guidance-tier manifest so `/check-freshness` can later detect drift between `policy_facets/` and this skill's outputs:

   ```bash
   xlator record-tier-manifest <domain> --tier guidance
   ```

   If the command exits non-zero, emit `:::error` with the captured stderr and stop — do not proceed to `:::next_step`.

8. Suggest next steps:

   :::next_step
   Next: Run /extract-sample-rules <domain> to extract sample rules.
   :::

## Output

| File | Action |
|------|--------|
| `$DOMAINS_DIR/<domain>/specs/guidance/ruleset-modules.yaml` | Created (first run) or merged (subsequent runs) |

## Common Mistakes to Avoid

- The tool enforces the R21 stage-boundary constraint — do not hand-edit candidates to span stages.
- The tool enforces priority-order suppression (`reuse_across_entities > policy_structure > sequential_chain > depth_threshold > variable_coupling > shared_gate`) — do not duplicate heuristic intent across entries.
- Re-runs preserve existing entries verbatim, including `sample_rules:`, `description:`, `depends_on:`, and `role:`. To remove or rename an entry, edit `ruleset-modules.yaml` directly between runs.
