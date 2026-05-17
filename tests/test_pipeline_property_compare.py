"""End-to-end tests for property_compare pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from property_assistant.analysis.comparison import PropertyRanking, RankedProperty
from property_assistant.core.property_record import PropertyRecord
from property_assistant.pipelines.property_compare import (
    RankingValidationError,
    run,
)


@pytest.fixture
def local_env(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("PROPERTY_DATA_DIR", str(tmp_path / "data"))


@pytest.fixture
def seeded_records(local_env):
    from property_assistant.storage import get_storage
    s = get_storage()
    s.upsert_property(PropertyRecord(
        address="A Street, EH3 9QF", hr_valuation=300000.0,
        bedrooms=2, floor_area=70.0, era=1890, epc_rating="C",
    ))
    s.upsert_property(PropertyRecord(
        address="B Road, EH8 9PF", hr_valuation=350000.0,
        bedrooms=3, floor_area=85.0, era=1900, epc_rating="D",
    ))
    s.upsert_property(PropertyRecord(
        address="C Lane, EH9 1HZ", hr_valuation=420000.0,
        bedrooms=3, floor_area=90.0, era=1880, epc_rating="C",
    ))
    return s


def test_run_compares_two_properties(seeded_records, tmp_path):
    out = tmp_path / "compare.html"
    result = run(addresses=["A Street", "B Road"], out_html=out)
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert "A Street" in html
    assert "B Road" in html
    assert "维度对比表" in html
    assert "HR 估价" in html
    assert "winner" in html  # at least one ✨ cell
    # Should NOT show ranking section
    assert "评估师推荐排名" not in html


def test_run_three_properties_with_ranking(seeded_records, tmp_path):
    ranking = PropertyRanking(
        ranked=[
            RankedProperty(address="A Street", rank=1, one_line="最便宜，估值合理"),
            RankedProperty(address="C Lane", rank=2, one_line="面积最大但贵"),
            RankedProperty(address="B Road", rank=3, one_line="EPC 偏低"),
        ],
        bottom_line="A 适合首付有限的买家。如果空间优先则选 C。",
    )
    ranking_path = tmp_path / "ranking.json"
    ranking_path.write_text(json.dumps(ranking.to_dict(), ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "compare3.html"
    result = run(
        addresses=["A Street", "B Road", "C Lane"],
        ranking_path=ranking_path,
        out_html=out,
    )
    html = out.read_text(encoding="utf-8")
    assert "评估师推荐排名" in html
    assert "最便宜，估值合理" in html
    assert "综合判断" in html
    assert result.ranking is not None
    assert result.ranking.ranked[0].address == "A Street"


def test_run_fails_with_invalid_ranking(seeded_records, tmp_path):
    bad = PropertyRanking(
        ranked=[
            RankedProperty(address="A Street", rank=1, one_line="x"),
            RankedProperty(address="A Street", rank=2, one_line="y"),  # duplicate
        ],
        bottom_line="x",
    )
    rp = tmp_path / "bad.json"
    rp.write_text(json.dumps(bad.to_dict()), encoding="utf-8")
    with pytest.raises(RankingValidationError):
        run(addresses=["A Street", "B Road"], ranking_path=rp, out_html=tmp_path / "x.html")


def test_run_fails_with_one_property(seeded_records, tmp_path):
    with pytest.raises(ValueError, match="至少需要 2"):
        run(addresses=["A Street"], out_html=tmp_path / "x.html")


def test_run_fails_with_unknown_address(seeded_records, tmp_path):
    with pytest.raises(ValueError, match="找不到"):
        run(addresses=["A Street", "NonExistent"], out_html=tmp_path / "x.html")
