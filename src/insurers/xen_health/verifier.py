"""
Xen Health fast verifier — MongoDB-direct, EXACT rate comparison.

Xen's pricing data lives in:
  - The "Deductibles" modifier's options: each option = (plan × copay variant)
  - Each option carries `premiumMod` with type=conditional-override and a list
    of conditionalPrices (one per age band × gender)
  - Maternity loading lives in the "Maternity" addon's premiumMod cps
  - baseAnnualPremium on PricingTable is a placeholder (zeros)

This verifier:
  1. For each Deductibles option: identify (plan, copay) → look up every cp's
     price and compare to raw rate xlsx value
  2. For each Maternity addon option: compare cp values to raw maternity_loadings
  3. Schema sanity (counts, NaN guards, annualLimit per plan, applicableTaxes)
"""

from pathlib import Path
from typing import Optional

from src.common.db_verifier import (
    Category,
    Finding,
    VerifyReport,
    find_provider,
    find_plans,
    find_pricing_tables,
    find_modifiers,
    find_coverages,
)
from .rate_parser import parse_xen_rate_xlsx


PROVIDER_TITLE = "xen_health"
PRICE_TOLERANCE = 0.01


# Per-residency authoritative settings.
RESIDENCY_CONFIG = {
    "DB_NE": {
        "region_substring": "Dubai",
        "rate_subdir":      "DB_NE-rates",
        "annual_limit_aed_by_plan": {
            "Xen Complete":  1_000_000,
            "Xen Elevate":   1_000_000,
            "Xen Boost":       300_000,
            "Xen Essential":   250_000,
        },
    },
    "AbuDhabi": {
        "region_substring": "Abu Dhabi",
        "rate_subdir":      "AUH-rates",
        "annual_limit_aed_by_plan": {
            "Xen Complete":  1_000_000,
            "Xen Elevate":   1_000_000,
            "Xen Boost":       300_000,
            "Xen Essential":   250_000,
        },
    },
}


# Map age range tuple → raw age band label
AGE_RANGE_TO_BAND = {
    (0, 10):  "0-10",
    (11, 17): "11-17",
    (18, 25): "18-25",
    (26, 30): "26-30",
    (31, 35): "31-35",
    (36, 40): "36-40",
    (41, 45): "41-45",
    (46, 50): "46-50",
    (51, 55): "51-55",
    (56, 59): "56-59",
    (60, 64): "60-64",
}


def _extract_condition(conds: list, type_name: str):
    for c in conds or []:
        if c.get("type") == type_name:
            return c.get("value")
    return None


def _plan_title_for_option(opt: dict, plan_by_id: dict) -> Optional[str]:
    plan_ids = _extract_condition(opt.get("conditions") or [], "PLAN_EQUALS_TO") or []
    for pid in plan_ids:
        if pid in plan_by_id:
            return plan_by_id[pid].get("title")
    return None


