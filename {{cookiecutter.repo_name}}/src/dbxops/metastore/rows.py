"""
Map fact and derived records onto table rows, independent of the database.

A value of ``DB_NOW`` means "the database clock, evaluated inside the write statement"; each repository turns it
into its own expression (``strftime`` on SQLite, ``now()`` on PostgreSQL, ``current_timestamp()`` on Unity
Catalog). The runner's own clock is never written.
"""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)
from typing import (
    Any,
    Iterable,
    Mapping,
    Sequence,
)

from dbxops.metastore.errors import PublishError
from dbxops.metastore.model import (
    DERIVED_KEYS,
    TAG_TABLE,
)
from dbxops.metastore.records import (
    ORIGINS,
    CostRecord,
    DeploymentRecord,
    EvalResultRecord,
    LineageEdge,
    OperationsBatch,
    PerformanceRecord,
    PublishContext,
    PublishLogEntry,
    QualityResultRecord,
    RegistrationRecord,
    RunRecord,
    SemanticLinkRow,
    TagRow,
    TuningKey,
    is_owned_tag_key,
)

PUBLISH_STARTED = "started"
PUBLISH_COMPLETE = "complete"
PUBLISH_FAILED = "failed"


class _DbNow:
    """Marker for the database clock."""

    def __repr__(self) -> str:
        return "DB_NOW"


DB_NOW = _DbNow()

FactRow = tuple[str, dict[str, Any]]


@dataclass(frozen=True)
class DerivedSet:
    """
    The complete desired set of one owner's Pattern B rows in one scope.

    A repository upserts ``items`` and deactivates every other active row of the same repo and ``scope``. For tags
    only owned keys are deactivated.
    """

    table_name: str
    items: Sequence[Mapping[str, Any]]
    scope: Mapping[str, str] = field(default_factory=dict)
    required_keys: Sequence[str] = ()

    @property
    def key_names(self) -> tuple[str, ...]:
        """Return the key columns of the table."""
        return DERIVED_KEYS[self.table_name]

    @property
    def data_names(self) -> list[str]:
        """Return the non-key columns the rows carry."""
        return [name for name in (self.items[0] if self.items else {}) if name not in self.key_names]

    def keys(self) -> list[tuple]:
        """Return the key tuple of every item."""
        return [tuple(item[name] for name in self.key_names) for item in self.items]

    def owns(self, key: Mapping[str, Any]) -> bool:
        """Return whether a stored row with this key may be deactivated by this set."""
        if self.table_name != TAG_TABLE:
            return True
        return is_owned_tag_key(key["tag_key"], self.required_keys)


def link_set(rows: Iterable[SemanticLinkRow]) -> DerivedSet:
    """Return the replace-set of semantic links."""
    items = [
        {"entity_type": row.entity_type, "entity_id": row.entity_id, "iri": row.iri, "label": row.label}
        for row in rows
    ]
    return DerivedSet("md_semantic_link", items)


def tag_set(rows: Iterable[TagRow], required_keys: Sequence[str] = ()) -> DerivedSet:
    """Return the replace-set of tags; a row with a key the platform does not own is rejected."""
    items = []
    for row in rows:
        if not is_owned_tag_key(row.tag_key, required_keys):
            raise PublishError(f"tag key {row.tag_key!r} is not an owned key; it cannot be published")
        items.append(
            {
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
                "tag_key": row.tag_key,
                "tag_value": row.tag_value,
            }
        )
    return DerivedSet(TAG_TABLE, items, required_keys=required_keys)


def lineage_set(edges: Sequence[LineageEdge], target: str, origin: str) -> DerivedSet:
    """Return the replace-set of ``origin`` lineage edges on ``target``."""
    if origin not in ORIGINS:
        raise PublishError(f"unknown lineage origin {origin!r}; expected one of {', '.join(sorted(ORIGINS))}")
    wrong = [edge for edge in edges if edge.origin != origin]
    if wrong:
        raise PublishError(f"lineage edge {wrong[0]} does not have origin {origin!r}")
    items = [
        {
            "target": target,
            "source_asset_id": edge.source_asset_id,
            "target_asset_id": edge.target_asset_id,
            "origin": edge.origin,
            "via": edge.via,
        }
        for edge in edges
    ]
    return DerivedSet("md_lineage_edge", items, scope={"target": target, "origin": origin})


