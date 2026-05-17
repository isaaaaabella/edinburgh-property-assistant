"""End-to-end pipeline test using injected parsed + opinion fixtures.

This exercises pipelines.home_report.run() WITHOUT actually invoking the PDF
parser or the LLM — fixtures stand in for both. The point is to verify the
glue between PropertyRecord, scoring, opinion validation, rendering, and
LocalJSONStorage works end-to-end.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from property_assistant.pipelines.home_report import (
    OpinionValidationError,
    run,
)


def _opinion_json() -> dict:
    """Valid SurveyorOpinion covering the single contradiction in _parsed_json."""
    return {
        "overall_positioning": [
            {"kind": "fact", "text": "维多利亚 Tenement，3/3 楼", "evidence_page": 4},
            {"kind": "judgment", "text": "经典爱丁堡保值类型"},
        ],
        "score_corrections": [
            {"kind": "judgment", "text": "Rainwater Cat 2 但 notes 反，建议回退",
             "contradiction_id": "Rainwater fittings_p17", "score_delta": 0.5},
        ],
        "real_concerns": [
            {"kind": "judgment", "text": "Common stairwell 维护协议待确认"},
        ],
        "valuation_judgment": [
            {"kind": "fact", "text": "HR 估价 £450k", "evidence_page": 5},
            {"kind": "judgment", "text": "略低于 Marchmont 同类成交均价"},
        ],
        "offer_direction": [
            {"kind": "judgment", "text": "建议挂牌价附近，留 1% 让步空间"},
        ],
        "viewing_priorities": [
            {"kind": "judgment", "text": "问 Factor 月费明细"},
            {"kind": "judgment", "text": "问最近一次屋顶大修"},
            {"kind": "judgment", "text": "问 Closing Date 安排"},
        ],
    }


def _parsed_json() -> dict:
    return {
        "regex_extracted": {
            "address": {"value": "10 Marchmont Rd, Edinburgh EH9 1HZ", "page": 1, "source": "title"},
            "postcode": {"value": "EH9 1HZ", "page": 1, "source": "title"},
            "hr_valuation": {"value": "£450,000", "page": 5, "source": "valuation"},
            "bedrooms": {"value": "3", "page": 7, "source": "accommodation"},
            "epc_rating": {"value": "C", "page": 29, "source": "epc"},
            "floor_area": {"value": "92", "page": 7, "source": "accommodation"},
            "asking_price": {"value": "£455,000", "page": 3, "source": "rightmove"},
            "building_type": {"value": "维多利亚Tenement ✅", "page": 4, "source": "construction"},
            "era": {"value": "1890", "page": 4, "source": "construction"},
            "floor": {"value": "2F", "page": 7, "source": "accommodation"},
            "gas_heating": {"value": "true", "page": 30, "source": "services"},
            "factor_status": {"value": "专业Factor含保险 ✅", "page": 33, "source": "factor"},
        },
        "condition_table": [
            {"row": "Chimney stacks", "cat": "2", "page": 14, "notes": "minor pointing"},
            {"row": "Rainwater fittings", "cat": "2", "page": 17, "notes": "was not raining"},
        ],
        "derived": {
            "category2_count": 2,
            "category3_count": 0,
            "roof_issue": False,
            "cat_notes_contradictions": [
                {"row": "Rainwater fittings", "page": 17, "negative_phrases": ["was not raining"]},
            ],
        },
        "warnings": [],
    }


@pytest.fixture
def fake_pdf(tmp_path: Path) -> Path:
    p = tmp_path / "sample_home_report.pdf"
    p.write_bytes(b"%PDF-1.4 fake content for pipeline test\n")
    return p


@pytest.fixture
def opinion_path(tmp_path: Path) -> Path:
    p = tmp_path / "opinion.json"
    p.write_text(json.dumps(_opinion_json(), ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture
def local_env(tmp_path, monkeypatch):
    """Use LocalJSONStorage rooted in tmp_path."""
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("PROPERTY_DATA_DIR", str(tmp_path / "data"))


def test_run_with_injected_parsed_succeeds(
    fake_pdf, opinion_path, tmp_path, local_env
):
    out = tmp_path / "report.html"
    result = run(
        fake_pdf,
        opinion_path=opinion_path,
        out_html=out,
        parsed=_parsed_json(),
    )

    assert result.html_path == out
    assert out.exists()
    assert result.breakdown.total > 0
    assert result.record.address == "10 Marchmont Rd, Edinburgh EH9 1HZ"
    assert result.property_id  # local storage returned slug
    # HTML contains the layered cards
    html = out.read_text(encoding="utf-8")
    assert "客观事实" in html
    assert "评估师判断" in html
    assert "⚡" in html  # contradiction badge


def test_run_persists_parsed_alongside_pdf(
    fake_pdf, opinion_path, tmp_path, local_env
):
    out = tmp_path / "r.html"
    result = run(fake_pdf, opinion_path=opinion_path, out_html=out, parsed=_parsed_json())
    assert result.parsed_path.exists()
    assert "sample_home_report_parsed.json" in result.parsed_path.name


def test_run_raises_on_invalid_opinion(
    fake_pdf, tmp_path, local_env
):
    # Opinion with score_corrections empty but parsed has contradictions
    bad = _opinion_json()
    bad["score_corrections"] = []
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    out = tmp_path / "r.html"

    with pytest.raises(OpinionValidationError) as exc_info:
        run(fake_pdf, opinion_path=bad_path, out_html=out, parsed=_parsed_json())
    assert any("score_corrections" in e for e in exc_info.value.errors)
    # HTML should NOT have been produced
    assert not out.exists()


def test_run_skip_storage(fake_pdf, opinion_path, tmp_path, local_env):
    out = tmp_path / "preview.html"
    result = run(
        fake_pdf, opinion_path=opinion_path, out_html=out,
        parsed=_parsed_json(), skip_storage=True,
    )
    assert out.exists()
    assert result.property_id == ""
