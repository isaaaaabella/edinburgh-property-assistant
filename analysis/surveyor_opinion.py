"""SurveyorOpinion — structured surveyor judgment for a Home Report.

This is the canonical contract the LLM must produce. It is enforced by
`validate()` before rendering — invalid opinion never reaches HTML.

Six required sections (per `feedback_surveyor_opinion.md`):
  1. 整体定位         (overall_positioning)
  2. 评分校正         (score_corrections)        ← must cover all cat_notes_contradictions
  3. 真正的关注点     (real_concerns, ≤5)
  4. 估值判断         (valuation_judgment)
  5. 出价方向         (offer_direction)
  6. 看房当日 3 个最关键问题  (viewing_priorities, 1-5 条)

Each section is a list of Findings. A Finding has a kind
(fact | judgment | assumption), which drives the layered-cards render.

CLI: `python -m property_assistant.analysis.surveyor_opinion validate \\
        --parsed <parsed.json> --opinion <opinion.json>`
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


FindingKind = Literal["fact", "judgment", "assumption"]
VALID_KINDS = {"fact", "judgment", "assumption"}


@dataclass
class Finding:
    """One atomic claim in the surveyor opinion.

    A rich Finding has 4 layers (any/all optional except text):
      text          — punchy headline (≤80 chars). The claim itself.
      rationale     — why this matters: comparison, mechanism, domain knowledge
                      (2-4 sentences, surveyor voice — drives the insight feel)
      quote         — verbatim excerpt from the PDF that triggers this Finding
      evidence_page — the page the fact/quote came from
    """

    kind: str                            # "fact" | "judgment" | "assumption"
    text: str                            # ≤80 chars headline
    rationale: str | None = None         # longer surveyor reasoning, 2-4 sentences
    quote: str | None = None             # verbatim PDF excerpt
    evidence_page: int | None = None     # required when kind == "fact"
    contradiction_id: str | None = None  # references derived.cat_notes_contradictions
    score_delta: float | None = None     # signed score adjustment (used in 评分校正)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Finding":
        # Tolerate unknown keys
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in allowed})


_SECTION_NAMES = [
    "overall_positioning",
    "score_corrections",
    "real_concerns",
    "valuation_judgment",
    "offer_direction",
    "viewing_priorities",
    "additional_thoughts",
]


@dataclass
class SurveyorOpinion:
    """Structured surveyor opinion.

    The first 6 sections are the strict structured opinion. `additional_thoughts`
    is a freeform 7th section for stray observations / historical experience /
    side-notes that don't fit the strict structure. Always optional.
    """

    overall_positioning: list[Finding] = field(default_factory=list)
    score_corrections:   list[Finding] = field(default_factory=list)
    real_concerns:       list[Finding] = field(default_factory=list)
    valuation_judgment:  list[Finding] = field(default_factory=list)
    offer_direction:     list[Finding] = field(default_factory=list)
    viewing_priorities:  list[Finding] = field(default_factory=list)
    additional_thoughts: list[Finding] = field(default_factory=list)

    def all_findings(self) -> list[Finding]:
        out: list[Finding] = []
        for name in _SECTION_NAMES:
            out.extend(getattr(self, name))
        return out

    def all_findings_by_kind(self, kind: str) -> list[Finding]:
        return [f for f in self.all_findings() if f.kind == kind]

    # ---- Serialisation ----

    def to_dict(self) -> dict[str, Any]:
        return {name: [f.to_dict() for f in getattr(self, name)] for name in _SECTION_NAMES}

    def to_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, **kwargs)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SurveyorOpinion":
        kwargs: dict[str, list[Finding]] = {}
        for name in _SECTION_NAMES:
            raw_list = data.get(name) or []
            kwargs[name] = [Finding.from_dict(d) for d in raw_list]
        return cls(**kwargs)

    @classmethod
    def from_json_file(cls, path: str) -> "SurveyorOpinion":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    # ---- Validation ----

    def validate(self, parsed_data: dict[str, Any] | None = None) -> list[str]:
        """Return list of human-readable error strings. Empty = OK."""
        errs: list[str] = []
        parsed_data = parsed_data or {}

        # 1. Kind sanity check
        for name in _SECTION_NAMES:
            for i, f in enumerate(getattr(self, name)):
                if f.kind not in VALID_KINDS:
                    errs.append(
                        f"{name}[{i}].kind={f.kind!r} 不合法，必须是 fact/judgment/assumption"
                    )
                if not f.text or not f.text.strip():
                    errs.append(f"{name}[{i}].text 为空")

        # 2. Always-non-empty sections (every property has these judgments)
        for name in ["overall_positioning", "valuation_judgment", "offer_direction"]:
            if not getattr(self, name):
                errs.append(f"{name} 不能为空")

        # 3. Conditional: cat_notes_contradictions must be referenced
        contradictions = ((parsed_data.get("derived") or {})
                          .get("cat_notes_contradictions") or [])
        if contradictions:
            if not self.score_corrections:
                errs.append(
                    f"检测到 {len(contradictions)} 个 cat_notes_contradictions 但 "
                    f"score_corrections 为空"
                )
            referenced = {
                f.contradiction_id
                for f in self.score_corrections
                if f.contradiction_id
            }
            for c in contradictions:
                cid = f"{c.get('row', '?')}_p{c.get('page', '?')}"
                if cid not in referenced:
                    errs.append(
                        f"score_corrections 未覆盖矛盾项: {cid}"
                        f"（在 Finding.contradiction_id 字段写入 {cid!r}）"
                    )

        # 4. Real concerns ≤ 5
        if len(self.real_concerns) > 5:
            errs.append(f"real_concerns 超过 5 条（{len(self.real_concerns)}）")

        # 5. Viewing priorities 1-5
        if not (1 <= len(self.viewing_priorities) <= 5):
            errs.append(
                f"viewing_priorities 必须 1-5 条（当前 {len(self.viewing_priorities)}）"
            )

        # 6. Facts must have evidence_page (additional_thoughts exempted — it's freeform)
        for name in ["overall_positioning", "score_corrections", "real_concerns",
                     "valuation_judgment"]:
            for i, f in enumerate(getattr(self, name)):
                if f.kind == "fact" and f.evidence_page is None:
                    errs.append(
                        f"{name}[{i}] kind=fact 但缺 evidence_page: {f.text[:30]}"
                    )

        # 8. additional_thoughts cap (no minimum; just don't overflow)
        if len(self.additional_thoughts) > 8:
            errs.append(
                f"additional_thoughts 不要超过 8 条（当前 {len(self.additional_thoughts)}）"
            )

        # 7. At least 3 judgments overall (otherwise it's just mechanical extraction)
        judgments = self.all_findings_by_kind("judgment")
        if len(judgments) < 3:
            errs.append(
                f"整体 judgment 类 Finding 不足 3 条（当前 {len(judgments)}）—— "
                f"评估师意见应包含足够判断，而非纯事实堆砌"
            )

        return errs


# ---------- CLI ----------

def _cli() -> int:
    ap = argparse.ArgumentParser(
        prog="python -m property_assistant.analysis.surveyor_opinion",
        description="Validate a SurveyorOpinion JSON file against a parsed Home Report.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    val = sub.add_parser("validate", help="Validate opinion JSON against parsed JSON")
    val.add_argument("--parsed", required=True, help="Path to parsed Home Report JSON")
    val.add_argument("--opinion", required=True, help="Path to opinion JSON")

    sub.add_parser("schema", help="Print the JSON schema (for LLM prompt context)")

    args = ap.parse_args()

    if args.cmd == "validate":
        try:
            with open(args.parsed, encoding="utf-8") as f:
                parsed = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"failed to load parsed JSON: {exc}", file=sys.stderr)
            return 2
        try:
            opinion = SurveyorOpinion.from_json_file(args.opinion)
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            print(f"failed to load opinion JSON: {exc}", file=sys.stderr)
            return 2
        errs = opinion.validate(parsed)
        if errs:
            print("\n".join(f"- {e}" for e in errs), file=sys.stderr)
            return 1
        print("ok")
        return 0

    if args.cmd == "schema":
        print(json.dumps(_schema_for_llm(), ensure_ascii=False, indent=2))
        return 0

    return 2


def _schema_for_llm() -> dict[str, Any]:
    """Compact schema description for embedding in SKILL.md prompts."""
    return {
        "type": "object",
        "required": [n for n in _SECTION_NAMES if n != "additional_thoughts"],
        "properties": {
            "overall_positioning": {
                "type": "array",
                "description": "整体定位：1-3 条 Finding，最重要的市场/类型/年代定位",
                "items": {"$ref": "#/$defs/finding"},
                "minItems": 1,
            },
            "score_corrections": {
                "type": "array",
                "description": (
                    "评分校正：若 parsed.derived.cat_notes_contradictions 非空，"
                    "每条矛盾必须有一条 Finding 引用其 contradiction_id "
                    "（格式 '{row}_p{page}'）并给 score_delta"
                ),
                "items": {"$ref": "#/$defs/finding"},
            },
            "real_concerns": {
                "type": "array",
                "description": "真正的关注点：≤5 条最值得警惕的问题",
                "items": {"$ref": "#/$defs/finding"},
                "maxItems": 5,
            },
            "valuation_judgment": {
                "type": "array",
                "description": "估值判断：1-3 条关于 HR 估价是否合理、市场对标",
                "items": {"$ref": "#/$defs/finding"},
                "minItems": 1,
            },
            "offer_direction": {
                "type": "array",
                "description": "出价方向：1-3 条建议（保守/积极/避免）",
                "items": {"$ref": "#/$defs/finding"},
                "minItems": 1,
            },
            "viewing_priorities": {
                "type": "array",
                "description": "看房当日 3 个最关键问题（1-5 条都接受）",
                "items": {"$ref": "#/$defs/finding"},
                "minItems": 1,
                "maxItems": 5,
            },
            "additional_thoughts": {
                "type": "array",
                "description": (
                    "可选第 7 段：评估师的随手观察 / 历史经验 / 不归类到 6 段里的"
                    "边角洞察。0-8 条。比如 '听说同一栋楼之前出过水管纠纷' / "
                    "'这个 postcode 的 Pre-1919 房子近期成交多在春季'。"
                    "语气可以更松散随性，但仍然要 grounded。"
                ),
                "items": {"$ref": "#/$defs/finding"},
                "maxItems": 8,
            },
        },
        "$defs": {
            "finding": {
                "type": "object",
                "required": ["kind", "text"],
                "properties": {
                    "kind": {"enum": ["fact", "judgment", "assumption"]},
                    "text": {"type": "string",
                             "description": "≤80 字 punchy 断言"},
                    "rationale": {
                        "type": ["string", "null"],
                        "description": "2-4 句分析师 voice 的理由：对比/机制/苏格兰房产知识",
                    },
                    "quote": {
                        "type": ["string", "null"],
                        "description": "PDF 原文摘录（引号会自动加）",
                    },
                    "evidence_page": {
                        "type": ["integer", "null"],
                        "description": "PDF 页码；kind=fact 时必填",
                    },
                    "contradiction_id": {
                        "type": ["string", "null"],
                        "description": "'{row}_p{page}' 格式",
                    },
                    "score_delta": {
                        "type": ["number", "null"],
                        "description": "评分校正：建议加减的分数（正数=加分）",
                    },
                },
            }
        },
    }


if __name__ == "__main__":
    sys.exit(_cli())
