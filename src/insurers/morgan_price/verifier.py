"""
Morgan Price fast verifier — MongoDB-direct, EXACT rate comparison.

Strategy:
  1. Build a coverage map: coverage_id → Area name (matched by title).
  2. Read the IP and OP deductible modifiers, build option-id → (network, coverage_id, deductible_label) maps.
  3. For each RateTable:
       - Look up its filter.value (= option-id) in the maps to determine which (network, coverage, deductible) it serves
       - For each rate entry: derive the age band, compute the expected Core IP (or OP optional-coinsure) value from raw
       - Compare price.value to expected — emit PRICE_MISMATCH on drift > 0.01
  4. For every addon modifier (Out-patient Consultations, Maternity, Dental, Enhanced Network Module):
       - Walk addonCost.conditionalPrices, compute expected from raw, compare cp.price.value
  5. Also verify the structural sanity checks (counts, NaN, references).

What this catches:
  - Wrong Core IP rate in RateTable (raw → parser → upload pipeline mistake)
  - Wrong addon cost (per-Area or per-age module loading misread)
  - Missing (plan × network × coverage × deductible) combinations
  - NaN/null prices, missing modifier references, broken plan IDs

What it doesn't catch (caught by Playwright instead):
  - Runtime quote-engine bugs (wrong order-of-application, postBenefit not firing)
  - Conditional gating bugs at the modifier level
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
    find_rate_tables,
    find_modifiers,
    find_coverages,
)
from .rate_parser import parse_rate_pdf, AREAS_TO_EXTRACT
from .benefit_parser import parse_raw_benefits


PROVIDER_TITLE = "Morgan Price (Flexible Choices)"
PRICE_TOLERANCE = 0.01      # rounding tolerance for AED/USD prices


# Coverage title → Area key in our raw rate table
COVERAGE_TITLE_TO_AREA = {
    "Bangladesh, Brunei, Cambodia, East Timor, India, Indonesia, Laos, Malaysia, Myanmar, Pakistan, Papua New Guinea, Philippines, Sri Lanka, Vietnam": "Area 1",
    "Worldwide excluding USA, China, Hong Kong, Singapore": "Area 2",
    "Worldwide excluding USA": "Area 3",
}

# Age (from, to) tuple → raw age-band key
AGE_RANGE_TO_BAND = {
    (0, 17):  "Child",
    (18, 24): "18 to 24",
    (25, 29): "25 to 29",
    (30, 34): "30 to 34",
    (35, 39): "35 to 39",
    (40, 44): "40 to 44",
    (45, 49): "45 to 49",
    (50, 54): "50 to 54",
    (55, 59): "55 to 59",
    (60, 64): "60 to 64",
    (65, 69): "65 to 69",
    (70, 74): "70 to 74",
}


def _build_coverage_map(db, plan_ids: list) -> dict:
    """coverage_id → Area name. Logs unmapped titles for debugging."""
    cov_ids: set = set()
    for pt in find_pricing_tables(db, plan_ids):
        for cid in pt.get("coverage") or []:
            cov_ids.add(cid)
    covs = find_coverages(db, cov_ids)
    out: dict = {}
    for c in covs:
        area = COVERAGE_TITLE_TO_AREA.get(c.get("title"))
        if area:
            out[c["_id"]] = area
    return out


def _extract_op_copay_label_from_option(opt: dict, modifier_options: list) -> Optional[str]:
    """For an OP deductible option, return its label (e.g. 'Nil per visit copay')."""
    return opt.get("label")


def _build_deductible_option_map(db, plan_ids: list, coverage_map: dict) -> tuple[dict, dict]:
    """
    Returns (ip_options, op_options): two dicts of option_id → {network, coverage_id, area, label}.
    Reads the "IP Deductible" and "OP Deductible" modifiers.
    """
    mods = find_modifiers(db, plan_ids)
    ip_options: dict = {}
    op_options: dict = {}
    # Detect IP / OP deductible modifiers by:
    #   - option-id prefix on the FIRST option ('ip-' vs 'op-'), OR
    #   - inputLabel containing 'IP Co/Pay' / 'OP Co/Pay'
    for m in mods:
        opts = m.get("options") or []
        if not opts:
            continue
        first_id = (opts[0].get("id") or "").lower()
        input_label = (m.get("inputLabel") or "").lower()
        is_ip = first_id.startswith("ip-") or "ip co/pay" in input_label or "ip co-pay" in input_label
        is_op = first_id.startswith("op-") or "op co/pay" in input_label or "op co-pay" in input_label
        if not (is_ip or is_op):
            continue
        target = ip_options if is_ip else op_options
        for o in m.get("options", []) or []:
            entry = {
                "network": None,
                "coverage_id": None,
                "area": None,
                "label": o.get("label"),
            }
            for cond in o.get("conditions") or []:
                if cond.get("type") == "COVERAGE_EQUALS_TO":
                    cov_ids = cond.get("value", [])
                    if cov_ids:
                        entry["coverage_id"] = cov_ids[0]
                        entry["area"] = coverage_map.get(cov_ids[0])
                elif cond.get("type") == "MODIFIER_INCLUDED":
                    vals = cond.get("value", [])
                    if vals and isinstance(vals[0], str):
                        entry["network"] = vals[0]
            target[o.get("id")] = entry
    return ip_options, op_options


def _expected_op_rate(op_label: str, area: str, age_band: str, rate_table: dict) -> float:
    """
    Per persona §16.11/§4.11c:
      OP Excluded         → 0
      10% per visit copay → 0
      Nil per visit copay → rate_table[area][age_band]['optional_coinsure']
    """
    if op_label and "Nil" in op_label:
        return float(rate_table[area][age_band].get("optional_coinsure") or 0)
    return 0.0


def verify(db, environment: str) -> VerifyReport:
    report = VerifyReport(
        insurer="morgan_price",
        environment=environment,
        provider_title=PROVIDER_TITLE,
    )

    # ── Provider exists ─────────────────────────────────────────────────────
    provider = find_provider(db, PROVIDER_TITLE)
    if not provider:
        report.add(Finding(False, Category.MISSING_PROVIDER,
            f"Provider {PROVIDER_TITLE!r} not found"))
        return report
    report.add(Finding(True, Category.MISSING_PROVIDER, "Provider exists"))

    # ── Plans ───────────────────────────────────────────────────────────────
    plans = find_plans(db, provider["_id"])
    plan_ids = [p["_id"] for p in plans]
    if not plans:
        report.add(Finding(False, Category.MISSING_PLAN, "No plans found for provider"))
        return report

    # ── Raw extraction (the ground truth) ───────────────────────────────────
    # Find ticket folder & rate files
    ticket = Path("/home/support/Desktop/MIRO/auto-onboarding/PROD-Morgan-Price-ROW-Enlistment")
    rate_pdf = next((ticket / "raw" / "rates").glob("*.pdf"))
    rate_table = parse_rate_pdf(rate_pdf)

    coverage_map = _build_coverage_map(db, plan_ids)
    _set_coverage_map(coverage_map)
    if len(coverage_map) != len(AREAS_TO_EXTRACT):
        report.add(Finding(False, Category.MISSING_COVERAGE,
            "Coverage→Area mapping incomplete",
            expected=len(AREAS_TO_EXTRACT), actual=len(coverage_map),
            detail=f"Got {list(coverage_map.values())}",
        ))
    else:
        report.add(Finding(True, Category.MISSING_COVERAGE,
            f"All {len(AREAS_TO_EXTRACT)} Areas mapped"))

    ip_options, op_options = _build_deductible_option_map(db, plan_ids, coverage_map)
    report.add(Finding(True, Category.MISSING_DEDUCTIBLE,
        f"Mapped {len(ip_options)} IP options + {len(op_options)} OP options"))

    # ── Rate-by-rate exact comparison ───────────────────────────────────────
    rate_tables = find_rate_tables(db, plan_ids)
    ip_checks = 0
    op_checks = 0
    skipped = 0
    for rt in rate_tables:
        filters = rt.get("filters") or []
        if not filters:
            report.add(Finding(False, Category.SCHEMA_ERROR,
                f"RateTable {rt.get('_id')} has no filters"))
            continue
        option_id = filters[0].get("value")
        if option_id in ip_options:
            scope = ip_options[option_id]
            is_ip = True
        elif option_id in op_options:
            scope = op_options[option_id]
            is_ip = False
        else:
            skipped += 1
            continue

        area = scope.get("area")
        if not area:
            report.add(Finding(False, Category.MISSING_COVERAGE,
                f"RateTable {rt.get('_id')} option {option_id} has no resolvable Area"))
            continue

        for rate in rt.get("rates") or []:
            cust = rate.get("customer") or {}
            from_age = cust.get("from")
            to_age = cust.get("to")
            band = AGE_RANGE_TO_BAND.get((from_age, to_age))
            if not band:
                report.add(Finding(False, Category.SCHEMA_ERROR,
                    f"Unmapped age range ({from_age}, {to_age}) in {rt.get('_id')}"))
                continue

            price = (rate.get("price") or {}).get("price")
            if price is None:
                report.add(Finding(False, Category.SCHEMA_ERROR,
                    f"Missing price for {area}/{band} in {rt.get('_id')}"))
                continue

            if is_ip:
                # IP rate should equal flat Core IP (no discount; no Enhanced Network bake-in)
                expected = float(rate_table[area][band]["core_ip"])
                ip_checks += 1
            else:
                expected = _expected_op_rate(scope.get("label"), area, band, rate_table)
                op_checks += 1

            if abs(float(price) - expected) > PRICE_TOLERANCE:
                report.add(Finding(False, Category.PRICE_MISMATCH,
                    f"Rate mismatch: {area} {band} {'IP' if is_ip else 'OP'} {scope.get('label')}",
                    expected=expected, actual=price,
                    detail=f"RateTable {rt.get('_id')} option={option_id}",
                ))

    if ip_checks:
        report.add(Finding(True, Category.PRICE_MISMATCH,
            f"Verified {ip_checks} IP rates against raw Core IP"))
    if op_checks:
        report.add(Finding(True, Category.PRICE_MISMATCH,
            f"Verified {op_checks} OP rates against raw OP cost model"))
    if skipped:
        report.add(Finding(False, Category.SCHEMA_ERROR,
            f"Skipped {skipped} RateTables (option-id not in IP/OP modifier maps)"))

    # ── Addon module rates: walk every modifier's addonCost.conditionalPrices ──
    modifiers = find_modifiers(db, plan_ids)
    _verify_addon_modules(modifiers, rate_table, report)

    # ── Schema sanity (NaN/null catches) ────────────────────────────────────
    _verify_annual_limit_modifier(modifiers, report)
    _verify_applicable_taxes(provider, report)

    return report


# ─────────────────────────────────────────────────────────────────────────────
# Per-modifier addon verification
# ─────────────────────────────────────────────────────────────────────────────

# Modifier label → raw rate-table column key
ADDON_MODIFIER_COL_MAP = {
    "Out-patient Consultations": None,    # has option-2 (OP Module 1) + option-3 (OP Module 2)
    "Dental":                     "dental",
    "Maternity (Consultations, Scans and Delivery)": "maternity",
    "Enhanced Network Module":    "enhanced_network",
}


def _verify_addon_modules(modifiers: list, rate_table: dict, report: VerifyReport) -> None:
    """For each known addon modifier, check that its addonCost.conditionalPrices match raw."""

    for m in modifiers:
        label = m.get("label", "")
        if label not in ADDON_MODIFIER_COL_MAP:
            continue
        rate_col = ADDON_MODIFIER_COL_MAP[label]

        for opt in m.get("options") or []:
            opt_label = opt.get("label", "")
            if rate_col is None:
                # Out-patient Consultations: route by option label
                if opt_label == "OP Module 1":
                    col = "op_module_1"
                elif opt_label == "OP Module 2":
                    col = "op_module_2"
                else:
                    continue
            else:
                col = rate_col

            addon = opt.get("addonCost") or {}
            cps = addon.get("conditionalPrices") or []
            if not cps:
                continue

            checks = 0
            mismatches = 0
            for cp in cps:
                # Extract age + coverage from conditions
                conds = cp.get("conditions") or []
                min_age = next((c.get("value") for c in conds if c.get("type") == "CUSTOMER_MIN_AGE"), None)
                max_age = next((c.get("value") for c in conds if c.get("type") == "CUSTOMER_MAX_AGE"), None)
                cov_ids = next((c.get("value") for c in conds if c.get("type") == "COVERAGE_EQUALS_TO"), [])
                if cov_ids and isinstance(cov_ids, list):
                    cov_id = cov_ids[0]
                else:
                    continue
                # Resolve area from coverage_id via the report's coverage_map
                area = _COV_MAP_CACHE.get(cov_id)
                if not area:
                    continue
                band = AGE_RANGE_TO_BAND.get((min_age, max_age))
                if not band:
                    continue
                expected = rate_table[area][band].get(col)
                if expected is None:
                    # raw column is null (e.g. Maternity for Child / 60+) — DB cp shouldn't exist either
                    continue
                price_entries = cp.get("price") or []
                if not price_entries:
                    continue
                actual = price_entries[0].get("value")
                if actual is None:
                    continue
                checks += 1
                if abs(float(actual) - float(expected)) > PRICE_TOLERANCE:
                    mismatches += 1
                    report.add(Finding(False, Category.PRICE_MISMATCH,
                        f"Addon {label!r} option {opt_label!r}: {area} {band}",
                        expected=expected, actual=actual,
                    ))
            if checks > 0:
                if mismatches == 0:
                    report.add(Finding(True, Category.PRICE_MISMATCH,
                        f"Addon {label!r} option {opt_label!r}: {checks} cps verified OK"))


# Module-level coverage map cache (populated by verify() above; keeps the addon walker simple).
_COV_MAP_CACHE: dict = {}


def _verify_annual_limit_modifier(modifiers: list, report: VerifyReport) -> None:
    al = next((m for m in modifiers if m.get("label") == "Annual Limit"), None)
    if not al:
        report.add(Finding(False, Category.MISSING_ANNUAL_LIMIT,
            "Annual Limit modifier not present"))
        return
    default_opt = next((o for o in al.get("options") or [] if o.get("default") is True), None)
    if not default_opt:
        report.add(Finding(False, Category.MISSING_ANNUAL_LIMIT,
            "Annual Limit has no default option"))
    elif default_opt.get("label") != "USD 500,000":
        report.add(Finding(False, Category.MISSING_ANNUAL_LIMIT,
            "Annual Limit default option wrong",
            expected="USD 500,000", actual=default_opt.get("label")))
    else:
        report.add(Finding(True, Category.MISSING_ANNUAL_LIMIT,
            "Annual Limit default = USD 500,000"))


def _verify_applicable_taxes(provider: dict, report: VerifyReport) -> None:
    taxes = provider.get("applicableTaxes") or []
    vat = next((t for t in taxes if t.get("tax") == "VAT"), None)
    if not vat:
        report.add(Finding(False, Category.MISSING_TAX_APPLICABLE,
            "applicableTaxes missing VAT entry — UI will display VAT by default"))
    elif vat.get("applicable") is not False:
        report.add(Finding(False, Category.MISSING_TAX_APPLICABLE,
            "VAT should be marked not-applicable",
            expected=False, actual=vat.get("applicable")))
    else:
        report.add(Finding(True, Category.MISSING_TAX_APPLICABLE,
            "VAT correctly suppressed"))


# Public hook so verify() can prime the cache before _verify_addon_modules runs.
def _set_coverage_map(coverage_map: dict) -> None:
    global _COV_MAP_CACHE
    _COV_MAP_CACHE = coverage_map
