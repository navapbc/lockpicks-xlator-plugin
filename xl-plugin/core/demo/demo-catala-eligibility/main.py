from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from python.Eligibility import (
    ClientData, DOLRecord, EligibilityDecisionIn, eligibility_decision,
    HouseholdType, HouseholdType_Code,
)
from python.eligibility_meta import SCOPE_METADATA
from catala_runtime import money_of_units_int, integer_of_int, Unit, money_to_float

app = FastAPI(
    title="Xlator AK DOH Eligibility Demo",
    description="Evaluates Alaska Medicaid income eligibility using Catala-compiled Python rules",
)
app.mount("/static", StaticFiles(directory="static"), name="static")

ELIGIBLE_MAP = {
    "Approve": "approve",
    "Deny": "deny",
    "ManualVerification": "manual_verification",
}


# ---------------------------------------------------------------------------
# Input model
# ---------------------------------------------------------------------------

class InputFacts(BaseModel):
    # ClientData fields
    client_gross_earned_income: float = Field(..., ge=0, description="Client-stated gross monthly earned income before any exclusions")
    client_federal_exclusions: float = Field(0.0, ge=0, description="Total income excluded by federal law applicable to earned income")
    client_eitc_exclusion: float = Field(0.0, ge=0, description="EITC advance or Child Tax Credit payment received this month")
    client_irregular_earned_income: float = Field(0.0, ge=0, description="Infrequent or irregular earned income this month (subject to $10/month exclusion)")
    client_student_earned_income: float = Field(0.0, ge=0, description="Earned income of a blind or disabled student under age 22 attending school regularly")
    client_student_monthly_limit: float = Field(0.0, ge=0, description="Monthly maximum for student earned income exclusion (e.g. $2,410 for 2026)")
    client_non_needs_based_unearned_remainder: float = Field(0.0, ge=0, description="Unused portion of the $20/month general income exclusion after applying to non-needs-based unearned income")
    client_impairment_work_expenses: float = Field(0.0, ge=0, description="Verified impairment-related work expenses for a disabled individual")
    client_blind_work_expenses: float = Field(0.0, ge=0, description="Verified work expenses of a blind individual")
    client_self_support_exclusion: float = Field(0.0, ge=0, description="Earned income used to fulfill an SSA- or DVR-approved plan for achieving self-support")
    client_household_type: str = Field(..., description="Household type code used to look up the applicable income standard (e.g. A1E, B1E, H1E, A2S, NHR)")
    client_benefit_year: int = Field(..., description="Calendar year for which income limits apply (e.g. 2026)")
    # DOLRecord fields
    dol_earned_income_available: bool = Field(False, description="True when DOL electronic wage data is available for this client")
    dol_quarterly_earnings: float = Field(0.0, ge=0, description="Most recent quarter of earnings reported by DOL and Workforce Development")
    dol_federal_exclusions: float = Field(0.0, ge=0, description="Same as client_federal_exclusions — identical exclusion inputs used for DOL comparison run")
    dol_eitc_exclusion: float = Field(0.0, ge=0, description="Same as client_eitc_exclusion for DOL comparison run")
    dol_irregular_earned_income: float = Field(0.0, ge=0, description="Same as client_irregular_earned_income for DOL comparison run")
    dol_student_earned_income: float = Field(0.0, ge=0, description="Same as client_student_earned_income for DOL comparison run")
    dol_student_monthly_limit: float = Field(0.0, ge=0, description="Same as client_student_monthly_limit for DOL comparison run")
    dol_non_needs_based_unearned_remainder: float = Field(0.0, ge=0, description="Same as client_non_needs_based_unearned_remainder for DOL comparison run")
    dol_impairment_work_expenses: float = Field(0.0, ge=0, description="Same as client_impairment_work_expenses for DOL comparison run")
    dol_blind_work_expenses: float = Field(0.0, ge=0, description="Same as client_blind_work_expenses for DOL comparison run")
    dol_self_support_exclusion: float = Field(0.0, ge=0, description="Same as client_self_support_exclusion for DOL comparison run")


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------

class DenialReason(BaseModel):
    code: str
    message: str


class ExclusionChainSteps(BaseModel):
    after_65: float = Field(description="[subscope_output] Income remaining after $65 earned income deduction")
    after_irwe: float = Field(description="[subscope_output] Income remaining after impairment-related work expense deduction")
    after_half: float = Field(description="[subscope_output] Income remaining after ½ remaining income exclusion")
    after_blind: float = Field(description="[subscope_output] Income remaining after blind work expense deduction")
    adjusted_earned_income: float = Field(description="[subscope_output] Final adjusted earned income after all exclusions")


