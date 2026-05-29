# Developer Guide — xlator plugin

Rules-as-Code pipeline: policy documents → Catala source (`.catala_en`) → `clerk typecheck` + `clerk test` → demo apps.

---

### First-time setup

Optional: install [`mise`](https://mise.jdx.dev/) for tool versions (Python 3.14, Rust, OCaml/opam).

```bash
./xlator_setup.sh        # Install uv, create .venv, install deps, install opam + catala/clerk
```

After setup, use `xlator` directly (this shim activates the venv and redirects to other scripts as needed).

---

## CLI — `xlator`

All commands follow the pattern `xlator <action> [domain] [module]`.

```bash
xlator list                              # Show all domain-module pairs
```

### Pipeline (Catala)

```bash
xlator catala-test-transpile  <domain> <module>   # YAML tests → Catala test file
xlator catala-test            <domain> <module>   # Run Catala tests via clerk
xlator catala-pipeline        <domain> <module>   # clerk typecheck → catala-test-transpile → clerk test
xlator clerk-loop             <domain> <module>   # Run the U2 clerk loop (typecheck + test + diagnostics)
```

### Demos

```bash
xlator catala-demo  <domain> <module>    # Start Catala-Python demo (foreground)
```

### Utilities

```bash
xlator graph              <domain> <module>   # Generate computation graph + Mermaid diagram (catala_depgraph.py)
xlator evaluate-catala    <domain> <module>   # Deterministic evaluator (catala interpret --output-format=json wrapper)
```

**Quick start with the AK DOH domain:**

```bash
xlator catala-pipeline ak_doh eligibility     # typecheck + transpile YAML tests + clerk test
xlator catala-demo ak_doh eligibility         # Launch interactive demo at localhost
```

---

## Project Layout

```
xlator/
├── xlator               # Shell wrapper — run this (handles venv activation)
├── core/                # Shared references: Catala quickrefs, naming-guide
├── domains/             # Folder containing multiple domains (default is 'domains')
│   └── <domain>/        # One folder per policy domain (source of truth)
│       ├── input/       # Raw policy documents (PDFs, Markdown, HTML)
│       ├── specs/       # Catala source (.catala_en) + naming-manifest + test YAML
│       └── output/      # Generated build artifacts (Catala-source copy, *_meta.py, demos)
├── tools/               # Python pipeline scripts
├── docs/                # Brainstorms, plans, solutions
│   ├── brainstorms/
│   ├── plans/
│   └── solutions/
└── .claude/
    └── skills/          # Claude Code skills (/extract-ruleset, etc.)
```

### Active Domains

Pro-tip: To provide a coding assistant with sample data, create a symlink for the `domains` folder to point to a checkout of https://github.com/navapbc/lockpick-xlator/tree/main/domains.

| Domain | Program | Description |
|--------|---------|-------------|
| `snap` | `eligibility` | SNAP federal income eligibility (FY2026) |
| `ak_doh` | `eligibility`, `exclusion_chain` | Alaska Department of Health programs (multi-module) |

### Adding a domain

```bash
/new-domain <domain>     # Creates domains/<domain>/{input/policy_docs,specs,output}
```

---

## `tools/` Scripts

| Script | Action | Purpose |
|--------|--------|---------|
| `clerk_loop.py` | `clerk-loop` | Runs `clerk typecheck` + `clerk test`, parses GNU-format diagnostics, performs naming-manifest divergence check, returns structured outcome (`status: "ok" \| "unresolved"`, `repair_history`). Library API used by authoring skills' post-emission step. |
| `catala_eval.py` | `evaluate-catala` | Thin wrapper around `catala interpret --output-format=json`. Preserves the JSON contract consumed by `/expand-tests`, `/detect-stale-cases`, `/create-tests`. |
| `transpile_to_catala_tests.py` | `catala-test-transpile` | YAML test cases → Catala `#[test]` scopes. Reads `specs/naming-manifest.yaml` for type metadata. |
| `catala_depgraph.py` | `graph` | Computation-graph generator (Catala-native). Produces `.graph.yaml` and `.mmd`. |
| `catala_to_python.sh` | *(via /create-demo)* | Catala → Python transpiler (invokes `clerk` build). |
| `merge_naming_manifest.py` | `merge-naming-manifest` | Merges per-emission manifest deltas with the analyst-authoritative source. |
| `load_extraction_context.py` | `load-extraction-context` | Pre-flight context loader for `/extract-ruleset` (manifest + guidance + facets). |
| `detect_stale_cases.py` | `detect-stale-cases` | Detects test cases whose expected outcomes diverge from the live Catala source. |
| `check_freshness.py` | `check-freshness` | Per-tier drift detection between `specs/*.catala_en` and `.catala-manifest.yaml`. |
| `record_tier_manifest.py` | `manifest-update` | Refresh SHAs in the per-tier manifest files. |
| `apa_html_to_md.py` | *(utility)* | Scrapes Alaska APA manual HTML → Markdown for input collection. |

All tools can be run directly:

```bash
uv run xl-plugin/tools/clerk_loop.py snap eligibility \
  --module-path domains/snap/specs/eligibility.catala_en
uv run xl-plugin/tools/catala_eval.py snap eligibility --inputs case.json
```

---

## `core/` Reference Files

| File | Description |
|------|-------------|
| `catala-authoring-quickref.md` | AI-targeted Catala authoring reference: scopes, contextual definitions, exceptions, modules, comprehensions, denial_reasons idiom, fence visibility, common AI failure modes |
| `catala-quickref.md` | General Catala 1.1.0 syntax patterns |
| `catala-test-quickref.md` | Catala `#[test]` annotation patterns |
| `naming_guide.md` | Static style rules for canonical identifier names (fallback under the authority chain) |
| `ruleset-shared.md` | Shared procedures (`SP-LoadNamingManifest`, `SP-LoadInputIndex`, etc.) consumed by every authoring skill |
| `output-fencing.md` | Semantic fence-block convention (`:::important`, `:::error`, etc.) for skill output |
| `proposed_transpilation_fixes_snapshot.md` | Recovered AI-failure-mode catalogue (six categories) that drives `catala-authoring-quickref.md` |
| `tests/fixtures/` | Persistent Catala fixtures used by `clerk_loop` test suite and U9 verification gates |
| `guidance-examples/` | Reference examples of populated `guidance/` files — illustrative only; no skill copies from this directory |

---

## Claude Code Slash Commands

Used for AI-assisted domain work. Run from within Claude Code (VS Code extension or CLI).

| Command | Purpose |
|---------|---------|
| `/new-domain` | Scaffold a new domain folder structure |
| `/index-inputs` | Build a reading index from large policy documents |
| `/refine-guidance` | Tune AI extraction guidance under `guidance/` |
| `/extract-ruleset` | Emit `specs/<module>.catala_en` from policy docs; runs clerk-loop post-emission |
| `/update-ruleset` | Update an existing Catala source with changed policy rules; Step 0 divergence check |
| `/create-tests` | Generate YAML test cases for a Catala module |
| `/expand-tests` | Add boundary, edge-case, and null-input tests via `catala_eval` |
| `/review-ruleset` | Refresh graph artifacts; capture guidance learnings |
| `/check-freshness` | Detect drift between input data and downstream artifacts |
| `/create-demo` | Create a Catala-Python demo app |

---

## Typical Development Workflow

### Edit an existing domain

```bash
# 1. Drive /update-ruleset to apply a policy-doc change
/update-ruleset <domain> <module>

# 2. Run the pipeline (typecheck + test transpile + clerk test)
xlator catala-pipeline <domain> <module>

# 3. (Optional) Regenerate computation graph
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

# 4. Refine guidance (orchestrated or step-by-step)
/refine-guidance <domain>

# 5. Extract ruleset (emits Catala via the clerk-loop)
/extract-ruleset <domain>

# 6. Create test cases
/create-tests <domain> <module>

# 7. Run the full pipeline
xlator catala-pipeline <domain> <module>
```

---

## Architecture Notes

- **Catala is the authored source format** as of v13.0.0. `domains/<d>/specs/<module>.catala_en` is the human-+-AI-authored truth; `domains/<d>/output/<module>.catala_en` is a copy of the source maintained by the build step for consumer compatibility (`catala_depgraph.py`, the FastAPI demo, and other build-artifact consumers continue reading from `output/`).
- **Two-phase authoring discipline.** Deterministic Python tools handle pre-flight context loading and post-emission verification; the AI emits Catala content. The clerk-loop (`xl-plugin/tools/clerk_loop.py`) runs `clerk typecheck` + `clerk test` after each AI emission and self-repairs before SME handoff.
- **Naming-manifest authority chain (two-tier).** `specs/naming-manifest.yaml` (analyst-authoritative, includes per-field types as of U7) → `core/naming_guide.md` (static style rules). The manifest carries identifier names AND types so `transpile_to_catala_tests.py` can emit `#[test]` scopes without parsing Catala source.
- **`policy_facets/` folder (per-domain).** Each domain has a `policy_facets/` folder for derived views of its policy docs:
  - **`policy_facets/compressed/`** — caveman-compressed mirror of `input/policy_docs/`, produced by `/index-inputs`. Downstream skills (`/extract-ruleset`, `/update-ruleset`) read these files for content.
  - **`policy_facets/computations/`** — per-source-file YAML lists of `{heading, summary, tags, computations?}` section blocks. Catala authoring uses these section blocks to mirror Markdown `## Heading` structure inside the literate `.catala_en` source.

  `input-index.yaml` (files block: SHAs, md_quality scores) also lives in `policy_facets/`.

---

## Key Files at a Glance

| File | Role |
|------|------|
| `xl-plugin/bin/xlator` | Entry point — always run this |
| `xl-plugin/tools/clerk_loop.py` | clerk-loop library + CLI (authoring skills' post-emission verification) |
| `xl-plugin/tools/catala_eval.py` | Deterministic Catala evaluator |
| `xl-plugin/core/catala-authoring-quickref.md` | AI-targeted Catala authoring reference |