def _fact(ctx: PublishContext, **values: Any) -> dict[str, Any]:
    return {**values, "repo_id": ctx.repo_id, "recorded_at": DB_NOW}


def deployment_rows(record: DeploymentRecord, ctx: PublishContext) -> list[FactRow]:
    """Return the ``md_deployment`` row and its ``md_deployed_asset`` rows."""
    ci = record.ci
    rows: list[FactRow] = [
        (
            "md_deployment",
            _fact(
                ctx,
                target=record.target,
                bundle=record.bundle,
                ci_run_id=ci.ci_run_id,
                ci_run_attempt=ci.ci_run_attempt,
                deployment_id=record.deployment_id,
                git_sha=ci.git_sha,
                ci_run_url=ci.ci_run_url,
                deployed_by=ci.deployed_by,
                deployed_at=(record.deployed_at if record.deployed_at is not None else DB_NOW),
                status=record.status,
                framework_version=ci.framework_version,
                dbxops_version=ci.dbxops_version,
            ),
        )
    ]
    for asset in record.assets:
        values = _fact(
            ctx,
            deployment_id=record.deployment_id,
            asset_id=asset.asset_id,
            asset_record_version=asset.asset_record_version,
            physical_name=asset.physical_name,
            workspace_object_id=asset.workspace_object_id,
        )
        rows.append(("md_deployed_asset", values))
    return rows


def run_rows(record: RunRecord, ctx: PublishContext) -> list[FactRow]:
    """Return the ``md_run``, ``md_pipeline_update`` and ``md_dataset_metrics`` rows of a run."""
    rows: list[FactRow] = [
        (
            "md_run",
            _fact(
                ctx,
                target=record.target,
                run_id=record.run_id,
                job_asset_id=record.job_asset_id,
                status=record.status,
                trigger=record.origin.trigger,
                started_at=record.timing.started_at,
                ended_at=record.timing.ended_at,
                git_sha=record.origin.git_sha,
            ),
        )
    ]
    for update in record.pipeline_updates:
        updated = _fact(
            ctx,
            target=record.target,
            update_id=update.update_id,
            run_id=record.run_id,
            pipeline_asset_id=update.pipeline_asset_id,
            status=update.status,
            full_refresh=update.full_refresh,
            started_at=update.timing.started_at,
            ended_at=update.timing.ended_at,
        )
        rows.append(("md_pipeline_update", updated))
        for metric in update.metrics:
            totals = _fact(
                ctx,
                target=record.target,
                update_id=update.update_id,
                asset_id=metric.asset_id,
                rows_written=metric.rows_written,
                rows_upserted=metric.rows_upserted,
                rows_deleted=metric.rows_deleted,
                rows_dropped=metric.rows_dropped,
            )
            rows.append(("md_dataset_metrics", totals))
    return rows


def quality_rows(results: Iterable[QualityResultRecord], ctx: PublishContext) -> list[FactRow]:
    """Return one ``md_quality_result`` row per result."""
    return [
        (
            "md_quality_result",
            _fact(
                ctx,
                target=item.target,
                update_id=item.update_id,
                asset_id=item.asset_id,
                rule_id=item.rule_id,
                contract_id=item.contract_id,
                rule_record_version=item.rule_record_version,
                passed=item.counts.passed,
                failed=item.counts.failed,
                dropped=item.counts.dropped,
                quarantined=item.counts.quarantined,
            ),
        )
        for item in results
    ]


def eval_rows(results: Iterable[EvalResultRecord], ctx: PublishContext) -> list[FactRow]:
    """Return one ``md_eval_result`` row per result."""
    return [
        (
            "md_eval_result",
            _fact(
                ctx,
                target=item.target,
                run_id=item.run_id,
                subject_asset_id=item.subject.asset_id,
                subject_version=item.subject.version,
                metric=item.metric,
                eval_kind=item.subject.kind,
                value=item.value,
                threshold=item.gate.threshold,
                comparator=item.gate.comparator,
                passed=item.gate.passed,
                alias=item.subject.alias,
                gate_source_path=item.gate.source_path,
            ),
        )
        for item in results
    ]