class ComputedBreakdown(BaseModel):
    dol_avg_monthly_income: float = Field(description="[computed_output] DOL quarterly earnings divided by 3 (average monthly)")
    is_compatible: bool = Field(description="[computed_output] True when DOL-adjusted income is within 10% of client-adjusted income")
    income_limit: float = Field(description="[computed_output] Applicable income standard for this household type and benefit year")


class EligibilityResponse(BaseModel):
    eligible: str
    reasons: list[DenialReason]
    breakdown: ComputedBreakdown
    client_chain: ExclusionChainSteps
    dol_chain: ExclusionChainSteps
    field_categories: dict[str, str]


# ---------------------------------------------------------------------------
# API route
# ---------------------------------------------------------------------------

@app.post("/api/ak_doh/eligibility", response_model=EligibilityResponse)
async def check(facts: InputFacts):
    try:
        dol_monthly = facts.dol_quarterly_earnings / 3.0

        client_data = ClientData(
            gross_earned_income=money_of_units_int(int(round(facts.client_gross_earned_income))),
            federal_exclusions=money_of_units_int(int(round(facts.client_federal_exclusions))),
            eitc_exclusion=money_of_units_int(int(round(facts.client_eitc_exclusion))),
            irregular_earned_income=money_of_units_int(int(round(facts.client_irregular_earned_income))),
            student_earned_income=money_of_units_int(int(round(facts.client_student_earned_income))),
            student_monthly_limit=money_of_units_int(int(round(facts.client_student_monthly_limit))),
            non_needs_based_unearned_remainder=money_of_units_int(int(round(facts.client_non_needs_based_unearned_remainder))),
            impairment_work_expenses=money_of_units_int(int(round(facts.client_impairment_work_expenses))),
            blind_work_expenses=money_of_units_int(int(round(facts.client_blind_work_expenses))),
            self_support_exclusion=money_of_units_int(int(round(facts.client_self_support_exclusion))),
            household_type=HouseholdType(HouseholdType_Code[facts.client_household_type], Unit()),
            benefit_year=integer_of_int(int(facts.client_benefit_year)),
        )
        dol_record = DOLRecord(
            dol_earned_income_available=facts.dol_earned_income_available,
            dol_quarterly_earnings=money_of_units_int(int(round(facts.dol_quarterly_earnings))),
            gross_earned_income=money_of_units_int(int(round(dol_monthly))),
            federal_exclusions=money_of_units_int(int(round(facts.dol_federal_exclusions))),
            eitc_exclusion=money_of_units_int(int(round(facts.dol_eitc_exclusion))),
            irregular_earned_income=money_of_units_int(int(round(facts.dol_irregular_earned_income))),
            student_earned_income=money_of_units_int(int(round(facts.dol_student_earned_income))),
            student_monthly_limit=money_of_units_int(int(round(facts.dol_student_monthly_limit))),
            non_needs_based_unearned_remainder=money_of_units_int(int(round(facts.dol_non_needs_based_unearned_remainder))),
            impairment_work_expenses=money_of_units_int(int(round(facts.dol_impairment_work_expenses))),
            blind_work_expenses=money_of_units_int(int(round(facts.dol_blind_work_expenses))),
            self_support_exclusion=money_of_units_int(int(round(facts.dol_self_support_exclusion))),
        )

        inp = EligibilityDecisionIn(client_data_in=client_data, d_o_l_record_in=dol_record)
        result = eligibility_decision(inp)

        eligible_str = ELIGIBLE_MAP.get(result.eligible.code.name, result.eligible.code.name)

        return EligibilityResponse(
            eligible=eligible_str,
            reasons=[DenialReason(code=str(r.code.name), message=str(r.code.name)) for r in result.reasons],
            breakdown=ComputedBreakdown(
                dol_avg_monthly_income=money_to_float(result.dol_avg_monthly_income),
                is_compatible=result.is_compatible,
                income_limit=money_to_float(result.income_limit),
            ),
            client_chain=ExclusionChainSteps(
                after_65=money_to_float(result.client_result.after_65),
                after_irwe=money_to_float(result.client_result.after_irwe),
                after_half=money_to_float(result.client_result.after_half),
                after_blind=money_to_float(result.client_result.after_blind),
                adjusted_earned_income=money_to_float(result.client_result.adjusted_earned_income),
            ),
            dol_chain=ExclusionChainSteps(
                after_65=money_to_float(result.dol_result.after_65),
                after_irwe=money_to_float(result.dol_result.after_irwe),
                after_half=money_to_float(result.dol_result.after_half),
                after_blind=money_to_float(result.dol_result.after_blind),
                adjusted_earned_income=money_to_float(result.dol_result.adjusted_earned_income),
            ),
            field_categories=SCOPE_METADATA,
        )
    except KeyError as e:
        raise HTTPException(status_code=422, detail=f"Invalid household_type value: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
