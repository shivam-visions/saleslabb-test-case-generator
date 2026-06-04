"""
BUPA DAC EEA smoke test-case generator (PROD-1968).

Per the IPRB (01.04.2026 BUPA DAC Global Health EEA):
- 5 set plans (Major Medical / Select / Premier / Elite / Ultimate)
- No addons
- Currencies: USD/GBP/EUR
- Residency: EEA (EU 27 + Iceland, Liechtenstein, Norway), but EXCLUDING
  Ireland, Germany, Poland, Austria
- Coverage areas:
  * Major Medical: Worldwide, Worldwide excluding USA
  * Select/Premier/Elite/Ultimate: Worldwide, Worldwide excluding USA, Europe (Including UK)
  * NOTE: IPRB lists Ultimate with all 3 coverages, but per the rate PDF
    Ultimate only has 1 deductible (NIL/NIL) and limited coverage data.
- Frequency: Annual only (Monthly & Quarterly: no surcharge — same annual /12 or /4)

NB discounts are SUB-REGION dependent (see §6 of IPRB):
- Greek residents: most aggressive (MM 23.5%, Sel 37%, Pre 50%, Eli 50%, Ult 54.5%)
- Italy/Portugal/Cyprus/France/Monaco residents: MM 15, Sel 30, Pre 37, Eli 40.5, Ult 51
- Other EEA countries Worldwide cover: MM 15, Sel 30, Pre 30, Eli 30, Ult 30
- Other EEA countries Europe/WWX-USA: MM 15, Sel 30, Pre 30, Eli 30, Ult 30

For the smoke CSV we use the "other EEA countries" tier (most common) and
pick a representative residence country (e.g. Spain ES) to avoid Greek
/Italy/Portugal/Cyprus/France/Monaco-specific overrides. IPM can vary the
residency to verify the sub-region discount logic.

PROD-1950 promo (Apr 15 – Jun 30 2026) may ADDITIONALLY apply on top of NB
discount — MM 32%, Sel/Pre/Eli 44%, Ult excluded. We DO NOT encode this in
expected_price (matching Monaco/France generator behavior); discrepancies
between this CSV's expected and the runtime actual reveal whether the promo
fired.
"""

from typing import List

PROVIDER_TITLE = "BUPA DAC (Global Health)"  # matches upload-tool BUPA_DAC_EEA provider/index.js
NETWORK = "No network restriction"
EMAIL = "qa.bupa.eea@example.com"

PLAN_COVERAGES = {
    "Major Medical": ["Worldwide", "Worldwide excluding USA"],
    "Select":        ["Worldwide", "Worldwide excluding USA", "Europe (Including UK)"],
    "Premier":       ["Worldwide", "Worldwide excluding USA", "Europe (Including UK)"],
    "Elite":         ["Worldwide", "Worldwide excluding USA", "Europe (Including UK)"],
    # Per IPRB: Ultimate has all 3 coverages listed, but rate PDF only ships
    # NIL/NIL at WWExclUS and WW. Europe (Including UK) for Ultimate is
    # uncertain — flag in IPM reference.
    "Ultimate":      ["Worldwide", "Worldwide excluding USA"],
}

PLAN_IP_DEDUCTIBLES = {
    "Major Medical": [0, 850, 1700, 3400, 8500],
    "Select":        [0, 850, 1700, 3400, 8500],
    "Premier":       [0, 1700, 3400, 8500, 12750],
    "Elite":         [0, 3400, 8500, 12750, 22550],
    "Ultimate":      [0],
}

PLAN_OP_DEDUCTIBLES = {
    "Major Medical": None,               # IP-only
    "Select":        [0, 425, 850, 1700],
    "Premier":       [0, 425, 850, 1700],
    "Elite":         [0, 425, 850, 1700],
    "Ultimate":      [0],
}

# NB discount — using "Other EEA countries" tier (most common, applies to
# Spain/Belgium/Netherlands/Croatia/Czechia/Denmark/Estonia/Finland/Hungary/
# Latvia/Lithuania/Luxembourg/Malta/Romania/Slovakia/Slovenia/Sweden/Iceland/
# Liechtenstein/Norway/Bulgaria/Greece's Worldwide-only — Greek WWX/Europe
# uses higher discounts).
# For IPM verification of sub-region overrides, set residency to GR/IT/PT/CY/FR/MC.
NB_DISCOUNT_OTHER_EEA = {
    "Major Medical": {"Worldwide": 0.15,  "Worldwide excluding USA": 0.15,  "Europe (Including UK)": 0.15},
    "Select":        {"Worldwide": 0.30,  "Worldwide excluding USA": 0.30,  "Europe (Including UK)": 0.30},
    "Premier":       {"Worldwide": 0.30,  "Worldwide excluding USA": 0.30,  "Europe (Including UK)": 0.30},
    "Elite":         {"Worldwide": 0.30,  "Worldwide excluding USA": 0.30,  "Europe (Including UK)": 0.30},
    "Ultimate":      {"Worldwide": 0.30,  "Worldwide excluding USA": 0.30,  "Europe (Including UK)": 0.30},
}

# Representative residency for smoke cases (Spain — Other EEA tier).
DEFAULT_RESIDENCE_COUNTRY = "Spain"
DEFAULT_NATIONALITY = "Spain"

# Zone 8 is the standard EEA pricing zone (per insurer-confirmed values
# 2026-05-20). For "Worldwide" coverage, we use Zone 1 (Incl US).
ZONE_FOR_COVERAGE = {
    "Worldwide": 1,
    "Worldwide excluding USA": 8,
    "Europe (Including UK)": 8,
}


