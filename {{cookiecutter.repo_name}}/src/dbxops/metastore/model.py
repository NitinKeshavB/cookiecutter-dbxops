"""
The metadata database model: every table of plan section D4 and the views over them.

``build_model(schema)`` returns one SQLAlchemy Core ``MetaData`` wrapped in a ``MetaModel``. The schema name is a
build argument (default ``platform_meta``): PostgreSQL and Lakebase use it as a real schema, SQLite has no schemas
so it is built with ``schema=None``, and Unity Catalog is built with a placeholder schema that the UC repository
replaces by ``catalog.schema`` when it renders statements. Building a fresh model per schema keeps every
dialect on one code path, with no ``schema_translate_map`` at execution time.

Table patterns (plan D2): A is an SCD2 definition with child snapshots keyed by ``(parent_id, record_version, ...)``,
B is derived current state with ``is_active``, and C is an append-only fact with an idempotency key.
"""

from __future__ import annotations

import importlib
import warnings
from dataclasses import dataclass
from typing import (
    Any,
    Iterable,
)

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Double,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    and_,
    case,
    exists,
    false,
    func,
    select,
    text,
    true,
)
from sqlalchemy.dialects import (
    postgresql,
    sqlite,
)
from sqlalchemy.schema import (
    CreateIndex,
    CreateTable,
)
from sqlalchemy.sql import quoted_name
from sqlalchemy.sql.expression import Select

from dbxops.metastore.errors import SchemaError
from dbxops.metastore.records import (
    ORIGIN_SPEC,
    ORIGIN_UC_SYSTEM,
)
from dbxops.metastore.types import (
    JsonType,
    UtcTimestamp,
)

DEFAULT_SCHEMA = "platform_meta"
SCHEMA_VERSION = 1
UC_SCHEMA_PLACEHOLDER = "dbxops_uc_ns"

WRITER_PLATFORM = "platform"
WRITER_SQUAD = "squad"


@dataclass(frozen=True)
class ChildSpec:
    """A Pattern A child table: keyed ``(parent_column, record_version, *key_columns)``."""

    table: str
    parent_column: str
    key_columns: tuple[str, ...]


@dataclass(frozen=True)
class EntitySpec:
    """A Pattern A entity: its parent table, the extracted parent columns, and its child tables."""

    kind: str
    table: str
    owner: str
    semver_required: bool
    columns: tuple[str, ...]
    children: tuple[ChildSpec, ...] = ()

    def child(self, table: str) -> ChildSpec:
        """Return the child spec of ``table``."""
        for spec in self.children:
            if spec.table == table:
                return spec
        raise SchemaError(f"{self.table} has no child table {table!r}")


_TYPE_CODES: dict[str, Any] = {
    "text": Text,
    "bool": Boolean,
    "int": Integer,
    "bigint": BigInteger,
    "double": Double,
    "json": JsonType,
    "ts": UtcTimestamp,
}

