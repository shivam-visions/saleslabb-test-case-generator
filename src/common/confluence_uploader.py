"""
Confluence uploader: creates a page under IPRB → Test Cases → ROW Test (or UAE Test)
and attaches the generated CSV to it.

Auth: HTTP Basic with ATLASSIAN_EMAIL + ATLASSIAN_API_TOKEN env vars.

Steps:
  1. Discover IPRB space + 'Test Cases' parent + 'ROW Test' (or UAE Test) child.
  2. Read one existing page under that parent to confirm convention (for log only).
  3. Create the new page with a one-paragraph body + a View File macro pointing at the CSV.
  4. Upload the CSV as an attachment to the new page.

The View File macro embed makes the page body reference the attachment, which is
what the Playwright globalSetup looks for (per the conventions doc §9).
"""

import argparse
import json
import os
import sys
from base64 import b64encode
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode

import urllib.request
import urllib.error


BASE_URL_DEFAULT = "https://vitavirtues.atlassian.net/wiki"

# Warn when the Atlassian API token is within this many days of expiry.
EXPIRY_WARN_DAYS = 30


def _check_token_expiry() -> None:
    """
    Read ATLASSIAN_API_TOKEN_EXPIRES (ISO date) and surface warnings/errors:
      - silent if more than EXPIRY_WARN_DAYS away
      - warn if within EXPIRY_WARN_DAYS
      - hard-error if already past
    Silent (with a one-line tip) if the env var isn't set.
    """
    raw = os.environ.get("ATLASSIAN_API_TOKEN_EXPIRES", "").strip()
    if not raw:
        print(
            "  ℹ  Tip: set ATLASSIAN_API_TOKEN_EXPIRES=YYYY-MM-DD in your .env file "
            "so the uploader can warn you before the token expires.",
            file=sys.stderr,
        )
        return
    try:
        expiry = datetime.fromisoformat(raw).date()
    except ValueError:
        print(
            f"  ⚠  ATLASSIAN_API_TOKEN_EXPIRES={raw!r} is not a valid ISO date (expected YYYY-MM-DD). "
            "Skipping expiry check.",
            file=sys.stderr,
        )
        return
    today = date.today()
    days_left = (expiry - today).days
    if days_left < 0:
        print(
            f"  ✗  ERROR: Atlassian API token EXPIRED {abs(days_left)} day(s) ago "
            f"({raw}). Generate a new token at "
            "https://id.atlassian.com/manage-profile/security/api-tokens",
            file=sys.stderr,
        )
        sys.exit(2)
    elif days_left <= EXPIRY_WARN_DAYS:
        print(
            f"  ⚠  WARNING: Atlassian API token expires in {days_left} day(s) on {raw}. "
            "Rotate it before then to avoid disruption.",
            file=sys.stderr,
        )


def _auth_header() -> str:
    email = os.environ.get("ATLASSIAN_EMAIL")
    token = os.environ.get("ATLASSIAN_API_TOKEN")
    if not email or not token:
        print("ERROR: ATLASSIAN_EMAIL and ATLASSIAN_API_TOKEN must be set", file=sys.stderr)
        sys.exit(2)
    creds = f"{email}:{token}".encode("utf-8")
    return f"Basic {b64encode(creds).decode('ascii')}"


def _api(method: str, path: str, base_url: str, body=None, headers=None, raw_bytes=False):
    url = f"{base_url}{path}"
    hdrs = {"Authorization": _auth_header(), "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    data = None
    if body is not None:
        if isinstance(body, (bytes, bytearray)):
            data = body
        else:
            data = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req) as resp:
            payload = resp.read()
            if raw_bytes:
                return resp.status, payload
            try:
                return resp.status, json.loads(payload) if payload else None
            except json.JSONDecodeError:
                return resp.status, payload.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code} on {method} {path}\n{body_text}", file=sys.stderr)
        raise


def find_space(base_url: str, space_key: str) -> dict:
    status, data = _api("GET", f"/rest/api/space/{space_key}", base_url)
    return data


def find_page_by_title(base_url: str, space_key: str, title: str, parent_id: str | None = None) -> dict | None:
    """Look up a page by title within a space. Optionally restrict to descendants of parent_id."""
    params = {"spaceKey": space_key, "title": title, "type": "page", "expand": "ancestors"}
    status, data = _api("GET", f"/rest/api/content?{urlencode(params)}", base_url)
    results = data.get("results", []) if data else []
    if parent_id:
        results = [r for r in results if any(a.get("id") == parent_id for a in r.get("ancestors", []))]
    return results[0] if results else None


def list_children(base_url: str, parent_id: str) -> list:
    status, data = _api("GET", f"/rest/api/content/{parent_id}/child/page?limit=100", base_url)
    return data.get("results", []) if data else []


# ---- V2 API for folders (legacy V1 doesn't expose folder-as-parent) ----

