"""Tests of the model DDL for each dialect, the views, and the JsonType/UtcTimestamp/clock types."""

from datetime import (
    datetime,
    timedelta,
    timezone,
)

import pytest
from sqlalchemy import (
    Column,
    MetaData,
    Table,
    create_engine,
    insert,
    select,
)
from sqlalchemy.dialects import (
    postgresql,
    sqlite,
)

from dbxops.metastore.errors import SchemaError
from dbxops.metastore.model import (
    ENTITIES,
    PLATFORM_KINDS,
    SQUAD_KINDS,
    build_model,
    entity_spec,
    render_index_ddl,
    render_table_ddl,
    render_view_ddl,
)
from dbxops.metastore.types import (
    JsonType,
    UtcTimestamp,
    format_utc,
    parse_utc,
    to_utc,
    utc_now,
    utc_now_minus_days,
)

TABLE_COUNT = 37
VIEW_COUNT = 24


@pytest.mark.parametrize(
    ("dialect", "schema"), [("sqlite", None), ("postgresql", "platform_meta"), ("databricks", "ns")]
)
def test__ddl_renders_every_table_and_view_for_each_dialect(dialect, schema):
    """CREATE TABLE and view statements render for sqlite, postgresql and databricks."""
    if dialect == "databricks":
        pytest.importorskip("databricks.sqlalchemy")
    model = build_model(schema)
    tables = render_table_ddl(model, dialect)
    views = render_view_ddl(model, dialect)
    assert len(tables) == TABLE_COUNT and all(ddl.startswith("CREATE TABLE IF NOT EXISTS") for ddl in tables)
    assert len(views) == VIEW_COUNT * (2 if dialect == "sqlite" else 1)
    assert any("v_lineage_drift" in view for view in views) and any("v_project_directory" in view for view in views)


def test__databricks_ddl_uses_variant_timestamp_and_quotes_reserved_names():
    """The databricks dialect renders JsonType as VARIANT, UtcTimestamp as TIMESTAMP and quotes `unique`."""
    pytest.importorskip("databricks.sqlalchemy")
    ddl = "\n".join(render_table_ddl(build_model("ns"), "databricks"))
    assert "payload VARIANT NOT NULL" in ddl
    assert "valid_from TIMESTAMP NOT NULL" in ddl and "published_at TIMESTAMP NOT NULL" in ddl
    assert "`unique` BOOLEAN" in ddl and "`schema` STRING" in ddl
    assert "USING DELTA" in ddl and "FOREIGN KEY" not in ddl


def test__postgres_ddl_uses_jsonb_timestamptz_and_partial_unique_indexes():
    """The PostgreSQL dialect gets JSONB, TIMESTAMP WITH TIME ZONE and a partial unique index per Pattern A table."""
    model = build_model("platform_meta")
    ddl = "\n".join(render_table_ddl(model, "postgresql"))
    assert "payload JSONB NOT NULL" in ddl and "valid_from TIMESTAMP WITH TIME ZONE NOT NULL" in ddl
    indexes = render_index_ddl(model, "postgresql")
    assert len(indexes) == len(ENTITIES) and all("WHERE is_current" in index for index in indexes)
    assert not render_index_ddl(model, "databricks")


def test__sqlite_ddl_stores_json_and_timestamps_as_text():
    """The SQLite dialect has no JSON or timestamp-with-zone type, so both are TEXT."""
    ddl = "\n".join(render_table_ddl(build_model(None), "sqlite"))
    assert "payload TEXT NOT NULL" in ddl and "valid_from TEXT NOT NULL" in ddl


def test__every_key_column_is_not_null_and_children_are_keyed_by_parent_and_version():
    """No NULLs in key columns, and child keys start with (parent id, record_version)."""
    model = build_model(None)
    for table in model.metadata.tables.values():
        assert all(column.nullable is False for column in table.primary_key.columns), table.name
    for spec in ENTITIES.values():
        for child in spec.children:
            keys = [column.name for column in model.table(child.table).primary_key.columns]
            assert keys[:2] == [child.parent_column, "record_version"]
    parent = [column.name for column in model.parent("data_contract").primary_key.columns]
    assert parent == ["entity_id", "record_version"]


def test__every_definition_table_carries_the_pattern_a_header():
    """repo_id, record_version, semver, status, hashes, provenance, validity and audit columns exist on all of them."""
    header = {
        "entity_id", "repo_id", "record_version", "semver", "status", "content_hash", "payload", "introduced_commit",
        "last_seen_commit", "source_path", "retired_commit", "valid_from", "valid_to", "is_current", "published_at", "published_by",
    }  # fmt: skip
    model = build_model(None)
    for spec in ENTITIES.values():
        assert header <= {column.name for column in model.table(spec.table).c}
    assert len(ENTITIES) == 8 and not PLATFORM_KINDS & SQUAD_KINDS
    assert {"domain", "team", "project", "glossary_concept"} == PLATFORM_KINDS


