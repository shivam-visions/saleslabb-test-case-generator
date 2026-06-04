"""T2-BEN rules — benefits*.xlsx structural validation."""
from __future__ import annotations

import re

from .models import (BAD_CHARS, KNOWN_TYPOS, ROW_ANNUAL_LIMIT, ROW_FILTERS,
                     ROW_GEO, ROW_NETWORK, COPAY_ROWS, STARTER_ROWS, USER_TYPES,
                     Severity, cell_str, is_blank)

AL_FORMAT = re.compile(r"^(?:[A-Za-z]{3}\s?[\d,][\d,\.]*|unlimited)$", re.I)
DOLLAR_PLACEHOLDER = re.compile(r"\$\s+co-?pay", re.I)


def plan_columns(sheet) -> list[tuple[int, str]]:
    """(index, header) of plan columns: everything after 'Benefit', minus
    dependentModifiers and blanks."""
    out, seen_benefit = [], False
    for i, h in enumerate(sheet.header):
        hs = h.strip() if isinstance(h, str) else None
        if hs == "Benefit":
            seen_benefit = True
            continue
        if seen_benefit and hs and hs != "dependentModifiers":
            out.append((i, hs))
    return out


def benefit_rows(sheet) -> dict[str, tuple[int, tuple]]:
    """Map of stripped Benefit-column label -> (xlsx row number, raw row)."""
    bi = sheet.col_index("Benefit")
    if bi is None:
        return {}
    rownos = getattr(sheet, "row_numbers", list(range(2, len(sheet.rows) + 2)))
    out = {}
    for r, rn in zip(sheet.rows, rownos):
        label = cell_str(r[bi] if bi < len(r) else None).strip()
        if label and label not in out:
            out[label] = (rn, r)
    return out


