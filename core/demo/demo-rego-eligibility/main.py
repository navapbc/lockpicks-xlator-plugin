"""
Xlator SNAP Eligibility Demo — FastAPI Backend

Accepts household facts from the browser form, evaluates them against the
OPA-compiled SNAP eligibility policy, and returns a structured decision.

Requires OPA REST server running on port 8181:
    opa run --server --addr :8181 $DOMAINS_DIR/snap/output/eligibility.rego

Start this server:
    uvicorn main:app --host 0.0.0.0 --port 8000
"""

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

OPA_URL = "http://localhost:8181"
OPA_DECISION_PATH = "/v1/data/snap/eligibility/decision"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Verify OPA is reachable at startup
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{OPA_URL}/health", timeout=3.0)
            resp.raise_for_status()
        log.info("OPA server reachable at %s", OPA_URL)
    except Exception as e:
        log.error(
            "WARNING: OPA server not reachable at %s: %s\n"
            "Start OPA before using the eligibility endpoint:\n"
            "    opa run --server --addr :8181 $DOMAINS_DIR/snap/output/eligibility.rego",
            OPA_URL,
            e,
        )
    yield


app = FastAPI(
    title="Xlator SNAP Eligibility Demo",
    description="Evaluates SNAP income eligibility using OPA-compiled CIVIL rules",
    version="2026Q1",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="static", html=True), name="static")


class HouseholdFacts(BaseModel):
    household_size: int = Field(..., ge=1, le=20, description="Number of SNAP household members")
    has_elderly_member: bool = Field(False, description="Any member age 60+")
    has_disabled_member: bool = Field(False, description="Any member meets SSI/SSDI disability criteria")
    gross_monthly_income: float = Field(..., ge=0, description="Total monthly income before deductions (USD)")
    earned_income: float = Field(0.0, ge=0, description="Portion from wages or self-employment (USD)")
    unearned_income: float = Field(0.0, ge=0, description="Social Security, child support, etc. (USD)")
    shelter_costs_monthly: float = Field(0.0, ge=0, description="Rent/mortgage + utilities per month (USD)")
    dependent_care_costs: float = Field(0.0, ge=0, description="Out-of-pocket dependent care costs per month (USD)")


class ComputedBreakdown(BaseModel):
    earned_income_deduction: float
    standard_deduction: float
    dependent_care_deduction: float
    income_after_prior_deductions: float
    shelter_excess: float
    is_exempt_household: bool
    shelter_deduction: float
    net_income: float
    gross_limit: float
    net_limit: float
    passes_gross_test: bool
    passes_net_test: bool


class DenialReason(BaseModel):
    code: str
    message: str
    citation: str = ""


class EligibilityResponse(BaseModel):
    eligible: bool
    denial_reasons: list[DenialReason]
    computed: ComputedBreakdown


@app.post("/api/snap/eligibility", response_model=EligibilityResponse)
async def check_eligibility(facts: HouseholdFacts):
    """
    Evaluate SNAP income eligibility for a household.

    Posts household facts to the OPA REST server, which evaluates them against
    the transpiled SNAP eligibility rules ($DOMAINS_DIR/snap/output/eligibility.rego).
    """
    payload = {"input": facts.model_dump()}

    async with httpx.AsyncClient() as client:
        try:
            opa_resp = await client.post(
                OPA_URL + OPA_DECISION_PATH,
                json=payload,
                timeout=10.0,
            )
            opa_resp.raise_for_status()
        except httpx.ConnectError:
            raise HTTPException(
                status_code=503,
                detail=f"Policy engine unavailable. Is OPA running at {OPA_URL}?",
            )
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=502,
                detail=f"OPA returned error: {e.response.status_code}",
            )
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Policy engine timed out")

    body = opa_resp.json()
    result = body.get("result")

    if result is None:
        raise HTTPException(
            status_code=422,
            detail="Policy engine returned undefined — check that all required input fields are present",
        )

    return EligibilityResponse(
        eligible=result["eligible"],
        denial_reasons=[DenialReason(**r) for r in result.get("denial_reasons", [])],
        computed=ComputedBreakdown(**result["computed"]),
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