# parent table -> (kind, owner, semver_required, extracted columns)
_PARENTS: dict[str, tuple[str, str, bool, tuple[tuple[str, str], ...]]] = {
    "md_domain": (
        "domain",
        WRITER_PLATFORM,
        False,
        (("name", "text"), ("parent_domain_id", "text"), ("description", "text")),
    ),
    "md_team": (
        "team",
        WRITER_PLATFORM,
        False,
        (("name", "text"), ("title", "text"), ("domain_id", "text"), ("github_team", "text")),
    ),
    "md_project": (
        "project",
        WRITER_PLATFORM,
        False,
        (
            ("title", "text"),
            ("project_type", "text"),
            ("repo_kind", "text"),
            ("owner_team_id", "text"),
            ("domain_id", "text"),
            ("repo_url", "text"),
            ("service_principal_app_id", "text"),
        ),
    ),
    "md_glossary_concept": (
        "glossary_concept",
        WRITER_PLATFORM,
        False,
        (
            ("iri", "text"),
            ("label", "text"),
            ("definition", "text"),
            ("scheme_iri", "text"),
            ("broader_iris", "json"),
        ),
    ),
    "md_data_product": (
        "data_product",
        WRITER_SQUAD,
        True,
        (
            ("name", "text"),
            ("api_version", "text"),
            ("domain_id", "text"),
            ("project_id", "text"),
            ("owner_team_id", "text"),
        ),
    ),
    "md_data_contract": (
        "data_contract",
        WRITER_SQUAD,
        True,
        (
            ("name", "text"),
            ("api_version", "text"),
            ("domain_id", "text"),
            ("product_id", "text"),
            ("owner_team_id", "text"),
            ("purpose", "text"),
            ("usage", "text"),
            ("limitations", "text"),
        ),
    ),
    "md_dataflow_spec": (
        "dataflow_spec",
        WRITER_SQUAD,
        False,
        (("bundle", "text"), ("flow_group", "text"), ("layer", "text"), ("spec_type", "text")),
    ),
    "md_asset": (
        "asset",
        WRITER_SQUAD,
        False,
        (
            ("asset_type", "text"),
            ("ontos_asset_type", "text"),
            ("fqn_template", "text"),
            ("bundle", "text"),
            ("resource_key", "text"),
            ("product_id", "text"),
        ),
    ),
}

# child table -> (parent table, parent column, key columns, data columns)
_CHILDREN: dict[str, tuple[str, str, tuple[str, ...], tuple[tuple[str, str], ...]]] = {
    "md_team_member": ("md_team", "team_id", ("member_type", "member_identifier", "role"), ()),
    "md_project_contact": ("md_project", "project_id", ("role", "email"), (("channel", "text"),)),
    "md_project_group": ("md_project", "project_id", ("access_role",), (("ad_group", "text"),)),
    "md_product_port": (
        "md_data_product",
        "product_id",
        ("direction", "port_name"),
        (
            ("port_version", "text"),
            ("contract_id", "text"),
            ("asset_id", "text"),
            ("asset_type", "text"),
            ("contains_pii", "bool"),
        ),
    ),
    "md_port_input_contract": (
        "md_data_product",
        "product_id",
        ("port_name", "contract_id"),
        (("contract_semver", "text"),),
    ),
    "md_contract_server": (
        "md_data_contract",
        "contract_id",
        ("environment",),
        (("type", "text"), ("catalog", "text"), ("schema", "text")),
    ),
    "md_contract_object": (
        "md_data_contract",
        "contract_id",
        ("object_name",),
        (
            ("physical_name", "text"),
            ("logical_type", "text"),
            ("physical_type", "text"),
            ("business_name", "text"),
            ("asset_id", "text"),
        ),
    ),
    "md_contract_property": (
        "md_data_contract",
        "contract_id",
        ("object_name", "property_path"),
        (
            ("parent_path", "text"),
            ("logical_type", "text"),
            ("physical_type", "text"),
            ("required", "bool"),
            ("primary_key", "bool"),
            ("pk_position", "int"),
            ("unique", "bool"),
            ("partitioned", "bool"),
            ("classification", "text"),
            ("critical", "bool"),
            ("is_pii", "bool"),
            ("business_name", "text"),
            ("description", "text"),
        ),
    ),
    "md_quality_rule": (
        "md_data_contract",
        "contract_id",
        ("rule_id",),
        (
            ("object_name", "text"),
            ("property_path", "text"),
            ("type", "text"),
            ("dimension", "text"),
            ("expression", "text"),
            ("action", "text"),
            ("quarantine_asset_id", "text"),
            ("origin", "text"),
            ("spec_id", "text"),
        ),
    ),
    "md_dataflow_flow": (
        "md_dataflow_spec",
        "spec_id",
        ("flow_name",),
        (
            ("flow_type", "text"),
            ("source_asset_id", "text"),
            ("target_asset_id", "text"),
            ("target_kind", "text"),
        ),
    ),
}

