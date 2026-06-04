"""Core data types for the intake gate."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Severity(str, Enum):
    ERROR = "ERROR"  # gate-blocking
    WARN = "WARN"    # human review (breakpoint B4 surfaces these)
    INFO = "INFO"


@dataclass
class Finding:
    rule: str
    severity: Severity
    file: str            # file name relative to bundle root ('' for bundle-level)
    message: str
    sheet: str | None = None
    row: int | None = None

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "severity": self.severity.value,
            "file": self.file,
            "sheet": self.sheet,
            "row": self.row,
            "message": self.message,
        }


@dataclass
class Sheet:
    name: str
    header: list          # raw first-row cells
    rows: list            # list of tuples (raw cell values), data rows only
    first_data_row: int = 2  # 1-based xlsx row number of rows[0]

    def col_index(self, name: str) -> int | None:
        for i, h in enumerate(self.header):
            if isinstance(h, str) and h.strip() == name:
                return i
        return None

    def dicts(self) -> list[dict]:
        """Rows as dicts keyed by stripped string headers (None headers skipped)."""
        keys = [(i, h.strip()) for i, h in enumerate(self.header) if isinstance(h, str)]
        out = []
        for r in self.rows:
            out.append({k: (r[i] if i < len(r) else None) for i, k in keys})
        return out


@dataclass
class WorkbookFile:
    path: Path
    role: str             # info | rateSheet | benefits | addons | conversion
    suffix: str           # '' | '1' | '2' ...
    sheets: list[Sheet] = field(default_factory=list)
    load_error: str | None = None

    @property
    def name(self) -> str:
        return self.path.name

    def main(self) -> Sheet | None:
        return self.sheets[0] if self.sheets else None


@dataclass
class Bundle:
    root: Path
    files: list[WorkbookFile] = field(default_factory=list)

    def by_role(self, role: str) -> list[WorkbookFile]:
        return sorted((f for f in self.files if f.role == role),
                      key=lambda f: int(f.suffix) if f.suffix else -1)

    def one(self, role: str) -> WorkbookFile | None:
        fs = self.by_role(role)
        return fs[0] if fs else None


# ---- shared vocab (Rate-Ingestion-Schema + Glossary) ----

KNOWN_CURRENCIES = {"USD", "AED", "EUR", "GBP", "SGD", "HKD"}
AGE_CALC_METHODS = {"advanced", "standard", "defaultage"}
FREQ_TOKENS = {"Annually", "month", "quarter", "semiAnnual"}
RATESHEET_FREQ = {"Annually", "month", "quarter", "semiAnnual"}
USER_TYPES = {"none", "type", "Pro", "All", "Starter"}
ADDON_TYPES = {"fixed", "percentage", "conditional-fixed", "conditional-override", "none"}
GENDERS = {"male", "female"}
BOOL_STR = {"true", "false"}

# benefits special rows
ROW_ANNUAL_LIMIT = "Annual Limit"
ROW_GEO = "Geographical Coverage"
ROW_NETWORK = "Network Details"
ROW_FILTERS = "Filters"
COPAY_ROWS = {"Copays", "Co-pay/excess"}
STARTER_ROWS = {"In-patient (Hospitalization & Surgery)", "Out-patient benefits"}

# typo scan (CLAUDE.md §5 cleanups)
KNOWN_TYPOS = ["Coveredin", "vists", "untrasound", "AED AED", "cop-pay"]
BAD_CHARS = {"\xa0": "non-breaking space", "‌": "zero-width non-joiner",
             "​": "zero-width space"}


def slash_list(value) -> list[str]:
    """Split a slash-separated config cell; drop empty tokens (trailing '/')."""
    if value is None:
        return []
    return [t for t in str(value).split("/") if t != ""]


def is_blank(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def cell_str(v) -> str:
    return "" if v is None else str(v)
