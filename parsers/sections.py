"""Section detection for Scottish Home Report PDFs.

A Home Report is three concatenated documents (Single Survey, Energy Report,
Property Questionnaire) plus a cover and Terms & Conditions. Extractors need
to scope their regex to the right section, so we identify the BODY page (not
TOC, not T&C) where each section begins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class SectionRange:
    start_page: Optional[int] = None
    end_page: Optional[int] = None


def detect_sections(pages_layout: list[str]) -> dict[str, SectionRange]:
    """
    Detect the page ranges for Single Survey, Energy Report, Property Questionnaire.

    Strategy: locate the first BODY page of each section using markers that only
    appear in real content (not TOC, not preamble Terms & Conditions). End page of
    each section = (start of next section) - 1.

    Single Survey body — distinguishing markers (in priority order):
        - "1. Information and scope of inspection" (exact heading)
        - A page whose first non-empty line is "Single Survey" AND which contains
          a real description (e.g., "Accommodation" / "Description" labels), not
          T&C boilerplate.
    Energy Report body:
        - "current rating is band X (NN)" anywhere on the page
        - "Recommended measures to improve" / "Cost effective measures"
    Property Questionnaire body:
        - "Length of ownership" heading
        - "How long have you owned the property"
    """
    n = len(pages_layout)

    def is_toc(text: str) -> bool:
        lower = text.lower()
        return (
            "home report index" in lower
            or (
                "single survey" in lower
                and "energy report" in lower
                and "property questionnaire" in lower
                and len(text) < 800
            )
        )

    def is_terms_and_conditions(text: str) -> bool:
        lower = text.lower()
        return (
            "terms and conditions" in lower[:200]
            or "part 1 - general" in lower
            or "the surveyors" in lower[:300]
        )

    def first_body_page(predicate) -> Optional[int]:
        for i, p in enumerate(pages_layout, start=1):
            if is_toc(p) or is_terms_and_conditions(p):
                continue
            if predicate(p):
                return i
        return None

    # Single Survey — strict markers
    ss_start = first_body_page(lambda p: (
        re.search(r"1\.\s+Information\s+and\s+scope\s+of\s+inspection", p, re.I) is not None
        or (
            p.lstrip().lower().startswith("single survey")
            and re.search(r"\b(?:Accommodation|Description)\b", p) is not None
            and "1.1" not in p[:200]  # exclude T&C "1.1 The Surveyors"
        )
    ))

    # Energy Report — distinctive markers only (NOT "Energy Performance Certificate"
    # alone, which appears in T&C boilerplate)
    er_start = first_body_page(lambda p: (
        re.search(r"current\s+rating\s+is\s+band\s+[A-G]", p, re.I) is not None
        or re.search(r"Recommended\s+measures\s+to\s+improve", p, re.I) is not None
        or re.search(r"^\s*energy\s+report\s*$", p, re.I | re.M) is not None
    ))

    # Property Questionnaire — first numbered question
    pq_start = first_body_page(lambda p: (
        re.search(r"^\s*1\.?\s+Length\s+of\s+ownership", p, re.M | re.I) is not None
        or re.search(r"How\s+long\s+have\s+you\s+owned\s+the\s+property", p, re.I) is not None
        or (
            p.lstrip().lower().startswith("property questionnaire")
            and "1" in p
        )
    ))

    starts = {
        "single_survey": ss_start,
        "energy_report": er_start,
        "property_questionnaire": pq_start,
    }
    # Compute end pages by sorting starts in document order
    ordered = sorted(((s, k) for k, s in starts.items() if s is not None))
    result = {k: SectionRange() for k in starts}
    for idx, (start, sec) in enumerate(ordered):
        end = ordered[idx + 1][0] - 1 if idx + 1 < len(ordered) else n
        result[sec] = SectionRange(start_page=start, end_page=end)
    return result
