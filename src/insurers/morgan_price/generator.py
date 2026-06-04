"""
Smoke combination generator for Morgan Price Flexible Choices.

Produces a list of TestCase dicts ready for the CSV writer. Each TestCase
covers ONE axis variation against a baseline customer profile, so a failure
points directly at which axis is broken (rates / deductible math / network
cost / payment-frequency surcharge / etc.).

Target: ~50 rows for smoke. Regression and full tiers are scaffolded as TODOs
and will land in Phase 1.
"""

from typing import Dict, List

from .pricer import compute_premium


PROVIDER_TITLE = "Morgan Price (Flexible Choices)"
PLAN_TITLE     = "Flexible Choices"

# Each Area's display label as it appears in the upload-tool's Coverage modifier.
AREA_TO_COVERAGE_LABEL = {
    "Area 1": "Bangladesh, Brunei, Cambodia, East Timor, India, Indonesia, Laos, Malaysia, Myanmar, Pakistan, Papua New Guinea, Philippines, Sri Lanka, Vietnam",
    "Area 2": "Worldwide excluding USA, China, Hong Kong, Singapore",
    "Area 3": "Worldwide excluding USA",
}

# A representative residency per Area (must fall within the Area's incl-list).
AREA_TO_RESIDENCY = {
    "Area 1": "India",
    "Area 2": "Thailand",
    "Area 3": "France",
}

# Fixed test fixtures (mirrors the convention in the existing Allianz CSV).
EMAIL = "qa.morgan@example.com"

# Benefit modifier titles (must match upload-tool exactly for Playwright join).
OP_MODIFIER_TITLE = "Out-patient Consultations"
DENTAL_MODIFIER_TITLE = "Dental"
MATERNITY_MODIFIER_TITLE = "Maternity (Consultations, Scans and Delivery)"
ANNUAL_LIMIT_MODIFIER_TITLE = "Annual Limit"

# Option labels emitted by the upload-tool bundle.
OP_OPTION_LABELS = {
    "OP Module 1": ("OP Module 1", "Covered in full within OP limit of USD 2,500 with 10% co-pay"),
    "OP Module 2": ("OP Module 2", "Covered in full within OP limit of USD 6,000 with 10% co-pay"),
}
DENTAL_OPTION_LABEL = ("Routine Dental covered up to USD 500", "Routine Dental covered up to USD 500")
MATERNITY_OPTION_LABEL = ("Covered in full up to USD 5,000", "Maternity (IP & OP): Covered in full up to USD 5,000")
AL_OPTION_LABELS = {
    "USD 250,000":   ("USD 250,000", "Reduced annual limit (-7.5% via Reduce SI)"),
    "USD 1,000,000": ("USD 1,000,000", "Increased annual limit (via Increase SI)"),
}


def _fmt_price(amount: float) -> str:
    """Format a number as 'USD 1,234.56' to match the expected CSV format."""
    return f"USD {amount:,.2f}"


def _baseline_customer(area: str, age: int, gender: str = "male", marital: str = "single") -> dict:
    """A minimal customer dict for one test case. Day/month are fixed."""
    return {
        "firstName": "QA",
        "lastName":  f"Tester_{area.replace(' ', '')}_{age}{gender[0]}",
        "email":     EMAIL,
        "age":       age,
        "day":       "15",
        "month":     "6",
        "gender":    gender,
        "maritalStatus": marital,
        "nationality":   AREA_TO_RESIDENCY[area],
        "residency":     AREA_TO_RESIDENCY[area],
        "relation":  "",
    }


def _compute(area, age, gender="male", *,
             ip_deductible="NIL per year deductible",
             op_copay="10% per visit copay",
             op_module="(Optional Benefit Available)",
             dental=False,
             maternity=False,
             annual_limit="USD 500,000",
             network="Enhanced Network",
             payment_frequency="Annual",
             rate_table, benefit_data) -> float:
    return compute_premium(
        area=area, age=age, gender=gender,
        ip_deductible=ip_deductible, op_copay=op_copay, op_module=op_module,
        dental_selected=dental, maternity_selected=maternity,
        annual_limit=annual_limit, network=network, payment_frequency=payment_frequency,
        rate_table=rate_table, benefit_data=benefit_data,
    )["annual"]


