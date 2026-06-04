"""
BUPA DAC Monaco/France rate parser.

Adapted from the existing build_rateSheet.py in the PROD-1985 ticket folder.
Reads the 10 BUPA DAC rate PDFs (5 plans × France + Monaco) and returns a
queryable rate table keyed by (residency, plan, coverage, age, ip_usd, op_usd,
frequency).

Per the persona file (auto-onboarding/PROD-1985-.../CLAUDE.md):
- Major Medical is IP-only; OP deductible is None
- Select/Premier/Elite/Ultimate are IP+OP plans
- NIL/NIL is one combo; non-NIL is N_IP × N_OP combos per plan
- Three currencies on the rate: USD primary, GBP + EUR alternates
- Three frequencies per (plan × age × deductible): Monthly / Quarterly / Annual
- France-Rates folder has older "1st Euro France" PDFs for MM/Select/Premier/Elite + the Monaco-style Ultimate PDF
- Monaco-Rates has all 5 plans on the latest "France & Monaco BGHP 2.0" PDFs

Returns:
    {
      "rates": {
        (residency, plan, coverage, age, ip_usd, op_usd, frequency): {
          "USD": float, "GBP": float, "EUR": float,
        },
        ...
      },
      "warnings": [str, ...]
    }
"""

import re
import subprocess
from collections import defaultdict
from pathlib import Path


PLAN_NAME_MAP = {
    "MajorMedical": "Major Medical", "Major Medical": "Major Medical",
    "M.M": "Major Medical", "Major  Medical": "Major Medical",
    "Select": "Select", "Premier": "Premier", "Elite": "Elite", "Ultimate": "Ultimate",
}
COVER_MAP = {
    "WWExcl.US": "Worldwide excluding USA",
    "WWIncl.US": "Worldwide",
    "Europe": "Europe (Including UK)",
}
DEDUCTIBLE_USD_CANONICAL = {
    "USD": {0: 0, 425: 425, 850: 850, 1700: 1700, 3400: 3400, 8500: 8500, 12750: 12750, 22550: 22550},
    "GBP": {0: 0, 250: 425, 500: 850, 1000: 1700, 2000: 3400, 5000: 8500, 7500: 12750, 10000: 22550},
    "EUR": {0: 0, 330: 425, 625: 850, 1250: 1700, 2500: 3400, 6250: 8500, 9400: 12750, 12500: 22550},
}
FREQ_MAP = {"Monthly": "month", "Quarterly": "quarter", "Annual": "Annually"}

TITLE_RE = re.compile(
    r"(?:France\s*&\s*Monaco|1st\s+Euro\s+France)\s+"
    r"(.+?)\s+"
    r"(Europe|WWExcl\.US|WWIncl\.US)\s+"
    r"Subscriptions\s*-\s*From\s+\d+\s+\w+\s+\d{4}\s*-\s*"
    r"(USD|GBP|EUR)",
    re.IGNORECASE,
)
NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
AGE_RE = re.compile(r"^(\d+(?:-\d+)?|\d+\+)$")
HEADER_GROUP_RE = re.compile(
    r"(\d+)\s*IP\s*&\s*(\d+)\s*OP|"
    r"(\d+)\s*Deductible|"
    r"(No\s+Deductible)"
)
MAX_GROUPS_PER_PAGE = {
    "Major Medical": 5,
    "Select": 3, "Premier": 3, "Elite": 3,
    "Ultimate": 1,
}


def _parse_age_token(tok):
    t = str(tok).strip()
    if t.endswith("+"):
        return [int(t[:-1])]
    if "-" in t:
        a, b = t.split("-", 1)
        return list(range(int(a), int(b) + 1))
    return [int(t)]


def _parse_header_line(line):
    out = []
    for m in HEADER_GROUP_RE.finditer(line):
        if m.group(1) and m.group(2):
            out.append((int(m.group(1)), int(m.group(2))))
        elif m.group(3):
            out.append((int(m.group(3)), None))
        elif m.group(4):
            out.append(("NIL", "NIL"))
    return out


def _normalize_groups(groups, plan_is_ip_only):
    fixed = []
    for g in groups:
        if g == ("NIL", "NIL"):
            fixed.append((0, None) if plan_is_ip_only else (0, 0))
        else:
            fixed.append(g)
    return fixed


def _pdftotext(pdf_path):
    return subprocess.check_output(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        text=True, errors="replace",
    )


def _split_pages(text):
    return text.split("\f")


