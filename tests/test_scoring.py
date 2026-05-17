"""Tests for analysis.scoring."""

from __future__ import annotations

import pytest

from property_assistant.analysis.scoring import compute, load_preferences
from property_assistant.core.property_record import PropertyRecord


@pytest.fixture(scope="module")
def prefs() -> dict:
    return load_preferences()


def test_perfect_property_scores_high(prefs):
    rec = PropertyRecord(
        address="Perfect, EH9 1HZ",
        asking_price=300000.0,
        floor_area=85.0,                              # £3,529/m² → "good"
        bedrooms=2,
        building_type="Traditional Tenement",
        era=1890,
        floor="1F",
        gas_heating=True,
        school_zone=["Boroughmuir High School"],
        cat2_count=1,
        cat3_count=0,
        roof_issue=False,
        epc_rating="B",
    )
    b = compute(rec, prefs)
    # Expectations:
    # value: ~£3,529/m² → "good" 19 + 2-bed bonus 3 = 22
    # building_type: 20 (tenement pre-1919)
    # floor: 10 (first)
    # gas: 15
    # school: 20 (Boroughmuir)
    # condition: 10 (≤2 cat2, 0 cat3, no roof)
    # bonus: min(5, 3 + 2) = 5
    # total ~ 102 → capped per-dimension, but bonus also capped at 5
    # Actually: 22+20+10+15+20+10+5 = 102 but bonus dim is capped at 5,
    # individual dims aren't capped by total. Total can exceed 100 with
    # bedroom_bonus included; check ≥ 80.
    assert b.total >= 80
    assert "强烈建议" in b.recommendation


def test_poor_property_scores_low(prefs):
    rec = PropertyRecord(
        address="Poor, EH4 9XX",
        asking_price=500000.0,
        floor_area=70.0,                              # £7,142/m² → expensive
        bedrooms=1,
        building_type="现代公寓 ⚠️",
        era=2010,
        floor="Ground ⚠️",
        is_main_door=False,
        gas_heating=False,
        school_zone=["其他"],
        cat2_count=8,
        cat3_count=2,
        roof_issue=True,
        epc_rating="E",
    )
    b = compute(rec, prefs)
    assert b.total < 45
    assert "不建议" in b.recommendation


def test_value_handles_missing_price_area(prefs):
    rec = PropertyRecord(address="x")
    b = compute(rec, prefs)
    value_dim = [d for d in b.dimensions if d.name == "value"][0]
    assert value_dim.score == 0
    assert "缺失" in value_dim.detail


def test_school_zone_with_emoji_matches(prefs):
    rec = PropertyRecord(
        address="x",
        school_zone=["James Gillespie's ✅"],
    )
    b = compute(rec, prefs)
    school = [d for d in b.dimensions if d.name == "school"][0]
    # Matched James Gillespie's High School → 18
    assert school.score == 18


def test_condition_roof_penalty_applied(prefs):
    base = PropertyRecord(address="x", cat2_count=1, cat3_count=0, roof_issue=False)
    with_roof = PropertyRecord(address="x", cat2_count=1, cat3_count=0, roof_issue=True)
    b1 = compute(base, prefs)
    b2 = compute(with_roof, prefs)
    cond1 = [d for d in b1.dimensions if d.name == "condition"][0].score
    cond2 = [d for d in b2.dimensions if d.name == "condition"][0].score
    assert cond2 < cond1


def test_dimensions_total_to_breakdown_total(prefs):
    rec = PropertyRecord(
        address="x", asking_price=400000.0, floor_area=80.0, bedrooms=2,
        building_type="Tenement", era=1900, floor="2F", gas_heating=True,
        school_zone=["Boroughmuir High School"], cat2_count=2, cat3_count=0,
        epc_rating="C",
    )
    b = compute(rec, prefs)
    assert round(sum(d.score for d in b.dimensions), 1) == b.total


def test_recommendation_thresholds():
    from property_assistant.analysis.scoring import _recommendation
    assert "强烈建议" in _recommendation(85)
    assert "建议考虑" in _recommendation(70)
    assert "有潜力" in _recommendation(50)
    assert "不建议" in _recommendation(20)