def generate_smoke_cases(rate_table: dict, benefit_data: dict) -> List[dict]:
    """
    Produce the smoke tier (~50 rows). Each entry is:
        {
            "name": "...",
            "customer": {...},
            "selections": {
                "Provider": ..., "Plan": ..., "Network": ...,
                "Coverage": ..., "IP/Deductible": ..., "OP/Deductible": ...,
                "PaymentFrequency": ...,
            },
            "benefits": [{"title": ..., "label": ..., "description": ...}, ...],
            "expected_price": "USD 1,234.56",
            "expected_plan_name": "Flexible Choices",
        }
    """
    cases: List[dict] = []
    case_idx = 0

    def add_case(name, area, age, *, gender="male", marital="single",
                 ip_deductible="NIL per year deductible",
                 op_copay="10% per visit copay",
                 op_module="(Optional Benefit Available)",
                 dental=False, maternity=False,
                 annual_limit="USD 500,000",
                 network="Enhanced Network",
                 payment_frequency="Annual",
                 benefits=None):
        nonlocal case_idx
        case_idx += 1
        price = _compute(
            area, age, gender,
            ip_deductible=ip_deductible, op_copay=op_copay, op_module=op_module,
            dental=dental, maternity=maternity, annual_limit=annual_limit,
            network=network, payment_frequency=payment_frequency,
            rate_table=rate_table, benefit_data=benefit_data,
        )
        cases.append({
            "name": f"{name} (TC-{case_idx})",
            "customer": _baseline_customer(area, age, gender, marital),
            "selections": {
                "Provider": PROVIDER_TITLE,
                "Plan":     PLAN_TITLE,
                "Network":  network,
                "Coverage": AREA_TO_COVERAGE_LABEL[area],
                "IP/Deductible": ip_deductible,
                "OP/Deductible": op_copay,
                "PaymentFrequency": payment_frequency,
            },
            "benefits": benefits or [],
            "expected_price": _fmt_price(price),
            "expected_plan_name": PLAN_TITLE,
        })

    # ── Group 1: Base premium per Area × representative age × gender (18 rows) ──
    representative_ages = [20, 35, 55]
    for area in ["Area 1", "Area 2", "Area 3"]:
        for age in representative_ages:
            for gender in ["male", "female"]:
                add_case(
                    f"Base premium - {area} - age {age} {gender}",
                    area, age, gender=gender,
                )

    # ── Group 2: OP Module 1 and 2 per Area (6 rows) ──
    for area in ["Area 1", "Area 2", "Area 3"]:
        for op_mod_key in ["OP Module 1", "OP Module 2"]:
            label, desc = OP_OPTION_LABELS[op_mod_key]
            add_case(
                f"OP {op_mod_key} - {area} - age 35 male",
                area, 35, op_module=op_mod_key,
                benefits=[{"title": OP_MODIFIER_TITLE, "label": label, "description": desc}],
            )

    # ── Group 3: Dental per Area (3 rows) ──
    for area in ["Area 1", "Area 2", "Area 3"]:
        label, desc = DENTAL_OPTION_LABEL
        add_case(
            f"Dental - {area} - age 35 male",
            area, 35, dental=True,
            benefits=[{"title": DENTAL_MODIFIER_TITLE, "label": label, "description": desc}],
        )

    # ── Group 4: Maternity (married female 30, requires OP Module 2) per Area (3 rows) ──
    op_lbl, op_desc = OP_OPTION_LABELS["OP Module 2"]
    mat_lbl, mat_desc = MATERNITY_OPTION_LABEL
    for area in ["Area 1", "Area 2", "Area 3"]:
        add_case(
            f"Maternity - {area} - married F30 with OP Module 2",
            area, 30, gender="female", marital="married",
            op_module="OP Module 2", maternity=True,
            benefits=[
                {"title": OP_MODIFIER_TITLE,        "label": op_lbl,  "description": op_desc},
                {"title": MATERNITY_MODIFIER_TITLE, "label": mat_lbl, "description": mat_desc},
            ],
        )

    # ── Group 5: All 7 IP deductibles (1 area × 1 age, 7 rows) ──
    for ded in benefit_data["ip_deductibles"]:
        add_case(
            f"IP Deductible - {ded} - Area 1 - age 35 male",
            "Area 1", 35, ip_deductible=ded,
        )

    # ── Group 6: All 4 payment frequencies (1 area × 1 age, 4 rows) ──
    for freq in ["Annual", "Semi-annual", "Quarterly", "Monthly"]:
        add_case(
            f"Payment frequency - {freq} - Area 1 - age 35 male",
            "Area 1", 35, payment_frequency=freq,
        )

    # ── Group 7: Annual Limit tiers (2 non-default rows; default already exercised) ──
    for al in ["USD 250,000", "USD 1,000,000"]:
        lbl, desc = AL_OPTION_LABELS[al]
        add_case(
            f"Annual Limit - {al} - Area 1 - age 35 male",
            "Area 1", 35, annual_limit=al,
            benefits=[{"title": ANNUAL_LIMIT_MODIFIER_TITLE, "label": lbl, "description": desc}],
        )

    # ── Group 8: Network swap (Standard Network on Area 1 + Area 3) ──
    for area in ["Area 1", "Area 3"]:
        add_case(
            f"Standard Network - {area} - age 35 male",
            area, 35, network="Standard Network",
        )

    # ── Group 9: One cross-axis combined case per Area — exercises everything ──
    op_lbl, op_desc = OP_OPTION_LABELS["OP Module 1"]
    dental_lbl, dental_desc = DENTAL_OPTION_LABEL
    for area in ["Area 1", "Area 2", "Area 3"]:
        add_case(
            f"Combined - {area} - OP1 + Dental + 250K + USD1000ded + Monthly",
            area, 40, op_module="OP Module 1", dental=True,
            ip_deductible="USD 1,000 per year deductible",
            annual_limit="USD 250,000",
            payment_frequency="Monthly",
            benefits=[
                {"title": OP_MODIFIER_TITLE,    "label": op_lbl,     "description": op_desc},
                {"title": DENTAL_MODIFIER_TITLE,"label": dental_lbl, "description": dental_desc},
            ],
        )

    return cases


# Regression and full tier generators — Phase 1 work.
def generate_regression_cases(rate_table, benefit_data) -> List[dict]:
    """TODO: pairwise on lower-priority axes, ~300 rows."""
    raise NotImplementedError("Regression tier scheduled for Phase 1")


def generate_full_cases(rate_table, benefit_data) -> List[dict]:
    """TODO: exhaustive on high-priority axes, pairwise elsewhere, ~1500 rows."""
    raise NotImplementedError("Full tier scheduled for Phase 1")
