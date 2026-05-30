"""Preference learning signals — diff "actual preference" against preferences.json.

Ground truth = explicit user action: `status` (⭐ 感兴趣 / 💰 已出价 / ❌ 已放弃)
and `self_score` / `partner_score`. We DO NOT use `self_feeling` / `partner_feeling`
text frequency — per the user feedback that pre-filtered conditions (bedroom count,
school catchment) are never discussed in feeling text and would be unfairly downweighted.

Output is advisory only — never auto-patches preferences.json. The CLI prints
the suggestions; the user decides whether to apply them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from property_assistant.analysis.scoring import ScoreBreakdown, compute
from property_assistant.core.property_record import PropertyRecord


# Minimum sample size before any analysis runs. Below this, status/score
# distributions are too noisy to draw conclusions from.
MIN_SAMPLE_SIZE = 5

# Status values that signal "user is positive about this property"
_POSITIVE_STATUSES = {"⭐ 感兴趣", "💰 已出价", "✅ 已购入"}
_NEGATIVE_STATUSES = {"❌ 已放弃"}

# Thresholds for status-mismatch detection
_UNDERRATED_SCORE_MAX = 55   # algo score < 55 but user is positive → algo missed something
_OVERRATED_SCORE_MIN = 75    # algo score > 75 but user abandoned → algo overweighted something


@dataclass
class Evidence:
    """One property's data point in a signal."""
    address: str
    algo_score: float
    self_score: Optional[float]
    status: Optional[str]
    note: str  # e.g. "floor dimension only 2/10 — possibly over-strict"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PreferenceSignal:
    """One actionable signal about preferences.json."""
    kind: str            # 'underrated' | 'overrated' | 'low_correlation' | 'dimension_suspicion'
    severity: str        # 'high' | 'medium' | 'low'
    summary: str         # one-line human description
    suggestion: str      # what to consider changing in preferences.json
    evidence: list[Evidence] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "summary": self.summary,
            "suggestion": self.suggestion,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass
class PreferenceSignals:
    """Aggregate output. `signals` is empty when there isn't enough data."""
    enough_data: bool
    sample_size: int
    algo_self_correlation: Optional[float]  # Spearman between algo total and self_score
    signals: list[PreferenceSignal] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enough_data": self.enough_data,
            "sample_size": self.sample_size,
            "algo_self_correlation": self.algo_self_correlation,
            "signals": [s.to_dict() for s in self.signals],
        }


# ----------------------------------------------------------------------------
# Analysis
# ----------------------------------------------------------------------------

def analyze(records: list[PropertyRecord],
            prefs: Optional[dict] = None) -> PreferenceSignals:
    """Run the full preference-signal analysis.

    `records` should already be filtered to "viewed" properties (the caller
    decides what "viewed" means). We only consider records that carry an
    actionable signal — either a status or a self_score.
    """
    actionable = [
        r for r in records
        if (r.status in _POSITIVE_STATUSES | _NEGATIVE_STATUSES) or r.self_score is not None
    ]
    n = len(actionable)

    if n < MIN_SAMPLE_SIZE:
        return PreferenceSignals(
            enough_data=False,
            sample_size=n,
            algo_self_correlation=None,
            signals=[],
        )

    # Compute algorithm scores once per actionable record
    scored: list[tuple[PropertyRecord, ScoreBreakdown]] = [
        (r, compute(r, prefs)) for r in actionable
    ]

    signals: list[PreferenceSignal] = []

    # ---- Signal 1: status mismatches (algo under/overrated vs user action) ----
    underrated = _detect_status_mismatches(scored, kind="underrated")
    overrated = _detect_status_mismatches(scored, kind="overrated")
    if underrated.evidence:
        signals.append(underrated.signal)
    if overrated.evidence:
        signals.append(overrated.signal)

    # ---- Signal 2: low algo-vs-self correlation ----
    self_scored = [(r, sb) for r, sb in scored if r.self_score is not None]
    corr: Optional[float] = None
    if len(self_scored) >= MIN_SAMPLE_SIZE:
        algo_scores = [sb.total for _, sb in self_scored]
        self_scores = [float(r.self_score) for r, _ in self_scored]
        corr = _spearman(algo_scores, self_scores)
        if corr is not None and corr < 0.5:
            severity = "high" if corr < 0.3 else "medium"
            signals.append(PreferenceSignal(
                kind="low_correlation",
                severity=severity,
                summary=f"算法排序 vs 你的 self_score 相关性弱（Spearman ρ={corr:+.2f}）",
                suggestion=(
                    "整体权重配置可能没反映你的真实偏好。看下面 dimension_suspicion 信号定位具体维度，"
                    "或考虑整体重排 score_weights（譬如 building_type 权重过高？）。"
                ),
                evidence=[
                    Evidence(
                        address=r.address,
                        algo_score=sb.total,
                        self_score=r.self_score,
                        status=r.status,
                        note=f"algo {sb.total} vs self {r.self_score}",
                    ) for r, sb in self_scored
                ],
            ))

    # ---- Signal 3: dimension suspicion per mismatch ----
    # Run underrated and overrated separately — they look for opposite extremes.
    signals.extend(_attribute_dimensions(underrated.evidence_records, mode="underrated"))
    signals.extend(_attribute_dimensions(overrated.evidence_records, mode="overrated"))

    return PreferenceSignals(
        enough_data=True,
        sample_size=n,
        algo_self_correlation=corr,
        signals=signals,
    )


