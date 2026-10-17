"""
Databricks SQL statement builders for the Unity Catalog repository.

Every builder is a pure function of the model and its arguments and returns ``(sql, params)``: named ``:param``
markers and a dict of values, which both ``databricks-sql-connector`` and ``spark.sql(sql, args=...)`` accept.
Values are cast by column type so a NULL or a string never changes a column's type, VARIANT payloads travel as JSON
text and are parsed with ``parse_json`` only inside the INSERT, and identifiers are quoted with backticks.

NOTE: the statements are checked offline by recording tests only. They follow the documented Delta Lake MERGE
semantics (``WHEN NOT MATCHED [BY SOURCE]``), but have not been run against a real SQL warehouse.
"""

from __future__ import annotations

import json
import re
from typing import (
    Any,
    Mapping,
    Sequence,
)

from sqlalchemy import (
    BigInteger,
    Boolean,
    Double,
    Integer,
    Table,
    Text,
)

from dbxops.hashing import canonical_json
from dbxops.metastore import rows as row_builders
from dbxops.metastore.errors import ConfigError
from dbxops.metastore.model import (
    TAG_TABLE,
    UC_SCHEMA_PLACEHOLDER,
    ChildSpec,
    EntitySpec,
    MetaModel,
    render_table_ddl,
    render_view_ddl,
)
from dbxops.metastore.performance import PERFORMANCE_COLUMNS
from dbxops.metastore.records import (
    OWNED_TAG_KEYS,
    OWNED_TAG_PREFIXES,
    DefinitionRecord,
    PublishContext,
)
from dbxops.metastore.scd2 import parent_values
from dbxops.metastore.types import (
    JsonType,
    UtcTimestamp,
    format_utc,
)

Statement = tuple[str, dict[str, Any]]

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_\-]+$")
_NOW = "current_timestamp()"
_SQL_TYPES: dict[type, str] = {
    Text: "STRING",
    Boolean: "BOOLEAN",
    Integer: "INT",
    BigInteger: "BIGINT",
    Double: "DOUBLE",
}


def _quote(identifier: str) -> str:
    return f"`{identifier}`"


def sql_type(column: Any) -> str:
    """Return the Databricks SQL type name used to cast values of ``column``."""
    if isinstance(column.type, UtcTimestamp):
        return "TIMESTAMP"
    if isinstance(column.type, JsonType):
        return "STRING"
    for python_type, name in _SQL_TYPES.items():
        if isinstance(column.type, python_type):
            return name
    raise ConfigError(f"no Databricks SQL type for column {column.name!r} ({column.type!r})")


def encode(column: Any, value: Any) -> Any:
    """Return the parameter value for ``column``: JSON as text, timestamps as ISO-8601 ``Z`` text."""
    if value is None:
        return None
    if isinstance(column.type, JsonType):
        return canonical_json(value)
    if isinstance(column.type, UtcTimestamp):
        return format_utc(value)
    return value


def decode(table: Table, row: Mapping[str, Any]) -> dict[str, Any]:
    """Parse the JSON text columns of a row read back from Databricks."""
    return {
        name: (json.loads(value) if isinstance(table.c[name].type, JsonType) and isinstance(value, str) else value)
        for name, value in row.items()
    }


def _owned_tag_predicate(required_keys: Sequence[str]) -> tuple[str, dict[str, Any]]:
    exact = sorted(set(OWNED_TAG_KEYS) | set(required_keys))
    params: dict[str, Any] = {f"owned_key_{i}": key for i, key in enumerate(exact)}
    clauses = [f"t.tag_key IN ({', '.join(':' + name for name in params)})"]
    for index, prefix in enumerate(OWNED_TAG_PREFIXES):
        params[f"owned_prefix_{index}"] = prefix
        clauses.append(f"startswith(t.tag_key, :owned_prefix_{index})")
    return f"({' OR '.join(clauses)})", params


def _stale_predicate(repo_id: str, derived: row_builders.DerivedSet) -> tuple[str, dict[str, Any]]:
    """Return the predicate selecting the active rows of this repo and scope that a publish may deactivate."""
    parts = ["t.repo_id = :stale_repo_id", "t.is_active = true"]
    params: dict[str, Any] = {"stale_repo_id": repo_id}
    for name, value in derived.scope.items():
        parts.append(f"t.{_quote(name)} = :scope_{name}")
        params[f"scope_{name}"] = value
    if derived.table_name == TAG_TABLE:
        owned, owned_params = _owned_tag_predicate(derived.required_keys)
        parts.append(owned)
        params.update(owned_params)
    return " AND ".join(parts), params


def _clock_columns(rows: Sequence[Mapping[str, Any]]) -> dict[str, bool]:
    """Map each column holding the DB-clock marker to whether every row does (then it is not in the source)."""
    marked = {name for row in rows for name, value in row.items() if value is row_builders.DB_NOW}
    return {name: all(row[name] is row_builders.DB_NOW for row in rows) for name in marked}


def _clock_expr(name: str, clock_columns: Mapping[str, bool]) -> str:
    """Return the INSERT expression of a column, using the database clock where the marker was given."""
    if name not in clock_columns:
        return f"s.{_quote(name)}"
    return _NOW if clock_columns[name] else f"coalesce(s.{_quote(name)}, {_NOW})"


