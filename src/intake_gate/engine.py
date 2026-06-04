"""Rule orchestration: load a bundle, run all rule groups, produce a verdict."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import loader
from .models import Bundle, Finding, Severity
from .rules_addons import run_addons
from .rules_benefits import run_benefits
from .rules_bundle import run_info, run_inventory
from .rules_cross import run_cross
from .rules_ratesheet import run_ratesheet


@dataclass
class Context:
    bundle: Bundle
    cfg: dict
    findings: list[Finding] = field(default_factory=list)

    def add(self, rule: str, severity: Severity, file: str, message: str,
            sheet: str | None = None, row: int | None = None) -> None:
        self.findings.append(Finding(rule=rule, severity=severity, file=file,
                                     message=message, sheet=sheet, row=row))


@dataclass
class RunResult:
    folder: str
    verdict: str                 # PASS | PASS_WITH_WARNINGS | FAIL
    findings: list[Finding]
    files: list[str]

    def to_dict(self) -> dict:
        sev = [f.severity for f in self.findings]
        return {
            "folder": self.folder,
            "verdict": self.verdict,
            "files": self.files,
            "counts": {
                "error": sum(1 for s in sev if s == Severity.ERROR),
                "warn": sum(1 for s in sev if s == Severity.WARN),
                "info": sum(1 for s in sev if s == Severity.INFO),
            },
            "findings": [f.to_dict() for f in self.findings],
        }


def validate(folder: str | Path) -> RunResult:
    bundle = loader.discover(folder)
    cfg = loader.info_config(bundle)
    ctx = Context(bundle=bundle, cfg=cfg)

    run_inventory(ctx)
    if bundle.one("info"):
        run_info(ctx)
        run_ratesheet(ctx)
        run_benefits(ctx)
        run_addons(ctx)
        run_cross(ctx)

    sevs = {f.severity for f in ctx.findings}
    verdict = ("FAIL" if Severity.ERROR in sevs
               else "PASS_WITH_WARNINGS" if Severity.WARN in sevs
               else "PASS")
    return RunResult(folder=str(folder), verdict=verdict,
                     findings=ctx.findings,
                     files=[f.name for f in bundle.files])
