"""
verify-product CLI.

Usage:
    python -m src.verify_cli <ticket-folder> --insurer <name> [options]

Pipeline:
    1. Resolve upload-tool folder (interactive confirmation if ambiguous).
    2. Connect to the chosen MongoDB environment (LOCAL/STAGING/PRODUCTION).
    3. Run the per-insurer exact-rate verifier.
    4. Print terminal report + write category-summary JSON.
    5. On green AND --publish: prompt user "Publish? Y/N".
       On Y: upload the CSV to S3 (s3://<bucket>/<provider-slug>/) and update
       the Confluence details page with test details + the bucket link.
    6. Exit code: 0 if green, non-zero otherwise.

Examples:
    # Verify Morgan Price against local DB
    python -m src.verify_cli /home/.../PROD-Morgan-Price-ROW-Enlistment \\
        --insurer morgan_price --env local --upload-tool-folder Morgan_Price_Flexible_Choices_ROW

    # Verify Xen Health DB_NE + publish CSV to S3 on green
    python -m src.verify_cli /home/.../PROD-2022-... \\
        --insurer xen_health --residency DB_NE --env local \\
        --upload-tool-folder Xen_health --publish
"""

import argparse
import os
import sys
from pathlib import Path


# Maps insurer key → keyword for upload-tool folder lookup
INSURER_UPLOAD_TOOL_HINTS = {
    "morgan_price":  "Morgan_Price",
    "xen_health":    "Xen",
    "bupa_dac_eea":  "BUPA_DAC_EEA",
}
UPLOAD_TOOL_ROOT = Path("/home/support/Desktop/MIRO/upload-tool")


def main():
    ap = argparse.ArgumentParser(prog="verify-product")
    ap.add_argument("ticket_folder", type=Path,
                    help="PROD-XXXX-... ticket folder (containing raw/ + PROD-XXXX-output/)")
    ap.add_argument("--insurer", required=True,
                    choices=["morgan_price", "xen_health", "bupa_dac_eea"])
    ap.add_argument("--env", default="local", choices=["local", "staging", "production"])
    ap.add_argument("--residency", default=None,
                    help="(Xen Health) DB_NE or AbuDhabi")
    ap.add_argument("--upload-tool-folder", default=None,
                    help="Explicit upload-tool/scripts/<folder> name (skips interactive prompt)")
    ap.add_argument("--non-interactive", action="store_true",
                    help="Fail on ambiguity instead of prompting")
    ap.add_argument("--publish", action="store_true",
                    help="On green, prompt to publish the CSV to S3 + update the Confluence details page (default: just verify, no publish)")
    ap.add_argument("--page-title", default=None,
                    help="Confluence page title for publish (auto-generated if omitted)")
    ap.add_argument("--subfolder-title", default=None, choices=[None, "ROW Test", "UAE Test"],
                    help="Confluence subfolder. Auto-inferred from insurer if omitted (ROW for Morgan Price, UAE for Xen Health)")
    args = ap.parse_args()

    if not args.ticket_folder.is_dir():
        print(f"ERROR: ticket folder not found: {args.ticket_folder}", file=sys.stderr)
        sys.exit(2)

    # Resolve upload-tool folder (interactive confirmation if needed)
    from src.common.upload_tool_finder import resolve_upload_tool_folder
    keyword = INSURER_UPLOAD_TOOL_HINTS[args.insurer]
    ut_folder = resolve_upload_tool_folder(
        UPLOAD_TOOL_ROOT,
        insurer_keyword=keyword,
        explicit_choice=args.upload_tool_folder,
        interactive=not args.non_interactive,
    )
    print(f"Upload-tool bundle confirmed: {ut_folder}")
    print()

    # Connect to MongoDB
    from src.common.db_verifier import get_db_url, connect, get_database, print_report, write_json, prompt_to_continue
    db_url = get_db_url(UPLOAD_TOOL_ROOT, args.env)
    print(f"Connecting to {args.env.upper()} ({db_url[:30]}…)")
    client = connect(db_url)
    db = get_database(client, db_url)
    print(f"  ✓ Connected to DB: {db.name}")
    print()

    # Run insurer-specific verifier
    if args.insurer == "morgan_price":
        from src.insurers.morgan_price.verifier import verify
        report = verify(db, environment=args.env)
    elif args.insurer == "xen_health":
        from src.insurers.xen_health.verifier import verify
        residency = args.residency or "DB_NE"
        report = verify(db, environment=args.env, residency_key=residency)
    elif args.insurer == "bupa_dac_eea":
        from src.insurers.bupa_dac_eea.verifier import run_verification
        report = run_verification(
            db, ticket_folder=args.ticket_folder,
            residency=args.residency, env=args.env,
        )
    else:
        raise SystemExit(f"Insurer {args.insurer} not yet supported")

    client.close()

    # Print + persist
    print_report(report)
    out_dir = _resolve_output_dir(args.ticket_folder, args.insurer, args.residency)
    json_path = out_dir / "verify-report.json"
    write_json(report, json_path)
    print(f"  JSON: {json_path}")

    if not report.is_green:
        print()
        print("✗ RED — verification failed. Fix the bundle and re-run.")
        print("  Detailed JSON: " + str(json_path))
        sys.exit(1)

    if not args.publish:
        print()
        print("✓ Verification passed. Re-run with --publish to push the CSV to S3.")
        sys.exit(0)

    # ── Publish gate ────────────────────────────────────────────────────────
    print()
    if not prompt_to_continue():
        print("Aborted. CSVs not published.")
        sys.exit(0)

    _publish(args, out_dir)


