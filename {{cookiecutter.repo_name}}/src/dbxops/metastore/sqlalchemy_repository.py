"""
``SqlAlchemyRepository``: the metadata repository for PostgreSQL, Lakebase and SQLite.

One code path serves all three through SQLAlchemy Core: ``INSERT ... ON CONFLICT`` for the idempotent tables and one
transaction per entity for definitions. SQLite is what the unit tests run on, so the tests exercise this exact code.

Schema handling: PostgreSQL and Lakebase use the ``schema`` argument (default ``platform_meta``) as a real schema.
SQLite has no schemas, so the model is built with ``schema=None`` and the argument is ignored. The model is rebuilt
per repository instead of using ``schema_translate_map``, so statements, DDL and views all agree on one name.

The database clock is evaluated inside the write statements (``valid_from``, ``valid_to``, ``published_at``,
``first_seen_at``, ``last_seen_at``, ``recorded_at``); no timestamp from the runner is ever bound for them.
"""

from __future__ import annotations

from typing import (
    Any,
    Mapping,
)

from sqlalchemy import (
    Connection,
    Engine,
    Table,
    and_,
    delete,
    exists,
    func,
    select,
    true,
    tuple_,
    update,
)
from sqlalchemy.dialects import (
    postgresql,
    sqlite,
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
    MetaModel,
    build_model,
    entity_spec,
    render_view_ddl,
)
from dbxops.metastore.performance import (
    PERFORMANCE_COLUMNS,
    performance_record,
)
from dbxops.metastore.records import (
    STATUS_RETIRED,
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
    parent_values,
    record_from_rows,
)
from dbxops.metastore.types import (
    utc_now,
    utc_now_minus_days,
)

_SUPPORTED_DIALECTS = frozenset({"sqlite", "postgresql"})
_CHUNK = 400


