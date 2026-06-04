"""
CSV writer for the 27-column Playwright test-case format.

Matches the schema in SaleslabbProject/tests/api/testcase_data/csv_to_json_Helpers.ts.
Handles:
  - Primary rows (one per test scenario; populate the full row)
  - Continuation rows for multi-benefit selections (blank Name; only BenefitType/Label/Description)
  - Continuation rows for additional members (blank Name; customer fields + Relation)
  - CSV quoting for cells containing commas, quotes, or newlines
"""

import csv
import json
from pathlib import Path
from typing import List


HEADER = [
    "Name", "Disabled",
    "FirstName", "LastName", "Email", "Age", "Day", "Month",
    "Gender", "MaritalStatus", "Nationality", "Residency", "Relation",
    "Provider", "Plan", "Network", "Coverage",
    "IP/Deductible", "OP/Deductible", "PaymentFrequency",
    "BenefitType", "BenefitLabel", "BenefitDescription",
    "ExpectedPlanName", "ExpectedPriceWithoutTax", "ExpectedPriceWithTax",
    "PID",   # product identifier — matched against provider.productID at runtime
]


def _build_primary_row(case: dict, first_benefit: dict | None, pid: str) -> List[str]:
    """First row of a test case carries the full selection set."""
    c = case["customer"]
    sel = case["selections"]
    expected_with_tax = case.get("expected_price_with_tax", case["expected_price"])
    return [
        case["name"],
        "false",
        c["firstName"], c["lastName"], c["email"],
        str(c["age"]), c["day"], c["month"],
        c["gender"], c["maritalStatus"],
        c["nationality"], c["residency"], c.get("relation", ""),
        sel["Provider"], sel["Plan"], sel.get("Network", ""), sel["Coverage"],
        sel.get("IP/Deductible", ""), sel.get("OP/Deductible", ""), sel["PaymentFrequency"],
        first_benefit["title"]       if first_benefit else "",
        first_benefit["label"]       if first_benefit else "",
        first_benefit["description"] if first_benefit else "",
        case["expected_plan_name"],
        case["expected_price"],
        expected_with_tax,
        pid,
    ]


def _build_benefit_continuation_row(benefit: dict) -> List[str]:
    """Continuation row for an extra benefit selection on the previous test case."""
    return [
        "", "",                                # Name, Disabled (blank → attach to previous)
        "", "", "", "", "", "",                # customer fields
        "", "", "", "", "",                    # gender..relation
        "", "", "", "",                        # provider..coverage
        "", "", "",                            # deductibles, freq
        benefit["title"], benefit["label"], benefit["description"],
        "", "", "",                            # expected fields
        "",                                    # PID (blank on continuation rows)
    ]


def _build_member_continuation_row(member: dict, primary_residency: str, primary_nationality: str) -> List[str]:
    """Continuation row for an additional member (spouse/child) on the previous test case."""
    return [
        "", "",
        member["firstName"], member["lastName"], member.get("email", ""),
        str(member["age"]), member["day"], member["month"],
        member["gender"], member.get("maritalStatus", ""),
        primary_nationality, primary_residency, member["relation"],
        "", "", "", "",
        "", "", "",
        "", "", "",
        "", "", "",
        "",   # PID (blank on continuation rows)
    ]


def write_csv(cases: List[dict], output_path: Path, pid: str = "") -> None:
    """
    Write the list of test cases to a CSV file at output_path.

    `pid` is the product identifier (e.g. "PID-9A429" for BUPA DAC Monaco/France).
    It's written as the last column on every primary row so the Playwright runner's
    provider-match check (`String(p?.productID) === String(testCase.PID)`) works
    without needing the page-name suffix appended at fetch time.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(HEADER)
        for case in cases:
            benefits = case.get("benefits", []) or []

            # Primary row carries the first benefit (if any).
            first = benefits[0] if benefits else None
            writer.writerow(_build_primary_row(case, first, pid))

            # Subsequent benefits as continuation rows.
            for extra in benefits[1:]:
                writer.writerow(_build_benefit_continuation_row(extra))

            # Members (spouse, child, …) as continuation rows.
            for member in case.get("members", []):
                writer.writerow(_build_member_continuation_row(
                    member,
                    primary_residency=case["customer"]["residency"],
                    primary_nationality=case["customer"]["nationality"],
                ))


def write_manifest(cases: List[dict], output_path: Path, tier: str, insurer: str) -> None:
    """
    Write a manifest JSON that auditors can read to confirm axis coverage.
    """
    axes: dict = {}
    for c in cases:
        sel = c["selections"]
        for key in ["Network", "Coverage", "IP/Deductible", "OP/Deductible", "PaymentFrequency"]:
            axes.setdefault(key, set()).add(sel.get(key, ""))
        for b in c.get("benefits", []) or []:
            axes.setdefault(f"Benefit:{b['title']}", set()).add(b["label"])

    manifest = {
        "insurer": insurer,
        "tier": tier,
        "row_count": len(cases),
        "axis_coverage": {k: sorted(v) for k, v in axes.items()},
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
