# Testing-First Workflow

Design document for the v11 testing-forward Xlator workflow. Captures how the PolicyBridge UI maps to Xlator commands and the changes needed to support it.

---

## Workflow Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        SETUP (pre-requisite)                        │
│  UI: "Welcome to PolicyBridge! What would you like to call it?"     │
│  UI: "What information goes in/out of the system you're building?"  │
│                                                                     │
│   /new-domain <domain>                                              │
│   user adds .md policy docs to input/policy_docs/                   │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 STEP 1 — INDEX DOCUMENTS                            │
│  UI: Documents tab · "Upload new policy documents"                  │
│  UI: spinner / background progress                                  │
│                                                                     │
│   /index-inputs <domain>                                            │
│                                                                     │
│   fans out in parallel per file:                                    │
│   ├── /compress-input <path>                                        │
│   │        writes policy_facets/compressed/<rel>.md                 │
│   └── /extract-computations <path>                                  │
│            writes policy_facets/computations/<rel>.md.yaml          │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │  (auto-triggers after upload)
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 STEP 2 — REFINE GUIDANCE                            │
│  UI: Chat pane — AI asks policy questions only                      │
│      "What are the eligibility criteria?"                           │
│      "What outputs should the system produce?"                      │
│                                                                     │
│  ⚠  AI must NOT: mention Catala, clerk, YAML, Python tools,        │
│     struct layout, iteration counts, or any backstage process       │
│                                                                     │
│   /refine-guidance <domain>  [orchestrated]                         │
│                                                                     │
│   internally runs in sequence (also available standalone):          │
│   /suggest-target-ruleset  →  /declare-target-ruleset               │
│   /create-skeleton  →  /create-ruleset-groups                       │
│   /create-ruleset-modules  →  /extract-sample-rules                 │
│   /tag-vars-to-include-with-output  →  /create-sample-tests         │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 STEP 3 — EXTRACT RULESET                            │
│  UI: Chat pane — AI asks only policy-clarification questions        │
│      (same constraint as Step 2: no technical details surfaced)     │
│                                                                     │
│   /extract-ruleset <domain>                                         │
│        emits specs/<program>.catala_en via clerk-loop               │
│        writes specs/extraction-manifest.yaml                        │
│        writes specs/naming-manifest.yaml                            │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 STEP 4 — REVIEW RULESET                             │
│  UI: Ruleset tab — computation graph, variable summary              │
│                                                                     │
│   /review-ruleset <domain>                                          │
│        renders computation graph (specs/<program>*.graph.json)      │
│        captures guidance learnings                                  │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│           STEP 5 — AUTOMATED TESTING PIPELINE  (no prompts)         │
│  UI: progress indicator · then auto-navigates to Tests tab          │
│                                                                     │
│   /create-tests <domain> --auto                                     │
│        auto-uses extracted-tests.yaml if present                    │
│        auto-runs /extract-test-cases if docs present and no yaml    │
│        seeds from guidance/sample-tests.yaml                        │
│        writes specs/tests/<program>_tests.yaml                      │
│                    │                                                │
│                    ▼                                                │
│   /transpile-and-test <domain>         ◄── NEW SKILL               │
│        calls /catala-emit-tests internally                          │
│            → transpiles YAML → specs/tests/<program>_tests.catala_en│
│            → self-checks via clerk typecheck                        │
│        runs clerk test on all fixtures                              │
│        emits :::test_results fence (routed to Tests tab UI)         │
│        records specs/tests/.catala-manifest.yaml                    │
│                    │                                                │
│                    ▼                                                │
│   /create-demo <domain>                                             │
│        generates FastAPI + browser demo app                         │
│        writes output/demo-catala-<program>/                         │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     TESTS TAB  (UI)                                 │
│   Heading: "Eligibility Tests"                                      │
│   Panel: Test Cases (list of case IDs, pass/fail badges)            │
│   Panel: Manual Test Simulator                                      │
│   Panel: How to use Tests                                           │
│   Test layouts: simulator view  or  ruleset view                    │
│   Test states: ✓ verified  ✗ rejected  → refer to ruleset          │
│                                                                     │
│   "automatically checks after all tests passed the first time"      │
└─────────────────────────────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━ MAINTENANCE PATHS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━

After policy docs change:
   /index-inputs <domain>  →  /update-ruleset <domain>
   then re-run: /create-tests <domain> --auto  →  /transpile-and-test  →  /create-demo

Expand test coverage (standalone):
   /expand-tests <domain>  →  /transpile-and-test <domain>

Audit drift across all tiers (read-only):
   /check-freshness <domain>

Sub-skill composability (advanced / standalone):
   /extract-test-cases <domain>   harvest concrete examples → extracted-tests.yaml
   /catala-emit-tests <domain>    transpile YAML → Catala fixtures (no manifest write)
   /convert-doc <file>            import .docx/.pdf → markdown
