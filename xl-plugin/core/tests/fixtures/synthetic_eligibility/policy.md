# Synthetic Eligibility Program — Spike Policy

This is a small synthetic policy document used to exercise the AI's ability to
emit typechecking Catala directly.

## Section 1. Definitions

For the purposes of this program:

- "Household monthly gross income" means the total monthly earned and unearned
  income of all household members, measured in U.S. dollars.
- "Household size" means the number of persons in the household.
- "Federal poverty line for the household size" means the official FPL amount
  for the household size, measured in U.S. dollars per month.

## Section 2. Income eligibility threshold

A household is income-eligible for this program if its household monthly gross
income is less than 200 percent of the federal poverty line for the household
size.

## Section 3. Categorical eligibility for elderly households

A household is also eligible if it contains at least one member who is age 65
or older, regardless of income.

## Section 4. Disqualification — assets exceeding limit

A household is disqualified, regardless of income or elderly composition, if
its total countable resources exceed $5,000.

## Section 5. Final eligibility decision

A household is eligible for this program if and only if it satisfies the
income test (Section 2) OR the categorical-elderly test (Section 3), AND it
is NOT disqualified by the resource test (Section 4).
