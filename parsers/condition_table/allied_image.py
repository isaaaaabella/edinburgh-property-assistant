"""Allied Surveyors / Onesurvey condition-table extraction.

Allied renders repair categories as color-coded image stamps AND types the row
labels in matching colors. We use the row-label font color (recovered via
`pdftohtml -xml`) which is fully deterministic.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from ..base import _run
from ..sections import SectionRange
from ._common import ConditionRow, build_label_alternation


# Allied surveyors color palette (verified against 4 Chalmers Buildings sample)
_ALLIED_COLOR_TO_CAT = {
    "#c12437": "3",  # red
    "#f6941d": "2",  # orange
    "#3fa29c": "1",  # teal
    "#878787": "-",  # grey (not applicable)
}


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
    label_alt = build_label_alternation()
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
