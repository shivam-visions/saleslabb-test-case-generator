"""
test-case-generator CLI.

Usage:
    python -m src.cli <ticket-folder> --insurer <name> [--tier smoke] [--residency DB_NE|AbuDhabi]
                      [--upload-tool-folder <name>]

Examples:
    python -m src.cli /home/.../PROD-Morgan-Price-ROW-Enlistment --insurer morgan_price
    python -m src.cli /home/.../PROD-2022-2039-... --insurer xen_health --residency DB_NE

The --upload-tool-folder flag identifies which upload-tool/scripts/<folder> this
product uses. Required because many folder names overlap (AXA / AXA_Kenya /
AXA_Global_Health_ROW; Allianz / Allianz_Care / Allianz_Britcare / ...). The
generator validates the chosen folder exists; if omitted, an interactive prompt
asks the user to pick from candidates that match the insurer keyword. Phase 2's
fast verifier will use this same flag.
"""

import argparse
import sys
from pathlib import Path


# Maps insurer key → (keyword to search in upload-tool/scripts/, default folder hint)
INSURER_UPLOAD_TOOL_HINTS = {
    "morgan_price":  "Morgan_Price",
    "xen_health":    "Xen",
    "bupa_dac":      "BUPA_DAC_Monaco_France",
    "bupa_dac_eea":  "BUPA_DAC_EEA",              # PROD-1968 EEA region
}

# Maps (insurer, residency) → PID (product identifier on the provider record).
# Read from the per-insurer persona files. Used to embed in CSV's PID column
# so Playwright's provider-match check works correctly on staging/production.
# Default residency is None (single-residency products).
INSURER_PID = {
    # PID convention: staging DB's `productID` field stores ONLY the suffix
    # (e.g. "C4A6E") — NOT the "PID-" prefix. Page titles use the human-readable
    # "Name- PID-C4A6E" form, and the Playwright runner extracts the last hyphen
    # token via split('-').pop(). So put just the suffix here.
    # Morgan Price PID — verify against staging MongoDB before using on staging runs.
    # Local DB providers have productID=undefined, so PID can be empty for local-DB testing.
    ("morgan_price", None):       "",
    ("xen_health",   "DB_NE"):    "1BECF",          # from persona §1 (was "PID-1BECF")
    ("xen_health",   "AbuDhabi"): "DBDEE",          # from persona §1 (was "PID-DBDEE")
    ("bupa_dac",     None):       "9A429",          # from PROD-1985 persona §"Product identity" (was "PID-9A429")
    ("bupa_dac",     "FR"):       "9A429",
    ("bupa_dac",     "MC"):       "9A429",
    ("bupa_dac_eea", None):       "C4A6E",          # from PROD-1968 IPRB (was "PID-C4A6E")
    ("bupa_dac_eea", "EEA"):      "C4A6E",
}


def _resolve_pid(insurer: str, residency: str | None) -> str:
    """Look up the PID for this insurer + residency. Returns empty string if unknown."""
    pid = INSURER_PID.get((insurer, residency))
    if pid is None and residency is not None:
        # Fall back to insurer-only entry
        pid = INSURER_PID.get((insurer, None))
    return pid or ""

UPLOAD_TOOL_ROOT = Path("/home/support/Desktop/MIRO/upload-tool")


def main():
    parser = argparse.ArgumentParser(prog="test-case-generator")
    parser.add_argument("ticket_folder", type=Path,
                        help="Path to the PROD-XXXX-... ticket folder (must contain raw/)")
    parser.add_argument("--insurer", required=True,
                        choices=["morgan_price", "xen_health", "bupa_dac", "bupa_dac_eea"],
                        help="Which insurer extractor to use")
    parser.add_argument("--tier", default="smoke", choices=["smoke", "regression", "full"],
                        help="Which tier of CSV to generate")
    parser.add_argument("--residency", default=None,
                        help="(Xen Health) Which residency: DB_NE or AbuDhabi")
    parser.add_argument("--upload-tool-folder", default=None,
                        help="Explicit upload-tool/scripts/ folder name for this product")
    parser.add_argument("--non-interactive", action="store_true",
                        help="Fail rather than prompt if upload-tool folder is ambiguous")
    parser.add_argument("--output-subdir", default=None,
                        help="Override output subfolder (default: PROD-XXXX-output/test-cases)")
    args = parser.parse_args()

    if not args.ticket_folder.is_dir():
        print(f"Error: ticket folder not found: {args.ticket_folder}", file=sys.stderr)
        sys.exit(2)

    raw_dir = args.ticket_folder / "raw"
    if not raw_dir.is_dir():
        print(f"Error: raw/ subfolder not found at {raw_dir}", file=sys.stderr)
        sys.exit(2)

    # Resolve + confirm upload-tool folder for this product (safety per user)
    upload_tool_folder = _resolve_upload_tool_folder(args)
    print(f"Upload-tool bundle confirmed: {upload_tool_folder}")
    print()

    # Dispatch to the insurer module
    if args.insurer == "morgan_price":
        return _run_morgan_price(args, raw_dir, upload_tool_folder)
    if args.insurer == "xen_health":
        return _run_xen_health(args, raw_dir, upload_tool_folder)
    if args.insurer == "bupa_dac":
        return _run_bupa_dac(args, raw_dir, upload_tool_folder)
    if args.insurer == "bupa_dac_eea":
        return _run_bupa_dac_eea(args, raw_dir, upload_tool_folder)
    print(f"Insurer {args.insurer} not yet supported", file=sys.stderr)
    sys.exit(2)


