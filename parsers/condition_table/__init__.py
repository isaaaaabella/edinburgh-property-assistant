"""Condition-table extraction — routes per template to textual or image strategy.

Re-exports the public surface used by `dispatcher.parse`:
- ConditionRow
- extract_condition_table (router)
- find_cat_notes_contradictions
"""

from __future__ import annotations

from ..sections import SectionRange
from ._common import ConditionRow, find_cat_notes_contradictions
from .allied_image import extract_condition_table_image
from .textual import extract_condition_table_textual


def extract_condition_table(template: str, pdf_path: str, pages: list[str],
                            ss_range: SectionRange, warnings: list[str]) -> list[ConditionRow]:
    """Pick the extraction strategy based on detected template.

    - Textual (inline digit): graham_sibbald / dm_hall / shepherd / dhkk
    - Image (colored stamps): allied_surveyors
    - Unknown: best-effort textual; if <12 rows, append a warning
    """
    if template in ("graham_sibbald", "dm_hall", "shepherd", "dhkk"):
        return extract_condition_table_textual(pages, ss_range)
    if template == "allied_surveyors":
        return extract_condition_table_image(pdf_path, pages, ss_range, warnings)
    # unknown template — try textual first as best-effort
    rows = extract_condition_table_textual(pages, ss_range)
    if len(rows) < 12:
        warnings.append("unknown template: textual condition extraction yielded <12 rows; consider manual review")
    return rows


__all__ = [
    "ConditionRow",
    "extract_condition_table",
    "extract_condition_table_textual",
    "extract_condition_table_image",
    "find_cat_notes_contradictions",
]
