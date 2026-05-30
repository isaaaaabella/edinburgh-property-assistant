"""Tests for analysis.preference_signals."""

from __future__ import annotations

import pytest

from property_assistant.analysis.preference_signals import (
    MIN_SAMPLE_SIZE,
    _ranks,
    _spearman,
    analyze,
)
from property_assistant.analysis.scoring import load_preferences
from property_assistant.core.property_record import PropertyRecord


@pytest.fixture(scope="module")
def prefs() -> dict:
    return load_preferences()


# ----------------------------------------------------------------------------
# Record builders — produce records that score predictably under default prefs
# ----------------------------------------------------------------------------

def _high_score_record(address: str, **overrides) -> PropertyRecord:
    """A record that scores ~90 under defaults: tenement + Boroughmuir + gas + 2-bed + low cat."""
    base = dict(
        address=address,
        asking_price=300000.0,
        floor_area=85.0,
        bedrooms=2,
        building_type="Traditional Tenement",
        era=1890,
        floor="1F",
        gas_heating=True,
        school_zone=["Boroughmuir High School"],
        cat2_count=1,
        cat3_count=0,
        roof_issue=False,
        epc_rating="B",
    )
    base.update(overrides)
    return PropertyRecord(**base)


def _low_score_record(address: str, **overrides) -> PropertyRecord:
    """A record that scores ~20-40: no gas, no school, expensive, multiple cat3."""
    base = dict(
        address=address,
        asking_price=600000.0,
        floor_area=60.0,                  # £10,000/m² → expensive
        bedrooms=1,
        building_type="Purpose Built Flat",
        era=2010,
        floor="Top",
        gas_heating=False,
        school_zone=[],
        cat2_count=8,
        cat3_count=2,
        roof_issue=True,
        epc_rating="F",
    )
    base.update(overrides)
    return PropertyRecord(**base)


# ----------------------------------------------------------------------------
# Spearman + ranks unit tests
# ----------------------------------------------------------------------------

def test_ranks_no_ties():
    assert _ranks([3.0, 1.0, 2.0]) == [3.0, 1.0, 2.0]


def test_ranks_with_ties():
    # Two values tied at the top → both get rank 2.5 (avg of 2 and 3)
    assert _ranks([1.0, 5.0, 5.0]) == [1.0, 2.5, 2.5]


def test_spearman_perfect_correlation():
    assert _spearman([1.0, 2.0, 3.0, 4.0], [10.0, 20.0, 30.0, 40.0]) == 1.0


def test_spearman_perfect_inverse():
    assert _spearman([1.0, 2.0, 3.0, 4.0], [40.0, 30.0, 20.0, 10.0]) == -1.0


def test_spearman_invalid_input():
    assert _spearman([1.0], [2.0]) is None
    assert _spearman([1.0, 2.0], [3.0]) is None
    assert _spearman([5.0, 5.0, 5.0], [1.0, 2.0, 3.0]) is None  # all-tied → div by 0


# ----------------------------------------------------------------------------
# analyze() — sample-size guards
# ----------------------------------------------------------------------------

def test_analyze_skips_when_below_min_sample(prefs):
    records = [_high_score_record(f"A{i}", status="⭐ 感兴趣") for i in range(MIN_SAMPLE_SIZE - 1)]
    result = analyze(records, prefs)
    assert result.enough_data is False
    assert result.sample_size == MIN_SAMPLE_SIZE - 1
    assert result.signals == []


def test_analyze_skips_when_no_actionable_signal(prefs):
    # 5 records but none have status in actionable set and no self_score
    records = [_high_score_record(f"A{i}", status="🔍 待看") for i in range(MIN_SAMPLE_SIZE)]
    result = analyze(records, prefs)
    assert result.enough_data is False
    assert result.sample_size == 0


# ----------------------------------------------------------------------------
# Status mismatch signals
# ----------------------------------------------------------------------------