def run_benefits(ctx) -> None:
    rate_files = ctx.bundle.by_role("rateSheet")
    for f in ctx.bundle.by_role("benefits"):
        if f.load_error or not f.main():
            continue
        sheet, name, sname = f.main(), f.name, f.main().name
        ut_i, b_i = sheet.col_index("User Type"), sheet.col_index("Benefit")
        plans = plan_columns(sheet)

        if ut_i is None or b_i is None or not plans:
            ctx.add("T2-BEN-001", Severity.ERROR, name,
                    "header must be 'User Type | Benefit | <Plan...>' "
                    f"(got {sheet.header[:6]})", sheet=sname)
            continue

        # plan header whitespace (join key to rateSheet.planName)
        for _, hs in plans:
            raw = next(h for h in sheet.header
                       if isinstance(h, str) and h.strip() == hs)
            if raw != raw.strip() or "\xa0" in raw:
                ctx.add("T2-BEN-009", Severity.ERROR, name,
                        f"plan column header {raw!r} has stray whitespace — "
                        "breaks exact-match join with rateSheet.planName",
                        sheet=sname)

        rows = benefit_rows(sheet)
        rownos = getattr(sheet, "row_numbers", list(range(2, len(sheet.rows) + 2)))

        # T2-BEN-002 — User Type enum
        bad_ut = []
        for r, rn in zip(sheet.rows, rownos):
            ut = cell_str(r[ut_i] if ut_i < len(r) else None).strip()
            if ut and ut not in USER_TYPES:
                bad_ut.append((rn, ut))
        for rn, ut in bad_ut[:10]:
            ctx.add("T2-BEN-002", Severity.ERROR, name,
                    f"User Type '{ut}' not in {sorted(USER_TYPES)}",
                    sheet=sname, row=rn)
        if len(bad_ut) > 10:
            ctx.add("T2-BEN-002", Severity.ERROR, name,
                    f"... and {len(bad_ut) - 10} more invalid User Type rows",
                    sheet=sname)

        # T2-BEN-003 — special rows
        if ROW_ANNUAL_LIMIT not in rows:
            ctx.add("T2-BEN-003", Severity.ERROR, name,
                    "'Annual Limit' row missing", sheet=sname)
        if ROW_GEO not in rows:
            ctx.add("T2-BEN-003", Severity.ERROR, name,
                    "'Geographical Coverage' row missing", sheet=sname)
        if ROW_NETWORK not in rows:
            ctx.add("T2-BEN-003", Severity.WARN, name,
                    "'Network Details' row missing (required when plans are "
                    "network-scoped)", sheet=sname)
        if ROW_FILTERS not in rows:
            ctx.add("T2-BEN-003", Severity.WARN, name,
                    "'Filters' footer header missing", sheet=sname)

        # Copays filter row: ERROR when rateSheet uses multiple copay values
        has_copay_row = any(cr in rows for cr in COPAY_ROWS)
        if not has_copay_row:
            multi_copay = False
            for rf in rate_files:
                if rf.suffix == f.suffix and rf.main():
                    ci = rf.main().col_index("copay")
                    if ci is not None:
                        vals = {cell_str(r[ci] if ci < len(r) else None).strip()
                                for r in rf.main().rows} - {""}
                        multi_copay = len(vals) > 1
            ctx.add("T2-BEN-003",
                    Severity.ERROR if multi_copay else Severity.WARN, name,
                    "no 'Copays' / 'Co-pay/excess' filter row" +
                    (" — rateSheet has multiple copay values; the exact-match "
                     "join will silently fail" if multi_copay else ""),
                    sheet=sname)

        # T2-BEN-004 — Annual Limit cell format per plan
        if ROW_ANNUAL_LIMIT in rows:
            rn, r = rows[ROW_ANNUAL_LIMIT]
            for i, hs in plans:
                v = r[i] if i < len(r) else None
                if is_blank(v):
                    ctx.add("T2-BEN-004", Severity.ERROR, name,
                            f"Annual Limit blank for plan '{hs}'",
                            sheet=sname, row=rn)
                elif isinstance(v, str):
                    if "\n" in v:
                        ctx.add("T2-BEN-004", Severity.ERROR, name,
                                f"Annual Limit for '{hs}' is multi-line — parser "
                                "CastError NaN (must be single-line '<CCY> <number>')",
                                sheet=sname, row=rn)
                    elif not AL_FORMAT.fullmatch(v.strip()):
                        ctx.add("T2-BEN-004", Severity.WARN, name,
                                f"Annual Limit for '{hs}' is {v!r} — expected "
                                "'<CCY> <number>' or 'unlimited'",
                                sheet=sname, row=rn)

        # T2-BEN-005 — Starter rows (benefit modifiers not emitted if absent)
        for sr in sorted(STARTER_ROWS - set(rows)):
            ctx.add("T2-BEN-005", Severity.WARN, name,
                    f"Starter row '{sr}' missing — its benefit modifier will "
                    "not be emitted", sheet=sname)

        # T2-BEN-006 — '$ copay' placeholders need a '$' substitution row
        uses_dollar = any(
            isinstance(c, str) and DOLLAR_PLACEHOLDER.search(c)
            for r in sheet.rows for c in r)
        if uses_dollar and "$" not in rows:
            ctx.add("T2-BEN-006", Severity.ERROR, name,
                    "benefit text uses '$ copay' placeholders but no '$' "
                    "substitution-key row exists", sheet=sname)

        # T2-BEN-007 — known typos / invisible characters
        typo_hits: dict[str, int] = {}
        for r in sheet.rows:
            for c in r:
                if isinstance(c, str):
                    for t in KNOWN_TYPOS:
                        if t in c:
                            typo_hits[t] = typo_hits.get(t, 0) + 1
                    for ch, label in BAD_CHARS.items():
                        if ch in c:
                            typo_hits[label] = typo_hits.get(label, 0) + 1
        for t, n in sorted(typo_hits.items()):
            ctx.add("T2-BEN-007", Severity.WARN, name,
                    f"known defect text '{t}' appears in {n} cell(s)", sheet=sname)

        # T2-BEN-008 — Geographical Coverage must not be bullet-prefixed
        if ROW_GEO in rows:
            rn, r = rows[ROW_GEO]
            for i, hs in plans:
                v = r[i] if i < len(r) else None
                if isinstance(v, str) and ("•" in v or "\n" in v):
                    ctx.add("T2-BEN-008", Severity.ERROR, name,
                            f"Geographical Coverage for '{hs}' contains bullets/"
                            "newlines — convert to slash-separated single line",
                            sheet=sname, row=rn)
