---
name: create-demo
description: Generate a Demo App (Catala-Python Backend)
---

# Generate a Demo App (Catala-Python Backend)

Generate a working FastAPI + browser demo for a domain's policy module using the **Catala-Python backend**. Reads the Catala source plus the naming manifest and test manifest to produce four files in `$DOMAINS_DIR/<domain>/output/demo-catala-<module>/`.

## Input

```
/create-demo                        # auto-detect domain/module or prompt if ambiguous
/create-demo <domain>               # use that domain; auto-detect module
/create-demo <domain> <module>      # skip scanning entirely
```

If no args are provided, list all `$DOMAINS_DIR/*/specs/*.catala_en` files and prompt the user to choose.

Read `../../core/output-fencing.md` now.

Read `../../core/catala-authoring-quickref.md` now for Catala syntax conventions (scope structure, fence visibility, money/date literals, enum qualification) — it is the authoritative reference whenever this skill needs to disambiguate Catala identifiers, types, or scope-input semantics.

## Pre-flight

1. **Domain folder exists?** — NO → Print:
   :::error
   Domain '<domain>' not found. Run /new-domain <domain> first.
   :::
   Stop.
2. **Catala source exists?**
   - `$DOMAINS_DIR/<domain>/specs/<module>.catala_en` missing → Print:
     :::error
     No Catala source found. Run /extract-ruleset <domain> first.
     :::
     Stop.
3. **Build-tree Catala source exists?**
   - Check `$DOMAINS_DIR/<domain>/output/<module>.catala_en` — this is a copy of the authored source maintained by the catala-pipeline build step (see plan Key Decisions: `specs/` is authored, `output/` is the build-tree mirror that downstream consumers read).
   - Missing → run the catala-pipeline build step (e.g., `xlator catala-pipeline <domain> <module>`) so the build-tree copy and any depending artifacts are refreshed.
3a. **Python package built?**
   - Run `xlator catala-to-python <domain> <module>` — this handles everything: builds via `clerk build` if needed, moves compiled files into place, and creates `__init__.py`. If it exits non-zero, print the error and stop.
4. **Naming manifest present?**
   - `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml` missing → Print:
     :::error
     No naming-manifest.yaml found. Run /extract-ruleset <domain> first so scope inputs and their types are available.
     :::
     Stop.
5. **Test manifest present?**
   - `$DOMAINS_DIR/<domain>/specs/tests/<module>_tests.yaml` missing → note: proceed with placeholder examples; print warning at the end.

---

## Mode Detection

```bash
ls $DOMAINS_DIR/<domain>/output/demo-catala-<module>/ 2>/dev/null
```

| Result | Mode |
|--------|------|
| Directory absent | **CREATE mode** |
| Directory present | **UPDATE mode** — prompt: `"Demo already exists at $DOMAINS_DIR/<domain>/output/demo-catala-<module>/. Regenerate and overwrite? [y/n]"` — abort on N |

:::user_input
Demo already exists at $DOMAINS_DIR/<domain>/output/demo-catala-<module>/. Regenerate and overwrite? [y/n]
:::

---

## Process — CREATE Mode

### Step 1: Read Inputs

**Scope-input metadata lookup (manifest first, Catala source as fallback).**

Per the project's Key Decisions, the naming manifest is the authority for identifiers AND their types (U7 extends the manifest schema with per-field type metadata). This skill reads scope inputs and their type information from `naming-manifest.yaml` first; it falls back to the Catala source only when a field surfaces in the source that is not yet covered by the manifest.

1. Load `$DOMAINS_DIR/<domain>/specs/naming-manifest.yaml` and apply `SP-LoadNamingManifest` (see `../../core/ruleset-shared.md`). The resulting map `{variable_name → manifest_entry}` covers `inputs.<Entity>.<field>`, `computed.<field>`, and `outputs.<field>`.

   For each manifest entry that participates in the demo's scope inputs, computed breakdown, or decision outputs, read these fields (the exact field names are defined by U7's `naming-manifest.yaml` schema extension; if U7's final naming differs, surface this as a flagged integration touch-up):
   - `type` — Catala primitive type (`money`, `integer`, `decimal`, `bool`, `date`, `<EnumName>`, etc.) — drives the Pydantic field type, the HTML input element, and the Catala-runtime conversion in the API handler
   - `optional` — whether the field has a default (drives `Field(default=...)` vs `Field(..., required)`)
   - `enum_variants` — when `type` is an enumeration, the list of variant names (drives `<select>` `<option>` entries and the `<EnumClass_Code>[value]` lookup)
   - `description` — used for the Pydantic `description=...` string and the `<span class="hint">` after each form input

