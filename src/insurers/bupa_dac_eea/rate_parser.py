"""
BUPA DAC EEA rate parser (PROD-1968).

Reads the 5 EEA rate PDFs (one per plan: MM/Select/Premier/Elite/Ultimate)
and returns a rate table keyed by (zone, plan, coverage, age, ip_usd,
op_usd) with USD/GBP/EUR triples per key.

Differences from PROD-1985 Monaco/France parser:
- Single annual frequency (no Monthly/Quarterly per page)
- Page layout = 11 Pricing Zones × per-age, NOT per-deductible columns
- Page-level title encodes (plan, currency, deductible, coverage)
- Zone 1 (Incl US) ALWAYS represents the "Worldwide" rate
- Zones 2-11 carry the page-labeled coverage's rate tier

Known issues encoded as warnings:
- Duplicate-title pages in EUR/USD halves (Nishma's 2026-05-19 comment)
- Mislabeled "WW" titles that are actually WWExclUS content (Nishma's
  2026-05-07 note)

Returns:
    {
      "rates": {
        (zone, plan, coverage, age, ip_usd, op_usd, frequency): {
          "USD": float, "GBP": float, "EUR": float,
        },
        ...
      },
      "warnings": [str, ...],
      "raw_pages": [{"plan", "currency", "ip_currency_native", "ip_usd",
                     "op_currency_native", "op_usd", "coverage_label",
                     "pdf", "page_no", "rates_by_zone_age"}, ...]
    }
"""

import re
import subprocess
from collections import defaultdict
from pathlib import Path


# --- Mapping tables -------------------------------------------------------

PLAN_FILE_PATTERN = {
    "Major Medical": "BGDAC EEA BGHP 2.0 Major Medical Rates - April 2026.pdf",
    "Select":        "BGDAC EEA BGHP 2.0 Select Rates - April 2026.pdf",
    "Premier":       "BGDAC EEA BGHP 2.0 Premier Rates - April 2026.pdf",
    "Elite":         "BGDAC EEA BGHP 2.0 Elite Rates - April 2026.pdf",
    "Ultimate":      "BGDAC EEA BGHP 2.0 Ultimate Rates - April 2026.pdf",
}

# Reuse Monaco/France's USD-canonical deductible mapping
DEDUCTIBLE_USD_CANONICAL = {
    "USD": {0: 0, 425: 425, 850: 850, 1700: 1700, 3400: 3400, 8500: 8500, 12750: 12750, 22550: 22550},
    "GBP": {0: 0, 250: 425, 500: 850, 1000: 1700, 2000: 3400, 5000: 8500, 7500: 12750, 10000: 22550},
    "EUR": {0: 0, 330: 425, 625: 850, 1250: 1700, 2500: 3400, 6250: 8500, 9400: 12750, 12500: 22550},
}

# Zone count per page
N_ZONES = 11

# Coverage normalization. We define three logical coverages:
#   - "Worldwide" (= Zone 1 of any WWExclUS page; Zone 1 of WW-labeled MM pages)
#   - "Worldwide excluding USA" (= Zones 2-11 of WWExclUS-labeled pages)
#   - "Europe (Including UK)" (= Zones 2-11 of Europe-labeled pages)
COVERAGE_WW = "Worldwide"
COVERAGE_WWX = "Worldwide excluding USA"
COVERAGE_EUROPE = "Europe (Including UK)"

# Title regexes
# Major Medical: "Major Medical GBP No IP Deductible WWExclUS - Annual Rates"
#                "Major Medical USD 625 IP Deductible WW - Annual Rates"
MM_TITLE_RE = re.compile(
    r"^Major Medical\s+(USD|GBP|EUR)\s+(No|\d+)\s+IP\s+Deductible\s+(WWExclUS|WW|Europe)\s*-\s*Annual\s+Rates\s*$",
    re.IGNORECASE,
)

# Select/Premier/Elite (IP+OP): "Select OP GBP 250 - IP 500 - Europe - Annual Rates"
#                                "Select OP No Deductible - WWExclUS - Annual Rates"
#                                "Select OP USD No Deductible - Europe - Annual Rates"
#                                "Select OP EUR No Deductible - Europe - Annual Rates"
IPOP_FULL_RE = re.compile(
    r"^(Select|Premier|Elite|Ultimate)\s+OP\s+(USD|GBP|EUR)\s+(\d+)\s*-\s*IP\s+(\d+)\s*-\s*(Europe|WWExclUS|WW)\s*-\s*Annual\s+Rates\s*$",
    re.IGNORECASE,
)
# No-deductible variants (NIL/NIL combo)
IPOP_NIL_RE = re.compile(
    r"^(Select|Premier|Elite|Ultimate)\s+OP\s+(?:(USD|GBP|EUR)\s+)?No\s+Deductible\s*-\s*(Europe|WWExclUS|WW)\s*-\s*Annual\s+Rates\s*$",
    re.IGNORECASE,
)

