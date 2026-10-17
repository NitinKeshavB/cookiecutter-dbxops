"""
``BaseRepository``: the publish semantics every concrete repository shares (Template Method).

The SCD2 decision rules, the retire flow, the validation of derived sets and the mapping of facts onto rows are the
same for every backend. A concrete repository only supplies how to write one entity, one replace-set and a batch of
idempotent rows, and how to read a definition and clean orphans.
"""

from __future__ import annotations

from abc import (
    ABC,
    abstractmethod,
)
from typing import Sequence

from dbxops.metastore import rows as row_builders
from dbxops.metastore.model import ENTITIES
from dbxops.metastore.records import (
    CostRecord,
    DefinitionRecord,
    DeploymentRecord,
    EvalResultRecord,
    LineageEdge,
    OperationsBatch,
    PublishContext,
    PublishCounts,
    PublishLogEntry,
    QualityResultRecord,
    RegistrationRecord,
    RunRecord,
    SemanticLinkRow,
    TagRow,
)
from dbxops.metastore.scd2 import retired_copy


class BaseRepository(ABC):
    """Common behaviour of the SQLAlchemy and Unity Catalog repositories."""

    @abstractmethod
    def get_current_definition(self, kind: str, entity_id: str) -> DefinitionRecord | None:
        """Return the current version of an entity with its child rows, or ``None``."""

    @abstractmethod
    def _upsert_one(self, record: DefinitionRecord, ctx: PublishContext) -> PublishCounts:
        """Publish one entity."""

    @abstractmethod
    def _cleanup_kind(self, kind: str) -> int:
        """Delete the orphan child rows of one entity kind; return how many were removed."""

    @abstractmethod
    def _replace_derived(self, derived: row_builders.DerivedSet, ctx: PublishContext) -> int:
        """Apply one replace-set of Pattern B rows."""

    @abstractmethod
    def _insert_absent(self, rows: list[row_builders.FactRow]) -> int:
        """Insert Pattern C rows whose idempotency key is absent; return how many were inserted."""

    def upsert_definitions(self, records: Sequence[DefinitionRecord], ctx: PublishContext) -> PublishCounts:
        """Publish definitions: orphan cleanup of the touched kinds first, then one entity at a time."""
        kinds = list(dict.fromkeys(record.kind for record in records))
        counts = PublishCounts(orphans_cleaned=sum(self._cleanup_kind(kind) for kind in kinds))
        for record in records:
            counts += self._upsert_one(record, ctx)
        return counts

    def retire_definitions(
        self,
        kind: str,
        entity_ids: Sequence[str],
        ctx: PublishContext,
        git_commit: str | None = None,
    ) -> PublishCounts:
        """Retire entities owned by ``ctx.repo_id``; the retired version stays current."""
        commit = git_commit if git_commit is not None else ctx.git_commit
        records = [
            retired_copy(self.get_current_definition(kind, entity_id), kind, entity_id, ctx.repo_id, commit)
            for entity_id in entity_ids
        ]
        return self.upsert_definitions(records, ctx)

    def upsert_semantic_links(self, rows: Sequence[SemanticLinkRow], ctx: PublishContext) -> int:
        """Replace the semantic links of ``ctx.repo_id``."""
        return self._replace_derived(row_builders.link_set(rows), ctx)

    def upsert_tags(self, rows: Sequence[TagRow], ctx: PublishContext, required_keys: Sequence[str] = ()) -> int:
        """Replace the owned tags of ``ctx.repo_id``; tags with other keys are rejected and never deactivated."""
        return self._replace_derived(row_builders.tag_set(rows, required_keys), ctx)

    def record_lineage(self, edges: Sequence[LineageEdge], ctx: PublishContext, *, target: str, origin: str) -> int:
        """Replace the ``origin`` lineage edges of ``ctx.repo_id`` on ``target``."""
        return self._replace_derived(row_builders.lineage_set(edges, target, origin), ctx)

    def record_registration(self, records: Sequence[RegistrationRecord], ctx: PublishContext) -> int:
        """Insert registration audit rows if absent."""
        return self._insert_absent(row_builders.registration_rows(records, ctx))

    def record_deployment(self, record: DeploymentRecord, ctx: PublishContext) -> int:
        """Insert a deployment and its assets if absent; a retried deployment inserts nothing."""
        return self._insert_absent(row_builders.deployment_rows(record, ctx))

    def record_run(self, run: RunRecord, ctx: PublishContext) -> int:
        """Insert a run, its pipeline updates and dataset metrics if absent."""
        return self._insert_absent(row_builders.run_rows(run, ctx))

    def record_quality(self, results: Sequence[QualityResultRecord], ctx: PublishContext) -> int:
        """Insert quality results if absent."""
        return self._insert_absent(row_builders.quality_rows(results, ctx))

    def record_evals(self, results: Sequence[EvalResultRecord], ctx: PublishContext) -> int:
        """Insert evaluation results if absent."""
        return self._insert_absent(row_builders.eval_rows(results, ctx))

    def record_operations(self, batch: OperationsBatch, ctx: PublishContext) -> int:
        """Insert performance, tuning, design-decision and log-event facts if absent."""
        return self._insert_absent(row_builders.operations_rows(batch, ctx))

    def record_costs(self, records: Sequence[CostRecord], ctx: PublishContext) -> int:
        """Insert daily cost rows if absent; a day already loaded is never changed."""
        return self._insert_absent(row_builders.cost_rows(records, ctx))

    def log_publish(self, entry: PublishLogEntry) -> None:
        """Append one status event of a publish to ``md_publish_log``."""
        self._insert_absent([row_builders.publish_log_row(entry)])

    def cleanup_orphans(self) -> int:
        """Delete child rows that belong to no version of any parent."""
        return sum(self._cleanup_kind(kind) for kind in ENTITIES)
