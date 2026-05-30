"""Derived fields, validation, and the deterministic EPC regulatory-risk block.

- `compute_derived` rolls up the condition table into counts and roof-issue flag.
- `validate` runs post-extraction sanity checks and zeros out implausible values.
- `epc_regulatory_risk` returns a hard-coded policy block (NOT extracted from
  the PDF) so the skill can describe Scottish MEES / Heat in Buildings Bill
  status without making things up.
"""

from __future__ import annotations

from typing import Optional

from .base import FieldEvidence, ROOF_ROW_LABELS
from .condition_table import ConditionRow


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
