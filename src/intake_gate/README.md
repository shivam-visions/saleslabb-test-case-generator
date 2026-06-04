# intake_gate — Tier-2 static validation rule pack

Phase A of **NDS-1190 Phase 3b** (Staged Intake Pipeline & Tiered Validation Gate —
see `MIRO/docs/IPM-Validation-Gate-Proposal-v2.md` / Confluence
https://vitavirtues.atlassian.net/wiki/x/DYAzIw).

Validates a 5-workbook onboarding bundle (info / rateSheet / benefits / addons /
conversion .xlsx) **statically and carrier-agnostically** — no MongoDB, no LLM,
no per-insurer extractor needed. This is the all-carriers Tier-2 layer; Tier-3
(value-level verification) remains the per-insurer `verifier.py` modules.

## Run

```bash
.venv/bin/python -m src.intake_gate <ticket-output-folder> [...] [--json out.json] [-v]
```

Exit codes: `0` PASS / PASS_WITH_WARNINGS, `2` any FAIL.

## Rule groups

| Prefix | Source of truth | What it checks |
|---|---|---|
| T1-* | persona CLAUDE.md §2 deliverables | file inventory incl. conditional addons/conversion requirements, residency-file parity |
| T2-INFO-* | Rate-Ingestion-Schema §info | required columns, ISO currency/date, slash-list alignment, frequency/ageCalculationMethod enums |
| T2-RATE-* | schema §rateSheet + CLAUDE.md §12 | age-band continuity/overlap per filter group, numeric rates, join-key whitespace, §12.9 multi-currency column, enum casing |
| T2-BEN-* | schema §benefits | header shape, User Type enum, special rows (Annual Limit single-line format, Geo, Network, Filters/Copays), Starter rows, `$` substitution row, known typos/invisible chars |
| T2-ADD-* | schema §addons | parent/child sheet pattern, type enum, sheetName refs (31-char truncation), flag→child-row coverage (§12.6), conditional-override pitfalls |
| T2-X-* | glossary join keys | planName / copay / network / coverage exact-match joins, addon identity triangle (info.addons ↔ parent **sheet names** ↔ benefits rows), multi-residency plan parity |

ERROR = gate-blocking, WARN = human review (breakpoint B4), severity calibrated
retroactively against shipped bundles (see below). Findings carry rule id +
file/sheet/row so a dashboard can render them directly.

## Pilot calibration record (2026-06-04, G1 evidence)

Retroactive run on 4 shipped production bundles — PROD-2022-2039 (Xen),
PROD-1985 (BUPA DAC Monaco/France), PROD-1930 (Morgan Price ROW),
PROD-1845 (Now Health AUH): **0 false-positive ERRORs** after calibration;
remaining WARNs are genuine review items (e.g. duplicate `Vaccination` in
PROD-1845 info.addons).

Calibrations learned from shipped data (schema doc corrections):
1. **Addon identity is the parent sheet name** (31-char xlsx truncation), not the
   `label` cell — `label` is the per-option label.
2. `flag='none'` / `type='none'` rows are display-only options with no child-rate
   lookup.
3. The rateTable dialect (template B) ships **without `currentRates`** and with
   `copayTypes` lacking the trailing slash.
4. The rateSheet `residency` column can carry non-residency-key condition values
   in some dialects → WARN, not ERROR.

Seeded-defect run (Xen copy, 6 defect classes: missing addons file, planName
typo, copay trailing space, non-numeric rate, age-band overlap, multi-line
Annual Limit): **6/6 caught**, each with the right rule and row.

**G1 follow-up rules (added 2026-06-04 evening, lifting static catch-rate
15% → 35%):** T2-ADD-007 maternity eligibility-condition pattern (WARN for the
older no-condition-columns dialect), T2-ADD-008 exact-duplicate option rows
(key includes description; per-sheet aggregate), T2-BEN-010 copay/deductible
option ordering, T2-BEN-011 duplicate copay tokens, T2-RATE-013
plan×coverage×currency completeness, T2-INFO-012 non-UAE VAT advisory (INFO).
Regression: 4/4 shipped bundles still pass with 0 errors; the new BEN-010
warnings on PROD-1985/1930 reproduce the actual G1 #16 dev query.

## Maintenance

Rules encode the dev team's "missing / off" definitions (proposal §7 "Roles").
When the schema doc or a parser changes, update the matching rule and re-run the
retroactive set above as the regression suite.
