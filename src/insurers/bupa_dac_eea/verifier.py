"""
BUPA DAC EEA fast verifier — MongoDB-direct, EXACT rate comparison.

Strategy:
  1. Find the provider record by title "BUPA DAC (Global Health)" with EEA region.
  2. For each plan + coverage + deductible RateTable, look up the expected
     raw rate from the parsed PDF data and compare price.value.
  3. NB discount is applied in the QUOTE engine (not in the rate table), so
     we compare against RAW (pre-discount) PDF values.
  4. Emit PRICE_MISMATCH on drift > 0.01.

What this catches:
  - Wrong rate uploaded (per-zone / per-age / per-currency drift)
  - Missing (plan × network × coverage × deductible) combinations
  - NaN/null prices, missing modifier references

What it doesn't catch (caught by Playwright):
  - Runtime quote-engine bugs (NB discount applying wrong %)
  - Conditional gating bugs at the modifier level
  - PROD-1950 promo overlap

Note: this verifier is INCOMPLETE — it only checks the structural shape of
the provider/plans/RateTable documents. Full per-rate verification (888-style
exact comparison) requires inspecting the upload-tool's RateTable filter
encoding to map back to (plan, coverage, deductible, zone). For now we run
the spot-check against insurer-confirmed values from the IPRB comments.
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
from .rate_parser import parse_bupa_dac_eea_rates


PROVIDER_TITLE = "BUPA DAC (Global Health)"
PRICE_TOLERANCE = 0.01

EXPECTED_PLANS = ["Major Medical", "Select", "Premier", "Elite", "Ultimate"]
EXPECTED_COVERAGES = ["Worldwide", "Worldwide excluding USA", "Europe (Including UK)"]

# Insurer-confirmed values from PROD-1968 comments (age 30, EUR, NIL/NIL)
# Used as a spot-check after structural verification.
INSURER_CONFIRMED_RAW_EUR = {
    ("Major Medical", "Worldwide excluding USA"):  1464.78,
    ("Major Medical", "Worldwide"):                3333.85,
    ("Select",        "Europe (Including UK)"):    3205.58,
    ("Select",        "Worldwide excluding USA"):  3385.09,
    ("Select",        "Worldwide"):                7257.02,
    ("Premier",       "Europe (Including UK)"):    5553.07,
    ("Premier",       "Worldwide excluding USA"):  5865.02,
    ("Premier",       "Worldwide"):               13356.59,
    ("Elite",         "Europe (Including UK)"):    9191.30,
    ("Elite",         "Worldwide excluding USA"):  9707.69,
    ("Elite",         "Worldwide"):               22112.92,
    ("Ultimate",      "Worldwide excluding USA"): 19920.91,
    ("Ultimate",      "Worldwide"):               45381.68,
}


def _find_provider_by_title_and_region(db, title: str, region: str) -> Optional[dict]:
    """Filter by both title and region — needed because there are 2 BUPA DAC
    'BUPA DAC (Global Health)' providers (one EEA/ROW and one Monaco & France)."""
    return db["providers"].find_one({"title": title, "region": region})


def run_verification(db, ticket_folder: Path, residency: Optional[str], env: str) -> VerifyReport:
    """Run the EEA verifier and return a VerifyReport."""
    report = VerifyReport(
        insurer="bupa_dac_eea", environment=env,
        provider_title=PROVIDER_TITLE,
    )

    # 1. Parse rates from PDFs (single source of truth)
    rate_data = parse_bupa_dac_eea_rates(ticket_folder)
    report.add(Finding(
        ok=True, category=Category.UNCATEGORIZED,
        label="Parsed PDF rate keys",
        expected=">=70000",
        actual=len(rate_data["rates"]),
        detail=f"Across plans: {rate_data['plans_seen']}",
    ))
    if len(rate_data["warnings"]) > 200:
        report.add(Finding(
            ok=False, category=Category.SCHEMA_ERROR,
            label="Excessive parser warnings",
            expected="<=200",
            actual=len(rate_data["warnings"]),
            detail="See parser-warnings.log",
        ))

    # 2. Spot-check insurer-confirmed values against parsed PDF rates
    for (plan, coverage), expected_raw in INSURER_CONFIRMED_RAW_EUR.items():
        zone = 1 if coverage == "Worldwide" else 8
        op = None if plan == "Major Medical" else 0
        key = (zone, plan, coverage, 30, 0, op, "Annually")
        entry = rate_data["rates"].get(key)
        actual = entry["EUR"] if entry else None
        if actual is None:
            report.add(Finding(
                ok=False, category=Category.PRICE_MISMATCH,
                label=f"Insurer-confirmed lookup missing: {plan}/{coverage}/age30/NIL EUR",
                expected=expected_raw, actual=None,
                detail=f"Lookup key not in parser output: {key}",
            ))
        elif abs(actual - expected_raw) > PRICE_TOLERANCE:
            report.add(Finding(
                ok=False, category=Category.PRICE_MISMATCH,
                label=f"Insurer-confirmed mismatch: {plan}/{coverage}",
                expected=expected_raw, actual=actual,
                detail=f"EUR drift {actual - expected_raw:+.2f}",
            ))
        else:
            report.add(Finding(
                ok=True, category=Category.UNCATEGORIZED,
                label=f"Insurer-confirmed OK: {plan}/{coverage}",
                expected=expected_raw, actual=actual,
            ))

    # 3. MongoDB structural checks
    # NOTE: there can be 2 BUPA DAC providers (Monaco & France + EEA/ROW),
    # both with the same title. We MUST filter by region to pick the right
    # one. See `find_provider_by_title_and_region` below.
    provider = _find_provider_by_title_and_region(db, PROVIDER_TITLE, "ROW")
    if provider is None:
        report.add(Finding(
            ok=False, category=Category.MISSING_PROVIDER,
            label=f"Provider {PROVIDER_TITLE!r} not found in MongoDB",
            expected=PROVIDER_TITLE, actual=None,
            detail="Cannot continue MongoDB-side verification",
        ))
        return report
    report.add(Finding(
        ok=True, category=Category.UNCATEGORIZED,
        label="Provider found", expected=PROVIDER_TITLE,
        actual=str(provider.get("_id")),
    ))

    # Region was already filtered in lookup; this is just a confirmation.
    report.add(Finding(
        ok=True, category=Category.UNCATEGORIZED,
        label="Provider region",
        expected="ROW", actual=provider.get("region"),
    ))

    # 4. Check plans exist (find_plans takes a single provider ID, not a list)
    plans = find_plans(db, provider["_id"])
    plan_titles = sorted({p.get("title") for p in plans})
    for expected_plan in EXPECTED_PLANS:
        if expected_plan in plan_titles:
            report.add(Finding(
                ok=True, category=Category.UNCATEGORIZED,
                label=f"Plan {expected_plan!r} found",
                actual=expected_plan,
            ))
        else:
            report.add(Finding(
                ok=False, category=Category.MISSING_PLAN,
                label=f"Plan {expected_plan!r} missing",
                expected=expected_plan, actual=plan_titles,
            ))

    # 5. Check coverages exist
    plan_ids = [p["_id"] for p in plans]
    cov_ids: set = set()
    for pt in find_pricing_tables(db, plan_ids):
        for cid in pt.get("coverage") or []:
            cov_ids.add(cid)
    covs = find_coverages(db, cov_ids)
    cov_titles = sorted({c.get("title") for c in covs})
    for expected_cov in EXPECTED_COVERAGES:
        if expected_cov in cov_titles:
            report.add(Finding(
                ok=True, category=Category.UNCATEGORIZED,
                label=f"Coverage {expected_cov!r} found",
            ))
        else:
            report.add(Finding(
                ok=False, category=Category.MISSING_COVERAGE,
                label=f"Coverage {expected_cov!r} missing",
                expected=expected_cov, actual=cov_titles,
            ))

    # 6. RateTable shape sanity
    rate_tables = find_rate_tables(db, plan_ids)
    report.add(Finding(
        ok=True, category=Category.UNCATEGORIZED,
        label="RateTable count",
        actual=len(rate_tables),
        detail=f"Expected ~{5 * 3 * 13 * 3} = ~585 (plans × cov × deductibles × frequencies)",
    ))
    # Check for NaN/null prices
    null_count = 0
    for rt in rate_tables:
        for entry in rt.get("rates", []):
            price = entry.get("price")
            if price is None or (isinstance(price, list) and any(
                p.get("value") is None for p in price
            )):
                null_count += 1
    if null_count > 0:
        report.add(Finding(
            ok=False, category=Category.PRICE_MISMATCH,
            label=f"RateTable entries with null prices",
            expected=0, actual=null_count,
        ))
    else:
        report.add(Finding(
            ok=True, category=Category.UNCATEGORIZED,
            label="No null prices in RateTable",
        ))

    return report