def list_folder_children_v2(base_url: str, folder_id: str) -> list:
    """
    List direct descendants of a Confluence folder (pages + sub-folders).
    Uses the V2 descendants endpoint and filters to entries whose parentId
    equals the queried folder.
    """
    status, data = _api("GET", f"/api/v2/folders/{folder_id}/descendants?limit=250", base_url)
    results = (data or {}).get("results", []) if data else []
    return [r for r in results if r.get("parentId") == folder_id]


def find_child_by_title_v2(base_url: str, parent_id: str, title: str) -> dict | None:
    """Among the direct children of a folder/page, return the one whose title matches."""
    for c in list_folder_children_v2(base_url, parent_id):
        if c.get("title", "").strip().lower() == title.strip().lower():
            return c
    return None


def get_page_body(base_url: str, page_id: str) -> str:
    status, data = _api("GET", f"/rest/api/content/{page_id}?expand=body.storage", base_url)
    return (data or {}).get("body", {}).get("storage", {}).get("value", "")


def create_page(base_url: str, space_key: str, parent_id: str, title: str, body_html: str) -> dict:
    payload = {
        "type": "page",
        "title": title,
        "space": {"key": space_key},
        "ancestors": [{"id": parent_id}],
        "body": {"storage": {"value": body_html, "representation": "storage"}},
    }
    status, data = _api("POST", "/rest/api/content", base_url, body=payload)
    return data


def update_page(base_url: str, page_id: str, title: str, body_html: str, current_version: int) -> dict:
    payload = {
        "id": page_id,
        "type": "page",
        "title": title,
        "version": {"number": current_version + 1},
        "body": {"storage": {"value": body_html, "representation": "storage"}},
    }
    status, data = _api("PUT", f"/rest/api/content/{page_id}", base_url, body=payload)
    return data


def upload_attachment(base_url: str, page_id: str, file_path: Path) -> dict:
    """Upload (or replace) a CSV attachment via multipart form-data."""
    boundary = "----saleslabbAttachmentBoundary"
    filename = file_path.name
    file_bytes = file_path.read_bytes()

    body_parts = [
        f"--{boundary}".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{filename}"'.encode(),
        b"Content-Type: text/csv",
        b"",
        file_bytes,
        f"--{boundary}".encode(),
        b'Content-Disposition: form-data; name="minorEdit"',
        b"",
        b"true",
        f"--{boundary}--".encode(),
        b"",
    ]
    body = b"\r\n".join(body_parts)

    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "X-Atlassian-Token": "no-check",  # required by Confluence for attachments
    }
    status, data = _api(
        "POST",
        f"/rest/api/content/{page_id}/child/attachment",
        base_url,
        body=body,
        headers=headers,
    )
    return data


def build_page_body(
    csv_filename: str,
    ticket_id: str,
    product_label: str,
    row_count: int,
    axes_summary: str,
    csv_content: str = "",
) -> str:
    """
    Build the storage-format body for the new page.

    Embeds the CSV TWO ways so the Playwright globalSetup can read it either:
      1. Inline `code` macro with `title=<csv_filename>` containing the raw CSV text
         inside CDATA. This is the PRIMARY data path because Confluence's legacy
         /wiki/download/attachments endpoint rejects Basic + API-token auth
         (WWW-Authenticate: OAuth) — see reference-confluence-download-401-bug.
      2. Original View File macro pointing to the attached file. Kept for human
         convenience (renders a nice file widget) and forward-compat if the
         download endpoint ever starts accepting API tokens.
    """
    description = (
        f"<p>Auto-generated Playwright smoke test cases for "
        f"<strong>{product_label}</strong> ({ticket_id}). "
        f"Generated from the raw insurer rate PDF + benefits xlsx via the deterministic "
        f"<code>test-case-generator</code> tool. "
        f"Row count: <strong>{row_count}</strong>. Axis coverage: {axes_summary}.</p>"
        "<p>The Playwright suite's <code>globalSetup</code> reads the CSV from the "
        "code macro below (title matches the filename). The View File widget is "
        "for human browsing only.</p>"
    )

    # CDATA can't contain the literal "]]>" sequence. Defensively split it if present.
    safe_csv = (csv_content or "").replace("]]>", "]]]]><![CDATA[>")
    code_macro = (
        '<ac:structured-macro ac:name="code" ac:schema-version="1">'
        f'<ac:parameter ac:name="title">{csv_filename}</ac:parameter>'
        '<ac:parameter ac:name="language">none</ac:parameter>'
        '<ac:parameter ac:name="collapse">true</ac:parameter>'
        f'<ac:plain-text-body><![CDATA[{safe_csv}]]></ac:plain-text-body>'
        '</ac:structured-macro>'
    )

    view_file_macro = (
        '<p><ac:structured-macro ac:name="view-file" ac:schema-version="1">'
        f'<ac:parameter ac:name="name">'
        f'<ri:attachment ri:filename="{csv_filename}" /></ac:parameter>'
        '<ac:parameter ac:name="height">600</ac:parameter>'
        "</ac:structured-macro></p>"
    )
    return description + code_macro + view_file_macro