def test_underrated_signal_when_algo_low_but_user_likes(prefs):
    # 3 records the user marked ⭐ but algo scores < 55
    records = [
        _low_score_record("Cheap1", status="⭐ 感兴趣"),
        _low_score_record("Cheap2", status="💰 已出价"),
        _low_score_record("Cheap3", status="⭐ 感兴趣"),
        # 2 control records with self_score (so they count as actionable but don't mismatch)
        _high_score_record("Other1", status="👀 已看", self_score=8.0),
        _high_score_record("Other2", status="👀 已看", self_score=8.5),
    ]
    result = analyze(records, prefs)
    assert result.enough_data is True
    kinds = [s.kind for s in result.signals]
    assert "underrated" in kinds
    underrated = next(s for s in result.signals if s.kind == "underrated")
    assert len(underrated.evidence) == 3
    assert underrated.severity == "high"  # ≥2 mismatches → high


def test_overrated_requires_explicit_low_self_score(prefs):
    """In the new design, ❌ 已放弃 alone is too noisy (could be funds/timing).
    Overrated signal only fires when the user ALSO gave a low self_score."""
    records = [
        # These two ARE overrated mismatches — abandoned AND self_score ≤ 4
        _high_score_record("HateHigh1", status="❌ 已放弃", self_score=3.0),
        _high_score_record("HateHigh2", status="❌ 已放弃", self_score=2.5),
        # 3 control records to clear sample-size guard
        _high_score_record("Neutral1", status="👀 已看", self_score=7.0),
        _high_score_record("Neutral2", status="👀 已看", self_score=7.5),
        _high_score_record("Neutral3", status="👀 已看", self_score=8.0),
    ]
    result = analyze(records, prefs)
    kinds = [s.kind for s in result.signals]
    assert "overrated" in kinds
    overrated = next(s for s in result.signals if s.kind == "overrated")
    assert len(overrated.evidence) == 2


def test_abandoned_without_self_score_is_not_actionable(prefs):
    """❌ 已放弃 with no self_score is treated as "external reason" (funds/timing)
    and should not enter the actionable pool. So 5 such records → enough_data=False."""
    records = [_high_score_record(f"Abandoned{i}", status="❌ 已放弃") for i in range(5)]
    result = analyze(records, prefs)
    assert result.enough_data is False
    assert result.sample_size == 0


def test_out_of_range_self_score_is_ignored(prefs):
    """Defends against a real-world data hazard: someone copies the algo total
    (e.g. 80, 90) into the 0-10 self_score column. Such records must not enter
    the actionable pool — they would otherwise create a spurious self-correlation
    by effectively comparing algo to itself."""
    records = [
        # 4 records with bogus self_score = algo total + 1 minor offset
        PropertyRecord(address=f"Bogus{i}", status="❌ 已放弃", self_score=85.0 - i * 5)
        for i in range(4)
    ] + [
        # 1 legitimate record
        PropertyRecord(address="Real", status="⭐ 感兴趣", self_score=8.0),
    ]
    result = analyze(records, prefs)
    # Only "Real" should be actionable
    assert result.sample_size == 1
    assert result.enough_data is False


def test_abandoned_high_self_score_does_not_fire_overrated(prefs):
    """If user abandoned but gave self_score=8 (e.g. liked but couldn't afford),
    that's NOT an overrated signal — algo wasn't wrong, external factor was."""
    records = [
        _high_score_record(f"LovedButAbandoned{i}", status="❌ 已放弃", self_score=8.0)
        for i in range(3)
    ] + [
        _high_score_record(f"Other{i}", status="👀 已看", self_score=8.0)
        for i in range(2)
    ]
    result = analyze(records, prefs)
    assert result.enough_data is True
    assert not any(s.kind == "overrated" for s in result.signals)


# ----------------------------------------------------------------------------
# Correlation signal
# ----------------------------------------------------------------------------

def test_low_correlation_signal(prefs):
    # 5 records with self_score that inversely correlates with algo score
    records = [
        _high_score_record("H1", self_score=2.0),   # algo high, self low
        _high_score_record("H2", self_score=3.0),
        _low_score_record("L1", self_score=9.0),    # algo low, self high
        _low_score_record("L2", self_score=8.5),
        _low_score_record("L3", self_score=9.5),
    ]
    result = analyze(records, prefs)
    assert result.enough_data is True
    assert result.algo_self_correlation is not None
    assert result.algo_self_correlation < 0.5
    assert any(s.kind == "low_correlation" for s in result.signals)


