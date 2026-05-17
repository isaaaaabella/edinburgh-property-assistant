"""Tests for render.renderer — fixture-driven HTML generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from property_assistant.analysis.scoring import DimensionScore, ScoreBreakdown
from property_assistant.analysis.surveyor_opinion import Finding, SurveyorOpinion
from property_assistant.core.property_record import PropertyRecord
from property_assistant.render.renderer import render_home_report


def _fixture_opinion() -> SurveyorOpinion:
    return SurveyorOpinion(
        overall_positioning=[
            Finding(kind="fact", text="维多利亚 Tenement，2/3 楼", evidence_page=4),
            Finding(kind="judgment", text="经典爱丁堡保值类型，但门窗已是 2010s 改装"),
        ],
        score_corrections=[
            Finding(kind="judgment",
                    text="Rainwater Cat 2 但 notes 写 'was not raining'，建议回退",
                    contradiction_id="Rainwater fittings_p17", score_delta=0.5),
        ],
        real_concerns=[
            Finding(kind="judgment", text="Common stairwell 维护协议待确认"),
            Finding(kind="assumption", text="未知 Factor 是否覆盖外墙"),
        ],
        valuation_judgment=[
            Finding(kind="fact", text="HR 估价 £450k", evidence_page=5),
            Finding(kind="judgment", text="略低于 Marchmont 同类成交均价"),
        ],
        offer_direction=[
            Finding(kind="judgment", text="建议挂牌价附近，留 1% 让步空间"),
        ],
        viewing_priorities=[
            Finding(kind="judgment", text="问 Factor 月费明细"),
            Finding(kind="judgment", text="问最近一次屋顶大修日期"),
            Finding(kind="judgment", text="问 Closing Date 安排"),
        ],
    )


def _fixture_record() -> PropertyRecord:
    return PropertyRecord(
        address="10 Marchmont Rd, Edinburgh EH9 1HZ",
        postcode="EH9 1HZ",
        hr_valuation=450000.0,
        asking_price=455000.0,
        bedrooms=3,
        floor_area=92.0,
        floor="2F ✅",
        building_type="维多利亚Tenement ✅",
        era=1890,
        epc_rating="C",
        epc_score=72,
        cat2_count=5,
        cat3_count=0,
        roof_issue=False,
        factor_status="专业Factor含保险 ✅",
        factor_monthly=18.0,
        school_zone=["James Gillespie's ✅"],
    )


def _fixture_breakdown() -> ScoreBreakdown:
    return ScoreBreakdown(
        total=82.0, recommendation="⭐ 强烈建议认真考虑",
        dimensions=[
            DimensionScore("value", 22, 25, "£4,945/m² → good (19) + bonus 3"),
            DimensionScore("building_type", 20, 20, "Traditional Tenement Pre-1919"),
            DimensionScore("floor", 9, 10, "Second floor"),
            DimensionScore("gas", 15, 15, "Gas ✓"),
            DimensionScore("school", 18, 20, "James Gillespie's"),
            DimensionScore("condition", 7, 10, "5 Cat 2"),
            DimensionScore("bonus", 3, 5, "EPC C +1 · Pre-1919 +2"),
        ],
    )


def _fixture_parsed() -> dict:
    return {
        "regex_extracted": {
            "address": {"value": "10 Marchmont Rd, Edinburgh EH9 1HZ", "page": 1, "source": "title"},
            "epc_rating": {"value": "C", "page": 29, "source": "epc"},
            "hr_valuation": {"value": "£450,000", "page": 5, "source": "valuation"},
        },
        "condition_table": [
            {"row": "Chimney stacks", "cat": "2", "page": 14, "notes": "minor pointing required"},
            {"row": "Rainwater fittings", "cat": "2", "page": 17, "notes": "was not raining at inspection"},
            {"row": "Internal walls", "cat": "1", "page": 21, "notes": "ok"},
        ],
        "derived": {
            "cat_notes_contradictions": [
                {"row": "Rainwater fittings", "page": 17, "negative_phrases": ["was not raining"]},
            ],
        },
        "warnings": [],
    }


def test_render_home_report_produces_html(tmp_path: Path):
    out = tmp_path / "report.html"
    result = render_home_report(
        record=_fixture_record(),
        breakdown=_fixture_breakdown(),
        opinion=_fixture_opinion(),
        parsed=_fixture_parsed(),
        out_path=out,
        storage_backend="local",
    )
    assert result.exists()
    html = result.read_text(encoding="utf-8")
    # Header
    assert "Marchmont" in html
    assert "82" in html  # score
    assert "⭐" in html or "强烈建议" in html
    # Layered cards
    assert "客观事实" in html
    assert "评估师判断" in html
    assert "假设与未知" in html
    # Contradiction badge appears
    assert "⚡" in html
    # Detailed sections
    assert "整体定位" in html
    assert "评分校正" in html
    assert "看房当日" in html
    # Meta grid
    assert "HR 估价" in html
    # Score breakdown
    assert "评分详情" in html
    # Condition table includes Cat 2 rows but not Cat 1
    assert "Chimney stacks" in html
    assert "Rainwater fittings" in html
    # Evidence details collapsible present
    assert "提取证据" in html


def test_render_home_report_handles_minimal_data(tmp_path: Path):
    out = tmp_path / "min.html"
    minimal_record = PropertyRecord(address="x")
    minimal_breakdown = ScoreBreakdown(
        total=0, recommendation="⚠️", dimensions=[],
    )
    minimal_opinion = SurveyorOpinion(
        overall_positioning=[Finding(kind="judgment", text="minimal opinion")],
        valuation_judgment=[Finding(kind="judgment", text="cannot value")],
        offer_direction=[Finding(kind="judgment", text="walk away")],
        viewing_priorities=[Finding(kind="judgment", text="don't visit")],
    )
    result = render_home_report(
        record=minimal_record,
        breakdown=minimal_breakdown,
        opinion=minimal_opinion,
        parsed={"regex_extracted": {}, "condition_table": [], "derived": {}},
        out_path=out,
    )
    assert result.exists()
    html = result.read_text(encoding="utf-8")
    assert "minimal opinion" in html


def test_highlight_metrics_wraps_money_pct_page():
    from property_assistant.render.renderer import highlight_metrics
    html = str(highlight_metrics("HR 估价 £450,000，上涨 +42%，见 p.17"))
    assert '<span class="hi-money">£450,000</span>' in html
    assert '<span class="hi-pct">+42%</span>' in html
    assert '<span class="hi-page">p.17</span>' in html


def test_highlight_metrics_escapes_dangerous_input():
    from property_assistant.render.renderer import highlight_metrics
    html = str(highlight_metrics("<script>alert(1)</script>"))
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_highlight_metrics_handles_none_and_empty():
    from property_assistant.render.renderer import highlight_metrics
    assert str(highlight_metrics(None)) == ""
    assert str(highlight_metrics("")) == ""


def test_render_includes_tldr_and_nav(tmp_path: Path):
    out = tmp_path / "r.html"
    render_home_report(
        record=_fixture_record(),
        breakdown=_fixture_breakdown(),
        opinion=_fixture_opinion(),
        parsed=_fixture_parsed(),
        out_path=out,
    )
    html = out.read_text(encoding="utf-8")
    assert "TL;DR" in html
    assert 'class="section-nav"' in html
    assert 'id="layered"' in html
    assert 'id="area"' in html or "区域情报" in html  # area only when school_zone present
    assert 'id="scoring"' in html
    assert 'id="opinion-1"' in html
    # Score correction visual appears because fixture has score_delta
    assert "评估师调整" in html


def test_render_explicit_tldr_overrides_derivation(tmp_path: Path):
    out = tmp_path / "r.html"
    render_home_report(
        record=_fixture_record(),
        breakdown=_fixture_breakdown(),
        opinion=_fixture_opinion(),
        parsed=_fixture_parsed(),
        out_path=out,
        tldr="一句话执行摘要 OVERRIDE",
    )
    html = out.read_text(encoding="utf-8")
    assert "一句话执行摘要 OVERRIDE" in html


def test_render_writes_to_nested_directory(tmp_path: Path):
    out = tmp_path / "subdir" / "deeper" / "r.html"
    render_home_report(
        record=_fixture_record(),
        breakdown=_fixture_breakdown(),
        opinion=_fixture_opinion(),
        parsed=_fixture_parsed(),
        out_path=out,
    )
    assert out.exists()
