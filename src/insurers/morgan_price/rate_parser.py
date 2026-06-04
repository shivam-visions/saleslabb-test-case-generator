"""
Deterministic PDF rate parser for Morgan Price Flexible Choices.

Reads the rate PDF and produces a typed rate table keyed by (area, age_band, column).
Uses `pdftotext -layout` (Poppler) — the same approach documented in CLAUDE.md §10.

Output shape:
    {
        "Area 1": {
            "Child":     {"core_ip": 643.40, "op_module_1": 321.70, ...},
            "18 to 24":  {"core_ip": 897.49, "op_module_1": 448.74, ...},
            ...
        },
        "Area 2": {...},
        "Area 3": {...},
    }
"""

import re
import subprocess
from pathlib import Path
from typing import Dict


# Column order in the rate PDF (left to right after the age column).
# These are the 9 module columns; "Optional" wording is dropped for clean keys.
RATE_COLUMNS = [
    "core_ip",
    "op_module_1",
    "op_module_2",
    "dental",
    "maternity",
    "enhanced_network",
    "optional_coinsure",
    "reduce_si",
    "increase_si",
]

AGE_BANDS = [
    "Child",
    "18 to 24",
    "25 to 29",
    "30 to 34",
    "35 to 39",
    "40 to 44",
    "45 to 49",
    "50 to 54",
    "55 to 59",
    "60 to 64",
    "65 to 69",
    "70 to 74",
]

AREA_NAMES = {
    "Area 1": "Bangladesh, Brunei, Cambodia, East Timor, India, Indonesia, Laos, Malaysia, Myanmar, Pakistan, Papua New Guinea, Philippines, Sri Lanka, Vietnam",
    "Area 2": "Worldwide excluding USA, China, Hong Kong, Singapore",
    "Area 3": "Worldwide excluding USA",
}

# Per the persona §16.16: Area 4 was intentionally withdrawn during the planning
# call. Do NOT include Area 4 rates even though they appear in the PDF.
AREAS_TO_EXTRACT = ["Area 1", "Area 2", "Area 3"]


def _pdftotext_layout(pdf_path: Path) -> str:
    """Run `pdftotext -layout` and return the captured text."""
    result = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _parse_money(token: str):
    """Convert '1,059.94' or '-67.31' or '-' to a float or None."""
    t = token.strip()
    if t == "-" or t == "":
        return None
    return float(t.replace(",", ""))


def _parse_area_page(area_text: str) -> Dict[str, Dict[str, float]]:
    """
    Given the text of one Area page, return rates per age band.

    Strategy: locate each AGE_BAND label, then read the 9 numeric tokens on
    the same line (handling '-' for N/A and negatives for Reduce SI).
    """
    table: Dict[str, Dict[str, float]] = {}

    # The token regex matches: 1,234.56 or 643.40 or -67.31 or - (placeholder)
    token_re = re.compile(r"-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-")

    for band in AGE_BANDS:
        # Each band line starts with the band label. "Child" is one token;
        # the others are like "18 to 24". Anchor on the literal label.
        band_re = re.compile(rf"^{re.escape(band)}\s+(.+)$", re.MULTILINE)
        match = band_re.search(area_text)
        if not match:
            raise ValueError(f"Age band '{band}' not found in area page")

        rest_of_line = match.group(1)
        tokens = token_re.findall(rest_of_line)

        # Filter out tokens that are too short to be money (e.g. single '-'
        # is valid as N/A; but stray dashes from formatting we ignore).
        # We expect exactly 9 numeric/placeholder tokens.
        money_tokens = [t for t in tokens if t == "-" or any(c.isdigit() for c in t)]

        if len(money_tokens) != len(RATE_COLUMNS):
            raise ValueError(
                f"Area band '{band}': expected {len(RATE_COLUMNS)} rate tokens, "
                f"got {len(money_tokens)}: {money_tokens}"
            )

        table[band] = {
            col: _parse_money(tok)
            for col, tok in zip(RATE_COLUMNS, money_tokens)
        }

    return table


def parse_rate_pdf(pdf_path: Path) -> Dict[str, Dict[str, Dict[str, float]]]:
    """
    Top-level entry point. Returns:
        { "Area 1": {age_band: {column: value}}, "Area 2": {...}, "Area 3": {...} }

    Raises ValueError if the PDF shape doesn't match expectations.
    """
    full_text = _pdftotext_layout(pdf_path)

    # Split on the literal "Area N" headers. The first segment is the cover
    # page; we discard it. Each subsequent segment is one Area page.
    area_split_re = re.compile(r"^Area (\d+)\s*$", re.MULTILINE)
    segments = area_split_re.split(full_text)
    # split() with a captured group returns [pre, area_num1, body1, area_num2, body2, ...]

    if len(segments) < 3:
        raise ValueError("PDF did not contain any 'Area N' page headers")

    result: Dict[str, Dict[str, Dict[str, float]]] = {}
    # Iterate captured pairs (area_num, body).
    for i in range(1, len(segments), 2):
        area_num = segments[i]
        body = segments[i + 1]
        area_key = f"Area {area_num}"
        if area_key not in AREAS_TO_EXTRACT:
            continue
        result[area_key] = _parse_area_page(body)

    missing = [a for a in AREAS_TO_EXTRACT if a not in result]
    if missing:
        raise ValueError(f"Missing expected areas in PDF: {missing}")

    return result


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) != 2:
        print("Usage: python pdf_rate_parser.py <path-to-rate-pdf>", file=sys.stderr)
        sys.exit(1)

    rates = parse_rate_pdf(Path(sys.argv[1]))
    print(json.dumps(rates, indent=2))
