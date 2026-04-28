# Propose and Write Ruleset Groups for a Domain

Read `input-index.yaml` for phase headings and logical groupings, propose `ruleset_groups`, and write them to `guidance.yaml` after `skeleton:` and before `constraints:`. A "Ruleset Group" is synonymous with a "ruleset group".

## Input

```
/create-ruleset-groups <domain>
```

## Pre-flight

Run these checks before doing anything else:

1. **Domain argument provided?**
   - NO → List all directories matching `$DOMAINS_DIR/*/` as a numbered menu and prompt:
     ```
     Available domains:
       1. snap
       2. example_domain
     Which domain? Enter a number or domain name:
     ```
     Await the user's response and use it as `<domain>`. Then continue.

2. **Domain folder exists?**
   - NO → Print:
     ```
     Domain not found: $DOMAINS_DIR/<domain>/
     ```
     Then stop.

3. **`guidance.yaml` exists?**
   - Check for `$DOMAINS_DIR/<domain>/specs/guidance.yaml`
   - ABSENT → Print:
     ```
     guidance.yaml not found: $DOMAINS_DIR/<domain>/specs/guidance.yaml
     Run /xl:declare-target-ruleset <domain> first.
     ```
     Then stop.

4. **`input-index.yaml` exists?**
   - Check for `$DOMAINS_DIR/<domain>/specs/input-index.yaml`
   - ABSENT → Print:
     ```
     Input index not found: $DOMAINS_DIR/<domain>/specs/input-index.yaml
     Run /xl:index-inputs <domain> first.
     ```
     Then stop.

5. **`skeleton:` key present in `guidance.yaml`?**
   - ABSENT → Print:
     ```
     Skeleton not found in guidance.yaml.
     Run /xl:create-skeleton <domain> first.
     ```
     Then stop.

## Mode Detection

After pre-flight, check whether the `ruleset_groups:` key already exists in `guidance.yaml`:

- **Present** → **UPDATE mode**. Display the existing list and prompt:
  ```
  ruleset_groups already defined:
    1. <name> — <description>
    2. <name> — <description>

  [a]ccept / [r]eplace / [m]erge?  (default: accept)
  ```
  - `a` → Exit without writing. Suggest next step:
    ```
    Next: Run /xl:create-ruleset-modules <domain> to detect ruleset module candidates.
    ```
  - `r` → Run the full process below (Steps 1–3): re-scan, re-propose, accept/edit, write.
  - `m` → Run the full process below to generate a new proposal list, then merge new + existing (deduplicated by `name`; new descriptions win on conflict). Display the merged list for confirmation using the same format as Step 2, then write on acceptance.

- **Absent** → **CREATE mode**. Run the full process below (Steps 1–3).

---

## Process

### Step 1: Scan for phase headings

Read `$DOMAINS_DIR/<domain>/specs/input-index.yaml`. Do NOT read files under `$DOMAINS_DIR/<domain>/input/` — `input-index.yaml` is the sole source of phase heading signals.

Look for:
- Section headings (`heading:` values) that name a test phase or logical grouping (e.g., "Income Test", "Household Size Verification", "Categorical Eligibility")
- Logical groupings of rules or conditions described in the policy

Convert detected headings to `snake_case` names and prepare a proposed list. Examples: "Income Test" → `income_test`, "Household Size Verification" → `household_size_verification`.

**If no phase headings are found:** propose a single catch-all stage derived from `display_name` in `guidance.yaml` (e.g., if `display_name` is "Determine Eligibility", propose `eligibility`), and note it can be refined later. Never leave `ruleset_groups:` empty.

In UPDATE mode with `m` (merge): after generating the new proposal list, combine it with the existing `ruleset_groups:` entries. Deduplicate by `name` — when the same stage name appears in both lists, keep the new `description`. Hold the merged list in memory for Step 2.

---

### Step 2: Display groups

Display the proposed list (or merged list in UPDATE `m` mode) in exactly this format:

```
Proposed ruleset groups
────────────────────────────────────────────────
  1. income_test          — Income eligibility tests
  2. household_test       — Household size and composition tests
  3. categorical_test     — Categorical eligibility checks
```

Do not prompt for user input. Proceed directly to Step 3.

---

### Step 3: Write `ruleset_groups:`

Write the confirmed groups to `$DOMAINS_DIR/<domain>/specs/guidance.yaml`:

- Insert `ruleset_groups:` as a top-level key immediately after `skeleton:` and before `constraints:` — do not append to the end of the file
- Update `generated_at` to today's date

YAML format:
```yaml
ruleset_groups:
  - name: income_test
    description: Income eligibility tests
  - name: household_test
    description: Household size and composition tests
```

Print:
```
$DOMAINS_DIR/<domain>/specs/guidance.yaml [UPDATED]
```

Then suggest the next step:
```
Next: Run /xl:create-ruleset-modules <domain> to detect ruleset module candidates.
```

---

## Output

```
$DOMAINS_DIR/<domain>/specs/guidance.yaml    [UPDATED]
```

## Common Mistakes to Avoid

- Do not read files under `$DOMAINS_DIR/<domain>/input/` — `input-index.yaml` is the sole source of phase heading signals
- `ruleset_groups:` is inserted after `skeleton:` and before `constraints:` in `guidance.yaml`, not at the end of the file
- In UPDATE mode "accept" (or Enter), exit without writing — do not overwrite existing `ruleset_groups:` content
- In UPDATE mode "merge", deduplicate by `name` — when the same stage name appears in both existing and new lists, keep the new `description`
- Convert phase headings to `snake_case` — "Income Test" → `income_test`, "Household Size Verification" → `household_size_verification`
- When no phase headings are found, propose a single catch-all stage from `display_name` — never leave `ruleset_groups:` empty or omit the key
- Note: requiring `ruleset_groups:` before ruleset module detection reverses the monolith's Step 4 → Step 5 order. This is intentional: ruleset modules must stay within a single stage, so groups must be defined first.
- This command has 3 steps — the step checklist rule (>3 steps) does NOT apply; do not show a step checklist
