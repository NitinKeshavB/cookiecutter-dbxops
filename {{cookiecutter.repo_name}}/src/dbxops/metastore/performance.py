"""
Pipeline-update performance facts, derived from what a run collection already holds.

Every number comes from the run's own timings and the event log's row counts, so measuring costs no extra scan of
the data. Cost per day is loaded separately from ``system.billing.usage`` (``dbxops.metastore.cost``), because billing
rows arrive hours after a run ends.
"""

from __future__ import annotations

from typing import (
    Any,
    Mapping,
)

from dbxops.metastore.records import (
    PerformanceRecord,
    PipelineUpdateRecord,
    RunRecord,
)

# the md_run_performance columns a PerformanceRecord is read back from (rows_per_second is derived, not read)
PERFORMANCE_COLUMNS = (
    "target",
    "update_id",
    "run_id",
    "pipeline_asset_id",
    "duration_seconds",
    "rows_written",
    "full_refresh",
)


def performance_record(row: Mapping[str, Any]) -> PerformanceRecord:
    """Return the record of one ``md_run_performance`` row (as a mapping of ``PERFORMANCE_COLUMNS``)."""
    duration = row["duration_seconds"]
    return PerformanceRecord(
        target=row["target"],
        update_id=row["update_id"],
        run_id=row["run_id"] or "",
        pipeline_asset_id=row["pipeline_asset_id"] or "",
        duration_seconds=float(duration) if duration is not None else None,
        rows_written=int(row["rows_written"] or 0),
        full_refresh=bool(row["full_refresh"]),
    )


def _duration_seconds(update: PipelineUpdateRecord) -> float | None:
    started, ended = update.timing.started_at, update.timing.ended_at
    if started is None or ended is None:
        return None
    return max((ended - started).total_seconds(), 0.0)


def performance_from_run(run: RunRecord) -> list[PerformanceRecord]:
    """Return one performance record per pipeline update of ``run``."""
    return [
        PerformanceRecord(
            target=run.target,
            update_id=update.update_id,
            run_id=run.run_id,
            pipeline_asset_id=update.pipeline_asset_id,
            duration_seconds=_duration_seconds(update),
            rows_written=sum(metric.rows_written + metric.rows_upserted for metric in update.metrics),
            full_refresh=update.full_refresh,
        )
        for update in run.pipeline_updates
    ]
