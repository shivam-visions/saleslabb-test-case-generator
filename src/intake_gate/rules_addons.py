"""T2-ADD rules — addons*.xlsx structural validation (two-sheet pattern)."""
from __future__ import annotations

from .models import ADDON_TYPES, Severity, cell_str, is_blank

PARENT_MARKERS = {"type", "label", "plan"}


def parent_sheets(wf) -> list:
    return [s for s in wf.sheets
            if PARENT_MARKERS <= {h.strip() for h in s.header
                                  if isinstance(h, str)}]


def run_addons(ctx) -> None:
    for f in ctx.bundle.by_role("addons"):
        if f.load_error or not f.sheets:
            continue
        name = f.name
        sheet_names = [s.name for s in f.sheets]
        parents = parent_sheets(f)
        if not parents:
            ctx.add("T2-ADD-001", Severity.ERROR, name,
                    "no parent sheet found (expected columns type/label/plan)")
            continue

        no_opt = [ps.name for ps in parents
                  if "isOptional" not in {h.strip() for h in ps.header
                                          if isinstance(h, str)}]
        if no_opt:
            ctx.add("T2-ADD-001", Severity.WARN, name,
                    f"{len(no_opt)} parent sheet(s) lack isOptional column "
                    f"(older dialect — confirm parser version handles it): "
                    f"{no_opt[:6]}{'…' if len(no_opt) > 6 else ''}")

        for ps in parents:
            rownos = getattr(ps, "row_numbers", list(range(2, len(ps.rows) + 2)))
            for r, rn in zip(ps.dicts(), rownos):
                lab = r.get("label")
                typ = cell_str(r.get("type")).strip()
                if is_blank(lab) and is_blank(typ):
                    continue  # spacer row
                # T2-ADD-002 — type enum
                if typ and typ not in ADDON_TYPES:
                    ctx.add("T2-ADD-002", Severity.ERROR, name,
                            f"addon type '{typ}' not in {sorted(ADDON_TYPES)}",
                            sheet=ps.name, row=rn)
                # T2-ADD-005 — conditional-override review flags
                if typ == "conditional-override":
                    sev = Severity.WARN
                    note = ("REPLACES the running premium (rare). Verify intent — "
                            "conditional-fixed (additive) is the usual choice")
                    if "annual limit" in cell_str(lab).lower():
                        note = ("conditional-override on an Annual Limit addon is "
                                "the documented pitfall: selecting a tier replaces "
                                "base premium instead of adjusting it (schema "
                                "§addons type invariants)")
                    ctx.add("T2-ADD-005", sev, name, note, sheet=ps.name, row=rn)
                # T2-ADD-006 — label whitespace (silent-skip join key)
                if isinstance(lab, str) and (lab != lab.strip() or "\xa0" in lab):
                    ctx.add("T2-ADD-006", Severity.ERROR, name,
                            f"label {lab!r} has stray whitespace — silently "
                            "skipped on info.addons / benefits-row match",
                            sheet=ps.name, row=rn)
                # T2-ADD-003 — sheetName must reference an existing sheet
                sn = cell_str(r.get("sheetName")).strip()
                if sn:
                    if not any(s == sn or s == sn[:31] for s in sheet_names):
                        ctx.add("T2-ADD-003", Severity.ERROR, name,
                                f"sheetName '{sn}' has no matching child sheet "
                                f"(sheets: {sheet_names})", sheet=ps.name, row=rn)
                    else:
                        # T2-ADD-004 — every flag needs >=1 child row (§12.6:
                        # build STOPS for that option)
                        child = next(s for s in f.sheets
                                     if s.name == sn or s.name == sn[:31])
                        flag = cell_str(r.get("flag")).strip()
                        # pilot calibration: flag/type 'none' = display-only
                        # option, no child-rate lookup (PROD-1930)
                        if flag and flag != "none" and typ != "none":
                            fi = child.col_index("flag")
                            child_flags = set()
                            if fi is not None:
                                child_flags = {
                                    cell_str(cr[fi] if fi < len(cr) else None).strip()
                                    for cr in child.rows}
                            if flag not in child_flags:
                                ctx.add("T2-ADD-004", Severity.ERROR, name,
                                        f"parent flag '{flag}' has no rows in "
                                        f"child sheet '{child.name}' — parser "
                                        "build stops for this option (§12.6)",
                                        sheet=ps.name, row=rn)
