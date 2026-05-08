---
name: create-ruleset-groups
description: Propose and Write Ruleset Groups for a Domain
---

# Propose and Write Ruleset Groups for a Domain

Read the per-file files under `policy_facets/computations/` for phase headings and logical groupings, propose `ruleset_groups`, and write them to `guidance/ruleset-groups.yaml`. A "Ruleset Group" is synonymous with a "ruleset group".

## Input

```
/create-ruleset-groups <domain>
```

Read `../../core/output-fencing.md` now.

## Pre-flight

Run these checks before doing anything else:

1. **Domain argument provided?**
   - NO → List all directories matching `$DOMAINS_DIR/*/` as a numbered menu and prompt:
     :::user_input
     Available domains:
       1. snap
       2. example_domain
     Which domain? Enter a number or domain name:
     :::
     Await the user's response and use it as `<domain>`. Then continue.

2. **Domain folder exists?**
   - NO → Print:
     :::error
     Domain not found: $DOMAINS_DIR/<domain>/
     :::
     Then stop.

3. **`guidance/metadata.yaml` exists?**
   - Check for `$DOMAINS_DIR/<domain>/specs/guidance/metadata.yaml`
   - ABSENT → Print:
     :::error
     guidance/metadata.yaml not found: $DOMAINS_DIR/<domain>/specs/guidance/metadata.yaml
     Run /declare-target-ruleset <domain> first.
     :::
     Then stop.

4. **Per-file computations present?**
   - Check that `$DOMAINS_DIR/<domain>/policy_facets/computations/` exists and contains at least one `*.md.yaml` file (recursive).
   - ABSENT or empty → Print:
     :::error
     Per-file computations not found under: $DOMAINS_DIR/<domain>/policy_facets/computations/
     Run /index-inputs <domain> first.
     :::
     Then stop.

5. **`guidance/skeleton.yaml` exists?**
   - ABSENT → Print:
     :::error
     Skeleton not found: $DOMAINS_DIR/<domain>/specs/guidance/skeleton.yaml
     Run /create-skeleton <domain> first.
     :::
     Then stop.

## Mode Detection

After pre-flight, check whether `$DOMAINS_DIR/<domain>/specs/guidance/ruleset-groups.yaml` exists:

- **Present** → **UPDATE mode**. Display the existing list and prompt:
  :::user_input
  ruleset_groups already defined:
    1. <name> — <description>
    2. <name> — <description>

  [a]ccept / [r]eplace / [m]erge?
  :::
  - `a` → Exit without writing. Suggest next step:
    :::next_step
    Next: Run /create-ruleset-modules <domain> to detect ruleset module candidates.
    :::
  - `r` → Run the full process below (Steps 1–3): re-scan, re-propose, accept/edit, write.
  - `m` → Run the full process below to generate a new proposal list, then merge new + existing (deduplicated by `name`; new descriptions win on conflict). Display the merged list for confirmation using the same format as Step 2, then write on acceptance.

- **Absent** → **CREATE mode**. Run the full process below (Steps 1–3).

---

## Process

### Step 1: Scan for phase signals

Glob every `*.md.yaml` file under `$DOMAINS_DIR/<domain>/policy_facets/computations/` and parse each as a YAML list of section blocks. Do NOT read files under `$DOMAINS_DIR/<domain>/input/` — `policy_facets/computations/` is the sole source of phase signals.

Two signal sources, applied in order:

**(a) Explicit `phase:` values** (preferred when present). For each section block that has a `phase:` field, collect the value. Apply normalization before deduplication:
- Strip a trailing `_test`, `_check`, or `_evaluation` suffix from the snake_case identifier (so `phase: income_test` and `phase: income` collapse to a single canonical group rather than producing two distinct entries that downstream `validate_civil.py` would later reject as mismatched group annotations).
- Compare case-insensitively.

Each distinct (post-normalization) `phase:` value becomes a `ruleset_groups:` entry with `name:` set to the canonical phase value (already snake_case) and `description:` derived deterministically from the identifier — replace underscores with spaces, then title-case (`initial_screening` → "Initial Screening"). Acronym preservation is not attempted (`ebt_eligibility` → "Ebt Eligibility", not "EBT Eligibility"); the analyst hand-edits `description:` after the fact if better wording is wanted.

**(b) Heading-text scan** (fallback for sections without `phase:`). For each section that lacks `phase:`, scan its `heading:` values and the policy's logical groupings:
- Section headings that name a test phase or logical grouping (e.g., "Income Test", "Household Size Verification", "Categorical Eligibility").
- Convert detected headings to `snake_case` names. Examples: "Income Test" → `income_test`, "Household Size Verification" → `household_size_verification`.

