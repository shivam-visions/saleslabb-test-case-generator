"""
BUPA DAC Monaco/France smoke test-case generator.

Per the persona (auto-onboarding/PROD-1985-.../CLAUDE.md):
- 5 set plans, no addons
- Each plan has specific coverage areas + deductible combos
- 3 currencies (USD primary, GBP/EUR alternates)
- 3 frequencies (Annual/Quarterly/Monthly)
- 2 residencies (FR, MC)
- Single network: "No network restriction"

Smoke scope (~45 cases):
- All 5 plans × representative coverage × representative deductible × 3 ages = ~20
- All allowed coverages per plan with NIL/NIL deductible = ~12
- IP+OP combo variations on Select = ~4
- Frequency variations on Premier = ~2
- Currency variations on Elite = ~2
- MC residency on a few = ~5
"""

from typing import List


PROVIDER_TITLE = "BUPA DAC (Global Health)"
NETWORK = "No network restriction"
EMAIL = "qa.bupa@example.com"

# Map plan → allowed coverages (per persona §"Coverage areas per plan")
PLAN_COVERAGES = {
    "Major Medical": ["Worldwide", "Worldwide excluding USA"],
    "Select":        ["Worldwide", "Worldwide excluding USA", "Europe (Including UK)"],
    "Premier":       ["Worldwide", "Worldwide excluding USA", "Europe (Including UK)"],
    "Elite":         ["Worldwide", "Worldwide excluding USA", "Europe (Including UK)"],
    "Ultimate":      ["Worldwide", "Worldwide excluding USA"],
}

# Map plan → allowed IP deductibles (USD canonical) — from persona deductible matrix
PLAN_IP_DEDUCTIBLES = {
    "Major Medical": [0, 850, 1700, 3400, 8500],
    "Select":        [0, 850, 1700, 3400, 8500],
    "Premier":       [0, 1700, 3400, 8500, 12750],
    "Elite":         [0, 3400, 8500, 12750, 22550],
    "Ultimate":      [0],
}

# IP+OP plans: OP deductible levels (USD canonical)
PLAN_OP_DEDUCTIBLES = {
    "Major Medical": None,            # IP-only — no OP deductible
    "Select":        [0, 425, 850, 1700],
    "Premier":       [0, 425, 850, 1700],
    "Elite":         [0, 425, 850, 1700],
    "Ultimate":      [0],
}

# Auto-applied New Business discount per (plan, coverage).
# Source: PROD-1985 IPRB §"Applicable Discounts" + persona §"Discount modifier" §7–§16.
# Format: discount fraction subtracted from raw PDF rate at quote time.
# IMPORTANT: PROD-1950 promo (Apr 15 – Jun 30 2026) may stack additionally — not encoded here.
# The pilot CSV uses NB Worldwide / Europe-WWX-USA only; mismatches with API will
# reveal whether the promo also applied at runtime.
NB_DISCOUNT_PCT = {
    "Major Medical": {"Worldwide": 0.15,  "Worldwide excluding USA": 0.15,  "Europe (Including UK)": 0.15},
    "Select":        {"Worldwide": 0.30,  "Worldwide excluding USA": 0.30,  "Europe (Including UK)": 0.30},
    "Premier":       {"Worldwide": 0.30,  "Worldwide excluding USA": 0.37,  "Europe (Including UK)": 0.37},
    "Elite":         {"Worldwide": 0.30,  "Worldwide excluding USA": 0.405, "Europe (Including UK)": 0.405},
    "Ultimate":      {"Worldwide": 0.51,  "Worldwide excluding USA": 0.51},
}


def _apply_nb_discount(rate: float, plan: str, coverage: str) -> float:
    pct = NB_DISCOUNT_PCT.get(plan, {}).get(coverage, 0)
    return round(rate * (1 - pct), 2)


def _copay_ip_label(usd: int) -> str:
    return "NIL per year deductible" if usd == 0 else f"USD {usd:,} per year deductible"


def _copay_op_label(usd: int) -> str:
    return "NIL per visit deductible" if usd == 0 else f"USD {usd:,} per visit deductible"


def _residency_country(residency_code: str) -> str:
    return {"FR": "France", "MC": "Monaco"}[residency_code]


def _fmt_price(amount: float, currency: str) -> str:
    return f"{currency} {amount:,.2f}"


def _baseline_customer(age: int, gender: str, residency_code: str) -> dict:
    country = _residency_country(residency_code)
    return {
        "firstName": "QA",
        "lastName":  f"Tester_BUPA_{age}{gender[0].upper()}_{residency_code}",
        "email": EMAIL,
        "age": age,
        "day": "15", "month": "6",
        "gender": gender,
        "maritalStatus": "single",
        "nationality": "United Kingdom",
        "residency": country,
        "relation": "",
    }