# derived (Pattern B) table -> (key columns, data columns)
_DERIVED: dict[str, tuple[tuple[str, ...], tuple[tuple[str, str], ...]]] = {
    "md_semantic_link": (("entity_type", "entity_id", "iri"), (("label", "text"),)),
    "md_tag": (("entity_type", "entity_id", "tag_key"), (("tag_value", "text"),)),
    "md_lineage_edge": (("target", "source_asset_id", "target_asset_id", "origin"), (("via", "text"),)),
}

DERIVED_KEYS: dict[str, tuple[str, ...]] = {name: keys for name, (keys, _) in _DERIVED.items()}
TAG_TABLE = "md_tag"

# fact (Pattern C) table -> (key columns, data columns); every fact table also gets repo_id and recorded_at
_FACTS: dict[str, tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]] = {
    "md_registration": (
        (("project_id", "text"), ("registry_pr_url", "text")),
        (
            ("issue_url", "text"),
            ("requested_by", "text"),
            ("approved_by", "text"),
            ("approved_at", "ts"),
            ("provisioned_repo_url", "text"),
        ),
    ),
    "md_deployment": (
        (("target", "text"), ("bundle", "text"), ("ci_run_id", "text"), ("ci_run_attempt", "int")),
        (
            ("deployment_id", "text"),
            ("git_sha", "text"),
            ("ci_run_url", "text"),
            ("deployed_by", "text"),
            ("deployed_at", "ts"),
            ("status", "text"),
            ("framework_version", "text"),
            ("dbxops_version", "text"),
        ),
    ),
    "md_deployed_asset": (
        (("deployment_id", "text"), ("asset_id", "text")),
        (("asset_record_version", "int"), ("physical_name", "text"), ("workspace_object_id", "text")),
    ),
    "md_run": (
        (("target", "text"), ("run_id", "text")),
        (
            ("job_asset_id", "text"),
            ("status", "text"),
            ("trigger", "text"),
            ("started_at", "ts"),
            ("ended_at", "ts"),
            ("git_sha", "text"),
        ),
    ),
    "md_pipeline_update": (
        (("target", "text"), ("update_id", "text")),
        (
            ("run_id", "text"),
            ("pipeline_asset_id", "text"),
            ("status", "text"),
            ("full_refresh", "bool"),
            ("started_at", "ts"),
            ("ended_at", "ts"),
        ),
    ),
    "md_dataset_metrics": (
        (("target", "text"), ("update_id", "text"), ("asset_id", "text")),
        (
            ("rows_written", "bigint"),
            ("rows_upserted", "bigint"),
            ("rows_deleted", "bigint"),
            ("rows_dropped", "bigint"),
        ),
    ),
    "md_quality_result": (
        (("target", "text"), ("update_id", "text"), ("asset_id", "text"), ("rule_id", "text")),
        (
            ("contract_id", "text"),
            ("rule_record_version", "int"),
            ("passed", "bigint"),
            ("failed", "bigint"),
            ("dropped", "bigint"),
            ("quarantined", "bigint"),
        ),
    ),
    "md_eval_result": (
        (
            ("target", "text"),
            ("run_id", "text"),
            ("subject_asset_id", "text"),
            ("subject_version", "text"),
            ("metric", "text"),
        ),
        (
            ("eval_kind", "text"),
            ("value", "double"),
            ("threshold", "double"),
            ("comparator", "text"),
            ("passed", "bool"),
            ("alias", "text"),
            ("gate_source_path", "text"),
        ),
    ),
    "md_run_performance": (
        (("target", "text"), ("update_id", "text")),
        (
            ("run_id", "text"),
            ("pipeline_asset_id", "text"),
            ("duration_seconds", "double"),
            ("rows_written", "bigint"),
            ("rows_per_second", "double"),
            ("full_refresh", "bool"),
        ),
    ),
    "md_cost_daily": (
        (("usage_date", "text"), ("object_type", "text"), ("object_id", "text"), ("sku_name", "text")),
        (
            ("day_index", "int"),
            ("project_id", "text"),
            ("target", "text"),
            ("bundle", "text"),
            ("git_repo", "text"),
            ("git_sha", "text"),
            ("attribution", "text"),
            ("dbus", "double"),
            ("list_cost", "double"),
            ("currency", "text"),
        ),
    ),
    "md_tuning_recommendation": (
        (("target", "text"), ("asset_id", "text"), ("rule_code", "text"), ("evidence_key", "text")),
        (("severity", "text"), ("message", "text"), ("expected_saving_pct", "double"), ("evidence", "json")),
    ),
    "md_tuning_decision": (
        (("target", "text"), ("asset_id", "text"), ("rule_code", "text"), ("evidence_key", "text")),
        (("decision", "text"), ("decided_by", "text"), ("reason", "text")),
    ),
    "md_design_decision": (
        (("asset_id", "text"), ("git_sha", "text")),
        (("recommended", "text"), ("chosen", "text"), ("justification", "text"), ("approver", "text")),
    ),
    "md_run_event": (
        (("run_id", "text"), ("seq", "int")),
        (("level", "text"), ("message", "text"), ("logged_at", "ts"), ("context", "json")),
    ),
    "md_publish_log": (
        (("publish_id", "text"), ("status", "text")),
        (
            ("target", "text"),
            ("what", "text"),
            ("git_sha", "text"),
            ("inserted", "int"),
            ("closed", "int"),
            ("unchanged", "int"),
            ("orphans_cleaned", "int"),
            ("message", "text"),
            ("started_at", "ts"),
            ("finished_at", "ts"),
        ),
    ),
}

