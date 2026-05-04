# Output Fencing — Authoring Reference

Shared fence protocol for all xlator skills. Skills emit fenced output so a web UI harness can parse and route blocks deterministically without AI or heuristics.

Do not invoke this file directly. It is referenced from `xl-plugin/CLAUDE.md` and may be read on demand by any AI skill.

---

## Syntax

```
:::type
Content goes here.
:::
```

- Opening delimiter: `:::type` on its own line (no leading/trailing spaces)
- Closing delimiter: `:::` on its own line
- Fences must not be nested
- Multiple fence blocks per response are allowed; each is parsed independently
- Unfenced output (including any output from a skill that has not yet been annotated) defaults to `detail`

---

## Fence Types

### `:::important`

**When to use:** Primary result the user needs to act on or read — success confirmations, written file paths, summary verdicts, final outputs.

```
:::important
Created $DOMAINS_DIR/snap/specs/guidance.yaml
:::
```

---

### `:::error`

**When to use:** Pre-flight failures, missing prerequisites, file-not-found messages. Always paired with a stop — do not use for warnings that allow continuation.

```
:::error
Domain not found: $DOMAINS_DIR/unknown/
:::
```

---

### `:::next_step`

**When to use:** Suggested follow-on skills and workflow guidance shown after successful completion.

```
:::next_step
Run /xl:create-skeleton <domain> to extract document signals and build the computation skeleton.
:::
```

---

### `:::detail`

**When to use:** Substantive technical output that is complete and available for on-demand inspection — computation skeletons, YAML blocks, rule tables, coverage maps, and verbatim subprocess relay output.

```
:::detail
module: snap_eligibility
version: "2026Q1"
rules:
  - id: deny_income
    ...
:::
```

**Verbatim-relay:** Wrap the relay block in `:::detail`. Open the fence immediately before beginning relay; close it immediately after relay completes. Each program's relay is a discrete block — do not wrap multiple programs in a single fence.

---

### `:::progress`

**When to use:** Transient in-flight status messages shown while the skill is still running — scanning lines, indexing counts, per-item extraction progress. Also use for step checklists that show checked/unchecked progress state during a multi-step skill.

`:::progress` is for *incomplete, in-flight* output. Once a result is final and complete, use `:::detail` or `:::important` instead.

```
:::progress
Scanning for modules…
:::
```

```
:::progress
[✓] Step 1: Pre-flight checks
[✓] Step 2: Load guidance
[ ] Step 3: Extract components
[ ] Step 4: Validate
:::
```

---

### `:::user_input`

**When to use:** Any prompt that requires a response before the skill can continue — domain-selection menus, mode-choice prompts, confirmation requests, disambiguation questions.

```
:::user_input
Available domains:
  1. snap
  2. example_domain
Which domain? Enter a number or domain name:
:::
```

---

## `progress` vs `detail` — Boundary Definition

| Type | State | Example |
|------|-------|---------|
| `:::progress` | In-flight, transient, not yet complete | "Indexing 4 documents…", per-item scan lines, step checklist mid-run |
| `:::detail` | Complete, substantive, available for inspection | Skeleton YAML, coverage map, rules table, completed relay output |

When in doubt: if the output would look wrong or misleading after the skill finishes, it is `progress`. If the user would want to read it after the skill finishes, it is `detail`.

---

## Authoring Rules for Each Output Category

| Output category | Fence type |
|-----------------|------------|
| Pre-flight failure (missing file, bad arg, unmet prerequisite) | `:::error` |
| In-flight status line, scan progress, step checklist mid-run | `:::progress` |
| Primary result, written confirmation, summary verdict | `:::important` |
| Skeleton, YAML, rule table, coverage map, verbatim relay | `:::detail` |
| Suggested follow-on skills | `:::next_step` |
| Any prompt requiring user response before continuing | `:::user_input` |

---

## Known Edge Case

If verbatim relay output contains a line that is exactly `:::` (e.g., a test runner that emits this string), the harness will treat it as a close delimiter. This is a known limitation; document it in any harness implementation.

---

## Per-Skill Mapping

The mapping of output blocks to fence types for each skill is determined at implementation time by reading the skill's output sections and applying the rules above. Per-skill notes appear in the skill file only when the mapping is non-obvious.