def registration_rows(records: Iterable[RegistrationRecord], ctx: PublishContext) -> list[FactRow]:
    """Return one ``md_registration`` row per registration."""
    return [
        (
            "md_registration",
            _fact(
                ctx,
                project_id=item.project_id,
                registry_pr_url=item.registry_pr_url,
                issue_url=item.issue_url,
                requested_by=item.requested_by,
                approved_by=item.approved_by,
                approved_at=item.approved_at,
                provisioned_repo_url=item.provisioned_repo_url,
            ),
        )
        for item in records
    ]


def performance_rows(records: Iterable[PerformanceRecord], ctx: PublishContext) -> list[FactRow]:
    """Return one ``md_run_performance`` row per pipeline update."""
    return [
        (
            "md_run_performance",
            _fact(
                ctx,
                target=item.target,
                update_id=item.update_id,
                run_id=item.run_id,
                pipeline_asset_id=item.pipeline_asset_id,
                duration_seconds=item.duration_seconds,
                rows_written=item.rows_written,
                rows_per_second=item.rows_per_second,
                full_refresh=item.full_refresh,
            ),
        )
        for item in records
    ]


def cost_rows(records: Iterable[CostRecord], ctx: PublishContext) -> list[FactRow]:
    """Return one ``md_cost_daily`` row per day, object and SKU."""
    return [
        (
            "md_cost_daily",
            _fact(
                ctx,
                usage_date=item.usage_date,
                object_type=item.object_type,
                object_id=item.object_id,
                sku_name=item.sku_name,
                day_index=item.day_index,
                project_id=item.owner.project_id,
                target=item.owner.target,
                bundle=item.owner.bundle,
                git_repo=item.owner.git_repo,
                git_sha=item.owner.git_sha,
                attribution=item.owner.attribution,
                dbus=item.amount.dbus,
                list_cost=item.amount.list_cost,
                currency=item.amount.currency,
            ),
        )
        for item in records
    ]


def _tuning_key(key: TuningKey) -> dict[str, Any]:
    return {
        "target": key.target,
        "asset_id": key.asset_id,
        "rule_code": key.rule_code,
        "evidence_key": key.evidence_key,
    }


def operations_rows(batch: OperationsBatch, ctx: PublishContext) -> list[FactRow]:
    """Return the rows of every squad-owned operational fact in ``batch``."""
    rows = performance_rows(batch.performance, ctx)
    rows += [
        (
            "md_tuning_recommendation",
            _fact(
                ctx,
                **_tuning_key(item.key),
                severity=item.severity,
                message=item.message,
                expected_saving_pct=item.expected_saving_pct,
                evidence=dict(item.evidence),
            ),
        )
        for item in batch.recommendations
    ]
    rows += [
        (
            "md_tuning_decision",
            _fact(
                ctx, **_tuning_key(item.key), decision=item.decision, decided_by=item.decided_by, reason=item.reason
            ),
        )
        for item in batch.tuning_decisions
    ]
    rows += [
        (
            "md_design_decision",
            _fact(
                ctx,
                asset_id=item.asset_id,
                git_sha=item.git_sha,
                recommended=item.recommended,
                chosen=item.chosen,
                justification=item.justification,
                approver=item.approver,
            ),
        )
        for item in batch.design_decisions
    ]
    rows += [
        (
            "md_run_event",
            _fact(
                ctx,
                run_id=item.run_id,
                seq=item.seq,
                level=item.level,
                message=item.message,
                logged_at=item.logged_at,
                context=dict(item.context),
            ),
        )
        for item in batch.run_events
    ]
    return rows


def publish_log_row(entry: PublishLogEntry) -> FactRow:
    """Return the ``md_publish_log`` row of one status event; ``started`` stamps start, others stamp finish."""
    started = entry.status == PUBLISH_STARTED
    ctx, counts = entry.ctx, entry.counts
    return (
        "md_publish_log",
        {
            "publish_id": ctx.publish_id,
            "status": entry.status,
            "repo_id": ctx.repo_id,
            "target": ctx.target,
            "what": entry.what,
            "git_sha": ctx.git_commit or "",
            "inserted": counts.inserted,
            "closed": counts.closed,
            "unchanged": counts.unchanged,
            "orphans_cleaned": counts.orphans_cleaned,
            "message": entry.message,
            "started_at": DB_NOW if started else None,
            "finished_at": None if started else DB_NOW,
            "recorded_at": DB_NOW,
        },
    )
