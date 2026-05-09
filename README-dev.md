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
    └── skills/          # Claude Code skills (/extract-ruleset, etc.)
```

### Active Domains

Pro-tip: To provide a coding assistant for sample data, create a symlink for the `domains` folder to point to a checkout of https://github.com/navapbc/lockpick-xlator/tree/main/domains.

| Domain | Program | Description |
|--------|---------|-------------|
| `snap` | `eligibility` | SNAP federal income eligibility (FY2026) |
| `ak_doh` | *(in progress)* | Alaska Department of Health programs |

### Adding a domain

```bash
/new-domain <domain>     # Creates domains/<domain>/{input/policy_docs,specs,output}
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
uv run tools/validate_civil.py --spec domains/snap/specs/eligibility.civil.yaml
uv run tools/transpile_to_rego.py --spec domains/snap/specs/eligibility.civil.yaml --package snap.eligibility
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
| `guidance-templates/` | Template files used to populate `guidance/` for each domain |

---

## Claude Code Slash Commands

Used for AI-assisted domain work. Run from within Claude Code (VS Code extension or CLI).

| Command | Purpose |
|---------|---------|
| `/new-domain` | Scaffold a new domain folder structure |
| `/index-inputs` | Build a reading index from large policy documents |
| `/refine-guidance` | Tune AI extraction guidance under `guidance/` |
| `/extract-ruleset` | Extract a CIVIL ruleset from policy docs in `input/policy_docs/` |
| `/update-ruleset` | Update an existing ruleset with changed policy rules |
| `/create-tests` | Generate test cases for a CIVIL module |
| `/expand-tests` | Add boundary, edge-case, and null-input tests |
| `/transpile-and-test` | Run transpile + test in one step |
| `/create-demo` | Create a demo app (Rego/OPA or Catala-Python) |

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
/new-domain <domain>

# 2. Drop policy documents into:
#    domains/<domain>/input/policy_docs/

# 3. Index inputs (for large docs)
/index-inputs <domain>

# 4. Extract ruleset interactively
/extract-ruleset <domain>

