"""Bundle discovery and workbook loading (openpyxl read-only, ghost-row safe)."""
from __future__ import annotations

import re
from pathlib import Path

from openpyxl import load_workbook

from .models import Bundle, Sheet, WorkbookFile, slash_list

ROLE_PATTERNS = {
    "info": re.compile(r"^info(\d*)\.xlsx$", re.I),
    "rateSheet": re.compile(r"^rateSheet(\d*)\.xlsx$", re.I),
    "benefits": re.compile(r"^benefits(\d*)\.xlsx$", re.I),
    "addons": re.compile(r"^addons(\d*)\.xlsx$", re.I),
    "conversion": re.compile(r"^conversion(\d*)\.xlsx$", re.I),
}

# stop reading a sheet after this many consecutive empty rows (ghost-row guard;
# PROD-1985 benefits.xlsx reports max_row=1048576)
EMPTY_RUN_LIMIT = 100
MAX_DATA_ROWS = 200_000


def _load_sheet(ws) -> Sheet:
    header: list = []
    rows: list = []
    empty_run = 0
    for i, row in enumerate(ws.iter_rows(values_only=True), start=1):
        if i == 1:
            header = list(row)
            continue
        if all(v is None or (isinstance(v, str) and v.strip() == "") for v in row):
            empty_run += 1
            if empty_run >= EMPTY_RUN_LIMIT:
                break
            continue
        # backfill skipped blank rows are irrelevant for validation; we keep
        # only non-empty rows but remember their xlsx row numbers via padding
        if empty_run:
            empty_run = 0
        rows.append((i, row))
        if len(rows) >= MAX_DATA_ROWS:
            break
    sheet = Sheet(name=ws.title, header=header, rows=[r for _, r in rows])
    sheet.row_numbers = [n for n, _ in rows]  # type: ignore[attr-defined]
    return sheet


def load_file(path: Path, role: str, suffix: str) -> WorkbookFile:
    wf = WorkbookFile(path=path, role=role, suffix=suffix)
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            wf.sheets = [_load_sheet(ws) for ws in wb.worksheets]
        finally:
            wb.close()
    except Exception as e:  # unreadable / corrupt / not xlsx
        wf.load_error = f"{type(e).__name__}: {e}"
    return wf


def discover(folder: str | Path) -> Bundle:
    root = Path(folder)
    bundle = Bundle(root=root)
    for p in sorted(root.iterdir()) if root.is_dir() else []:
        if not p.is_file():
            continue
        for role, pat in ROLE_PATTERNS.items():
            m = pat.match(p.name)
            if m:
                bundle.files.append(load_file(p, role, m.group(1)))
                break
    return bundle


def info_config(bundle: Bundle) -> dict:
    """Parse info.xlsx first sheet (header row + single value row) into a config
    dict, with slash-lists pre-split under *_list keys."""
    f = bundle.one("info")
    cfg: dict = {}
    sheet = f.main() if f and not f.load_error else None
    if not sheet or not sheet.rows:
        return cfg
    values = sheet.rows[0]
    for i, h in enumerate(sheet.header):
        if isinstance(h, str) and h.strip():
            cfg[h.strip()] = values[i] if i < len(values) else None
    for key in ("residencies", "currencies", "frequencies", "addons",
                "multiCurrency", "copayTypes", "rateTable",
                "exchange_premium", "exchange_benefit"):
        cfg[key + "_list"] = slash_list(cfg.get(key))
    # runtime-defined residency areas: optional 'residencies' sheet with
    # '<Key>-incl' / '<Key>-excl' column pairs (e.g. Morgan Price ROW)
    keys = set(cfg["residencies_list"])
    for s in f.sheets:
        if s.name.lower() == "residencies":
            for h in s.header:
                if isinstance(h, str) and h.strip().endswith(("-incl", "-excl")):
                    keys.add(h.strip().rsplit("-", 1)[0])
    cfg["residency_keys"] = keys
    return cfg
