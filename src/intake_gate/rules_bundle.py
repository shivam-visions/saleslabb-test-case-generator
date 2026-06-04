"""T1 inventory rules + T2-INFO rules (info.xlsx)."""
from __future__ import annotations

import re
from datetime import date, datetime

from .models import (AGE_CALC_METHODS, FREQ_TOKENS, KNOWN_CURRENCIES, Severity,
                     is_blank, slash_list)

INFO_REQUIRED = ["provider", "residencies", "currentRates", "conversion",
                 "startDate", "currency", "insurerName"]


def run_inventory(ctx) -> None:
    b, cfg = ctx.bundle, ctx.cfg

    for f in b.files:
        if f.load_error:
            ctx.add("T1-000", Severity.ERROR, f.name,
                    f"workbook unreadable: {f.load_error}")

    if not b.one("info"):
        ctx.add("T1-001", Severity.ERROR, "",
                "info.xlsx missing — bundle cannot be interpreted")
        return
    if not b.by_role("rateSheet"):
        ctx.add("T1-002", Severity.ERROR, "", "no rateSheet*.xlsx present")
    if not b.by_role("benefits"):
        ctx.add("T1-003", Severity.ERROR, "", "no benefits*.xlsx present")

    n_rate, n_ben = len(b.by_role("rateSheet")), len(b.by_role("benefits"))
    if n_rate and n_ben and n_rate != n_ben:
        ctx.add("T1-006", Severity.WARN, "",
                f"residency file parity: {n_rate} rateSheet file(s) vs "
                f"{n_ben} benefits file(s)")

    # addons.xlsx required iff info.addons is set (persona §2 deliverables)
    addons_declared = cfg.get("addons_list") or []
    addons_files = b.by_role("addons")
    if addons_declared and not addons_files:
        ctx.add("T1-004", Severity.ERROR, "",
                f"info.addons declares {len(addons_declared)} addon(s) "
                f"({'/'.join(addons_declared[:4])}…) but no addons*.xlsx present")
    if addons_files and not addons_declared:
        ctx.add("T1-004", Severity.WARN, addons_files[0].name,
                "addons.xlsx present but info.addons is empty — addons will be "
                "silently skipped by the parser")
    if addons_declared and addons_files and n_rate > 1 and len(addons_files) != n_rate:
        ctx.add("T1-006", Severity.WARN, "",
                f"multi-residency: {n_rate} rateSheet file(s) but "
                f"{len(addons_files)} addons file(s) — addon labels must exist "
                "in every residency's file")

    # conversion.xlsx required iff multiCurrency includes 'benefits' AND 2+ currencies
    multi = cfg.get("multiCurrency_list") or []
    currencies = cfg.get("currencies_list") or []
    needs_conversion = "benefits" in multi and len(currencies) >= 2
    has_conversion = bool(b.one("conversion"))
    if needs_conversion and not has_conversion:
        ctx.add("T1-005", Severity.ERROR, "",
                "conversion.xlsx required (multiCurrency includes 'benefits', "
                f"currencies={'/'.join(currencies)}) but missing")
    if has_conversion and not needs_conversion:
        ctx.add("T1-005", Severity.INFO, "conversion.xlsx",
                "conversion.xlsx present but not required by info settings")