def _extract_section(page_text):
    lines = page_text.split("\n")
    title_idx = None
    for i, ln in enumerate(lines[:8]):
        if TITLE_RE.search(ln):
            title_idx = i
            break
    if title_idx is None:
        return None
    title = lines[title_idx]
    m = TITLE_RE.search(title)
    plan_raw = m.group(1).strip()
    cover_raw = m.group(2).strip()
    currency = m.group(3).strip().upper()
    plan = PLAN_NAME_MAP.get(plan_raw, plan_raw)
    cover = COVER_MAP[cover_raw]

    groups = None
    header_line_idx = None
    for i in range(title_idx + 1, min(len(lines), title_idx + 8)):
        candidates = _parse_header_line(lines[i])
        if candidates:
            groups = candidates
            header_line_idx = i
            break
    if not groups:
        return None
    plan_is_ip_only = (plan == "Major Medical")
    groups = _normalize_groups(groups, plan_is_ip_only)
    max_g = MAX_GROUPS_PER_PAGE.get(plan, 5)
    if len(groups) > max_g:
        groups = groups[-max_g:]

    n_groups = len(groups)
    n_freqs = 3
    expected = n_groups * n_freqs
    age_to_vals = {}
    for ln in lines[header_line_idx + 1:]:
        toks = ln.split()
        if not toks:
            continue
        age_pos = None
        for i, t in enumerate(toks):
            if AGE_RE.match(t):
                age_pos = i
                break
        if age_pos is None:
            continue
        age_tok = toks[age_pos]
        rest = toks[age_pos + 1:]
        slots = [None] * expected
        idx = 0
        for t in rest:
            if idx >= expected:
                break
            if t == "#######" or re.fullmatch(r"#+", t):
                idx += 1
            elif NUM_RE.fullmatch(t):
                slots[idx] = float(t.replace(",", ""))
                idx += 1
        if idx == 0:
            continue
        # Sibling recovery
        for gi in range(n_groups):
            base = gi * 3
            mm, qq, aa = slots[base], slots[base + 1], slots[base + 2]
            if aa is None and qq is not None: aa = round(qq * 4, 2)
            if aa is None and mm is not None: aa = round(mm * 12, 2)
            if qq is None and aa is not None: qq = round(aa / 4, 2)
            if qq is None and mm is not None: qq = round(mm * 3, 2)
            if mm is None and aa is not None: mm = round(aa / 12, 2)
            if mm is None and qq is not None: mm = round(qq / 3, 2)
            slots[base], slots[base + 1], slots[base + 2] = mm, qq, aa
        if any(v is None for v in slots):
            continue
        ages = _parse_age_token(age_tok)
        for age in ages:
            age_to_vals[age] = slots
    return plan, cover, currency, groups, age_to_vals


def _residency_for(folder_name):
    return {"Monaco-Rates": "MC", "France-Rates": "FR"}[folder_name]


def parse_bupa_dac_rates(ticket_folder: Path) -> dict:
    """
    Walk all 10 PDFs under ticket_folder/raw/{France,Monaco}-Rates/ and return
    a long-form rate table keyed by (residency, plan, coverage, age, ip_usd,
    op_usd, frequency) with USD/GBP/EUR values per key.
    """
    raw_root = ticket_folder / "raw"
    long_rates = defaultdict(lambda: {"USD": None, "GBP": None, "EUR": None})
    warnings = []
    plans_seen = set()
    coverages_seen = set()

    for sub in ("France-Rates", "Monaco-Rates"):
        sub_dir = raw_root / sub
        if not sub_dir.is_dir():
            continue
        residency = _residency_for(sub)
        for pdf in sorted(sub_dir.glob("*.pdf")):
            full_text = _pdftotext(pdf)
            for page in _split_pages(full_text):
                section = _extract_section(page)
                if section is None:
                    continue
                plan, cover, currency, groups, age_to_vals = section
                plans_seen.add(plan)
                coverages_seen.add(cover)
                ded_map = DEDUCTIBLE_USD_CANONICAL.get(currency, {})
                norm_groups = []
                for ip_n, op_n in groups:
                    ip_u = ded_map.get(ip_n) if ip_n is not None else None
                    if ip_u is None and ip_n is not None:
                        warnings.append(f"{pdf.name}: unknown IP {ip_n} {currency}")
                        ip_u = ip_n
                    op_u = ded_map.get(op_n) if op_n is not None else None
                    if op_u is None and op_n is not None and op_n != 0:
                        warnings.append(f"{pdf.name}: unknown OP {op_n} {currency}")
                        op_u = op_n
                    norm_groups.append((ip_u, op_u))
                for age, vals in age_to_vals.items():
                    for gi, (ip_usd, op_usd) in enumerate(norm_groups):
                        for fi, freq_label in enumerate(("Monthly", "Quarterly", "Annual")):
                            idx = gi * 3 + fi
                            if idx >= len(vals):
                                continue
                            if vals[idx] is None:
                                continue
                            rs_freq = FREQ_MAP[freq_label]
                            key = (residency, plan, cover, age, ip_usd, op_usd, rs_freq)
                            long_rates[key][currency] = vals[idx]

    return {
        "rates": dict(long_rates),
        "warnings": warnings,
        "plans_seen": sorted(plans_seen),
        "coverages_seen": sorted(coverages_seen),
    }


if __name__ == "__main__":
    import sys
    import json
    if len(sys.argv) != 2:
        print("Usage: python rate_parser.py <ticket-folder-path>", file=sys.stderr)
        sys.exit(1)
    data = parse_bupa_dac_rates(Path(sys.argv[1]))
    print(f"Plans: {data['plans_seen']}")
    print(f"Coverages: {data['coverages_seen']}")
    print(f"Rate keys: {len(data['rates'])}")
    print(f"Warnings: {len(data['warnings'])}")
    # Spot-check one
    sample_key = next(iter(data["rates"]))
    print(f"\nSample key: {sample_key}")
    print(f"  Values: {data['rates'][sample_key]}")