def _run_bupa_dac_eea(args, raw_dir: Path, upload_tool_folder: Path):
    from src.insurers.bupa_dac_eea.rate_parser import parse_bupa_dac_eea_rates
    from src.insurers.bupa_dac_eea import generator
    from src.common.csv_writer import write_csv, write_manifest

    print("Reading 5 BUPA DAC EEA rate PDFs from raw/rates/ …")
    rate_data = parse_bupa_dac_eea_rates(args.ticket_folder)
    print(f"  ✓ Parsed {len(rate_data['rates'])} rate keys across {rate_data['plans_seen']}")
    print(f"  ⚠ {len(rate_data['warnings'])} warnings — see warnings dump (first 5 below):")
    for w in rate_data["warnings"][:5]:
        print(f"     - {w}")

    if args.tier == "smoke":
        cases = generator.generate_smoke_cases(rate_data)
    else:
        print(f"Tier '{args.tier}' not yet implemented for BUPA DAC EEA", file=sys.stderr)
        sys.exit(2)

    out_dir = _resolve_output_dir(args)
    csv_path = out_dir / f"quote-scenarios-{args.tier}.csv"
    manifest_path = out_dir / "test-cases.manifest.json"
    pid = _resolve_pid(args.insurer, args.residency)
    write_csv(cases, csv_path, pid=pid)
    write_manifest(cases, manifest_path, tier=args.tier, insurer=args.insurer)
    if pid:
        print(f"  PID embedded in CSV: {pid}")
    else:
        print(f"  ⚠ No PID for ({args.insurer}, {args.residency}) — CSV has blank PID column.")

    # Also dump parser warnings to a sidecar file for the IPM reference doc
    warnings_path = out_dir / "parser-warnings.log"
    warnings_path.write_text(
        f"BUPA DAC EEA parser warnings ({len(rate_data['warnings'])} total)\n"
        + "=" * 70 + "\n"
        + "\n".join(rate_data["warnings"]) + "\n",
        encoding="utf-8",
    )

    print()
    print(f"✓ Generated {len(cases)} {args.tier} test cases for BUPA DAC EEA")
    print(f"  CSV:      {csv_path}")
    print(f"  Manifest: {manifest_path}")
    print(f"  Warnings: {warnings_path}")


def _run_bupa_dac(args, raw_dir: Path, upload_tool_folder: Path):
    from src.insurers.bupa_dac.rate_parser import parse_bupa_dac_rates
    from src.insurers.bupa_dac import generator
    from src.common.csv_writer import write_csv, write_manifest

    print("Reading 10 BUPA DAC rate PDFs from raw/{France,Monaco}-Rates/ …")
    rate_data = parse_bupa_dac_rates(args.ticket_folder)
    print(f"  ✓ Parsed {len(rate_data['rates'])} rate keys across {rate_data['plans_seen']}")
    if rate_data["warnings"]:
        print(f"  ⚠ {len(rate_data['warnings'])} warnings (first 3):")
        for w in rate_data["warnings"][:3]:
            print(f"     - {w}")

    residency = args.residency or "FR"
    if args.tier == "smoke":
        cases = generator.generate_smoke_cases(rate_data, residency=residency)
    else:
        print(f"Tier '{args.tier}' not yet implemented for BUPA DAC", file=sys.stderr)
        sys.exit(2)

    out_dir = _resolve_output_dir(args)
    csv_path = out_dir / f"quote-scenarios-{args.tier}.csv"
    manifest_path = out_dir / "test-cases.manifest.json"
    pid = _resolve_pid(args.insurer, args.residency)
    write_csv(cases, csv_path, pid=pid)
    write_manifest(cases, manifest_path, tier=args.tier, insurer=args.insurer)
    if pid:
        print(f"  PID embedded in CSV: {pid}")
    else:
        print(f"  ⚠ No PID for ({args.insurer}, {args.residency}) — CSV has blank PID column.")

    print()
    print(f"✓ Generated {len(cases)} {args.tier} test cases for BUPA DAC ({residency})")
    print(f"  CSV:      {csv_path}")
    print(f"  Manifest: {manifest_path}")


