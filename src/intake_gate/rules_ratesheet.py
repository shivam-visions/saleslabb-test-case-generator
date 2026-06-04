"""T2-RATE rules — rateSheet*.xlsx structural validation."""
from __future__ import annotations

import re

from .models import GENDERS, RATESHEET_FREQ, Severity, cell_str, is_blank

REQUIRED_COLS = ["planName", "ageStart", "ageEnd", "rates", "copay", "coverage"]
JOIN_KEY_COLS = ["planName", "copay", "network", "coverage", "residency", "custom"]
CURRENCY_COL = re.compile(r"^[A-Z]{3}$")
ROW_FINDING_CAP = 12


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    n = _num(v)
    return int(n) if n is not None and float(n).is_integer() else None


def _capped(ctx, rule, sev, fname, sheet, msgs):
    for m in msgs[:ROW_FINDING_CAP]:
        ctx.add(rule, sev, fname, m[1], sheet=sheet, row=m[0])
    if len(msgs) > ROW_FINDING_CAP:
        ctx.add(rule, sev, fname,
                f"... and {len(msgs) - ROW_FINDING_CAP} more row(s) with the same issue",
                sheet=sheet)


def run_ratesheet(ctx) -> None:
    currencies = ctx.cfg.get("currencies_list") or []
    for f in ctx.bundle.by_role("rateSheet"):
        if f.load_error or not f.main():
            continue
        sheet = f.main()
        name, sname = f.name, sheet.name
        headers = [h.strip() for h in sheet.header if isinstance(h, str)]

        missing = [c for c in REQUIRED_COLS if c not in headers]
        if missing:
            ctx.add("T2-RATE-001", Severity.ERROR, name,
                    f"required column(s) missing: {', '.join(missing)} — parser "
                    "silently drops unrecognized and silently skips absent joins",
                    sheet=sname)
        if not sheet.rows:
            ctx.add("T2-RATE-001", Severity.ERROR, name, "no data rows", sheet=sname)
            continue

        rows = sheet.dicts()
        rownos = getattr(sheet, "row_numbers", list(range(2, len(rows) + 2)))

        # multi-currency parser-bug workaround (schema §9.9 / CLAUDE.md §12.9)
        if len(currencies) > 1 and not any(h.startswith("rates/") for h in headers):
            ctx.add("T2-RATE-005", Severity.WARN, name,
                    f"info.currencies has {len(currencies)} entries but no "
                    "'rates/<currency>' column — known parser bug yields "
                    "baseAnnualPremium=0 without it (§12.9). Verify intentional "
                    "(rateTable-flow files may differ).", sheet=sname)

        age_bad, rate_bad, zero_warn, ws_bad, bullet_bad, freq_bad, gender_bad, plat_bad = \
            [], [], [], [], [], [], [], []
        groups: dict[tuple, list] = {}
        group_cols = [h for h in headers
                      if h not in ("ageStart", "ageEnd", "rates")
                      and not h.startswith("rates/")
                      and not CURRENCY_COL.fullmatch(h)]

        for r, rn in zip(rows, rownos):
            a0, a1 = _int(r.get("ageStart")), _int(r.get("ageEnd"))
            if "ageStart" in headers and (a0 is None or a1 is None or
                                          not (0 <= a0 <= a1 <= 130)):
                age_bad.append((rn, f"ageStart/ageEnd invalid: "
                                    f"{r.get('ageStart')!r}/{r.get('ageEnd')!r}"))
            rv = _num(r.get("rates"))
            if "rates" in headers:
                if rv is None:
                    rate_bad.append((rn, f"rates not numeric: {r.get('rates')!r}"))
                elif rv < 0:
                    rate_bad.append((rn, f"rates negative: {rv}"))
                elif rv == 0 and cell_str(r.get("copayType")).strip() != "IP":
                    zero_warn.append(rn)
            for col in JOIN_KEY_COLS:
                v = r.get(col)
                if isinstance(v, str) and (v != v.strip() or "\xa0" in v):
                    ws_bad.append((rn, f"{col} has leading/trailing/NBSP "
                                       f"whitespace: {v!r} — breaks exact-match "
                                       "join / deterministic _id"))
            cov = r.get("coverage")
            if isinstance(cov, str) and cov.lstrip().startswith("•"):
                bullet_bad.append((rn, f"coverage is bullet-prefixed: {cov[:40]!r}"))
            fr = r.get("frequency")
            if not is_blank(fr) and str(fr).strip() not in RATESHEET_FREQ:
                freq_bad.append((rn, f"frequency '{fr}' not in {sorted(RATESHEET_FREQ)} "
                                     "(exact case)"))
            g = r.get("gender")
            if not is_blank(g) and str(g).strip() not in GENDERS:
                gender_bad.append((rn, f"gender '{g}' must be lowercase male|female"))
            p = r.get("platform")
            if not is_blank(p) and str(p).strip() != "V2":
                plat_bad.append((rn, f"platform '{p}' — only V2 is parsed"))

            if a0 is not None and a1 is not None:
                key = tuple(cell_str(r.get(c)).strip() for c in group_cols)
                groups.setdefault(key, []).append((a0, a1, rn))

        _capped(ctx, "T2-RATE-002", Severity.ERROR, name, sname, age_bad)
        _capped(ctx, "T2-RATE-004", Severity.ERROR, name, sname, rate_bad)
        if zero_warn:  # aggregated: rateTable-flow products price via parts
            ctx.add("T2-RATE-004", Severity.WARN, name,
                    f"{len(zero_warn)} non-IP row(s) with rates=0 (e.g. rows "
                    f"{zero_warn[:5]}) — expected only for rateTable-flow / "
                    "module-priced products; verify intentional", sheet=sname)
        _capped(ctx, "T2-RATE-009", Severity.ERROR, name, sname, ws_bad)
        _capped(ctx, "T2-RATE-010", Severity.ERROR, name, sname, bullet_bad)
        _capped(ctx, "T2-RATE-007", Severity.ERROR, name, sname, freq_bad)
        _capped(ctx, "T2-RATE-008", Severity.ERROR, name, sname, gender_bad)
        _capped(ctx, "T2-RATE-006", Severity.WARN, name, sname, plat_bad)

        # T2-RATE-003 — age-band continuity per filter group
        overlaps, gaps = [], []
        for key, bands in groups.items():
            bands.sort()
            for (s0, e0, _), (s1, e1, rn1) in zip(bands, bands[1:]):
                if s1 <= e0:
                    overlaps.append((rn1, f"age band {s1}-{e1} overlaps {s0}-{e0} "
                                          f"in group {dict(zip(group_cols, key))}"))
                elif s1 > e0 + 1:
                    gaps.append((rn1, f"age gap {e0 + 1}-{s1 - 1} before band "
                                      f"{s1}-{e1} in group "
                                      f"{ {c: v for c, v in zip(group_cols, key) if v} }"))
        _capped(ctx, "T2-RATE-003", Severity.ERROR, name, sname, overlaps)
        _capped(ctx, "T2-RATE-003", Severity.WARN, name, sname, gaps)

        # T2-RATE-011 — multi-network duplication completeness (heuristic)
        if "network" in headers:
            nets = {cell_str(r.get("network")).strip() for r in rows} - {""}
            if len(nets) > 1:
                per_plan: dict[str, set] = {}
                for r in rows:
                    pn = cell_str(r.get("planName")).strip()
                    nv = cell_str(r.get("network")).strip()
                    if pn and nv:
                        per_plan.setdefault(pn, set()).add(nv)
                partial = {p: sorted(v) for p, v in per_plan.items() if v != nets}
                if partial:
                    ctx.add("T2-RATE-011", Severity.WARN, name,
                            f"plans not duplicated across all networks {sorted(nets)}: "
                            f"{partial} — missing combos throw 'No rates found for "
                            "base key' unless intentionally plan-scoped", sheet=sname)

        # T2-RATE-012 — residency values should be known residency keys
        # (info.residencies cell + runtime 'residencies' sheet). WARN, not
        # ERROR: pilot showed dialects where this column carries other
        # condition values (PROD-1985 FR/MC).
        res_keys = set(ctx.cfg.get("residency_keys") or [])
        if "residency" in headers and res_keys:
            unknown = {cell_str(r.get("residency")).strip() for r in rows} - res_keys - {""}
            if unknown:
                ctx.add("T2-RATE-012", Severity.WARN, name,
                        f"residency value(s) {sorted(unknown)} not among known "
                        f"residency keys {sorted(res_keys)} — verify mapping",
                        sheet=sname)
