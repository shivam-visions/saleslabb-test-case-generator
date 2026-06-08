"""CLI: python -m src.intake_gate <ticket-output-folder> [...] [--json out.json]

Exit codes: 0 = all PASS / PASS_WITH_WARNINGS, 2 = any FAIL, 3 = usage error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engine import validate
from .models import Severity

SEV_MARK = {Severity.ERROR: "✗", Severity.WARN: "!", Severity.INFO: "·"}
VERDICT_MARK = {"PASS": "✅ PASS", "PASS_WITH_WARNINGS": "⚠️  PASS (warnings)",
                "FAIL": "❌ FAIL"}


def print_result(res, verbose: bool) -> None:
    print("=" * 88)
    print(f"{VERDICT_MARK[res.verdict]}  {res.folder}")
    print(f"  files: {', '.join(res.files) if res.files else '(none recognized)'}")
    counts = res.to_dict()["counts"]
    print(f"  findings: {counts['error']} error / {counts['warn']} warn "
          f"/ {counts['info']} info")
    shown = res.findings if verbose else [
        f for f in res.findings if f.severity != Severity.INFO]
    for f in shown:
        loc = f.file or "(bundle)"
        if f.sheet:
            loc += f" [{f.sheet}]"
        if f.row:
            loc += f" row {f.row}"
        print(f"  {SEV_MARK[f.severity]} {f.severity.value:5} {f.rule:12} "
              f"{loc}: {f.message}")


def detect(folder: Path) -> dict:
    """Identify the bundle from its own contents (no pre-defined inputs):
    provider/insurerName from info.xlsx, plus a workbook inventory and raw
    file listing. Used by the backend to infer product/carrier at intake."""
    from . import loader
    bundle = loader.discover(folder)
    cfg = loader.info_config(bundle)
    roles = {r: len(bundle.by_role(r))
             for r in ("info", "rateSheet", "benefits", "addons", "conversion")}
    others = sorted(p.name for p in folder.iterdir()
                    if p.is_file() and not any(
                        f.path.name == p.name for f in bundle.files))
    return {
        "provider": cfg.get("provider"),
        "insurerName": cfg.get("insurerName"),
        "currencies": cfg.get("currencies_list") or [],
        "residencies": cfg.get("residencies_list") or [],
        "workbooks": roles,
        "otherFiles": others,
        "detected": bool(cfg.get("provider") or cfg.get("insurerName")),
    }


def capabilities() -> dict:
    """Derive T3 coverage from the codebase itself: an insurer is covered when
    its module ships a verifier."""
    insurers_dir = Path(__file__).resolve().parent.parent / "insurers"
    covered = sorted(
        d.name for d in insurers_dir.iterdir()
        if d.is_dir() and (d / "verifier.py").exists())
    modules = sorted(
        d.name for d in insurers_dir.iterdir()
        if d.is_dir() and not d.name.startswith("_"))
    return {"t3": covered, "modules": modules}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="intake_gate",
        description="Tier-2 static validation for onboarding workbook bundles "
                    "(NDS-1190 Phase 3b)")
    ap.add_argument("folders", nargs="*",
                    help="ticket output folder(s) containing the workbooks")
    ap.add_argument("--json", metavar="PATH",
                    help="write combined results JSON to PATH")
    ap.add_argument("--html", metavar="PATH",
                    help="write a self-contained HTML report to PATH "
                         "(Phase C dashboard preview)")
    ap.add_argument("--detect", action="store_true",
                    help="print bundle identity JSON (provider/insurerName/"
                         "inventory) instead of validating")
    ap.add_argument("--raw-check", action="store_true",
                    help="run carrier-agnostic RAW-file integrity checks "
                         "(IPM intake; reads _categories.json if present)")
    ap.add_argument("--capabilities", action="store_true",
                    help="print engine coverage JSON (which insurers have T3)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="also print INFO findings")
    args = ap.parse_args(argv)

    if args.capabilities:
        print(json.dumps(capabilities()))
        return 0
    if args.raw_check:
        from .raw_checks import run_raw_checks
        if not args.folders:
            print("error: --raw-check needs a folder", file=sys.stderr)
            return 3
        results = [run_raw_checks(f) for f in args.folders]
        out = json.dumps(results, ensure_ascii=False)
        if args.json:
            Path(args.json).write_text(out)
        print(out[:2000])
        return 2 if any(r["verdict"] == "FAIL" for r in results) else 0
    if args.detect:
        if not args.folders:
            print("error: --detect needs a folder", file=sys.stderr)
            return 3
        print(json.dumps([detect(Path(f)) for f in args.folders]))
        return 0
    if not args.folders:
        ap.error("folders required unless --capabilities")

    results = []
    for folder in args.folders:
        p = Path(folder)
        if not p.is_dir():
            print(f"error: not a directory: {folder}", file=sys.stderr)
            return 3
        res = validate(p)
        results.append(res)
        print_result(res, args.verbose)

    print("=" * 88)
    fails = sum(1 for r in results if r.verdict == "FAIL")
    print(f"bundles: {len(results)}  |  FAIL: {fails}  |  "
          f"PASS/warn: {len(results) - fails}")

    if args.json:
        Path(args.json).write_text(
            json.dumps([r.to_dict() for r in results], indent=2,
                       ensure_ascii=False))
        print(f"json written: {args.json}")

    if args.html:
        from .report_html import render
        Path(args.html).write_text(render(results))
        print(f"html report written: {args.html}")

    return 2 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
