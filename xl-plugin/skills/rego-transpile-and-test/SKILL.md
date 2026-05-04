---
name: rego-transpile-and-test
description: Transpile and Test for Rego
---

# Transpile and Test for Rego

Transpile a CIVIL module to Rego and run the test suite.

## Input

```
/transpile-and-test [<domain>]                 # auto-detect program or prompt if ambiguous
/transpile-and-test [<domain> <program>]       # target a specific program
```

If `<domain>` is not provided, run `xlator list` and prompt the user to choose.

Read `../../core/output-fencing.md` now.

## Pre-flight

1. **Determine module:**
   Find `*.civil.yaml` files in `$DOMAINS_DIR/<domain>/specs/`:
   - Exactly one found and `<program>` not specified → use it automatically.
   - Multiple found and `<program>` not specified → ask which program to transpile.
   - None found → Print
   :::error
   No CIVIL spec found in `$DOMAINS_DIR/<domain>/specs/`
   :::
   Stop.

2. **Run pre-flight check:**
   ```bash
   xlator preflight <domain> <module> --backend rego
   ```
   If exit code != 0: show the error in an `:::error` fence and stop. Do not proceed.

## Execution

```bash
xlator rego-pipeline <domain> <program>
```

Open a `:::detail` fence. Relay output verbatim. No summary formatting. Close the `:::` fence when relay completes.

**On failure:** Show the failing case ID(s) and actual vs. expected output. In a `:::user_input` fence, ask the user to diagnose:

- **Rule error** — the CIVIL `when:` expression is wrong → fix in the CIVIL file and re-run `/extract-ruleset <domain>`
- **Test expectation error** — the test case has wrong expected values → fix in the tests file and re-run `/rego-transpile-and-test <domain>`
- **Transpiler bug** — the Rego generation is incorrect → file a transpiler issue; do not modify CIVIL or tests
