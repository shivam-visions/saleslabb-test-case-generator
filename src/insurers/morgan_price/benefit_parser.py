"""
Deterministic parser for raw-benefits.xlsx (Morgan Price specifically).

Extracts the structured insurer parameters needed for pricing + test-case
generation: plan name, annual limits, networks, IP deductibles, OP copays,
payment frequency surcharges, and area incl-lists (from Geographical Coverage).

Persona-derived constants (deductible discount %, network labels, etc.) live
here as well — these are the values from the persona file that aren't
explicit in raw-benefits.xlsx but ARE the insurer's documented rules.
"""

import re
from pathlib import Path
from typing import Dict, List

import openpyxl


# Deductible -> discount fraction. Per persona §9.7 (Excess Discount):
# applied as a percentage on the combined total (postBenefit application).
DEDUCTIBLE_DISCOUNTS: Dict[str, float] = {
    "NIL per year deductible":      0.00,
    "USD 100 per year deductible":  0.05,
    "USD 250 per year deductible":  0.10,
    "USD 500 per year deductible":  0.15,
    "USD 1,000 per year deductible": 0.20,
    "USD 2,500 per year deductible": 0.30,
    "USD 5,000 per year deductible": 0.40,
}

# Network modifier labels (per persona §16.5 / §4.11f).
NETWORK_OPTIONS: List[str] = ["Enhanced Network", "Standard Network"]

# OP co-pay options (deductible.OP modifier).
OP_COPAY_OPTIONS: List[str] = ["OP Excluded", "10% per visit copay", "Nil per visit copay"]

# OP module options (Out-patient Consultations primary cascade addon).
OP_MODULE_OPTIONS: List[str] = ["(Optional Benefit Available)", "OP Module 1", "OP Module 2"]

# Annual Limit options. 500K is default per persona §9.3.
ANNUAL_LIMIT_OPTIONS: List[str] = ["USD 250,000", "USD 500,000", "USD 1,000,000"]
ANNUAL_LIMIT_DEFAULT: str = "USD 500,000"


def _strip_bullet(line: str) -> str:
    """Strip leading bullet markers from a multi-line list cell."""
    return line.lstrip("•").lstrip("-").strip()


def parse_raw_benefits(xlsx_path: Path) -> dict:
    """
    Parse the raw insurer benefit workbook and return a structured dict.

    Output shape:
        {
            "plan_name": "Flexible Choices",
            "annual_limits": ["USD 250,000", "USD 500,000", "USD 1,000,000"],
            "annual_limit_default": "USD 500,000",
            "ip_deductibles": [...7 entries from raw],
            "op_copays": [...3 entries from raw],
            "networks": ["Enhanced Network", "Standard Network"],
            "op_modules": [...3 entries],
            "payment_frequencies": {"Annual": 0.0, "Semi-annual": 0.04, "Quarterly": 0.05, "Monthly": 0.08},
            "deductible_discounts": {label: fraction, ...},
        }
    """
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active if "USDGBPEUR" not in wb.sheetnames else wb["USDGBPEUR"]

    # Build a {label -> value} map from rows where col A has a label and col B has a value.
    rows: Dict[str, str] = {}
    for r in range(1, ws.max_row + 1):
        label = ws.cell(row=r, column=1).value
        value = ws.cell(row=r, column=2).value
        if label:
            rows[str(label).strip()] = str(value) if value is not None else ""

    # --- Plan name ---
    plan_name = rows.get("Plan Name", "").strip()
    if not plan_name:
        raise ValueError("raw-benefits.xlsx: 'Plan Name' row missing")

    # --- Annual limit ---
    al_text = rows.get("Annual Limit", "")
    annual_limits = [_strip_bullet(l) for l in al_text.split("\n") if l.strip()]

    # --- Payment frequency surcharges ---
    def pct_to_fraction(s: str) -> float:
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
        if not m:
            raise ValueError(f"Could not parse percentage from {s!r}")
        return float(m.group(1)) / 100.0

    payment_frequencies = {
        "Annual": 0.0,
        "Semi-annual": pct_to_fraction(rows.get("Semi Annual Surcharge", "0%")),
        "Quarterly":   pct_to_fraction(rows.get("Quarterly Surcharge",  "0%")),
        "Monthly":     pct_to_fraction(rows.get("Monthly Surcharge",    "0%")),
    }

    # --- IP / OP options (parsed from the 'Co-pay/excess' filter row) ---
    copay_text = rows.get("Co-pay/excess", "")
    ip_deductibles: List[str] = []
    op_copays_raw: List[str] = []
    section = None
    for line in copay_text.split("\n"):
        ls = line.strip()
        if ls == "IP":
            section = "ip"
            continue
        if ls == "OP":
            section = "op"
            continue
        if not ls:
            continue
        cleaned = _strip_bullet(ls)
        if section == "ip":
            ip_deductibles.append(cleaned)
        elif section == "op":
            op_copays_raw.append(cleaned)

    # Normalize OP copays to the canonical modifier labels used in upload-tool / Playwright.
    # raw says "10% co-pay" -> modifier says "10% per visit copay"; "NIL co-pay" -> "Nil per visit copay".
    op_copay_normalization = {
        "OP Excluded": "OP Excluded",
        "10% co-pay": "10% per visit copay",
        "NIL co-pay": "Nil per visit copay",
    }
    op_copays = [op_copay_normalization.get(c, c) for c in op_copays_raw]

    return {
        "plan_name": plan_name,
        "annual_limits": annual_limits or ANNUAL_LIMIT_OPTIONS,
        "annual_limit_default": ANNUAL_LIMIT_DEFAULT,
        "ip_deductibles": ip_deductibles,
        "op_copays": op_copays,
        "op_modules": OP_MODULE_OPTIONS,
        "networks": NETWORK_OPTIONS,
        "payment_frequencies": payment_frequencies,
        "deductible_discounts": DEDUCTIBLE_DISCOUNTS,
    }


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) != 2:
        print("Usage: python benefit_parser.py <path-to-raw-benefits.xlsx>", file=sys.stderr)
        sys.exit(1)
    data = parse_raw_benefits(Path(sys.argv[1]))
    print(json.dumps(data, indent=2))
