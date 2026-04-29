# Generate a Demo App (Rego/OPA Backend)

Generate a working FastAPI + browser demo for a domain's policy module using the **Rego/OPA backend**. Reads the CIVIL spec and test manifest to produce four files in `$DOMAINS_DIR/<domain>/output/demo-rego-<module>/`.

## Input

```
/rego-create-demo                        # auto-detect domain/module or prompt if ambiguous
/rego-create-demo <domain>               # use that domain; auto-detect module
/rego-create-demo <domain> <module>      # skip scanning entirely
```

If no args are provided, list all `$DOMAINS_DIR/*/specs/*.civil.yaml` files and prompt the user to choose.

## Pre-flight

1. **Domain folder exists?** — NO → Print:
   :::error
   Domain '<domain>' not found. Run /xl:new-domain <domain> first.
   :::
   Stop.
2. **CIVIL file exists?**
   - `$DOMAINS_DIR/<domain>/specs/<module>.civil.yaml` missing → Print:
     :::error
     No CIVIL file found. Run /xl:extract-ruleset <domain> first.
     :::
     Stop.
3. **Transpiled output exists?**
   - Check `$DOMAINS_DIR/<domain>/output/<module>.rego`
   - Not found → Print:
     :::error
     No .rego file found. Run /xl:transpile-and-test <domain> <module> first.
     :::
     Stop.
4. **Test manifest present?**
   - `$DOMAINS_DIR/<domain>/specs/tests/<module>_tests.yaml` missing → note: proceed with placeholder examples; print warning at the end.

---

## Mode Detection

```bash
ls $DOMAINS_DIR/<domain>/output/demo-rego-<module>/ 2>/dev/null
```

| Result | Mode |
|--------|------|
| Directory absent | **CREATE mode** |
| Directory present | **UPDATE mode** — prompt: `"Demo already exists at $DOMAINS_DIR/<domain>/output/demo-rego-<module>/. Regenerate and overwrite? (y/n)"` — abort on N |

:::user_input
Demo already exists at $DOMAINS_DIR/<domain>/output/demo-rego-<module>/. Regenerate and overwrite? (y/n)
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
mkdir -p $DOMAINS_DIR/<domain>/output/demo-rego-<module>/static
```

### Step 3: Write `requirements.txt`

Static content, no domain-specific substitutions:

```
fastapi
uvicorn[standard]
httpx
pydantic
```

### Step 4: Write `start.sh`

Model on `$CLAUDE_PLUGIN_ROOT/core/demo/demo-rego-eligibility/start.sh`. Key substitutions:

```bash
#!/usr/bin/env bash
# Start the Xlator <Domain> <Module> Demo
# ...

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"       # always 4 levels up
REGO_FILE="$REPO_ROOT/$DOMAINS_DIR/<domain>/output/<module>.rego"
OPA_PORT=8181
FASTAPI_PORT=8000
```

The Rego prerequisite error message must use the correct domain/module:
```bash
  echo "  ${CLAUDE_PLUGIN_ROOT}/xlator rego-transpile <domain> <module>"
```

Keep all other logic verbatim: OPA health-check loop, cleanup trap, uvicorn launch.

### Step 5: Write `main.py`

Model on `$CLAUDE_PLUGIN_ROOT/core/demo/demo-rego-eligibility/main.py`. Key substitutions:

**Constants:**
```python
OPA_URL = "http://localhost:8181"
OPA_DECISION_PATH = "/v1/data/<domain>/<module>/decision"
```

**App title** (use human-readable domain/module names from CIVIL `metadata:`):
```python
app = FastAPI(
    title="Xlator <Domain> <Module> Demo",
    description="Evaluates <module> using OPA-compiled CIVIL rules",
    ...
)
```

**`InputFacts` Pydantic model** — one field per `inputs.<Entity>.fields` entry:

| CIVIL type | Python type | Field default |
|-----------|-------------|---------------|
| `int` | `int` | `Field(..., ...)` if required; `Field(0, ...)` if optional |
| `money` | `float` | `Field(..., ge=0, ...)` if required; `Field(0.0, ge=0, ...)` if optional |
| `bool` | `bool` | `Field(False, ...)` (always optional) |
| `string` | `str` | `Field(..., ...)` if required; `Field("", ...)` if optional |

Use the CIVIL `description:` as the Pydantic `description=` string.

**`ComputedBreakdown` Pydantic model** — one field per `computed:` key:
- Keys ending in `_deduction`, `_income`, `_limit`, `_excess`, `_costs` → `float`
- Keys starting with `passes_` or `is_` → `bool`
- Default to `float` when unsure

**`DenialReason` model** (standard, copy verbatim):
```python
class DenialReason(BaseModel):
    code: str
    message: str
    citation: str = ""