# Age token: "0-4" or "85+" or "30"
AGE_RE = re.compile(r"^(\d+(?:-\d+)?|\d+\+)$")
NUM_RE = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")


def _parse_age_token(tok):
    t = str(tok).strip()
    if t.endswith("+"):
        return [int(t[:-1])]
    if "-" in t:
        a, b = t.split("-", 1)
        return list(range(int(a), int(b) + 1))
    return [int(t)]


def _pdftotext(pdf_path):
    return subprocess.check_output(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        text=True, errors="replace",
    )


def _parse_zone_row(line, expected_cols=N_ZONES):
    """Given a data line like '30    3333.85    2539.66 ... 1804.21', return
    (age_token, [zone1, zone2, ..., zone11]) or (None, None)."""
    toks = line.split()
    if not toks:
        return None, None
    # Find the age token (first AGE_RE match)
    age_pos = None
    for i, t in enumerate(toks):
        if AGE_RE.match(t):
            age_pos = i
            break
    if age_pos is None:
        return None, None
    age_tok = toks[age_pos]
    rest = toks[age_pos + 1:]
    vals = []
    for t in rest:
        if NUM_RE.fullmatch(t):
            vals.append(float(t.replace(",", "")))
        elif t == "#######" or re.fullmatch(r"#+", t):
            vals.append(None)
        if len(vals) >= expected_cols:
            break
    if len(vals) < expected_cols:
        # Pad with None — caller will warn if too short
        vals = vals + [None] * (expected_cols - len(vals))
    return age_tok, vals


def _parse_page(page_text, plan_hint, pdf_name, page_no):
    """Parse one PDF page → returns dict or None if not a rate page."""
    lines = [l for l in page_text.split("\n") if l.strip()]
    if not lines:
        return None
    title = lines[0].strip()

    m_mm = MM_TITLE_RE.match(title)
    m_full = IPOP_FULL_RE.match(title)
    m_nil = IPOP_NIL_RE.match(title)

    if m_mm:
        plan = "Major Medical"
        currency = m_mm.group(1).upper()
        ip_raw = m_mm.group(2)
        ip_native = 0 if ip_raw.lower() == "no" else int(ip_raw)
        op_native = None  # MM is IP-only
        coverage_label = m_mm.group(3)
    elif m_full:
        plan = m_full.group(1)
        currency = m_full.group(2).upper()
        op_native = int(m_full.group(3))
        ip_native = int(m_full.group(4))
        coverage_label = m_full.group(5)
    elif m_nil:
        plan = m_nil.group(1)
        # Currency: explicit (group 2) OR inferred. For Select/Premier/Elite,
        # the "USD No Deductible" and "EUR No Deductible" cases have explicit
        # currency, while "OP No Deductible" (the unprefixed variant) appears
        # to be in GBP per page sequence in raw PDFs.
        cur_match = m_nil.group(2)
        currency = cur_match.upper() if cur_match else "GBP"
        ip_native = 0
        op_native = 0
        coverage_label = m_nil.group(3)
    else:
        return None

    # Normalize coverage label per Zone-1 semantics:
    # - Page tagged "WW" → Zone 1 is true WW; Zones 2-11 are WWX (per Nishma's note)
    # - Page tagged "WWExclUS" → Zone 1 is true WW; Zones 2-11 are WWX
    # - Page tagged "Europe" → Zone 1 is Europe+US, Zones 2-11 are Europe
    if coverage_label.lower() in ("ww", "wwexclus"):
        zone_label = "WWX-region"
    elif coverage_label.lower() == "europe":
        zone_label = "Europe-region"
    else:
        zone_label = coverage_label

    # Convert native deductibles to USD canonical
    ded_map = DEDUCTIBLE_USD_CANONICAL.get(currency, {})
    ip_usd = ded_map.get(ip_native, ip_native)
    op_usd = ded_map.get(op_native, op_native) if op_native is not None else None

    # Parse data lines
    rates_by_zone_age = {}
    for line in lines[1:]:
        # Skip header lines (Zone 1, Zone 2, etc.)
        if "Zone" in line and "Incl" not in line and not AGE_RE.match(line.split()[0] if line.split() else ""):
            continue
        if line.strip().startswith("US)"):
            continue
        age_tok, vals = _parse_zone_row(line)
        if age_tok is None:
            continue
        ages = _parse_age_token(age_tok)
        for age in ages:
            for zone_idx, val in enumerate(vals, start=1):
                if val is not None:
                    rates_by_zone_age[(zone_idx, age)] = val

    return {
        "plan": plan, "currency": currency,
        "ip_native": ip_native, "ip_usd": ip_usd,
        "op_native": op_native, "op_usd": op_usd,
        "coverage_label": coverage_label,
        "zone_label": zone_label,
        "pdf": pdf_name, "page_no": page_no,
        "rates_by_zone_age": rates_by_zone_age,
        "raw_title": title,
    }


