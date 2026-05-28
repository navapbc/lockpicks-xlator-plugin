---
name: convert-doc
description: Convert a .docx or .pdf policy document to clean markdown
---

# Convert a Policy Document to Markdown

Take a `.docx` or `.pdf` source file, parse it to markdown, optionally clean it up with Claude, and place the result under the target domain's `input/policy_docs/` while archiving the original under `input/_originals/`. A diagnostics JSON sits next to the original so the UI can surface parse warnings.

## Input

```
/convert-doc <domain> <source-file> [--force-cleanup] [--no-cleanup]
```

If `<domain>` is not provided, list all `$DOMAINS_DIR/*/input/policy_docs/` directories as a numbered menu, prompt the user to choose, await their response, and use it as `<domain>` before continuing. If `<source-file>` is not provided, prompt: "Path to the .docx or .pdf source file?"

Read `../../core/output-fencing.md` now.

## Pre-flight

Run these checks before doing anything else:

1. **Domain argument provided?**
   - NO → List all `$DOMAINS_DIR/*/input/policy_docs/` directories as a numbered menu and prompt:
     :::user_input
     Available domains:
       1. snap
       2. example_domain
     Which domain? Enter a number or domain name:
     :::
     Await the user's response. Then continue.

2. **Source file provided and readable?**
   - Missing → prompt for the path.
   - Not a file → :::error
     Source file not found: <source-file>
     :::
     Then stop.

3. **Extension supported?**
   - Anything other than `.docx` or `.pdf` →
     :::error
     Unsupported extension. Only .docx and .pdf are accepted by this command.
     :::
     Then stop.

4. **Domain folder exists?**
   - NO →
     :::error
     Domain not found: $DOMAINS_DIR/<domain>/. Run /new-domain <domain> first.
     :::
     Then stop.

## Process

### Step 1: Run the conversion

```bash
xlator convert-doc <domain> <source-file>
```

Add `--force-cleanup` if the user has confirmed a large doc, or `--no-cleanup` for raw-only output. The command streams `:::progress` and `:::diagnostic` lines on stdout and exits non-zero when cleanup confirmation is required (exit code 4).

### Step 2: Interpret the result

| Exit | Meaning | Next |
|------|---------|------|
| 0 | Converted (or cache hit) | Step 3 |
| 2 | Source file not found | Stop — surface the path that was missing |
| 3 | Scanned PDF but no API key | Stop — instruct the user to set `ANTHROPIC_API_KEY` |
| 4 | `confirm_cleanup_required` | Step 4 |
| other | Unexpected failure | Stop — surface stderr |

### Step 3: Success

:::important
✓ Converted <source-file> → input/policy_docs/<basename>.md
  Original archived at input/_originals/<basename>.<ext>
  Diagnostics at input/_originals/<basename>.diagnostics.json
:::

:::next_step
Next step: `/index-inputs <domain>` to incorporate the new .md.
:::

### Step 4: Cleanup confirmation required

The CLI emits a `:::diagnostic {"code": "confirm_cleanup_required", ...}` line carrying `page_count` and `estimated_input_tokens`. Surface those numbers and prompt:

:::user_input
This document is large (<page_count> pages, ~<estimated_input_tokens> tokens). Cleanup will call Claude on the full body — proceed? [y/n]
:::

- **y** → re-run `xlator convert-doc <domain> <source-file> --force-cleanup`, then jump to Step 3.
- **n** → re-run with `--no-cleanup` to keep the raw parse, then jump to Step 3 and note the cleanup was skipped.

## Common Mistakes to Avoid

- Don't shell out to `mammoth` / `pymupdf` directly — always go through `xlator convert-doc` so the diagnostics JSON and `_originals/` archive stay in sync with the .md.
- Don't rerun the command after a successful exit unless the source has changed — it short-circuits on a SHA-256 match against the cached original anyway, but doing so floods the progress channel for no reason.
- Don't strip the `:::progress` / `:::diagnostic` fences from your stdout — the UI subscribes to those.
- Don't write the output `.md` into `input/_originals/` — that directory is for the source files only; `input/policy_docs/` is the indexed surface.
