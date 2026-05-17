"""ViewingStrategy — viewing-day operational playbook for one property.

Complements (does not replace) SurveyorOpinion:
- SurveyorOpinion answers "should you buy this?" (analysis layer)
- ViewingStrategy answers "what do you DO during the viewing?" (action layer)

Contract enforced by validate(); SKILL.md generates JSON, CLI validates.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


# Allowed enum values
VALID_OFFER_LABELS = {"opening", "target", "walk_away"}
VALID_CHECKLIST_CATS = {"structural", "comfort", "neighbours", "documents"}


@dataclass
class QABilingual:
    """One question to ask the agent, in 中 + EN with rationale."""
    chinese: str
    english: str
    why: str  # ≤120 字, why this question matters

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "QABilingual":
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in allowed})


@dataclass
class OfferTier:
    """One tier in the 3-tier offer plan."""
    label: str          # 'opening' | 'target' | 'walk_away'
    amount: float
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OfferTier":
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in allowed})


@dataclass
class ChecklistItem:
    """One on-site checklist item with category bucket."""
    category: str       # 'structural' | 'comfort' | 'neighbours' | 'documents'
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChecklistItem":
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in allowed})


@dataclass
class ViewingStrategy:
    """Action-oriented viewing-day playbook for one property."""
    headline_risks:        list[str]            = field(default_factory=list)
    bilingual_qa:          list[QABilingual]    = field(default_factory=list)
    communication_tactics: list[str]            = field(default_factory=list)
    offer_tiers:           list[OfferTier]      = field(default_factory=list)
    on_site_checklist:     list[ChecklistItem]  = field(default_factory=list)

    # ---- Serialisation ----

    def to_dict(self) -> dict[str, Any]:
        return {
            "headline_risks": list(self.headline_risks),
            "bilingual_qa": [q.to_dict() for q in self.bilingual_qa],
            "communication_tactics": list(self.communication_tactics),
            "offer_tiers": [t.to_dict() for t in self.offer_tiers],
            "on_site_checklist": [c.to_dict() for c in self.on_site_checklist],
        }

    def to_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, **kwargs)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ViewingStrategy":
        return cls(
            headline_risks=list(d.get("headline_risks") or []),
            bilingual_qa=[QABilingual.from_dict(x) for x in (d.get("bilingual_qa") or [])],
            communication_tactics=list(d.get("communication_tactics") or []),
            offer_tiers=[OfferTier.from_dict(x) for x in (d.get("offer_tiers") or [])],
            on_site_checklist=[ChecklistItem.from_dict(x) for x in (d.get("on_site_checklist") or [])],
        )

    @classmethod
    def from_json_file(cls, path: str) -> "ViewingStrategy":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    # ---- Validation ----

    def validate(self) -> list[str]:
        """Return list of human-readable error strings; empty = OK."""
        errs: list[str] = []

        if not (1 <= len(self.headline_risks) <= 5):
            errs.append(f"headline_risks 必须 1-5 条（当前 {len(self.headline_risks)}）")
        for i, r in enumerate(self.headline_risks):
            if not r or not r.strip():
                errs.append(f"headline_risks[{i}] 为空")

        if len(self.bilingual_qa) < 3:
            errs.append(f"bilingual_qa 至少 3 条（当前 {len(self.bilingual_qa)}）")
        if len(self.bilingual_qa) > 12:
            errs.append(f"bilingual_qa 不要超过 12 条避免疲劳（当前 {len(self.bilingual_qa)}）")
        for i, qa in enumerate(self.bilingual_qa):
            if not (qa.chinese and qa.english and qa.why):
                errs.append(f"bilingual_qa[{i}] 三字段都必须非空（chinese/english/why）")

        if len(self.offer_tiers) != 3:
            errs.append(f"offer_tiers 必须恰好 3 档（当前 {len(self.offer_tiers)}）")
        labels = [t.label for t in self.offer_tiers]
        for t in self.offer_tiers:
            if t.label not in VALID_OFFER_LABELS:
                errs.append(f"offer_tier label {t.label!r} 必须是 {sorted(VALID_OFFER_LABELS)} 之一")
            if t.amount <= 0:
                errs.append(f"offer_tier {t.label!r} amount 必须 >0（当前 {t.amount}）")
            if not t.rationale or not t.rationale.strip():
                errs.append(f"offer_tier {t.label!r} rationale 为空")
        if set(labels) == VALID_OFFER_LABELS:
            opening = next(t.amount for t in self.offer_tiers if t.label == "opening")
            target = next(t.amount for t in self.offer_tiers if t.label == "target")
            walk = next(t.amount for t in self.offer_tiers if t.label == "walk_away")
            if not opening <= target <= walk:
                errs.append(
                    f"offer 三档金额必须递增 opening({opening}) ≤ target({target}) ≤ walk_away({walk})"
                )

        if not (1 <= len(self.communication_tactics) <= 8):
            errs.append(f"communication_tactics 必须 1-8 条（当前 {len(self.communication_tactics)}）")

        if len(self.on_site_checklist) < 5:
            errs.append(f"on_site_checklist 至少 5 条（当前 {len(self.on_site_checklist)}）")
        for i, item in enumerate(self.on_site_checklist):
            if item.category not in VALID_CHECKLIST_CATS:
                errs.append(
                    f"on_site_checklist[{i}].category={item.category!r} 必须是 "
                    f"{sorted(VALID_CHECKLIST_CATS)} 之一"
                )
            if not item.text or not item.text.strip():
                errs.append(f"on_site_checklist[{i}].text 为空")

        return errs


# ---------- CLI ----------

def _schema_for_llm() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["headline_risks", "bilingual_qa", "communication_tactics",
                     "offer_tiers", "on_site_checklist"],
        "properties": {
            "headline_risks": {
                "type": "array",
                "description": "进门前先记住的 1-5 件事（≤80 字短句）",
                "items": {"type": "string"},
                "minItems": 1, "maxItems": 5,
            },
            "bilingual_qa": {
                "type": "array",
                "description": "中介问答清单 (≥3, ≤12)。每条含 chinese / english / why",
                "items": {"$ref": "#/$defs/qa"},
                "minItems": 3, "maxItems": 12,
            },
            "communication_tactics": {
                "type": "array",
                "description": "1-8 条谈判与现场沟通策略（buyer 姿态、何时透露兴趣、何时沉默）",
                "items": {"type": "string"},
                "minItems": 1, "maxItems": 8,
            },
            "offer_tiers": {
                "type": "array",
                "description": "出价 3 档：opening / target / walk_away（金额递增）",
                "items": {"$ref": "#/$defs/offer_tier"},
                "minItems": 3, "maxItems": 3,
            },
            "on_site_checklist": {
                "type": "array",
                "description": "看房现场检查清单 ≥5。category 必须是 structural/comfort/neighbours/documents",
                "items": {"$ref": "#/$defs/checklist_item"},
                "minItems": 5,
            },
        },
        "$defs": {
            "qa": {
                "type": "object",
                "required": ["chinese", "english", "why"],
                "properties": {
                    "chinese": {"type": "string", "description": "中文问题"},
                    "english": {"type": "string", "description": "英文问法"},
                    "why": {"type": "string", "description": "≤120 字, 为什么问这个"},
                },
            },
            "offer_tier": {
                "type": "object",
                "required": ["label", "amount", "rationale"],
                "properties": {
                    "label": {"enum": ["opening", "target", "walk_away"]},
                    "amount": {"type": "number", "exclusiveMinimum": 0},
                    "rationale": {"type": "string"},
                },
            },
            "checklist_item": {
                "type": "object",
                "required": ["category", "text"],
                "properties": {
                    "category": {"enum": ["structural", "comfort", "neighbours", "documents"]},
                    "text": {"type": "string"},
                },
            },
        },
    }


def _cli() -> int:
    ap = argparse.ArgumentParser(
        prog="python -m property_assistant.analysis.viewing_strategy",
        description="Validate a ViewingStrategy JSON file.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="Validate strategy JSON")
    v.add_argument("--strategy", required=True, help="Path to strategy JSON")
    sub.add_parser("schema", help="Print JSON schema for SKILL.md prompt context")
    args = ap.parse_args()

    if args.cmd == "validate":
        try:
            strategy = ViewingStrategy.from_json_file(args.strategy)
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            print(f"failed to load strategy JSON: {exc}", file=sys.stderr)
            return 2
        errs = strategy.validate()
        if errs:
            print("\n".join(f"- {e}" for e in errs), file=sys.stderr)
            return 1
        print("ok")
        return 0

    if args.cmd == "schema":
        print(json.dumps(_schema_for_llm(), ensure_ascii=False, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(_cli())