def _lookup_rate(rate_data: dict, *, residency: str, plan: str, coverage: str,
                 age: int, ip_usd: int, op_usd, frequency: str, currency: str,
                 apply_nb_discount: bool = True) -> float | None:
    """Look up the raw PDF rate by composite key, optionally apply NB discount.

    Per the PROD-1985 IPRB + persona §"Discount modifier" §7-§16, new business
    quotes auto-apply a per-plan, per-coverage discount on the raw PDF rate.
    The Playwright runner sends businessType="New Business" (hardcoded in
    quote/helpers.ts), so for our test CSV we must encode the discount-adjusted
    value as the expected price.

    The PROD-1950 promo (Apr 15 – Jun 30 2026) may additionally apply on top of
    this for non-Ultimate plans. If running during that window, the actual API
    response may further reduce the price by the promo factor. We do NOT encode
    the promo here; mismatches between this expected and API actual that show a
    32% / 44% additional reduction indicate the promo also fired at runtime.
    """
    key = (residency, plan, coverage, age, ip_usd, op_usd, frequency)
    entry = rate_data["rates"].get(key)
    if entry is None:
        return None
    raw = entry.get(currency)
    if raw is None or not apply_nb_discount:
        return raw
    return _apply_nb_discount(raw, plan, coverage)


def generate_smoke_cases(rate_data: dict, residency: str = "FR") -> List[dict]:
    """Generate ~45 smoke cases."""
    cases: List[dict] = []
    idx = 0

    def add(name: str, *, plan: str, coverage: str, ip_usd: int, op_usd,
            age: int, gender: str = "male", frequency: str = "Annually",
            currency: str = "USD", residency_override: str | None = None):
        nonlocal idx
        res = residency_override or residency
        rate = _lookup_rate(
            rate_data,
            residency=res, plan=plan, coverage=coverage,
            age=age, ip_usd=ip_usd, op_usd=op_usd,
            frequency=frequency, currency=currency,
        )
        if rate is None:
            print(f"⚠ Skipping case (no rate found): {plan}/{coverage}/age{age}/"
                  f"IP{ip_usd}/OP{op_usd}/{frequency}/{currency}/{res}")
            return
        idx += 1
        # Map our internal frequency keys to the Playwright CSV's PaymentFrequency values
        freq_label = {"Annually": "Annual", "month": "Monthly", "quarter": "Quarterly"}[frequency]
        cases.append({
            "name": f"{name} (TC-{idx})",
            "customer": _baseline_customer(age, gender, res),
            "selections": {
                "Provider": PROVIDER_TITLE,
                "Plan": plan,
                "Network": NETWORK,
                "Coverage": coverage,
                "IP/Deductible": _copay_ip_label(ip_usd),
                "OP/Deductible": _copay_op_label(op_usd) if op_usd is not None else "",
                "PaymentFrequency": freq_label,
            },
            "benefits": [],
            "expected_price": _fmt_price(rate, currency),
            "expected_plan_name": plan,
        })

    # ── Group 1: One representative case per plan × representative coverage × NIL deductible
    for plan in ["Major Medical", "Select", "Premier", "Elite", "Ultimate"]:
        cov = PLAN_COVERAGES[plan][1]  # "Worldwide excluding USA" — common middle option
        op = 0 if PLAN_OP_DEDUCTIBLES[plan] is not None else None
        add(f"Base - {plan} - {cov} - NIL - age 35", plan=plan, coverage=cov,
            ip_usd=0, op_usd=op, age=35)

    # ── Group 2: All coverages per plan × NIL/NIL × age 35
    for plan, coverages in PLAN_COVERAGES.items():
        for cov in coverages:
            op = 0 if PLAN_OP_DEDUCTIBLES[plan] is not None else None
            add(f"Coverage - {plan} - {cov} - NIL - age 35",
                plan=plan, coverage=cov, ip_usd=0, op_usd=op, age=35)

    # ── Group 3: Age boundaries for Major Medical (IP-only)
    for age in [0, 17, 18, 30, 50, 65, 75]:
        add(f"Age boundary - Major Medical - WWX-USA - age {age}",
            plan="Major Medical", coverage="Worldwide excluding USA",
            ip_usd=0, op_usd=None, age=age)

    # ── Group 4: IP/OP combos on Select (rateTable=deductible flow stress test)
    for ip in [850, 1700, 3400, 8500]:
        for op in [425, 850, 1700]:
            add(f"IP/OP combo - Select - WW - IP {ip}/OP {op} - age 40",
                plan="Select", coverage="Worldwide",
                ip_usd=ip, op_usd=op, age=40)

    # ── Group 5: Frequency variations on Premier
    for freq in ["Annually", "quarter", "month"]:
        add(f"Frequency - Premier - WWX-USA - {freq} - age 45",
            plan="Premier", coverage="Worldwide excluding USA",
            ip_usd=1700, op_usd=425, age=45, frequency=freq)

    # ── Group 6: Currency variations on Elite
    for currency in ["USD", "GBP", "EUR"]:
        add(f"Currency - Elite - WW - NIL - age 35 - {currency}",
            plan="Elite", coverage="Worldwide",
            ip_usd=0, op_usd=0, age=35, currency=currency)

    # ── Group 7: MC residency on Ultimate + Select
    for plan in ["Ultimate", "Select"]:
        cov = PLAN_COVERAGES[plan][0]   # "Worldwide"
        op = 0 if PLAN_OP_DEDUCTIBLES[plan] is not None else None
        add(f"MC residency - {plan} - {cov} - NIL - age 35",
            plan=plan, coverage=cov, ip_usd=0, op_usd=op, age=35,
            residency_override="MC")

    return cases
