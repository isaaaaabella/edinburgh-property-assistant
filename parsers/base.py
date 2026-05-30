"""Shared building blocks for the Home Report parser package.

Constants, PDF tooling wrappers (pdftotext / pdfimages / pdftoppm), page
splitting, and the FieldEvidence dataclass used by every field extractor.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
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
# Tooling (pdftotext, pdfimages, pdftoppm)
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
# Field evidence — every extractor returns one of these
# ----------------------------------------------------------------------------

@dataclass
class FieldEvidence:
    value: Any = None
    page: Optional[int] = None
    source: Optional[str] = None


# ----------------------------------------------------------------------------
# Regex helpers (shared by extractors and condition_table)
# ----------------------------------------------------------------------------

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
