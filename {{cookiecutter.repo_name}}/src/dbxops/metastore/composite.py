"""
``CompositeRepository``: fans every call out to several sinks (Composite).

Sinks are written in order and the first failure propagates, so the primary sink (the first) is always written
before the others. Publishes are idempotent, so after a failure the same publish can simply be run again: sinks that
already hold the data report it as unchanged and the failed one catches up. Reads and counts come from the primary.
"""

from __future__ import annotations

from typing import (
    Any,
    Callable,
    Sequence,
)

from dbxops.metastore.records import (
    CostRecord,
    DefinitionRecord,
    DeploymentRecord,
    EvalResultRecord,
    LineageEdge,
    OperationsBatch,
    PerformanceRecord,
    PublishContext,
    PublishCounts,
    PublishLogEntry,
    QualityResultRecord,
    QualityRuleRef,
    RegistrationRecord,
    RunRecord,
    SemanticLinkRow,
    TagRow,
)
from dbxops.metastore.repository import MetadataRepository


class CompositeRepository:
    """A repository that writes to every sink in order and reads from the first."""

    def __init__(self, sinks: Sequence[MetadataRepository]):
        if not sinks:
            raise ValueError("a CompositeRepository needs at least one sink")
        self.sinks = list(sinks)

    def _fan_out(self, call: Callable[[MetadataRepository], Any]) -> Any:
        return [call(sink) for sink in self.sinks][0]

    def ensure_schema(self) -> None:
        """Ensure the schema on every sink."""
        self._fan_out(lambda sink: sink.ensure_schema())

    def upsert_definitions(self, records: Sequence[DefinitionRecord], ctx: PublishContext) -> PublishCounts:
        """Publish definitions to every sink; return the primary sink's counts."""
        return self._fan_out(lambda sink: sink.upsert_definitions(records, ctx))

    def retire_definitions(
        self, kind: str, entity_ids: Sequence[str], ctx: PublishContext, git_commit: str | None = None
    ) -> PublishCounts:
        """Retire entities on every sink."""
        return self._fan_out(lambda sink: sink.retire_definitions(kind, entity_ids, ctx, git_commit))

    def get_current_definition(self, kind: str, entity_id: str) -> DefinitionRecord | None:
        """Read from the primary sink."""
        return self.sinks[0].get_current_definition(kind, entity_id)

    def list_current_entity_ids(self, kind: str, repo_id: str, include_retired: bool = False) -> list[str]:
        """Read from the primary sink."""
        return self.sinks[0].list_current_entity_ids(kind, repo_id, include_retired)

    def current_quality_rules(self, repo_id: str) -> list[QualityRuleRef]:
        """Read from the primary sink."""
        return self.sinks[0].current_quality_rules(repo_id)

    def recent_performance(self, repo_id: str, target: str, limit: int = 500) -> list[PerformanceRecord]:
        """Read recent performance facts from the primary sink."""
        return self.sinks[0].recent_performance(repo_id, target, limit)

    def upsert_semantic_links(self, rows: Sequence[SemanticLinkRow], ctx: PublishContext) -> int:
        """Replace semantic links on every sink."""
        return self._fan_out(lambda sink: sink.upsert_semantic_links(rows, ctx))

    def upsert_tags(self, rows: Sequence[TagRow], ctx: PublishContext, required_keys: Sequence[str] = ()) -> int:
        """Replace tags on every sink."""
        return self._fan_out(lambda sink: sink.upsert_tags(rows, ctx, required_keys))

    def record_lineage(self, edges: Sequence[LineageEdge], ctx: PublishContext, *, target: str, origin: str) -> int:
        """Replace lineage edges on every sink."""
        return self._fan_out(lambda sink: sink.record_lineage(edges, ctx, target=target, origin=origin))

    def record_registration(self, records: Sequence[RegistrationRecord], ctx: PublishContext) -> int:
        """Insert registrations on every sink."""
        return self._fan_out(lambda sink: sink.record_registration(records, ctx))

    def record_deployment(self, record: DeploymentRecord, ctx: PublishContext) -> int:
        """Insert a deployment on every sink."""
        return self._fan_out(lambda sink: sink.record_deployment(record, ctx))

    def record_run(self, run: RunRecord, ctx: PublishContext) -> int:
        """Insert a run on every sink."""
        return self._fan_out(lambda sink: sink.record_run(run, ctx))

    def record_quality(self, results: Sequence[QualityResultRecord], ctx: PublishContext) -> int:
        """Insert quality results on every sink."""
        return self._fan_out(lambda sink: sink.record_quality(results, ctx))

    def record_evals(self, results: Sequence[EvalResultRecord], ctx: PublishContext) -> int:
        """Insert evaluation results on every sink."""
        return self._fan_out(lambda sink: sink.record_evals(results, ctx))

    def record_operations(self, batch: OperationsBatch, ctx: PublishContext) -> int:
        """Insert operational facts on every sink."""
        return self._fan_out(lambda sink: sink.record_operations(batch, ctx))

    def record_costs(self, records: Sequence[CostRecord], ctx: PublishContext) -> int:
        """Insert daily cost rows on every sink."""
        return self._fan_out(lambda sink: sink.record_costs(records, ctx))

    def log_publish(self, entry: PublishLogEntry) -> None:
        """Append a publish-log event on every sink."""
        self._fan_out(lambda sink: sink.log_publish(entry))

    def cleanup_orphans(self) -> int:
        """Clean orphans on every sink; return the primary sink's count."""
        return self._fan_out(lambda sink: sink.cleanup_orphans())

    def purge_runtime(self, retention_days: int) -> int:
        """Purge old runtime facts on every sink."""
        return self._fan_out(lambda sink: sink.purge_runtime(retention_days))