# runtime facts purged after retention_days; definitions and deployments are kept forever
RUNTIME_TABLES = (
    "md_run",
    "md_pipeline_update",
    "md_dataset_metrics",
    "md_quality_result",
    "md_eval_result",
    "md_run_performance",
    "md_tuning_recommendation",
    "md_run_event",
)


def _build_specs() -> dict[str, EntitySpec]:
    specs = {}
    for table, (kind, owner, semver_required, extras) in _PARENTS.items():
        children = tuple(
            ChildSpec(child, parent_column, keys)
            for child, (parent, parent_column, keys, _) in _CHILDREN.items()
            if parent == table
        )
        specs[kind] = EntitySpec(kind, table, owner, semver_required, tuple(name for name, _ in extras), children)
    return specs


ENTITIES: dict[str, EntitySpec] = _build_specs()
PLATFORM_KINDS = frozenset(kind for kind, spec in ENTITIES.items() if spec.owner == WRITER_PLATFORM)
SQUAD_KINDS = frozenset(kind for kind, spec in ENTITIES.items() if spec.owner == WRITER_SQUAD)
PLATFORM_FACT_TABLES = frozenset({"md_registration", "md_cost_daily"})
DERIVED_TABLES = tuple(_DERIVED)
FACT_TABLES = tuple(_FACTS)


def entity_spec(kind: str) -> EntitySpec:
    """Return the spec of an entity kind, or raise ``SchemaError`` for an unknown kind."""
    try:
        return ENTITIES[kind]
    except KeyError:
        raise SchemaError(f"unknown entity kind {kind!r}; expected one of {', '.join(sorted(ENTITIES))}") from None


def _column(name: str, code: str, **kwargs: Any) -> Column:
    column_name = quoted_name(name, True) if name in {"unique", "schema"} else name
    return Column(column_name, _TYPE_CODES[code](), **kwargs)


def _key_columns(names: Iterable[tuple[str, str]]) -> list[Column]:
    return [_column(name, code, nullable=False) for name, code in names]