```

---

## AI Interaction Constraints (Steps 2 & 3)

The v11 design explicitly constrains what the AI surfaces to the user during `/refine-guidance` and `/extract-ruleset`:

> *"AI should only ask non-technical questions related to project goals and policy interpretation. AI should not ask technical questions. AI should not think out loud or mention technical processes happening 'backstage'."*

In practice:
- **Ask**: scope, inputs/outputs, policy intent, eligibility criteria, edge cases, thresholds
- **Never ask**: Catala syntax choices, struct layout, module naming, clerk flags, YAML schemas
- **Route to hidden fences**: all clerk output, tool invocations, iteration counts go in `:::detail` or `:::progress` (the UI chat pane shows neither)
- **User_input fences**: only for policy-domain choices, never for technical ones

---

## New Skill: `/transpile-and-test`

The SKILL.md does not yet exist, but the underlying tooling already does and is in use:

- **`xlator catala-pipeline <domain> <module>`** — copy-source-to-output → clerk typecheck → clerk test. Already referenced in `expand-tests`'s `:::next_step` fence and in the project READMEs.
- **`xlator export-test-results <domain>`** — runs clerk tests with `--trace` and exports per-case inputs + results to CSV.

The `/transpile-and-test` skill is a thin wrapper that gives this pipeline an AI-invocable SKILL.md entry, standard pre-flight checks, and a structured `:::test_results` output fence for the Tests tab UI.

### Invocation

```
/transpile-and-test <domain>
/transpile-and-test <domain> <program>
```

### What it does

1. **Pre-flight**: domain exists · Catala source exists · naming manifest exists · YAML tests exist
2. Calls `/catala-emit-tests <domain> <program>` — transpiles YAML → `.catala_en` fixtures, self-checks via `clerk typecheck`. If the clerk-loop returns `unresolved`, emit `:::error` and stop (no hand-edit prompt — the pipeline is broken).
3. Runs `xlator catala-pipeline <domain> <program>` — copies source to output, typechecks, and executes `clerk test`.
4. Runs `xlator export-test-results <domain>` to collect per-case pass/fail data.
5. Emits `:::test_results` fence with structured per-case results (see Output Format below).
6. Records tests-tier manifest: `xlator record-tier-manifest <domain> --tier tests`

### Non-interactive contract

This skill never emits `:::user_input`. It is designed to run unattended in the post-extraction pipeline. Failures surface as `:::error`.

### Output format (`:::test_results`)

```
:::test_results
module: <program>
run_at: "YYYY-MM-DD"
passed: N
failed: M
cases:
  - case_id: allow_001
    status: pass
    diff: null
  - case_id: deny_gross_001
    status: fail
    diff:
      expected: { eligible: false }
      actual:   { eligible: true }
:::
```

The `:::test_results` fence type is new (not currently in `output-fencing.md`). The UI harness routes it directly to the Tests tab without heuristic parsing.

---

## Changes to Existing Skills

### `/create-tests` — add `--auto` flag

New invocation form for the automated pipeline:

```
/create-tests <domain> <program> --auto
```

In `--auto` mode, Step 0 (the user-facing extracted-tests prompt) is bypassed:
- `extracted-tests.yaml` present → use it silently
- docs present, no yaml → run `/extract-test-cases` silently then proceed
- neither → proceed to Mode Detection without extracted tests

All other behavior (CREATE / UPDATE modes, 6-tag coverage, sample-test seeding) is unchanged. Interactive mode (no `--auto`) stays fully backward compatible.

### `/extract-ruleset` — update `:::next_step` fence

Current (line ~507):
```
:::next_step
Run the review gate to validate and finalize:
  /review-ruleset <domain> <program>
:::
```

New:
```
:::next_step
Run the review gate, then the automated testing pipeline:

  /review-ruleset <domain> <program>

  Then (runs without prompts):
  /create-tests <domain> <program> --auto
  /transpile-and-test <domain> <program>
  /create-demo <domain>

Open the Tests tab to review results.
:::
```

### `/refine-guidance` and `/extract-ruleset` — AI interaction constraints

Add a section near the top of each SKILL.md (after frontmatter):

```markdown
## AI Interaction Mode

When running in the chat-guided workflow:
- Ask only policy-domain questions: scope, input/output definitions, policy interpretation
- Never ask about technical choices (Catala syntax, struct layout, naming conventions, tool flags)
- Route all technical progress to `:::detail` or `:::progress` fences (hidden from the chat pane)
- Never "think out loud" about internal tooling — clerk loops, Python scripts, YAML writes are backstage
```

### `output-fencing.md` — add `:::test_results` fence type

Add a row to the fence-type table:

| Fence type | When to use |
|---|---|
| `:::test_results` | Structured per-case test run output from `/transpile-and-test`; routed by the UI to the Tests tab |

---

## Files to Create or Modify

| File | Action |
|------|--------|
| `skills/transpile-and-test/SKILL.md` | **Create** — skill wrapper around existing `xlator catala-pipeline` + `xlator export-test-results` |
| `skills/create-tests/SKILL.md` | Add `--auto` flag + Step 0 bypass logic |
| `skills/extract-ruleset/SKILL.md` | Update `:::next_step` fence (~line 507) |
| `skills/refine-guidance/SKILL.md` | Add AI interaction constraints section |
| `core/output-fencing.md` | Add `:::test_results` fence type |
| `CLAUDE.md` | Update workflow docs; add AI interaction constraints |
| `.claude-plugin/plugin.json` | Bump MINOR version (new non-breaking skill) |

**Not changed:** `catala-emit-tests` stays as a composable sub-skill. Its "do not record manifest" contract is preserved — `transpile-and-test` owns the manifest write.
