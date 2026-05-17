"""Tests for ViewingStrategy validate()."""

from __future__ import annotations

from property_assistant.analysis.viewing_strategy import (
    ChecklistItem,
    OfferTier,
    QABilingual,
    ViewingStrategy,
)


def _full_strategy() -> ViewingStrategy:
    return ViewingStrategy(
        headline_risks=[
            "确认无 factor 的 MRL 协调风险",
            "看顶层是否有斜屋顶压抑空间",
            "问最近一次共有维修日期",
        ],
        bilingual_qa=[
            QABilingual(chinese="Factor 月费包含什么？",
                        english="What does the monthly factor fee cover?",
                        why="确认是否含烟囱清扫和外墙维护"),
            QABilingual(chinese="最近 5 年是否做过屋顶或外墙工程？",
                        english="Any roof or external wall works in the past 5 years?",
                        why="预测下次大修时间窗"),
            QABilingual(chinese="Closing date 有计划吗？",
                        english="Is there a closing date set?",
                        why="判断卖方紧迫程度"),
        ],
        communication_tactics=[
            "进门不要主动赞美，保持冷静",
            "对 Factor 等具体细节追问",
            "结束时只说'I'll come back to you'，不承诺",
        ],
        offer_tiers=[
            OfferTier(label="opening", amount=315000.0, rationale="HR 价下浮 1.5%，测下调空间"),
            OfferTier(label="target",  amount=320000.0, rationale="HR 价持平，公允出价"),
            OfferTier(label="walk_away", amount=325000.0, rationale="HR 价上浮 1.5%，过此放弃"),
        ],
        on_site_checklist=[
            ChecklistItem(category="structural", text="检查外墙石材风化深度"),
            ChecklistItem(category="structural", text="看烟囱有无歪斜"),
            ChecklistItem(category="comfort", text="看主卧朝向（南/北）"),
            ChecklistItem(category="neighbours", text="楼下是否商铺或短租"),
            ChecklistItem(category="documents", text="索要近 5 年共有维修发票"),
        ],
    )


# ---- pass cases ----

def test_full_strategy_passes():
    assert _full_strategy().validate() == []


def test_roundtrip_dict():
    s = _full_strategy()
    s2 = ViewingStrategy.from_dict(s.to_dict())
    assert s2.validate() == []
    assert s2.to_dict() == s.to_dict()


# ---- fail cases (one per rule) ----

def test_fail_headline_risks_empty():
    s = _full_strategy()
    s.headline_risks = []
    assert any("headline_risks" in e for e in s.validate())


def test_fail_headline_risks_too_many():
    s = _full_strategy()
    s.headline_risks = ["a"] * 6
    assert any("headline_risks" in e for e in s.validate())


def test_fail_bilingual_qa_too_few():
    s = _full_strategy()
    s.bilingual_qa = s.bilingual_qa[:2]
    assert any("bilingual_qa 至少 3" in e for e in s.validate())


def test_fail_bilingual_qa_missing_field():
    s = _full_strategy()
    s.bilingual_qa[0] = QABilingual(chinese="x", english="x", why="")
    assert any("三字段都必须非空" in e for e in s.validate())


def test_fail_offer_tiers_not_three():
    s = _full_strategy()
    s.offer_tiers = s.offer_tiers[:2]
    assert any("offer_tiers 必须恰好 3" in e for e in s.validate())


def test_fail_offer_tier_unknown_label():
    s = _full_strategy()
    s.offer_tiers[0] = OfferTier(label="aggressive", amount=300000, rationale="x")
    assert any("offer_tier label 'aggressive'" in e for e in s.validate())


def test_fail_offer_amounts_not_monotonic():
    s = _full_strategy()
    s.offer_tiers = [
        OfferTier(label="opening", amount=330000, rationale="x"),
        OfferTier(label="target", amount=320000, rationale="x"),
        OfferTier(label="walk_away", amount=325000, rationale="x"),
    ]
    assert any("递增" in e for e in s.validate())


def test_fail_checklist_too_few():
    s = _full_strategy()
    s.on_site_checklist = s.on_site_checklist[:4]
    assert any("on_site_checklist 至少 5" in e for e in s.validate())


def test_fail_checklist_bad_category():
    s = _full_strategy()
    s.on_site_checklist[0] = ChecklistItem(category="kitchen", text="x")
    assert any("'kitchen'" in e for e in s.validate())


# ---- CLI ----

def test_cli_validate_returns_zero(tmp_path):
    import subprocess, sys, json
    path = tmp_path / "s.json"
    path.write_text(json.dumps(_full_strategy().to_dict()), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-m", "property_assistant.analysis.viewing_strategy",
         "validate", "--strategy", str(path)],
        capture_output=True, text=True, cwd="/Users/duoduoyang/.claude",
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ok"


def test_cli_validate_returns_one_on_error(tmp_path):
    import subprocess, sys, json
    bad = _full_strategy()
    bad.offer_tiers = bad.offer_tiers[:1]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad.to_dict()), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-m", "property_assistant.analysis.viewing_strategy",
         "validate", "--strategy", str(path)],
        capture_output=True, text=True, cwd="/Users/duoduoyang/.claude",
    )
    assert r.returncode == 1
    assert "offer_tiers" in r.stderr
