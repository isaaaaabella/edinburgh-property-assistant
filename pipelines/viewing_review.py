"""Viewing review pipeline — post-viewing reflection + gap analysis.

Reads all viewed properties from storage, surfaces:
- Score vs subjective feeling gaps (high score / negative feeling, etc.)
- Partner disagreements (self vs partner feeling diverge)
- Status overview (待看 / 已看 / 感兴趣 / 已出价 / 已购入 / 已放弃)

Pure terminal output (no HTML); the orchestrator wraps it for /property review.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from property_assistant.analysis.scoring import compute
from property_assistant.core.property_record import PropertyRecord
from property_assistant.storage import get_storage


# Status select values from Notion DB (see SCHEMA_AUDIT.md).
# "Viewed" = has been visited; we treat any status that implies post-viewing.
_VIEWED_STATUSES = {"👀 已看", "⭐ 感兴趣", "💰 已出价", "✅ 已购入", "❌ 已放弃"}


@dataclass
class PropertyReview:
    address: str
    storage_id: str | None
    score: float
    recommendation: str
    status: str | None
    self_feeling: str | None
    partner_feeling: str | None
    self_score: float | None
    partner_score: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Gap:
    kind: str           # 'score_vs_feeling' | 'partner_disagreement'
    address: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReviewResult:
    viewed_count: int
    properties: list[PropertyReview] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    status_counts: dict[str, int] = field(default_factory=dict)
    shortlist: list[str] = field(default_factory=list)   # addresses where status="⭐ 感兴趣"

    def to_dict(self) -> dict[str, Any]:
        return {
            "viewed_count": self.viewed_count,
            "properties": [p.to_dict() for p in self.properties],
            "gaps": [g.to_dict() for g in self.gaps],
            "status_counts": dict(self.status_counts),
            "shortlist": list(self.shortlist),
        }


# ---------- Sentiment heuristic ----------

_POSITIVE_KEYWORDS = [
    "好", "喜欢", "明亮", "宽敞", "舒服", "干净", "采光",
    "love", "great", "spacious", "bright", "clean", "perfect", "comfortable",
]
_NEGATIVE_KEYWORDS = [
    "暗", "小", "压抑", "潮湿", "脏", "吵", "不喜欢", "失望", "差", "霉",
    "dark", "small", "cramped", "damp", "noisy", "dirty", "smell", "disappointing",
    "concerning", "issue", "problem",
]


def _sentiment(text: str | None) -> int:
    """Return +1 (positive), -1 (negative), or 0 (neutral / no clear signal)."""
    if not text:
        return 0
    t = text.lower()
    pos = sum(1 for kw in _POSITIVE_KEYWORDS if kw in t)
    neg = sum(1 for kw in _NEGATIVE_KEYWORDS if kw in t)
    if pos > neg + 1:
        return 1
    if neg > pos + 1:
        return -1
    return 0


# ---------- Main pipeline ----------

def run(
    *,
    only_shortlist: bool = False,
    records: list[PropertyRecord] | None = None,
) -> ReviewResult:
    """Survey viewed properties and surface gaps.

    Args:
        only_shortlist: restrict to status="⭐ 感兴趣"
        records: pre-loaded list (for testing); else loaded from storage
    """
    if records is None:
        storage = get_storage()
        if only_shortlist:
            records = storage.list_by_filter(status="⭐ 感兴趣")
        else:
            # Pull everything; filter to "viewed" downstream
            records = storage.list_by_filter()

    # Filter to actually-viewed (has subjective feeling OR viewed status)
    viewed: list[PropertyRecord] = []
    for r in records:
        if r.status in _VIEWED_STATUSES:
            viewed.append(r)
        elif r.self_feeling or r.partner_feeling:
            viewed.append(r)
        elif r.viewing_date and r.viewing_date <= date.today():
            viewed.append(r)

    if only_shortlist:
        viewed = [r for r in viewed if r.status == "⭐ 感兴趣"]

    result = ReviewResult(viewed_count=len(viewed))

    for r in viewed:
        bd = compute(r)
        review = PropertyReview(
            address=r.address, storage_id=r.storage_id,
            score=bd.total, recommendation=bd.recommendation,
            status=r.status,
            self_feeling=r.self_feeling, partner_feeling=r.partner_feeling,
            self_score=r.self_score, partner_score=r.partner_score,
        )
        result.properties.append(review)

        if r.status:
            result.status_counts[r.status] = result.status_counts.get(r.status, 0) + 1
        if r.status == "⭐ 感兴趣":
            result.shortlist.append(r.address)

        # Gap: high score (≥70) but negative feeling
        self_sent = _sentiment(r.self_feeling)
        partner_sent = _sentiment(r.partner_feeling)
        if bd.total >= 70 and (self_sent == -1 or partner_sent == -1):
            who = "你" if self_sent == -1 else "伴侣"
            result.gaps.append(Gap(
                kind="score_vs_feeling",
                address=r.address,
                description=f"评分高 ({bd.total}/100) 但{who}的感受偏负面 — 评分维度可能漏掉重要因素",
            ))
        elif bd.total < 55 and (self_sent == 1 or partner_sent == 1):
            who = "你" if self_sent == 1 else "伴侣"
            result.gaps.append(Gap(
                kind="score_vs_feeling",
                address=r.address,
                description=f"评分偏低 ({bd.total}/100) 但{who}的感受很正面 — 你可能在重视评分没覆盖的维度",
            ))

        # Gap: partner disagreement
        if self_sent != 0 and partner_sent != 0 and self_sent != partner_sent:
            result.gaps.append(Gap(
                kind="partner_disagreement",
                address=r.address,
                description=f"你 ({['负面', '中性', '正面'][self_sent+1]}) vs 伴侣 ({['负面', '中性', '正面'][partner_sent+1]}) 感受分歧",
            ))
        elif r.self_score and r.partner_score and abs(r.self_score - r.partner_score) >= 2:
            result.gaps.append(Gap(
                kind="partner_disagreement",
                address=r.address,
                description=f"打分差距大：你 {r.self_score} vs 伴侣 {r.partner_score}（差 {abs(r.self_score - r.partner_score):.1f}）",
            ))

    return result


# ---------- CLI ----------

def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="python -m property_assistant.pipelines.viewing_review",
        description="Post-viewing review + gap analysis.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="Run review")
    r.add_argument("--shortlist", action="store_true",
                   help="Only properties with status='⭐ 感兴趣'")
    r.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.cmd != "run":
        return 2

    try:
        result = run(only_shortlist=args.shortlist)
    except Exception as exc:  # noqa: BLE001
        print(f"viewing_review failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0

    print(f"📋 Reviewed {result.viewed_count} viewed properties\n")
    if result.status_counts:
        print("状态分布:")
        for status, count in result.status_counts.items():
            print(f"  {status}: {count}")
        print()

    if result.properties:
        print("房源概览:")
        for p in result.properties:
            line = f"  · {p.address[:40]:40s} {p.score}/100 {p.recommendation}"
            if p.status:
                line += f" · {p.status}"
            print(line)
            if p.self_feeling:
                print(f"      你: {p.self_feeling[:80]}")
            if p.partner_feeling:
                print(f"      伴侣: {p.partner_feeling[:80]}")
        print()

    if result.gaps:
        print(f"⚠️  Gap 分析 ({len(result.gaps)}):")
        for g in result.gaps:
            print(f"  · [{g.kind}] {g.address[:30]}")
            print(f"      {g.description}")
        print()

    if result.shortlist:
        print(f"⭐ Shortlist ({len(result.shortlist)}):")
        for addr in result.shortlist:
            print(f"  · {addr}")
        print()
        print(f"建议下一步: /property compare {' '.join(result.shortlist[:3])}")

    return 0


if __name__ == "__main__":
    sys.exit(_cli())