# ----------------------------------------------------------------------------
# Status mismatch detection
# ----------------------------------------------------------------------------

@dataclass
class _MismatchBucket:
    """Internal helper: a PreferenceSignal plus the raw (record, breakdown) pairs
    so the dimension-attribution step can reuse them without recomputing."""
    signal: PreferenceSignal
    evidence_records: list[tuple[PropertyRecord, ScoreBreakdown]] = field(default_factory=list)

    # forward PreferenceSignal attrs for compatibility with the rest of analyze()
    @property
    def evidence(self) -> list[Evidence]:
        return self.signal.evidence


def _detect_status_mismatches(scored: list[tuple[PropertyRecord, ScoreBreakdown]],
                              *, kind: str) -> _MismatchBucket:
    """kind = 'underrated' (algo low but user positive) or 'overrated' (algo high but user gave up)."""
    matches: list[tuple[PropertyRecord, ScoreBreakdown]] = []
    for r, sb in scored:
        if kind == "underrated":
            if r.status in _POSITIVE_STATUSES and sb.total < _UNDERRATED_SCORE_MAX:
                matches.append((r, sb))
        else:  # overrated
            if r.status in _NEGATIVE_STATUSES and sb.total >= _OVERRATED_SCORE_MIN:
                matches.append((r, sb))

    if kind == "underrated":
        signal = PreferenceSignal(
            kind="underrated",
            severity="high" if len(matches) >= 2 else "medium",
            summary=f"算法给低分但你标记为感兴趣/出价/已购：{len(matches)} 套",
            suggestion=(
                "这些房子有评分维度没覆盖到的优点。看每条 evidence 的 note 字段定位"
                "算法在哪个维度打得最低 → 考虑该维度的阈值是否过严或权重是否太高。"
            ),
        )
    else:
        signal = PreferenceSignal(
            kind="overrated",
            severity="high" if len(matches) >= 2 else "medium",
            summary=f"算法给高分但你最终放弃：{len(matches)} 套",
            suggestion=(
                "这些房子的算法高分维度可能虚高。看每条 evidence 的 note 找出"
                "算法给得最满的维度 → 考虑该维度权重是否该降。"
            ),
        )

    bucket = _MismatchBucket(signal=signal)
    for r, sb in matches:
        note = _dim_attribution_note(sb, mode=kind)
        signal.evidence.append(Evidence(
            address=r.address,
            algo_score=sb.total,
            self_score=r.self_score,
            status=r.status,
            note=note,
        ))
        bucket.evidence_records.append((r, sb))
    return bucket