class SqlAlchemyRepository(BaseRepository):
    """A metadata repository over a SQLAlchemy engine (PostgreSQL, Lakebase or SQLite)."""

    def __init__(self, engine: Engine, schema: str | None = DEFAULT_SCHEMA):
        self.dialect_name = engine.dialect.name
        if self.dialect_name not in _SUPPORTED_DIALECTS:
            raise SchemaError(f"SqlAlchemyRepository supports sqlite and postgresql, not {self.dialect_name!r}")
        self._engine = engine
        self._model: MetaModel = build_model(None if self.dialect_name == "sqlite" else schema)
        self._insert = sqlite.insert if self.dialect_name == "sqlite" else postgresql.insert

    @property
    def model(self) -> MetaModel:
        """Return the table model this repository writes."""
        return self._model

    # ------------------------------------------------------------------ schema

    def ensure_schema(self) -> None:
        """Create missing tables, check ``md_schema_version`` and (re)create the views."""
        with self._engine.begin() as conn:
            if self._model.schema:
                preparer = self._engine.dialect.identifier_preparer
                conn.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {preparer.quote_schema(self._model.schema)}")
            self._model.metadata.create_all(conn, checkfirst=True)
            self._check_version(conn)
            for statement in render_view_ddl(self._model, self.dialect_name):
                conn.exec_driver_sql(statement)

    def _check_version(self, conn: Connection) -> None:
        table = self._model.table("md_schema_version")
        found = conn.execute(select(func.max(table.c.version))).scalar()
        if found is not None and found > SCHEMA_VERSION:
            raise SchemaError(
                f"database schema version {found} is newer than this library's {SCHEMA_VERSION}; upgrade dbxops"
            )
        if found is None:
            conn.execute(
                table.insert().values(version=SCHEMA_VERSION, description="initial schema", applied_at=utc_now())
            )

    # ------------------------------------------------------------- definitions

    def _upsert_one(self, record: DefinitionRecord, ctx: PublishContext) -> PublishCounts:
        spec = entity_spec(record.kind)
        children = normalised_children(self._model, spec, record)
        parent = self._model.table(spec.table)
        with self._engine.begin() as conn:
            current = self._current_row(conn, parent, record.entity_id)
            action = decide(spec, record, current)
            if action is Action.TOUCH:
                self._touch(conn, parent, record, ctx)
                return PublishCounts(unchanged=1)
            version = 1 if current is None else current.record_version + 1
            if current is not None:
                self._close(conn, parent, current)
            values = parent_values(spec, record, version, ctx.published_by)
            values.update(valid_from=utc_now(), valid_to=None, published_at=utc_now())
            conn.execute(parent.insert().values(**values))
            self._insert_children(conn, record, children, version)
            self._assert_single_current(conn, parent, record.entity_id)
        return PublishCounts(inserted=1, closed=0 if current is None else 1)

    def _current_row(self, conn: Connection, parent: Table, entity_id: str) -> CurrentRow | None:
        statement = (
            select(
                parent.c.entity_id,
                parent.c.record_version,
                parent.c.repo_id,
                parent.c.semver,
                parent.c.status,
                parent.c.content_hash,
            )
            .where(and_(parent.c.entity_id == entity_id, parent.c.is_current.is_(true())))
            .with_for_update()
        )
        found = conn.execute(statement).all()
        if len(found) > 1:
            raise PublishError(f"{entity_id} has {len(found)} current rows; the one-current-row invariant is broken")
        return CurrentRow(*found[0]) if found else None

    def _touch(self, conn: Connection, parent: Table, record: DefinitionRecord, ctx: PublishContext) -> None:
        values: dict[str, Any] = {"published_at": utc_now(), "published_by": ctx.published_by}
        if record.git_commit is not None:
            values["last_seen_commit"] = record.git_commit
        conn.execute(
            update(parent)
            .where(and_(parent.c.entity_id == record.entity_id, parent.c.is_current.is_(true())))
            .values(**values)
        )

    def _close(self, conn: Connection, parent: Table, current: CurrentRow) -> None:
        result = conn.execute(
            update(parent)
            .where(
                and_(
                    parent.c.entity_id == current.entity_id,
                    parent.c.record_version == current.record_version,
                    parent.c.is_current.is_(true()),
                )
            )
            .values(is_current=False, valid_to=utc_now())
        )
        if result.rowcount != 1:
            raise PublishError(f"{current.entity_id}: version {current.record_version} was closed concurrently")

    def _insert_children(
        self, conn: Connection, record: DefinitionRecord, children: Mapping[str, list[dict]], version: int
    ) -> None:
        spec = entity_spec(record.kind)
        for table_name, rows in children.items():
            if rows:
                parent_column = spec.child(table_name).parent_column
                extra = {parent_column: record.entity_id, "record_version": version}
                conn.execute(self._model.table(table_name).insert(), [{**row, **extra} for row in rows])

    def _assert_single_current(self, conn: Connection, parent: Table, entity_id: str) -> None:
        count = conn.execute(
            select(func.count()).where(and_(parent.c.entity_id == entity_id, parent.c.is_current.is_(true())))
        ).scalar()
        if count != 1:
            raise PublishError(f"{entity_id} has {count} current rows after publish; expected exactly 1")

    def get_current_definition(self, kind: str, entity_id: str) -> DefinitionRecord | None:
        """Return the current version of an entity with its child rows, or ``None``."""
        spec = entity_spec(kind)
        parent = self._model.table(spec.table)
        with self._engine.connect() as conn:
            row = (
                conn.execute(
                    select(parent).where(and_(parent.c.entity_id == entity_id, parent.c.is_current.is_(true())))
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            children = {}
            for child in spec.children:
                table = self._model.table(child.table)
                found = conn.execute(
                    select(table).where(
                        and_(
                            table.c[child.parent_column] == entity_id, table.c.record_version == row["record_version"]
                        )
                    )
                ).mappings()
                children[child.table] = [dict(item) for item in found]
        return record_from_rows(spec, dict(row), children)

    def list_current_entity_ids(self, kind: str, repo_id: str, include_retired: bool = False) -> list[str]:
        """Return the entity ids whose current version belongs to ``repo_id``."""
        parent = self._model.parent(kind)
        condition = and_(parent.c.repo_id == repo_id, parent.c.is_current.is_(true()))
        if not include_retired:
            condition = and_(condition, parent.c.status != STATUS_RETIRED)
        with self._engine.connect() as conn:
            return [row[0] for row in conn.execute(select(parent.c.entity_id).where(condition).order_by("entity_id"))]

    def recent_performance(self, repo_id: str, target: str, limit: int = 500) -> list[PerformanceRecord]:
        """Return the newest ``limit`` performance facts of ``repo_id`` on ``target``, newest first."""
        table = self._model.table("md_run_performance")
        statement = (
            select(*(table.c[name] for name in PERFORMANCE_COLUMNS))
            .where(and_(table.c.repo_id == repo_id, table.c.target == target))
            .order_by(table.c.recorded_at.desc(), table.c.update_id.desc())
            .limit(limit)
        )
        with self._engine.connect() as conn:
            return [performance_record(dict(zip(PERFORMANCE_COLUMNS, row))) for row in conn.execute(statement)]

    def current_quality_rules(self, repo_id: str) -> list[QualityRuleRef]:
        """Return the quality rules of the current contracts of ``repo_id``, with the asset each applies to."""
        rule = self._model.table("md_quality_rule")
        contract = self._model.parent("data_contract")
        obj = self._model.table("md_contract_object")
        joined = rule.join(
            contract,
            and_(contract.c.entity_id == rule.c.contract_id, contract.c.record_version == rule.c.record_version),
        ).outerjoin(
            obj,
            and_(
                obj.c.contract_id == rule.c.contract_id,
                obj.c.record_version == rule.c.record_version,
                obj.c.object_name == rule.c.object_name,
            ),
        )
        statement = (
            select(rule.c.contract_id, rule.c.record_version, rule.c.rule_id, func.coalesce(obj.c.asset_id, ""))
            .select_from(joined)
            .where(and_(contract.c.is_current.is_(true()), contract.c.repo_id == repo_id))
        )
        with self._engine.connect() as conn:
            return [QualityRuleRef(*row) for row in conn.execute(statement)]

    # -------------------------------------------------- derived (Pattern B) rows

    def _replace_derived(self, derived: row_builders.DerivedSet, ctx: PublishContext) -> int:
        table = self._model.table(derived.table_name)
        with self._engine.begin() as conn:
            self._reject_foreign_rows(conn, derived, ctx)
            for item in derived.items:
                self._upsert_derived_row(conn, derived, item, ctx)
            for key in self._stale_keys(conn, derived, ctx):
                match = and_(*(table.c[name] == value for name, value in zip(derived.key_names, key)))
                conn.execute(update(table).where(match).values(is_active=False))
        return len(derived.items)

    def _reject_foreign_rows(self, conn: Connection, derived: row_builders.DerivedSet, ctx: PublishContext) -> None:
        table = self._model.table(derived.table_name)
        columns = [table.c[name] for name in derived.key_names]
        keys = derived.keys()
        for start in range(0, len(keys), _CHUNK):
            end = start + _CHUNK
            found = conn.execute(
                select(*columns, table.c.repo_id).where(
                    and_(tuple_(*columns).in_(keys[start:end]), table.c.repo_id != ctx.repo_id)
                )
            ).first()
            if found is not None:
                raise WriterError(f"{table.name} row {tuple(found[:-1])} is owned by repo {found[-1]!r}")

    def _upsert_derived_row(
        self, conn: Connection, derived: row_builders.DerivedSet, item: Mapping[str, Any], ctx: PublishContext
    ) -> None:
        table = self._model.table(derived.table_name)
        values = {**item, "repo_id": ctx.repo_id, "is_active": True, "first_seen_at": utc_now()}
        values["last_seen_at"] = utc_now()
        changed = {name: item[name] for name in derived.data_names}
        changed.update(is_active=True, last_seen_at=utc_now())
        statement = self._insert(table).values(**values)
        conn.execute(statement.on_conflict_do_update(index_elements=list(derived.key_names), set_=changed))

    def _stale_keys(self, conn: Connection, derived: row_builders.DerivedSet, ctx: PublishContext) -> list[tuple]:
        table = self._model.table(derived.table_name)
        conditions = [table.c.repo_id == ctx.repo_id, table.c.is_active.is_(true())]
        conditions += [table.c[name] == value for name, value in derived.scope.items()]
        wanted = set(derived.keys())
        found = conn.execute(select(*(table.c[name] for name in derived.key_names)).where(and_(*conditions)))
        return [
            tuple(row)
            for row in found
            if tuple(row) not in wanted and derived.owns(dict(zip(derived.key_names, tuple(row))))
        ]

    # ---------------------------------------------------------- facts (Pattern C)

    def _insert_absent(self, rows: list[row_builders.FactRow]) -> int:
        inserted = 0
        with self._engine.begin() as conn:
            for table_name, values in rows:
                bound = {name: utc_now() if value is row_builders.DB_NOW else value for name, value in values.items()}
                statement = self._insert(self._model.table(table_name)).values(**bound).on_conflict_do_nothing()
                inserted += conn.execute(statement).rowcount
        return inserted

    # ------------------------------------------------------------------ upkeep

    def _cleanup_kind(self, kind: str) -> int:
        spec = entity_spec(kind)
        parent = self._model.table(spec.table)
        removed = 0
        with self._engine.begin() as conn:
            for child in spec.children:
                table = self._model.table(child.table)
                has_parent = exists().where(
                    and_(
                        parent.c.entity_id == table.c[child.parent_column],
                        parent.c.record_version >= table.c.record_version,
                    )
                )
                removed += conn.execute(delete(table).where(~has_parent)).rowcount
        return removed

    def purge_runtime(self, retention_days: int) -> int:
        """Delete runtime facts older than ``retention_days``."""
        removed = 0
        with self._engine.begin() as conn:
            for name in RUNTIME_TABLES:
                table = self._model.table(name)
                old = table.c.recorded_at < utc_now_minus_days(retention_days)
                removed += conn.execute(delete(table).where(old)).rowcount
        return removed
