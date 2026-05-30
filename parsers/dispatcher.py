#!/usr/bin/env python3
"""Top-level orchestrator for Scottish Home Report PDF extraction.

Responsibilities:
- `detect_template(pages_layout)` — classify a PDF as one of
  'graham_sibbald' | 'dm_hall' | 'shepherd' | 'dhkk' | 'allied_surveyors' | 'unknown'.
  Today this only routes condition-table extraction strategy; in the future,
  per-template extractor overrides should also dispatch from here.
- `parse(pdf_path)` — run the full pipeline and return the JSON-serialisable
  result dict.
- `main(argv)` + `__main__` — preserves the CLI contract: `python -m
  property_assistant.parsers.dispatcher <pdf_path>` writes the JSON document
  to stdout, identical to the legacy `parse_home_report.py` entry point.

Outputs a JSON document to stdout containing:
- regex_extracted: dict of field → {value, page, source}  (~22 fields)
- condition_table: list of {row, cat, page, notes}        (~18-24 rows)
- derived: {category{1,2,3}_count, roof_issue, ...}
- pages: list of {n, text_layout, text_raw}               (raw text dumps for LLM fallback)
- warnings: list of human-readable warning strings

Templates supported:
- Quest / Graham + Sibbald     — inline `Repair category   1` digits
- DM Hall                      — inline `Repair Category   1` (capital C, slightly different valuation label)
- Shepherd / DHKK              — inline digits, distinguished from Quest by branding
- Allied Surveyors / Onesurvey — digits rendered as colored image stamps; we extract by hashing/colour

Depends only on `pdftotext`, `pdfimages`, `pdftohtml`, `pdftoppm` (poppler) and PIL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict
from typing import Optional

from .base import (
    extract_text_layout,
    extract_text_raw,
    page_count,
    split_pages,
)
from .condition_table import (
    extract_condition_table,
    find_cat_notes_contradictions,
)
from .derived import compute_derived, epc_regulatory_risk, validate
from .extractors import (
    derive_construction_period,
    extract_address,
    extract_age_year,
    extract_bedrooms,
    extract_council_tax,
    extract_epc,
    extract_factor,
    extract_floor_area,
    extract_floor_info,
    extract_gas_heating,
    extract_inspection_date,
    extract_owner_years,
    extract_postcode,
    extract_property_type,
    extract_statutory_notices,
    extract_valuations,
    extract_warranty_items,
)
from .base import FieldEvidence
from .sections import detect_sections


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
    insp_year: Optional[int] = None
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
    condition_rows = extract_condition_table(
        template, pdf_path, pages_layout, sections["single_survey"], warnings
    )

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