# 5. Create test cases
/create-tests <domain> <module>

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
- **`policy_facets/` folder (per-domain).** Each domain has a `policy_facets/` folder for derived views of its policy docs. Two views ship today:
  - **`policy_facets/compressed/`** — caveman-compressed mirror of `input/policy_docs/` produced by `/index-inputs`'s unified per-file batch (which invokes the per-file primitive `/compress-input`). Downstream skills that need policy doc *content* (`/extract-ruleset`, `/update-ruleset`) read these files.
  - **`policy_facets/computations/`** — per-source-file YAML lists of `{heading, summary, tags, computations?}` section blocks, one file per source doc, generated by `/index-inputs`'s unified per-file batch (which invokes `/extract-computations` per file alongside `/compress-input`). Filenames mirror the source with `.yaml` appended (e.g., `foo.md` → `foo.md.yaml`) so editors render them as YAML. Downstream skills that need policy doc *structured section data* (`/suggest-target-ruleset`, `/create-skeleton`, `/create-ruleset-groups`, `/create-ruleset-modules`, `/extract-sample-rules`, `/refine-guidance`) glob `policy_facets/computations/**/*.md.yaml`. The source path is encoded in the filename: `policy_facets/computations/<rel>.md.yaml` describes `input/policy_docs/<rel>.md` (strip the trailing `.yaml`).

  `input-index.yaml` (files block: SHAs, md_quality scores) also lives in `policy_facets/`. Index path keys still reference `input/policy_docs/<rel>.md` (the canonical source). The folder is auto-created on the first `/index-inputs` run.

  **No legacy migration.** v3.0.0 removes the previous monolithic `policy_facets/input-sections.yaml` artifact. Existing domains carrying that file are not auto-migrated — re-run `/index-inputs <domain>` to regenerate per-file files from scratch; the legacy file is left on disk for the maintainer to delete manually. `xlator extract-sections` was removed in the same release.

  **v5.0.0 — naming defaults (per-file shape break).** Per-file `policy_facets/computations/<rel>.md.yaml` files become a YAML map `{naming_manifest, sections}` (was a bare list of section blocks). `naming_manifest:` lists every variable surfaced in the file with its verbatim `policy_phrase:` and optional `role_hint:`; `sections:` retains the prior list-of-section-blocks shape. `/index-inputs` runs a new finalize step `xlator naming-defaults --build` that merges per-file `naming_manifest:` blocks into `policy_facets/naming-defaults.yaml` (canonical names + synonyms + `role_hint:` resolution) and rewrites in place any per-file `sections[*].computations[*].variables` lists whose names changed during canonicalization. Workers consult a three-level authority chain on every run — `specs/naming-manifest.yaml` (highest, analyst-confirmed) → `policy_facets/naming-defaults.yaml` (mid, auto-picked) → `core/naming_guide.md` (lowest, static style rules) — so analyst renames in `/extract-ruleset` Step 7 (recorded with `original_name:`) flow back into per-file files automatically on the next `/index-inputs` run, with no manual copy-back. The MAJOR bump reflects the per-file file-format break for every consumer skill; existing domains regenerate by re-running `/index-inputs` (no migration code, per `CLAUDE.md` "Don't migrate old files").

  **v7.0.0 — consolidate variables.yaml + per-file source_doc explicit.** `specs/guidance/variables.yaml` is gone; structural variable data (names + types + values + provenance + synonyms) consolidates into `specs/naming-manifest.yaml`, and descriptive guidance splits across four focused files: `output-variables.yaml`, `input-variables.yaml`, `include-with-output.yaml`, `constants-and-tables.yaml`. A new `xlator validate-guidance` CLI enforces name-ref alignment between manifest and guidance files. The merge tool gains a two-pass merge so phraseless seeded entries (declared by `/declare-target-ruleset` before extraction) surface as first-class canonicals in `naming-defaults.yaml`; PR #31's preserve-verbatim rule is amended to preserve-non-null so seed-time analyst values compose cleanly with defaults gap-fill. Per-file `naming_manifest.variables.<name>` entries gain explicit `source_doc:` (worker-emitted, replacing path-derivation) and rename `source_section:` → `section:` (aligning with the v6.0.0 output schema). `specs/naming-manifest.yaml`'s authority semantics broaden from "analyst-confirmed against a doc" to "analyst-authoritative — confirmed against a doc OR seeded pre-extraction"; provenance fields are nullable on seeded entries, filled in by `/extract-ruleset` Step 7. The MAJOR bump reflects the file-format break across every domain (variables.yaml deletion + nullable provenance + per-file schema rename); existing domains regenerate by re-running the full workflow.
- **CIVIL DSL version:** Key features by version:
  - v1: Baseline version with minimal features
  - v2: Enable computed/intermediate variables to decompose long formulas into multi-step computations via the `computed:` field
  - v3: Add `tags` to computed variables so they can be included as part of the output for explaining ruleset results
  - v4: Enable running ruleset module on different input data and using the results in other rules via `invoke:` ruleset module computed fields
  - v5: Allow `decisions:` fields to support any CIVIL type (money, int, string, enum, etc.) with an optional `expr:` or `conditional:`, not just `bool` eligibility and `list` reasons.
  - v6: Improve rulesets maintainability using CIVIL DSL annotations and self-review gate to modularize rulesets, separate orchestration concerns out of CIVIL rules, and minimize rule interactions.
  - v7: Add `table_lookup` as a 4th computed field variant — declarative table lookup with implicit key resolution by name match; desugars to `expr:` at transpile time via `civil_expr.normalize_computed_doc()`.
  - v8: Rename `rule_set.workflow_stages` to `rule_set.ruleset_groups` and `WorkflowStage` model to `RulesetGroup` for vocabulary consistency with the `/create-ruleset-groups` command.
  - v9: Rename top-level keys `facts:` → `inputs:` and `decisions:` → `outputs:` for plain-language clarity. Rename computed field tag `"output"` → `"expose"` to eliminate naming overlap between the new `outputs:` section and the tag used to expose computed fields to parent modules.

---

## Key Files at a Glance

| File | Role |
|------|------|
| `xl-plugin/bin/xlator` | Entry point — always run this |
| `xl-plugin/tools/civil_schema.py` | CIVIL DSL Pydantic models (schema source of truth) |
| `xl-plugin/core/CIVIL_DSL_spec.md` | DSL reference documentation |
