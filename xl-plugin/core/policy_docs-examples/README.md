# Example Policy Documents for Testing

A fictional regulatory policy docs. Not based on any real jurisdiction.

These policy docs were created to provide testing policy docs that:

- Is **intuitive** — no specialized policy knowledge required to judge whether the extracted ruleset is correct.
- Is **small** to minimize token usage and speed up testing
- Implies **multiple stages** within each document (classify → score → decide).
- Implies **multiple modules** across documents.
- Has **one module reused across both inputs**, so the extractor's cross-document module-detection behavior can be exercised on a clean, deliberate signal.

To use them, run `/new-domain <some_name>`, copy the `*.md` files to the `<domain>/input/policy_docs/` folder, and continue the Xlator process (refer to the "Skill workflow" section in `xl-plugin/CLAUDE.md`).

## drivers license (`dl`)

Two policy documents in [dl/](dl/):

| File | Stages |
|------|--------|
| [traffic_violation_code.md](dl/traffic_violation_code.md) | classify violation → assign demerit points → compute fine (with school-zone multiplier and repeat-violation surcharge) |
| [license_suspension_policy.md](dl/license_suspension_policy.md) | identify recent violations → apply recency weight → check severity escalations → assign action tier |

### Intended workflow

The two documents form a two-stage pipeline operating at different scopes:

1. **Per-violation (described in `traffic_violation_code.md`).** When a single violation is adjudicated, classify it into a Severity Class (Section 300), assign Demerit Points from that class (Section 400), and compute the final fine with school-zone multiplier and repeat-violation surcharge (Section 500). The per-violation outputs are: Severity Class, Demerit Points, and Final Fine.

2. **Per-driver assessment (described in `license_suspension_policy.md`).** At assessment time, gather the driver's Recent Violations (≤24 months) along with each violation's Severity Class and Demerit Points — both *carried over* from the Traffic Violation Code rather than redefined. Apply the recency weight to each violation's points (Section 300), sum to a Weighted Point Total, check Section 310 escalations on Severity Class C/D counts, and assign an Action Tier (Section 400).

The cross-document handoff is the per-violation record `(severity_class, demerit_points, adjudication_date)`: produced once per incident by the Traffic Violation Code, consumed many-to-one by the License Suspension Policy when assessing a driver. This is why `severity_classification` and `demerit_points` are expected to surface as shared modules — they are computed by the first document and read by the second.

### Intended module structure

Note that while these are the intended modules, an AI may decide on a different module decomposition, depending on the input and output of the desired target ruleset. The AI is likely to use different module names unless the names are provided when requested.

The two documents are written to imply the following module decomposition:

- **`severity_classification`** — **reused across both documents.** Defined in `traffic_violation_code.md` Section 300; explicitly incorporated by reference in `license_suspension_policy.md` Section 200 ("treats Severity Class as authoritative and does not redefine it").
- **`demerit_points`** — defined in `traffic_violation_code.md` Section 400; referenced by `license_suspension_policy.md` for weighted accumulation.
- **`fine_schedule`** — `traffic_violation_code.md` only.
- **`recency_weighting`** / **`point_accumulation`** — `license_suspension_policy.md` only.
- **`action_tier_assignment`** — `license_suspension_policy.md` only.

If the extractor identifies `severity_classification` as a single shared module (not duplicated per document), it has correctly handled the cross-document reuse signal.
