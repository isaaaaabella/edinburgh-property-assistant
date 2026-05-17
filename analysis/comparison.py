"""Side-by-side comparison of 2-5 properties.

Two layers:
- `compute_comparison(records)` — pure mechanical comparison, no LLM. For each
  dimension (HR price, £/m², bedrooms, Cat counts, commute, score, ...) it
  produces a row with formatted values + an optional `winner_idx`.
- `PropertyRanking` — optional LLM-driven 1st/2nd/3rd ranking with one-line
  rationale per property + a bottom-line narrative. Validated by CLI like
  SurveyorOpinion / ViewingStrategy.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from property_assistant.analysis.scoring import ScoreBreakdown, compute
from property_assistant.analysis.surveyor_opinion import Finding
from property_assistant.core.property_record import PropertyRecord


# ---------- Mechanical comparison ----------

@dataclass
class CompareRow:
    label: str                          # e.g. "HR 估价"
    values: list[str]                   # one display string per property
    winner_idx: int | None = None       # None = tie / N/A
    note: str | None = None             # short explanation (e.g. "lower=better")


@dataclass
class Comparison:
    properties: list[PropertyRecord]
    breakdowns: list[ScoreBreakdown]
    rows: list[CompareRow]


def _argmin_idx(values: list[float | None]) -> int | None:
    pairs = [(v, i) for i, v in enumerate(values) if v is not None]
    if len(pairs) < 2:
        return None
    pairs.sort(key=lambda p: p[0])
    if pairs[0][0] == pairs[1][0]:  # tie
        return None
    return pairs[0][1]


def _argmax_idx(values: list[float | None]) -> int | None:
    pairs = [(v, i) for i, v in enumerate(values) if v is not None]
    if len(pairs) < 2:
        return None
    pairs.sort(key=lambda p: p[0], reverse=True)
    if pairs[0][0] == pairs[1][0]:
        return None
    return pairs[0][1]


def _row_from_numeric(
    label: str,
    records: list[PropertyRecord],
    extract: Callable[[PropertyRecord], float | None],
    *,
    format: str = "{:g}",
    higher_is_better: bool = True,
    note: str | None = None,
) -> CompareRow:
    raw = [extract(r) for r in records]
    display = [format.format(v) if v is not None else "—" for v in raw]
    winner = _argmax_idx(raw) if higher_is_better else _argmin_idx(raw)
    return CompareRow(label=label, values=display, winner_idx=winner, note=note)


def _row_from_string(
    label: str,
    records: list[PropertyRecord],
    extract: Callable[[PropertyRecord], Any],
    *,
    join: str = ", ",
) -> CompareRow:
    """For non-orderable fields (building_type, area, school_zone) — no winner."""
    display = []
    for r in records:
        v = extract(r)
        if v is None or v == [] or v == "":
            display.append("—")
        elif isinstance(v, (list, tuple)):
            display.append(join.join(str(x) for x in v) or "—")
        else:
            display.append(str(v))
    return CompareRow(label=label, values=display, winner_idx=None)


def _ppsm(rec: PropertyRecord) -> float | None:
    """£/m² from asking_price or hr_valuation ÷ floor_area."""
    price = rec.asking_price or rec.hr_valuation
    if not price or not rec.floor_area:
        return None
    return round(price / rec.floor_area, 0)


def _cat_distribution(rec: PropertyRecord) -> str | None:
    """Visual summary of Cat 1/2/3 counts: '🟢 17 · 🟡 2 · 🔴 0'."""
    if rec.cat1_count is None and rec.cat2_count is None and rec.cat3_count is None:
        return None
    parts = []
    if rec.cat1_count is not None:
        parts.append(f"🟢 {rec.cat1_count}")
    if rec.cat2_count is not None:
        parts.append(f"🟡 {rec.cat2_count}")
    if rec.cat3_count is not None:
        parts.append(f"🔴 {rec.cat3_count}")
    return " · ".join(parts)


def compute_comparison(records: list[PropertyRecord]) -> Comparison:
    """Build mechanical side-by-side rows + scoring breakdown for each property."""
    if not records:
        raise ValueError("compute_comparison requires at least 1 property")
    breakdowns = [compute(r) for r in records]

    rows: list[CompareRow] = [
        _row_from_numeric("总分", records, lambda r: breakdowns[records.index(r)].total,
                          format="{:g}", higher_is_better=True, note="0-100"),
        _row_from_numeric("HR 估价", records, lambda r: r.hr_valuation,
                          format="£{:,.0f}", higher_is_better=False,
                          note="lower=cheaper for similar"),
        _row_from_numeric("挂牌价", records, lambda r: r.asking_price,
                          format="£{:,.0f}", higher_is_better=False),
        _row_from_numeric("£/m²", records, _ppsm,
                          format="£{:,.0f}", higher_is_better=False),
        _row_from_numeric("卧室", records, lambda r: r.bedrooms,
                          format="{:.0f}", higher_is_better=True),
        _row_from_numeric("面积 m²", records, lambda r: r.floor_area,
                          format="{:g}", higher_is_better=True),
        _row_from_string("楼层", records, lambda r: r.floor),
        _row_from_string("类型", records, lambda r: r.building_type),
        _row_from_numeric("建造年代", records, lambda r: r.era,
                          format="{:.0f}", higher_is_better=False,
                          note="lower=older=often more character"),
        _row_from_string("EPC", records,
                         lambda r: f"{r.epc_rating} ({r.epc_score})"
                                   if r.epc_rating and r.epc_score
                                   else r.epc_rating),
        _row_from_string("状况分布",
                         records,
                         lambda r: _cat_distribution(r)),
        _row_from_numeric("Cat 1 数", records, lambda r: r.cat1_count,
                          format="{:.0f}", higher_is_better=False,
                          note="一般无需处理"),
        _row_from_numeric("Cat 2 数", records, lambda r: r.cat2_count,
                          format="{:.0f}", higher_is_better=False,
                          note="需关注"),
        _row_from_numeric("Cat 3 数", records, lambda r: r.cat3_count,
                          format="{:.0f}", higher_is_better=False,
                          note="需立即处理"),
        _row_from_string("屋顶标记", records,
                         lambda r: ("⚠️ 有" if r.roof_issue else "✓ 无")
                                   if r.roof_issue is not None else None),
        _row_from_string("Gas 供暖", records,
                         lambda r: ("✓" if r.gas_heating else "❌")
                                   if r.gas_heating is not None else None),
        _row_from_string("Factor", records, lambda r: r.factor_status),
        _row_from_numeric("Factor 月费", records, lambda r: r.factor_monthly,
                          format="£{:.0f}", higher_is_better=False),
        _row_from_string("学区", records, lambda r: r.school_zone, join=" · "),
        _row_from_numeric("通勤 (user)", records, lambda r: r.commute_user_min,
                          format="{:.0f} min", higher_is_better=False),
        _row_from_numeric("通勤 (partner)", records, lambda r: r.commute_partner_min,
                          format="{:.0f} min", higher_is_better=False),
    ]
    # Drop rows where everything is "—" (no data across the board)
    rows = [row for row in rows if not all(v == "—" for v in row.values)]
    return Comparison(properties=records, breakdowns=breakdowns, rows=rows)


# ---------- Optional LLM ranking layer ----------

@dataclass
class RankedProperty:
    """One property's position in the AI ranking."""
    address: str       # must match a property in the comparison set
    rank: int          # 1 = best
    one_line: str      # short rationale, ≤80 chars

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RankedProperty":
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in allowed})