2. Read the Catala source `$DOMAINS_DIR/<domain>/specs/<module>.catala_en` to confirm the scope name and to discover any scope input that the manifest doesn't cover. Scope inputs in Catala appear as `input <name> content <Type>` lines inside a `declaration scope <ScopeName>:` block (in a `catala-metadata` fenced block — see `../../core/catala-authoring-quickref.md` "Fence visibility"). For any input whose name is not in the manifest map, parse the `data <name> content <Type>` declaration directly from the source as a fallback and treat the result as a synthesized manifest entry for the rest of this skill. Note: do NOT use `clerk list-vars` for identifier extraction — that command returns `clerk.toml` build variables, not Catala source identifiers.

3. Multi-module domains (e.g., `ak_doh`): when the main module uses `> Using <SubModuleName>` directives and references sub-module scopes via `<sub_var> scope <SubModule.SubScopeName>`, the form fields cover the full transitive set of scope inputs across all imported sub-modules. The manifest entries for sub-module inputs are loaded the same way (a single manifest spans the whole domain). For any sub-module input that is not in the manifest, perform the same source-text fallback against the sub-module's `.catala_en` file.

4. Read the Catala source for computed and decision outputs the same way: prefer manifest entries (with `type`, `description`); fall back to scanning the source for `output <name> content <Type>` / `output <name> condition` declarations when an output is not in the manifest.

5. Load `$DOMAINS_DIR/<domain>/specs/tests/<module>_tests.yaml` if present — pick up to 3 test cases with distinct outcomes (prefer one allow case, one deny case, one edge case).

### Step 2: Create Output Directory

```bash
mkdir -p $DOMAINS_DIR/<domain>/output/demo-catala-<module>/static
```

### Step 3: Copy `start.sh` and `requirements.txt`

Copy `start.sh` and `requirements.txt` from `../../core/catala` to `$DOMAINS_DIR/<domain>/output/demo-catala-<module>`.

### Step 4: Write `main.py`

Model on `../../core/demo/demo-catala-eligibility/main.py`.

**How to identify the Catala entry points** — read `demo-catala-<module>/python/<ModuleName>.py` (module filename from `clerk.toml` `modules` field, e.g. `"Earned_income"`; populated by pre-flight step 3a) and find:
- The `*In` class (e.g. `EarnedIncomeDecisionIn`) — input type; look for `class <Name>In:`
- The top-level function (e.g. `earned_income_decision`) — entry point; look for `def <snake_case_name>(<snake_case_name>_in:` at module level
- Any enum classes (e.g. `HouseholdType`, `HouseholdType_Code`) — needed for enum field conversions

**Catala type → Catala-runtime conversion** (applied in the API handler — manifest `type` field drives the choice):

| Manifest `type` | Python value type | Catala-runtime conversion |
|-----------|-------------|------------------|
| `money` | `float` | `money_of_units_int(int(round(value)))` |
| `integer` / `int` | `int` | `integer_of_int(int(value))` |
| `decimal` / `float` | `float` | `decimal_of_string(str(value))` (or the runtime equivalent your generated module imports) |
| `bool` | `bool` | Direct assignment (no conversion) |
| `date` | `str` (`YYYY-MM-DD`) | `date_of_numbers(year, month, day)` |
| `<EnumName>` | `str` | `<EnumClass>(<EnumClass_Code>[value], Unit())` |

