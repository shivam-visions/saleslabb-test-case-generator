"""
Helpers for resolving the upload-tool script folder for a given product/insurer.

This is a defensive layer: the user has many similarly-named folders under
`upload-tool/scripts/` (e.g., AXA, AXA_Kenya, AXA_Global_Health_ROW; Allianz,
Allianz_Care, Allianz_Britcare, Allianz_Britcare_AUH, Allianz_Care_Test, ...).
Auto-picking the wrong one would cause Phase 2's fast verifier to compare
against the wrong upload bundle.

Convention:
  - The caller passes the user's intended folder name via `--upload-tool-folder`.
  - If omitted, we scan `upload-tool/scripts/` for entries whose name contains
    the insurer keyword (e.g. 'Xen', 'Morgan_Price') and require the user to
    pick one interactively. No silent defaulting.
"""

import sys
from pathlib import Path
from typing import Iterable, Optional


def list_candidate_folders(upload_tool_root: Path, insurer_keyword: str) -> list[Path]:
    """Return sub-folders of <upload-tool>/scripts/ that contain insurer_keyword (case-insensitive)."""
    scripts_dir = upload_tool_root / "scripts"
    if not scripts_dir.is_dir():
        return []
    kw = insurer_keyword.lower()
    return sorted([
        p for p in scripts_dir.iterdir()
        if p.is_dir() and kw in p.name.lower() and not p.name.startswith(".")
    ])


def resolve_upload_tool_folder(
    upload_tool_root: Path,
    insurer_keyword: str,
    explicit_choice: Optional[str],
    interactive: bool = True,
) -> Path:
    """
    Return the absolute path to the upload-tool script folder for the product.

    Order of resolution:
      1. If `explicit_choice` is set, verify it exists and return it (no prompts).
      2. Otherwise, scan for candidates by insurer_keyword.
         - 1 candidate: prompt user to confirm. (We do NOT silently auto-pick
           even one candidate — too many false positives historically.)
         - 0 or >1 candidates: prompt user to choose from the list, or to type
           an explicit name.
    """
    scripts_dir = upload_tool_root / "scripts"
    if not scripts_dir.is_dir():
        print(f"ERROR: {scripts_dir} does not exist.", file=sys.stderr)
        sys.exit(2)

    if explicit_choice:
        target = scripts_dir / explicit_choice
        if not target.is_dir():
            print(f"ERROR: explicit --upload-tool-folder '{explicit_choice}' not found at {target}", file=sys.stderr)
            sys.exit(2)
        return target

    candidates = list_candidate_folders(upload_tool_root, insurer_keyword)
    if not interactive:
        if len(candidates) == 1:
            return candidates[0]
        print(
            f"ERROR: non-interactive mode but {len(candidates)} candidates for keyword "
            f"'{insurer_keyword}'. Pass --upload-tool-folder=<name> explicitly.",
            file=sys.stderr,
        )
        sys.exit(2)

    return _prompt_for_choice(scripts_dir, insurer_keyword, candidates)


def _prompt_for_choice(scripts_dir: Path, insurer_keyword: str, candidates: list[Path]) -> Path:
    """Interactive prompt — never silently picks a folder."""
    print()
    print(f"Need to confirm the upload-tool script folder for keyword '{insurer_keyword}'.")
    print(f"Looking under: {scripts_dir}")
    print()
    if candidates:
        print("Candidates found:")
        for i, c in enumerate(candidates, 1):
            print(f"  [{i}] {c.name}")
        print(f"  [0] None of the above — type a different name")
    else:
        print("No candidates matched. Type the folder name manually.")
    print()

    while True:
        choice = input("Enter the number, OR type the exact folder name: ").strip()
        # Number?
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(candidates):
                selected = candidates[idx - 1]
                print(f"✓ Using: {selected.name}")
                return selected
            if idx == 0:
                continue  # fall through to typed-name path
            print(f"  Out of range. Pick 0–{len(candidates)}.")
            continue
        # Typed name?
        if choice:
            target = scripts_dir / choice
            if target.is_dir():
                print(f"✓ Using: {target.name}")
                return target
            print(f"  Folder '{choice}' does not exist under {scripts_dir}. Try again.")
            continue
        print("  Empty input. Try again.")
