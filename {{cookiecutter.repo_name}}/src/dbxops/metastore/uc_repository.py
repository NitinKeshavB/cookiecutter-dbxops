"""
``UcRepository``: the metadata repository for Unity Catalog Delta tables.

Statements go through an injected ``SqlExecutor`` (Strategy), so the same repository runs off-cluster through
``databricks-sql-connector`` (``DatabricksSqlConnectorExecutor``) and on-cluster through ``spark.sql``
(``SparkSqlExecutor``). Tests use a recording fake; no network or Spark is ever touched here.

Publish atomicity (plan D3): each UC statement is its own Delta commit, so one entity is published as
(1) children at version N+1, which no view shows because views join children on the current version, then
(2) ONE parent MERGE that closes N and inserts N+1. A crash between the two leaves orphan children; the next
publish deletes them before writing.
"""

from __future__ import annotations

import importlib
from typing import (
    Any,
    Callable,
    Mapping,
    Protocol,
)

from dbxops.metastore import rows as row_builders
from dbxops.metastore.base_repository import BaseRepository
from dbxops.metastore.errors import (
    PublishError,
    SchemaError,
    WriterError,
)
from dbxops.metastore.model import (
    DEFAULT_SCHEMA,
    RUNTIME_TABLES,
    SCHEMA_VERSION,
    UC_SCHEMA_PLACEHOLDER,
    EntitySpec,
    build_model,
    entity_spec,
)
from dbxops.metastore.performance import performance_record
from dbxops.metastore.records import (
    DefinitionRecord,
    PerformanceRecord,
    PublishContext,
    PublishCounts,
    QualityRuleRef,
)
from dbxops.metastore.scd2 import (
    Action,
    CurrentRow,
    decide,
    normalised_children,
    record_from_rows,
)
from dbxops.metastore.uc_sql import (
    Statement,
    UcStatements,
    decode,
    recent_performance_statement,
)

Row = dict[str, Any]


class SqlExecutor(Protocol):
    """Runs one SQL statement with named ``:param`` markers and returns its result rows as dicts."""

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> list[Row]:
        """Execute ``sql`` and return the rows it produced (DML returns Delta's ``num_*_rows`` summary row)."""