def _post_process_duplicate_titles(raw_pages, warnings):
    """Disambiguate duplicate page titles (the bug Nishma flagged 2026-05-19).

    Within a single plan, when two consecutive pages share the same title
    (e.g. 'Select OP EUR 330 - IP 625 - Europe - Annual Rates' appears twice),
    the first occurrence is the genuine "Europe" page and the second is
    actually the "WWExclUS" page mislabeled as "Europe".

    This matches the empirical observation:
    - Select WWExclUS Zone 8 age 30 = 3385.09 (from genuinely-labeled P52)
    - Higher than Europe (smaller geographic coverage → cheaper)
    - The duplicate page rates are HIGHER than the first instance → WWExclUS

    Mutates raw_pages in place: flips coverage_label/zone_label on duplicates.
    """
    # Group by (plan, raw_title); within each group, if 2+ pages,
    # the 2nd+ are flipped to WWExclUS.
    from collections import defaultdict as _dd
    groups = _dd(list)
    for p in raw_pages:
        groups[(p["plan"], p["raw_title"])].append(p)
    for (plan, title), pages_with_title in groups.items():
        if len(pages_with_title) < 2:
            continue
        # Only flip Europe-labeled duplicates (the documented bug pattern)
        first = pages_with_title[0]
        if first["zone_label"] != "Europe-region":
            continue
        for dup in pages_with_title[1:]:
            warnings.append(
                f"{dup['pdf']} P{dup['page_no']}: duplicate-title detected — "
                f"reinterpreting as WWExclUS (Nishma 2026-05-19 mislabel). "
                f"Original title: {title!r}"
            )
            dup["coverage_label"] = "WWExclUS"
            dup["zone_label"] = "WWX-region"


def _infer_currency_from_sequence(raw_pages):
    """For 'OP No Deductible' pages that lack explicit currency in the title,
    infer based on the page sequence within the same PDF: the first one is
    GBP, the second EUR, the third USD (matches the file convention).
    Mutates raw_pages."""
    from collections import defaultdict as _dd
    grouped_by_pdf = _dd(list)
    for p in raw_pages:
        grouped_by_pdf[p["pdf"]].append(p)
    for pdf, plist in grouped_by_pdf.items():
        plist.sort(key=lambda x: x["page_no"])
        # We track running currency sections: GBP → EUR → USD
        currency_order = ["GBP", "EUR", "USD"]
        last_currency = None
        currency_visited = set()
        for p in plist:
            if p["currency"] in ("USD", "GBP", "EUR"):
                last_currency = p["currency"]
                currency_visited.add(last_currency)
        # Now, for any page that was originally inferred as GBP (default for no-currency
        # OP-No-Deductible), see if it actually appears AFTER the GBP block in sequence
        # and re-label.
        # We do this by tracking which section we're in based on neighbor pages.
        for i, p in enumerate(plist):
            # Heuristic: a NIL-NIL page (ip_native=0 op_native=0) with empty
            # explicit currency carries the currency of the preceding rate page
            # in the same PDF.
            if p["ip_native"] != 0 or p["op_native"] != 0:
                continue
            # Look backward for the most recent explicit-currency page
            preceding_currency = None
            for j in range(i - 1, -1, -1):
                preceding_currency = plist[j]["currency"]
                break
            if preceding_currency and preceding_currency != p["currency"]:
                # Only override if the parser had defaulted to GBP without confidence
                # i.e. our regex inferred GBP. We check by the title not containing the
                # explicit currency name.
                if not any(c in p["raw_title"] for c in ("USD", "GBP", "EUR")):
                    p["currency"] = preceding_currency


