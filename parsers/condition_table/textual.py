"""Textual condition-table extraction.

Used by Quest / Graham + Sibbald / DM Hall / Shepherd / DHKK — all of which
render the repair category as an inline digit ("Repair Category   1").
"""

from __future__ import annotations

import re

from ..sections import SectionRange
from ._common import ConditionRow, build_label_alternation


def extract_condition_table_textual(pages: list[str], ss_range: SectionRange) -> list[ConditionRow]:
    """
    Quest / DM Hall: digits inline as 'Repair category   1'.
    """
    page_range = (ss_range.start_page or 1, ss_range.end_page or len(pages))
    label_alt = build_label_alternation()
    rows: list[ConditionRow] = []
    for page_num in range(page_range[0], page_range[1] + 1):
        text = pages[page_num - 1]
        # Pattern: row label, then within 300 chars, "Repair Category   1" (or "-")
        # Capture notes prose up to next row label or page end.
        block_pat = (
            r"(?:^|\n)\s*" + label_alt + r"\s*\n"
            r"\s*Repair\s+Category[:\s]+([123\-])"
            r"(?:\s*\n\s*Notes?\s*[:\s]*([\s\S]{0,800}?))?"
            r"(?=\n\s*(?:" + label_alt + r"|Address:|\d+\.\s+[A-Z]|$))"
        )
        for m in re.finditer(block_pat, text, re.I | re.M):
            row = m.group(1).strip()
            cat = m.group(2)
            notes = (m.group(3) or "").strip()
            # Trim notes to first 600 chars
            notes = re.sub(r"\s+", " ", notes)[:600]
            rows.append(ConditionRow(row=row, cat=cat, page=page_num, notes=notes))
    # Dedup: same (row, page) — keep first
    seen = set()
    deduped: list[ConditionRow] = []
    for r in rows:
        key = (r.row.lower(), r.page)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped
