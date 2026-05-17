"""Tests for pipelines.viewing_review."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from property_assistant.core.property_record import PropertyRecord
from property_assistant.pipelines.viewing_review import _sentiment, run


def test_sentiment_positive():
    assert _sentiment("非常喜欢，明亮宽敞干净") == 1
    assert _sentiment("Bright and spacious, love it") == 1


def test_sentiment_negative():
    assert _sentiment("暗，压抑，潮湿") == -1
    assert _sentiment("dark and cramped, smells damp") == -1


def test_sentiment_neutral_or_unclear():
    assert _sentiment("OK") == 0
    assert _sentiment(None) == 0
    assert _sentiment("") == 0


def test_run_filters_to_viewed(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("PROPERTY_DATA_DIR", str(tmp_path / "data"))
    from property_assistant.storage import get_storage
    s = get_storage()
    s.upsert_property(PropertyRecord(address="A — not viewed",
                                      status="🔍 待看",
                                      hr_valuation=300000.0, bedrooms=2, floor_area=70.0))
    s.upsert_property(PropertyRecord(address="B — viewed",
                                      status="👀 已看",
                                      self_feeling="明亮宽敞",
                                      hr_valuation=320000.0, bedrooms=2, floor_area=70.0))
    s.upsert_property(PropertyRecord(address="C — shortlist",
                                      status="⭐ 感兴趣",
                                      hr_valuation=350000.0, bedrooms=3, floor_area=85.0))
    result = run()
    addrs = [p.address for p in result.properties]
    assert "B — viewed" in addrs
    assert "C — shortlist" in addrs
    assert "A — not viewed" not in addrs
    assert result.viewed_count == 2


def test_run_detects_score_feeling_gap():
    # High score, negative feeling
    rec = PropertyRecord(
        address="Test, EH9 1HZ",
        status="👀 已看",
        hr_valuation=320000.0, asking_price=320000.0,
        bedrooms=2, floor_area=70.0,
        building_type="维多利亚Tenement ✅", era=1890,
        floor="2F ✅", gas_heating=True,
        school_zone=["Boroughmuir High School"],
        cat2_count=1, cat3_count=0, epc_rating="C",
        self_feeling="暗，压抑，潮湿",
    )
    result = run(records=[rec])
    assert len(result.gaps) >= 1
    assert any("评分高" in g.description for g in result.gaps)


def test_run_detects_partner_disagreement():
    rec = PropertyRecord(
        address="Test",
        status="👀 已看",
        self_feeling="明亮宽敞", partner_feeling="暗，小，压抑",
        hr_valuation=300000.0, bedrooms=2, floor_area=70.0,
    )
    result = run(records=[rec])
    assert any(g.kind == "partner_disagreement" for g in result.gaps)


def test_run_shortlist_populated():
    a = PropertyRecord(address="Liked", status="⭐ 感兴趣")
    b = PropertyRecord(address="Other", status="👀 已看")
    result = run(records=[a, b])
    assert "Liked" in result.shortlist
    assert "Other" not in result.shortlist


def test_run_only_shortlist_filter():
    a = PropertyRecord(address="X", status="⭐ 感兴趣")
    b = PropertyRecord(address="Y", status="👀 已看")
    result = run(records=[a, b], only_shortlist=True)
    assert {p.address for p in result.properties} == {"X"}


def test_run_partner_score_disagreement():
    rec = PropertyRecord(
        address="Test",
        status="👀 已看",
        self_score=8.5, partner_score=5.0,
    )
    result = run(records=[rec])
    assert any("打分差距大" in g.description for g in result.gaps)