```python
# Catala package loaded via PYTHONPATH set by start.sh
# (two entries: demo-catala-<module>/  and  demo-catala-<module>/python/)
# Also requires demo-catala-<module>/python/__init__.py (created by pre-flight step 3a)
from python.<ModuleName> import <InputClass>, <entry_function>
# Import enum classes only if the manifest declares any enum-typed fields:
# from python.<ModuleName> import <EnumClass>, <EnumClass_Code>
from catala_runtime import money_of_units_int, integer_of_int, Unit
```

**`InputFacts` Pydantic model** — one field per scope input (manifest entry, or source-text fallback) — driven by the manifest `type` and `optional` fields:

| Manifest `type` | Python type | Field default |
|-----------|-------------|---------------|
| `integer` / `int` | `int` | `Field(..., ...)` when `optional` is false/absent; `Field(0, ...)` when `optional` is true |
| `money` | `float` | `Field(..., ge=0, ...)` when required; `Field(0.0, ge=0, ...)` when optional |
| `decimal` / `float` | `float` | `Field(..., ...)` when required; `Field(0.0, ...)` when optional |
| `bool` | `bool` | `Field(False, ...)` (always optional in practice) |
| `string` | `str` | `Field(..., ...)` when required; `Field("", ...)` when optional |
| `date` | `str` | `Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", ...)` |
| `<EnumName>` | `str` | `Field(..., ...)` — string value matching one of `enum_variants` |

Use the manifest entry's `description:` as the Pydantic `description=` string. When the field came in via source-text fallback (manifest entry absent), synthesize a short description from the Catala identifier or leave the description empty.

**`ComputedBreakdown` Pydantic model** — check for a `<module>_meta.py` sidecar in `demo-catala-<module>/python/` (generated by the transpiler pipeline). If present, import `SCOPE_METADATA`, `COMPUTED_OUT_FIELDS`, and `SUBSCOPE_FIELDS` from it and use those lists to identify which fields belong in `ComputedBreakdown` (flat computed outputs) and which need a separate steps model (subscope results). If the sidecar is absent, fall back to reading the `*Decision` class attributes directly and excluding known decision fields. For each computed output field emit a `float` (Money via `money_to_float`), `int` (Integer), or `bool` field with `Field(description="[computed_output] ...")`. For subscope output fields define a separate steps model with `Field(description="[subscope_output] ...")`.

Import `money_to_float` alongside the other catala_runtime imports:
```python
from catala_runtime import money_of_units_int, integer_of_int, Unit, money_to_float
```

Populate each field individually from the `*Decision` object attributes — **do NOT use `ComputedBreakdown(**result["computed"])`** (that is the Rego/OPA pattern and does not work for Catala):
```python
breakdown=ComputedBreakdown(
    income_standard=money_to_float(result.income_standard),
    # ... one line per field
)
```

**Response model** — include `breakdown: ComputedBreakdown` and `field_categories` from the sidecar (if present):
```python
class DenialReason(BaseModel):
    code: str
    message: str

class <ResponseModel>(BaseModel):
    eligible: bool
    reasons: list[DenialReason]
    breakdown: ComputedBreakdown
    field_categories: dict[str, str]  # from SCOPE_METADATA — omit if no sidecar
```

Populate `field_categories=SCOPE_METADATA` in the handler return. This makes the API self-describing: consumers can distinguish `decision` / `computed_output` / `subscope_output` fields without reading the Catala source.

**App creation** — no lifespan health check:

```python
app = FastAPI(
    title="Xlator <Domain> <Module> Demo",
    description="Evaluates <module> using Catala-compiled Python rules",
)
```

**API route** — direct Python call. The request payload mirrors the Catala scope-input shape (one field per `input <name> content <Type>` declaration, transitive across imported sub-modules in the multi-module case):

