# civil-ruleset/

Canonical example of the **output** of `/extract-ruleset` — a CIVIL DSL ruleset built from the guidance-tier inputs.

## Files

- `canonical.civil.yaml` — the output of `extract-ruleset`, a working CIVIL ruleset for the `medicaid_income_eligibility` module.

## Input bundle (lives in other corpus folders, not copied here)

`extract-ruleset` consumes seven input artifacts. They are NOT duplicated into this folder; the source-of-truth canonicals live under their own file-type subfolders:

- [../naming-manifest/canonical.yaml](../naming-manifest/canonical.yaml)
- [../prompt-context/canonical.yaml](../prompt-context/canonical.yaml)
- [../ruleset-modules/canonical.yaml](../ruleset-modules/canonical.yaml)
- [../sample-artifacts/canonical.yaml](../sample-artifacts/canonical.yaml)
- [../output-variables/canonical.yaml](../output-variables/canonical.yaml)
- [../input-variables/canonical.yaml](../input-variables/canonical.yaml)
- [../constants-and-tables/canonical.yaml](../constants-and-tables/canonical.yaml)

The `extract-ruleset/SKILL.md` required-read step reads all eight files (7 inputs + this canonical output) so the AI sees the full input→output chain. Co-locating copies of the 7 inputs in this folder would create a silent-drift surface when those source canonicals are edited later; enumerating by path keeps a single source of truth.
