#!/usr/bin/env bash
# NDS-1190 Phase 3b — intake gate demo (Phase A / G1)
# Runs the Tier-2 static validation gate over:
#   1. four shipped production bundles  -> expected: PASS (with review warnings)
#   2. a deliberately broken bundle     -> expected: FAIL on the seeded defects
# Outputs console results + dashboard-preview HTML reports in demo-output/.
set -euo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python
OUT=demo-output
mkdir -p "$OUT"

AOB="/home/support/Desktop/MIRO/auto-onboarding"
SHIPPED=(
  "$AOB/PROD-2022-2039-PlanUpdate-XenHealth-AbuDhabi-DubaiNE/PROD-2022-2039-output"
  "$AOB/PROD-1985-PlanUpdate-BupaDac-Monaco-France/PROD-1985-output"
  "$AOB/PROD-Morgan-Price-ROW-Enlistment/PROD-1930-output"
  "$AOB/PROD-1845 Plan Update – Now Health (World Care) – Res: Abu Dhabi/PROD-1845-output"
)

echo "════════════════════════════════════════════════════════════════════"
echo " PART 1 — four SHIPPED production bundles (retroactive calibration)"
echo "════════════════════════════════════════════════════════════════════"
$PY -m src.intake_gate "${SHIPPED[@]}" \
    --json "$OUT/shipped.json" --html "$OUT/shipped-report.html"

echo
echo "════════════════════════════════════════════════════════════════════"
echo " PART 2 — seeding 6 classic defects into a copy of the Xen bundle"
echo "   missing addons file · planName typo · copay trailing space"
echo "   non-numeric rate · age-band overlap · multi-line Annual Limit"
echo "════════════════════════════════════════════════════════════════════"
BROKEN="$OUT/broken-bundle"
rm -rf "$BROKEN"; mkdir -p "$BROKEN"
$PY - "$BROKEN" <<'EOF'
import sys, shutil, os
from openpyxl import load_workbook
src = "/home/support/Desktop/MIRO/auto-onboarding/PROD-2022-2039-PlanUpdate-XenHealth-AbuDhabi-DubaiNE/PROD-2022-2039-output"
dst = sys.argv[1]
for f in os.listdir(src):
    if f.endswith(".xlsx") and not f.startswith("addons"):  # defect 1: drop addons
        shutil.copy(os.path.join(src, f), dst)
wb = load_workbook(os.path.join(dst, "rateSheet.xlsx")); ws = wb.active
h = {c.value: i + 1 for i, c in enumerate(ws[1])}
ws.cell(row=3, column=h["planName"]).value = "Xen Compleet"     # defect 2
ws.cell(row=5, column=h["copay"]).value = "20% copay "          # defect 3
ws.cell(row=7, column=h["rates"]).value = "TBD"                 # defect 4
ws.cell(row=9, column=h["ageStart"]).value = 18                 # defect 5
ws.cell(row=9, column=h["ageEnd"]).value = 45
wb.save(os.path.join(dst, "rateSheet.xlsx"))
wb = load_workbook(os.path.join(dst, "benefits.xlsx")); ws = wb.active
for r in range(2, ws.max_row + 1):
    if ws.cell(row=r, column=2).value == "Annual Limit":
        ws.cell(row=r, column=3).value = "AED 150,000\nplus extras"  # defect 6
        break
wb.save(os.path.join(dst, "benefits.xlsx"))
print("broken bundle seeded:", sorted(os.listdir(dst)))
EOF

$PY -m src.intake_gate "$BROKEN" \
    --json "$OUT/broken.json" --html "$OUT/broken-report.html" || true

echo
echo "════════════════════════════════════════════════════════════════════"
echo " Demo artifacts (open the HTML files for the dashboard preview):"
echo "   $OUT/shipped-report.html   <- what a clean IPM run looks like"
echo "   $OUT/broken-report.html    <- what the IPM sees when data is off"
echo "   src/intake_gate/G1-RETROACTIVE-PILOT.md  <- catch-rate evidence"
echo "════════════════════════════════════════════════════════════════════"
