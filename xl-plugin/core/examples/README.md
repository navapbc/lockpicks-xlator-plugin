# Examples Corpus

This directory holds canonical reference examples of every AI-generated artifact the plugin produces. Skills and tests read these canonicals to anchor output shape and prevent drift across runs.

## What lives here

One subfolder per artifact type (file type / skill), each containing the canonical example for that artifact:

```
core/examples/
  <file-type>/
    canonical.<ext>     # the reference example
    [variant.<ext>]     # only when canonical cannot anchor the shape (see R3)
    [source.md]         # paired examples (transformation skills)
    [README.md]         # paired examples that point at canonicals in other folders
```

All examples describe a single fictional eligibility program — **Determine AK Medicaid Eligibility** — so cross-file references (a `ruleset-modules.yaml` referenced from `skeleton.yaml`, a `civil-ruleset` example built from the guidance-tier canonicals) stay coherent.

This corpus is **not** under `$DOMAINS_DIR`. `xlator.py` does not need to exclude it.

## How skills consume it

Each in-scope skill's `SKILL.md` declares one of three consumption modes:

| Mode | When | Mechanism |
|---|---|---|
| **Required-read** | Default for medium-to-large or variance-prone artifacts | `Read `../../core/examples/<type>/canonical.yaml` now.` line in the SKILL.md, fired before the skill emits output |
| **Inlined** | Tiny, structurally-fixed files (e.g., `metadata.yaml`) | YAML block embedded directly in the SKILL.md. A canonical file also lives at `core/examples/metadata/canonical.yaml` so the corpus stays uniform; the two copies are kept in sync verbatim |
| **Loose pointer** | Rare; reserved for cases where shape compliance doesn't affect correctness | Plain prose citation, no required-read |

For paired transformation skills (`extract-ruleset`, `index-inputs`, `compress-input`, `extract-computations`), the required-read step instructs the AI to read **both** the input and output files so the mapping is visible, not just the output shape.

## Paired examples (3 shapes)

| Layout | Folders | Why |
|---|---|---|
| **One-output, one-input, corpus-local** | `compressed/`, `computations/` | Input is corpus-authored prose (`source.md`); output sits next to it (`canonical.md` or `canonical.md.yaml`). Two siblings; no subfolders. |
| **One-output, small-corpus-local-input-set** | `input-index/` | Output index sits at the folder root; sources live under `input/policy_docs/<file>.md` so the canonical's keys mirror real-shape paths. |
| **Inputs point at other canonicals** | `civil-ruleset/` | The `extract-ruleset` input bundle (7 YAML files) is **not** copied into `civil-ruleset/`. Instead, `civil-ruleset/README.md` enumerates the 7 input canonicals by path; the `extract-ruleset` required-read step reads all 8 files. Eliminates silent drift between paired-folder copies and source canonicals. |

## Variants (R3)

A second file under a file-type subfolder is added **only** when that file type has a genuinely distinct second shape the canonical cannot anchor (e.g., a single-module skeleton when the canonical is multi-module). Variants declare in-folder how they differ from the canonical. No speculative variants — add when a concrete need surfaces.

## Test access

Tests reach into this corpus via the shared helper at [xl-plugin/tools/test_helpers.py](../../tools/test_helpers.py):

```python
from test_helpers import load_canonical, canonical_path

manifest = load_canonical("naming-manifest")       # parsed dict
folder   = canonical_path("compressed")            # Path to the folder, for non-YAML siblings
source   = (folder / "source.md").read_text()
```

`load_canonical` covers YAML canonicals (`.yaml`, `.civil.yaml`). For `.md` / `.md.yaml` canonicals (`compressed/`, `computations/`), use `canonical_path` to resolve the folder and read directly. A text-loading helper will be added if a second test needs it.

Test-only fixtures (malformed inputs, edge cases with no AI-reference value) belong under `xl-plugin/tools/test_fixtures/`, **not** here. That directory is created when the first such fixture is needed.

## Scope

Covered: every AI-generated artifact for `suggest-target-ruleset`, `declare-target-ruleset`, `refine-guidance`, `create-skeleton`, `create-ruleset-groups`, `create-ruleset-modules`, `extract-sample-rules`, `create-sample-tests`, `extract-ruleset`, `create-tests`, `expand-tests`, `index-inputs`, `compress-input`, `extract-computations`.

Explicitly excluded: `include-with-output.yaml` (deterministic via `tag_vars_include_output.py`), Catala/Rego transpiled outputs, and all auto-generated manifests (`.facets-manifest.yaml`, `extraction-manifest.yaml`, `.civil-manifest.yaml`).