```

**Response model** — derive from `outputs:`:
- `eligible: bool` field → `eligible: bool`
- `list` type field (e.g., `reasons`) → `list[DenialReason]`
- Always include `computed: ComputedBreakdown`

**API route:**
```python
@app.post("/api/<domain>/<module>", response_model=<ResponseModel>)
async def check_eligibility(facts: InputFacts):
    payload = {"input": facts.model_dump()}
    ...
    return <ResponseModel>(
        eligible=result["eligible"],
        reasons=[DenialReason(**r) for r in result.get("reasons", [])],
        computed=ComputedBreakdown(**result["computed"]),
    )
```

Keep lifespan OPA health-check, error handling (ConnectError, HTTPStatusError, TimeoutException), and `/health` endpoint verbatim.

### Step 6: Write `static/index.html`

Read the shared Rego HTML template and substitute all `{{PLACEHOLDER}}` markers with domain-specific content:

```bash
# Read: $CLAUDE_PLUGIN_ROOT/core/demo/rego.html.template
```

The template has `.breakdown-table`, `.test-result`, and `testIcon()` already baked in — do not duplicate them in `{{EXTRA_CSS}}` or `{{EXTRA_JS_HELPERS}}`.

**Placeholder contract** — substitute each marker exactly as described:

| Placeholder | Required? | Content | Notes |
|---|---|---|---|
| `{{PAGE_TITLE}}` | Required | Human-readable module name, e.g. `SNAP Eligibility` | Plain text; template provides `<title>` and `<h1>` wrappers |
| `{{SUBTITLE_HTML}}` | Required | Inner HTML of subtitle paragraph | May include `<a>` links; template provides `<p class="subtitle">` wrapper |
| `{{EXTRA_CSS}}` | Optional | Domain-specific CSS rules only (no `<style>` tag) | Write empty string if none; template provides `<style id="extra">` wrapper |
| `{{FIELDS_HTML}}` | Required | Complete form markup: `.card` div, `<form>` tags, fields, submit button | Includes opening and closing `<form>` tags |
| `{{EXAMPLE_BUTTONS_HTML}}` | Required | `<button class="example-btn" onclick="loadExample(...)">` elements | One per test case |
| `{{EXAMPLES_JS}}` | Required | `const EXAMPLES = { … };` statement | Full statement including `const` keyword |
| `{{LOAD_EXAMPLE_BODY_JS}}` | Required | Lines setting field values via `document.getElementById` | Template provides `function loadExample(key) {` wrapper; write body only |
| `{{EXTRA_JS_HELPERS}}` | Optional | Named function declarations (hoisting-safe) | Write empty string if none |
| `{{RENDER_RESULTS_JS}}` | Required | Body of `renderResults` only | Template provides `function renderResults(data, payload) {` wrapper; write body only |
| `{{SUBMIT_PAYLOAD_JS}}` | Required | Key-value pairs for the payload object | Template provides `const payload = { … };`; write only the contents (no braces) |
| `{{API_PATH}}` | Required | Full API endpoint path, e.g. `/api/snap/eligibility` | Used verbatim in `fetch('{{API_PATH}}', …)` |
| `{{FOOTER_HTML}}` | Required | Footer paragraph(s) | May include links; template provides the `<footer>` wrapper |

**`{{EXTRA_CSS}}`** — always include badge variant rules:
```css
.badge.eligible { background: #e8f5e9; color: #1b5e20; }
.badge.ineligible { background: #ffebee; color: #b71c1c; }
```

**`{{FIELDS_HTML}}`** — complete form structure. One `<div class="field">` per `inputs.<Entity>.fields` entry:
- `bool` → `<input type="checkbox" id="<name>" name="<name>">` with label
- `int` / `money` → `<input type="number" id="<name>" name="<name>" min="0" step="1">`
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
document.getElementById('field_name').value = ex.field_name;   // numbers
document.getElementById('bool_field').checked = ex.bool_field; // booleans
```

**`{{SUBMIT_PAYLOAD_JS}}`** — key-value pairs only (no surrounding braces), one per input fact field:
- `bool` → `document.getElementById('<name>').checked`
- `int` → `parseInt(document.getElementById('<name>').value)`
- `money` → `parseFloat(document.getElementById('<name>').value) || 0`

**`{{RENDER_RESULTS_JS}}`** — function body only (no `function renderResults(...)` line). In scope: `data` (API response), `payload` (submitted values), `fmt(v)` (money formatter), `testIcon(pass)` (✓/✗), any helpers from `{{EXTRA_JS_HELPERS}}`. Show eligible/ineligible badge, breakdown table for numeric `computed:` fields, pass/fail `<div class="test-result">` for boolean `computed:` fields starting with `passes_`, and denial reasons list if `!eligible && data.reasons.length > 0`. Note: field is `data.computed` (not `data.breakdown` — that is the Catala pattern).

**`{{EXTRA_JS_HELPERS}}`** — named `function` declarations for domain-specific rendering helpers. Write empty string if none.

**Verification**: After writing, confirm no literal `{{...}}` strings remain in the output file.

### Step 7: Print Summary

:::important
Demo created at $DOMAINS_DIR/<domain>/output/demo-rego-<module>/
  requirements.txt
  start.sh
  main.py
  static/index.html
:::

If no test manifest was found, print:
:::important
⚠  No test manifest found — EXAMPLES in index.html contain TODO placeholders.
   Run /xl:create-tests <domain> <module> for realistic example scenarios.
:::

:::next_step
Next steps:
  1. Install deps:   pip install -r $DOMAINS_DIR/<domain>/output/demo-rego-<module>/requirements.txt
  2. Run the demo:   ${CLAUDE_PLUGIN_ROOT}/xlator rego-demo <domain> <module>
  3. Open browser:   http://localhost:8000/static/index.html
  4. API docs:       http://localhost:8000/docs
:::

---

## Process — UPDATE Mode

After confirming overwrite, execute CREATE mode in full. Overwrite all 4 files.

---

## CIVIL Type → Generated Artifact Summary

| CIVIL field | Rego mode artifact |
|-------------|-------------------|
| `inputs.<Entity>.fields[type=int/money/bool/string]` | `InputFacts` Pydantic fields; `<input>` elements; `payload` fields in submit handler |
| `computed:` keys | `ComputedBreakdown` Pydantic fields; breakdown table rows + test-result divs in `renderResults()` |
| `outputs:` keys | Response model fields; badge + denial list in `renderResults()` |
| `metadata.domain` + module name | `OPA_DECISION_PATH`, FastAPI route, app title, page title |
| Test cases (up to 3) | `EXAMPLES` dict + button labels in `index.html` |

---

## Common Mistakes to Avoid

- **Do NOT hardcode `snap` or `eligibility`** — derive all names from `<domain>` and `<module>` args
- **Do NOT copy SNAP-specific field names** — read the CIVIL spec and derive field names from it
- **Do NOT include Rego generation logic** — Rego is a pre-existing prerequisite
- **Use `inputs.<Entity>.fields` keys verbatim** as Python attribute names and HTML `id`/`name` values — they are already snake_case
- **The `computed:` block may have `conditional:` entries** — extract the key name only; ignore the expression
- **The `outputs:` block may have a `list` type field** (e.g., `reasons`) — map to `list[DenialReason]` in Python
- **`start.sh` REPO_ROOT is always 4 levels up** from the script — `$(cd "$SCRIPT_DIR/../../../.." && pwd)` — do not change this

**Reference files (read these before generating):**
- `$CLAUDE_PLUGIN_ROOT/core/demo/rego.html.template` — shared HTML template; read this and substitute `{{PLACEHOLDER}}` markers for `static/index.html`
- `$CLAUDE_PLUGIN_ROOT/core/demo/demo-rego-eligibility/main.py` — canonical FastAPI pattern
- `$CLAUDE_PLUGIN_ROOT/core/demo/demo-rego-eligibility/start.sh` — canonical launcher pattern
