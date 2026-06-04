"""
Morgan Price (Flexible Choices) pricing engine.

Implements the modular-additive formula documented in morgan_price-persona.md
§6 + §16.1 + §16.11 + §16.12. Inputs come from rate_parser (raw PDF rates) and
benefit_parser (raw xlsx options + discount %).

Formula:
    sum_of_modules = core_ip
                   + (op_module_1 OR op_module_2 OR 0 depending on op_module choice)
                   + (dental if Dental selected)
                   + (maternity if Maternity selected AND female AND 18-59 AND OP Module 2)
                   + (optional_coinsure if op_copay == 'Nil per visit copay')
                   + (reduce_si if annual_limit == 'USD 250,000')          # negative
                   + (increase_si if annual_limit == 'USD 1,000,000')      # positive
                   + (enhanced_network if network == 'Enhanced Network')

    after_discount = sum_of_modules × (1 - deductible_discount[ip_deductible])
    final_annual   = after_discount × (1 + payment_freq_surcharge)

Per persona §16.2 + §12.10: no taxes apply → PriceWithoutTax == PriceWithTax.
"""

from typing import Optional


AGE_BANDS = [
    (0, 17,  "Child"),
    (18, 24, "18 to 24"),
    (25, 29, "25 to 29"),
    (30, 34, "30 to 34"),
    (35, 39, "35 to 39"),
    (40, 44, "40 to 44"),
    (45, 49, "45 to 49"),
    (50, 54, "50 to 54"),
    (55, 59, "55 to 59"),
    (60, 64, "60 to 64"),
    (65, 69, "65 to 69"),
    (70, 74, "70 to 74"),
]


def age_to_band(age: int) -> str:
    """Map a single-year age to its rate-PDF age band label."""
    for lo, hi, label in AGE_BANDS:
        if lo <= age <= hi:
            return label
    raise ValueError(f"Age {age} is outside Morgan Price's rate table (0-74)")


def compute_premium(
    *,
    area: str,
    age: int,
    gender: str,
    ip_deductible: str,
    op_copay: str,
    op_module: str,
    dental_selected: bool,
    maternity_selected: bool,
    annual_limit: str,
    network: str,
    payment_frequency: str,
    rate_table: dict,
    benefit_data: dict,
) -> dict:
    """
    Compute one customer's annual premium for Morgan Price Flexible Choices.

    Returns a dict with the final annual amount plus a per-component breakdown,
    so the test-case writer can attribute mismatches to a specific module.

    All currency values are returned as USD-equivalent floats. Per-currency
    expansion (EUR, GBP) is handled upstream — Morgan Price has amount parity
    across the three currencies (persona §16.2).
    """
    rates = rate_table[area][age_to_band(age)]

    breakdown = {"core_ip": rates["core_ip"]}
    total = rates["core_ip"]

    # OP module choice
    if op_module == "OP Module 1":
        breakdown["op_module_1"] = rates["op_module_1"]
        total += rates["op_module_1"]
    elif op_module == "OP Module 2":
        breakdown["op_module_2"] = rates["op_module_2"]
        total += rates["op_module_2"]
    # else "(Optional Benefit Available)" → no OP module cost

    # Dental
    if dental_selected:
        breakdown["dental"] = rates["dental"]
        total += rates["dental"]

    # Maternity — eligibility validated by the combination generator, not here.
    # If the caller asks for it on an ineligible customer, the rate column is None
    # and we surface a clear error.
    if maternity_selected:
        mat = rates["maternity"]
        if mat is None:
            raise ValueError(
                f"Maternity not applicable for age band {age_to_band(age)} (rate column is null)"
            )
        breakdown["maternity"] = mat
        total += mat

    # Optional co-insure removal — applies when OP co-pay is "Nil per visit copay"
    if op_copay == "Nil per visit copay":
        breakdown["optional_coinsure"] = rates["optional_coinsure"]
        total += rates["optional_coinsure"]

    # Annual Limit (Sum Insured) adjustment
    if annual_limit == "USD 250,000":
        breakdown["reduce_si"] = rates["reduce_si"]
        total += rates["reduce_si"]  # negative
    elif annual_limit == "USD 1,000,000":
        breakdown["increase_si"] = rates["increase_si"]
        total += rates["increase_si"]
    # else USD 500,000 (default) → no adjustment

    # Enhanced Network module (only when Enhanced Network selected; persona §4.11f)
    if network == "Enhanced Network":
        breakdown["enhanced_network"] = rates["enhanced_network"]
        total += rates["enhanced_network"]

    # Excess (deductible) discount on combined total — postBenefit application
    discount = benefit_data["deductible_discounts"][ip_deductible]
    after_discount = total * (1 - discount)
    breakdown["sum_of_modules"] = round(total, 2)
    breakdown["deductible_discount_pct"] = discount * 100
    breakdown["after_discount"] = round(after_discount, 2)

    # Payment frequency surcharge
    freq_surcharge = benefit_data["payment_frequencies"][payment_frequency]
    final_annual = after_discount * (1 + freq_surcharge)
    breakdown["frequency_surcharge_pct"] = freq_surcharge * 100
    breakdown["final_annual"] = round(final_annual, 2)

    return {
        "annual": round(final_annual, 2),
        "currency": "USD",
        "breakdown": breakdown,
    }