def main():
    ap = argparse.ArgumentParser(prog="confluence_uploader")
    ap.add_argument("--csv", required=True, type=Path, help="Path to the CSV to upload")
    ap.add_argument("--manifest", type=Path, help="Path to test-cases.manifest.json (for axes summary)")
    ap.add_argument("--space", default="IPRB")
    ap.add_argument(
        "--test-cases-folder-id",
        default="420544515",
        help="Confluence folder ID for the 'Test Cases' folder (default = production IPRB)",
    )
    ap.add_argument("--subfolder-title", default="ROW Test", choices=["ROW Test", "UAE Test"])
    ap.add_argument("--page-title", required=True, help="Title for the new page")
    ap.add_argument("--ticket-id", required=True, help="e.g. PROD-1930")
    ap.add_argument("--product-label", required=True, help="e.g. Morgan Price (Flexible Choices)")
    ap.add_argument("--base-url", default=BASE_URL_DEFAULT)
    args = ap.parse_args()

    if not args.csv.is_file():
        print(f"CSV not found: {args.csv}", file=sys.stderr)
        sys.exit(2)

    # Build axes summary
    axes_summary = ""
    row_count = 0
    if args.manifest and args.manifest.is_file():
        m = json.loads(args.manifest.read_text())
        row_count = m.get("row_count", 0)
        axes = m.get("axis_coverage", {})
        axes_summary = ", ".join(f"{k} ({len(v)})" for k, v in axes.items())

    print(f"Auth: {os.environ.get('ATLASSIAN_EMAIL', '<missing>')}")
    _check_token_expiry()
    print(f"Space: {args.space}")
    print(f"Test Cases folder ID: {args.test_cases_folder_id}")
    print(f"Subfolder: {args.subfolder_title}")
    print(f"New page title: {args.page_title}")
    print(f"CSV: {args.csv}")
    print()

    # 1. Verify space exists (sanity check; we use the folder ID directly afterwards)
    print(f"[1/6] Looking up space '{args.space}'…")
    space = find_space(args.base_url, args.space)
    print(f"      ✓ Found: {space.get('name')} (id={space.get('id')})")

    # 2. List children of the 'Test Cases' FOLDER (using V2 API since folders aren't pages)
    print(f"[2/6] Listing children of Test Cases folder (id={args.test_cases_folder_id})…")
    folder_children = list_folder_children_v2(args.base_url, args.test_cases_folder_id)
    print(f"      → {len(folder_children)} children")
    for c in folder_children[:10]:
        print(f"         - [{c.get('type')}] {c.get('title')!r} (id={c.get('id')})")

    # 3. Find ROW Test (or UAE Test) subfolder
    print(f"[3/6] Looking for '{args.subfolder_title}' among Test Cases children…")
    subfolder = next(
        (c for c in folder_children if c.get("title", "").strip().lower() == args.subfolder_title.strip().lower()),
        None,
    )
    if not subfolder:
        print(f"      ✗ '{args.subfolder_title}' not found under Test Cases", file=sys.stderr)
        sys.exit(2)
    subfolder_id = subfolder["id"]
    subfolder_type = subfolder.get("type", "page")
    print(f"      ✓ Found: id={subfolder_id} (type={subfolder_type})")

    # 4. Read direct children of ROW Test for convention reference
    print(f"[4/6] Reading existing pages under '{args.subfolder_title}'…")
    siblings = list_folder_children_v2(args.base_url, subfolder_id)
    print(f"      → {len(siblings)} existing items")
    for sib in siblings[:5]:
        print(f"         - [{sib.get('type')}] {sib.get('title')!r} (id={sib['id']})")

    # 5. Create the new page (or update existing) — V1 API for create+update, with the folder/page as ancestor
    print(f"[5/6] Creating page '{args.page_title}' under '{args.subfolder_title}'…")
    csv_text = args.csv.read_text(encoding="utf-8")
    body_html = build_page_body(
        csv_filename=args.csv.name,
        ticket_id=args.ticket_id,
        product_label=args.product_label,
        row_count=row_count,
        axes_summary=axes_summary,
        csv_content=csv_text,
    )
    existing = next(
        (c for c in siblings if c.get("title", "").strip().lower() == args.page_title.strip().lower()),
        None,
    )
    if existing:
        full = _api("GET", f"/rest/api/content/{existing['id']}?expand=version", args.base_url)[1]
        v = full["version"]["number"]
        page = update_page(args.base_url, existing["id"], args.page_title, body_html, current_version=v)
        action = "updated"
    else:
        page = create_page(args.base_url, args.space, subfolder_id, args.page_title, body_html)
        action = "created"
    page_id = page["id"]
    page_url = args.base_url + page["_links"]["webui"]
    print(f"      ✓ Page {action}: {page_url}")

    # 6. Upload (or replace) the CSV attachment
    print(f"[6/6] Uploading attachment '{args.csv.name}' to page {page_id}…")
    upload_attachment(args.base_url, page_id, args.csv)
    print(f"      ✓ Attached")

    print()
    print("==== UPLOAD COMPLETE ====")
    print(f"Page URL: {page_url}")
    print(f"Page ID:  {page_id}")
    print(f"CSV:      {args.csv.name} (attached + referenced via View File macro)")


if __name__ == "__main__":
    main()