@dataclass
class PropertyRanking:
    """LLM-generated ranking + narrative. Optional input to comparison render.

    `additional_thoughts` is the same freeform-7th-section concept as
    SurveyorOpinion — cross-property observations, market timing notes,
    historical patterns, anything interesting that doesn't fit into
    per-property ranking.
    """
    ranked: list[RankedProperty]      # ordered 1..N
    bottom_line: str                  # 2-3 sentences synthesising the trade-off
    additional_thoughts: list[Finding] = field(default_factory=list)  # 0-8

    def to_dict(self) -> dict[str, Any]:
        return {
            "ranked": [r.to_dict() for r in self.ranked],
            "bottom_line": self.bottom_line,
            "additional_thoughts": [f.to_dict() for f in self.additional_thoughts],
        }

    def to_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, **kwargs)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PropertyRanking":
        return cls(
            ranked=[RankedProperty.from_dict(x) for x in (d.get("ranked") or [])],
            bottom_line=d.get("bottom_line") or "",
            additional_thoughts=[
                Finding.from_dict(f) for f in (d.get("additional_thoughts") or [])
            ],
        )

    @classmethod
    def from_json_file(cls, path: str) -> "PropertyRanking":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def validate(self, properties: list[PropertyRecord]) -> list[str]:
        errs: list[str] = []

        if not self.bottom_line or not self.bottom_line.strip():
            errs.append("bottom_line 为空")

        if len(self.ranked) != len(properties):
            errs.append(
                f"ranked 必须覆盖全部 {len(properties)} 个房子（当前 {len(self.ranked)}）"
            )

        # Each property addressed exactly once
        addrs_seen: dict[str, int] = {}
        prop_addrs = {p.address: p for p in properties}
        for r in self.ranked:
            if r.address not in prop_addrs:
                # Try fuzzy match (substring)
                matched = [a for a in prop_addrs if r.address in a or a in r.address]
                if not matched:
                    errs.append(f"ranked 里的地址 {r.address!r} 不在 comparison set 中")
                else:
                    addrs_seen[matched[0]] = addrs_seen.get(matched[0], 0) + 1
            else:
                addrs_seen[r.address] = addrs_seen.get(r.address, 0) + 1
            if r.rank < 1 or r.rank > len(properties):
                errs.append(f"rank={r.rank} 超出 1..{len(properties)} 范围")
            if not r.one_line or not r.one_line.strip():
                errs.append(f"ranked[{r.address}].one_line 为空")
        for addr, count in addrs_seen.items():
            if count > 1:
                errs.append(f"地址 {addr!r} 在 ranked 中出现 {count} 次（应恰好 1 次）")

        # Ranks must be a permutation of 1..N
        ranks = sorted(r.rank for r in self.ranked)
        if ranks != list(range(1, len(self.ranked) + 1)):
            errs.append(f"rank 必须是 1..N 的全排列（当前 {ranks}）")

        # additional_thoughts cap (no minimum; just don't overflow)
        if len(self.additional_thoughts) > 8:
            errs.append(
                f"additional_thoughts 不要超过 8 条（当前 {len(self.additional_thoughts)}）"
            )

        return errs