def _header_columns() -> list[Column]:
    return [
        Column("entity_id", Text, nullable=False),
        Column("record_version", Integer, nullable=False),
        Column("repo_id", Text, nullable=False),
        Column("semver", Text),
        Column("status", Text, nullable=False),
        Column("content_hash", Text, nullable=False),
        Column("payload", JsonType, nullable=False),
        Column("introduced_commit", Text),
        Column("last_seen_commit", Text),
        Column("source_path", Text),
        Column("retired_commit", Text),
        Column("valid_from", UtcTimestamp, nullable=False),
        Column("valid_to", UtcTimestamp),
        Column("is_current", Boolean, nullable=False),
        Column("published_at", UtcTimestamp, nullable=False),
        Column("published_by", Text, nullable=False),
    ]


class MetaModel:
    """The tables and views of the metadata database for one schema name."""

    def __init__(self, schema: str | None = DEFAULT_SCHEMA):
        self.schema = schema
        self.metadata = MetaData()
        self.tables: dict[str, Table] = {}
        self._build_parents()
        self._build_children()
        self._build_derived()
        self._build_facts()
        self._build_admin()

    def table(self, name: str) -> Table:
        """Return the table called ``name``."""
        try:
            return self.tables[name]
        except KeyError:
            raise SchemaError(f"unknown table {name!r}") from None

    def parent(self, kind: str) -> Table:
        """Return the parent table of an entity kind."""
        return self.table(entity_spec(kind).table)

    def _add(self, name: str, *columns: Column, key: Iterable[str], extra: Iterable[Any] = ()) -> Table:
        table = Table(name, self.metadata, *columns, PrimaryKeyConstraint(*key), *extra, schema=self.schema)
        self.tables[name] = table
        return table

    def _build_parents(self) -> None:
        for name, (_, _, _, extras) in _PARENTS.items():
            columns = _header_columns() + [_column(col, code) for col, code in extras]
            index = Index(
                f"uq_{name}_current",
                "entity_id",
                unique=True,
                sqlite_where=text("is_current"),
                postgresql_where=text("is_current"),
            )
            self._add(name, *columns, key=("entity_id", "record_version"), extra=(index,))

    def _build_children(self) -> None:
        for name, (_, parent_column, keys, data) in _CHILDREN.items():
            columns = [Column(parent_column, Text, nullable=False), Column("record_version", Integer, nullable=False)]
            columns += _key_columns((key, "text") for key in keys)
            columns += [_column(col, code) for col, code in data]
            self._add(name, *columns, key=(parent_column, "record_version", *keys))

    def _build_derived(self) -> None:
        for name, (keys, data) in _DERIVED.items():
            columns = _key_columns((key, "text") for key in keys)
            columns += [_column(col, code) for col, code in data]
            columns += [
                Column("repo_id", Text, nullable=False),
                Column("is_active", Boolean, nullable=False),
                Column("first_seen_at", UtcTimestamp, nullable=False),
                Column("last_seen_at", UtcTimestamp, nullable=False),
            ]
            self._add(name, *columns, key=keys)

    def _build_facts(self) -> None:
        for name, (keys, data) in _FACTS.items():
            columns = _key_columns(keys) + [_column(col, code) for col, code in data]
            columns += [Column("repo_id", Text, nullable=False), Column("recorded_at", UtcTimestamp, nullable=False)]
            self._add(name, *columns, key=[key for key, _ in keys])

    def _build_admin(self) -> None:
        self._add(
            "md_schema_version",
            Column("version", Integer, nullable=False),
            Column("description", Text),
            Column("applied_at", UtcTimestamp, nullable=False),
            key=("version",),
        )

    def views(self) -> dict[str, Select]:
        """Return the view name -> defining select, in creation order."""
        views: dict[str, Select] = {}
        for spec in ENTITIES.values():
            parent = self.table(spec.table)
            views[_view_name(spec.table)] = select(parent).where(parent.c.is_current.is_(true()))
            for child in spec.children:
                views[_view_name(child.table)] = self._child_view(parent, child)
        views["v_lineage"] = self._lineage_view()
        views["v_lineage_drift"] = self._lineage_drift_view()
        views["v_project_directory"] = self._project_directory_view()
        views["v_cost_by_project"] = self._cost_by_project_view()
        views["v_burn_rate"] = self._burn_rate_view()
        views["v_open_tuning_recommendations"] = self._open_tuning_view()
        return views

    def _daily_cost(self):
        cost = self.table("md_cost_daily")
        return (
            select(
                cost.c.project_id,
                cost.c.usage_date,
                cost.c.day_index,
                func.sum(cost.c.dbus).label("dbus"),
                func.sum(cost.c.list_cost).label("list_cost"),
            )
            .group_by(cost.c.project_id, cost.c.usage_date, cost.c.day_index)
            .subquery("daily")
        )

    def _cost_by_project_view(self) -> Select:
        daily = self._daily_cost()
        return select(daily.c.project_id, daily.c.usage_date, daily.c.day_index, daily.c.dbus, daily.c.list_cost)

    def _burn_rate_view(self) -> Select:
        """Per project: the latest day's cost against the averages of the 7 and 28 days before it."""
        daily = self._daily_cost()
        latest = (
            select(daily.c.project_id, func.max(daily.c.day_index).label("latest_day"))
            .group_by(daily.c.project_id)
            .subquery("latest")
        )
        other = self._daily_cost().alias("window")

        def window_sum(first_offset: int, last_offset: int):
            return (
                select(func.coalesce(func.sum(other.c.list_cost), 0.0))
                .where(
                    and_(
                        other.c.project_id == latest.c.project_id,
                        other.c.day_index >= latest.c.latest_day - first_offset,
                        other.c.day_index <= latest.c.latest_day - last_offset,
                    )
                )
                .scalar_subquery()
            )

        last_day = window_sum(0, 0)
        avg_7 = window_sum(7, 1) / 7.0
        avg_28 = window_sum(28, 1) / 28.0
        growth = case((avg_7 > 0, (last_day - avg_7) * 100.0 / avg_7), else_=None)
        return select(
            latest.c.project_id,
            latest.c.latest_day,
            last_day.label("cost_latest_day"),
            avg_7.label("avg_daily_cost_prev_7d"),
            avg_28.label("avg_daily_cost_prev_28d"),
            growth.label("growth_pct_vs_7d"),
            (avg_7 * 30.0).label("projected_30d_cost"),
        )

    def _open_tuning_view(self) -> Select:
        recommendation, decision = self.table("md_tuning_recommendation"), self.table("md_tuning_decision")
        decided = exists().where(
            and_(
                decision.c.target == recommendation.c.target,
                decision.c.asset_id == recommendation.c.asset_id,
                decision.c.rule_code == recommendation.c.rule_code,
                decision.c.evidence_key == recommendation.c.evidence_key,
            )
        )
        return select(recommendation).where(~decided)

    def _child_view(self, parent: Table, child: ChildSpec) -> Select:
        table = self.table(child.table)
        joined = table.join(
            parent,
            and_(
                parent.c.entity_id == table.c[child.parent_column],
                parent.c.record_version == table.c.record_version,
            ),
        )
        return (
            select(table, parent.c.repo_id, parent.c.semver, parent.c.status)
            .select_from(joined)
            .where(parent.c.is_current.is_(true()))
        )

    def _edge_exists(self, edge, other_origin: str):
        other = self.table("md_lineage_edge").alias("o")
        return exists().where(
            and_(
                other.c.target == edge.c.target,
                other.c.source_asset_id == edge.c.source_asset_id,
                other.c.target_asset_id == edge.c.target_asset_id,
                other.c.origin == other_origin,
                other.c.is_active.is_(true()),
            )
        )

    def _lineage_view(self) -> Select:
        edge = self.table("md_lineage_edge")
        observed = case((self._edge_exists(edge, ORIGIN_UC_SYSTEM), true()), else_=false()).label("observed")
        return select(
            edge.c.target, edge.c.source_asset_id, edge.c.target_asset_id, edge.c.via, edge.c.repo_id, observed
        ).where(and_(edge.c.origin == ORIGIN_SPEC, edge.c.is_active.is_(true())))

    def _lineage_drift_view(self) -> Select:
        edge = self.table("md_lineage_edge")
        return select(edge.c.target, edge.c.source_asset_id, edge.c.target_asset_id, edge.c.via, edge.c.repo_id).where(
            and_(
                edge.c.origin == ORIGIN_UC_SYSTEM,
                edge.c.is_active.is_(true()),
                ~self._edge_exists(edge, ORIGIN_SPEC),
            )
        )

    def _project_directory_view(self) -> Select:
        project, group = self.table("md_project"), self.table("md_project_group")
        joined = project.outerjoin(
            group, and_(group.c.project_id == project.c.entity_id, group.c.record_version == project.c.record_version)
        )
        return (
            select(
                project.c.entity_id.label("project_id"),
                project.c.title,
                project.c.status,
                project.c.repo_kind,
                project.c.domain_id,
                project.c.owner_team_id,
                project.c.repo_url,
                group.c.access_role,
                group.c.ad_group,
            )
            .select_from(joined)
            .where(project.c.is_current.is_(true()))
        )


