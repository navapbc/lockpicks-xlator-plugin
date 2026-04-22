# Developer Guide — xlator plugin

Rules-as-Code pipeline: policy documents → CIVIL DSL (YAML) → Catala (or OPA/Rego) → demo apps.

---

### First-time setup

Optional: install [`mise`](https://mise.jdx.dev/) for tool versions (Python 3.14, OPA, Rust, OCaml/opam).

```bash
./xlator_setup.sh        # Install uv, create .venv, install deps, install OPA
```

After setup, use `xlator` directly (this shim activates the venv and redirects to other scripts as needed).

---

## CLI — `xlator`

All commands follow the pattern `xlator <action> [domain] [module]`.

```bash
xlator list                              # Show all domain-module pairs
```

### Pipeline (using Catala)

```bash
xlator validate               <domain> <module>   # Validate CIVIL YAML against schema
xlator catala-transpile       <domain> <module>   # CIVIL YAML → Catala (.catala_en)
xlator catala-test-transpile  <domain> <module>   # YAML tests → Catala test file
xlator catala-test            <domain> <module>   # Run Catala tests via clerk
xlator catala-pipeline        <domain> <module>   # validate → catala-transpile → catala-test-transpile → catala-test (full CI)
```

### Rego (OPA backend)

```bash
xlator rego-transpile   <domain> <module>     # Generate Rego from CIVIL YAML
xlator rego-test        <domain> <module>     # Start OPA, run tests, stop OPA
xlator rego-pipeline    <domain> <module>     # validate → rego-transpile → rego-test (full CI)
```

### Demos

```bash
xlator catala-demo  <domain> <module>    # Start Catala-Python demo (foreground)
xlator rego-demo    <domain> <module>    # Start OPA + FastAPI demo (foreground)
```

### Utilities

```bash
xlator graph              <domain> <module>   # Generate computation graph + Mermaid diagram

xlator generate-schema                        # Regenerate core/ruleset.schema.json to enable VSCode hover tips for civil.yaml files
```

**Quick start with the AK DOH domain:**

```bash
xlator catala-pipeline ak_doh eligibility     # Validate + transpile + test
xlator catala-demo ak_doh eligibility         # Launch interactive demo at localhost
```

---

## Project Layout

```
xlator/
├── xlator               # Shell wrapper — run this (handles venv activation)
├── core/                # Shared references: CIVIL spec, schemas, quickrefs
├── domains/             # Folder containing multiple domains (default is 'domains')
│   └── <domain>/        # One folder per policy domain (source of truth)
│       ├── input/       # Raw policy documents (PDFs, Markdown, HTML)
│       ├── specs/       # CIVIL YAML + test YAML (hand-authored/AI-extracted)
│       └── output/      # Generated Catala, Rego, demos — DO NOT hand-edit
├── tools/               # Python pipeline scripts
├── docs/                # Brainstorms, plans, solutions
│   ├── brainstorms/
│   ├── plans/
│   └── solutions/
└── .claude/
    └── commands/        # Claude Code slash commands (/extract-ruleset, etc.)
```

### Active Domains

Pro-tip: To provide a coding assistant for sample data, create a symlink for the `domains` folder to point to a checkout of https://github.com/navapbc/lockpick-xlator/tree/main/domains.

| Domain | Program | Description |
|--------|---------|-------------|
| `snap` | `eligibility` | SNAP federal income eligibility (FY2026) |
| `ak_doh` | *(in progress)* | Alaska Department of Health programs |

### Adding a domain

```bash
/xl:new-domain <domain>     # Creates domains/<domain>/{input/policy_docs,specs,output}
```

---

## `tools/` Scripts

| Script | Action | Purpose |
|--------|--------|---------|
| `validate_civil.py` | `validate` | Validates CIVIL YAML using Pydantic schema. Detects circular deps and missing refs. |
| `civil_schema.py` | `generate-schema` | Pydantic v2 data models — the single source of truth for CIVIL DSL structure. |
| `civil_expr.py` | *(internal)* | Expression parser: resolves field refs from CIVIL expression strings. |
| `transpile_to_rego.py` | `transpile` | CIVIL YAML → OPA/Rego. Fully generic; no domain-specific code. |
| `transpile_to_catala.py` | `catala-transpile` | CIVIL YAML → Catala 1.1.0 literate program. Handles multi-module deps. |
| `transpile_to_catala_tests.py` | `catala-test-transpile` | YAML test cases → Catala test file (`#[test]` pattern). |
| `rego-run_tests.py` | `test` | Hits OPA REST at `/v1/data/<pkg>/<module>/decision`, reports pass/fail. |
| `computation_graph.py` | `graph` | Generates `<program>.graph.yaml` + `.mmd` (Mermaid diagram). |
| `catala_depgraph.py` | *(utility)* | Converts Catala files or graph JSON → Graphviz/Mermaid/PNG. |
| `apa_html_to_md.py` | *(utility)* | Scrapes Alaska APA manual HTML → Markdown for input collection. |

All tools can be run directly:

```bash
python tools/validate_civil.py --spec domains/snap/specs/eligibility.civil.yaml
python tools/transpile_to_rego.py --spec domains/snap/specs/eligibility.civil.yaml --package snap.eligibility
```

---

## `core/` Reference Files

| File | Description |
|------|-------------|
| `CIVIL_DSL_spec.md` | Full CIVIL DSL spec with expression language, design rationale, examples |
| `civil-quickref.md` | Syntax quick reference for CIVIL YAML fields |
| `catala-quickref.md` | Catala 1.1.0 syntax patterns |
| `catala-test-quickref.md` | Catala test annotation patterns |
| `ruleset.schema.json` | JSON Schema (auto-generated — regenerate via `xlator generate-schema`) |
| `guidance-templates/` | Template files used to create guidance.yaml for each domain |

---

## Claude Code Slash Commands

Used for AI-assisted domain work. Run from within Claude Code (VS Code extension or CLI).

| Command | Purpose |
|---------|---------|
| `/xl:new-domain` | Scaffold a new domain folder structure |
| `/xl:index-inputs` | Build a reading index from large policy documents |
| `/xl:refine-guidance` | Tune AI extraction guidance in `guidance.yaml` |
| `/xl:extract-ruleset` | Extract a CIVIL ruleset from policy docs in `input/policy_docs/` |
| `/xl:update-ruleset` | Update an existing ruleset with changed policy rules |
| `/xl:create-tests` | Generate test cases for a CIVIL module |
| `/xl:expand-tests` | Add boundary, edge-case, and null-input tests |
| `/xl:transpile-and-test` | Run transpile + test in one step |
| `/xl:create-demo` | Create a demo app (Rego/OPA or Catala-Python) |

---

## Typical Development Workflow

### Investigate an existing domain

```bash
# 1. Edit the CIVIL spec
#    domains/<domain>/specs/<module>.civil.yaml

# 2. Validate your changes
xlator validate <domain> <module>

# 3. Regenerate Rego and re-run tests
xlator rego-pipeline <domain> <module>

# 4. (Optional) Regenerate computation graph
xlator graph <domain> <module>
```

### Adding a new domain from policy docs

```bash
# 1. Scaffold the folder
/xl:new-domain <domain>

# 2. Drop policy documents into:
#    domains/<domain>/input/policy_docs/

# 3. Index inputs (for large docs)
/xl:index-inputs <domain>

# 4. Extract ruleset interactively
/xl:extract-ruleset <domain>

# 5. Create test cases
/xl:create-tests <domain> <module>

# 6. Run the full pipeline
xlator rego-pipeline <domain> <module>
```

---

## Architecture Notes

- **Transpilers are fully generic.** Domain-specific logic lives in CIVIL YAML (`computed:` with `conditional:`), never in `tools/*.py`.
- **`output/` is generated.** Never hand-edit files under `domains/*/output/`. Regenerate via `xlator rego-transpile`.
- **OPA query path convention:** `/v1/data/<pkg>/<module>/decision` — package maps directly to `<domain>.<module>`.
- **Rego constraints to know:**
  - `||` is not valid in a Rego rule body — OR logic requires multiple rules with the same head.
  - Always emit `default rule := false` for boolean rules — missing this causes the entire `decision` object to be undefined.
  - CIVIL `max(a, b)` → Rego `max([a, b])`.
- **Shell environment:** `rm` is aliased to `rm -i` — use `rm -f` in scripts to avoid interactive prompts.
- **CIVIL DSL version:** Key features by version:
  - v1: Baseline version with minimal features
  - v2: Enable computed/intermediate variables to decompose long formulas into multi-step computations via the `computed:` field
  - v3: Add `tags` to computed variables so they can be included as part of the output for explaining ruleset results
  - v4: Enable running sub-ruleset on different input data and using the results in other rules via `invoke:` sub-ruleset computed fields
  - v5: Allow `decisions:` fields to support any CIVIL type (money, int, string, enum, etc.) with an optional `expr:` or `conditional:`, not just `bool` eligibility and `list` reasons.
  - v6: Improve rulesets maintainability using CIVIL DSL annotations and self-review gate to modularize rulesets, separate orchestration concerns out of CIVIL rules, and minimize rule interactions.
  - v7: Add `table_lookup` as a 4th computed field variant — declarative table lookup with implicit key resolution by name match; desugars to `expr:` at transpile time via `civil_expr.normalize_computed_doc()`.

---

## Key Files at a Glance

| File | Role |
|------|------|
| `xl-plugin/bin/xlator` | Entry point — always run this |
| `xl-plugin/tools/civil_schema.py` | CIVIL DSL Pydantic models (schema source of truth) |
| `xl-plugin/core/CIVIL_DSL_spec.md` | DSL reference documentation |