def verify(db, environment: str, residency_key: str = "DB_NE") -> VerifyReport:
    cfg = RESIDENCY_CONFIG[residency_key]
    report = VerifyReport(
        insurer=f"xen_health/{residency_key}",
        environment=environment,
        provider_title=f"xen_health ({cfg['region_substring']})",
    )

    # ── Find the right residency's Provider ─────────────────────────────────
    provider = db["providers"].find_one(
        {"title": PROVIDER_TITLE, "region": {"$regex": cfg["region_substring"]}}
    )
    if not provider:
        report.add(Finding(False, Category.MISSING_PROVIDER,
            f"Xen Health provider for {residency_key} not found",
            detail=f"Looked for title='xen_health' region containing '{cfg['region_substring']}'"))
        return report
    report.add(Finding(True, Category.MISSING_PROVIDER,
        f"Provider {provider.get('region')!r} found"))

    plans = find_plans(db, provider["_id"])
    plan_ids = [p["_id"] for p in plans]
    plan_by_id = {p["_id"]: p for p in plans}
    if len(plans) != 4:
        report.add(Finding(False, Category.MISSING_PLAN,
            "Expected 4 Xen plans", expected=4, actual=len(plans),
            detail=f"Got: {[p.get('title') for p in plans]}"))
    else:
        report.add(Finding(True, Category.MISSING_PLAN,
            f"All 4 plans present: {sorted(p.get('title') for p in plans)}"))

    # ── Parse the raw rate xlsx ─────────────────────────────────────────────
    raw_xlsx_dir = (
        Path("/home/support/Desktop/MIRO/auto-onboarding/PROD-2022-2039-PlanUpdate-XenHealth-AbuDhabi-DubaiNE/raw")
        / cfg["rate_subdir"]
    )
    xlsx_files = list(raw_xlsx_dir.glob("Xen*.xlsx"))
    if not xlsx_files:
        report.add(Finding(False, Category.SCHEMA_ERROR,
            f"Raw rate xlsx not found in {raw_xlsx_dir}"))
        return report
    rate_data = parse_xen_rate_xlsx(xlsx_files[0])

    # ── Locate the Deductibles modifier ─────────────────────────────────────
    mods = find_modifiers(db, plan_ids)
    ded_type = db["modifiertypes"].find_one({"title": "Deductible"})
    if not ded_type:
        report.add(Finding(False, Category.SCHEMA_ERROR, "modifiertypes 'Deductible' missing"))
        return report
    deductibles = next((m for m in mods if m.get("type") == ded_type["_id"]), None)
    if not deductibles:
        report.add(Finding(False, Category.MISSING_DEDUCTIBLE,
            "Deductibles modifier not found among plan modifiers"))
        return report

    # ── Per-option exact-rate check ────────────────────────────────────────
    options_checked = 0
    rate_checks = 0
    mismatches = 0
    for opt in deductibles.get("options") or []:
        opt_label = opt.get("label", "")
        plan_title = _plan_title_for_option(opt, plan_by_id)
        if not plan_title:
            report.add(Finding(False, Category.MISSING_PLAN,
                f"Deductibles option {opt.get('id')} has no PLAN_EQUALS_TO condition"))
            continue
        if plan_title not in rate_data["rates"]:
            report.add(Finding(False, Category.MISSING_PLAN,
                f"Plan {plan_title!r} not in raw rate data"))
            continue
        # Find matching copay variant in raw by exact label match
        copay_variants = rate_data["rates"][plan_title]
        raw_copay = next((k for k in copay_variants if k == opt_label), None)
        if not raw_copay:
            # Try a normalized match (Xen DB_NE persona uses 'copay' no hyphen; AUH uses 'co-pay')
            cleaned_opt = opt_label.replace("co-pay", "copay")
            raw_copay = next((k for k in copay_variants if k.replace("co-pay", "copay") == cleaned_opt), None)
        if not raw_copay:
            report.add(Finding(False, Category.MISSING_DEDUCTIBLE,
                f"Copay label {opt_label!r} not found in raw rate data for {plan_title}"))
            continue
        options_checked += 1

        # Walk premiumMod conditional prices
        pm = opt.get("premiumMod") or {}
        cps = pm.get("conditionalPrices") or []
        for cp in cps:
            conds = cp.get("conditions") or []
            min_age = _extract_condition(conds, "CUSTOMER_MIN_AGE")
            max_age = _extract_condition(conds, "CUSTOMER_MAX_AGE")
            gender  = _extract_condition(conds, "CUSTOMER_GENDER")
            band    = AGE_RANGE_TO_BAND.get((min_age, max_age))
            if not band:
                continue
            g_short = "M" if gender == "male" else "F"
            expected = rate_data["rates"][plan_title][raw_copay].get(band, {}).get(g_short)
            if expected is None:
                continue
            prices = cp.get("price") or []
            if not prices:
                continue
            actual = prices[0].get("value")
            rate_checks += 1
            if abs(float(actual) - float(expected)) > PRICE_TOLERANCE:
                mismatches += 1
                report.add(Finding(False, Category.PRICE_MISMATCH,
                    f"{plan_title} / {opt_label[:40]} / {band} / {g_short}",
                    expected=expected, actual=actual,
                ))

    report.add(Finding(True, Category.MISSING_DEDUCTIBLE,
        f"Deductibles modifier: {options_checked} options matched to raw copay variants"))
    if mismatches == 0:
        report.add(Finding(True, Category.PRICE_MISMATCH,
            f"All {rate_checks} Xen Health deductible rates match raw xlsx exactly"))

    # ── Maternity loading verification ─────────────────────────────────────
    _verify_maternity_loadings(db, mods, rate_data, plan_by_id, report)

    # ── Annual Limit per plan ──────────────────────────────────────────────
    _verify_annual_limits(db, plan_ids, cfg["annual_limit_aed_by_plan"], plan_by_id, report)

    # ── applicableTaxes (UAE has 5% VAT applicable) ────────────────────────
    taxes = provider.get("applicableTaxes") or []
    vat = next((t for t in taxes if t.get("tax") == "VAT"), None)
    if not vat:
        report.add(Finding(False, Category.MISSING_TAX_APPLICABLE,
            "applicableTaxes missing VAT entry"))
    else:
        report.add(Finding(True, Category.MISSING_TAX_APPLICABLE,
            f"VAT present: rate={vat.get('rate')}, applicable={vat.get('applicable')}"))

    return report


