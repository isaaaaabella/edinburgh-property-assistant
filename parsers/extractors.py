"""Field-level extractors for the Single Survey, Energy Report, and Property
Questionnaire sections of a Scottish Home Report PDF.

Each `extract_*` function returns a `FieldEvidence` (value + page + source line)
so the downstream skill can cite provenance. Patterns are unions across all
known surveyor templates (Quest / Graham+Sibbald / DM Hall / Shepherd / DHKK /
Allied), in priority-of-specificity order — no per-template dispatch here.
"""

from __future__ import annotations

import re
from typing import Optional

from .base import FieldEvidence, _find_in_pages
from .sections import SectionRange


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
