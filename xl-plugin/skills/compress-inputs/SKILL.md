---
name: compress-inputs
description: Compress Policy Documents Into policy_facets/compressed/
---

# Compress Policy Documents Into `policy_facets/compressed/`

Produce and incrementally sync a caveman-compressed mirror of `<domain>/input/policy_docs/` at `<domain>/policy_facets/compressed/`. Downstream skills (`/index-inputs`, `/refine-guidance`, `/extract-ruleset`) consume the compressed copies as canonical input, cutting input-token cost on every AI step that reads policy docs.

This skill orchestrates the AI half of the workflow (per-file caveman compression). The non-AI half (file enumeration, copy, manifest sync, mirror-deletes, backup cleanup) runs as `xlator compress-inputs <domain> --plan` and `xlator compress-inputs <domain> --finalize`.

## Input

```
/compress-inputs <domain>
```

If `<domain>` is not provided, list candidate domains and prompt.

Read `../../core/output-fencing.md` now.

## Pre-flight

Run these checks before doing anything else:

1. **Domain argument provided?**
   - NO → List all directories matching `$DOMAINS_DIR/*/input/policy_docs/` as a numbered menu and prompt:
     :::user_input
     Available domains:
       [a] snap
       [b] ak_doh
     Which domain? (or type in different response)
     :::
     Await the user's response and use it as `<domain>`. Then continue.

2. **Domain folder exists?**
   - NO →
     :::error
     Domain not found: $DOMAINS_DIR/<domain>/
     :::
     Then stop.

3. **Input docs present?**
   - `$DOMAINS_DIR/<domain>/input/policy_docs/` missing or contains no `.md` files →
     :::error
     No input documents found. Add .md files to $DOMAINS_DIR/<domain>/input/policy_docs/ and re-run.
     :::
     Then stop.

4. **Caveman `/compress` skill installed?**
   - Check whether any directory matching `~/.claude/plugins/cache/caveman/caveman/*/skills/compress/SKILL.md` exists.
   - If absent →
     :::error
     The caveman /compress skill is required but not installed.
     Install the 'caveman' Claude Code plugin: https://github.com/JuliusBrussee/caveman
     :::
     Then stop.

## Process Checklist

This skill has 5 steps:
- [ ] Step 1: Run `xlator compress-inputs <domain> --plan`
- [ ] Step 2: For each `to_compress` entry, invoke `/caveman:compress`
- [ ] Step 3: Run `xlator compress-inputs <domain> --finalize`
- [ ] Step 4: Surface failures (if any)
- [ ] Step 5: Print summary and next-step

## Step 1: Plan

Run:

```bash
xlator compress-inputs <domain> --plan
```

The tool bootstraps `policy_facets/` (creates the folder, moves any legacy `specs/input-{index,sections}.yaml` into `policy_facets/`), sweeps stale `*.original.md` files, computes the work plan, copies each `to_compress` source file into its destination under `policy_facets/compressed/`, and writes the work plan to `policy_facets/.compress-plan.tmp`.

The tool emits the work plan as JSON on stdout. Parse it. The shape is:

```json
{
  "to_compress":     [ {"src": "...", "dst": "...", "source_sha": "..."}, ... ],
  "to_delete":       [ "policy_facets/compressed/<rel>.md", ... ],
  "noop":            [ {"src": "...", "reason": "unchanged"}, ... ],
  "skipped":         [ {"src": "...", "reason": "sensitive_path"|"not_eligible"}, ... ],
  "bootstrap_moved": [ "specs/input-index.yaml -> policy_facets/...", ... ],
  "succeeded":       [],
  "failed":          []
}
```

If `bootstrap_moved` is non-empty, emit:

:::important
Migrated index files into policy_facets/:
  <list of moved files>
:::

If `to_compress` is empty AND `to_delete` is empty, emit:

:::important
Nothing to compress. <noop count> unchanged, <skipped count> skipped.
:::

Then skip directly to Step 3 (finalize) so any pending mirror-deletes still apply.

If `to_compress` has entries, continue to Step 2.

## Step 2: Compress each file via caveman `/compress`

Read `policy_facets/.compress-plan.tmp` so you can mutate the `succeeded:` and `failed:` lists as you go.

For each entry in `to_compress`:

1. Compute the absolute path of `dst` (it has already been copied into place by Step 1).
2. Emit a progress line:
   :::progress
   Compressing <src>...
   :::
3. Run the caveman skill:

   ```
   /caveman:compress <absolute_path_of_dst>
   ```

4. On success: append the entry's `src` value to the `succeeded:` list inside `.compress-plan.tmp`. Save the file after each update so a crashed run can be recovered by `--finalize`.
5. On failure: append `{"src": "<src>", "error": "<short message>"}` to the `failed:` list and continue with the next file. Do NOT abort the whole run on one bad file.

## Step 3: Finalize

Run:

```bash
xlator compress-inputs <domain> --finalize
```

The tool:
- removes every `*.original.md` backup left by caveman under `policy_facets/compressed/`,
- deletes destination copies for any `to_compress` entry that was not in `succeeded:` (so the next run reattempts them),
- applies mirror-deletes from `to_delete` and prunes their manifest entries,
- writes the manifest atomically (`tmp + os.replace`),
- removes `.compress-plan.tmp`.

Relay its summary verbatim inside `:::detail`:

:::detail
<verbatim output of the finalize command>
:::

## Step 4: Surface failures (if any)

If `failed:` is non-empty:

:::important
⚠ <N> file(s) failed to compress:
  - <src1>: <error1>
  - <src2>: <error2>
The corresponding compressed copies were removed; re-running /compress-inputs will retry.
:::

Exit non-zero in this case so a parent skill (e.g., `/index-inputs`) can decide whether to proceed.

## Step 5: Print summary and next step

:::important
✓ <succeeded count> compressed, <deleted count> deleted, <noop count> unchanged, <skipped count> skipped.
:::

## Common Mistakes to Avoid

- Do NOT manually edit files under `policy_facets/compressed/` — they are derived. The user should edit the source under `input/policy_docs/` instead and re-run this skill.
- Do NOT delete `policy_facets/.compress-manifest.yaml` between runs — it tracks which source files are already compressed. If you need a full rebuild, also clear `policy_facets/compressed/`.
- Do NOT batch-update the `succeeded:` list at the end of Step 2 — update it after each file so a crashed run can be cleanly resumed.