def test__the_schema_name_is_overridable():
    """The same model builds under another schema name."""
    model = build_model("other_meta")
    assert all(table.schema == "other_meta" for table in model.metadata.tables.values())
    assert "other_meta.v_data_contract_current" in "\n".join(render_view_ddl(model, "postgresql"))


def test__lookup_errors_name_the_problem():
    """Unknown kinds, tables, children and dialects raise SchemaError."""
    model = build_model(None)
    with pytest.raises(SchemaError, match="unknown entity kind"):
        entity_spec("nope")
    with pytest.raises(SchemaError, match="unknown table"):
        model.table("nope")
    with pytest.raises(SchemaError, match="no child table"):
        entity_spec("asset").child("md_quality_rule")
    with pytest.raises(SchemaError, match="unsupported dialect"):
        render_table_ddl(model, "oracle")


@pytest.mark.usefixtures("repo")
def test__views_create_and_query_on_sqlite(sqlite_engine):
    """Every generated view is valid SQL on SQLite (ensure_schema created them) and starts empty."""
    with sqlite_engine.connect() as conn:
        names = [row[0] for row in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'view'")]
        assert len(names) == VIEW_COUNT
        for name in names:
            assert conn.exec_driver_sql(f"SELECT * FROM {name}").fetchall() == []


def sample_table():
    """Return a table using both custom types."""
    return Table("t", MetaData(), Column("doc", JsonType), Column("at", UtcTimestamp))


def test__json_type_round_trips_documents_as_canonical_text_on_sqlite():
    """The stored text is canonical JSON (sorted, compact); reading returns the document."""
    engine, table = create_engine("sqlite://"), sample_table()
    table.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(table).values(doc={"b": 1, "a": [1, {"é": "x"}]}, at=None))
        assert conn.exec_driver_sql("SELECT doc FROM t").scalar() == '{"a":[1,{"é":"x"}],"b":1}'
        assert conn.execute(select(table.c.doc)).scalar() == {"a": [1, {"é": "x"}], "b": 1}
        assert conn.execute(select(table.c.at)).scalar() is None


def test__utc_timestamp_normalises_offsets_and_rejects_naive_values():
    """A +10:00 value is stored as UTC with a Z suffix; a naive datetime is refused."""
    engine, table = create_engine("sqlite://"), sample_table()
    table.metadata.create_all(engine)
    local = datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone(timedelta(hours=10)))
    with engine.begin() as conn:
        conn.execute(insert(table).values(doc=None, at=local))
        assert conn.exec_driver_sql("SELECT at FROM t").scalar() == "2026-10-10T00:00:00.000Z"
        back = conn.execute(select(table.c.at)).scalar()
    assert back == local and back.utcoffset() == timedelta(0)
    with pytest.raises(ValueError, match="naive"):
        to_utc(datetime(2026, 1, 1))


def test__timestamp_text_helpers_round_trip():
    """format_utc and parse_utc are inverses at millisecond precision."""
    moment = datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=timezone.utc)
    assert format_utc(moment) == "2026-01-02T03:04:05.678Z" and parse_utc(format_utc(moment)) == moment


def test__the_database_clock_compiles_per_dialect():
    """utc_now is strftime on SQLite, now() on PostgreSQL and current_timestamp() on Databricks."""
    assert "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')" in str(select(utc_now()).compile(dialect=sqlite.dialect()))
    assert "now()" in str(select(utc_now()).compile(dialect=postgresql.dialect()))
    assert "'-400 days'" in str(select(utc_now_minus_days(400)).compile(dialect=sqlite.dialect()))
    assert "now() - INTERVAL '400 days'" in str(select(utc_now_minus_days(400)).compile(dialect=postgresql.dialect()))
    assert "CURRENT_TIMESTAMP" in str(select(utc_now()).compile())
    assert "INTERVAL '3 days'" in str(select(utc_now_minus_days(3)).compile())
    dialect = pytest.importorskip("databricks.sqlalchemy").DatabricksDialect()
    assert "current_timestamp()" in str(select(utc_now()).compile(dialect=dialect))
    assert "INTERVAL 7 DAYS" in str(select(utc_now_minus_days(7)).compile(dialect=dialect))


def test__the_database_clock_returns_a_utc_datetime_on_sqlite():
    """Selecting utc_now() through the type gives a timezone-aware UTC datetime near the real UTC time."""
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        now = conn.execute(select(utc_now())).scalar()
    assert now.utcoffset() == timedelta(0)
    assert abs(now - datetime.now(timezone.utc)) < timedelta(seconds=30)
