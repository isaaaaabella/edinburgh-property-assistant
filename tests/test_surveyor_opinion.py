"""Tests for SurveyorOpinion validate() — every rule has fail + pass case."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from property_assistant.analysis.surveyor_opinion import (
    Finding,
    SurveyorOpinion,
    _schema_for_llm,
)


def F(**kwargs) -> Finding:
    """Quick Finding builder with sensible defaults."""
    kwargs.setdefault("kind", "judgment")
    kwargs.setdefault("text", "stub text")
    return Finding(**kwargs)


def _full_valid_opinion(parsed: dict | None = None) -> SurveyorOpinion:
    """Build a SurveyorOpinion that passes all checks for empty parsed."""
    return SurveyorOpinion(
        overall_positioning=[F(text="1960s 公寓，2/2 楼")],
        score_corrections=[],  # no contradictions to cover
        real_concerns=[F(text="Cat 2 数量偏多")],
        valuation_judgment=[F(text="HR 估价偏保守")],
        offer_direction=[F(text="建议挂牌价附近出")],
        viewing_priorities=[
            F(text="问 Factor 月费"),
            F(text="问最近一次屋顶大修"),
            F(text="问 Closing Date"),
        ],
    )


# ---------- Pass cases ----------

def test_valid_opinion_with_no_contradictions_passes():
    op = _full_valid_opinion()
    assert op.validate({"derived": {"cat_notes_contradictions": []}}) == []


def test_valid_opinion_with_contradictions_covered_passes():
    parsed = {"derived": {"cat_notes_contradictions": [
        {"row": "Rainwater fittings", "page": 17},
    ]}}
    op = _full_valid_opinion()
    op.score_corrections = [
        F(text="Rainwater Cat 2 但 notes 反", contradiction_id="Rainwater fittings_p17",
          score_delta=0.5),
    ]
    assert op.validate(parsed) == []


# ---------- Fail cases (one per rule) ----------

def test_fail_when_required_section_empty():
    op = _full_valid_opinion()
    op.overall_positioning = []
    errs = op.validate()
    assert any("overall_positioning 不能为空" in e for e in errs)


def test_fail_when_contradiction_uncovered():
    parsed = {"derived": {"cat_notes_contradictions": [
        {"row": "Roof coverings", "page": 14},
    ]}}
    op = _full_valid_opinion()
    op.score_corrections = []  # missing
    errs = op.validate(parsed)
    assert any("score_corrections 为空" in e for e in errs)


def test_fail_when_contradiction_id_mismatched():
    parsed = {"derived": {"cat_notes_contradictions": [
        {"row": "Roof coverings", "page": 14},
    ]}}
    op = _full_valid_opinion()
    op.score_corrections = [F(contradiction_id="Other thing_p99", score_delta=-1)]
    errs = op.validate(parsed)
    assert any("未覆盖矛盾项: Roof coverings_p14" in e for e in errs)


def test_fail_when_real_concerns_exceed_five():
    op = _full_valid_opinion()
    op.real_concerns = [F(text=f"c{i}") for i in range(6)]
    errs = op.validate()
    assert any("real_concerns 超过 5 条" in e for e in errs)


def test_fail_when_viewing_priorities_empty():
    op = _full_valid_opinion()
    op.viewing_priorities = []
    errs = op.validate()
    assert any("viewing_priorities 必须 1-5 条" in e for e in errs)


def test_fail_when_viewing_priorities_too_many():
    op = _full_valid_opinion()
    op.viewing_priorities = [F(text=f"q{i}") for i in range(6)]
    errs = op.validate()
    assert any("viewing_priorities 必须 1-5 条" in e for e in errs)


def test_fail_when_fact_missing_evidence_page():
    op = _full_valid_opinion()
    op.overall_positioning = [F(kind="fact", text="some fact")]
    errs = op.validate()
    assert any("kind=fact 但缺 evidence_page" in e for e in errs)


def test_fail_when_too_few_judgments():
    op = _full_valid_opinion()
    # Make every finding a fact with evidence_page → 0 judgments
    for name in ["overall_positioning", "real_concerns", "valuation_judgment",
                 "offer_direction", "viewing_priorities"]:
        setattr(op, name, [F(kind="fact", text="x", evidence_page=1)])
    errs = op.validate()
    assert any("judgment 类 Finding 不足 3 条" in e for e in errs)


def test_fail_when_invalid_kind():
    op = _full_valid_opinion()
    op.overall_positioning = [Finding(kind="opinion", text="bad kind")]
    errs = op.validate()
    assert any("不合法" in e for e in errs)


def test_fail_when_empty_text():
    op = _full_valid_opinion()
    op.overall_positioning = [F(text="")]
    errs = op.validate()
    assert any("text 为空" in e for e in errs)


# ---------- Round-trip + factory ----------

def test_roundtrip_to_dict_from_dict():
    parsed = {"derived": {"cat_notes_contradictions": [
        {"row": "x", "page": 1},
    ]}}
    op = _full_valid_opinion()
    op.score_corrections = [F(contradiction_id="x_p1", score_delta=0.5)]
    d = op.to_dict()
    op2 = SurveyorOpinion.from_dict(d)
    assert op2.validate(parsed) == []
    assert op2.to_dict() == d


def test_from_dict_ignores_unknown_finding_keys():
    op = SurveyorOpinion.from_dict({
        "overall_positioning": [{"kind": "judgment", "text": "x", "future_field": "ignore"}],
    })
    assert op.overall_positioning[0].text == "x"


def test_all_findings_by_kind():
    op = SurveyorOpinion(
        overall_positioning=[F(kind="fact", text="a", evidence_page=1)],
        valuation_judgment=[F(kind="judgment", text="b"),
                            F(kind="assumption", text="c")],
    )
    assert [f.text for f in op.all_findings_by_kind("fact")] == ["a"]
    assert [f.text for f in op.all_findings_by_kind("judgment")] == ["b"]
    assert [f.text for f in op.all_findings_by_kind("assumption")] == ["c"]


def test_derive_tldr_uses_positioning_and_offer():
    op = SurveyorOpinion(
        overall_positioning=[F(text="1960s 公寓，2/2 楼")],
        offer_direction=[F(text="±5% 出价")],
        valuation_judgment=[F(text="不评论")],
    )
    assert op.derive_tldr() == "1960s 公寓，2/2 楼。±5% 出价"


def test_derive_tldr_explicit_overrides_derivation():
    op = SurveyorOpinion(
        overall_positioning=[F(text="X")],
        offer_direction=[F(text="Y")],
    )
    assert op.derive_tldr(explicit="hand-written") == "hand-written"


def test_derive_tldr_falls_back_to_single_section():
    op_pos_only = SurveyorOpinion(overall_positioning=[F(text="只有定位")])
    assert op_pos_only.derive_tldr() == "只有定位"
    op_off_only = SurveyorOpinion(offer_direction=[F(text="只有出价")])
    assert op_off_only.derive_tldr() == "只有出价"


def test_derive_tldr_returns_none_when_empty():
    op = SurveyorOpinion()
    assert op.derive_tldr() is None


def test_schema_for_llm_has_six_required_sections():
    sch = _schema_for_llm()
    assert set(sch["required"]) == {
        "overall_positioning", "score_corrections", "real_concerns",
        "valuation_judgment", "offer_direction", "viewing_priorities",
    }


# ---------- CLI ----------

def test_cli_validate_returns_zero_on_valid(tmp_path: Path):
    parsed = tmp_path / "parsed.json"
    parsed.write_text(json.dumps({"derived": {"cat_notes_contradictions": []}}))
    op_path = tmp_path / "opinion.json"
    op_path.write_text(json.dumps(_full_valid_opinion().to_dict()))

    import subprocess, sys
    r = subprocess.run(
        [sys.executable, "-m", "property_assistant.analysis.surveyor_opinion",
         "validate", "--parsed", str(parsed), "--opinion", str(op_path)],
        capture_output=True, text=True, cwd="/Users/duoduoyang/.claude",
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ok"


def test_cli_validate_returns_one_with_errors_on_stderr(tmp_path: Path):
    parsed = tmp_path / "parsed.json"
    parsed.write_text(json.dumps({"derived": {"cat_notes_contradictions": [
        {"row": "Roof", "page": 14},
    ]}}))
    op_path = tmp_path / "opinion.json"
    bad = _full_valid_opinion()  # score_corrections empty but contradictions present
    op_path.write_text(json.dumps(bad.to_dict()))

    import subprocess, sys
    r = subprocess.run(
        [sys.executable, "-m", "property_assistant.analysis.surveyor_opinion",
         "validate", "--parsed", str(parsed), "--opinion", str(op_path)],
        capture_output=True, text=True, cwd="/Users/duoduoyang/.claude",
    )
    assert r.returncode == 1
    assert "score_corrections" in r.stderr
