# eligibility_meta.py  (transpiler-generated — do not edit)
# Field categories for EligibilityDecision scope.
# "decision"        — primary outcome fields (decisions: section)
# "computed_output" — intermediate values tagged output: (computed: section)
# "subscope_output" — invoke: computed fields that are subscope references

SCOPE_METADATA: dict[str, str] = {
    # Subscope outputs
    "client_result": "subscope_output",
    "dol_result": "subscope_output",
    # Computed outputs
    "dol_avg_monthly_income": "computed_output",
    "is_compatible": "computed_output",
    "income_limit": "computed_output",
    # Decisions
    "eligible": "decision",
    "reasons": "decision",
}

DECISION_FIELDS     = [k for k, v in SCOPE_METADATA.items() if v == "decision"]
COMPUTED_OUT_FIELDS = [k for k, v in SCOPE_METADATA.items() if v == "computed_output"]
SUBSCOPE_FIELDS     = [k for k, v in SCOPE_METADATA.items() if v == "subscope_output"]
