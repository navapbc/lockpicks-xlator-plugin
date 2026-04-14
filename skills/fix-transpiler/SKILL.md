# Fix Transpiler Bug

1. Run full test suite: `${CLAUDE_PLUGIN_ROOT}/xlator catala-pipeline <domain> <program>` to identify all failures
2. Group failures by root cause (don't fix symptoms individually)
3. Fix the root cause in transpiler source code
4. Re-run ALL tests across ALL domains — not just the failing ones
5. If tests pass, update transpiler docs with the fix pattern
6. Use Catala semantics only — never Rego conventions
7. Watch for: money literal commas, entity prefixes, bare field names, module Include vs Using