def test_high_correlation_no_correlation_signal(prefs):
    # algo and self agree → no low_correlation signal
    records = [
        _high_score_record("H1", self_score=9.0),
        _high_score_record("H2", self_score=8.5),
        _low_score_record("L1", self_score=3.0),
        _low_score_record("L2", self_score=2.5),
        _low_score_record("L3", self_score=2.0),
    ]
    result = analyze(records, prefs)
    assert result.enough_data is True
    assert result.algo_self_correlation is not None
    assert result.algo_self_correlation >= 0.5
    assert not any(s.kind == "low_correlation" for s in result.signals)


# ----------------------------------------------------------------------------
# Dimension attribution
# ----------------------------------------------------------------------------

def test_dimension_suspicion_emitted_for_underrated_cluster(prefs):
    # 3 underrated records all share the same blamed dimensions (low value, no gas, no school)
    records = [
        _low_score_record(f"Bad{i}", status="⭐ 感兴趣") for i in range(3)
    ] + [
        _high_score_record(f"OK{i}", status="👀 已看", self_score=8.0) for i in range(2)
    ]
    result = analyze(records, prefs)
    dim_signals = [s for s in result.signals if s.kind == "dimension_suspicion"]
    # At least one dim should be consistently extreme across the underrated cluster
    assert len(dim_signals) >= 1


# ----------------------------------------------------------------------------
# Output shape
# ----------------------------------------------------------------------------

def test_to_dict_is_json_safe(prefs):
    import json
    records = [_low_score_record(f"L{i}", status="⭐ 感兴趣") for i in range(3)] + \
              [_high_score_record(f"H{i}", status="👀 已看", self_score=8.0) for i in range(2)]
    result = analyze(records, prefs)
    # Should be JSON-serialisable end to end
    json.dumps(result.to_dict(), ensure_ascii=False)


# ----------------------------------------------------------------------------
# Feeling excerpt (CLI annotation, NOT a signal input)
# ----------------------------------------------------------------------------

def test_feeling_excerpt_populates_when_present(prefs):
    records = [
        _low_score_record("Cheap1", status="⭐ 感兴趣",
                          self_feeling="位置不错，邻居安静，价格能接受"),
        _low_score_record("Cheap2", status="⭐ 感兴趣"),
        _low_score_record("Cheap3", status="⭐ 感兴趣"),
        _high_score_record("Other1", status="👀 已看", self_score=8.0),
        _high_score_record("Other2", status="👀 已看", self_score=8.5),
    ]
    result = analyze(records, prefs)
    underrated = next(s for s in result.signals if s.kind == "underrated")
    cheap1_ev = next(e for e in underrated.evidence if e.address == "Cheap1")
    cheap2_ev = next(e for e in underrated.evidence if e.address == "Cheap2")
    assert cheap1_ev.feeling_excerpt is not None
    assert "位置不错" in cheap1_ev.feeling_excerpt
    assert cheap2_ev.feeling_excerpt is None


def test_feeling_excerpt_truncates_long_text(prefs):
    long_text = "这套房子" + "非常喜欢，" * 30  # ~150 chars
    records = [
        _low_score_record("CheapVerbose", status="⭐ 感兴趣", self_feeling=long_text),
        _low_score_record("Cheap2", status="⭐ 感兴趣"),
        _low_score_record("Cheap3", status="⭐ 感兴趣"),
        _high_score_record("Other1", status="👀 已看", self_score=8.0),
        _high_score_record("Other2", status="👀 已看", self_score=8.5),
    ]
    result = analyze(records, prefs)
    underrated = next(s for s in result.signals if s.kind == "underrated")
    ev = next(e for e in underrated.evidence if e.address == "CheapVerbose")
    assert ev.feeling_excerpt is not None
    assert ev.feeling_excerpt.endswith("…")
    assert len(ev.feeling_excerpt) <= 65   # 60 chars + ellipsis margin