def _resolve_upload_tool_folder(args) -> Path:
    from src.common.upload_tool_finder import resolve_upload_tool_folder
    keyword = INSURER_UPLOAD_TOOL_HINTS.get(args.insurer, args.insurer)
    return resolve_upload_tool_folder(
        UPLOAD_TOOL_ROOT,
        insurer_keyword=keyword,
        explicit_choice=args.upload_tool_folder,
        interactive=not args.non_interactive,
    )


def _resolve_output_dir(args) -> Path:
    if args.output_subdir:
        return args.ticket_folder / args.output_subdir
    prod_outputs = list(args.ticket_folder.glob("PROD-*-output"))
    return (prod_outputs[0] if prod_outputs else args.ticket_folder) / "test-cases"


def _run_morgan_price(args, raw_dir: Path, upload_tool_folder: Path):
    from src.insurers.morgan_price.rate_parser import parse_rate_pdf
    from src.insurers.morgan_price.benefit_parser import parse_raw_benefits
    from src.insurers.morgan_price import generator
    from src.common.csv_writer import write_csv, write_manifest

    pdf_candidates = list((raw_dir / "rates").glob("*.pdf")) + list(raw_dir.glob("*.pdf"))
    if not pdf_candidates:
        print(f"Error: no rate PDF found under {raw_dir}/rates/", file=sys.stderr)
        sys.exit(2)
    rate_pdf = pdf_candidates[0]

    benefits_candidates = list(raw_dir.glob("raw-benefits*.xlsx")) + list(raw_dir.glob("*benefits*.xlsx"))
    if not benefits_candidates:
        print(f"Error: no raw-benefits xlsx found under {raw_dir}", file=sys.stderr)
        sys.exit(2)
    benefits_xlsx = benefits_candidates[0]

    print(f"Reading rates from:    {rate_pdf}")
    print(f"Reading benefits from: {benefits_xlsx}")

    rate_table = parse_rate_pdf(rate_pdf)
    benefit_data = parse_raw_benefits(benefits_xlsx)

    if args.tier == "smoke":
        cases = generator.generate_smoke_cases(rate_table, benefit_data)
    elif args.tier == "regression":
        cases = generator.generate_regression_cases(rate_table, benefit_data)
    else:
        cases = generator.generate_full_cases(rate_table, benefit_data)

    out_dir = _resolve_output_dir(args)
    csv_path = out_dir / f"quote-scenarios-{args.tier}.csv"
    manifest_path = out_dir / "test-cases.manifest.json"
    pid = _resolve_pid(args.insurer, args.residency)
    write_csv(cases, csv_path, pid=pid)
    write_manifest(cases, manifest_path, tier=args.tier, insurer=args.insurer)
    if pid:
        print(f"  PID embedded in CSV: {pid}")
    else:
        print(f"  ⚠ No PID for ({args.insurer}, {args.residency}) — CSV has blank PID column.")

    print()
    print(f"✓ Generated {len(cases)} {args.tier} test cases")
    print(f"  CSV:      {csv_path}")
    print(f"  Manifest: {manifest_path}")


def _run_xen_health(args, raw_dir: Path, upload_tool_folder: Path):
    from src.insurers.xen_health.rate_parser import parse_xen_rate_xlsx
    from src.insurers.xen_health import generator
    from src.common.csv_writer import write_csv, write_manifest

    residency = args.residency or "DB_NE"
    rate_dir_name = "DB_NE-rates" if residency == "DB_NE" else "AUH-rates"
    rate_dir = raw_dir / rate_dir_name
    if not rate_dir.is_dir():
        print(f"Error: rate folder not found: {rate_dir}", file=sys.stderr)
        sys.exit(2)
    xlsx_candidates = list(rate_dir.glob("Xen*.xlsx"))
    if not xlsx_candidates:
        print(f"Error: no Xen rate xlsx found in {rate_dir}", file=sys.stderr)
        sys.exit(2)
    rate_xlsx = xlsx_candidates[0]

    print(f"Residency:             {residency}")
    print(f"Reading rates from:    {rate_xlsx}")

    rate_data = parse_xen_rate_xlsx(rate_xlsx)

    if args.tier == "smoke":
        cases = generator.generate_smoke_cases(rate_data, residency_key=residency)
    else:
        print(f"Tier '{args.tier}' not yet implemented for Xen Health", file=sys.stderr)
        sys.exit(2)

    out_dir = _resolve_output_dir(args)
    suffix = f"-{residency}" if residency != "DB_NE" else ""
    csv_path = out_dir / f"quote-scenarios-{args.tier}{suffix}.csv"
    manifest_path = out_dir / f"test-cases.manifest{suffix}.json"
    write_csv(cases, csv_path)
    write_manifest(cases, manifest_path, tier=args.tier, insurer=f"{args.insurer}/{residency}")

    print()
    print(f"✓ Generated {len(cases)} {args.tier} test cases for Xen Health / {residency}")
    print(f"  CSV:      {csv_path}")
    print(f"  Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