def _as_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return datetime.strptime(v.strip(), "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def run_info(ctx) -> None:
    f = ctx.bundle.one("info")
    if not f or f.load_error or not f.main() or not f.main().rows:
        if f and not f.load_error:
            ctx.add("T2-INFO-001", Severity.ERROR, f.name,
                    "info.xlsx has no data row")
        return
    cfg, name = ctx.cfg, f.name

    missing = [c for c in INFO_REQUIRED if is_blank(cfg.get(c))]
    # pilot calibration (PROD-1985): the rateTable dialect (template B) ships
    # without currentRates — required only in the standard dialect
    rate_table_flow = bool(cfg.get("rateTable_list"))
    if rate_table_flow and "currentRates" in missing:
        missing.remove("currentRates")
        ctx.add("T2-INFO-001", Severity.WARN, name,
                "currentRates absent (tolerated in the rateTable dialect — "
                "confirm rate-window handling)")
    if missing:
        ctx.add("T2-INFO-001", Severity.ERROR, name,
                f"required info column(s) missing or blank: {', '.join(missing)}")

    prov = cfg.get("provider")
    if isinstance(prov, str) and not re.fullmatch(r"[A-Za-z0-9_]+", prov):
        ctx.add("T2-INFO-008", Severity.WARN, name,
                f"provider '{prov}' is not snake_case — must match the "
                "Inputs/<folderName>/ key exactly")

    cur = cfg.get("currency")
    if not is_blank(cur):
        cs = str(cur).strip()
        if not re.fullmatch(r"[A-Z]{3}", cs):
            ctx.add("T2-INFO-002", Severity.ERROR, name,
                    f"currency '{cs}' is not a 3-letter ISO code")
        elif cs not in KNOWN_CURRENCIES:
            ctx.add("T2-INFO-002", Severity.WARN, name,
                    f"currency '{cs}' not in known set {sorted(KNOWN_CURRENCIES)}")

    sd, ed = cfg.get("startDate"), cfg.get("endDate")
    sdd = _as_date(sd)
    if not is_blank(sd) and sdd is None:
        ctx.add("T2-INFO-003", Severity.ERROR, name,
                f"startDate '{sd}' is not an ISO date (YYYY-MM-DD)")
    if not is_blank(ed):
        edd = _as_date(ed)
        if edd is None:
            ctx.add("T2-INFO-003", Severity.ERROR, name,
                    f"endDate '{ed}' is not an ISO date (YYYY-MM-DD)")
        elif sdd and edd <= sdd:
            ctx.add("T2-INFO-003", Severity.ERROR, name,
                    f"endDate {edd} is not after startDate {sdd}")

    currencies = cfg.get("currencies_list") or []
    for col in ("exchange_premium", "exchange_benefit"):
        if len(currencies) > 1 and not is_blank(cfg.get(col)):
            n = len(slash_list(cfg.get(col)))
            if n != len(currencies):
                ctx.add("T2-INFO-004", Severity.ERROR, name,
                        f"{col} has {n} value(s) but currencies lists "
                        f"{len(currencies)} ({'/'.join(currencies)}) — slash "
                        "lists must align positionally")

    acm = cfg.get("ageCalculationMethod")
    if not is_blank(acm) and str(acm).strip().lower() not in AGE_CALC_METHODS:
        ctx.add("T2-INFO-005", Severity.ERROR, name,
                f"ageCalculationMethod '{acm}' not in advanced|standard|defaultAge")

    cpt = cfg.get("copayTypes")
    if not is_blank(cpt) and not str(cpt).endswith("/"):
        # pilot calibration: PROD-1985 shipped 'IP/OP' without trailing slash
        ctx.add("T2-INFO-006", Severity.WARN, name,
                f"copayTypes '{cpt}' does not end with '/' (schema says it "
                "should; at least one shipped product omits it)")

    bad_freq = [t for t in (cfg.get("frequencies_list") or [])
                if t not in FREQ_TOKENS]
    if bad_freq:
        ctx.add("T2-INFO-007", Severity.ERROR, name,
                f"frequencies token(s) {bad_freq} invalid — allowed (exact case): "
                f"{sorted(FREQ_TOKENS)}")

    conv = cfg.get("conversion")
    if not is_blank(conv):
        try:
            float(conv)
        except (TypeError, ValueError):
            ctx.add("T2-INFO-009", Severity.ERROR, name,
                    f"conversion '{conv}' is not numeric")

    cr = cfg.get("currentRates")
    if not is_blank(cr) and not isinstance(cr, bool) and \
            str(cr).strip().lower() not in {"true", "false", "1", "0"}:
        ctx.add("T2-INFO-010", Severity.WARN, name,
                f"currentRates '{cr}' is not boolean-like")

    # T2-INFO-012 — VAT advisory (G1: PROD-1930 "Remove VAT"). Non-UAE
    # products often need the §12.10 applicableTaxes suppression hand-patch
    # in provider/index.js; the workbooks cannot express it.
    res_keys = cfg.get("residencies_list") or []
    uae_markers = ("AE", "NE_", "Dubai", "AbuDhabi", "DXB", "AUH")
    if res_keys and not any(any(m in k for m in uae_markers) for k in res_keys):
        ctx.add("T2-INFO-012", Severity.INFO, name,
                f"non-UAE residencies ({'/'.join(res_keys[:3])}…): if this "
                "product is VAT-free, provider/index.js needs the "
                "applicableTaxes override (§12.10) — VAT displays by default")
