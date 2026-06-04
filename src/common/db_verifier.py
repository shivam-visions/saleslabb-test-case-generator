"""
MongoDB-direct verifier framework (Phase 2).

Common bits — env loading, DB connection, document lookups, comparison results.
Per-insurer verification logic lives in insurers/<name>/verifier.py.

Categories match the Playwright bug-categorization taxonomy (see
SaleslabbProject/tests/api/quote/categorized-error.ts) so a developer
reading the fast-verifier output can correlate with Playwright failures.
"""

import os
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Optional

import pymongo


class Category(str, Enum):
    PRICE_MISMATCH               = "PRICE_MISMATCH"
    MISSING_PROVIDER             = "MISSING_PROVIDER"
    MISSING_PLAN                 = "MISSING_PLAN"
    MISSING_COVERAGE             = "MISSING_COVERAGE"
    MISSING_NETWORK              = "MISSING_NETWORK"
    MISSING_DEDUCTIBLE           = "MISSING_DEDUCTIBLE"
    MISSING_BENEFIT_OPTION       = "MISSING_BENEFIT_OPTION"
    MISSING_RESIDENCY_MATCH      = "MISSING_RESIDENCY_MATCH"
    MISSING_PAYMENT_FREQUENCY    = "MISSING_PAYMENT_FREQUENCY"
    MISSING_ANNUAL_LIMIT         = "MISSING_ANNUAL_LIMIT"
    MISSING_TAX_APPLICABLE       = "MISSING_TAX_APPLICABLE"
    SCHEMA_ERROR                 = "SCHEMA_ERROR"
    CUSTOM_CONDITION_FAILED      = "CUSTOM_CONDITION_FAILED"
    UNCATEGORIZED                = "UNCATEGORIZED"


@dataclass
class Finding:
    """One verification check that either passed (ok=True) or failed."""
    ok: bool
    category: Category
    label: str                      # human-readable check name
    expected: Any = None
    actual: Any = None
    detail: str = ""


@dataclass
class VerifyReport:
    """Aggregated results from one full verifier run for one product."""
    insurer: str
    environment: str                # 'local' / 'staging' / 'production'
    provider_title: str
    findings: list[Finding] = field(default_factory=list)

    def add(self, f: Finding) -> None:
        self.findings.append(f)

    @property
    def passed_count(self) -> int:
        return sum(1 for f in self.findings if f.ok)

    @property
    def failed_count(self) -> int:
        return sum(1 for f in self.findings if not f.ok)

    @property
    def is_green(self) -> bool:
        return self.failed_count == 0

    def by_category(self) -> dict[str, int]:
        out: dict[str, int] = {c.value: 0 for c in Category}
        for f in self.findings:
            if not f.ok:
                out[f.category.value] += 1
        return {k: v for k, v in out.items() if v > 0}

    def to_dict(self) -> dict:
        return {
            "insurer": self.insurer,
            "environment": self.environment,
            "provider_title": self.provider_title,
            "totals": {
                "passed": self.passed_count,
                "failed": self.failed_count,
                "is_green": self.is_green,
            },
            "categories": self.by_category(),
            "findings": [
                {
                    "ok": f.ok,
                    "category": f.category.value,
                    "label": f.label,
                    "expected": f.expected,
                    "actual": f.actual,
                    "detail": f.detail,
                }
                for f in self.findings
            ],
        }


# ─────────────────────────────────────────────────────────────────────────────
# Env loading — read MongoDB URLs from the upload-tool's .env
# ─────────────────────────────────────────────────────────────────────────────

ENV_VAR_BY_ENV = {
    "local":      "LOCAL_DB_URL",
    "staging":    "STAGING_DB_URL",
    "production": "PRODUCTION_DB_URL",
}