class DatabricksSqlConnectorExecutor:
    """Executes statements on a SQL warehouse through ``databricks-sql-connector`` (the ``uc`` extra)."""

    def __init__(
        self,
        server_hostname: str,
        warehouse_id: str,
        *,
        connection_options: Mapping[str, Any] | None = None,
        connect: Callable[..., Any] | None = None,
    ):
        """
        Prepare a connection to ``warehouse_id``.

        ``connection_options`` are extra ``databricks.sql.connect`` arguments, e.g. ``access_token`` or
        ``credentials_provider``. ``connect`` replaces ``databricks.sql.connect`` (for tests).
        """
        self._connection_args: dict[str, Any] = {
            "server_hostname": server_hostname,
            "http_path": f"/sql/1.0/warehouses/{warehouse_id}",
            **(connection_options or {}),
        }
        self._connect = connect
        self._connection: Any = None

    def _conn(self) -> Any:
        if self._connection is None:
            connect = self._connect or importlib.import_module("databricks.sql").connect
            self._connection = connect(**self._connection_args)
        return self._connection

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> list[Row]:
        """Execute ``sql`` with native named parameters and return the rows as dicts."""
        with self._conn().cursor() as cursor:
            cursor.execute(sql, dict(params) if params else None)
            if cursor.description is None:
                return []
            names = [column[0] for column in cursor.description]
            return [dict(zip(names, row)) for row in cursor.fetchall()]

    def close(self) -> None:
        """Close the warehouse connection, if one was opened."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None


class SparkSqlExecutor:
    """Executes statements through an active ``SparkSession`` (on-cluster, e.g. in a post-run job task)."""

    def __init__(self, spark: Any):
        self._spark = spark

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> list[Row]:
        """Run ``spark.sql(sql, args=params)`` and return the collected rows as dicts."""
        frame = self._spark.sql(sql, args=dict(params)) if params else self._spark.sql(sql)
        return [row.asDict() for row in frame.collect()]


class UcRepository(BaseRepository):
    """A metadata repository over Unity Catalog Delta tables, written through a ``SqlExecutor``."""

    def __init__(self, executor: SqlExecutor, catalog: str, schema: str = DEFAULT_SCHEMA):
        self._executor = executor
        self._model = build_model(UC_SCHEMA_PLACEHOLDER)
        self._sql = UcStatements(self._model, catalog, schema)
        self._session_ready = False

    # ---------------------------------------------------------------- plumbing

    def _run(self, statement: Statement) -> list[Row]:
        if not self._session_ready:
            self._session_ready = True
            self._executor.execute(*self._sql.session())
        return self._executor.execute(statement[0], statement[1])

    @staticmethod
    def _affected(rows: list[Row], key: str) -> int:
        return int(rows[0].get(key, 0)) if rows else 0

    # ------------------------------------------------------------------ schema

    def ensure_schema(self) -> None:
        """Create the schema, tables (change data feed on) and views, and check ``md_schema_version``."""
        for sql in self._sql.schema_ddl():
            self._run((sql, {}))
        read, record = self._sql.schema_version_statements()
        found = self._run(read)
        version = found[0]["version"] if found else None
        if version is not None and version > SCHEMA_VERSION:
            raise SchemaError(f"database schema version {version} is newer than this library's {SCHEMA_VERSION}")
        if version is None:
            self._run(record)
        for sql in self._sql.view_ddl():
            self._run((sql, {}))

    # ------------------------------------------------------------- definitions

    def _upsert_one(self, record: DefinitionRecord, ctx: PublishContext) -> PublishCounts:
        spec = entity_spec(record.kind)
        children = normalised_children(self._model, spec, record)
        current = self._current(spec, record.entity_id)
        if decide(spec, record, current) is Action.TOUCH:
            self._run(self._sql.touch(spec, record, ctx.published_by))
            return PublishCounts(unchanged=1)
        previous = 0 if current is None else current.record_version
        for child in spec.children:
            if children[child.table]:
                self._run(self._sql.insert_children(child, record.entity_id, previous + 1, children[child.table]))
        self._run(self._sql.merge_parent(spec, record, previous, ctx))
        self._assert_single_current(spec, record.entity_id)
        return PublishCounts(inserted=1, closed=0 if current is None else 1)

    def _current(self, spec: EntitySpec, entity_id: str) -> CurrentRow | None:
        found = self._run(self._sql.select_current(spec, entity_id))
        if len(found) > 1:
            raise PublishError(f"{entity_id} has {len(found)} current rows; the one-current-row invariant is broken")
        return CurrentRow(**found[0]) if found else None

    def _assert_single_current(self, spec: EntitySpec, entity_id: str) -> None:
        count = self._affected(self._run(self._sql.count_current(spec, entity_id)), "n")
        if count != 1:
            raise PublishError(f"{entity_id} has {count} current rows after publish; expected exactly 1")

    def get_current_definition(self, kind: str, entity_id: str) -> DefinitionRecord | None:
        """Return the current version of an entity with its child rows, or ``None``."""
        spec = entity_spec(kind)
        found = self._run(self._sql.select_definition(spec, entity_id))
        if not found:
            return None
        parent = decode(self._model.table(spec.table), found[0])
        children = {
            child.table: [
                decode(self._model.table(child.table), row)
                for row in self._run(self._sql.select_children(child, entity_id, parent["record_version"]))
            ]
            for child in spec.children
        }
        return record_from_rows(spec, parent, children)

    def list_current_entity_ids(self, kind: str, repo_id: str, include_retired: bool = False) -> list[str]:
        """Return the entity ids whose current version belongs to ``repo_id``."""
        statement = self._sql.list_entity_ids(entity_spec(kind), repo_id, include_retired)
        return [row["entity_id"] for row in self._run(statement)]

    def current_quality_rules(self, repo_id: str) -> list[QualityRuleRef]:
        """Return the quality rules of the current contracts of ``repo_id``, with the asset each applies to."""
        return [QualityRuleRef(**row) for row in self._run(self._sql.quality_rules(repo_id))]

    def recent_performance(self, repo_id: str, target: str, limit: int = 500) -> list[PerformanceRecord]:
        """Return the newest ``limit`` performance facts of ``repo_id`` on ``target``, newest first."""
        return [
            performance_record(row)
            for row in self._run(
                recent_performance_statement(self._sql.table_path("md_run_performance"), repo_id, target, limit)
            )
        ]

    # -------------------------------------------------- derived (Pattern B) rows

    def _replace_derived(self, derived: row_builders.DerivedSet, ctx: PublishContext) -> int:
        if not derived.items:
            self._run(self._sql.deactivate_derived(derived, ctx))
            return 0
        foreign = self._run(self._sql.foreign_rows(derived, ctx.repo_id))
        if foreign:
            raise WriterError(f"{derived.table_name} row {foreign[0]} is owned by another repo than {ctx.repo_id!r}")
        self._run(self._sql.merge_derived(derived, ctx))
        return len(derived.items)

    # ---------------------------------------------------------- facts (Pattern C)

    def _insert_absent(self, rows: list[row_builders.FactRow]) -> int:
        grouped: dict[str, list[dict]] = {}
        for table_name, values in rows:
            grouped.setdefault(table_name, []).append(values)
        inserted = 0
        for table_name, items in grouped.items():
            found = self._run(self._sql.merge_insert_absent(table_name, items))
            inserted += self._affected(found, "num_inserted_rows")
        return inserted

    # ------------------------------------------------------------------ upkeep

    def _cleanup_kind(self, kind: str) -> int:
        spec = entity_spec(kind)
        removed = 0
        for child in spec.children:
            removed += self._affected(self._run(self._sql.delete_orphans(spec, child)), "num_affected_rows")
        return removed

    def purge_runtime(self, retention_days: int) -> int:
        """Delete runtime facts older than ``retention_days``."""
        removed = 0
        for name in RUNTIME_TABLES:
            removed += self._affected(self._run(self._sql.purge(name, retention_days)), "num_affected_rows")
        return removed