def _view_name(table: str) -> str:
    return f"v_{table.removeprefix('md_')}_current"


def build_model(schema: str | None = DEFAULT_SCHEMA) -> MetaModel:
    """Build the metadata model for ``schema`` (``None`` for databases without schemas, like SQLite)."""
    return MetaModel(schema)


def _dialect(name: str):
    if name == "sqlite":
        return sqlite.dialect()
    if name == "postgresql":
        return postgresql.dialect()
    if name == "databricks":
        return importlib.import_module("databricks.sqlalchemy").DatabricksDialect()
    raise SchemaError(f"unsupported dialect {name!r}; expected sqlite, postgresql or databricks")


def render_table_ddl(model: MetaModel, dialect_name: str) -> list[str]:
    """Render ``CREATE TABLE IF NOT EXISTS`` for every table, in dependency order."""
    dialect = _dialect(dialect_name)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # the Databricks dialect warns about autoincrement on every integer key
        return [
            str(CreateTable(table, if_not_exists=True).compile(dialect=dialect)).strip()
            for table in model.metadata.sorted_tables
        ]


def render_index_ddl(model: MetaModel, dialect_name: str) -> list[str]:
    """Render ``CREATE UNIQUE INDEX`` statements (the at-most-one-current-row guard) where the dialect has indexes."""
    if dialect_name not in {"sqlite", "postgresql"}:
        return []
    dialect = _dialect(dialect_name)
    statements = []
    for table in model.metadata.sorted_tables:
        for index in sorted(table.indexes, key=lambda item: item.name):
            statements.append(str(CreateIndex(index, if_not_exists=True).compile(dialect=dialect)).strip())
    return statements


def render_view_ddl(model: MetaModel, dialect_name: str) -> list[str]:
    """Render the view statements; SQLite drops and recreates because it has no ``CREATE OR REPLACE VIEW``."""
    dialect = _dialect(dialect_name)
    preparer = dialect.identifier_preparer
    statements = []
    for name, query in model.views().items():
        qualified = preparer.quote(name) if model.schema is None else f"{preparer.quote_schema(model.schema)}.{name}"
        body = str(query.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))
        if dialect_name == "sqlite":
            statements.append(f"DROP VIEW IF EXISTS {qualified}")
            statements.append(f"CREATE VIEW {qualified} AS {body}")
        else:
            statements.append(f"CREATE OR REPLACE VIEW {qualified} AS {body}")
    return statements


__all__ = [
    "DEFAULT_SCHEMA",
    "ENTITIES",
    "PLATFORM_KINDS",
    "SCHEMA_VERSION",
    "SQUAD_KINDS",
    "ChildSpec",
    "EntitySpec",
    "MetaModel",
    "build_model",
    "entity_spec",
    "render_index_ddl",
    "render_table_ddl",
    "render_view_ddl",
]
