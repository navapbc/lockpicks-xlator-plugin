---
name: declare-target-ruleset
description: Declare Ruleset Input-Output from a Suggestion File
---

# Declare Ruleset Input-Output from a Suggestion File

Bootstrap the `guidance/` folder for a domain from a ruleset file produced by `/suggest-target-ruleset`. The deterministic write logic lives in `xlator declare-target-ruleset`; this skill owns the menu prompts and overwrite confirmation.

## Input

```
/declare-target-ruleset <domain> [<ruleset_name>]
```

Read `../../core/output-fencing.md` now.

Read `../../core/examples/naming-manifest/canonical.yaml`, `../../core/examples/metadata/canonical.yaml`, and `../../core/examples/prompt-context/canonical.yaml` now.

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

2. **Domain folder exists?**
   - NO →
     :::error
     Domain not found: $DOMAINS_DIR/<domain>/
     :::
     Then stop.

3. **`suggested_targets/` directory exists and has at least one `.yaml` file?**
   - Directory absent or empty →
     :::error
     No ruleset files found. Run /suggest-target-ruleset <domain> first.
     :::
     Then stop.

4. **Ruleset file resolved:**
   - If `<ruleset_name>` was provided: check `$DOMAINS_DIR/<domain>/specs/suggested_targets/<ruleset_name>.yaml` exists.
     - NOT FOUND →
       :::error
       Ruleset file not found: $DOMAINS_DIR/<domain>/specs/suggested_targets/<ruleset_name>.yaml
       Available ruleset files:
         - <file1>.yaml
         - <file2>.yaml
       :::
       Then stop.
   - If `<ruleset_name>` was NOT provided: list `.yaml` files in `$DOMAINS_DIR/<domain>/specs/suggested_targets/` as a numbered menu and prompt:
     :::user_input
     Available ruleset files:
       1. <file1>
       2. <file2>
     Which ruleset file? Enter a number or file name:
     :::
     Await the user's response and use the resolved stem as `<ruleset_name>`. Then continue.

5. **`guidance/metadata.yaml` already exists?**
   - `$DOMAINS_DIR/<domain>/specs/guidance/metadata.yaml` present → Prompt:
     :::user_input
     guidance/metadata.yaml already exists at $DOMAINS_DIR/<domain>/specs/guidance/metadata.yaml. Overwrite? [y/n]
     :::
     - `n` → Stop without writing.
     - `y` → continue.

## Process

1. Ensure the `guidance/` folder exists and seed `CLAUDE.md`:

   ```bash
   xlator ensure-guidance <domain>
   ```

   This is idempotent — safe to run when the folder already exists.

2. Run the deterministic bootstrap tool:

   ```bash
   xlator declare-target-ruleset <domain> <ruleset_name>
   ```

   The tool reads `specs/suggested_targets/<ruleset_name>.yaml` and writes the three bootstrap files atomically.

   Open a `:::important` fence. Relay the tool's stdout verbatim. Close the fence.

   If the tool exits non-zero, emit `:::error` with the captured stderr and stop.

3. Suggest the next step:

   :::next_step
   Next: Run `/refine-guidance <domain>` to populate descriptive guidance files.
   :::

## Output

| File | Action |
|------|--------|
| `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml`         | Created (or overwritten) |
| `$DOMAINS_DIR/<domain>/specs/guidance/metadata.yaml`       | Created (or overwritten) |
| `$DOMAINS_DIR/<domain>/specs/guidance/prompt-context.yaml` | Created (or overwritten) |

The `metadata.yaml` shape is small and structurally fixed — `display_name` plus `description`. For convenience, the canonical content is shown inline here verbatim (kept byte-identical to `../../core/examples/metadata/canonical.yaml`):

```yaml
display_name: "Determine Medicaid Income Eligibility"
description: "Decide whether to authorize, deny, or request further verification for an Alaska Medicaid case by comparing the applicant's countable monthly income against the household-size-adjusted income standard."
```

## Common Mistakes to Avoid

- **Do not write `generated_at`** — git tracks version history; the tool drops this field.
- **Do not propagate the `primary: true|false` flag** from `suggested_targets/*.yaml` into `naming-manifest.yaml` — primary distinction lives in `guidance/output-variables.yaml` (written by `/create-skeleton`). The tool enforces this.
- **Do not write `policy_phrase:`, `source_doc:`, or `section:` on seeded `naming-manifest.yaml` entries** — these provenance fields are nullable at seed time. The tool omits them; `/extract-ruleset` Step 7 fills them in once the analyst confirms a seeded name against an observed phrase.
