"""
``WriterGuard``: a Proxy that enforces the writer split of plan D1 before any write reaches a repository.

A publisher may insert, close or retire only rows whose ``repo_id`` equals its own, and only in the tables its kind of
writer owns. The registry repo writes as ``_platform`` and owns the organisation tables and project tags; a squad
repo writes as its ``project_id`` and never touches organisation tables. Isolation between squads in shared tables is
enforced here and audited by ``md_publish_log``, not by the database. Violations raise ``WriterError``.
"""

from __future__ import annotations

from typing import Sequence

from dbxops.metastore.errors import WriterError
from dbxops.metastore.model import (
    PLATFORM_KINDS,
    SQUAD_KINDS,
)
from dbxops.metastore.records import (
    PLATFORM_REPO_ID,
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

PROJECT_TAG_ENTITY = "project"

__all__ = ["WriterError", "WriterGuard"]


class WriterGuard:
    """Wraps a repository and rejects writes outside the publisher's ``repo_id`` and owned tables."""

    def __init__(self, inner: MetadataRepository, repo_id: str):
        self._inner = inner
        self.repo_id = repo_id
        self._is_platform = repo_id == PLATFORM_REPO_ID

    def _check_ctx(self, ctx: PublishContext) -> None:
        if ctx.repo_id != self.repo_id:
            raise WriterError(f"context repo {ctx.repo_id!r} differs from the guarded publisher {self.repo_id!r}")

    def _check_kind(self, kind: str) -> None:
        allowed = PLATFORM_KINDS if self._is_platform else SQUAD_KINDS
        if kind not in allowed:
            writer = "the registry (_platform)" if self._is_platform else f"squad repo {self.repo_id!r}"
            raise WriterError(f"{writer} may not write {kind!r} definitions")

    def _require_squad(self, what: str) -> None:
        if self._is_platform:
            raise WriterError(f"the registry (_platform) may not write {what}")

    def _require_platform(self, what: str) -> None:
        if not self._is_platform:
            raise WriterError(f"repo {self.repo_id!r} may not write {what}; only _platform may")

    # ----------------------------------------------------------- guarded writes

    def upsert_definitions(self, records: Sequence[DefinitionRecord], ctx: PublishContext) -> PublishCounts:
        """Publish definitions after checking every record's kind and ``repo_id``."""
        self._check_ctx(ctx)
        for record in records:
            self._check_kind(record.kind)
            if record.repo_id != self.repo_id:
                raise WriterError(
                    f"{record.entity_id} has repo_id {record.repo_id!r}; repo {self.repo_id!r} may only write its own"
                )
        return self._inner.upsert_definitions(records, ctx)

    def retire_definitions(
        self,
        kind: str,
        entity_ids: Sequence[str],
        ctx: PublishContext,
        git_commit: str | None = None,
    ) -> PublishCounts:
        """Retire entities of an owned kind; the repository checks each entity is owned by this repo."""
        self._check_ctx(ctx)
        self._check_kind(kind)
        return self._inner.retire_definitions(kind, entity_ids, ctx, git_commit)

    def upsert_semantic_links(self, rows: Sequence[SemanticLinkRow], ctx: PublishContext) -> int:
        """Write semantic links; only squads own them."""
        self._check_ctx(ctx)
        self._require_squad("semantic links")
        return self._inner.upsert_semantic_links(rows, ctx)

    def upsert_tags(self, rows: Sequence[TagRow], ctx: PublishContext, required_keys: Sequence[str] = ()) -> int:
        """Write tags; the registry owns ``project`` tags, squads own all other tags."""
        self._check_ctx(ctx)
        for row in rows:
            is_project_tag = row.entity_type == PROJECT_TAG_ENTITY
            if is_project_tag != self._is_platform:
                raise WriterError(f"repo {self.repo_id!r} may not write {row.entity_type!r} tags")
        return self._inner.upsert_tags(rows, ctx, required_keys)

    def record_lineage(self, edges: Sequence[LineageEdge], ctx: PublishContext, *, target: str, origin: str) -> int:
        """Write lineage edges; only squads own them."""
        self._check_ctx(ctx)
        self._require_squad("lineage edges")
        return self._inner.record_lineage(edges, ctx, target=target, origin=origin)

    def record_registration(self, records: Sequence[RegistrationRecord], ctx: PublishContext) -> int:
        """Write registration audit rows; only the registry owns them."""
        self._check_ctx(ctx)
        self._require_platform("registrations")
        return self._inner.record_registration(records, ctx)

    def record_deployment(self, record: DeploymentRecord, ctx: PublishContext) -> int:
        """Write a deployment; only squads own them."""
        self._check_ctx(ctx)
        self._require_squad("deployments")
        return self._inner.record_deployment(record, ctx)

    def record_run(self, run: RunRecord, ctx: PublishContext) -> int:
        """Write a run; only squads own them."""
        self._check_ctx(ctx)
        self._require_squad("runs")
        return self._inner.record_run(run, ctx)

    def record_quality(self, results: Sequence[QualityResultRecord], ctx: PublishContext) -> int:
        """Write quality results; only squads own them."""
        self._check_ctx(ctx)
        self._require_squad("quality results")
        return self._inner.record_quality(results, ctx)

    def record_evals(self, results: Sequence[EvalResultRecord], ctx: PublishContext) -> int:
        """Write evaluation results; only squads own them."""
        self._check_ctx(ctx)
        self._require_squad("evaluation results")
        return self._inner.record_evals(results, ctx)

    def record_operations(self, batch: OperationsBatch, ctx: PublishContext) -> int:
        """Write performance, tuning, design-decision and log-event facts; only squads own them."""
        self._check_ctx(ctx)
        self._require_squad("operational facts")
        return self._inner.record_operations(batch, ctx)

    def record_costs(self, records: Sequence[CostRecord], ctx: PublishContext) -> int:
        """Write daily cost rows; only the registry owns them, because one load covers every project."""
        self._check_ctx(ctx)
        self._require_platform("daily costs")
        return self._inner.record_costs(records, ctx)

    def log_publish(self, entry: PublishLogEntry) -> None:
        """Append a publish-log event for this publisher only."""
        if entry.ctx.repo_id != self.repo_id:
            raise WriterError(f"publish log entry for repo {entry.ctx.repo_id!r} from publisher {self.repo_id!r}")
        self._inner.log_publish(entry)

    def purge_runtime(self, retention_days: int) -> int:
        """Purge runtime facts of every repo; only the registry may."""
        self._require_platform("a runtime purge")
        return self._inner.purge_runtime(retention_days)

    # ------------------------------------------------------ pass-through access

    def ensure_schema(self) -> None:
        """Create or check the schema."""
        self._inner.ensure_schema()

    def get_current_definition(self, kind: str, entity_id: str) -> DefinitionRecord | None:
        """Read the current version of an entity."""
        return self._inner.get_current_definition(kind, entity_id)

    def list_current_entity_ids(self, kind: str, repo_id: str, include_retired: bool = False) -> list[str]:
        """List the current entity ids of a repo."""
        return self._inner.list_current_entity_ids(kind, repo_id, include_retired)

    def current_quality_rules(self, repo_id: str) -> list[QualityRuleRef]:
        """List the current quality rules of a repo."""
        return self._inner.current_quality_rules(repo_id)

    def recent_performance(self, repo_id: str, target: str, limit: int = 500) -> list[PerformanceRecord]:
        """Read a repo's recent performance facts."""
        return self._inner.recent_performance(repo_id, target, limit)

    def cleanup_orphans(self) -> int:
        """Delete orphan child rows; they belong to no version, so any writer may clean them."""
        return self._inner.cleanup_orphans()
