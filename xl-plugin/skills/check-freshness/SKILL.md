---
name: check-freshness
description: Check Domain Freshness
---

# Check Domain Freshness

Detect drift across the full xlator derivation chain for a domain — `input/policy_docs/` → `policy_facets/` → `specs/guidance/` → `specs/*.catala_en` → `specs/tests/` — by comparing recorded per-tier SHA manifests against current working-tree state. Read-only: this skill does not modify any artifact.

## Input

```
/check-freshness [<domain>]
```

If `<domain>` is not provided, list all directories matching `$DOMAINS_DIR/*/input/policy_docs/` as a numbered menu, prompt the user to choose, await their response, and use it as `<domain>` before continuing.

Read `../../core/output-fencing.md` now.

## Pre-flight

1. **Domain argument provided?**
   - NO → List all directories matching `$DOMAINS_DIR/*/input/policy_docs/` as a numbered menu and prompt:
     :::user_input
     Available domains:
       1. snap
       2. example_domain
     Which domain? Enter a number or domain name:
     :::
     Await the user's response and use it as `<domain>`. Then continue.

2. **Domain folder exists?**
   - NO →
     :::error
     Domain not found: $DOMAINS_DIR/<domain>/
     :::
     Then stop.

## Execution

Run the freshness check:

```bash
xlator check-freshness <domain>
```

Open a `:::detail` fence. Relay the tool's stdout verbatim. No summary formatting. Close the `:::` fence when relay completes.

Capture the tool's exit code. The tool emits one drift record per line in the form `<tier>  <category>  <path>` with whitespace-aligned columns and blank lines between tier groups, followed by a final `summary facets=<n> guidance=<n> catala=<n> tests=<n>` line. Exit code is `0` when no drift, `1` on any drift (including degraded-environment `git_unavailable` records), and `2` on environment/usage error.

## Summary

After the relay closes, parse the final `summary` line and the per-tier categories emitted, then emit a `:::important` block with the overall status:

- **All fresh (exit code 0):**
  :::important
  All four tiers fresh for <domain>. No drift detected.
  :::

- **Drift detected (exit code 1):** One line per tier with a non-zero count, naming the dominant category. Example:
  :::important
  Drift detected for <domain>:
    facets (policy_facets): 2 source_edited
    guidance (specs/guidance): 1 guidance_stale
    catala: clean
    tests: 1 tests_manifest_missing
  :::

- **Environment error (exit code 2):** Surface the tool's stderr in an `:::error` fence and stop.

## Next steps

When drift is detected, emit a `:::next_step` block listing the appropriate remediation per affected tier:

- `facets` drift → suggest `/index-inputs <domain>` to refresh `policy_facets/input-index.yaml` and the compressed/computations counterparts.
- `guidance` drift → suggest `/refine-guidance <domain>` (or the specific child skill) to refresh `specs/guidance/` artifacts and rewrite `specs/guidance/.facets-manifest.yaml`.
- `catala` drift → suggest `/extract-ruleset <domain>` (or `/update-ruleset <domain>` for an incremental refresh) to refresh `specs/*.catala_en` and the `consumed_guidance[]` block in `specs/extraction-manifest.yaml`.
- `tests` drift → suggest `/create-tests <domain>` (when baseline tests need regeneration) or `/expand-tests <domain>` (when expanding existing tests). Both refresh `specs/tests/.catala-manifest.yaml`.

When all tiers are fresh, no `:::next_step` block is needed.

## Common Mistakes to Avoid

- Do not modify any tier's manifest from this skill — it is read-only. To refresh a stale manifest, run the appropriate generation skill (see Next steps).
- Do not interpret `tests not_applicable specs/tests/` as drift. It is informational and indicates `specs/tests/` is empty or absent — a normal state for domains that have not yet generated tests.
- Do not silently ignore a `git_unavailable` record. It signals the environment cannot compute SHAs reliably; the freshness verdict is degraded. Surface it in the summary.