class UcStatements:
    """Builds the Databricks SQL for one ``catalog.schema`` target."""

    def __init__(self, model: MetaModel, catalog: str, schema: str):
        for name in (catalog, schema):
            if not _IDENTIFIER.match(name):
                raise ConfigError(f"invalid Unity Catalog identifier {name!r}")
        self.model = model
        self.schema_path = f"{_quote(catalog)}.{_quote(schema)}"

    # ----------------------------------------------------------------- names

    def table_path(self, table: str) -> str:
        """Return the fully qualified, quoted name of a table or view."""
        return f"{self.schema_path}.{_quote(table)}"

    @staticmethod
    def _expr(table: Table, name: str, param: str) -> str:
        return f"CAST(:{param} AS {sql_type(table.c[name])})"

    def _qualify(self, sql: str) -> str:
        return sql.replace(f"{UC_SCHEMA_PLACEHOLDER}.", f"{self.schema_path}.")

    # ------------------------------------------------------------------- DDL

    @staticmethod
    def session() -> Statement:
        """Return the statement that pins the session time zone to UTC."""
        return "SET TIME ZONE 'UTC'", {}

    def schema_ddl(self) -> list[str]:
        """Return the statements that create the schema, the tables (with change data feed) and the views."""
        statements = [f"CREATE SCHEMA IF NOT EXISTS {self.schema_path}"]
        statements += [self._qualify(ddl) for ddl in render_table_ddl(self.model, "databricks")]
        statements += [
            f"ALTER TABLE {self.table_path(name)} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)"
            for name in self.model.tables
        ]
        return statements

    def view_ddl(self) -> list[str]:
        """Return the ``CREATE OR REPLACE VIEW`` statements."""
        return [self._qualify(ddl) for ddl in render_view_ddl(self.model, "databricks")]

    def schema_version_statements(self) -> tuple[Statement, Statement]:
        """Return the statement that reads the schema version and the one that records version 1."""
        table = self.table_path("md_schema_version")
        return (
            (f"SELECT max(version) AS version FROM {table}", {}),
            (
                f"INSERT INTO {table} (version, description, applied_at) VALUES (:version, :description, {_NOW})",
                {"version": 1, "description": "initial schema"},
            ),
        )

    # ------------------------------------------------------------ definitions

    def select_current(self, spec: EntitySpec, entity_id: str) -> Statement:
        """Return the statement that reads the header of the current version of an entity."""
        return (
            "SELECT entity_id, record_version, repo_id, semver, status, content_hash "
            f"FROM {self.table_path(spec.table)} WHERE entity_id = :entity_id AND is_current = true",
            {"entity_id": entity_id},
        )

    def count_current(self, spec: EntitySpec, entity_id: str) -> Statement:
        """Return the statement behind the at-most-one-current-row invariant check."""
        return (
            f"SELECT count(*) AS n FROM {self.table_path(spec.table)} "
            "WHERE entity_id = :entity_id AND is_current = true",
            {"entity_id": entity_id},
        )

    def touch(self, spec: EntitySpec, record: DefinitionRecord, published_by: str) -> Statement:
        """Return the statement for an unchanged hash: only the last-seen commit and publish audit move."""
        sets = [f"published_at = {_NOW}", "published_by = :published_by"]
        params: dict[str, Any] = {"entity_id": record.entity_id, "published_by": published_by}
        if record.git_commit is not None:
            sets.append("last_seen_commit = :git_commit")
            params["git_commit"] = record.git_commit
        return (
            f"UPDATE {self.table_path(spec.table)} SET {', '.join(sets)} "
            "WHERE entity_id = :entity_id AND is_current = true",
            params,
        )

    def insert_children(
        self, child: ChildSpec, entity_id: str, version: int, rows: Sequence[Mapping[str, Any]]
    ) -> Statement:
        """Return one multi-row INSERT of the child snapshot at ``version`` (invisible until the parent MERGE)."""
        table = self.model.table(child.table)
        names = [child.parent_column, "record_version", *rows[0]]
        params: dict[str, Any] = {}
        tuples = []
        for index, row in enumerate(rows):
            values = {child.parent_column: entity_id, "record_version": version, **row}
            for name in names:
                params[f"p{index}_{name}"] = encode(table.c[name], values[name])
            tuples.append("(" + ", ".join(self._expr(table, name, f"p{index}_{name}") for name in names) + ")")
        columns = ", ".join(_quote(name) for name in names)
        return (f"INSERT INTO {self.table_path(child.table)} ({columns}) VALUES {', '.join(tuples)}", params)

    def merge_parent(
        self, spec: EntitySpec, record: DefinitionRecord, previous_version: int, ctx: PublishContext
    ) -> Statement:
        """
        Return the one MERGE that closes version N and inserts N+1 in a single Delta commit.

        The source pairs a key row (merge key = entity id, which closes N) with an insert row (merge key NULL, which
        never matches and so is inserted). For a new entity (``previous_version`` 0) nothing matches and only the
        insert happens.
        """
        parent = self.model.table(spec.table)
        values = parent_values(spec, record, previous_version + 1, ctx.published_by)
        params = {name: encode(parent.c[name], value) for name, value in values.items()}
        params["previous_version"] = previous_version
        selects = ", ".join(f"{self._expr(parent, name, name)} AS {_quote(name)}" for name in values)
        columns = [_quote(name) for name in values] + ["valid_from", "valid_to", "is_current", "published_at"]
        inserts = [self._insert_expr(parent, name) for name in values]
        inserts += [_NOW, "CAST(NULL AS TIMESTAMP)", "true", _NOW]
        sql = (
            f"MERGE INTO {self.table_path(spec.table)} AS t USING ("
            "SELECT k.merge_key, d.* FROM "
            "(SELECT CAST(:entity_id AS STRING) AS merge_key UNION ALL SELECT CAST(NULL AS STRING)) AS k "
            f"CROSS JOIN (SELECT {selects}) AS d) AS s "
            "ON t.entity_id = s.merge_key AND t.is_current = true AND t.record_version = :previous_version "
            f"WHEN MATCHED THEN UPDATE SET t.is_current = false, t.valid_to = {_NOW} "
            f"WHEN NOT MATCHED AND s.merge_key IS NULL THEN INSERT ({', '.join(columns)}) "
            f"VALUES ({', '.join(inserts)})"
        )
        return sql, params

    @staticmethod
    def _insert_expr(table: Table, name: str) -> str:
        reference = f"s.{_quote(name)}"
        return f"parse_json({reference})" if isinstance(table.c[name].type, JsonType) else reference

    def delete_orphans(self, spec: EntitySpec, child: ChildSpec) -> Statement:
        """Return the DELETE of child rows no version of the parent can show (children of a crashed publish)."""
        parent, table = self.table_path(spec.table), self.table_path(child.table)
        return (
            f"DELETE FROM {table} WHERE NOT EXISTS (SELECT 1 FROM {parent} p "
            f"WHERE p.entity_id = {table}.{_quote(child.parent_column)} "
            f"AND p.record_version >= {table}.record_version)",
            {},
        )

    def select_definition(self, spec: EntitySpec, entity_id: str) -> Statement:
        """Return the statement that reads the whole current parent row."""
        table = self.model.table(spec.table)
        return (
            f"SELECT {self._select_list(table)} FROM {self.table_path(spec.table)} "
            "WHERE entity_id = :entity_id AND is_current = true",
            {"entity_id": entity_id},
        )

    def select_children(self, child: ChildSpec, entity_id: str, version: int) -> Statement:
        """Return the statement that reads the child rows of one version."""
        table = self.model.table(child.table)
        return (
            f"SELECT {self._select_list(table)} FROM {self.table_path(child.table)} "
            f"WHERE {_quote(child.parent_column)} = :entity_id AND record_version = :version",
            {"entity_id": entity_id, "version": version},
        )

    @staticmethod
    def _select_list(table: Table) -> str:
        return ", ".join(
            (
                f"CAST({_quote(c.name)} AS STRING) AS {_quote(c.name)}"
                if isinstance(c.type, JsonType)
                else _quote(c.name)
            )
            for c in table.c
        )

    def list_entity_ids(self, spec: EntitySpec, repo_id: str, include_retired: bool) -> Statement:
        """Return the statement listing the current entity ids of a repo."""
        retired = "" if include_retired else " AND status <> 'retired'"
        return (
            f"SELECT entity_id FROM {self.table_path(spec.table)} "
            f"WHERE repo_id = :repo_id AND is_current = true{retired} ORDER BY entity_id",
            {"repo_id": repo_id},
        )

    def quality_rules(self, repo_id: str) -> Statement:
        """Return the statement listing the quality rules of a repo's current contracts."""
        rule, contract = self.table_path("md_quality_rule"), self.table_path("md_data_contract")
        obj = self.table_path("md_contract_object")
        return (
            "SELECT r.contract_id, r.record_version, r.rule_id, coalesce(o.asset_id, '') AS asset_id "
            f"FROM {rule} r JOIN {contract} c ON c.entity_id = r.contract_id AND c.record_version = r.record_version "
            f"LEFT JOIN {obj} o ON o.contract_id = r.contract_id AND o.record_version = r.record_version "
            "AND o.object_name = r.object_name WHERE c.is_current = true AND c.repo_id = :repo_id",
            {"repo_id": repo_id},
        )

    # --------------------------------------------------------- facts (Pattern C)

    def merge_insert_absent(self, table_name: str, rows: Sequence[Mapping[str, Any]]) -> Statement:
        """Return one MERGE that inserts the rows whose idempotency key is absent."""
        table = self.model.table(table_name)
        keys = [column.name for column in table.primary_key.columns]
        source, params, clock_columns = self._source(table, rows)
        names = list(rows[0])
        inserts = [_clock_expr(name, clock_columns) for name in names]
        on = " AND ".join(f"t.{_quote(key)} = s.{_quote(key)}" for key in keys)
        sql = (
            f"MERGE INTO {self.table_path(table_name)} AS t USING {source} ON {on} "
            f"WHEN NOT MATCHED THEN INSERT ({', '.join(_quote(n) for n in names)}) VALUES ({', '.join(inserts)})"
        )
        return sql, params

    def _source(self, table: Table, rows: Sequence[Mapping[str, Any]]) -> tuple[str, dict[str, Any], dict[str, bool]]:
        """Return the ``(VALUES ...) AS s(...)`` source, its params, and the clock columns."""
        clock_columns = _clock_columns(rows)
        names = [name for name in rows[0] if not clock_columns.get(name, False)]
        params: dict[str, Any] = {}
        tuples = []
        for index, row in enumerate(rows):
            for name in names:
                value = None if row[name] is row_builders.DB_NOW else row[name]
                params[f"p{index}_{name}"] = encode(table.c[name], value)
            tuples.append("(" + ", ".join(self._expr(table, name, f"p{index}_{name}") for name in names) + ")")
        alias = ", ".join(_quote(name) for name in names)
        return f"(VALUES {', '.join(tuples)}) AS s({alias})", params, clock_columns

    # --------------------------------------------------- derived (Pattern B) rows

    def foreign_rows(self, derived: row_builders.DerivedSet, repo_id: str) -> Statement:
        """Return the statement that finds desired keys already owned by another repo."""
        table = self.model.table(derived.table_name)
        keys = derived.key_names
        key_items = [{name: item[name] for name in keys} for item in derived.items]
        source, params, _ = self._source(table, key_items)
        on = " AND ".join(f"t.{_quote(key)} = s.{_quote(key)}" for key in keys)
        params["repo_id"] = repo_id
        selected = ", ".join(f"t.{_quote(key)}" for key in keys)
        return (
            f"SELECT {selected}, t.repo_id FROM {self.table_path(derived.table_name)} t JOIN {source} ON {on} "
            "WHERE t.repo_id <> :repo_id LIMIT 1",
            params,
        )

    def merge_derived(self, derived: row_builders.DerivedSet, ctx: PublishContext) -> Statement:
        """
        Return one MERGE that upserts the desired rows and deactivates the stale ones of the same repo and scope.

        For ``md_tag`` only owned tag keys are ever deactivated.
        """
        table = self.model.table(derived.table_name)
        names = list(derived.items[0])
        source, params, _ = self._source(table, derived.items)
        on = " AND ".join(f"t.{_quote(key)} = s.{_quote(key)}" for key in derived.key_names)
        updates = [f"t.{_quote(name)} = s.{_quote(name)}" for name in derived.data_names]
        updates += ["t.is_active = true", f"t.last_seen_at = {_NOW}"]
        columns = [_quote(name) for name in names] + ["repo_id", "is_active", "first_seen_at", "last_seen_at"]
        values = [f"s.{_quote(name)}" for name in names] + [":repo_id", "true", _NOW, _NOW]
        stale, stale_params = _stale_predicate(ctx.repo_id, derived)
        params.update(stale_params)
        params["repo_id"] = ctx.repo_id
        sql = (
            f"MERGE INTO {self.table_path(derived.table_name)} AS t USING {source} ON {on} "
            f"WHEN MATCHED THEN UPDATE SET {', '.join(updates)} "
            f"WHEN NOT MATCHED THEN INSERT ({', '.join(columns)}) VALUES ({', '.join(values)}) "
            f"WHEN NOT MATCHED BY SOURCE AND {stale} THEN UPDATE SET t.is_active = false"
        )
        return sql, params

    def deactivate_derived(self, derived: row_builders.DerivedSet, ctx: PublishContext) -> Statement:
        """Return the UPDATE that deactivates everything in scope; used when a publisher sends an empty set."""
        stale, params = _stale_predicate(ctx.repo_id, derived)
        return (f"UPDATE {self.table_path(derived.table_name)} AS t SET is_active = false WHERE {stale}", params)

    # --------------------------------------------------------------- upkeep

    def purge(self, table_name: str, retention_days: int) -> Statement:
        """Return the DELETE of facts older than the retention window."""
        return (
            f"DELETE FROM {self.table_path(table_name)} "
            f"WHERE recorded_at < {_NOW} - INTERVAL {int(retention_days)} DAYS",
            {},
        )


def recent_performance_statement(table_path: str, repo_id: str, target: str, limit: int) -> Statement:
    """Return the statement listing a repo's newest performance facts on one target from ``table_path``."""
    columns = ", ".join(_quote(name) for name in PERFORMANCE_COLUMNS)
    return (
        f"SELECT {columns} FROM {table_path} "
        "WHERE repo_id = :repo_id AND target = :target ORDER BY recorded_at DESC, update_id DESC "
        f"LIMIT {int(limit)}",
        {"repo_id": repo_id, "target": target},
    )
