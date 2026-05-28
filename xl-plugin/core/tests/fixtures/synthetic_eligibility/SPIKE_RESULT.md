# PA3 spike result — AI Catala emission validation

**Date:** 2026-05-28
**Plan:** [refactor/replace-civil-with-catala](../../../../docs/plans/2026-05-28-001-refactor-replace-civil-with-catala-plan.md), Pre-Phase-1 action PA3
**Result:** PASS — bet validated

## What was tested

Whether the AI (running directly against the existing `xl-plugin/core/catala-quickref.md` without the new authoring-targeted quickref U1 will produce) can emit acceptable Catala source from:

- A small synthetic policy doc (`policy.md`) — a 5-section eligibility program with income test, categorical elderly path, resource disqualification, and a final eligibility decision
- A minimal naming-manifest (`naming-manifest.yaml`) — 4 input fields, 4 computed booleans/money, 1 output

## Outcome

| Pass | Tool | Errors | Notes |
|------|------|--------|-------|
| 1 | `catala typecheck` | 1 | Module name `SpikeProgram` didn't match filename `eligibility.catala_en` |
| 2 | `clerk typecheck` (after `clerk start`) | 0 | Clean |
| 2 | `clerk test` | 0 fail / 0 pass / 0 total | No `#[test]` annotations in the fixture; clerk test is a no-op for this scope |

Wall-clock: ~3 minutes total including reading the quickref. No external research needed beyond the existing quickref.

## Lessons for U1 (Catala authoring quickref)

The new authoring-quickref should explicitly call out:

1. **Module name == CamelCase of filename.** Easy to confabulate; `> Module SpikeProgram` in `eligibility.catala_en` produces a hard error. The AI should derive the module name from the filename mechanically.
2. **`clerk start` is a per-project bootstrap.** The Catala stdlib must be linked. U2's clerk-loop tool should detect missing `_build/libcatala` and invoke `clerk start` before the first typecheck pass.
3. **`clerk test` can emit runtime warnings during dry-run evaluation** even when no `#[test]` annotations exist. These are not typecheck failures and should be classified separately in U2's diagnostic taxonomy (a `runtime_warning` category, distinct from `runtime_error`).

## Decision

The PA3 gate is GREEN. Phase 1 work (U12, U1, U2, U3) proceeds.

## Reuse

These fixture files (`policy.md`, `naming-manifest.yaml`, `eligibility.catala_en`) are preserved here for U2 to use as the single-module clerk-loop test fixture. U9 Step 1.5's two-module fixture is a separate artifact and lives at `xl-plugin/core/tests/fixtures/synthetic_multimodule/`.
