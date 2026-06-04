"""Render RunResults to a self-contained HTML report — a preview of the
Phase C dashboard results table the IPMs will see."""
from __future__ import annotations

import html
from pathlib import Path

from .models import Severity

_CSS = """
body{font-family:'Segoe UI',system-ui,sans-serif;background:#f6f7fb;margin:0;padding:32px;color:#1a1a2e}
h1{font-size:22px;color:#3b2d63}h1 small{color:#888;font-weight:400;font-size:13px;display:block;margin-top:4px}
.card{background:#fff;border-radius:10px;box-shadow:0 1px 4px rgba(60,40,120,.10);margin:18px 0;padding:18px 22px}
.verdict{display:inline-block;padding:3px 14px;border-radius:14px;font-weight:600;font-size:13px}
.PASS{background:#e3f6e8;color:#1a7f37}.PASS_WITH_WARNINGS{background:#fff3d6;color:#9a6700}.FAIL{background:#ffe3e3;color:#c0202a}
table{border-collapse:collapse;width:100%;margin-top:12px;font-size:13px}
th{text-align:left;color:#6b6b80;font-weight:600;padding:6px 10px;border-bottom:2px solid #ece9f4}
td{padding:6px 10px;border-bottom:1px solid #f0eef7;vertical-align:top}
.sev{font-weight:700;border-radius:4px;padding:1px 8px;font-size:12px;white-space:nowrap}
.sev.ERROR{background:#ffe3e3;color:#c0202a}.sev.WARN{background:#fff3d6;color:#9a6700}.sev.INFO{background:#eef1ff;color:#3d5af1}
.rule{font-family:ui-monospace,monospace;font-size:12px;color:#5b4b8a;white-space:nowrap}
.loc{color:#888;font-size:12px;white-space:nowrap}
.files{color:#666;font-size:12px;margin:4px 0 0}
.counts{margin-left:14px;color:#666;font-size:13px}
"""


def render(results: list, title: str = "Intake Gate — Validation Report") -> str:
    parts = [f"<!doctype html><html><head><meta charset='utf-8'>"
             f"<title>{html.escape(title)}</title><style>{_CSS}</style></head><body>"
             f"<h1>{html.escape(title)}"
             f"<small>NDS-1190 Phase 3b · Tier-2 static validation · "
             f"deterministic rule pack (no LLM in the verdict)</small></h1>"]
    for res in results:
        c = res.to_dict()["counts"]
        bundle = html.escape(Path(res.folder).name)
        parts.append(
            f"<div class='card'><span class='verdict {res.verdict}'>"
            f"{res.verdict.replace('_', ' ')}</span>"
            f"<strong style='margin-left:12px'>{bundle}</strong>"
            f"<span class='counts'>{c['error']} error · {c['warn']} warn · "
            f"{c['info']} info</span>"
            f"<div class='files'>{html.escape(', '.join(res.files))}</div>")
        shown = [f for f in res.findings if f.severity != Severity.INFO]
        if shown:
            parts.append("<table><tr><th>Severity</th><th>Rule</th>"
                         "<th>Location</th><th>What needs attention</th></tr>")
            for f in shown:
                loc = f.file or "bundle"
                if f.sheet:
                    loc += f" · {f.sheet}"
                if f.row:
                    loc += f" · row {f.row}"
                parts.append(
                    f"<tr><td><span class='sev {f.severity.value}'>"
                    f"{f.severity.value}</span></td>"
                    f"<td class='rule'>{f.rule}</td>"
                    f"<td class='loc'>{html.escape(loc)}</td>"
                    f"<td>{html.escape(f.message)}</td></tr>")
            parts.append("</table>")
        else:
            parts.append("<p style='color:#1a7f37;margin:10px 0 2px'>"
                         "All checks passed — ready for the Jira gate.</p>")
        parts.append("</div>")
    parts.append("</body></html>")
    return "".join(parts)