def parse_bupa_dac_eea_rates(ticket_folder: Path) -> dict:
    """Walk all 5 EEA PDFs under ticket_folder/raw/rates/ and return rate table."""
    raw_root = ticket_folder / "raw" / "rates"
    if not raw_root.is_dir():
        raise FileNotFoundError(f"Rate folder not found: {raw_root}")

    long_rates = defaultdict(lambda: {"USD": None, "GBP": None, "EUR": None})
    warnings = []
    raw_pages = []

    # Track which titles we've seen to detect duplicates per Nishma's comment
    seen_titles_by_plan = defaultdict(list)

    for plan, fname in PLAN_FILE_PATTERN.items():
        pdf_path = raw_root / fname
        if not pdf_path.is_file():
            warnings.append(f"Missing PDF for {plan}: {fname}")
            continue
        full_text = _pdftotext(pdf_path)
        pages = full_text.split("\f")
        for page_no, page_text in enumerate(pages, start=1):
            section = _parse_page(page_text, plan, fname, page_no)
            if section is None:
                continue
            raw_pages.append(section)
            seen_titles_by_plan[plan].append(section["raw_title"])

    # Post-process pages
    _infer_currency_from_sequence(raw_pages)
    _post_process_duplicate_titles(raw_pages, warnings)
    # Recompute IP/OP USD canonical (currency may have changed)
    for p in raw_pages:
        ded_map = DEDUCTIBLE_USD_CANONICAL.get(p["currency"], {})
        p["ip_usd"] = ded_map.get(p["ip_native"], p["ip_native"])
        if p["op_native"] is not None:
            p["op_usd"] = ded_map.get(p["op_native"], p["op_native"])

    for section in raw_pages:
        # Convert section to long-form rate entries.
        #
        # Zone semantics validated against insurer-confirmed values
        # (PROD-1968 comments, 2026-05-20):
        #   * On a WWExclUS-titled page: Zone 1 (Incl US) = "Worldwide".
        #     E.g. Select NIL/NIL EUR Zone 1 = €7,257.02 = insurer's WW.
        #   * On a WWExclUS-titled page: Zones 2-11 = "Worldwide excl. USA"
        #     pricing-zone tiers.
        #   * On a Europe-titled page: Zones 2-11 = "Europe (Including UK)"
        #     pricing-zone tiers. Zone 1 of Europe-titled pages is
        #     "Europe + USA" (a hybrid tier) — not in the IPRB list of
        #     sellable coverages, so we DROP it to avoid Worldwide-key
        #     collisions with the WWExclUS page.
        for (zone_idx, age), val in section["rates_by_zone_age"].items():
            if section["zone_label"] == "Europe-region":
                if zone_idx == 1:
                    continue  # see comment above — drop hybrid tier
                coverage = COVERAGE_EUROPE
            else:
                # WWX-region page
                if zone_idx == 1:
                    coverage = COVERAGE_WW
                else:
                    coverage = COVERAGE_WWX

            # Frequency is "Annually" for EEA
            # Note: a same (zone, plan, coverage, age, ip, op) may be hit
            # multiple times due to duplicate-title pages — keep the FIRST
            # value seen and warn on the second.
            key = (zone_idx, section["plan"], coverage, age,
                   section["ip_usd"], section["op_usd"], "Annually")
            if long_rates[key][section["currency"]] is not None:
                existing = long_rates[key][section["currency"]]
                if abs(existing - val) > 0.5:
                    warnings.append(
                        f"{section['pdf']} P{section['page_no']}: duplicate key "
                        f"{key} {section['currency']} — existing {existing}, "
                        f"new {val} (kept existing)"
                    )
            else:
                long_rates[key][section["currency"]] = val

    # Detect outright duplicate page titles per plan (Nishma's flagged issue)
    for plan, titles in seen_titles_by_plan.items():
        from collections import Counter
        ctr = Counter(titles)
        for title, count in ctr.items():
            if count > 1:
                warnings.append(f"{plan}: duplicate page title ({count}x): {title!r}")

    return {
        "rates": dict(long_rates),
        "warnings": warnings,
        "raw_pages": raw_pages,
        "plans_seen": sorted(seen_titles_by_plan.keys()),
    }


if __name__ == "__main__":
    import sys
    import json
    if len(sys.argv) != 2:
        print("Usage: python rate_parser.py <ticket-folder-path>", file=sys.stderr)
        sys.exit(1)
    data = parse_bupa_dac_eea_rates(Path(sys.argv[1]))
    print(f"Plans seen: {data['plans_seen']}")
    print(f"Raw pages: {len(data['raw_pages'])}")
    print(f"Rate keys: {len(data['rates'])}")
    print(f"Warnings: {len(data['warnings'])}")
    for w in data['warnings'][:20]:
        print(f"  - {w}")
    if len(data['warnings']) > 20:
        print(f"  ... and {len(data['warnings'])-20} more")