**Lenient hybrid coverage:** when some sections in the domain have `phase:` and others don't, both signal sources contribute. Merge the two lists, deduplicated by `name`. **On name collision between a phase-derived candidate and a heading-derived candidate, the phase-derived `description:` wins** — phase is an explicit doc signal and heading-text is a derived guess; the explicit signal should beat the inference.

**If neither signal produces any candidates** (no `phase:` values in any file AND no recognizable phase headings): propose a single catch-all stage derived from `display_name` in `guidance/metadata.yaml` (e.g., if `display_name` is "Determine Eligibility", propose `eligibility`), and note it can be refined later. Never leave `ruleset_groups:` empty.

**Byte/semantic-equivalence guarantee for the no-`phase:` case:** when NO section in any file has a `phase:` value, the heading-text scan path is the only path executed; the produced `ruleset_groups:` list is semantically equivalent to the pre-`phase:` behavior — same group names, same descriptions, same group order. The new (a) branch is a no-op when there are no `phase:` values to consume.

In UPDATE mode with `m` (merge): after generating the new proposal list, combine it with the existing `ruleset_groups:` entries. Deduplicate by `name`. Description-precedence in merge mode is split by signal source so analyst hand-edits stay sticky:

- **Phase-derived candidates** (those whose `name:` came from an explicit `phase:` value in the per-file YAMLs): on `name:` collision with an existing entry, **preserve the existing `description:`**. The deterministic phase-humanization rule (underscores→spaces, title-case) is a creation-time default — not a per-run authority. An analyst who hand-edited `description:` after a prior run keeps that edit through every subsequent re-run; otherwise re-running `[m]erge` would silently revert their wording every time.
- **Heading-derived candidates** (those whose `name:` came from the heading-text scan): on `name:` collision, the existing legacy rule applies — keep the new `description`. (This branch produces AI-generated descriptions whose token sampling can shift between runs; the historical "new wins" rule remains correct here.)

Hold the merged list in memory for Step 2.

---

### Step 2: Display groups

Display the proposed list (or merged list in UPDATE `m` mode) in exactly this format:

:::detail
Proposed ruleset groups
────────────────────────────────────────────────
  1. income_test          — Income eligibility tests
  2. household_test       — Household size and composition tests
  3. categorical_test     — Categorical eligibility checks
:::

Do not prompt for user input. Proceed directly to Step 3.

---

### Step 3: Write `guidance/ruleset-groups.yaml`

Write the confirmed groups to `$DOMAINS_DIR/<domain>/specs/guidance/ruleset-groups.yaml`:

```yaml
ruleset_groups:
  - name: income_test
    description: Income eligibility tests
  - name: household_test
    description: Household size and composition tests
```

Do not write `generated_at`.

Print:
:::important
$DOMAINS_DIR/<domain>/specs/guidance/ruleset-groups.yaml [CREATED]
:::

Then suggest the next step:
:::next_step
Next: Run /create-ruleset-modules <domain> to detect ruleset module candidates.
:::

---

## Output

```
$DOMAINS_DIR/<domain>/specs/guidance/ruleset-groups.yaml    [CREATED]
```

## Common Mistakes to Avoid

- Do not read files under `$DOMAINS_DIR/<domain>/input/` — `policy_facets/computations/` is the sole source of phase signals
- In UPDATE mode "accept", exit without writing — do not overwrite existing `ruleset-groups.yaml` content
- In UPDATE mode "merge", deduplicate by `name` — when the same stage name appears in both existing and new lists, keep the new `description`
- Convert phase headings to `snake_case` — "Income Test" → `income_test`, "Household Size Verification" → `household_size_verification`
- When no phase signals are found (neither explicit `phase:` nor recognizable phase headings), propose a single catch-all stage from `display_name` in `guidance/metadata.yaml` — never leave `ruleset_groups:` empty or omit the key
- **Apply suffix normalization to explicit `phase:` values** — `phase: income_test` and `phase: income` collapse to one canonical group (`income`); skip this step and `validate_civil.py` rejects rules with mismatched group annotations downstream
- **On name collision between phase-derived and heading-derived candidates, the phase-derived `description:` wins** — explicit doc signal beats heading-text inference
- **Do not write `phase:` or modify it** — `phase:` is single-owner; only `/extract-computations` writes the field. This skill reads it
- Do not write `generated_at`
- Note: requiring `ruleset_groups:` before ruleset module detection reverses the monolith's Step 4 → Step 5 order. This is intentional: ruleset modules must stay within a single stage, so groups must be defined first.
- This command has 3 steps — the step checklist rule (>3 steps) does NOT apply; do not show a step checklist
