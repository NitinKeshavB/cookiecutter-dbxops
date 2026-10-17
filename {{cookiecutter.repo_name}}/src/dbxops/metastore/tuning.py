"""
The tuning advisor: turns each pipeline's recent performance into recommendations a person accepts or rejects.

Each rule looks at one pipeline's newest update against its own earlier updates and returns at most one
``TuningRecommendation`` (Chain of Responsibility: every rule runs, each reports independently). Nothing here changes a
pipeline. Recommendations are recorded in ``md_tuning_recommendation``; a person's answer goes to
``md_tuning_decision`` (``python -m dbxops.metastore decide``), and ``v_open_tuning_recommendations`` lists what is
still waiting. A recommendation is keyed by the update it fired on, so re-running the advisor records nothing twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import (
    Callable,
    Iterable,
    Sequence,
)

from dbxops.metastore.records import (
    PerformanceRecord,
    TuningKey,
    TuningRecommendation,
)

SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"


@dataclass(frozen=True)
class TuningPolicy:
    """Thresholds of the advisor; ``min_history`` earlier updates are needed before a comparison is made."""

    slower_ratio: float = 1.25
    throughput_ratio: float = 0.75
    full_refresh_limit: int = 2
    window: int = 10
    min_history: int = 3


@dataclass(frozen=True)
class PipelineHistory:
    """One pipeline's newest update and the updates before it (newest first)."""

    latest: PerformanceRecord
    earlier: Sequence[PerformanceRecord]


Rule = Callable[[PipelineHistory, TuningPolicy], TuningRecommendation | None]


def _key(history: PipelineHistory, rule_code: str) -> TuningKey:
    latest = history.latest
    return TuningKey(latest.target, latest.pipeline_asset_id, rule_code, latest.update_id)


def _baseline(values: Iterable[float | None]) -> float | None:
    known = [value for value in values if value]
    return median(known) if known else None


def rule_slower_than_baseline(history: PipelineHistory, policy: TuningPolicy) -> TuningRecommendation | None:
    """Flag an update that took ``slower_ratio`` times longer than the median of the earlier ones."""
    if len(history.earlier) < policy.min_history or not history.latest.duration_seconds:
        return None
    baseline = _baseline(item.duration_seconds for item in history.earlier)
    if baseline is None or history.latest.duration_seconds < baseline * policy.slower_ratio:
        return None
    ratio = history.latest.duration_seconds / baseline
    return TuningRecommendation(
        _key(history, "slower_than_baseline"),
        SEVERITY_WARNING,
        f"update took {history.latest.duration_seconds:.0f}s, {ratio:.1f}x the median of the previous "
        f"{len(history.earlier)} ({baseline:.0f}s). Check for data growth, skew, small files or a lost incremental "
        "refresh before changing compute.",
        expected_saving_pct=round((1 - 1 / ratio) * 100, 1),
        evidence={"duration_seconds": history.latest.duration_seconds, "baseline_seconds": baseline},
    )


def rule_throughput_drop(history: PipelineHistory, policy: TuningPolicy) -> TuningRecommendation | None:
    """Flag an update whose rows per second fell below ``throughput_ratio`` of the earlier median."""
    latest = history.latest.rows_per_second
    if len(history.earlier) < policy.min_history or not latest:
        return None
    baseline = _baseline(item.rows_per_second for item in history.earlier)
    if baseline is None or latest > baseline * policy.throughput_ratio:
        return None
    return TuningRecommendation(
        _key(history, "throughput_drop"),
        SEVERITY_WARNING,
        f"throughput fell to {latest:.0f} rows/s from a median of {baseline:.0f}. Typical causes: skewed keys, "
        "clustering keys that no longer match the filters, or a source read that stopped being incremental.",
        evidence={"rows_per_second": latest, "baseline_rows_per_second": baseline},
    )


def rule_repeated_full_refresh(history: PipelineHistory, policy: TuningPolicy) -> TuningRecommendation | None:
    """Flag a pipeline that fully refreshed more than ``full_refresh_limit`` times in the window."""
    recent = [history.latest, *history.earlier][: policy.window]
    count = sum(1 for item in recent if item.full_refresh)
    if not history.latest.full_refresh or count <= policy.full_refresh_limit:
        return None
    return TuningRecommendation(
        _key(history, "repeated_full_refresh"),
        SEVERITY_WARNING,
        f"{count} of the last {len(recent)} updates were full refreshes. Full refreshes recompute everything; find out "
        "who triggers them, and check that materialized-view sources have row tracking so refreshes stay incremental.",
        evidence={"full_refreshes": count, "window": len(recent)},
    )


DEFAULT_TUNING_RULES: tuple[Rule, ...] = (rule_slower_than_baseline, rule_throughput_drop, rule_repeated_full_refresh)


def histories(records: Sequence[PerformanceRecord], window: int) -> list[PipelineHistory]:
    """Group newest-first records by pipeline into one history each (at most ``window`` earlier updates)."""
    grouped: dict[str, list[PerformanceRecord]] = {}
    for record in records:
        grouped.setdefault(record.pipeline_asset_id, []).append(record)
    return [PipelineHistory(items[0], items[1:][:window]) for items in grouped.values() if items]


def advise(
    records: Sequence[PerformanceRecord],
    policy: TuningPolicy | None = None,
    rules: tuple[Rule, ...] = DEFAULT_TUNING_RULES,
) -> list[TuningRecommendation]:
    """Return every recommendation the rules make over newest-first performance ``records``."""
    chosen = policy or TuningPolicy()
    found = []
    for history in histories(records, chosen.window):
        for rule in rules:
            recommendation = rule(history, chosen)
            if recommendation is not None:
                found.append(recommendation)
    return found
