"""
Deterministic rate-xlsx parser for Xen Health (DB_NE + AbuDhabi residencies).

Each insurer rate file has 3 sheets (one per copay variant). Each sheet has a
fixed-shape table:

    R5  : plan name headers (Complete | Elevate | Boost | Essential)
    R6  : gender headers (M | F) repeated per plan
    R7-R17: age bands (11 bands) × rate cells (currency-native AED)
    R19 : "Additional Premium" + plan name headers
    R20 : Married Female 18-42 maternity loading per plan

The mapping between SHEET NAME and the rate-sheet/upload-tool copay label is
fixed and documented in xen_health-persona.md §6:

    "20% Coins on all OP"   -> "Consultation 20% up to max AED 50, OP 20% co-pay, Pharmacy 20% co-pay"
                                (Essential only here, with Pharmacy 30%)
    "20% Coins on Pharma"   -> "Consultation 20% up to max AED 50, OP NIL co-pay, Pharmacy 20% co-pay"
    "No Coins"              -> "Consultation 20% up to max AED 50, OP NIL co-pay, Pharmacy NIL co-pay"
"""

from pathlib import Path
from typing import Dict, Optional

import openpyxl


# Sheet name -> copay label (for non-Essential plans).
SHEET_TO_COPAY = {
    "20% Coins on all OP": "Consultation 20% up to max AED 50, OP 20% co-pay, Pharmacy 20% co-pay",
    "20% Coins on Pharma": "Consultation 20% up to max AED 50, OP NIL co-pay, Pharmacy 20% co-pay",
    "No Coins":            "Consultation 20% up to max AED 50, OP NIL co-pay, Pharmacy NIL co-pay",
}

# Essential is sold only in "20% Coins on all OP" sheet, with its own copay label.
ESSENTIAL_COPAY = "Consultation 20% up to max AED 50, OP 20% co-pay, Pharmacy 30% co-pay"

# Column positions are fixed (1-based):
#   col 2  = age band label
#   cols 3-4   = Complete M | F
#   cols 5-6   = Elevate  M | F
#   cols 7-8   = Boost    M | F
#   cols 9-10  = Essential M | F
PLAN_COLUMNS = {
    "Xen Complete":  (3, 4),
    "Xen Elevate":   (5, 6),
    "Xen Boost":     (7, 8),
    "Xen Essential": (9, 10),
}

# Maternity loading row (R20) — plan-name header is in R19 at columns 5-8 (one column
# offset from rate-row plan layout because rate rows split M/F per plan, header row doesn't).
MATERNITY_ROW = 20
MATERNITY_PLAN_COLUMNS = {
    "Xen Complete":  5,
    "Xen Elevate":   6,
    "Xen Boost":     7,
    "Xen Essential": 8,
}

AGE_BAND_ROWS_START = 7   # First age band row
AGE_BAND_ROWS_END   = 17  # Last age band row (inclusive)


def _read_cell_number(ws, row: int, col: int) -> Optional[float]:
    v = ws.cell(row=row, column=col).value
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_xen_rate_xlsx(xlsx_path: Path) -> dict:
    """
    Parse one Xen Health residency's rate xlsx file.
    Returns:
        {
            "rates": {
                "Xen Complete": {
                    "Consultation … OP NIL co-pay, Pharmacy NIL co-pay": {
                        "0-10": {"M": 8361, "F": 7038},
                        "11-17": {"M": 5615, "F": 5794},
                        …
                    },
                    "Consultation … 20% co-pay, Pharmacy 20% co-pay": {...},
                    "Consultation … NIL co-pay, Pharmacy 20% co-pay": {...},
                },
                "Xen Elevate":   {…},
                "Xen Boost":     {…},
                "Xen Essential": {
                    "Consultation … 20% co-pay, Pharmacy 30% co-pay": {...},
                },
            },
            "maternity_loadings": {
                "Xen Complete":  4741,
                "Xen Elevate":   2923,
                "Xen Boost":     2319,
                "Xen Essential": 1955,
            },
            "age_bands": ["0-10", "11-17", ..., "60-64"],
        }
    """
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    rates: Dict[str, Dict[str, Dict[str, Dict[str, float]]]] = {p: {} for p in PLAN_COLUMNS}
    age_bands_seen: list = []
    maternity_loadings: Dict[str, float] = {}

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]

        # Read age band labels and per-plan rates
        per_plan_per_band: Dict[str, Dict[str, Dict[str, float]]] = {p: {} for p in PLAN_COLUMNS}
        for r in range(AGE_BAND_ROWS_START, AGE_BAND_ROWS_END + 1):
            band = ws.cell(row=r, column=2).value
            if not band:
                continue
            band = str(band).strip()
            if not age_bands_seen or band not in age_bands_seen:
                age_bands_seen.append(band)
            for plan_name, (col_m, col_f) in PLAN_COLUMNS.items():
                rate_m = _read_cell_number(ws, r, col_m)
                rate_f = _read_cell_number(ws, r, col_f)
                if rate_m is None and rate_f is None:
                    # Plan not sold under this copay variant
                    continue
                per_plan_per_band[plan_name][band] = {"M": rate_m, "F": rate_f}

        # Map the sheet to a copay label, and attach the table to the right plan keys
        for plan_name, per_band in per_plan_per_band.items():
            if not per_band:
                continue
            if plan_name == "Xen Essential":
                # Essential is only sold under "20% Coins on all OP" with a 30% Pharmacy variant.
                # Map it to its dedicated label regardless of sheet wording.
                if sheet_name == "20% Coins on all OP":
                    rates[plan_name][ESSENTIAL_COPAY] = per_band
                else:
                    # Essential rates shouldn't appear in other sheets; warn loudly if they do.
                    raise ValueError(
                        f"Essential rates found in unexpected sheet '{sheet_name}'. "
                        "Persona says Essential is sold only under '20% Coins on all OP'."
                    )
            else:
                copay_label = SHEET_TO_COPAY[sheet_name]
                rates[plan_name][copay_label] = per_band

        # Read the Married Female 18-42 loading row (R20). Present in every sheet,
        # but the values are uniform — take from the first sheet we see.
        if not maternity_loadings:
            for plan_name, mat_col in MATERNITY_PLAN_COLUMNS.items():
                v = _read_cell_number(ws, MATERNITY_ROW, mat_col)
                if v is not None:
                    maternity_loadings[plan_name] = v

    return {
        "rates": rates,
        "maternity_loadings": maternity_loadings,
        "age_bands": age_bands_seen,
    }


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) != 2:
        print("Usage: python rate_parser.py <path-to-Xen-rate-xlsx>", file=sys.stderr)
        sys.exit(1)
    data = parse_xen_rate_xlsx(Path(sys.argv[1]))
    # Print summary
    print(f"Age bands: {data['age_bands']}")
    print(f"Maternity loadings: {data['maternity_loadings']}")
    print()
    for plan, copays in data["rates"].items():
        for copay, ages in copays.items():
            sample_age = next(iter(ages))
            sample = ages[sample_age]
            print(f"  {plan} / {copay[:60]}... → {sample_age}: M={sample['M']}, F={sample['F']}")
