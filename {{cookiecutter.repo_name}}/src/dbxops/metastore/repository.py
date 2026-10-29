"""
The ``MetadataRepository`` protocol: everything a publisher needs from a metadata database.

Implementations: ``SqlAlchemyRepository`` (PostgreSQL, Lakebase, SQLite), ``UcRepository`` (Unity Catalog Delta
through an injected SQL executor), ``CompositeRepository`` (fan-out) and ``WriterGuard`` (a protecting proxy).
All of them follow the same semantics, from plan sections D2 and D3:

* Pattern A definitions are versioned (SCD2). An unchanged content hash only moves ``last_seen_commit``; a changed
  hash closes the current version and inserts the next; retiring inserts a new version with status ``retired`` that
  stays current; a retired entity that reappears gets a new version.
* Pattern B rows (links, tags, lineage edges) are replace-sets: what a publisher no longer sends is deactivated.
* Pattern C rows (facts) are inserted if absent, on an idempotency key.
"""

from __future__ import annotations

from typing import (
    Protocol,
    Sequence,
    runtime_checkable,
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


@runtime_checkable
class MetadataRepository(Protocol):
    """A metadata database sink."""

    def ensure_schema(self) -> None:
        """Create missing tables, check ``md_schema_version`` and (re)create the views."""

    def upsert_definitions(self, records: Sequence[DefinitionRecord], ctx: PublishContext) -> PublishCounts:
        """Publish Pattern A definitions; each entity is published atomically in the sense of plan D3."""

    def retire_definitions(
        self,
        kind: str,
        entity_ids: Sequence[str],
        ctx: PublishContext,
        git_commit: str | None = None,
    ) -> PublishCounts:
        """Retire entities owned by ``ctx.repo_id``: a new version with status ``retired``, which stays current."""

    def get_current_definition(self, kind: str, entity_id: str) -> DefinitionRecord | None:
        """Return the current version of an entity with its child rows, or ``None``."""

    def list_current_entity_ids(self, kind: str, repo_id: str, include_retired: bool = False) -> list[str]:
        """Return the entity ids whose current version belongs to ``repo_id``."""

    def current_quality_rules(self, repo_id: str) -> list[QualityRuleRef]:
        """Return the quality rules of the current contracts of ``repo_id``, with the asset each applies to."""

    def recent_performance(self, repo_id: str, target: str, limit: int = 500) -> list[PerformanceRecord]:
        """Return the newest ``limit`` performance facts of ``repo_id`` on ``target``, newest first."""

    def upsert_semantic_links(self, rows: Sequence[SemanticLinkRow], ctx: PublishContext) -> int:
        """Replace the semantic links of ``ctx.repo_id``: upsert ``rows`` and deactivate the rest."""

    def upsert_tags(self, rows: Sequence[TagRow], ctx: PublishContext, required_keys: Sequence[str] = ()) -> int:
        """Replace the tags of ``ctx.repo_id``; only owned keys are ever deactivated, and only owned keys accepted."""

    def record_lineage(self, edges: Sequence[LineageEdge], ctx: PublishContext, *, target: str, origin: str) -> int:
        """Replace the ``origin`` lineage edges of ``ctx.repo_id`` on ``target``."""

    def record_registration(self, records: Sequence[RegistrationRecord], ctx: PublishContext) -> int:
        """Insert registration audit rows if absent; return the number inserted."""

    def record_deployment(self, record: DeploymentRecord, ctx: PublishContext) -> int:
        """Insert a deployment and its assets if absent; return the number of rows inserted (0 on a retry)."""

    def record_run(self, run: RunRecord, ctx: PublishContext) -> int:
        """Insert a run, its pipeline updates and dataset metrics if absent; return the rows inserted."""

    def record_quality(self, results: Sequence[QualityResultRecord], ctx: PublishContext) -> int:
        """Insert quality results if absent; return the number inserted."""

    def record_evals(self, results: Sequence[EvalResultRecord], ctx: PublishContext) -> int:
        """Insert evaluation results if absent; return the number inserted."""

    def record_operations(self, batch: OperationsBatch, ctx: PublishContext) -> int:
        """Insert performance, tuning, design-decision and log-event facts if absent; return the number inserted."""

    def record_costs(self, records: Sequence[CostRecord], ctx: PublishContext) -> int:
        """Insert daily cost rows if absent; return the number inserted."""

    def log_publish(self, entry: PublishLogEntry) -> None:
        """Append one status event of a publish to ``md_publish_log``."""

    def cleanup_orphans(self) -> int:
        """Delete child rows that belong to no version of any parent; return how many were removed."""

    def purge_runtime(self, retention_days: int) -> int:
        """Delete runtime facts older than ``retention_days``; return how many were removed."""