# ---------- CLI ----------

def _schema_for_llm(n_properties: int) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["ranked", "bottom_line"],
        "properties": {
            "ranked": {
                "type": "array",
                "description": f"必须恰好 {n_properties} 条，每个房子一条，rank 1..{n_properties} 不重复",
                "items": {
                    "type": "object",
                    "required": ["address", "rank", "one_line"],
                    "properties": {
                        "address": {"type": "string",
                                    "description": "完整地址（与 comparison 输入一致）"},
                        "rank": {"type": "integer", "minimum": 1, "maximum": n_properties},
                        "one_line": {"type": "string",
                                     "description": "≤80 字简短理由"},
                    },
                },
                "minItems": n_properties, "maxItems": n_properties,
            },
            "bottom_line": {
                "type": "string",
                "description": "2-3 句综合判断：trade-off 在哪、不同 buyer profile 适合谁",
            },
            "additional_thoughts": {
                "type": "array",
                "description": (
                    "可选：跨房源的随手观察 / 历史经验 / 不归到 ranking 里的"
                    "市场判断。0-8 条。比如 '这三套都属于 EH3 ground floor，"
                    "未来 5 年转售取决于学区改革' / "
                    "'1880s tenement 近期成交集中在春季，秋天挂可能要等更久'。"
                    "Finding 结构 (kind/text/rationale 等) 同 SurveyorOpinion。"
                ),
                "items": {
                    "type": "object",
                    "required": ["kind", "text"],
                    "properties": {
                        "kind": {"enum": ["fact", "judgment", "assumption"]},
                        "text": {"type": "string"},
                        "rationale": {"type": ["string", "null"]},
                        "quote": {"type": ["string", "null"]},
                    },
                },
                "maxItems": 8,
            },
        },
    }


def _cli() -> int:
    ap = argparse.ArgumentParser(
        prog="python -m property_assistant.analysis.comparison",
        description="Validate a PropertyRanking JSON file.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="Validate ranking JSON")
    v.add_argument("--ranking", required=True)
    v.add_argument("--addresses", required=True,
                   help="Semicolon-separated property addresses (use ; not , because addresses contain commas)")
    s = sub.add_parser("schema", help="Print schema for SKILL.md")
    s.add_argument("--n", type=int, required=True, help="Number of properties")
    args = ap.parse_args()

    if args.cmd == "validate":
        try:
            ranking = PropertyRanking.from_json_file(args.ranking)
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            print(f"failed to load ranking JSON: {exc}", file=sys.stderr)
            return 2
        # Synthesize stub PropertyRecord just to feed addresses
        stub_records = [PropertyRecord(address=a.strip())
                        for a in args.addresses.split(";") if a.strip()]
        errs = ranking.validate(stub_records)
        if errs:
            print("\n".join(f"- {e}" for e in errs), file=sys.stderr)
            return 1
        print("ok")
        return 0

    if args.cmd == "schema":
        print(json.dumps(_schema_for_llm(args.n), ensure_ascii=False, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(_cli())
