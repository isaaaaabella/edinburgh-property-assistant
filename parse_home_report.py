#!/usr/bin/env python3
"""
parse_home_report.py — Deterministic Scottish Home Report PDF extractor.

Usage:
    python parse_home_report.py <pdf_path>
    python parse_home_report.py <pdf_path> --debug   # also print diagnostic info to stderr

Outputs a JSON document to stdout containing:
- regex_extracted: dict of field → {value, page, source}  (~22 fields)
- condition_table: list of {row, cat, page, notes}        (~18-24 rows)
- derived: {category{1,2,3}_count, roof_issue, ...}
- pages: list of {n, text_layout, text_raw}               (raw text dumps for LLM fallback)
- warnings: list of human-readable warning strings

The skill (`/home-report.md`) treats `regex_extracted` and `condition_table` as
authoritative and is forbidden from "correcting" them.

Templates supported:
- Quest / Graham + Sibbald     — inline `Repair category   1` digits
- DM Hall                      — inline `Repair Category   1` (capital C, slightly different valuation label)
- Allied Surveyors / Onesurvey — digits rendered as colored image stamps; we extract by hashing/colour

The parser depends only on `pdftotext`, `pdfimages` (poppler) and Pillow (PIL).
All three are already present in the user's Anaconda environment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

# ----------------------------------------------------------------------------
# Constants — known-stable across Scottish Home Reports
# ----------------------------------------------------------------------------

CONDITION_ROW_LABELS = [
    "Structural movement",
    "Dampness, rot and infestation",
    "Chimney stacks",
    "Roofing including roof space",
    "Rainwater fittings",
    "Main walls",
    "Windows, external doors and joinery",
    "External decorations",
    "Conservatories/porches",
    "Conservatories / porches",  # spacing variant
    "Communal areas",
    "Garages and permanent outbuildings",
    "Outside areas and boundaries",
    "Ceilings",
    "Internal walls",
    "Floors including sub-floors",
    "Floors including sub floors",  # hyphen variant
    "Internal joinery and kitchen fittings",
    "Chimney breasts and fireplaces",
    "Roof spaces",
    "Bathroom fittings",
    "Kitchen fittings",
    "Electricity",
    "Gas",
    "Water",
    "Heating and hot water",
    "Heating",
    "Drainage",
]

# Rows that count toward "roof issue" (Cat 2 or 3 in any of these → roof_issue=true)
ROOF_ROW_LABELS = {
    "Roofing including roof space",
    "Chimney stacks",
    "Rainwater fittings",
}

# RGB averages for the Allied colored stamp images.
# Cat 1 = green, Cat 2 = orange, Cat 3 = red. Tuple is (R, G, B).
ALLIED_STAMP_RGB = {
    "1": (80, 178, 79),   # green
    "2": (247, 149, 31),  # orange
    "3": (220, 50, 50),   # red — extrapolated; not in our sample, so colour-distance fallback is used
}

# ----------------------------------------------------------------------------
# Tooling (pdftotext, pdfimages)
# ----------------------------------------------------------------------------

def _run(cmd: list[str], **kwargs) -> str:
    """Run a subprocess and return stdout. Errors raise."""
    res = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if res.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {res.stderr.strip()}")
    return res.stdout


def extract_text_layout(pdf_path: str) -> str:
    return _run(["pdftotext", "-layout", pdf_path, "-"])


def extract_text_raw(pdf_path: str) -> str:
    return _run(["pdftotext", "-raw", pdf_path, "-"])


def page_count(pdf_path: str) -> int:
    out = _run(["pdfinfo", pdf_path])
    m = re.search(r"^Pages:\s*(\d+)", out, re.M)
    return int(m.group(1)) if m else 0


def render_page_pngs(pdf_path: str, first: int, last: int, out_dir: str, dpi: int = 150) -> list[str]:
    """Render specified PDF pages as PNGs. Returns list of file paths in page order."""
    prefix = os.path.join(out_dir, "page")
    _run([
        "pdftoppm", "-f", str(first), "-l", str(last), "-r", str(dpi),
        "-png", pdf_path, prefix,
    ])
    files = sorted(Path(out_dir).glob("page-*.png"))
    return [str(f) for f in files]


def extract_page_images(pdf_path: str, first: int, last: int, out_dir: str) -> list[str]:
    """Extract embedded image objects from page range. Returns list of png paths in document order."""
    prefix = os.path.join(out_dir, "img")
    try:
        _run([
            "pdfimages", "-f", str(first), "-l", str(last), "-png",
            pdf_path, prefix,
        ])
    except RuntimeError as e:
        # pdfimages can fail if there are no images; treat as empty
        if "no images" in str(e).lower():
            return []
        raise
    files = sorted(Path(out_dir).glob("img-*.png"))
    return [str(f) for f in files]


# ----------------------------------------------------------------------------
# Page splitting — pdftotext uses form-feed (\f) between pages
# ----------------------------------------------------------------------------

def split_pages(text: str) -> list[str]:
    return text.split("\f")


# ----------------------------------------------------------------------------
# Section detection
# ----------------------------------------------------------------------------

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


# ----------------------------------------------------------------------------
# Regex extraction helpers
# ----------------------------------------------------------------------------

@dataclass
class FieldEvidence:
    value: Any = None
    page: Optional[int] = None
    source: Optional[str] = None


def _first_match(patterns: list[str], text: str, flags=0) -> Optional[re.Match]:
    """Try each pattern in order. Return first non-None match."""
    for pat in patterns:
        m = re.search(pat, text, flags)
        if m:
            return m
    return None


def _find_in_pages(pages: list[str], patterns: list[str], flags=0,
                   page_range: Optional[tuple[int, int]] = None) -> Optional[tuple[re.Match, int, str]]:
    """
    Search a sequence of pages, optionally restricted to (start, end) 1-indexed inclusive.
    Returns (match, 1-indexed page, source_line) or None.
    """
    if page_range:
        s, e = page_range
        s = max(1, s); e = min(len(pages), e or len(pages))
        scoped = list(enumerate(pages[s-1:e], start=s))
    else:
        scoped = list(enumerate(pages, start=1))

    for page_num, text in scoped:
        for pat in patterns:
            m = re.search(pat, text, flags)
            if m:
                # source = line(s) containing the match
                start = max(0, text.rfind("\n", 0, m.start()) + 1)
                end = text.find("\n", m.end())
                if end == -1:
                    end = len(text)
                source = text[start:end].strip()
                return m, page_num, source
    return None


# ----------------------------------------------------------------------------
# Field extractors
# ----------------------------------------------------------------------------

def extract_postcode(pages: list[str]) -> FieldEvidence:
    """Edinburgh postcode (EH prefix), found anywhere in the first 5 pages."""
    res = _find_in_pages(
        pages,
        [r"\b(EH\d{1,2}\s*\d[A-Z]{2})\b"],
        page_range=(1, min(5, len(pages))),
    )
    if not res:
        # fall back: try any UK postcode
        res = _find_in_pages(
            pages,
            [r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2})\b"],
            page_range=(1, min(5, len(pages))),
        )
    if res:
        m, page, source = res
        # Normalise: insert space before last 3 chars
        pc = m.group(1).upper().replace(" ", "")
        norm = pc[:-3] + " " + pc[-3:]
        return FieldEvidence(value=norm, page=page, source=source)
    return FieldEvidence()


def extract_address(pages: list[str], postcode: Optional[str]) -> FieldEvidence:
    """
    Extract address from cover or first content page. Strategy (in order):

    1. PREFERRED: find a single line containing the postcode AND ≥1 comma —
       Shepherd-style PDFs include a clean "<addr>, <city>, <postcode>" page
       footer. This avoids any chance of bleeding into surrounding labels.
    2. Else: walk back from the postcode line, collect 1-4 preceding lines,
       filter out dates / headings / surveyor names, dedup, and join with commas.

    The "Allied cover with surveyor name in same line" case is handled by
    splitting on runs of 3+ spaces.
    """
    if not postcode:
        return FieldEvidence()
    pc_compact = postcode.replace(" ", "")
    pc_pat = r"\b(?:" + re.escape(postcode) + r"|" + re.escape(pc_compact) + r")\b"

    # Strategy 1: clean single-line address with the postcode + commas + digits
    # Require: ≥2 commas (street, area/city, postcode) AND ≥1 digit (street number).
    # This rejects 2-line page footers like "Edinburgh, EH8 9PF".
    for page_num in range(1, min(8, len(pages)) + 1):
        text = pages[page_num - 1]
        for line in text.splitlines():
            ls = line.strip()
            if not ls:
                continue
            if (
                re.search(pc_pat, ls)
                and ls.count(",") >= 2
                and re.search(r"\d", ls.split(",", 1)[0])  # first comma-segment has a digit
                and len(ls) <= 200
                and not re.match(r"^(Property address|Customer|Address)", ls, re.I)
            ):
                pc_match = re.search(pc_pat, ls)
                if pc_match:
                    end = pc_match.end()
                    ls_trimmed = ls[:end].strip(" ,;")
                    if ls_trimmed.count(",") >= 2:
                        return FieldEvidence(value=ls_trimmed, page=page_num, source=ls_trimmed)
                return FieldEvidence(value=ls, page=page_num, source=ls)

    # Strategy 2: assemble from preceding lines
    for page_num in range(1, min(8, len(pages)) + 1):
        text = pages[page_num - 1]
        m = re.search(pc_pat, text)
        if not m:
            continue
        idx = m.start()
        start = idx
        for _ in range(5):
            prev_nl = text.rfind("\n", 0, start - 1)
            if prev_nl == -1:
                start = 0
                break
            start = prev_nl + 1
        end = text.find("\n", m.end())
        if end == -1:
            end = len(text)
        snippet = text[start:end]

        addr_lines = []
        for line in snippet.splitlines():
            ls = line.strip()
            if not ls:
                continue
            if re.match(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\s*$", ls):
                continue
            if re.match(r"^(Home Report|Title|Date|Ref:|Page|Inspection|Customer|Prepared|Single Survey|survey report)", ls, re.I):
                continue
            # Strip leading labels: "Property address    <addr>" → keep <addr>
            ls = re.sub(r"^(Property\s+address|Address|Customer\s+address)\s+", "", ls, flags=re.I)
            parts = re.split(r"\s{3,}", ls)
            if len(parts) >= 2:
                first, second = parts[0].strip(), parts[1].strip()
                first_has_digit = bool(re.search(r"\d", first))
                second_has_digit = bool(re.search(r"\d", second))
                second_is_name = (
                    not second_has_digit
                    and re.match(r"^[A-Z][a-z]+(\s+[A-Z][a-zA-Z'.-]+){0,4}$", second) is not None
                )
                if first_has_digit and second_is_name:
                    ls = first
                elif not first_has_digit and second_has_digit:
                    ls = second  # the address part might be on the right
            ls = re.sub(r"\s+", " ", ls).strip()
            if ls:
                addr_lines.append(ls)

        if addr_lines:
            seen = set(); cleaned = []
            for x in addr_lines:
                if x.lower() not in seen:
                    seen.add(x.lower()); cleaned.append(x)
            address = ", ".join(cleaned)
            if len(address) > 200:
                address = cleaned[-1] if cleaned else address[:200]
            return FieldEvidence(value=address, page=page_num, source=address)
    return FieldEvidence()


def extract_inspection_date(pages: list[str]) -> FieldEvidence:
    """Look for the inspection/report date on cover or first SS page."""
    patterns = [
        # "Date of inspection" / "Inspection date"
        r"Date of (?:inspection|report)[:\s]+(\d{1,2}(?:st|nd|rd|th)?\s+\w+\s+\d{4})",
        r"(?:Date of inspection|Inspection date)[:\s]+(\d{1,2}/\d{1,2}/\d{4})",
        # Fallback: any "<day> <month-name> <year>" in page 1
        r"(\d{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})",
        r"(\d{1,2}/\d{1,2}/\d{4})",
    ]
    res = _find_in_pages(pages, patterns, flags=re.I, page_range=(1, min(6, len(pages))))
    if not res:
        return FieldEvidence()
    m, page, source = res
    raw = m.group(1)
    # Convert to YYYY-MM-DD
    iso = _to_iso_date(raw)
    return FieldEvidence(value=iso or raw, page=page, source=source)


def _to_iso_date(s: str) -> Optional[str]:
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    }
    s = s.strip()
    # "11th March 2026" / "1 April 2026"
    m = re.match(r"(\d{1,2})(?:st|nd|rd|th)?\s+(\w+)\s+(\d{4})", s, re.I)
    if m:
        d, mn, y = int(m.group(1)), months.get(m.group(2).lower()), int(m.group(3))
        if mn:
            return f"{y:04d}-{mn:02d}-{d:02d}"
    # "27/03/2026"
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        d, mn, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y:04d}-{mn:02d}-{d:02d}"
    return None


def extract_floor_area(pages: list[str]) -> FieldEvidence:
    """Gross internal floor area in m². Several phrasings."""
    patterns = [
        r"Gross\s+[Ii]nternal\s+[Ff]loor\s+[Aa]rea\s*\(?m[²2]\)?[\s:]+(?:Approximately\s+)?(\d{2,4})\s*m[²2]",
        r"Gross\s+[Ii]nternal\s+[Ff]loor\s+[Aa]rea[\s:]+(?:Approximately\s+)?(\d{2,4})\s*(?:m[²2]|sq\s*m|sqm)",
        r"Total\s+floor\s+area[\s:]+(\d{2,4})\s*m[²2]?",
        r"[Aa]pproximately\s+(\d{2,4})\s*m[²2]\.?",  # last-resort
    ]
    res = _find_in_pages(pages, patterns)
    if not res:
        return FieldEvidence()
    m, page, source = res
    val = int(m.group(1))
    if not (15 <= val <= 1000):
        return FieldEvidence()
    return FieldEvidence(value=val, page=page, source=source)


def extract_bedrooms(pages: list[str]) -> FieldEvidence:
    """Bedroom count. Look in accommodation description on first SS pages.

    Strategy:
    1. Look for explicit "<digit> Bedrooms" or "<word> Bedrooms".
    2. Fallback: count occurrences of "Bedroom" / "Bedrooms" (case-insensitive)
       in the accommodation list on a single page. Many HRs list rooms one-by-one
       (e.g., "Hallway, Living Room, Bedroom, Kitchen") rather than giving a count.
    """
    word_to_num = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
    page_range = (1, min(20, len(pages)))
    # Strategy 1: explicit count
    patterns = [
        r"(\d+)\s+[Bb]edrooms?\b",
        r"\b(one|two|three|four|five|six)\s+[Bb]edrooms?\b",
    ]
    res = _find_in_pages(pages, patterns, flags=re.I, page_range=page_range)
    if res:
        m, page, source = res
        g = m.group(1).lower()
        val = int(g) if g.isdigit() else word_to_num.get(g)
        if val and 0 < val <= 10:
            return FieldEvidence(value=val, page=page, source=source)

    # Strategy 2: count "Bedroom" mentions in the labelled Accommodation block.
    # Anchor on "Accommodation" as a LABEL (line-start, followed by ≥3 spaces or a colon
    # and then content) — not the boilerplate "type, accommodation, neighbourhood..." prose.
    accom_label_pat = re.compile(
        r"(?:^|\n)\s*Accommodation\s{2,}([\s\S]{0,500}?)(?=\n\s*(?:Gross|Floor|Neighbourhood|Age|Weather|Description)|\n\n)",
        re.M,
    )
    for i, p in enumerate(pages, start=1):
        if i > page_range[1]:
            break
        am = accom_label_pat.search(p)
        if not am:
            continue
        block = am.group(1)
        cnt = len(re.findall(r"\b[Bb]edrooms?\b", block))
        if cnt:
            return FieldEvidence(value=cnt, page=i, source=("Accommodation: " + block.strip())[:160])
    return FieldEvidence()


def extract_council_tax(pages: list[str], pq_range: SectionRange) -> FieldEvidence:
    """
    Council Tax band. Try patterns in order of specificity.
    Allied template uses [x]A / [x]B checkboxes.
    Quest/DM Hall use either same-line or next-line value after the question.
    """
    # Restrict to PQ section if known, else search broadly
    page_range = (pq_range.start_page or 1, pq_range.end_page or len(pages))
    # Pattern 1: same line: "Which Council Tax band ... is your property in? D"
    res = _find_in_pages(
        pages,
        [r"Which\s+Council\s+Tax\s+[Bb]and[^\n]*?\?\s*([A-H])\b"],
        flags=re.I,
        page_range=page_range,
    )
    if res:
        m, page, source = res
        return FieldEvidence(value=m.group(1).upper(), page=page, source=source)

    # Pattern 2: next line: "Which Council Tax band is your property in?\n   D"
    res = _find_in_pages(
        pages,
        [r"Which\s+Council\s+Tax\s+[Bb]and[^\n]*\n[^\n]*?\b([A-H])\b"],
        flags=re.I,
        page_range=page_range,
    )
    if res:
        m, page, source = res
        return FieldEvidence(value=m.group(1).upper(), page=page, source=source)

    # Pattern 3: Allied checkbox style: "[X]A" or "[x]C"
    res = _find_in_pages(
        pages,
        [r"\[\s*[Xx]\s*\]\s*([A-H])\b"],
        page_range=page_range,
    )
    if res:
        m, page, source = res
        return FieldEvidence(value=m.group(1).upper(), page=page, source=source)

    # Pattern 4: any "Council Tax Band: X" anywhere
    res = _find_in_pages(
        pages,
        [r"Council\s+Tax\s+[Bb]and[\s:]+([A-H])\b"],
        flags=re.I,
    )
    if res:
        m, page, source = res
        return FieldEvidence(value=m.group(1).upper(), page=page, source=source)
    return FieldEvidence()


def extract_epc(pages: list[str]) -> tuple[FieldEvidence, FieldEvidence, FieldEvidence]:
    """Returns (rating, score, potential_score)."""
    patterns_current = [
        r"current\s+rating\s+is\s+band\s+([A-G])\s*\((\d{1,3})\)",
        r"[Cc]urrent\s+rating[\s\S]{0,80}?\b([A-G])\s*\((\d{1,3})\)",
    ]
    rating, score, potential = FieldEvidence(), FieldEvidence(), FieldEvidence()
    res = _find_in_pages(pages, patterns_current, flags=re.I)
    if res:
        m, page, source = res
        r = m.group(1).upper()
        s = int(m.group(2))
        if 1 <= s <= 100:
            rating = FieldEvidence(value=r, page=page, source=source)
            score = FieldEvidence(value=s, page=page, source=source)

    # Potential score
    res = _find_in_pages(
        pages,
        [
            r"potential\s+rating[\s\S]{0,80}?\b([A-G])\s*\((\d{1,3})\)",
            r"could\s+be[\s\S]{0,80}?\b([A-G])\s*\((\d{1,3})\)",
        ],
        flags=re.I,
    )
    if res:
        m, page, source = res
        s = int(m.group(2))
        if 1 <= s <= 100:
            potential = FieldEvidence(value=s, page=page, source=source)
    return rating, score, potential


def extract_gas_heating(pages: list[str], er_range: SectionRange) -> FieldEvidence:
    """Boolean: gas central heating present.

    Tolerant of layout-induced line breaks between 'mains' and 'gas'
    (Allied template wraps the value across lines).
    """
    page_range = (er_range.start_page or 1, er_range.end_page or len(pages))
    res = _find_in_pages(
        pages,
        [
            # Loose: 'Main heating' label, then within 200 chars, 'mains gas' or
            # 'gas boiler' or 'gas-fired'/'gas central' allowing newline+whitespace.
            r"Main\s+heating[\s\S]{0,200}?\b(mains\s+gas|gas[\s-]?fired|gas\s+boiler|gas\s+central)\b",
            # Allied wraps as: 'Boiler and radiators, mains' \n ... \n 'gas'
            r"Main\s+heating[\s\S]{0,250}?Boiler\s+and\s+radiators,\s+mains[\s\S]{0,80}?gas",
        ],
        flags=re.I,
        page_range=page_range,
    )
    if res:
        m, page, source = res
        return FieldEvidence(value=True, page=page, source=source)
    # Explicit non-gas heating
    res = _find_in_pages(
        pages,
        [r"Main\s+heating[\s\S]{0,200}?\b(electric|storage\s+heater|heat\s+pump|oil[\s-]?fired)\b"],
        flags=re.I,
        page_range=page_range,
    )
    if res:
        m, page, source = res
        return FieldEvidence(value=False, page=page, source=source)
    return FieldEvidence()


def extract_age_year(pages: list[str], ss_range: SectionRange,
                     inspection_year: Optional[int]) -> FieldEvidence:
    """Construction year approximation.

    Handles multiple phrasings:
        - "Age: 1890." (Quest)
        - "Age   Built around 1900." (DM Hall)
        - "Approximate age: 1880" (Allied)
        - "Age   28 years approximately." → year = inspection_year - 28 (Shepherd)
        - "Pre-1919" / "circa 1900" / "constructed 1880"
    """
    page_range = (ss_range.start_page or 1, min(ss_range.end_page or len(pages), len(pages)))
    # Order matters — most specific first
    patterns_year = [
        r"\bAge\b[\s:]+(?:[Cc]irca\s+|[Bb]uilt\s+around\s+|[Aa]round\s+|[Aa]pprox(?:imately)?\s+)?((?:18|19|20)\d{2})",
        r"\bApproximate\s+age[\s:]+(?:[Cc]irca\s+|[Bb]uilt\s+around\s+)?((?:18|19|20)\d{2})",
        r"\bDate\s+of\s+construction[\s:]+(?:[Cc]irca\s+)?((?:18|19|20)\d{2})",
        r"(?:built|constructed)\s+(?:in\s+)?(?:[Cc]irca\s+|[Aa]round\s+)?((?:18|19|20)\d{2})",
        r"\b[Cc]irca\s+((?:18|19|20)\d{2})\b",
    ]
    res = _find_in_pages(pages, patterns_year, page_range=page_range)
    if res:
        m, page, source = res
        val = int(m.group(1))
        if 1700 <= val <= 2030:
            return FieldEvidence(value=val, page=page, source=source)

    # "Pre-1919" or "Pre 1919" → derive a year (1900-ish)
    res = _find_in_pages(pages, [r"\bPre[\s-]?(\d{4})\b"], page_range=page_range)
    if res:
        m, page, source = res
        upper = int(m.group(1))
        return FieldEvidence(value=max(1700, upper - 19), page=page, source=source)

    # Relative age: "28 years approximately" → year = inspection_year - 28
    if inspection_year:
        res = _find_in_pages(
            pages,
            [r"\bAge\b[\s:]+(\d{1,3})\s*years?\s+(?:approximately|approx|old)?",
             r"(\d{1,3})\s+years?\s+(?:approximately|approx|old)\b"],
            page_range=page_range,
        )
        if res:
            m, page, source = res
            yrs = int(m.group(1))
            if 0 < yrs <= 250:
                derived = inspection_year - yrs
                if 1700 <= derived <= 2030:
                    return FieldEvidence(value=derived, page=page,
                                         source=f"{source} (derived from inspection_year={inspection_year} - {yrs})")
    return FieldEvidence()


def derive_construction_period(year: Optional[int]) -> Optional[str]:
    if year is None:
        return None
    if year < 1919:
        return "Pre-1919"
    if year < 1945:
        return "1919-1944"
    if year < 1985:
        return "1945-1984"
    return "Post-1985"


def extract_property_type(pages: list[str], ss_range: SectionRange) -> FieldEvidence:
    """The 'Description' line on the first SS body page."""
    page_range = (ss_range.start_page or 1, min(ss_range.end_page or len(pages), len(pages)))
    # The description is usually under "Description" label
    # Try a window-search: "Description" + up to 200 chars
    for page_num in range(page_range[0], page_range[1] + 1):
        text = pages[page_num - 1]
        m = re.search(r"\bDescription\b\s+([^\n]+(?:\n[ \t]+[^\n]+){0,3})", text)
        if m:
            desc = " ".join(line.strip() for line in m.group(1).splitlines() if line.strip())
            desc = desc.strip(" .")
            if len(desc) > 5 and len(desc) < 500:
                return FieldEvidence(value=desc, page=page_num, source=desc[:200])
    return FieldEvidence()


def extract_floor_info(pages: list[str], property_type_value: Optional[str]) -> tuple[FieldEvidence, FieldEvidence, FieldEvidence, FieldEvidence]:
    """Returns (floor (text), floor_number, total_floors, main_door_flat)."""
    floor_words = {
        "ground": 0, "first": 1, "second": 2, "third": 3, "fourth": 4,
        "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    }
    storey_words = {
        "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
        "nine": 9, "ten": 10,
    }

    floor = FieldEvidence()
    floor_number = FieldEvidence()
    total_floors = FieldEvidence()
    main_door = FieldEvidence(value=False)

    # Scan property_type first, then search whole document for "<word> floor flat"
    sources = []
    if property_type_value:
        sources.append(property_type_value)
    # Add cover + first 5 SS pages
    for p in pages[:8]:
        sources.append(p)
    combined = "\n".join(sources)

    # floor description
    fm = re.search(r"\b(ground|first|second|third|fourth|fifth|sixth|top)\s+floor\b", combined, re.I)
    if fm:
        word = fm.group(1).lower()
        floor_text = f"{word.capitalize()} floor"
        floor = FieldEvidence(value=floor_text, source=fm.group(0))
        if word == "top":
            # don't know exact number
            floor_number = FieldEvidence(value=None, source=fm.group(0))
        else:
            floor_number = FieldEvidence(value=floor_words.get(word), source=fm.group(0))

    # total storeys: "within a four storey block"
    sm = re.search(r"within\s+a\s+(\w+)\s+storey", combined, re.I)
    if sm:
        w = sm.group(1).lower()
        if w in storey_words:
            total_floors = FieldEvidence(value=storey_words[w], source=sm.group(0))
        elif w.isdigit():
            total_floors = FieldEvidence(value=int(w), source=sm.group(0))

    # main door
    if re.search(r"\bmain[\s-]?door\b", combined, re.I):
        main_door = FieldEvidence(value=True, source="contains 'main door'")

    return floor, floor_number, total_floors, main_door


def extract_valuations(pages: list[str]) -> tuple[FieldEvidence, FieldEvidence]:
    """Returns (market_valuation, reinstatement_cost)."""
    market = FieldEvidence()
    reinst = FieldEvidence()

    # Try clearly-labelled patterns
    market_patterns = [
        # Quest: "market value of the property at the date of valuation is...£330,000"
        r"market\s+value[\s\S]{0,400}?£\s*([\d,]+)",
        # DM Hall: "Valuation (£) and Market Comments\nTwo Hundred...\n£285,000"
        r"Valuation\s+\(£\)[\s\S]{0,200}?£\s*([\d,]+)",
        # Shepherd: "Market value in present condition" then form-aligned digits, no £ symbol
        r"Market\s+value\s+in\s+present\s+condition[\s.]*([\d,]{3,})",
    ]
    reinst_patterns = [
        r"[Rr]einstatement[\s\S]{0,200}?£\s*([\d,]+)",
        r"Insurance\s+reinstatement\s+value\s*£?\s*([\d,]+)",
    ]

    res = _find_in_pages(pages, reinst_patterns)
    if res:
        m, page, source = res
        val = int(m.group(1).replace(",", ""))
        if 20000 <= val <= 5_000_000:
            reinst = FieldEvidence(value=val, page=page, source=source)

    # Market value: avoid picking up the same number as reinstatement.
    # We search in the valuation section pages (typically pages with "market value" body text).
    res = _find_in_pages(pages, market_patterns, flags=re.I)
    if res:
        m, page, source = res
        val = int(m.group(1).replace(",", ""))
        if 20000 <= val <= 5_000_000:
            # Sanity: market is usually < reinstatement * 1.5 and > 30% of reinstatement
            if reinst.value is None or 0.2 * reinst.value <= val <= 2.0 * reinst.value:
                market = FieldEvidence(value=val, page=page, source=source)
            else:
                # Try second-best £ figure on the same page
                page_text = pages[page - 1]
                figures = re.findall(r"£\s*([\d,]+)", page_text)
                for f in figures:
                    candidate = int(f.replace(",", ""))
                    if 20000 <= candidate <= 5_000_000 and candidate != reinst.value:
                        if 0.2 * reinst.value <= candidate <= 2.0 * reinst.value:
                            market = FieldEvidence(value=candidate, page=page, source=f"£{f}")
                            break

    return market, reinst


def extract_pq_yes_no(pages: list[str], pq_range: SectionRange, question_pattern: str,
                      window_chars: int = 600,
                      back_chars: int = 250) -> Optional[tuple[bool, int, str]]:
    """
    Generic Yes/No extractor for a labelled PQ question.

    Strategy (priority order):
    1. Search forward from the question keyword for `[x]YES` / `[x]NO`.
    2. Search BACKWARD from the question keyword (Allied template puts the
       checkbox on the line BEFORE the question text — e.g. Q12b
       "[ ]YES [x]NO\n        Is there a common buildings insurance policy?")
    3. Fallback: bare "YES"/"NO" forward — but skip "answered yes" boilerplate.
    """
    page_range = (pq_range.start_page or 1, pq_range.end_page or len(pages))

    # Locate the question keyword first
    res = _find_in_pages(pages, [question_pattern], page_range=page_range)
    if not res:
        return None
    m, page, source_line = res
    text = pages[page - 1]

    # Strategy 1: forward checkbox
    fwd = text[m.end():m.end() + window_chars]
    fwd_cb = re.search(r"\[\s*[Xx]\s*\]\s*(YES|NO|Yes|No)", fwd)
    fwd_pos = fwd_cb.start() if fwd_cb else 1e9

    # Strategy 2: backward checkbox (Allied right-margin layout)
    back_start = max(0, m.start() - back_chars)
    back = text[back_start:m.start()]
    # Take the LAST checkbox before the question (closest one)
    back_cb_iter = list(re.finditer(r"\[\s*[Xx]\s*\]\s*(YES|NO|Yes|No)", back))
    back_cb = back_cb_iter[-1] if back_cb_iter else None
    back_dist = (m.start() - (back_start + back_cb.end())) if back_cb else 1e9

    # Pick whichever checkbox is closer to the question keyword
    if fwd_cb and back_cb:
        if fwd_pos <= back_dist:
            picked = fwd_cb.group(1)
            src = fwd[:fwd_cb.end() + 30]
        else:
            picked = back_cb.group(1)
            src = back[max(0, back_cb.start() - 5):]
        return picked.strip().lower() == "yes", page, (source_line + " | " + src.strip()[:120])
    if fwd_cb:
        return fwd_cb.group(1).strip().lower() == "yes", page, (
            source_line + " | " + fwd[:fwd_cb.end() + 30].strip()[:120]
        )
    if back_cb:
        return back_cb.group(1).strip().lower() == "yes", page, (
            source_line + " | (preceding line) " + back[max(0, back_cb.start() - 5):].strip()[:120]
        )

    # Strategy 3: bare yes/no with boilerplate stripped
    cleaned = re.sub(
        r"\b(?:If\s+you\s+have\s+)?answered\s+(?:yes|no)\b[^\n]*",
        " ",
        fwd,
        flags=re.I,
    )
    cleaned = re.sub(r"please\s+(?:say|give|describe|provide)[^\n]*", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"if\s+you\s+(?:have\s+)?answered[^\n]*", " ", cleaned, flags=re.I)
    bare_match = re.search(r"\b(YES|NO|Yes|No)\b", cleaned)
    if bare_match:
        return bare_match.group(1).strip().lower() == "yes", page, (source_line + " | " + bare_match.group(0))
    return None


def _find_question_window(pages: list[str], question_pattern: str,
                          pq_range: SectionRange) -> Optional[tuple[int, str]]:
    """
    Find a PQ question and return (1-indexed page, text from question to next
    numbered question / end of PQ). Bounded by ~1500 chars for safety.
    """
    page_range = (pq_range.start_page or 1, pq_range.end_page or len(pages))
    res = _find_in_pages(pages, [question_pattern], page_range=page_range)
    if not res:
        return None
    m, page, _ = res
    text = pages[page - 1]
    start = m.start()
    # Find next numbered question (e.g. "13.", "14.")
    next_q = re.search(r"\n\s*\d{1,2}\s*\.\s+[A-Z]", text[m.end():m.end() + 1500])
    end = m.end() + (next_q.start() if next_q else 1500)
    end = min(end, len(text))
    return page, text[start:end]


def extract_owner_years(pages: list[str], pq_range: SectionRange) -> FieldEvidence:
    """How long have you owned the property → years (int).

    Handles:
        - "X years", "X years Y months"
        - "Since <Month Year>" with parenthetical years
        - "X months" → years = X // 12
        - Bare number answer: "How long have you owned the property?\n\n  12"
          (Allied PDFs answer with just a digit; assume years if 0-100)
    """
    page_range = (pq_range.start_page or 1, pq_range.end_page or len(pages))
    # Pattern A: explicit "<n> years"
    res = _find_in_pages(
        pages,
        [r"How\s+long\s+have\s+you\s+owned[\s\S]{0,300}?(\d+)\s*[Yy]ears?"],
        flags=re.I,
        page_range=page_range,
    )
    if res:
        m, page, source = res
        val = int(m.group(1))
        if 0 <= val <= 100:
            return FieldEvidence(value=val, page=page, source=source)
    # Pattern B: "Since November 2020 (5 years 4 months)"
    res = _find_in_pages(
        pages,
        [r"Since\s+\w+\s+\d{4}[\s\S]{0,40}?\((\d+)\s*[Yy]ears?"],
        page_range=page_range,
    )
    if res:
        m, page, source = res
        val = int(m.group(1))
        if 0 <= val <= 100:
            return FieldEvidence(value=val, page=page, source=source)
    # Pattern C: "X months"
    res = _find_in_pages(
        pages,
        [r"How\s+long\s+have\s+you\s+owned[\s\S]{0,300}?(\d+)\s*[Mm]onths?"],
        flags=re.I,
        page_range=page_range,
    )
    if res:
        m, page, source = res
        months = int(m.group(1))
        if 0 <= months <= 1200:
            years = months // 12
            return FieldEvidence(value=years, page=page,
                                 source=f"{source} (derived: {months} months → {years} years)")
    # Pattern D: bare number after the question — Allied style
    # Look for "How long have you owned the property?" then within 250 chars
    # find an isolated number (1-3 digits) on its own line / surrounded by whitespace.
    res = _find_in_pages(
        pages,
        [r"How\s+long\s+have\s+you\s+owned[^\n]*(?:\n[^\n]*)?\n\s+(\d{1,3})\s*\n"],
        flags=re.I,
        page_range=page_range,
    )
    if res:
        m, page, source = res
        val = int(m.group(1))
        # Accept only plausibly years (≤80). Larger likely means the answer was in months.
        if 0 < val <= 80:
            return FieldEvidence(value=val, page=page, source=source)
    return FieldEvidence()


def extract_factor(pages: list[str], pq_range: SectionRange) -> tuple[FieldEvidence, FieldEvidence, FieldEvidence, FieldEvidence]:
    """Returns (has_factor, factor_name, factor_cost_monthly, has_building_insurance).

    Critical: factor_name/factor_cost_monthly MUST be sourced from within the
    Q12 (Charges associated with your property) bounds — NOT Q11 (shared-stair
    £10/month fund), which is a different concept and a frequent false-match.
    """
    has_factor = FieldEvidence()
    name = FieldEvidence()
    cost = FieldEvidence()
    insurance = FieldEvidence()

    # has_factor — Q12a
    factor_q_pat = (
        r"(?:12\s*\.\s+(?:Charges|Factor)|Is\s+there\s+a\s+factor\s+or\s+property\s+manager"
        r"|Have\s+you\s+had\s+factoring)"
    )
    res = extract_pq_yes_no(pages, pq_range, factor_q_pat)
    if res:
        val, page, source = res
        has_factor = FieldEvidence(value=val, page=page, source=source)

    # has_building_insurance — Q12b
    res = extract_pq_yes_no(
        pages, pq_range,
        r"(?:common\s+buildings\s+insurance(?:\s+policy)?|building\s+insurance\s+policy)",
    )
    if res:
        val, page, source = res
        insurance = FieldEvidence(value=val, page=page, source=source)

    # name and cost are scoped strictly to Q12. Find the Q12 window first.
    if has_factor.value is True:
        # Locate Q12 (Charges associated...) and bound the search to within it.
        # Tolerant to "12.", "12 ." or just "12   " (some PDFs omit the period).
        page_range = (pq_range.start_page or 1, pq_range.end_page or len(pages))
        q12_window = None
        for pn in range(page_range[0], page_range[1] + 1):
            txt = pages[pn - 1]
            qm = re.search(
                r"(12\s*\.?\s+Charges\s+associated[\s\S]{0,2500}?)(?=\n\s*13\s*\.?\s|\Z)",
                txt, re.I,
            )
            if qm:
                q12_window = (pn, qm.group(1))
                break

        if q12_window:
            pn, win = q12_window
            # Factor name — try a few labelled-field patterns; otherwise pick the
            # first non-Yes/No / non-£ line after the factor question that looks
            # like a company name (Title-Case + ≥2 words, optionally followed by
            # an address line).
            nm = re.search(
                r"(?:Name\s+of\s+factor|Factor\s+name|Name\s+and\s+address)[\s:]+([^\n]+)",
                win, re.I,
            )
            cand = None
            if nm:
                c = nm.group(1).strip()
                if c and c.lower() not in ("yes", "no", "n/a"):
                    cand = c
            if not cand:
                # Fallback: line after the factor question that's not a £ amount
                # and not Yes/No/[ ]/[x]/boilerplate.
                # Find the factor question's end position within `win`
                fq = re.search(r"factor\s+or\s+property\s+manager[\s\S]{0,400}?(?:\n|$)", win, re.I)
                start_pos = fq.end() if fq else 0
                for line in win[start_pos:].splitlines():
                    ls = line.strip()
                    if not ls:
                        continue
                    if re.match(r"^(Yes|No|YES|NO|\[|If|Is|please|monthly|annual|current|£)", ls, re.I):
                        continue
                    # Accept Title-Case lines incl. digits + / + , (factor names like
                    # "Trinity 209/211 Bruntsfield Place" or "Charles White, Edinburgh")
                    if re.match(r"^[A-Z][\w&'\s\-\.\/,]{3,80}$", ls):
                        cand = ls
                        break
            if cand:
                name = FieldEvidence(value=cand[:120], page=pn, source=cand[:200])

            # Cost — search ONLY inside Q12 window. Try monthly → annual → quarterly.
            cm = re.search(
                r"£\s*(\d+(?:,\d{3})*(?:\.\d{2})?)\s*(?:per\s+month|/month|pcm|monthly)",
                win, re.I,
            )
            if cm:
                cost = FieldEvidence(
                    value=float(cm.group(1).replace(",", "")),
                    page=pn, source=cm.group(0),
                )
            else:
                # Annual fee (Trinity / Charles White / James Gibb style: "Current annual charge £748.34")
                am = re.search(
                    r"(?:[Aa]nnual\s+(?:charge|fee|payment)|per\s+(?:year|annum))[\s:]*£\s*(\d+(?:,\d{3})*(?:\.\d{2})?)",
                    win, re.I,
                ) or re.search(
                    r"£\s*(\d+(?:,\d{3})*(?:\.\d{2})?)\s*(?:per\s+year|per\s+annum|/year|annually|p\.?a\.?)\b",
                    win, re.I,
                )
                if am:
                    annual = float(am.group(1).replace(",", ""))
                    monthly = round(annual / 12, 2)
                    cost = FieldEvidence(
                        value=monthly, page=pn,
                        source=f"{am.group(0).strip()} (annual ÷ 12 = £{monthly}/mo)",
                    )
                else:
                    # Quarterly
                    qm = re.search(
                        r"(?:per\s+quarter|/quarter|quarterly)[\s:]*£\s*(\d+(?:,\d{3})*(?:\.\d{2})?)",
                        win, re.I,
                    ) or re.search(
                        r"£\s*(\d+(?:,\d{3})*(?:\.\d{2})?)\s*(?:per\s+quarter|/quarter|quarterly)",
                        win, re.I,
                    )
                    if qm:
                        quarterly = float(qm.group(1).replace(",", ""))
                        monthly = round(quarterly / 3, 2)
                        cost = FieldEvidence(
                            value=monthly, page=pn,
                            source=f"{qm.group(0).strip()} (quarterly ÷ 3 = £{monthly}/mo)",
                        )

    return has_factor, name, cost, insurance


def extract_statutory_notices(pages: list[str], pq_range: SectionRange) -> FieldEvidence:
    """Boolean: any statutory notices / planning enforcement on the property?

    Some PQ formats split this across Q16 a/b/c. We treat True if ANY of those
    is yes, False if ALL are explicitly No, else null.
    """
    page_range = (pq_range.start_page or 1, pq_range.end_page or len(pages))
    # Look for the Q16 block — "Notices" or "Statutory notices"
    for pn in range(page_range[0], page_range[1] + 1):
        txt = pages[pn - 1]
        qm = re.search(
            r"(16\s*\.\s+(?:Notices|Statutory\s+notices)[\s\S]{0,2000}?)(?=\n\s*1[7-9]\s*\.|\n\s*Note\s+for|\Z)",
            txt, re.I,
        )
        if not qm:
            # try generic: "Statutory notices" / "Notices of work" anywhere
            qm = re.search(
                r"((?:[Ss]tatutory\s+notices?|Notices\s+of\s+work)[\s\S]{0,1500})",
                txt,
            )
        if qm:
            window = qm.group(1)
            # Count [x]YES vs [x]NO in this window (ignore [ ]YES/[ ]NO unchecked).
            yes_count = len(re.findall(r"\[\s*[Xx]\s*\]\s*YES", window, re.I))
            no_count = len(re.findall(r"\[\s*[Xx]\s*\]\s*NO", window, re.I))
            if yes_count + no_count > 0:
                val = yes_count > 0
                return FieldEvidence(value=val, page=pn,
                                     source=f"Q16 statutory notices: {yes_count} YES, {no_count} NO checked")
    # Fallback to single-question yes/no
    res = extract_pq_yes_no(
        pages, pq_range,
        r"(?:[Ss]tatutory\s+notices?|Repairs\s+notices?|Notices\s+of\s+work)",
    )
    if res:
        val, page, source = res
        return FieldEvidence(value=val, page=page, source=source)
    return FieldEvidence()


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


def find_cat_notes_contradictions(condition_table: list["ConditionRow"]) -> list[dict]:
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


def extract_warranty_items(pages: list[str], pq_range: SectionRange) -> FieldEvidence:
    """List of warranties / certificates marked Yes in PQ."""
    page_range = (pq_range.start_page or 1, pq_range.end_page or len(pages))
    candidates = [
        ("NHBC", r"NHBC[\s\S]{0,80}?\b(Yes|No)\b"),
        ("Damp-proof", r"[Dd]amp[\s-]?proof[\s\S]{0,100}?\b(Yes|No)\b"),
        ("Timber treatment", r"[Tt]imber\s+treatment[\s\S]{0,100}?\b(Yes|No)\b"),
        ("Roofing", r"[Rr]oofing\s+(?:guarantee|warranty)[\s\S]{0,80}?\b(Yes|No)\b"),
        ("Electrical", r"[Ee]lectrical\s+certificate[\s\S]{0,80}?\b(Yes|No)\b"),
        ("Gas safety", r"[Gg]as\s+safety[\s\S]{0,80}?\b(Yes|No)\b"),
    ]
    items = []
    for name, pat in candidates:
        res = _find_in_pages(pages, [pat], page_range=page_range)
        if res and res[0].group(1).strip().lower() == "yes":
            items.append(name)
    if items:
        return FieldEvidence(value=items, page=pq_range.start_page, source=", ".join(items))
    return FieldEvidence(value=[], page=None, source=None)


# ----------------------------------------------------------------------------
# Condition table extractors
# ----------------------------------------------------------------------------

def _build_label_alternation() -> str:
    # Sort longer first to avoid partial matches
    labels_sorted = sorted(set(CONDITION_ROW_LABELS), key=len, reverse=True)
    return "(" + "|".join(re.escape(l) for l in labels_sorted) + ")"


@dataclass
class ConditionRow:
    row: str
    cat: Optional[str]  # "1" / "2" / "3" / "-" / None
    page: Optional[int]
    notes: str = ""


def extract_condition_table_textual(pages: list[str], ss_range: SectionRange) -> list[ConditionRow]:
    """
    Quest / DM Hall: digits inline as 'Repair category   1'.
    """
    page_range = (ss_range.start_page or 1, ss_range.end_page or len(pages))
    label_alt = _build_label_alternation()
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


def extract_condition_table_image(pdf_path: str, pages: list[str], ss_range: SectionRange,
                                  warnings: list[str]) -> list[ConditionRow]:
    """
    Allied template: digits are color-coded image stamps AND row labels are
    typeset in matching colors. We use the row-label font color (extracted via
    pdftohtml -xml) which is fully deterministic.

    Color → cat mapping (Allied uses these exact hex values):
        #c12437 (red)    → "3"
        #f6941d (orange) → "2"
        #3fa29c (teal)   → "1"
        #878787 (grey)   → "-"  (not applicable — no stamp rendered)

    For robustness, if pdftohtml unavailable or hex doesn't match, we fall back
    to nearest-named-color classification.
    """
    page_range = (ss_range.start_page or 1, ss_range.end_page or len(pages))
    label_alt = _build_label_alternation()
    rows: list[ConditionRow] = []

    # Identify condition pages
    cond_pages = []
    for page_num in range(page_range[0], page_range[1] + 1):
        text = pages[page_num - 1]
        if re.search(r"Repair\s+[Cc]ategory", text) and re.search(label_alt, text):
            cond_pages.append(page_num)

    if not cond_pages:
        warnings.append("Allied template: no condition pages detected")
        return []

    with tempfile.TemporaryDirectory() as tmp:
        for page_num in cond_pages:
            text = pages[page_num - 1]
            # Build a notes-by-label dict from layout text (same regex as Quest, with empty digit)
            note_pat = (
                r"(?:^|\n)\s*" + label_alt + r"\s*\n"
                r"\s*Repair\s+[Cc]ategory[:\s]*\n"
                r"\s*Notes?\s*[:\s]*([\s\S]{0,800}?)"
                r"(?=\n\s*(?:" + label_alt + r"|Address:|\d+\.\s+[A-Z]|$))"
            )
            notes_by_label: dict[str, str] = {}
            for m in re.finditer(note_pat, text, re.M):
                lbl = m.group(1)
                notes = re.sub(r"\s+", " ", (m.group(2) or "").strip())[:600]
                notes_by_label[lbl] = notes

            # Run pdftohtml -xml to recover per-text font colors
            page_dir = os.path.join(tmp, f"p{page_num}")
            os.makedirs(page_dir, exist_ok=True)
            xml_path = os.path.join(page_dir, "page.xml")
            try:
                _run([
                    "pdftohtml", "-f", str(page_num), "-l", str(page_num),
                    "-xml", "-i", "-q",
                    pdf_path, xml_path,
                ])
                # pdftohtml writes to <prefix> but appends .xml automatically
                actual_xml = xml_path if os.path.isfile(xml_path) else xml_path + ".xml"
                if not os.path.isfile(actual_xml):
                    # try alternate suffix
                    candidates = list(Path(page_dir).glob("*.xml"))
                    if candidates:
                        actual_xml = str(candidates[0])
                xml_content = open(actual_xml).read()
            except (RuntimeError, FileNotFoundError) as e:
                warnings.append(f"pdftohtml failed on p{page_num}: {e}")
                continue

            page_rows = _parse_allied_xml_page(xml_content, page_num, label_alt, notes_by_label, warnings)
            rows.extend(page_rows)

    return rows


def _parse_allied_xml_page(xml: str, page_num: int, label_alt: str,
                           notes_by_label: dict[str, str],
                           warnings: list[str]) -> list[ConditionRow]:
    """
    Parse pdftohtml -xml output for a single Allied condition page.
    Returns ordered list of ConditionRow.
    """
    # Build font_id → cat map from <fontspec> elements.
    font_to_cat: dict[str, str] = {}
    for m in re.finditer(r'<fontspec\s+id="(\d+)"[^>]*color="(#[0-9a-fA-F]{6})"', xml):
        fid, color = m.group(1), m.group(2).lower()
        cat = _hex_color_to_cat(color)
        if cat is not None:
            font_to_cat[fid] = cat

    if not font_to_cat:
        warnings.append(f"Allied p{page_num}: no recognised font colors")
        return []

    # Iterate <text> elements, find label text whose content matches a known row label
    # Use top-position to keep document order.
    label_pat = re.compile(label_alt)
    found = []  # (top_y, label, cat)
    for m in re.finditer(
        r'<text[^>]*top="(\d+)"[^>]*font="(\d+)"[^>]*>([^<]+)</text>',
        xml,
    ):
        top, fid, content = int(m.group(1)), m.group(2), m.group(3).strip()
        if not content:
            continue
        # Match content against known row labels (case-insensitive, exact substring acceptable)
        for lbl_match in label_pat.finditer(content):
            lbl = lbl_match.group(0)
            if fid in font_to_cat:
                found.append((top, lbl, font_to_cat[fid]))
            break  # one label per text element

    found.sort(key=lambda x: x[0])

    rows = []
    seen_labels = set()
    for top, lbl, cat in found:
        # Avoid duplicates within the same page (sometimes labels appear twice)
        if (lbl, page_num) in seen_labels:
            continue
        seen_labels.add((lbl, page_num))
        rows.append(ConditionRow(
            row=lbl,
            cat=cat,
            page=page_num,
            notes=notes_by_label.get(lbl, ""),
        ))
    return rows


# Allied surveyors color palette (verified against 4 Chalmers Buildings sample)
_ALLIED_COLOR_TO_CAT = {
    "#c12437": "3",  # red
    "#f6941d": "2",  # orange
    "#3fa29c": "1",  # teal
    "#878787": "-",  # grey (not applicable)
}


def _hex_color_to_cat(hex_color: str) -> Optional[str]:
    """
    Map a #rrggbb hex color to one of "1"/"2"/"3"/"-".
    Exact-match the Allied palette first; fall back to RGB-distance classification.
    """
    hex_color = hex_color.lower()
    if hex_color in _ALLIED_COLOR_TO_CAT:
        return _ALLIED_COLOR_TO_CAT[hex_color]
    # Parse RGB
    if not re.match(r"^#[0-9a-f]{6}$", hex_color):
        return None
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    # Skip black, white, and very dark colors (these are body text)
    if max(r, g, b) < 80 or min(r, g, b) > 200:
        return None
    # Skip near-grey tones (low saturation)
    if max(r, g, b) - min(r, g, b) < 30:
        # but allow #878787 already handled above
        return None
    # Nearest-neighbour to known cats
    targets = {
        "1": (63, 162, 156),   # teal
        "2": (246, 148, 29),   # orange
        "3": (193, 36, 55),    # red
    }
    best, best_d = None, 1e9
    for k, (tr, tg, tb) in targets.items():
        d = (r - tr) ** 2 + (g - tg) ** 2 + (b - tb) ** 2
        if d < best_d:
            best, best_d = k, d
    # Reject if distance is huge (color isn't close to any known cat)
    return best if best_d < 6000 else None


# ----------------------------------------------------------------------------
# Template detection
# ----------------------------------------------------------------------------

def detect_template(pages_layout: list[str]) -> str:
    """Return one of: 'graham_sibbald' | 'dm_hall' | 'shepherd' | 'dhkk' | 'allied_surveyors' | 'unknown'.

    Shepherd / DHKK / Graham Sibbald 都用 inline-digit condition tables（"Repair category 1"），
    所以 condition table extraction 路径相同；这里区分仅用于 downstream voice / scoring norm。
    """
    full = "\n".join(pages_layout)
    inline_digits = re.findall(r"[Rr]epair\s+[Cc]ategory[:\s]+([123\-])", full)
    colon_only = re.findall(r"Repair\s+category:", full)
    has_dmhall = bool(re.search(r"\bdmhall\.co\.uk\b|DM\s+Hall", full, re.I))
    has_shepherd = bool(re.search(r"shepherd\.co\.uk|SHEPHERD\s+CHARTERED\s+SURVEYORS|J\s*&\s*E\s+Shepherd", full))
    has_dhkk = bool(re.search(r"\bDHKK\b", full))
    if len(inline_digits) >= 5:
        if has_dmhall:
            return "dm_hall"
        if has_shepherd:
            return "shepherd"
        if has_dhkk:
            return "dhkk"
        return "graham_sibbald"
    if colon_only and len(colon_only) >= 5:
        return "allied_surveyors"
    return "unknown"


# ----------------------------------------------------------------------------
# Derived fields
# ----------------------------------------------------------------------------

def compute_derived(condition_table: list[ConditionRow]) -> dict:
    counts = {"1": 0, "2": 0, "3": 0, "-": 0, "unknown": 0}
    for r in condition_table:
        counts[r.cat or "unknown"] += 1
    roof_evidence = []
    roof_issue = False
    for r in condition_table:
        if r.row.strip().rstrip(":").lower() in {l.lower() for l in ROOF_ROW_LABELS} and r.cat in {"2", "3"}:
            roof_issue = True
            roof_evidence.append({"row": r.row, "cat": r.cat})
    return {
        "category1_count": counts["1"],
        "category2_count": counts["2"],
        "category3_count": counts["3"],
        "category_unknown_count": counts["unknown"],
        "roof_issue": roof_issue,
        "roof_issue_evidence": roof_evidence,
    }


# ----------------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------------

def validate(regex_extracted: dict[str, FieldEvidence], condition_table: list[ConditionRow],
             warnings: list[str]) -> None:
    def _check(field: str, predicate, msg: str):
        ev = regex_extracted.get(field)
        if ev and ev.value is not None and not predicate(ev.value):
            warnings.append(f"{field} validation failed: {ev.value!r} — {msg}")
            ev.value = None

    _check("council_tax_band", lambda v: isinstance(v, str) and v in "ABCDEFGH",
           "must be A-H")
    _check("epc_rating", lambda v: isinstance(v, str) and v in "ABCDEFG",
           "must be A-G")
    _check("epc_score", lambda v: isinstance(v, int) and 1 <= v <= 100, "must be 1-100")
    _check("epc_potential_score", lambda v: isinstance(v, int) and 1 <= v <= 100, "must be 1-100")
    _check("construction_year_approx", lambda v: isinstance(v, int) and 1700 <= v <= 2030, "year out of range")
    _check("floor_area_m2", lambda v: isinstance(v, int) and 15 <= v <= 1000, "m² out of plausible range")
    _check("market_valuation", lambda v: isinstance(v, int) and 20000 <= v <= 5_000_000, "£ out of plausible range")
    _check("reinstatement_cost", lambda v: isinstance(v, int) and 20000 <= v <= 5_000_000, "£ out of plausible range")
    _check("bedrooms", lambda v: isinstance(v, int) and 0 < v <= 10, "bedroom count implausible")

    if len(condition_table) < 12:
        warnings.append(
            f"condition_table has only {len(condition_table)} rows; expected ≥12. Template may be unrecognized."
        )
    counts = compute_derived(condition_table)
    if counts["category_unknown_count"] > 0:
        warnings.append(
            f"{counts['category_unknown_count']} condition rows have unknown category"
        )


# ----------------------------------------------------------------------------
# M1: EPC regulatory risk classification (deterministic, NOT extracted from PDF)
# ----------------------------------------------------------------------------

def epc_regulatory_risk(epc_rating: Optional[str]) -> dict:
    """
    Returns a fixed deterministic block describing the policy risk.
    Critically: this is NOT extracted from the PDF. It's a hard-coded mapping
    based on Scottish Government policy STATUS as of 2024-2026.

    Scope (as of 2026-05):
    - Owner-occupier homes: NO MEES law in effect or imminent. Was consulted; not legislated.
    - Private Rental Sector (PRS): Energy Efficiency (Scotland) Regulations 2020 paused;
      a successor Heat in Buildings Bill is in consultation, not yet enacted (as of 2026).
    - Sale of property: NO mandatory upgrade requirement.

    Skill is forbidden from generating its own narrative on this — must use this enum.
    """
    if not epc_rating:
        return {
            "status": "Unknown_no_rating",
            "owner_occupier_legal_requirement": "None_currently",
            "owner_occupier_proposed_rules": "Heat_in_Buildings_Bill_in_consultation_2024_2026",
            "prs_legal_requirement": "Paused_2020_regulations_no_active_minimum",
            "narrative_disclaimer": (
                "无 EPC 等级数据。任何关于 EPC 政策的描述应注明："
                "自住房目前无强制 MEES 立法。"
            ),
        }
    # Both bands of risk: where we sit and where the bar is rumored to be set.
    return {
        "status": ("Compliant_with_proposed_C" if epc_rating in "ABC" else "Below_proposed_C"),
        "owner_occupier_legal_requirement": "None_currently",
        "owner_occupier_proposed_rules": "Heat_in_Buildings_Bill_in_consultation_2024_2026",
        "prs_legal_requirement": "Paused_2020_regulations_no_active_minimum",
        "narrative_disclaimer": (
            "苏格兰自住房目前无强制 EPC 等级要求。Heat in Buildings Bill 仍处于咨询阶段"
            "（2024-2026 起草中），不应描述为「已立法」或「即将强制」。"
            "私人出租 (PRS) 2020 法规已暂停。"
        ),
    }


# ----------------------------------------------------------------------------
# Main pipeline
# ----------------------------------------------------------------------------

def parse(pdf_path: str, debug: bool = False) -> dict:
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(pdf_path)

    pdf_sha = hashlib.sha256(open(pdf_path, "rb").read()).hexdigest()
    n_pages = page_count(pdf_path)
    layout_text = extract_text_layout(pdf_path)
    raw_text = extract_text_raw(pdf_path)
    pages_layout = split_pages(layout_text)
    pages_raw = split_pages(raw_text)
    # Pad shorter list (rare alignment glitch in pdftotext)
    while len(pages_raw) < len(pages_layout):
        pages_raw.append("")

    if debug:
        print(f"[debug] pdf_path={pdf_path}", file=sys.stderr)
        print(f"[debug] page_count={n_pages}", file=sys.stderr)

    template = detect_template(pages_layout)
    sections = detect_sections(pages_layout)

    if debug:
        print(f"[debug] template={template}", file=sys.stderr)
        for k, v in sections.items():
            print(f"[debug] {k}: {v.start_page}–{v.end_page}", file=sys.stderr)

    warnings: list[str] = []
    if template == "unknown":
        warnings.append(
            "Template not recognised — extraction will be best-effort; review condition_table carefully."
        )

    # === regex_extracted ===
    re_x: dict[str, FieldEvidence] = {}
    re_x["postcode"] = extract_postcode(pages_layout)
    re_x["address"] = extract_address(pages_layout, re_x["postcode"].value)
    re_x["inspection_date"] = extract_inspection_date(pages_layout)
    re_x["floor_area_m2"] = extract_floor_area(pages_layout)
    re_x["bedrooms"] = extract_bedrooms(pages_layout)
    re_x["council_tax_band"] = extract_council_tax(pages_layout, sections["property_questionnaire"])
    epc_r, epc_s, epc_ps = extract_epc(pages_layout)
    re_x["epc_rating"] = epc_r
    re_x["epc_score"] = epc_s
    re_x["epc_potential_score"] = epc_ps
    re_x["gas_central_heating"] = extract_gas_heating(pages_layout, sections["energy_report"])
    insp_year = None
    if re_x["inspection_date"].value and isinstance(re_x["inspection_date"].value, str):
        m = re.match(r"(\d{4})", re_x["inspection_date"].value)
        if m:
            insp_year = int(m.group(1))
    age_ev = extract_age_year(pages_layout, sections["single_survey"], insp_year)
    re_x["construction_year_approx"] = age_ev
    re_x["construction_period"] = FieldEvidence(
        value=derive_construction_period(age_ev.value),
        page=age_ev.page,
        source=f"derived from year={age_ev.value}" if age_ev.value else None,
    )
    pt_ev = extract_property_type(pages_layout, sections["single_survey"])
    re_x["property_type"] = pt_ev
    floor, floor_n, total_n, main_door = extract_floor_info(pages_layout, pt_ev.value)
    re_x["floor"] = floor
    re_x["floor_number"] = floor_n
    re_x["total_floors"] = total_n
    re_x["main_door_flat"] = main_door
    market, reinst = extract_valuations(pages_layout)
    re_x["market_valuation"] = market
    re_x["reinstatement_cost"] = reinst
    re_x["owner_years"] = extract_owner_years(pages_layout, sections["property_questionnaire"])
    has_factor, factor_name, factor_cost, has_insurance = extract_factor(
        pages_layout, sections["property_questionnaire"]
    )
    re_x["has_factor"] = has_factor
    re_x["factor_name"] = factor_name
    re_x["factor_cost_monthly"] = factor_cost
    re_x["has_building_insurance"] = has_insurance
    re_x["statutory_notices_present"] = extract_statutory_notices(
        pages_layout, sections["property_questionnaire"]
    )
    re_x["warranty_items"] = extract_warranty_items(pages_layout, sections["property_questionnaire"])
    re_x["closing_date"] = FieldEvidence()  # never in HR; populated by /process-emails

    # === condition_table ===
    if template in ("graham_sibbald", "dm_hall", "shepherd", "dhkk"):
        condition_rows = extract_condition_table_textual(pages_layout, sections["single_survey"])
    elif template == "allied_surveyors":
        condition_rows = extract_condition_table_image(
            pdf_path, pages_layout, sections["single_survey"], warnings
        )
    else:
        # unknown template — try textual first as best-effort
        condition_rows = extract_condition_table_textual(pages_layout, sections["single_survey"])
        if len(condition_rows) < 12:
            warnings.append("unknown template: textual condition extraction yielded <12 rows; consider manual review")

    derived = compute_derived(condition_rows)
    derived["cat_notes_contradictions"] = find_cat_notes_contradictions(condition_rows)

    # === validation ===
    validate(re_x, condition_rows, warnings)

    # === assemble output ===
    out = {
        "pdf_path": pdf_path,
        "pdf_sha256": pdf_sha,
        "page_count": n_pages,
        "template_detected": template,
        "sections": {k: asdict(v) for k, v in sections.items()},
        "regex_extracted": {k: asdict(v) for k, v in re_x.items()},
        "condition_table": [asdict(r) for r in condition_rows],
        "derived": derived,
        "epc_regulatory_risk": epc_regulatory_risk(re_x["epc_rating"].value),
        "warnings": warnings,
        # Compact pages dump for LLM fallback. Layout text is more useful;
        # we cap each page at 8000 chars to keep payload manageable.
        "pages": [
            {"n": i + 1, "text_layout": p[:8000]}
            for i, p in enumerate(pages_layout)
        ],
    }
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf_path")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--no-pages", action="store_true",
                    help="Omit the per-page text dump from output (smaller JSON)")
    args = ap.parse_args(argv)

    try:
        result = parse(args.pdf_path, debug=args.debug)
    except Exception as e:
        # Always emit valid JSON so the skill can detect errors.
        json.dump({"error": str(e), "pdf_path": args.pdf_path}, sys.stdout)
        return 1

    if args.no_pages:
        result.pop("pages", None)
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