def _resolve_output_dir(ticket_folder: Path, insurer: str, residency: str | None) -> Path:
    prod_outputs = list(ticket_folder.glob("PROD-*-output"))
    base = (prod_outputs[0] if prod_outputs else ticket_folder)
    return base / "test-cases"


def _publish(args, out_dir: Path) -> None:
    """Upload the generated CSV to S3 (canonical home), then update the
    Confluence details page with test details + the bucket link.

    The CSV itself no longer lives on Confluence — Playwright globalSetup and
    the dashboard backend (S3TestCasesService) read it from S3. If Atlassian
    credentials are missing, the S3 upload still happens and only the
    details-page update is skipped.
    """
    # Resolve CSV path
    if args.insurer == "xen_health":
        suffix = f"-{args.residency or 'DB_NE'}" if (args.residency or 'DB_NE') != 'DB_NE' else ""
        csv_path = out_dir / f"quote-scenarios-smoke{suffix}.csv"
        manifest_path = out_dir / f"test-cases.manifest{suffix}.json"
    else:
        csv_path = out_dir / "quote-scenarios-smoke.csv"
        manifest_path = out_dir / "test-cases.manifest.json"

    if not csv_path.is_file():
        print(f"ERROR: CSV not found at {csv_path}. Run /generate-tests first.", file=sys.stderr)
        sys.exit(2)

    # Resolve title + subfolder
    if args.insurer == "morgan_price":
        subfolder = args.subfolder_title or "ROW Test"
        page_title = args.page_title or "Morgan Price (Flexible Choices) — PROD-1930 [Auto-generated smoke]"
        ticket_id = "PROD-1930"
        product_label = "Morgan Price (Flexible Choices)"
    elif args.insurer == "bupa_dac_eea":
        subfolder = args.subfolder_title or "ROW Test"
        page_title = args.page_title or "Bupa DAC (Global Health) EEA — PROD-1968 [Auto-generated smoke]"
        ticket_id = "PROD-1968"
        product_label = "Bupa DAC (Global Health) EEA"
    else:
        subfolder = args.subfolder_title or "UAE Test"
        residency = args.residency or "DB_NE"
        page_title = args.page_title or f"Xen Health ({residency}) — PROD-2022 [Auto-generated smoke]"
        ticket_id = "PROD-2022"
        product_label = f"Xen Health ({residency})"

    # 1. Upload CSV to S3 (canonical home — globalSetup + backend read from here)
    from src.common.s3_uploader import upload_csv, slugify_product_label
    slug = slugify_product_label(product_label)
    print()
    print(f"Uploading {csv_path.name} → S3 (provider folder: {slug!r})")
    s3 = upload_csv(csv_path, product_label)
    print(f"  ✓ s3://{s3['bucket']}/{s3['key']} ({s3['size']} bytes)")

    # 2. Read manifest for the details page
    import json
    row_count = 0
    axes_summary = ""
    if manifest_path.is_file():
        m = json.loads(manifest_path.read_text())
        row_count = m.get("row_count", 0)
        axes_summary = ", ".join(f"{k} ({len(v)})" for k, v in m.get("axis_coverage", {}).items())

    # 3. Update the Confluence details page (test details + bucket link ONLY —
    #    no CSV attachment). Degrade gracefully if Atlassian creds are absent.
    if not os.environ.get("ATLASSIAN_EMAIL") or not os.environ.get("ATLASSIAN_API_TOKEN"):
        print()
        print("WARNING: ATLASSIAN_EMAIL / ATLASSIAN_API_TOKEN not set — skipped the")
        print("         Confluence details-page update. CSV is live on S3 regardless.")
        print()
        print("==== PUBLISHED (S3 only) ====")
        print(f"  {s3['url']}")
        return

    from src.common.confluence_uploader import (
        _check_token_expiry, list_folder_children_v2, create_page, update_page, _api,
    )
    _check_token_expiry()
    base_url = "https://vitavirtues.atlassian.net/wiki"
    print()
    print(f"Updating Confluence details page under {subfolder!r}: {page_title!r}")

    children = list_folder_children_v2(base_url, "420544515")
    sub = next((c for c in children if c.get("title", "").strip().lower() == subfolder.strip().lower()), None)
    if not sub:
        print(f"ERROR: '{subfolder}' not found under Test Cases", file=sys.stderr)
        sys.exit(2)

    siblings = list_folder_children_v2(base_url, sub["id"])
    existing = next((c for c in siblings if c.get("title", "").strip().lower() == page_title.strip().lower()), None)

    body_html = _build_details_body(ticket_id, product_label, row_count, axes_summary, s3, slug)

    if existing:
        full = _api("GET", f"/rest/api/content/{existing['id']}?expand=version", base_url)[1]
        page = update_page(base_url, existing["id"], page_title, body_html, current_version=full["version"]["number"])
        action = "updated"
    else:
        page = create_page(base_url, "IPRB", sub["id"], page_title, body_html)
        action = "created"
    page_url = base_url + page["_links"]["webui"]
    print(f"  ✓ Page {action}: {page_url}")
    print()
    print("==== PUBLISHED ====")
    print(f"  CSV:  {s3['url']}")
    print(f"  Page: {page_url}")


def _build_details_body(ticket_id: str, product_label: str, row_count: int,
                        axes_summary: str, s3: dict, slug: str) -> str:
    """Details-only page body: test metadata + S3 bucket link (no attachment)."""
    return (
        f"<p><strong>{product_label}</strong> — auto-generated smoke test cases for {ticket_id}.</p>"
        f"<ul>"
        f"<li><strong>Rows:</strong> {row_count}</li>"
        f"<li><strong>Axis coverage:</strong> {axes_summary or 'see manifest'}</li>"
        f"<li><strong>CSV (canonical, S3):</strong> <a href=\"{s3['url']}\">s3://{s3['bucket']}/{s3['key']}</a></li>"
        f"<li><strong>Dashboard product folder:</strong> <code>{slug}</code></li>"
        f"</ul>"
        f"<p>The CSV lives on S3 — Playwright globalSetup and the dashboard backend read it "
        f"from there. This page carries test details + the bucket link only.</p>"
    )


if __name__ == "__main__":
    main()
