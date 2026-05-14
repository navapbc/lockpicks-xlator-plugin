---
name: compress-input
description: Caveman-compress one policy doc into policy_facets/compressed/
---

# Caveman-Compress One Policy Doc

Read one policy doc under `<domain>/input/policy_docs/`, copy it to its mirrored destination under `<domain>/policy_facets/compressed/`, and run `/caveman:compress` on the destination so the compressed mirror reflects the source.

This skill is the per-file primitive that mirrors `/extract-computations`'s shape. It is invoked per file by `/index-inputs`'s unified per-file batch worker (alongside `/extract-computations` for the same source file in a single subagent context) and may also be invoked standalone by the analyst against a single source file. The non-AI half (file enumeration, manifest sync, mirror-deletes, plan/finalize handoff for the whole domain) lives in `xlator compress-inputs <domain> --plan` and `--finalize`, called by `/index-inputs`. This skill is the AI half that does the per-file caveman-compression.

## Input

```
/compress-input <path_to_policy_file>
```

`<path_to_policy_file>` must resolve to a path under `<DOMAINS_DIR>/<domain>/input/policy_docs/`. If not, pre-flight emits `:::error` and stops.

Read `../../core/output-fencing.md` now.

Read `../../core/examples/compressed/source.md` and `../../core/examples/compressed/canonical.md` now — both the uncompressed input and the compressed output, so the input→output transformation is visible.

## Pre-flight

Run these checks before doing anything else. Skip pre-flight when invoked from inside `/index-inputs`'s subagent worker — the parent already validated the path.

1. **Argument provided?**
   - NO →
     :::error
     Usage: /compress-input <path_to_policy_file>
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

No md_quality gate here. Compression is not quality-sensitive — `/index-inputs` enforces the quality gate at the orchestrator level by skipping REJECTED files before dispatching workers.

## Process Checklist

This skill has 3 steps:
- [ ] Step 1: Compute destination and copy source
- [ ] Step 2: Invoke `/caveman:compress` on the destination
- [ ] Step 3: Print summary

## Step 1: Compute destination and copy source

Compute the destination path:

```
<DOMAINS_DIR>/<domain>/policy_facets/compressed/<rel>.md
```

where `<rel>.md` is the source path relative to `input/policy_docs/`.

Create intermediate directories under `policy_facets/compressed/` as needed and copy the source to the destination. Overwrite the destination unconditionally — the copy is idempotent. When invoked from `/index-inputs`, the destination already exists (the orchestrator's `xlator compress-inputs --plan` pre-copied it); the overwrite is a no-op in content terms but ensures the standalone-invocation path works without depending on `--plan` having run.

## Step 2: Invoke `/caveman:compress` on the destination

Invoke:

```
/caveman:compress <abs path to dst under policy_facets/compressed/<rel>.md>
```

The caveman skill replaces the destination's contents with a token-reduced (caveman-format) version. It also creates a sibling `<rel>.md.original.md` backup; that backup is swept by `xlator compress-inputs --finalize` (or by `xlator compress-inputs --plan`'s defensive sweep on the next run if the analyst invokes this skill standalone).

If `/caveman:compress` emits `:::error`, propagate the error and stop. Do not retry.

## Step 3: Print summary

:::important
✓ Compressed: policy_facets/compressed/<rel>.md
:::

Do NOT emit `:::next_step` from this skill — it is per-file and is normally invoked from a parent loop or subagent worker. The parent (e.g., `/index-inputs`) emits the workflow's next-step suggestion.

## Common Mistakes to Avoid

- **Don't update the manifest from this skill** — the manifest is the single responsibility of `xlator compress-inputs --finalize`. When invoked standalone (outside `/index-inputs`), the destination file is overwritten with the compressed version but the manifest is not updated; the next `xlator compress-inputs --plan` run (typically via `/index-inputs`) will simply re-classify this file as `to_compress` because it has no manifest entry yet (or its SHA mismatches). This is acceptable best-effort behavior for the standalone path and matches `/extract-computations`'s convention.
- **Don't emit `:::next_step`** — this skill runs inside a parent worker loop and the parent owns the next-step signal.
- **Don't skip the source-to-dst copy** — even when the destination exists from a prior `--plan`'s pre-copy, an idempotent overwrite is required so the standalone-invocation path works without `--plan`.
- **Don't run this skill on a non-`.md` source** — pre-flight refuses; only `.md` is supported in v1.