def _dim_attribution_note(sb: ScoreBreakdown, *, mode: str) -> str:
    """For one property, find the dimension that most explains the mismatch."""
    if not sb.dimensions:
        return ""
    if mode == "underrated":
        # find dimension with the lowest fill ratio (algo punished it hardest)
        worst = min(sb.dimensions, key=lambda d: (d.score / d.max_score) if d.max_score else 0)
        ratio = (worst.score / worst.max_score) if worst.max_score else 0
        return f"{worst.name}: {worst.score}/{worst.max_score} ({ratio:.0%}) — 该维度拖低了总分"
    else:
        # find dimension with the highest fill ratio (algo rewarded it hardest)
        best = max(sb.dimensions, key=lambda d: (d.score / d.max_score) if d.max_score else 0)
        ratio = (best.score / best.max_score) if best.max_score else 0
        return f"{best.name}: {best.score}/{best.max_score} ({ratio:.0%}) — 该维度撑起了总分"


def _attribute_dimensions(mismatches: list[tuple[PropertyRecord, ScoreBreakdown]],
                          *, mode: str) -> list[PreferenceSignal]:
    """Find dimensions consistently extreme across mismatched properties.

    mode='underrated' — look for dims that are consistently LOW (algo over-penalised
    properties the user actually liked).
    mode='overrated' — look for dims that are consistently HIGH (algo rewarded
    properties the user ended up abandoning).

    A dimension is flagged only if ≥2 properties in this mismatch bucket show
    the extreme pattern (avg ratio < 0.3 for underrated, > 0.9 for overrated).
    """
    if not mismatches:
        return []

    blame: dict[str, list[tuple[PropertyRecord, ScoreBreakdown, float]]] = {}
    for r, sb in mismatches:
        for d in sb.dimensions:
            if not d.max_score:
                continue
            ratio = d.score / d.max_score
            blame.setdefault(d.name, []).append((r, sb, ratio))

    signals: list[PreferenceSignal] = []
    for dim_name, entries in blame.items():
        if len(entries) < 2:
            continue
        ratios = [ratio for _, _, ratio in entries]
        avg_ratio = sum(ratios) / len(ratios)
        if mode == "underrated" and avg_ratio < 0.3:
            signals.append(PreferenceSignal(
                kind="dimension_suspicion",
                severity="medium",
                summary=f"维度 `{dim_name}` 在算法低估的房子中平均得分 {avg_ratio:.0%}",
                suggestion=(
                    f"该维度阈值可能过严（你喜欢的房子在这维度上反复被打低分）。"
                    f"在 preferences.json 检查 `{dim_name}` 的阈值/分级配置，"
                    f"或考虑调低 score_weights.{dim_name}。"
                ),
                evidence=[
                    Evidence(
                        address=r.address,
                        algo_score=sb.total,
                        self_score=r.self_score,
                        status=r.status,
                        note=f"{dim_name} ratio={ratio:.0%}",
                    ) for r, sb, ratio in entries
                ],
            ))
        elif mode == "overrated" and avg_ratio > 0.9:
            signals.append(PreferenceSignal(
                kind="dimension_suspicion",
                severity="medium",
                summary=f"维度 `{dim_name}` 在算法高估的房子中平均得分 {avg_ratio:.0%}",
                suggestion=(
                    f"该维度持续给满分但你最终放弃了这些房子，权重可能虚高。"
                    f"考虑在 preferences.json 调低 score_weights.{dim_name}。"
                ),
                evidence=[
                    Evidence(
                        address=r.address,
                        algo_score=sb.total,
                        self_score=r.self_score,
                        status=r.status,
                        note=f"{dim_name} ratio={ratio:.0%}",
                    ) for r, sb, ratio in entries
                ],
            ))
    return signals


# ----------------------------------------------------------------------------
# Spearman rank correlation (no scipy)
# ----------------------------------------------------------------------------

def _spearman(xs: list[float], ys: list[float]) -> Optional[float]:
    """Spearman rank correlation. Returns None if input invalid or all-tied."""
    n = len(xs)
    if n < 2 or len(ys) != n:
        return None
    rx = _ranks(xs)
    ry = _ranks(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx)
    dy = sum((b - my) ** 2 for b in ry)
    if dx == 0 or dy == 0:
        return None
    return round(num / (dx * dy) ** 0.5, 3)


def _ranks(values: list[float]) -> list[float]:
    """Convert values to ranks, handling ties by averaging."""
    indexed = sorted(enumerate(values), key=lambda p: p[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2 + 1  # 1-indexed average
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return ranks
