"""Intake gate — Tier-2 static validation rule pack for the 5 standardized
onboarding workbooks (info / rateSheet / benefits / addons / conversion).

NDS-1190 Phase 3b, Phase A (G1 retroactive pilot).
Spec sources:
  - auto-onboarding/SalesLab-Rate-Ingestion-Schema.md
  - auto-onboarding/SalesLab-Data-Model-Glossary.md
  - auto-onboarding/CLAUDE.md (§12 parser bugs, §13 validation playbook)

Deterministic only — no LLM at runtime (same principle as the extractors).
Usage:
    python -m src.intake_gate <ticket-output-folder> [...] [--json out.json]
"""

__version__ = "0.1.0"
