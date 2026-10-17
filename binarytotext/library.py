"""Rule library: a folder of rule set JSON files, picked per file by their match pattern."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .rules import RuleSet, parse_tag


@dataclass
class Entry:
    path: Path
    ruleset: RuleSet

    @property
    def specificity(self) -> int:
        """Number of fixed (non-??) bytes in the match pattern."""
        try:
            return sum(b is not None for b in parse_tag(self.ruleset.match_pattern))
        except ValueError:
            return 0


def scan(folder: str | Path) -> tuple[list[Entry], list[str]]:
    """Load every *.json rule set in folder (not recursive). Returns (entries, errors)."""
    entries, errors = [], []
    root = Path(folder)
    if not root.is_dir():
        return [], [f"규칙 폴더가 없습니다: {folder}"]
    for p in sorted(root.glob("*.json")):
        try:
            rs = RuleSet.load(p)
        except Exception as e:  # a broken file must not hide the others
            errors.append(f"{p.name}: {e}")
            continue
        entries.append(Entry(p, rs))
    return entries, errors


def find(data: bytes, entries: list[Entry]) -> list[Entry]:
    """Rule sets whose pattern matches data, most specific first."""
    hits = [e for e in entries if e.ruleset.matches(data)]
    hits.sort(key=lambda e: (-e.specificity, e.path.name))
    return hits
