"""T2-X rules — cross-workbook exact-match join keys (the silent killers)."""
from __future__ import annotations

from .models import ROW_GEO, ROW_NETWORK, COPAY_ROWS, Severity, cell_str
from .rules_addons import parent_sheets
from .rules_benefits import benefit_rows, plan_columns


def _col_values(wf, col: str) -> set[str]:
    s = wf.main()
    if not s:
        return set()
    i = s.col_index(col)
    if i is None:
        return set()
    return {cell_str(r[i] if i < len(r) else None).strip() for r in s.rows} - {""}


def _row_tokens(sheet, row_label_set) -> set[str]:
    """Slash-split tokens across all plan columns of the first matching
    special row."""
    rows = benefit_rows(sheet)
    out: set[str] = set()
    for label in row_label_set if isinstance(row_label_set, set) else {row_label_set}:
        if label in rows:
            _, r = rows[label]
            for i, _h in plan_columns(sheet):
                v = r[i] if i < len(r) else None
                for tok in cell_str(v).split("/"):
                    if tok.strip():
                        out.add(tok.strip())
    return out


def _pairs(bundle, role_a, role_b):
    """Pair files of two roles by residency suffix."""
    bs = {f.suffix: f for f in bundle.by_role(role_b)}
    for fa in bundle.by_role(role_a):
        fb = bs.get(fa.suffix)
        if fb and not fa.load_error and not fb.load_error \
                and fa.main() and fb.main():
            yield fa, fb


def run_cross(ctx) -> None:
    cfg = ctx.cfg
    copay_types = cfg.get("copayTypes_list") or []

    for rf, bf in _pairs(ctx.bundle, "rateSheet", "benefits"):
        bsheet = bf.main()
        ben_plans = {h for _, h in plan_columns(bsheet)}
        rate_plans = _col_values(rf, "planName")

        # T2-X-001 — planName exact match both directions (silent premium drop)
        only_rate = rate_plans - ben_plans
        only_ben = ben_plans - rate_plans
        if only_rate:
            ctx.add("T2-X-001", Severity.ERROR, rf.name,
                    f"planName(s) {sorted(only_rate)} in {rf.name} have no "
                    f"matching column in {bf.name} — premiums silently dropped")
        if only_ben:
            ctx.add("T2-X-001", Severity.ERROR, bf.name,
                    f"plan column(s) {sorted(only_ben)} in {bf.name} have no "
                    f"rates in {rf.name}")

        # T2-X-002 — copay join: rateSheet.copay ⊆ benefits Copays row
        ben_copays = _row_tokens(bsheet, COPAY_ROWS)
        if ben_copays:
            if copay_types:  # rateTable flow: benefits side carries IP-/OP- prefix
                stripped = set()
                for t in ben_copays:
                    for p in ("IP-", "OP-"):
                        if t.startswith(p):
                            t = t[len(p):]
                            break
                    stripped.add(t)
                ben_copays |= stripped
            missing = _col_values(rf, "copay") - ben_copays
            if missing:
                ctx.add("T2-X-002", Severity.ERROR, rf.name,
                        f"copay value(s) {sorted(missing)[:8]} not present in the "
                        f"benefits Copays filter row of {bf.name} — exact-match "
                        "join (deductible modifier link) silently breaks")

        # T2-X-003 — network join
        ben_nets = _row_tokens(bsheet, ROW_NETWORK)
        rate_nets = _col_values(rf, "network")
        if rate_nets and ben_nets:
            missing = rate_nets - ben_nets
            if missing:
                ctx.add("T2-X-003", Severity.ERROR, rf.name,
                        f"network(s) {sorted(missing)} in {rf.name} not in "
                        f"'Network Details' of {bf.name}")

        # T2-X-004 — coverage join (rateSheet.coverage may be slash-multi)
        ben_cov = _row_tokens(bsheet, ROW_GEO)
        rate_cov: set[str] = set()
        for v in _col_values(rf, "coverage"):
            for tok in v.split("/"):
                if tok.strip():
                    rate_cov.add(tok.strip())
        if rate_cov and ben_cov:
            missing = rate_cov - ben_cov
            if missing:
                ctx.add("T2-X-004", Severity.WARN, rf.name,
                        f"coverage value(s) {sorted(missing)[:6]} not present in "
                        f"'Geographical Coverage' of {bf.name} — verify Area "
                        "descriptions agree")

    # T2-X-005 — addon identity triangle. Pilot calibration (PROD-1930/2022):
    # info.addons entries identify addons by parent SHEET NAME (xlsx-truncated
    # to 31 chars); 'label' is the per-option label, not the addon identity.
    declared = cfg.get("addons_list") or []
    addons_files = ctx.bundle.by_role("addons")
    if declared and addons_files:
        dupes = {d for d in declared if declared.count(d) > 1}
        if dupes:
            ctx.add("T2-X-005", Severity.WARN, "info.xlsx",
                    f"duplicate info.addons entr{'y' if len(dupes) == 1 else 'ies'}: "
                    f"{sorted(dupes)}")
        for af in addons_files:
            if af.load_error or not af.sheets:
                continue
            names = {s.name for s in af.sheets}
            missing = [d for d in declared
                       if d not in names and d[:31] not in names]
            if missing:
                ctx.add("T2-X-005", Severity.ERROR, af.name,
                        f"info.addons entr{'y' if len(missing) == 1 else 'ies'} "
                        f"{missing} have no matching parent sheet in {af.name} "
                        "(matched by sheet name, 31-char truncated) — addon "
                        "silently skipped")
        for bf in ctx.bundle.by_role("benefits"):
            if bf.load_error or not bf.main():
                continue
            ben_rows = set(benefit_rows(bf.main()))
            missing = [d for d in declared if d not in ben_rows]
            if missing:
                ctx.add("T2-X-005", Severity.WARN, bf.name,
                        f"info.addons entr{'y' if len(missing) == 1 else 'ies'} "
                        f"{missing} have no matching benefit row in {bf.name}")

    # T2-X-006 — multi-residency plan-set parity across suffix pairs
    rate_files = [f for f in ctx.bundle.by_role("rateSheet")
                  if not f.load_error and f.main()]
    if len(rate_files) > 1:
        plan_sets = {f.name: _col_values(f, "planName") for f in rate_files}
        base = plan_sets[rate_files[0].name]
        diff = {n: sorted(s ^ base) for n, s in plan_sets.items() if s != base}
        if diff:
            ctx.add("T2-X-006", Severity.WARN, "",
                    f"plan sets differ across residency rateSheets: {diff} — "
                    "confirm plans intentionally differ per residency")
