"""Property scoring — 0-100 across 7 dimensions.

Weights and thresholds come from ~/.claude/property_assistant/preferences.json
so they can be tuned without code changes. Output is a typed `ScoreBreakdown`
suitable for both terminal summaries and HTML rendering.

Dimensions (default weights):
  value          25   £/m² × bedroom bonus
  building_type  20   tenement era / type
  floor          10   floor preference
  gas            15   gas central heating
  school         20   school catchment
  condition      10   Category counts + roof penalty (can go negative)
  bonus           5   EPC + pre-1919 bonuses
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from property_assistant.core.property_record import PropertyRecord


PREFERENCES_PATH = Path(__file__).resolve().parent.parent / "preferences.json"
PREFERENCES_EXAMPLE_PATH = Path(__file__).resolve().parent.parent / "preferences.example.json"


@dataclass
class DimensionScore:
    name: str
    score: float
    max_score: float
    detail: str


@dataclass
class ScoreBreakdown:
    total: float
    recommendation: str       # ⭐/✅/⚠️/❌ + label
    dimensions: list[DimensionScore] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "recommendation": self.recommendation,
            "dimensions": [asdict(d) for d in self.dimensions],
        }


def load_preferences(path: Path | None = None) -> dict[str, Any]:
    """Load user preferences.json; fall back to preferences.example.json if absent.

    First-time users (e.g., friends who just cloned the repo) get sensible
    defaults from the example template until they `cp preferences.example.json
    preferences.json` and customise.
    """
    p = path or PREFERENCES_PATH
    if not p.exists() and path is None:
        p = PREFERENCES_EXAMPLE_PATH
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def compute(record: PropertyRecord, prefs: dict | None = None) -> ScoreBreakdown:
    prefs = prefs or load_preferences()

    dims = [
        _score_value(record, prefs),
        _score_building_type(record, prefs),
        _score_floor(record, prefs),
        _score_gas(record, prefs),
        _score_school(record, prefs),
        _score_condition(record, prefs),
        _score_bonus(record, prefs),
    ]
    total = round(sum(d.score for d in dims), 1)
    return ScoreBreakdown(
        total=total,
        recommendation=_recommendation(total),
        dimensions=dims,
    )


def _recommendation(total: float) -> str:
    if total >= 80:
        return "⭐ 强烈建议认真考虑"
    if total >= 65:
        return "✅ 建议考虑"
    if total >= 45:
        return "⚠️ 有潜力但需谨慎"
    return "❌ 不建议认真考虑"


# ---------- Dimension scorers ----------

def _score_value(rec: PropertyRecord, prefs: dict) -> DimensionScore:
    weights = prefs["score_weights"]
    cfg = prefs["value_scores"]
    price = rec.asking_price or rec.hr_valuation
    area = rec.floor_area
    if not price or not area:
        return DimensionScore("value", 0, weights["value"],
                              "价格或面积缺失，无法计算 £/m²")
    per_sqm = price / area
    band, base = _value_band(per_sqm, cfg)
    bonus_map = cfg.get("bedroom_bonus", {})
    if rec.bedrooms is not None:
        bonus = bonus_map.get(str(rec.bedrooms)) or bonus_map.get("default") or 0
    else:
        bonus = 0
    final = min(weights["value"], base + bonus)
    detail = f"£{per_sqm:,.0f}/m² → {band} ({base}分) + bedroom bonus +{bonus}"
    return DimensionScore("value", final, weights["value"], detail)


def _value_band(per_sqm: float, cfg: dict) -> tuple[str, float]:
    """Return (band_label, base_score)."""
    if per_sqm < cfg["excellent"]["max_gbp_per_sqm"]:
        return "excellent", cfg["excellent"]["score"]
    if per_sqm < cfg["good"]["max_gbp_per_sqm"]:
        return "good", cfg["good"]["score"]
    if per_sqm < cfg["fair"]["max_gbp_per_sqm"]:
        return "fair", cfg["fair"]["score"]
    return "expensive", cfg["expensive"]["score"]


def _score_building_type(rec: PropertyRecord, prefs: dict) -> DimensionScore:
    w = prefs["score_weights"]["building_type"]
    cfg = prefs["building_type_scores"]
    bt = (rec.building_type or "").lower()
    era = rec.era

    if "tenement" in bt and era and era < 1919:
        score = cfg["traditional_tenement_pre1919"]
        detail = f"Traditional Tenement (Pre-1919): {bt}"
    elif "维多利亚tenement" in bt or "tenement" in bt:
        score = cfg["tenement_other"]
        detail = f"Tenement variant: {bt}"
    elif "现代公寓" in bt or "purpose" in bt:
        score = cfg["purpose_built_flat"]
        detail = f"Purpose-built flat: {bt}"
    else:
        score = cfg["other"]
        detail = f"其他: {bt or 'unknown'}"
    return DimensionScore("building_type", score, w, detail)


def _score_floor(rec: PropertyRecord, prefs: dict) -> DimensionScore:
    w = prefs["score_weights"]["floor"]
    cfg = prefs["floor_scores"]
    floor = (rec.floor or "").lower()
    main_door = bool(rec.is_main_door)

    if "ground" in floor:
        score = cfg["ground_main_door"] if main_door else cfg["ground_standard"]
        detail = f"Ground ({'main door' if main_door else 'standard'})"
    elif floor.startswith("1f") or "1楼" in floor:
        score = cfg["first"]
        detail = "First floor"
    elif floor.startswith("2f") or "2楼" in floor:
        score = cfg["second"]
        detail = "Second floor"
    elif floor.startswith("3f") or "3楼" in floor:
        score = cfg["third"]
        detail = "Third floor"
    elif "顶层" in floor or "top" in floor:
        score = cfg["top"]
        detail = "Top floor"
    else:
        score = (cfg["first"] + cfg["second"]) / 2
        detail = f"楼层未识别 ({floor or 'unknown'}) — 取中位"
    return DimensionScore("floor", score, w, detail)


def _score_gas(rec: PropertyRecord, prefs: dict) -> DimensionScore:
    w = prefs["score_weights"]["gas"]
    if rec.gas_heating is True:
        return DimensionScore("gas", w, w, "Gas central heating ✓")
    if rec.gas_heating is False:
        return DimensionScore("gas", 0, w, "非 gas 供暖")
    return DimensionScore("gas", w / 2, w, "供暖类型未知 — 取一半")


def _score_school(rec: PropertyRecord, prefs: dict) -> DimensionScore:
    w = prefs["score_weights"]["school"]
    cfg = prefs["school_scores"]
    zones = rec.school_zone or []

    best = 0
    matched = None
    for z in zones:
        z_clean = z.replace("✅", "").strip()
        if "Boroughmuir" in z_clean and cfg.get("Boroughmuir High School", 0) > best:
            best = cfg["Boroughmuir High School"]
            matched = z
        elif "Gillespie" in z_clean and cfg.get("James Gillespie's High School", 0) > best:
            best = cfg["James Gillespie's High School"]
            matched = z
    if best == 0:
        if any("待确认" in z or "unknown" in z.lower() for z in zones):
            return DimensionScore("school", cfg["unknown"], w, "学区待确认 — 给中间分")
        if zones:
            return DimensionScore("school", cfg["other_south_side"], w,
                                  f"其他学区: {', '.join(zones)}")
        return DimensionScore("school", cfg["unknown"], w, "无学区数据 — 给中间分")
    return DimensionScore("school", best, w, f"匹配 {matched}")


def _score_condition(rec: PropertyRecord, prefs: dict) -> DimensionScore:
    w = prefs["score_weights"]["condition"]
    cfg = prefs["condition_scores"]
    cat2 = rec.cat2_count or 0
    cat3 = rec.cat3_count or 0

    if cat3 >= 2:
        base = cfg["cat3_2plus"]
        base_label = "2+ Cat 3"
    elif cat3 == 1:
        base = cfg["cat3_1"]
        base_label = "1 Cat 3"
    elif cat2 >= 6:
        base = cfg["cat3_0_cat2_6plus"]
        base_label = f"{cat2} Cat 2"
    elif cat2 >= 3:
        base = cfg["cat3_0_cat2_3to5"]
        base_label = f"{cat2} Cat 2"
    else:
        base = cfg["cat3_0_cat2_lte2"]
        base_label = f"≤2 Cat 2 (实际 {cat2})"

    roof_pen = cfg["roof_penalty"] if rec.roof_issue else 0
    total = max(cfg["minimum"], base + roof_pen)
    detail = f"基础 {base} ({base_label})"
    if roof_pen:
        detail += f" + roof_penalty {roof_pen}"
    return DimensionScore("condition", total, w, detail)


def _score_bonus(rec: PropertyRecord, prefs: dict) -> DimensionScore:
    w = prefs["score_weights"]["bonus"]
    cfg = prefs["bonus_scores"]
    parts = []
    total = 0
    epc = (rec.epc_rating or "").upper()
    if epc in {"A", "B"}:
        total += cfg["epc_B_or_above"]
        parts.append(f"EPC {epc} +{cfg['epc_B_or_above']}")
    elif epc == "C":
        total += cfg["epc_C"]
        parts.append(f"EPC C +{cfg['epc_C']}")
    if rec.era and rec.era < 1919:
        total += cfg["pre_1919"]
        parts.append(f"Pre-1919 +{cfg['pre_1919']}")
    detail = " · ".join(parts) if parts else "no bonuses"
    return DimensionScore("bonus", min(w, total), w, detail)
