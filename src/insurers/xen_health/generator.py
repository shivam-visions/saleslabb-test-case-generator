"""
Xen Health smoke test-case generator.

Constants per xen_health-persona.md §1 + §2 + §11. Plans, networks, and coverage
are FIXED PER PLAN (not user-selectable axes), so combination generation
exercises:
    plan × copay × age (boundary + mid) × gender × marital × currency
    + family scenarios (2 children for the family discount)
"""

from typing import List

from .pricer import (
    compute_customer_premium,
    apply_family_discount,
)


PROVIDER_TITLE = "xen_health"     # Title in upload-tool (per persona §1)

# Each plan's fixed Network + Coverage (per persona §2).
PLAN_ATTRIBUTES = {
    "Xen Complete":  {
        "network":  "NAS General Network (GN)",
        "coverage": "Worldwide excluding USA & Canada",
        "annual_limit_aed": 1_000_000,
    },
    "Xen Elevate":   {
        "network":  "NAS Restricted Network (RN)",
        "coverage": "Worldwide excluding USA, Canada and Europe",
        "annual_limit_aed": 1_000_000,
    },
    "Xen Boost":     {
        "network":  "NAS Super Restricted Network (SRN)",
        "coverage": "Arab Countries, Middle East, South Asia and Southeast Asia Countries",
        "annual_limit_aed": 300_000,
    },
    "Xen Essential": {
        "network":  "NAS Workers Network (WN)",
        "coverage": "UAE",
        "annual_limit_aed": 250_000,
    },
}

# Per-residency display strings.
RESIDENCY_DETAILS = {
    "DB_NE": {
        "residency_str":   "UAE - Dubai",
        "nationality_str": "India",
    },
    "AbuDhabi": {
        "residency_str":   "UAE - Abu Dhabi",
        "nationality_str": "India",
    },
}

EMAIL = "qa.xen@example.com"


def _fmt_price(amount: float, currency: str) -> str:
    return f"{currency} {amount:,.2f}"


def _baseline_customer(age: int, gender_full: str, marital: str, residency_key: str) -> dict:
    g = "male" if gender_full == "M" else "female"
    return {
        "firstName": "QA",
        "lastName": f"Tester_Xen_{age}{g[0].upper()}",
        "email": EMAIL,
        "age": age,
        "day": "15",
        "month": "6",
        "gender": g,
        "maritalStatus": marital,
        "nationality": RESIDENCY_DETAILS[residency_key]["nationality_str"],
        "residency": RESIDENCY_DETAILS[residency_key]["residency_str"],
        "relation": "",
    }


def _scenario(*, name, plan, copay, age, gender, marital, currency,
              rate_data, residency_key, members=None, is_dependent_child=False):
    """Build one test case dict."""
    primary = compute_customer_premium(
        plan=plan, copay=copay, age=age, gender=gender, marital=marital,
        is_dependent_child=is_dependent_child, rate_data=rate_data, currency=currency,
    )
    total = primary["amount"]
    customers_for_family = [{"age": age, "is_child": is_dependent_child}]

    member_objs = []
    if members:
        for m in members:
            m_priced = compute_customer_premium(
                plan=plan, copay=copay, age=m["age"], gender=m["gender"],
                marital=m.get("marital", "single"),
                is_dependent_child=m.get("is_dependent_child", False),
                rate_data=rate_data, currency=currency,
            )
            total += m_priced["amount"]
            customers_for_family.append({"age": m["age"], "is_child": m.get("is_dependent_child", False)})
            member_objs.append({
                "firstName": "QA",
                "lastName": f"Tester_Xen_mem_{m['age']}",
                "email": EMAIL,
                "age": m["age"],
                "day": "15", "month": "6",
                "gender": "male" if m["gender"] == "M" else "female",
                "maritalStatus": m.get("marital", "single"),
                "relation": m["relation"],
            })

    # Apply family discount on the policy total (currency-aware)
    total, _disc = apply_family_discount(total, customers_for_family)

    attrs = PLAN_ATTRIBUTES[plan]
    return {
        "name": name,
        "customer": _baseline_customer(age, gender, marital, residency_key),
        "members": member_objs,
        "selections": {
            "Provider": PROVIDER_TITLE,
            "Plan": plan,
            "Network": attrs["network"],
            "Coverage": attrs["coverage"],
            "IP/Deductible": "",                # Xen has no IP/OP split
            "OP/Deductible": copay,             # The single copay choice goes here
            "PaymentFrequency": "Annual",
        },
        "benefits": [],
        "expected_price": _fmt_price(total, currency),
        "expected_plan_name": plan,
    }


