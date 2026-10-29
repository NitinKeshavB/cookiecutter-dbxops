"""
``MetadataPublisher``: the facade over collectors, the writer guard and a repository.

Callers hand it collected records. It wraps the repository in a ``WriterGuard`` for its ``repo_id``, applies the
records idempotently, and brackets every publish with ``md_publish_log`` events (``started`` then ``complete`` or
``failed``, with row counts) so each publish is audited.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import (
    Callable,
    Sequence,
)

from dbxops.metastore.errors import CollectError
from dbxops.metastore.guard import WriterGuard
from dbxops.metastore.performance import performance_from_run
from dbxops.metastore.records import (
    CostRecord,
    DefinitionRecord,
    DeploymentRecord,
    LineageEdge,
    OperationsBatch,
    PublishContext,
    PublishCounts,
    PublishLogEntry,
    RegistrationRecord,
    SemanticLinkRow,
    TagRow,
)
from dbxops.metastore.repository import MetadataRepository
from dbxops.metastore.rows import (
    PUBLISH_COMPLETE,
    PUBLISH_FAILED,
    PUBLISH_STARTED,
)
from dbxops.metastore.runtime_collectors import RunCollection


def dedupe_records(records: Sequence[DefinitionRecord]) -> list[DefinitionRecord]:
    """Drop repeated records of the same entity (e.g. an asset found in a contract and a product)."""
    kept: dict[tuple[str, str], DefinitionRecord] = {}
    for record in records:
        key = (record.kind, record.entity_id)
        earlier = kept.get(key)
        if earlier is None:
            kept[key] = record
        elif earlier.content_hash != record.content_hash:
            raise CollectError(f"{record.kind} {record.entity_id!r} is defined twice with different content")
    return list(kept.values())


class MetadataPublisher:
    """Publishes collected metadata for one repo to one repository, idempotently and with an audit trail."""

    def __init__(self, repository: MetadataRepository, context: PublishContext, required_tag_keys: Sequence[str] = ()):
        """
        Create a publisher.

        ``context`` names the writer (``repo_id``), the target, who publishes and the commit; each publish gets a
        fresh publish id. ``required_tag_keys`` are the project tag keys the registry owns in ``md_tag``.
        """
        self.repo_id = context.repo_id
        self.target = context.target
        self.context = context
        self._required_tag_keys = tuple(required_tag_keys)
        self._repository = WriterGuard(repository, context.repo_id)

    def _publish(self, what: str, action: Callable[[PublishContext], PublishCounts]) -> PublishCounts:
        ctx = replace(self.context, publish_id=uuid.uuid4().hex)
        self._repository.log_publish(PublishLogEntry(ctx, what, PUBLISH_STARTED))
        try:
            counts = action(ctx)
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            self._repository.log_publish(PublishLogEntry(ctx, what, PUBLISH_FAILED, message=message))
            raise
        self._repository.log_publish(PublishLogEntry(ctx, what, PUBLISH_COMPLETE, counts))
        return counts

    def ensure_schema(self) -> None:
        """Create or check the schema of the repository."""
        self._repository.ensure_schema()

    def publish_definitions(
        self,
        records: Sequence[DefinitionRecord],
        *,
        links: Sequence[SemanticLinkRow] | None = None,
        tags: Sequence[TagRow] | None = None,
        retire_missing: Sequence[str] = (),
    ) -> PublishCounts:
        """
        Publish definitions, and optionally the full link and tag sets of this repo.

        ``links`` and ``tags`` replace what the repo published before (missing ones are deactivated), so pass the
        complete set or ``None`` to leave them alone. For each kind in ``retire_missing``, current entities of this
        repo that are absent from ``records`` are retired; use it only when ``records`` is the complete set.
        """

        def action(ctx: PublishContext) -> PublishCounts:
            unique = dedupe_records(records)
            counts = self._repository.upsert_definitions(unique, ctx)
            for kind in retire_missing:
                counts += self._retire_missing(kind, {r.entity_id for r in unique if r.kind == kind}, ctx)
            if links is not None:
                self._repository.upsert_semantic_links(links, ctx)
            if tags is not None:
                self._repository.upsert_tags(tags, ctx, self._required_tag_keys)
            return counts

        return self._publish("definitions", action)

    def _retire_missing(self, kind: str, present: set[str], ctx: PublishContext) -> PublishCounts:
        current = self._repository.list_current_entity_ids(kind, self.repo_id)
        missing = [entity_id for entity_id in current if entity_id not in present]
        return self._repository.retire_definitions(kind, missing, ctx) if missing else PublishCounts()

    def publish_registrations(self, records: Sequence[RegistrationRecord]) -> PublishCounts:
        """Append registration audit rows (registry only)."""
        return self._publish(
            "registrations", lambda ctx: PublishCounts(inserted=self._repository.record_registration(records, ctx))
        )

    def publish_deployment(self, record: DeploymentRecord) -> PublishCounts:
        """Record a deployment; a retry of the same CI run inserts nothing."""
        return self._publish(
            "deployments", lambda ctx: PublishCounts(inserted=self._repository.record_deployment(record, ctx))
        )

    def publish_lineage(self, edges: Sequence[LineageEdge], origin: str) -> PublishCounts:
        """Replace this repo's lineage edges of one origin on the publisher's target."""
        return self._publish(
            "lineage",
            lambda ctx: PublishCounts(
                inserted=self._repository.record_lineage(edges, ctx, target=self.target, origin=origin)
            ),
        )

    def publish_run(self, collection: RunCollection) -> PublishCounts:
        """Record a collected run with its quality results."""

        def action(ctx: PublishContext) -> PublishCounts:
            inserted = self._repository.record_run(collection.run, ctx)
            inserted += self._repository.record_quality(collection.quality, ctx)
            batch = OperationsBatch(performance=performance_from_run(collection.run))
            inserted += self._repository.record_operations(batch, ctx)
            return PublishCounts(inserted=inserted)

        return self._publish("runs", action)

    def publish_operations(self, batch: OperationsBatch) -> PublishCounts:
        """Record operational facts (tuning recommendations and decisions, design decisions, log events)."""
        return self._publish(
            "operations", lambda ctx: PublishCounts(inserted=self._repository.record_operations(batch, ctx))
        )

    def publish_costs(self, records: Sequence[CostRecord]) -> PublishCounts:
        """Record daily costs (registry only); days already loaded are left unchanged."""
        return self._publish("costs", lambda ctx: PublishCounts(inserted=self._repository.record_costs(records, ctx)))

    def cleanup_orphans(self) -> int:
        """Delete orphan child rows left by a crashed publish."""
        return self._repository.cleanup_orphans()
