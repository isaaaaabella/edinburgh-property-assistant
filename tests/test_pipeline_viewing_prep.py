"""End-to-end test for viewing_prep pipeline using fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from property_assistant.core.property_record import PropertyRecord
from property_assistant.pipelines.viewing_prep import (
    StrategyValidationError,
    run,
)
from property_assistant.tests.test_viewing_strategy import _full_strategy


@pytest.fixture
def local_env(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("PROPERTY_DATA_DIR", str(tmp_path / "data"))


@pytest.fixture
def strategy_path(tmp_path: Path) -> Path:
    p = tmp_path / "strategy.json"
    p.write_text(json.dumps(_full_strategy().to_dict(), ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture
def seeded_record(local_env):
    """Seed a PropertyRecord into LocalJSONStorage."""
    from property_assistant.storage import get_storage
    storage = get_storage()
    rec = PropertyRecord(
        address="4 Chalmers Buildings, Edinburgh, EH3 9QF",
        postcode="EH3 9QF",
        hr_valuation=320000.0,
        bedrooms=2,
        floor_area=68.0,
        floor="Ground ⚠️",
        is_main_door=True,
        building_type="维多利亚Tenement ✅",
        era=1880,
        epc_rating="C",
        epc_score=71,
        cat2_count=2, cat3_count=0, roof_issue=True,
        gas_heating=True,
        factor_status="无 ❌",
    )
    storage.upsert_property(rec)
    return rec


def test_run_generates_html(seeded_record, strategy_path, tmp_path):
    out = tmp_path / "brief.html"
    result = run(
        "Chalmers",
        strategy_path=strategy_path,
        out_html=out,
        viewing_time="2026-05-23 11:00",
        agent_name="ESPC Edinburgh",
    )
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    # Header bits
    assert "Chalmers" in html
    assert "2026-05-23 11:00" in html
    assert "ESPC Edinburgh" in html
    # Strategy sections
    assert "进门前先记住" in html
    assert "出价策略" in html
    assert "中介问答清单" in html
    assert "看房现场检查清单" in html
    assert "谈判与沟通策略" in html
    # Offer tiers labels translated
    assert "开价" in html and "心理价" in html and "止损线" in html
    # Checkboxes
    assert "checkbox" in html


def test_run_fails_if_address_unknown(local_env, strategy_path, tmp_path):
    with pytest.raises(ValueError, match="找不到匹配地址"):
        run("NonExistent Street", strategy_path=strategy_path, out_html=tmp_path / "x.html")


def test_run_fails_on_invalid_strategy(seeded_record, tmp_path):
    bad = _full_strategy()
    bad.offer_tiers = bad.offer_tiers[:1]  # only 1 tier — fails validate
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad.to_dict()), encoding="utf-8")
    with pytest.raises(StrategyValidationError):
        run("Chalmers", strategy_path=path, out_html=tmp_path / "x.html")
    assert not (tmp_path / "x.html").exists()


def test_run_with_opinion_includes_layered_summary(seeded_record, strategy_path, tmp_path):
    from property_assistant.analysis.surveyor_opinion import Finding, SurveyorOpinion
    opinion = SurveyorOpinion(
        overall_positioning=[Finding(kind="judgment", text="经典 Tollcross 主门")],
        score_corrections=[],
        real_concerns=[Finding(kind="judgment", text="MRL 风险")],
        valuation_judgment=[Finding(kind="judgment", text="估价合理")],
        offer_direction=[Finding(kind="judgment", text="挂牌价附近")],
        viewing_priorities=[Finding(kind="judgment", text="问 Factor")],
    )
    op_path = tmp_path / "opinion.json"
    op_path.write_text(json.dumps(opinion.to_dict(), ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "with_opinion.html"
    run("Chalmers", strategy_path=strategy_path, opinion_path=op_path, out_html=out)
    html = out.read_text(encoding="utf-8")
    assert "客观事实" in html  # layered_summary card title
    assert "整体定位" in html  # opinion_detail section title
    assert "TL;DR" in html