def _verify_maternity_loadings(db, mods, rate_data, plan_by_id, report):
    """Each Maternity addon option should add the per-plan loading from the raw xlsx."""
    maternity = next(
        (m for m in mods
         if "Maternity (Consultations" in (m.get("label") or "")),
        None,
    )
    if not maternity:
        report.add(Finding(False, Category.MISSING_BENEFIT_OPTION,
            "Maternity addon modifier not found"))
        return

    matched = 0
    for opt in maternity.get("options") or []:
        plan_title = _plan_title_for_option(opt, plan_by_id)
        if not plan_title:
            continue
        expected = rate_data["maternity_loadings"].get(plan_title)
        if expected is None:
            continue
        # Loading may sit on premiumMod or addonCost
        for field in ("premiumMod", "addonCost"):
            obj = opt.get(field) or {}
            cps = obj.get("conditionalPrices") or []
            for cp in cps:
                prices = cp.get("price") or []
                if not prices:
                    continue
                actual = prices[0].get("value")
                if actual is None:
                    continue
                if abs(float(actual) - float(expected)) > PRICE_TOLERANCE:
                    report.add(Finding(False, Category.PRICE_MISMATCH,
                        f"Maternity loading wrong for {plan_title}",
                        expected=expected, actual=actual,
                    ))
                else:
                    matched += 1
    if matched > 0:
        report.add(Finding(True, Category.PRICE_MISMATCH,
            f"Maternity loadings verified for {matched} plan-cps"))
    else:
        report.add(Finding(False, Category.MISSING_BENEFIT_OPTION,
            "Maternity loadings: no cps matched any plan"))


def _verify_annual_limits(db, plan_ids, expected_by_plan, plan_by_id, report):
    pts = find_pricing_tables(db, plan_ids)
    seen_plans = set()
    for pt in pts:
        plan_title = plan_by_id.get(pt.get("plan"), {}).get("title")
        if not plan_title:
            continue
        seen_plans.add(plan_title)
        al = pt.get("annualLimit")
        # Shape: list of {currency, value}
        if isinstance(al, list) and al:
            aed_entries = [e for e in al if e.get("currency") == "AED"]
            if not aed_entries:
                report.add(Finding(False, Category.MISSING_ANNUAL_LIMIT,
                    f"Annual Limit missing AED entry for {plan_title}"))
                continue
            actual = aed_entries[0].get("value")
        else:
            actual = al
        expected = expected_by_plan.get(plan_title)
        if expected is None:
            continue
        if actual != expected:
            report.add(Finding(False, Category.MISSING_ANNUAL_LIMIT,
                f"Annual Limit for {plan_title} mismatch",
                expected=expected, actual=actual,
            ))
        else:
            report.add(Finding(True, Category.MISSING_ANNUAL_LIMIT,
                f"Annual Limit for {plan_title} = AED {actual:,}"))

    missing = set(expected_by_plan) - seen_plans
    if missing:
        report.add(Finding(False, Category.MISSING_ANNUAL_LIMIT,
            f"PricingTable missing for plans: {sorted(missing)}"))
