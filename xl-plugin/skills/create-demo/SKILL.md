---
name: create-demo
description: Generate a Demo App (Catala-Python Backend)
---

# Generate a Demo App (Catala-Python Backend)

Generate a working FastAPI + browser demo for a domain's policy module using the **Catala-Python backend**. Reads the CIVIL spec and test manifest to produce four files in `$DOMAINS_DIR/<domain>/output/demo-catala-<module>/`.

## Input

```
/create-demo                        # auto-detect domain/module or prompt if ambiguous
/create-demo <domain>               # use that domain; auto-detect module
/create-demo <domain> <module>      # skip scanning entirely
```

If no args are provided, list all `$DOMAINS_DIR/*/specs/*.civil.yaml` files and prompt the user to choose.

Read `../../core/output-fencing.md` now.

## Pre-flight

1. **Domain folder exists?** — NO → Print:
   :::error
   Domain '<domain>' not found. Run /new-domain <domain> first.
   :::
   Stop.
2. **CIVIL file exists?**
   - `$DOMAINS_DIR/<domain>/specs/<module>.civil.yaml` missing → Print:
     :::error
     No CIVIL file found. Run /extract-ruleset <domain> first.
     :::
     Stop.
3. **Transpiled output exists?**
   - Check `$DOMAINS_DIR/<domain>/output/<module>.catala_en`
   - Not found → Run `xlator catala-transpile <domain> <module>`.
3a. **Python package built?**
   - Run `xlator catala-to-python <domain> <module>` — this handles everything: builds via `clerk build` if needed, moves compiled files into place, and creates `__init__.py`. If it exits non-zero, print the error and stop.
4. **Test manifest present?**
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

- Load `$DOMAINS_DIR/<domain>/specs/<module>.civil.yaml` — extract:
  - `inputs.<Entity>.fields` — input field names, types, optionality, descriptions
  - `computed:` — output computed field names (keys only; ignore `expr:`/`conditional:` values)
  - `outputs:` — output decision field names and types
  - `metadata` — domain name, description, any policy citation
- Load `$DOMAINS_DIR/<domain>/specs/tests/<module>_tests.yaml` if present — pick up to 3 test cases with distinct outcomes (prefer one `allow_*`, one `deny_*`, one edge case).

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

**CIVIL type → Catala conversion** (applied in the API handler):

| CIVIL type | Python type | Catala conversion |
|-----------|-------------|------------------|
| `money` | `float` | `money_of_units_int(int(round(value)))` |
| `int` | `int` | `integer_of_int(int(value))` |
| `bool` | `bool` | Direct assignment (no conversion) |
| `enum` | `str` | `<EnumClass>(<EnumClass_Code>[value], Unit())` |

```python
# Catala package loaded via PYTHONPATH set by start.sh
# (two entries: demo-catala-<module>/  and  demo-catala-<module>/python/)
# Also requires demo-catala-<module>/python/__init__.py (created by pre-flight step 3a)
from python.<ModuleName> import <InputClass>, <entry_function>
# Import enum classes only if the CIVIL spec has enum-type fields:
# from python.<ModuleName> import <EnumClass>, <EnumClass_Code>
from catala_runtime import money_of_units_int, integer_of_int, Unit
```

**`InputFacts` Pydantic model** — one field per `inputs.<Entity>.fields` entry:

| CIVIL type | Python type | Field default |
|-----------|-------------|---------------|
| `int` | `int` | `Field(..., ...)` if required; `Field(0, ...)` if optional |
| `money` | `float` | `Field(..., ge=0, ...)` if required; `Field(0.0, ge=0, ...)` if optional |
| `bool` | `bool` | `Field(False, ...)` (always optional) |
| `string` | `str` | `Field(..., ...)` if required; `Field("", ...)` if optional |
| `enum` | `str` | `Field(..., ...)` — string value matching an enum variant name |

Use the CIVIL `description:` as the Pydantic `description=` string.

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

Populate `field_categories=SCOPE_METADATA` in the handler return. This makes the API self-describing: consumers can distinguish `decision` / `computed_output` / `subscope_output` fields without reading the CIVIL source.

**App creation** — no lifespan health check:

```python
app = FastAPI(
    title="Xlator <Domain> <Module> Demo",
    description="Evaluates <module> using Catala-compiled Python rules",
)
```

**API route** — direct Python call:

```python
@app.post("/api/<domain>/<module>", response_model=<ResponseModel>)
async def check(facts: InputFacts):
    try:
        inp = <InputClass>(
            # one line per field — apply Catala type conversions:
            gross_wages_in=money_of_units_int(int(round(facts.gross_wages))),
            age_in=integer_of_int(int(facts.age)),
            household_type_in=HouseholdType(HouseholdType_Code[facts.household_type], Unit()),
            is_blind_or_disabled_in=facts.is_blind_or_disabled,
            # ... all remaining fields with correct conversions ...
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

**`{{FIELDS_HTML}}`** — complete form structure. One `<div class="field">` per `inputs.<Entity>.fields` entry:
- `bool` → `<input type="checkbox" id="<name>" name="<name>">` with label
- `int` / `money` → `<input type="number" id="<name>" name="<name>" min="0" step="1">`
- `enum` → `<select id="<name>" name="<name>">` with one `<option value="<variant>">` per `enum_values` entry:
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
- Use the CIVIL `description:` as a `<span class="hint">` after the input
- Wrap in `<div class="card"><h2>…</h2><form id="eligibility-form">…<button type="submit" id="submit-btn">Check Eligibility</button></form></div>`

**`{{EXAMPLES_JS}}`** — one entry per selected test case:
```javascript
const EXAMPLES = {
  <key>: { <field_name>: <value>, … },
  …
};
```
If no test manifest: `const EXAMPLES = { example_1: { /* TODO: fill in after running /create-tests <domain> */ } };`

**`{{LOAD_EXAMPLE_BODY_JS}}`** — body only, one line per input fact field:
```javascript
document.getElementById('field_name').value = ex.field_name;   // numbers/enums
document.getElementById('bool_field').checked = ex.bool_field; // booleans
```

**`{{SUBMIT_PAYLOAD_JS}}`** — key-value pairs only (no surrounding braces), one per input fact field:
- `bool` → `document.getElementById('<name>').checked`
- `int` → `parseInt(document.getElementById('<name>').value)`
- `money` → `parseFloat(document.getElementById('<name>').value) || 0`
- `enum` → `document.getElementById('<name>').value`

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
  1. Install deps:   pip install -r $DOMAINS_DIR/<domain>/output/demo-catala-<module>/requirements.txt
  2. Run the demo:   xlator catala-demo <domain> <module>
  3. Open browser:   http://localhost:8000/static/index.html
  4. API docs:       http://localhost:8000/docs
:::

---

## Process — UPDATE Mode

After confirming overwrite, execute CREATE mode in full. Overwrite all 4 files.

---

## CIVIL Type → Generated Artifact Summary

| CIVIL field | Catala-Python mode artifact |
|-------------|---------------------|
| `inputs.<Entity>.fields[type=int/money/bool/string]` | `InputFacts` Pydantic fields; `<input>` elements; `payload` fields in submit handler |
| `inputs.<Entity>.fields[type=enum]` | `str` Pydantic field; `<select>` element; `<EnumClass>(<EnumClass_Code>[v], Unit())` conversion |
| `computed:` keys | `ComputedBreakdown` Pydantic fields (Money→`money_to_float`, Integer→`int`, bool→`bool`); rendered as `.breakdown-table` in UI |
| `outputs:` keys | Response model fields; badge + denial list in `renderResults()` |
| `metadata.domain` + module name | FastAPI route, app title, page title |
| Test cases (up to 3) | `EXAMPLES` dict + button labels in `index.html` |

---

## Common Mistakes to Avoid

- **Do NOT hardcode domain or module names** — derive all names from `<domain>` and `<module>` args
- **Do NOT copy field names from another domain** — read the CIVIL spec and derive field names from it
- **Use `inputs.<Entity>.fields` keys verbatim** as Python attribute names and HTML `id`/`name` values — they are already snake_case
- **The `computed:` block may have `conditional:` entries** — extract the key name only; ignore the expression
- **The `outputs:` block may have a `list` type field** (e.g., `reasons`) — map to `list[DenialReason]` in Python

**Catala-Python mode — additional mistakes to avoid:**
- **Do NOT use `money_of_cents_int`** — it does not exist. Use `money_of_units_int(int(round(value)))` for dollar amounts.
- **Do NOT use `ComputedBreakdown(**result["computed"])`** — that is the Rego/OPA pattern. For Catala, populate each `ComputedBreakdown` field individually from the `*Decision` object attributes using `money_to_float()` for Money fields.
- **Do NOT forget `money_to_float()` for every `Money`-typed field** in `ComputedBreakdown` — omitting it leaves a raw `Money` object in the JSON response, which Pydantic cannot serialize.
- **Do NOT set PYTHONPATH to only `demo-catala-<module>/python/`** — Catala-generated files use relative imports (`from . import Stdlib_en`) which require a package context. Set PYTHONPATH to BOTH the demo folder itself `demo-catala-<module>/` (enables `from python.<ModuleName> import ...`) AND `demo-catala-<module>/python/` (enables `from catala_runtime import *` inside generated files). Also ensure `demo-catala-<module>/python/__init__.py` exists (pre-flight step 3a creates it).
- **Read the `.py` file to find the `*In` class and entry function** — do not hardcode class/function names; they vary per module. Look for `class <Name>In:` and `def <name>(<name>_in:` at module level. Use `clerk.toml` `modules` field for the capitalized filename (e.g., `"Earned_income"`). The file lives at `$DOMAINS_DIR/<domain>/output/demo-catala-<module>/python/<ModuleName>.py` after pre-flight step 3a populates it.
- **`DenialReason.message` equals `code` in v1** — `str(r.code.name)` produces the raw enum name (e.g., `"EarnedIncomeExceedsLimit"`); acceptable for demo.

**Reference files (read these before generating):**
- `../../core/demo/catala.html.template` — shared HTML template; read this and substitute `{{PLACEHOLDER}}` markers for `static/index.html`
- `../../core/demo/demo-catala-eligibility/main.py` — canonical FastAPI pattern (includes ComputedBreakdown + ExclusionChainSteps)
- `../../core/demo/demo-catala-eligibility/python/Eligibility.py` — canonical Catala Python module structure
