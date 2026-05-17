"""Tests for analysis.comparison: mechanical compare + ranking validation."""

from __future__ import annotations

from property_assistant.analysis.comparison import (
    PropertyRanking,
    RankedProperty,
    compute_comparison,
)
from property_assistant.core.property_record import PropertyRecord


def _r(addr: str, **kwargs) -> PropertyRecord:
    return PropertyRecord(address=addr, **kwargs)


def test_compute_comparison_basic_two_properties():
    a = _r("A", hr_valuation=300000.0, asking_price=305000.0, bedrooms=2,
           floor_area=70.0, era=1890, epc_rating="C", epc_score=72,
           cat2_count=3, cat3_count=0)
    b = _r("B", hr_valuation=350000.0, asking_price=355000.0, bedrooms=3,
           floor_area=85.0, era=1900, epc_rating="D", epc_score=60,
           cat2_count=5, cat3_count=1)
    c = compute_comparison([a, b])
    assert len(c.properties) == 2
    assert len(c.breakdowns) == 2
    assert all(len(row.values) == 2 for row in c.rows)
    # HR price row: A is cheaper → winner = 0
    hr_row = next(r for r in c.rows if r.label == "HR 估价")
    assert hr_row.values == ["£300,000", "£350,000"]
    assert hr_row.winner_idx == 0
    # Bedrooms row: B has more → winner = 1
    br = next(r for r in c.rows if r.label == "卧室")
    assert br.winner_idx == 1
    # Cat 3 row: A has 0, B has 1 → A wins (lower)
    cat3 = next(r for r in c.rows if r.label == "Cat 3 数")
    assert cat3.winner_idx == 0


def test_compute_comparison_handles_missing_values():
    a = _r("A", hr_valuation=300000.0)
    b = _r("B")  # no data
    c = compute_comparison([a, b])
    hr_row = next(r for r in c.rows if r.label == "HR 估价")
    assert hr_row.values == ["£300,000", "—"]
    # Not enough data to pick a winner
    assert hr_row.winner_idx is None


def test_compute_comparison_tie_no_winner():
    a = _r("A", hr_valuation=300000.0)
    b = _r("B", hr_valuation=300000.0)
    c = compute_comparison([a, b])
    hr_row = next(r for r in c.rows if r.label == "HR 估价")
    assert hr_row.winner_idx is None


def test_compute_comparison_ppsm():
    a = _r("A", hr_valuation=320000.0, floor_area=80.0)   # £4,000/m²
    b = _r("B", hr_valuation=400000.0, floor_area=80.0)   # £5,000/m²
    c = compute_comparison([a, b])
    p = next(r for r in c.rows if r.label == "£/m²")
    assert p.values == ["£4,000", "£5,000"]
    assert p.winner_idx == 0


def test_compute_comparison_drops_all_dash_rows():
    a = _r("A")
    b = _r("B")
    c = compute_comparison([a, b])
    labels = {r.label for r in c.rows}
    assert "通勤 (user)" not in labels  # both empty
    assert "总分" in labels  # always computed


def test_compute_comparison_school_zone_multi():
    a = _r("A", school_zone=["Gillespie's", "Boroughmuir"])
    b = _r("B", school_zone=["其他"])
    c = compute_comparison([a, b])
    sch = next(r for r in c.rows if r.label == "学区")
    assert sch.values == ["Gillespie's · Boroughmuir", "其他"]


# ---------- Ranking validation ----------

def _good_ranking() -> PropertyRanking:
    return PropertyRanking(
        ranked=[
            RankedProperty(address="A", rank=1, one_line="价低估值公允"),
            RankedProperty(address="B", rank=2, one_line="更大但 EPC 偏低"),
        ],
        bottom_line="A 适合首付有限的买家；B 适合家庭，但需算保温改造。",
    )


def test_ranking_passes_validation():
    props = [_r("A"), _r("B")]
    assert _good_ranking().validate(props) == []


def test_ranking_fails_missing_property():
    props = [_r("A"), _r("B"), _r("C")]
    r = _good_ranking()
    assert any("ranked 必须覆盖" in e for e in r.validate(props))


def test_ranking_fails_unknown_address():
    props = [_r("A"), _r("B")]
    r = _good_ranking()
    r.ranked[0].address = "X"
    errs = r.validate(props)
    assert any("X" in e for e in errs)


def test_ranking_fails_duplicate_rank():
    props = [_r("A"), _r("B")]
    r = _good_ranking()
    r.ranked[1].rank = 1
    errs = r.validate(props)
    assert any("全排列" in e for e in errs)


def test_ranking_fails_empty_bottom_line():
    props = [_r("A"), _r("B")]
    r = _good_ranking()
    r.bottom_line = ""
    assert any("bottom_line" in e for e in r.validate(props))


def test_ranking_roundtrip_dict():
    r = _good_ranking()
    r2 = PropertyRanking.from_dict(r.to_dict())
    assert r2.to_dict() == r.to_dict()
