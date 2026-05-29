# Synthetic two-module fixture (U9 Step 1.5)

A persistent two-module Catala fixture exercising `> Using Module`, sub-module exported types, and a deliberate cross-module contract scenario. Used by the CIVIL→Catala pivot's U9 verification gate ([2026-05-28-001-refactor-replace-civil-with-catala-plan.md](../../../../../docs/plans/2026-05-28-001-refactor-replace-civil-with-catala-plan.md), Step 1.5) as the pre-`ak_doh`-regeneration sanity check.

## Files

| File | Role |
|------|------|
| `policy.md` | Fake policy doc, shape mirrors ak_doh's two-module structure |
| `Earnings.catala_en` | Sub-module: exports `EarningsResult` struct and `ApplyExclusions` scope |
| `Eligibility.catala_en` | Parent module: `> Using Earnings`, references `Earnings.EarningsResult` and `Earnings.ApplyExclusions` |
| `naming-manifest.yaml` | Type-extended manifest (per U7) for both modules |
| `clerk.toml` | Registers both modules under the `eligibility` target |

## Loop verification outcome (2026-05-28)

```
$ uv run --no-project xl-plugin/tools/clerk_loop.py synthetic synthetic_multimodule \
    --module-path /<abs-path>/xl-plugin/core/tests/fixtures/synthetic_multimodule/Eligibility.catala_en

{"status": "ok", "iterations": 1, "diagnostic_count": 0, "regenerate_recommended": false, ...}
clerk typecheck + clerk test passed on iteration 1.
```

- `clerk typecheck` on `Eligibility.catala_en` resolves `Earnings.ApplyExclusions` and `Earnings.EarningsResult` cleanly through the `> Using Earnings` directive.
- `clerk test` exits 0 with the "ALL TESTS PASSED" banner (no `#[test]` annotations declared yet; this is the dry-run path).
- The `repair_history` reports a `regenerate` action with note `"unparseable diagnostic region (no GNU records matched)"` — this is a side-effect of `clerk test` emitting a `Division_by_zero` runtime warning in a `[ERROR]` box that doesn't parse as a GNU-format diagnostic. The `_clerk_test_passed` sentinel-check correctly classifies it as a pass, so the loop converges. This matches PA3 lesson #3.

## Gating decision (U9 Step 1.5)

**GREEN.** The clerk loop converges to `status=ok` on a correctly-emitted two-module fixture. ak_doh regeneration (U9 Step 4) is unblocked once the upstream regeneration of `snap` (Step 2-3) succeeds.

## Side findings

1. **clerk_loop CLI + relative module path → broken include dir.** When the CLI is invoked with a relative `--module-path`, the loop computes `include_dirs = [module_path.parent]` as a relative path, then passes the relative path through `--include` while running the clerk subprocess with `cwd=module_path.parent`. The relative path doesn't resolve against the new cwd, so clerk reports `Ignoring included directory ...: it is not a directory or file does not exist.` followed by `No matching files found`. Always invoke with an absolute path until this is fixed.
2. **`clerk test` dry-run `Division_by_zero`.** The loop's `unparseable_region` flag fires on this benign warning. Convergence still works because `_clerk_test_passed` treats the "ALL TESTS PASSED" banner as authoritative. The `regenerate` action recorded in `repair_history` is a misleading artifact — a future clerk_loop refinement could classify dry-run runtime warnings into their own category instead of forcing `unparseable_region`.

## Reuse

This fixture is preserved for re-running U9 Step 1.5 verification before each ak_doh regeneration cycle, and for any future cross-module clerk_loop work that needs a known-good multi-module Catala source.