def load_upload_tool_env(upload_tool_root: Path) -> dict[str, str]:
    """Parse upload-tool/.env (simple KEY=VALUE format) without external deps."""
    env_path = upload_tool_root / ".env"
    if not env_path.is_file():
        raise FileNotFoundError(f"upload-tool .env not found at {env_path}")
    out: dict[str, str] = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # KEY=value or KEY="value"
        m = re.match(r'^([A-Z_][A-Z0-9_]*)\s*=\s*"?(.*?)"?\s*$', line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def get_db_url(upload_tool_root: Path, environment: str) -> str:
    env_var = ENV_VAR_BY_ENV.get(environment.lower())
    if not env_var:
        raise ValueError(f"Unknown environment {environment!r}. Use local/staging/production.")
    envs = load_upload_tool_env(upload_tool_root)
    url = envs.get(env_var)
    if not url:
        raise ValueError(f"{env_var} not set in upload-tool/.env")
    return url


# ─────────────────────────────────────────────────────────────────────────────
# Connection + document lookups
# ─────────────────────────────────────────────────────────────────────────────

def connect(db_url: str) -> pymongo.MongoClient:
    """Open a MongoDB client. Caller is responsible for .close()."""
    client = pymongo.MongoClient(db_url, serverSelectionTimeoutMS=10000)
    # Eager check
    client.admin.command("ping")
    return client


def get_database(client: pymongo.MongoClient, url: str):
    """Use the database name from the connection URL (after the last '/' before '?')."""
    # MongoDB URLs are like mongodb://.../<dbname>?...
    m = re.search(r"/([^/?]+)(?:\?|$)", url)
    if not m or not m.group(1):
        raise ValueError(f"Could not extract DB name from URL: {url}")
    return client[m.group(1)]


def find_provider(db, title_or_regex) -> Optional[dict]:
    """Look up a Provider by title (exact match preferred; regex if no exact)."""
    exact = db["providers"].find_one({"title": title_or_regex})
    if exact:
        return exact
    return db["providers"].find_one({"title": {"$regex": title_or_regex, "$options": "i"}})


def find_plans(db, provider_id) -> list[dict]:
    return list(db["plans"].find({"provider": provider_id}))


def find_pricing_tables(db, plan_ids: Iterable) -> list[dict]:
    return list(db["pricingtables"].find({"plan": {"$in": list(plan_ids)}}))


def find_rate_tables(db, plan_ids: Iterable) -> list[dict]:
    return list(db["ratetables"].find({"plans": {"$in": list(plan_ids)}}))


def find_modifiers(db, plan_ids: Iterable) -> list[dict]:
    return list(db["modifiers"].find({"plans": {"$in": list(plan_ids)}}))


def find_coverages(db, coverage_ids: Iterable) -> list[dict]:
    return list(db["coverages"].find({"_id": {"$in": list(coverage_ids)}}))


# ─────────────────────────────────────────────────────────────────────────────
# Reporting helpers (terminal + JSON)
# ─────────────────────────────────────────────────────────────────────────────

def print_report(report: VerifyReport) -> None:
    print()
    print("──────────────────────────────────────────────")
    print(f"  Fast Verifier — {report.insurer} ({report.environment})")
    print(f"  Provider: {report.provider_title}")
    print("──────────────────────────────────────────────")
    print(f"  Total checks: {len(report.findings)}")
    print(f"  Passed:       {report.passed_count}")
    print(f"  Failed:       {report.failed_count}")
    if report.failed_count == 0:
        # Surface the rate-comparison summaries so it's clear the verifier did real work
        rate_findings = [
            f for f in report.findings
            if f.ok and f.category.value == "PRICE_MISMATCH" and ("Verified" in f.label or "verified" in f.label)
        ]
        if rate_findings:
            print()
            print("  Rate-comparison summary:")
            for f in rate_findings:
                print(f"    ✓ {f.label}")
        print()
        print("  ✓ GREEN — ready to publish")
        print("──────────────────────────────────────────────")
        return
    print()
    print("  Failures by category:")
    for cat, n in sorted(report.by_category().items(), key=lambda x: -x[1]):
        print(f"    {cat:30} {n}")
    print()
    print("  First 10 failures:")
    shown = 0
    for f in report.findings:
        if f.ok:
            continue
        marker = "✗"
        exp = "" if f.expected is None else f"  expected={f.expected}"
        act = "" if f.actual is None else f"  actual={f.actual}"
        print(f"    {marker} [{f.category.value}] {f.label}{exp}{act}")
        if f.detail:
            print(f"      └─ {f.detail}")
        shown += 1
        if shown >= 10:
            remaining = report.failed_count - shown
            if remaining > 0:
                print(f"    … and {remaining} more")
            break
    print("──────────────────────────────────────────────")


def write_json(report: VerifyReport, out_path: Path) -> None:
    import json
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Halt/continue gate
# ─────────────────────────────────────────────────────────────────────────────

def prompt_to_continue() -> bool:
    """Ask the user whether to proceed to Confluence upload."""
    while True:
        ans = input("Publish CSVs to Confluence? (Y/N): ").strip().lower()
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("Please answer Y or N.")