```python
@app.post("/api/<domain>/<module>", response_model=<ResponseModel>)
async def check(facts: InputFacts):
    try:
        inp = <InputClass>(
            # one line per scope input — apply Catala-runtime conversions based on manifest type:
            gross_wages_in=money_of_units_int(int(round(facts.gross_wages))),
            age_in=integer_of_int(int(facts.age)),
            household_type_in=HouseholdType(HouseholdType_Code[facts.household_type], Unit()),
            is_blind_or_disabled_in=facts.is_blind_or_disabled,
            # ... all remaining scope inputs with correct conversions ...
        )
        result = <entry_function>(inp)
        return <ResponseModel>(
            eligible=result.eligible_for_benefits,
            # reasons items are Catala objects with .code (enum) attribute:
            reasons=[DenialReason(code=str(r.code.name), message=str(r.code.name)) for r in result.reasons],
            # NOTE: message == code in v1; raw enum name is acceptable for demo
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

### Step 5: Write `static/index.html`

Read the shared Catala HTML template and substitute all `{{PLACEHOLDER}}` markers with domain-specific content:

```bash
# Read: core/demo/catala.html.template
```

**Placeholder contract** — substitute each marker exactly as described:

| Placeholder | Required? | Content | Notes |
|---|---|---|---|
| `{{PAGE_TITLE}}` | Required | Human-readable module name, e.g. `AK DOH Eligibility` | Plain text; template provides `<title>` and `<h1>` wrappers |
| `{{SUBTITLE_HTML}}` | Required | Inner HTML of subtitle paragraph | May include `<a>` links; template provides `<p class="subtitle">` wrapper |
| `{{EXTRA_CSS}}` | Optional | Domain-specific CSS rules only (no `<style>` tag) | Write empty string if none; template provides `<style id="extra">` wrapper |
| `{{FIELDS_HTML}}` | Required | Complete form markup: `.card` divs, `<form>` tags, fields, submit button | Includes opening and closing `<form>` tags |
| `{{EXAMPLE_BUTTONS_HTML}}` | Required | `<button class="example-btn" onclick="loadExample(...)">` elements | One per test case |
| `{{EXAMPLES_JS}}` | Required | `const EXAMPLES = { … };` statement | Full statement including `const` keyword |
| `{{LOAD_EXAMPLE_BODY_JS}}` | Required | Lines setting field values via `document.getElementById` | Template provides `function loadExample(key) {` wrapper; write body only |
| `{{EXTRA_JS_HELPERS}}` | Optional | Named function declarations (hoisting-safe) | Write empty string if none |
| `{{RENDER_RESULTS_JS}}` | Required | Body of `renderResults` only | Template provides `function renderResults(data, payload) {` wrapper; write body only |
| `{{SUBMIT_PAYLOAD_JS}}` | Required | Key-value pairs for the payload object | Template provides `const payload = { … };`; write only the contents (no braces) |
| `{{API_PATH}}` | Required | Full API endpoint path, e.g. `/api/ak_doh/eligibility` | Used verbatim in `fetch('{{API_PATH}}', …)` |
| `{{FOOTER_HTML}}` | Required | Footer paragraph(s) | May include links; template provides the `<footer>` wrapper |

**`{{EXTRA_CSS}}`** — always include badge variant rules. For two-outcome decisions:
```css
.badge.eligible { background: #e8f5e9; color: #1b5e20; }
.badge.ineligible { background: #ffebee; color: #b71c1c; }
```
For three-outcome decisions (approve / deny / manual):
```css
.badge.approve { background: #e8f5e9; color: #1b5e20; }
.badge.deny { background: #ffebee; color: #b71c1c; }
.badge.manual { background: #fff8e1; color: #e65100; }
```
Add `.cat-tag`, `details.chain-detail`, `.info-note`, `.manual-note`, or `.breakdown-table` rules as needed for this module's breakdown rendering.

**`{{FIELDS_HTML}}`** — complete form structure. One `<div class="field">` per scope input (manifest entry, or source-text fallback):
- `bool` → `<input type="checkbox" id="<name>" name="<name>">` with label
- `integer` / `money` / `decimal` → `<input type="number" id="<name>" name="<name>" min="0" step="1">` (use `step="0.01"` for `money`/`decimal` when finer precision is appropriate)
- `date` → `<input type="date" id="<name>" name="<name>">`
- `string` → `<input type="text" id="<name>" name="<name>">`
- `<EnumName>` → `<select id="<name>" name="<name>">` with one `<option value="<variant>">` per entry in the manifest's `enum_variants` list:
```html
<div class="field">
  <label for="<name>"><Label></label>
  <select id="<name>" name="<name>">
    <option value="<variant1>"><variant1></option>
    <option value="<variant2>"><variant2></option>
  </select>
  <span class="hint"><description></span>
</div>
```
- Group related fields with `<div class="field-group">` (2-column grid)
- Use the manifest entry's `description:` as a `<span class="hint">` after the input (fall back to an empty hint when the field came in via source-text fallback)
- Wrap in `<div class="card"><h2>…</h2><form id="eligibility-form">…<button type="submit" id="submit-btn">Check Eligibility</button></form></div>`

**`{{EXAMPLES_JS}}`** — one entry per selected test case:
```javascript
const EXAMPLES = {
  <key>: { <field_name>: <value>, … },
  …
};
```
If no test manifest: `const EXAMPLES = { example_1: { /* TODO: fill in after running /create-tests <domain> */ } };`

**`{{LOAD_EXAMPLE_BODY_JS}}`** — body only, one line per scope input field:
```javascript
document.getElementById('field_name').value = ex.field_name;   // numbers/enums/strings/dates
document.getElementById('bool_field').checked = ex.bool_field; // booleans
```

**`{{SUBMIT_PAYLOAD_JS}}`** — key-value pairs only (no surrounding braces), one per scope input field. The conversion mirrors the Pydantic field type:
- `bool` → `document.getElementById('<name>').checked`
- `integer` → `parseInt(document.getElementById('<name>').value)`
- `money` / `decimal` → `parseFloat(document.getElementById('<name>').value) || 0`
- `date` / `string` / `<EnumName>` → `document.getElementById('<name>').value`

**`{{RENDER_RESULTS_JS}}`** — function body only (no `function renderResults(...)` line). In scope: `data` (API response), `payload` (submitted values), `fmt(v)` (money formatter), any helpers from `{{EXTRA_JS_HELPERS}}`. Show badge, denial reasons, and a breakdown table reading `data.breakdown`. For chain/steps objects, wrap in `<details class="chain-detail">`. Show breakdown for **all** outcomes — computation runs regardless of verdict. Note: field is `data.breakdown` (not `data.computed` — that is the Rego pattern).

**`{{EXTRA_JS_HELPERS}}`** — named `function` declarations for domain-specific rendering helpers (e.g., `catTag()`, `renderChainTable()`). Write empty string if none.

**Verification**: After writing, confirm no literal `{{...}}` strings remain in the output file.

### Step 6: Print Summary

:::important
Demo created at $DOMAINS_DIR/<domain>/output/demo-catala-<module>/
  requirements.txt
  start.sh
  main.py
  static/index.html
:::

If no test manifest was found, print:
:::important
⚠  No test manifest found — EXAMPLES in index.html contain TODO placeholders.
   Run /create-tests <domain> <module> for realistic example scenarios.
:::

:::next_step
Next steps:
  1. Install deps:   uv pip install -r $DOMAINS_DIR/<domain>/output/demo-catala-<module>/requirements.txt
  2. Run the demo:   xlator catala-demo <domain> <module>
  3. Open browser:   http://localhost:8000/static/index.html
  4. API docs:       http://localhost:8000/docs
:::

---

## Process — UPDATE Mode

After confirming overwrite, execute CREATE mode in full. Overwrite all 4 files.

---

## Scope Input → Generated Artifact Summary

| Scope-input source (manifest entry, or source-text fallback) | Catala-Python demo artifact |
|-------------|---------------------|
| Input with primitive `type` (`integer` / `money` / `decimal` / `bool` / `string` / `date`) | `InputFacts` Pydantic fields; `<input>` elements; `payload` fields in submit handler |
| Input with enum-typed `type` and `enum_variants` list | `str` Pydantic field; `<select>` element; `<EnumClass>(<EnumClass_Code>[v], Unit())` conversion |
| `computed` manifest entries / Catala `internal` or `output` value-typed declarations | `ComputedBreakdown` Pydantic fields (Money→`money_to_float`, Integer→`int`, bool→`bool`); rendered as `.breakdown-table` in UI |
| `outputs` manifest entries / Catala `output … condition` and decision-enum declarations | Response model fields; badge + denial list in `renderResults()` |
| Domain name + module name | FastAPI route, app title, page title |
| Test cases (up to 3) | `EXAMPLES` dict + button labels in `index.html` |

---

## Common Mistakes to Avoid

- **Do NOT hardcode domain or module names** — derive all names from `<domain>` and `<module>` args
- **Do NOT copy field names from another domain** — read the manifest (and the Catala source when needed) to derive field names for this module
- **Use manifest variable-name keys verbatim** as Python attribute names and HTML `id`/`name` values — they are already snake_case (manifest is the canonical-name authority per `SP-LoadNamingManifest`)
- **Do not re-derive identifiers from policy text** — the manifest is canonical (see `../../core/ruleset-shared.md` `SP-LoadNamingManifest`)
- **Multi-module domains:** the form covers the transitive set of scope inputs across all `> Using <SubModuleName>` imports — do not omit sub-module inputs even when the top-level scope appears to take fewer fields
- **The Catala source may declare `output … condition` outputs** (boolean) and `output … content list of <Reason>` outputs (denial-reasons accumulation idiom) — map to `bool` and `list[DenialReason]` in the response model respectively (see `../../core/catala-authoring-quickref.md` "denial-reasons accumulation")

**Catala-Python mode — additional mistakes to avoid:**
- **Do NOT use `money_of_cents_int`** — it does not exist. Use `money_of_units_int(int(round(value)))` for dollar amounts.
- **Do NOT use `ComputedBreakdown(**result["computed"])`** — that is the Rego/OPA pattern. For Catala, populate each `ComputedBreakdown` field individually from the `*Decision` object attributes using `money_to_float()` for Money fields.
- **Do NOT forget `money_to_float()` for every `Money`-typed field** in `ComputedBreakdown` — omitting it leaves a raw `Money` object in the JSON response, which Pydantic cannot serialize.
- **Do NOT set PYTHONPATH to only `demo-catala-<module>/python/`** — Catala-generated files use relative imports (`from . import Stdlib_en`) which require a package context. Set PYTHONPATH to BOTH the demo folder itself `demo-catala-<module>/` (enables `from python.<ModuleName> import ...`) AND `demo-catala-<module>/python/` (enables `from catala_runtime import *` inside generated files). Also ensure `demo-catala-<module>/python/__init__.py` exists (pre-flight step 3a creates it).
- **Read the `.py` file to find the `*In` class and entry function** — do not hardcode class/function names; they vary per module. Look for `class <Name>In:` and `def <name>(<name>_in:` at module level. Use `clerk.toml` `modules` field for the capitalized filename (e.g., `"Earned_income"`). The file lives at `$DOMAINS_DIR/<domain>/output/demo-catala-<module>/python/<ModuleName>.py` after pre-flight step 3a populates it.
- **Do NOT call `clerk list-vars` to enumerate scope inputs** — that command returns `clerk.toml` build variables, not Catala source identifiers. Read the manifest first; fall back to parsing `data <name> content <Type>` declarations from the `.catala_en` source when the manifest does not cover a field.
- **`DenialReason.message` equals `code` in v1** — `str(r.code.name)` produces the raw enum name (e.g., `"EarnedIncomeExceedsLimit"`); acceptable for demo.

**Reference files (read these before generating):**
- `../../core/catala-authoring-quickref.md` — AI-targeted Catala reference (scope structure, fence visibility, money/date literals, enum qualification, denial-reasons accumulation)
- `../../core/ruleset-shared.md` — `SP-LoadNamingManifest` (canonical-name authority chain and manifest field schema)
- `../../core/demo/catala.html.template` — shared HTML template; read this and substitute `{{PLACEHOLDER}}` markers for `static/index.html`
- `../../core/demo/demo-catala-eligibility/main.py` — canonical FastAPI pattern (includes ComputedBreakdown + ExclusionChainSteps)
- `../../core/demo/demo-catala-eligibility/python/Eligibility.py` — canonical Catala Python module structure
