# Synthetic two-module policy (U9 Step 1.5 fixture)

A fake policy used by the two-module gate (`docs/plans/2026-05-28-001-refactor-replace-civil-with-catala-plan.md`, U9 Step 1.5). The shape mirrors `ak_doh`'s two-module structure: an `Earnings` sub-module that exports a struct used by the parent `Eligibility` module via `> Using Earnings`.

## Section 1. Earnings exclusions

For each applicant, exclude up to $65 of earned income per month plus one-half of the remainder. This produces an `excluded_amount` content money and a `remaining_earned_income` content money for downstream use.

## Section 2. Income threshold

A household is income-eligible when its post-exclusion earned income is below the household-size-specific limit. Household-size-1 limit is $943; each additional member adds $360.

## Section 3. Decision

The applicant is eligible when income_eligible is fulfilled.