def _apply_nb_discount(rate: float, plan: str, coverage: str) -> float:
    pct = NB_DISCOUNT_OTHER_EEA.get(plan, {}).get(coverage, 0)
    return round(rate * (1 - pct), 2)


def _copay_ip_label(usd: int) -> str:
    return "NIL per year deductible" if usd == 0 else f"USD {usd:,} per year deductible"


def _copay_op_label(usd: int) -> str:
    return "NIL per visit deductible" if usd == 0 else f"USD {usd:,} per visit deductible"


def _fmt_price(amount: float, currency: str) -> str:
    return f"{currency} {amount:,.2f}"


def _baseline_customer(age: int, gender: str) -> dict:
    return {
        "firstName": "QA",
        "lastName":  f"BupaEEA_{age}{gender[0].upper()}",
        "email": EMAIL,
        "age": age,
        "day": "15", "month": "6",
        "gender": gender,
        "maritalStatus": "single",
        "nationality": DEFAULT_NATIONALITY,
        "residency": DEFAULT_RESIDENCE_COUNTRY,
        "relation": "",
    }


def _lookup_rate(rate_data: dict, *, plan: str, coverage: str, age: int,
                 ip_usd: int, op_usd, currency: str,
                 apply_nb_discount: bool = True) -> float | None:
    zone = ZONE_FOR_COVERAGE.get(coverage)
    if zone is None:
        return None
    key = (zone, plan, coverage, age, ip_usd, op_usd, "Annually")
    entry = rate_data["rates"].get(key)
    if entry is None:
        return None
    raw = entry.get(currency)
    if raw is None:
        return None
    if not apply_nb_discount:
        return raw
    return _apply_nb_discount(raw, plan, coverage)


def generate_smoke_cases(rate_data: dict) -> List[dict]:
    """Generate ~45 smoke test cases for BUPA DAC EEA."""
    cases: List[dict] = []
    idx = 0

    def add(name: str, *, plan: str, coverage: str, ip_usd: int, op_usd,
            age: int, gender: str = "male", currency: str = "USD",
            frequency: str = "Annually"):
        nonlocal idx
        rate = _lookup_rate(
            rate_data,
            plan=plan, coverage=coverage, age=age, ip_usd=ip_usd,
            op_usd=op_usd, currency=currency,
        )
        if rate is None:
            print(f"  ⚠ Skipping case (no rate found): {plan}/{coverage}/age{age}/"
                  f"IP{ip_usd}/OP{op_usd}/{currency}")
            return
        idx += 1
        freq_label = {"Annually": "Annual", "month": "Monthly", "quarter": "Quarterly"}[frequency]
        cases.append({
            "name": f"{name} (TC-{idx})",
            "customer": _baseline_customer(age, gender),
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

    # ── Group 1: Base case per plan, representative coverage, NIL deductible, age 35 ──
    for plan in ["Major Medical", "Select", "Premier", "Elite", "Ultimate"]:
        cov = PLAN_COVERAGES[plan][1]  # "Worldwide excluding USA"
        op = 0 if PLAN_OP_DEDUCTIBLES[plan] is not None else None
        add(f"Base - {plan} - {cov} - NIL - age 35", plan=plan, coverage=cov,
            ip_usd=0, op_usd=op, age=35)

    # ── Group 2: All coverages per plan × NIL/NIL × age 35 ──
    for plan, coverages in PLAN_COVERAGES.items():
        for cov in coverages:
            op = 0 if PLAN_OP_DEDUCTIBLES[plan] is not None else None
            add(f"Coverage - {plan} - {cov} - NIL - age 35",
                plan=plan, coverage=cov, ip_usd=0, op_usd=op, age=35)

    # ── Group 3: Age boundaries on Major Medical (IP-only) WWX-USA ──
    for age in [0, 17, 18, 30, 50, 65, 75, 84]:
        add(f"Age boundary - MM - WWX-USA - age {age}",
            plan="Major Medical", coverage="Worldwide excluding USA",
            ip_usd=0, op_usd=None, age=age)

    # ── Group 4: IP/OP combos on Select WWX-USA (rateTable flow) ──
    for ip in [850, 1700, 3400, 8500]:
        for op in [425, 850, 1700]:
            add(f"IP/OP - Select - WWX - IP{ip}/OP{op} - age 40",
                plan="Select", coverage="Worldwide excluding USA",
                ip_usd=ip, op_usd=op, age=40)

    # ── Group 5: Currency variations on Elite Europe, age 35 ──
    for currency in ["USD", "GBP", "EUR"]:
        add(f"Currency - Elite - Europe - NIL - age 35 - {currency}",
            plan="Elite", coverage="Europe (Including UK)",
            ip_usd=0, op_usd=0, age=35, currency=currency)

    # ── Group 6: Insurer-confirmed sanity-check rows (age 30 EUR Zone 8) ──
    # These should match the values in Nishma's 2026-05-20 comment.
    # Note: these use age 30 which is below Group 3's range. NB discount
    # still applies (30% for Other EEA), so expected ≠ raw confirmed value.
    for plan in ["Select", "Premier", "Elite"]:
        add(f"Insurer-confirmed - {plan} - Europe - age 30 - EUR",
            plan=plan, coverage="Europe (Including UK)",
            ip_usd=0, op_usd=0, age=30, currency="EUR")
        add(f"Insurer-confirmed - {plan} - WWX - age 30 - EUR",
            plan=plan, coverage="Worldwide excluding USA",
            ip_usd=0, op_usd=0, age=30, currency="EUR")
        add(f"Insurer-confirmed - {plan} - WW - age 30 - EUR",
            plan=plan, coverage="Worldwide",
            ip_usd=0, op_usd=0, age=30, currency="EUR")

    return cases
