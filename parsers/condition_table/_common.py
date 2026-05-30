"""Shared types and helpers for both condition-table extraction strategies
(textual digit and Allied colored-image)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ..base import CONDITION_ROW_LABELS


@dataclass
class ConditionRow:
    row: str
    cat: Optional[str]  # "1" / "2" / "3" / "-" / None
    page: Optional[int]
    notes: str = ""


def build_label_alternation() -> str:
    # Sort longer first to avoid partial matches
    labels_sorted = sorted(set(CONDITION_ROW_LABELS), key=len, reverse=True)
    return "(" + "|".join(re.escape(l) for l in labels_sorted) + ")"


# Heuristic phrases that, when found in a Cat 2/3 row's notes, contradict the
# classification (i.e. the surveyor's notes say "no problem" but they still
# stamped Cat 2/3 — a known surveyor conservatism pattern that misleads scoring).
NEGATIVE_NOTES_PATTERNS = [
    r"\bwas\s+not\s+noted\b",
    r"\bno\s+evidence\s+of\b",
    r"\bno\s+significant\s+defects?\b",
    r"\bno\s+visual\s+(?:signs?|defects)\b",
    r"\bgenerally\s+(?:in\s+)?(?:fair|good|satisfactory)\b",
    r"\bin\s+excellent\s+condition\b",
    r"\bnot\s+applicable\b",
    r"\bappears?\s+to\s+be\s+long[- ]?standing\b",
    r"\bnon[- ]?progressive\b",
    # Shepherd cover-their-back: Cat 2 给的同时说"没测、只是例行建议"
    r"\bprecautionary\s+check\b",
    r"\bin\s+accordance\s+with\s+good\s+(?:maintenance\s+)?practice\b",
    r"\bno\s+tests?\s+(?:were|was)?\s*carried\s+out\b",
]


def find_cat_notes_contradictions(condition_table: list[ConditionRow]) -> list[dict]:
    """For each cat≥2 row whose notes contain a "no problem" phrase, return a flag.

    Surfaces cases like the Viewforth Rainwater fittings (Cat 2 but notes:
    "Corrosion and evidence of localised leakage was not noted") — where the
    surveyor was conservative but the actual condition is fine. Skill should
    surface these as "verify on site; don't apply full penalty".
    """
    flags = []
    for r in condition_table:
        if r.cat not in {"2", "3"} or not r.notes:
            continue
        matched = []
        for pat in NEGATIVE_NOTES_PATTERNS:
            if re.search(pat, r.notes, re.I):
                m = re.search(pat, r.notes, re.I)
                matched.append(m.group(0))
        if matched:
            flags.append({
                "row": r.row,
                "cat": r.cat,
                "page": r.page,
                "notes_excerpt": r.notes[:200],
                "negative_phrases": matched,
            })
    return flags
