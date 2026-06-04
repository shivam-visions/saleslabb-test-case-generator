"""
Xen Health pricing engine (DB_NE + AbuDhabi).

Formula (per xen_health-persona.md §7 + §9 + §13):

  base = rate_lookup(plan, copay, age_band, gender)

  loadings (additive in AED, applied BEFORE discount %):
    + maternity_loading[plan] if customer is married female age 18-42

  per_customer % adjustments (applied AFTER base + loadings):
    + 40% Children Loading if customer is a standalone child age 0-17
      (i.e. dependent, no spouse on policy, age <= 17)

  per_plan % adjustments (applied on the policy total):
    - 10% Family Discount if there are 2+ children under age 10 on the policy

  Currency conversion:
    - All rates from xlsx are in AED.
    - To quote in USD, apply exchange_premium AED→USD = 0.272294078 (non-reciprocal).

  Annual frequency only (no monthly/quarterly surcharges).
  No taxes (applicableTaxes set to 0% not-applicable in upload-tool).

The pricer below operates on a single customer at a time and returns the AED amount.
Multi-member family math (the family discount) lives in the generator, which
totals all members then applies the % discount.
"""

from typing import Optional


# Authoritative exchange rates from xen_health-persona.md §13
EXCHANGE_PREMIUM_AED_TO_USD = 0.272294078
EXCHANGE_PREMIUM_USD_TO_AED = 3.6725

# Discount/loading percentages (xen_health-persona.md §9)
CHILDREN_LOADING_PCT = 0.40        # +40% on standalone child under 17
FAMILY_DISCOUNT_PCT  = 0.10        # -10% when 2+ children under 10 on policy

# Age bands map (an integer age → the band label used in the rate table)
AGE_BANDS = [
    (0, 10,  "0-10"),
    (11, 17, "11-17"),
    (18, 25, "18-25"),
    (26, 30, "26-30"),
    (31, 35, "31-35"),
    (36, 40, "36-40"),
    (41, 45, "41-45"),
    (46, 50, "46-50"),
    (51, 55, "51-55"),
    (56, 59, "56-59"),
    (60, 64, "60-64"),
]


def age_to_band(age: int) -> str:
    for lo, hi, label in AGE_BANDS:
        if lo <= age <= hi:
            return label
    raise ValueError(f"Age {age} is outside Xen Health's rate table (0-64)")


def compute_customer_premium(
    *,
    plan: str,
    copay: str,
    age: int,
    gender: str,           # "M" or "F"
    marital: str,          # "single" or "married"
    is_dependent_child: bool,   # True if this customer is a child dependent under 17
    rate_data: dict,
    currency: str = "AED",
) -> dict:
    """
    Compute one customer's premium for Xen Health.

    Returns a dict with `amount` (in `currency`) plus a `breakdown` for
    test-case explanation.
    """
    rates_for_plan = rate_data["rates"].get(plan)
    if not rates_for_plan:
        raise KeyError(f"Plan {plan!r} not found in rate table")

    rates_for_copay = rates_for_plan.get(copay)
    if not rates_for_copay:
        raise KeyError(f"Copay {copay!r} not found for plan {plan!r}")

    band = age_to_band(age)
    rates_for_band = rates_for_copay.get(band)
    if not rates_for_band:
        raise KeyError(f"Age band {band!r} not found for {plan!r}/{copay!r}")

    base = rates_for_band.get(gender)
    if base is None:
        raise KeyError(f"Gender {gender!r} rate missing for {plan!r}/{copay!r}/{band}")

    breakdown = {"base_aed": base}
    total_aed = base

    # Married-female 18-42 maternity loading (additive in AED)
    if gender == "F" and marital == "married" and 18 <= age <= 42:
        mat = rate_data["maternity_loadings"].get(plan, 0)
        breakdown["maternity_loading_aed"] = mat
        total_aed += mat

    # Standalone-child 40% loading (multiplicative)
    if is_dependent_child:
        loading = total_aed * CHILDREN_LOADING_PCT
        breakdown["children_loading_aed"] = round(loading, 2)
        total_aed += loading

    breakdown["customer_total_aed"] = round(total_aed, 2)

    # Currency conversion
    if currency == "USD":
        amount = total_aed * EXCHANGE_PREMIUM_AED_TO_USD
        breakdown["currency_conversion"] = f"AED→USD × {EXCHANGE_PREMIUM_AED_TO_USD}"
    elif currency == "AED":
        amount = total_aed
    else:
        raise ValueError(f"Unsupported currency {currency!r}")

    return {
        "amount": round(amount, 2),
        "currency": currency,
        "breakdown": breakdown,
    }


def apply_family_discount(total: float, customers: list[dict]) -> tuple[float, dict]:
    """
    Apply the 10% family discount if 2+ children under age 10 are on the policy.
    `customers` is a list of dicts with at least 'age' and 'is_child' keys.
    Returns (new_total, breakdown_dict).
    """
    children_under_10 = sum(1 for c in customers if c.get("is_child") and c.get("age", 0) < 10)
    if children_under_10 >= 2:
        new_total = total * (1 - FAMILY_DISCOUNT_PCT)
        return round(new_total, 2), {
            "family_discount_applied": True,
            "children_under_10_count": children_under_10,
            "discount_pct": FAMILY_DISCOUNT_PCT * 100,
            "before_discount": round(total, 2),
            "after_discount": round(new_total, 2),
        }
    return total, {"family_discount_applied": False}
