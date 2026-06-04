"""S3 uploader for Playwright test-case CSVs.

Replaces confluence_uploader.py. CSVs land at:
    s3://<bucket>/<provider-slug>/<csv_name>

where <provider-slug> is the provider's display label slugified (spaces → '-',
non-alphanumerics stripped) so the AWS console shows human-readable folders
that IPMs can browse.

Credentials: uses boto3's default chain (env vars / ~/.aws/credentials / IAM
role). Set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY + AWS_REGION in your env
for local runs, or attach an IAM role for CI runs.

Env vars:
    S3_TEST_CASES_BUCKET   default 'playwrite-test-cases'
    AWS_REGION             default 'us-east-1' (override if your bucket is elsewhere)

CLI usage:
    python -m src.common.s3_uploader \\
        --csv /path/to/quote-scenario.csv \\
        --product-label "Bupa DAC (Global Health) EEA" \\
        [--csv-name quote-scenario.csv]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


DEFAULT_BUCKET = os.environ.get("S3_TEST_CASES_BUCKET", "playwrite-test-cases")
DEFAULT_REGION = os.environ.get("AWS_REGION", "us-east-1")
DEFAULT_CSV_NAME = "quote-scenario.csv"


def slugify_product_label(label: str) -> str:
    """Turn "Bupa DAC (Global Health) EEA" → "Bupa-DAC-Global-Health-EEA".

    Rules:
    - Strip punctuation (parens, commas, ampersands, slashes, em/en dashes)
    - Collapse whitespace to single '-'
    - Drop trailing/leading '-'
    - Keep alphanumerics + dashes
    - Preserve case (IPMs prefer reading "Bupa DAC" not "bupa-dac")
    """
    # Remove punctuation but keep alphanumerics and spaces
    cleaned = re.sub(r"[^\w\s-]", "", label, flags=re.UNICODE)
    # Collapse runs of whitespace/dashes to single dash
    slug = re.sub(r"[\s_-]+", "-", cleaned).strip("-")
    return slug


def s3_key_for(provider_label: str, csv_name: str = DEFAULT_CSV_NAME) -> str:
    """Compose the canonical S3 key for a provider's CSV."""
    slug = slugify_product_label(provider_label)
    if not slug:
        raise ValueError(f"product_label slugified to empty: {label!r}")
    return f"{slug}/{csv_name}"


def upload_csv(
    csv_path: Path,
    provider_label: str,
    csv_name: str = DEFAULT_CSV_NAME,
    bucket: str = DEFAULT_BUCKET,
    region: str = DEFAULT_REGION,
) -> dict:
    """Upload a CSV to s3://<bucket>/<slug>/<csv_name>.

    Returns {'bucket', 'key', 'url', 'size'} on success.
    Raises ClientError on AWS failures.
    """
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    key = s3_key_for(provider_label, csv_name)
    body = csv_path.read_bytes()

    client = boto3.client("s3", region_name=region)
    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="text/csv",
            ContentDisposition=f'inline; filename="{csv_name}"',
            Metadata={
                "product-label": provider_label,
                "source": "test-case-generator",
            },
        )
    except ClientError as e:
        # Re-raise with a friendlier message but keep the boto exception type.
        code = e.response.get("Error", {}).get("Code", "Unknown")
        msg = e.response.get("Error", {}).get("Message", str(e))
        print(
            f"[s3_uploader] AWS error {code}: {msg}\n"
            f"  Bucket: s3://{bucket}/{key}\n"
            f"  Region: {region}\n"
            f"  Check: bucket exists, IAM has s3:PutObject, region matches",
            file=sys.stderr,
        )
        raise

    url = f"https://{bucket}.s3.{region}.amazonaws.com/{key}"
    return {"bucket": bucket, "key": key, "url": url, "size": len(body)}


def main():
    ap = argparse.ArgumentParser(prog="s3_uploader")
    ap.add_argument("--csv", required=True, type=Path)
    ap.add_argument(
        "--product-label",
        required=True,
        help='Human-readable provider name, e.g. "Bupa DAC (Global Health) EEA". '
        "Used to derive the S3 folder slug.",
    )
    ap.add_argument(
        "--csv-name",
        default=DEFAULT_CSV_NAME,
        help=f"Object name within the slug folder (default: {DEFAULT_CSV_NAME})",
    )
    ap.add_argument(
        "--bucket",
        default=DEFAULT_BUCKET,
        help=f"S3 bucket (default: {DEFAULT_BUCKET}, override via $S3_TEST_CASES_BUCKET)",
    )
    ap.add_argument(
        "--region",
        default=DEFAULT_REGION,
        help=f"AWS region (default: {DEFAULT_REGION}, override via $AWS_REGION)",
    )
    args = ap.parse_args()

    print(
        f"[1/2] Slugifying product label…\n"
        f"      label = {args.product_label!r}\n"
        f"      slug  = {slugify_product_label(args.product_label)!r}"
    )
    print(f"[2/2] Uploading {args.csv.name} → s3://{args.bucket}/{s3_key_for(args.product_label, args.csv_name)}")
    result = upload_csv(
        csv_path=args.csv,
        provider_label=args.product_label,
        csv_name=args.csv_name,
        bucket=args.bucket,
        region=args.region,
    )
    print(
        f"\n==== UPLOAD COMPLETE ====\n"
        f"URL:  {result['url']}\n"
        f"Size: {result['size']:,} bytes\n"
    )


if __name__ == "__main__":
    main()
