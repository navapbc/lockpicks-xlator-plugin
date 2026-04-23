# Tag Variables to Include with Output

Auto-detect which intermediate variables should be exposed in the API's `ComputedBreakdown` response and merge them into `guidance.yaml` under `intermediate_variables.include_with_output`. Runs non-interactively — no mid-run prompting.

## Input

```
/tag-vars-to-include-with-output [<domain>]
```

If `<domain>` is not provided, list all `$DOMAINS_DIR/*/specs/guidance.yaml` files as a numbered menu and prompt:

```
Available domains:
  1. snap
  2. ak_doh
Which domain? Enter a number or domain name:
```

Await the user's response and use it as `<domain>`. Then continue.

## Pre-flight

1. **Domain argument provided?** — If not, show domain menu (above). Await response.

2. **Domain folder exists?**
   - NO → Print: `Domain not found: $DOMAINS_DIR/<domain>/` Then stop.

3. **`guidance.yaml` exists?**
   - NO → Print:
     ```
     guidance.yaml not found: $DOMAINS_DIR/<domain>/specs/guidance.yaml
     Run /xl:suggest-ruleset-io <domain> first.
     ```
     Stop.

---

## Process

### Detection pass 1 — invoke-derived variables

Scan all `computations:` entries across every `intermediate_variables.categories[]` entry in `guidance.yaml`. For each entry whose `expr:` value contains dot-notation (`<identifier>.<identifier>`), collect the **base name** — the portion before the first dot — as an auto-tagged variable.

Example: `expr: "client_result.adjusted_earned_income"` → base name is `client_result`.

Collect all such base names as `auto_tagged`.

### Detection pass 2 — declared output variables

Collect `output_variables.primary.name` and all `output_variables.secondary_decisions[].name` values from `guidance.yaml`. These are primary decision outputs already declared as the module's outputs.

Collect all such names as `output_declared`.

### Merge and write

Compute: `new_include_with_output` = `auto_tagged` ∪ `output_declared` ∪ existing `intermediate_variables.include_with_output` values (if any).

Deduplicate. Preserve all existing names — never remove entries.

Write the merged list to `guidance.yaml` under `intermediate_variables.include_with_output`. Do not modify any other section of `guidance.yaml`.

### Print result

Print one line per name in the final `include_with_output` list, labeled with its detection reason:

```
include_with_output written to guidance.yaml:
  client_result   (invoke-derived: client_result.adjusted_earned_income)
  dol_result      (invoke-derived: dol_result.adjusted_earned_income)
  eligible        (output variable: output_variables.primary)
  denial_reason   (output variable: output_variables.secondary_decisions)
  income_limit    (existing)
```

If no variables were detected and no existing values were present, print:

```
No variables auto-detected. include_with_output: [] written to guidance.yaml.
```

---

## Output

| File | Action |
|------|--------|
| `$DOMAINS_DIR/<domain>/specs/guidance.yaml` | Updated — `intermediate_variables.include_with_output` merged |

---

## Common Mistakes to Avoid

- **Do not remove existing entries** from `include_with_output` — this command only adds; removal is a manual edit
- **Do not modify any section other than `intermediate_variables.include_with_output`** — preserve all other guidance.yaml content verbatim
- **Base name only for dot-notation** — collect the identifier before the first dot (e.g., `client_result` from `client_result.adjusted_earned_income`), not the full expression
- **Idempotent** — running this command twice must produce no duplicates and no changes on the second run