def generate_smoke_cases(rate_data: dict, residency_key: str = "DB_NE") -> List[dict]:
    """Produce ~50 smoke cases for a single residency."""
    cases: List[dict] = []
    idx = 0

    def add(**kw):
        nonlocal idx
        idx += 1
        kw["name"] = f"{kw['name']} (TC-{idx})"
        cases.append(_scenario(rate_data=rate_data, residency_key=residency_key, **kw))

    plans = ["Xen Complete", "Xen Elevate", "Xen Boost", "Xen Essential"]
    rep_ages = [20, 35, 55]

    # Group 1: base premium per plan × representative age × gender (24 rows)
    for plan in plans:
        # Pick the lowest-copay variant for each plan (one entry for Essential)
        copays = list(rate_data["rates"][plan].keys())
        copay = copays[0]
        for age in rep_ages:
            for gender in ["M", "F"]:
                add(name=f"Base - {plan} - age {age} {gender}",
                    plan=plan, copay=copay, age=age, gender=gender,
                    marital="single", currency="AED")

    # Group 2: All copay variants for Complete + Elevate + Boost (one age, M)
    for plan in ["Xen Complete", "Xen Elevate", "Xen Boost"]:
        for copay in rate_data["rates"][plan]:
            add(name=f"Copay variant - {plan} - {copay[:50]}",
                plan=plan, copay=copay, age=35, gender="M",
                marital="single", currency="AED")

    # Group 3: Maternity loading — married female 30, each plan (4 rows)
    for plan in plans:
        copay = list(rate_data["rates"][plan].keys())[0]
        add(name=f"Maternity loading - {plan} - married F30",
            plan=plan, copay=copay, age=30, gender="F",
            marital="married", currency="AED")

    # Group 4: Family scenarios — primary + 2 children under 10 (family discount fires)
    copay_c = list(rate_data["rates"]["Xen Complete"].keys())[0]
    add(name="Family - Complete - primary + 2 kids under 10",
        plan="Xen Complete", copay=copay_c, age=35, gender="M",
        marital="married", currency="AED",
        members=[
            {"age": 7, "gender": "M", "relation": "child", "is_dependent_child": True},
            {"age": 5, "gender": "F", "relation": "child", "is_dependent_child": True},
        ])

    # Group 5: Children loading — single child under 17 (standalone)
    add(name="Children loading - Boost - standalone child 12",
        plan="Xen Boost", copay=list(rate_data["rates"]["Xen Boost"].keys())[0],
        age=12, gender="M", marital="single", currency="AED",
        is_dependent_child=True)

    # Group 6: USD currency expression (2 rows)
    add(name="Currency USD - Complete M35 single",
        plan="Xen Complete", copay=list(rate_data["rates"]["Xen Complete"].keys())[0],
        age=35, gender="M", marital="single", currency="USD")
    add(name="Currency USD - Essential M35 single",
        plan="Xen Essential", copay=list(rate_data["rates"]["Xen Essential"].keys())[0],
        age=35, gender="M", marital="single", currency="USD")

    # Group 7: Age boundaries — youngest, mid-life, oldest band
    for age, label in [(0, "infant"), (17, "teen"), (64, "senior")]:
        add(name=f"Age boundary - Complete - {label} age {age}",
            plan="Xen Complete",
            copay=list(rate_data["rates"]["Xen Complete"].keys())[0],
            age=age, gender="M", marital="single", currency="AED")

    return cases
