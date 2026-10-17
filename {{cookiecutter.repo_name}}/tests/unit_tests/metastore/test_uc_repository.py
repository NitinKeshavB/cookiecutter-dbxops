"""Tests of UcRepository against a recording executor: exact statement order and shape, and the crash scenario."""

import copy
import re
from dataclasses import replace
from types import SimpleNamespace

import pytest

from dbxops.metastore.errors import (
    ConfigError,
    PublishError,
    SchemaError,
    WriterError,
)
from dbxops.metastore.model import (
    UC_SCHEMA_PLACEHOLDER,
    build_model,
)
from dbxops.metastore.records import (
    ORIGIN_SPEC,
    LineageEdge,
    PublishCounts,
    TagRow,
)
from dbxops.metastore.uc_repository import (
    DatabricksSqlConnectorExecutor,
    SparkSqlExecutor,
    UcRepository,
)
from dbxops.metastore.uc_sql import UcStatements
from tests.fixtures.metastore import (
    CONTRACT_ID,
    SQUAD,
    RecordingExecutor,
    contract_record,
    deployment,
)

CHILD_TAGS = [
    "orphans:md_contract_server",
    "orphans:md_contract_object",
    "orphans:md_contract_property",
    "orphans:md_quality_rule",
]
INSERT_TAGS = [
    "insert:md_contract_server",
    "insert:md_contract_object",
    "insert:md_contract_property",
    "insert:md_quality_rule",
]


@pytest.fixture
def uc(executor: RecordingExecutor) -> UcRepository:
    """Return a UcRepository over the recording executor."""
    return UcRepository(executor, "main_cat")


def current_row(record, version: int = 1, content_hash: str | None = None) -> dict:
    """Return the header row the executor answers for the current version of ``record``."""
    return {
        "entity_id": record.entity_id,
        "record_version": version,
        "repo_id": record.repo_id,
        "semver": record.semver,
        "status": record.status,
        "content_hash": content_hash or record.content_hash,
    }


def edited(doc: dict) -> dict:
    """Return a changed copy of a contract with a bumped semver."""
    other = copy.deepcopy(doc)
    other["version"] = "1.1.0"
    other["description"]["purpose"] = "changed"
    return other


def test__a_new_entity_is_published_children_first_then_one_parent_merge(uc, executor, contract_doc, ctx):
    """Order: session, orphan cleanup, current-row read, children at version 1, ONE parent MERGE, invariant check."""
    counts = uc.upsert_definitions([contract_record(contract_doc)], ctx)
    assert executor.tags == [
        "session",
        *CHILD_TAGS,
        "current",
        *INSERT_TAGS,
        "scd2-merge:md_data_contract",
        "invariant",
    ]
    assert counts == PublishCounts(inserted=1)


def publish_change(uc, executor, contract_doc, ctx):
    """Publish a changed contract over a stored version 1 and return the counts."""
    old = contract_record(contract_doc)
    executor.on(r"^SELECT entity_id, record_version, repo_id", [current_row(old)])
    return uc.upsert_definitions([contract_record(edited(contract_doc))], ctx)


def test__a_changed_entity_writes_children_at_n_plus_1_before_one_parent_merge(uc, executor, contract_doc, ctx):
    """Children go in at N+1 first, then exactly one parent MERGE, and nothing else touches the parent."""
    counts = publish_change(uc, executor, contract_doc, ctx)
    assert counts == PublishCounts(inserted=1, closed=1)
    assert executor.tags[-6:] == [*INSERT_TAGS, "scd2-merge:md_data_contract", "invariant"][-6:]
    assert executor.tags.count("scd2-merge:md_data_contract") == 1
    assert not any(tag.startswith("update:") for tag in executor.tags)
    child_inserts = [params for sql, params in executor.calls if sql.startswith("INSERT INTO")]
    assert all(p["p0_record_version"] == 2 for p in child_inserts)


def test__the_parent_merge_closes_n_and_inserts_n_plus_1(uc, executor, contract_doc, ctx):
    """The single MERGE closes the matched current row and inserts the new version from the unmatched source row."""
    publish_change(uc, executor, contract_doc, ctx)
    sql, params = next((s, p) for s, p in executor.calls if s.startswith("MERGE INTO"))
    assert "WHEN MATCHED THEN UPDATE SET t.is_current = false, t.valid_to = current_timestamp()" in sql
    assert "WHEN NOT MATCHED AND s.merge_key IS NULL THEN INSERT" in sql
    assert "t.record_version = :previous_version" in sql and "parse_json(s.`payload`)" in sql
    assert (params["record_version"], params["previous_version"]) == (2, 1)


def test__an_unchanged_hash_only_updates_the_last_seen_commit(uc, executor, contract_doc, ctx):
    """No children and no MERGE: a single UPDATE of last_seen_commit and the publish audit columns."""
    record = contract_record(contract_doc)
    executor.on(r"^SELECT entity_id, record_version, repo_id", [current_row(record)])
    counts = uc.upsert_definitions([contract_record(contract_doc, commit="c2")], ctx)
    assert executor.tags == ["session", *CHILD_TAGS, "current", "update:md_data_contract"]
    sql, params = executor.calls[-1]
    assert "last_seen_commit = :git_commit" in sql and params["git_commit"] == "c2"
    assert "introduced_commit" not in sql and "payload" not in sql
    assert counts == PublishCounts(unchanged=1)


def test__a_hash_change_without_a_semver_bump_is_rejected_before_any_write(uc, executor, contract_doc, ctx):
    """The shared SCD2 rule applies on UC too: nothing but reads has run when the error is raised."""
    record = contract_record(contract_doc)
    executor.on(r"^SELECT entity_id, record_version, repo_id", [current_row(record, content_hash="different")])
    with pytest.raises(PublishError, match="bump the version"):
        uc.upsert_definitions([record], ctx)
    assert executor.tags[-1] == "current"


def test__an_entity_owned_by_another_repo_is_refused(uc, executor, contract_doc, ctx):
    """A current row with a different repo_id raises WriterError and nothing is written."""
    record = contract_record(contract_doc)
    executor.on(r"^SELECT entity_id, record_version, repo_id", [{**current_row(record), "repo_id": "other"}])
    with pytest.raises(WriterError):
        uc.upsert_definitions([record], ctx)
    assert executor.tags[-1] == "current"


def test__a_crash_after_the_children_leaves_the_parent_untouched_and_the_next_publish_cleans_orphans(
    uc, executor, contract_doc, ctx
):
    """The executor fails at the MERGE: children exist at N+1, no view or parent statement succeeded; retry cleans up."""
    record = contract_record(contract_doc)
    executor.on(r"^SELECT entity_id, record_version, repo_id", [current_row(record)])
    executor.on(r"^MERGE INTO .* USING \(SELECT k\.merge_key", error=RuntimeError("warehouse went away"))
    with pytest.raises(RuntimeError, match="went away"):
        uc.upsert_definitions([contract_record(edited(contract_doc))], ctx)
    assert executor.tags[-1] == "scd2-merge:md_data_contract"
    assert executor.tags[-5:-1] == INSERT_TAGS
    assert not any(tag in ("view", "create", "alter") for tag in executor.tags)
    assert "invariant" not in executor.tags

    healthy = RecordingExecutor()
    healthy.on(r"^SELECT entity_id, record_version, repo_id", [current_row(record)])
    healthy.on(r"WHERE NOT EXISTS", [{"num_affected_rows": 3}])
    retry = UcRepository(healthy, "main_cat").upsert_definitions([contract_record(edited(contract_doc))], ctx)
    assert healthy.tags[:6] == ["session", *CHILD_TAGS, "current"]
    assert healthy.tags.index("current") < healthy.tags.index("insert:md_contract_server")
    assert retry == PublishCounts(inserted=1, closed=1, orphans_cleaned=12)


def test__views_join_children_on_the_current_parent_version():
    """Because every child view requires child.record_version = parent.record_version, N+1 children stay invisible."""
    statements = UcStatements(build_model(UC_SCHEMA_PLACEHOLDER), "main_cat", "platform_meta").view_ddl()
    view = next(sql for sql in statements if "VIEW" in sql and "v_quality_rule_current" in sql)
    assert re.search(r"md_data_contract\.record_version = \S+md_quality_rule\.record_version", view)
    assert "md_data_contract.is_current IS true" in view


def test__the_retire_path_reads_the_current_version_then_publishes_a_retired_one(uc, executor, contract_doc, ctx):
    """Retiring reads the stored definition, then runs the normal children-then-MERGE sequence with status retired."""
    record = contract_record(contract_doc)
    parent = {
        **dict(record.columns),
        "entity_id": record.entity_id,
        "record_version": 1,
        "repo_id": SQUAD,
        "semver": "1.0.0",
        "status": "active",
        "payload": '{"id": "%s"}' % CONTRACT_ID,
        "source_path": "x",
        "last_seen_commit": "c1",
        "retired_commit": None,
    }
    executor.on(r"CAST\(`payload` AS STRING\)", [parent])
    executor.on(
        r"^SELECT entity_id, record_version, repo_id", [current_row(replace(record, payload={"id": CONTRACT_ID}))]
    )
    uc.retire_definitions("data_contract", [CONTRACT_ID], ctx, git_commit="c9")
    assert executor.tags[-2:] == ["scd2-merge:md_data_contract", "invariant"]
    merge_params = next(p for sql, p in executor.calls if sql.startswith("MERGE INTO"))
    assert (merge_params["status"], merge_params["retired_commit"], merge_params["record_version"]) == (
        "retired",
        "c9",
        2,
    )


def test__retiring_an_unknown_entity_is_refused(uc, ctx):
    """With no current row, retire raises PublishError."""
    with pytest.raises(PublishError, match="unknown"):
        uc.retire_definitions("data_contract", ["dc_missing"], ctx)


def test__facts_are_inserted_with_one_merge_that_only_inserts_when_absent(uc, executor, ctx):
    """A deployment is two MERGE ... WHEN NOT MATCHED THEN INSERT statements and the DB clock stamps recorded_at."""
    record = deployment(assets=1)
    executor.on(r"MERGE INTO .*md_deployment`", [{"num_inserted_rows": 1}])
    executor.on(r"MERGE INTO .*md_deployed_asset`", [{"num_inserted_rows": 1}])
    assert uc.record_deployment(record, ctx) == 2
    assert executor.tags == ["session", "merge:md_deployment", "merge:md_deployed_asset"]
    sql, params = executor.calls[1]
    assert "WHEN MATCHED" not in sql and "WHEN NOT MATCHED THEN INSERT" in sql
    assert "ON t.`target` = s.`target` AND t.`bundle` = s.`bundle` AND t.`ci_run_id` = s.`ci_run_id`" in sql
    assert "current_timestamp()" in sql and "p0_recorded_at" not in params and "p0_deployed_at" not in params
    assert params["p0_ci_run_attempt"] == 1


def test__tags_use_one_merge_with_not_matched_by_source_limited_to_owned_keys(uc, executor, ctx):
    """Upserting tags checks foreign rows, then one MERGE that deactivates only owned keys of this repo."""
    uc.upsert_tags([TagRow("asset", "s.t", "classification", "internal")], ctx, required_keys=["cost_center"])
    assert executor.tags == ["session", "foreign-check", "merge:md_tag"]
    sql, params = executor.calls[-1]
    assert "WHEN NOT MATCHED BY SOURCE AND t.repo_id = :stale_repo_id AND t.is_active = true" in sql
    assert "t.tag_key IN (:owned_key_0, :owned_key_1)" in sql and "startswith(t.tag_key, :owned_prefix_0)" in sql
    assert params["owned_key_0"] == "classification" and params["owned_key_1"] == "cost_center"
    assert params["stale_repo_id"] == SQUAD


def test__an_empty_tag_set_deactivates_with_a_scoped_update(uc, executor, ctx):
    """Publishing no tags runs one UPDATE limited to this repo's owned keys."""
    assert uc.upsert_tags([], ctx) == 0
    assert executor.tags == ["session", "update:md_tag"]
    assert "WHERE t.repo_id = :stale_repo_id" in executor.calls[-1][0]


def test__tag_rows_owned_by_another_repo_raise_writer_error(uc, executor, ctx):
    """If the foreign-row probe finds a key owned elsewhere, nothing is merged."""
    executor.on(r"^SELECT t\.`entity_type`", [{"entity_type": "asset", "repo_id": "other"}])
    with pytest.raises(WriterError):
        uc.upsert_tags([TagRow("asset", "s.t", "classification", "internal")], ctx)
    assert "merge:md_tag" not in executor.tags


def test__tags_with_non_owned_keys_are_refused(uc, ctx):
    """A non-owned key never reaches the database."""
    with pytest.raises(PublishError):
        uc.upsert_tags([TagRow("asset", "s.t", "random")], ctx)


def test__lineage_is_scoped_by_target_and_origin(uc, executor, ctx):
    """The stale predicate carries the target and origin so other scopes are untouched."""
    uc.record_lineage([LineageEdge("a.x", "a.y")], ctx, target="dev", origin=ORIGIN_SPEC)
    sql, params = executor.calls[-1]
    assert "t.`target` = :scope_target AND t.`origin` = :scope_origin" in sql
    assert (params["scope_target"], params["scope_origin"]) == ("dev", "spec")


def test__ensure_schema_creates_tables_with_cdf_then_records_the_version_then_views(uc, executor):
    """DDL order: schema, tables, change data feed, version read and insert, then views."""
    executor.on(r"max\(version\)", [{"version": None}])
    uc.ensure_schema()
    tags = executor.tags
    assert tags[0] == "session" and tags[1] == "create"
    assert tags.count("create") == 38 and tags.count("alter") == 37
    statements = [sql for sql, _ in executor.calls]
    version_read = next(i for i, sql in enumerate(statements) if "max(version)" in sql)
    version_write = next(
        i for i, sql in enumerate(statements) if sql.startswith("INSERT INTO") and "md_schema_version" in sql
    )
    assert tags.index("alter") < version_read < version_write < tags.index("view")


def test__ensure_schema_renders_databricks_ddl_for_the_qualified_schema(uc, executor):
    """Tables are created under catalog.schema with VARIANT and TIMESTAMP columns and change data feed enabled."""
    uc.ensure_schema()
    statements = [sql for sql, _ in executor.calls]
    joined = "\n".join(sql for sql in statements if sql.startswith("CREATE TABLE"))
    assert "CREATE TABLE IF NOT EXISTS `main_cat`.`platform_meta`.md_data_contract (" in joined
    assert "payload VARIANT NOT NULL" in joined and "valid_from TIMESTAMP NOT NULL" in joined
    cdf = "ALTER TABLE `main_cat`.`platform_meta`.`md_run` SET TBLPROPERTIES (delta.enableChangeDataFeed = true)"
    assert cdf in statements


def test__ensure_schema_refuses_a_newer_schema_version(uc, executor):
    """A stored version above the library's raises SchemaError before views are touched."""
    executor.on(r"max\(version\)", [{"version": 5}])
    with pytest.raises(SchemaError, match="newer"):
        uc.ensure_schema()
    assert "view" not in executor.tags


def test__catalog_and_schema_names_are_validated(executor):
    """Identifiers with quotes or spaces never reach a statement."""
    with pytest.raises(ConfigError):
        UcRepository(executor, "bad`name")
    with pytest.raises(ConfigError):
        UcRepository(executor, "cat", "bad schema")


def test__session_time_zone_is_set_once_before_the_first_statement(uc, executor, contract_doc, ctx):
    """The session time zone is pinned to UTC on first use only."""
    uc.upsert_definitions([contract_record(contract_doc)], ctx)
    uc.upsert_definitions([contract_record(contract_doc, commit="c3")], ctx)
    assert executor.calls[0][0] == "SET TIME ZONE 'UTC'"
    assert executor.tags.count("session") == 1


def test__no_runner_timestamp_is_bound_for_the_scd2_columns(uc, executor, contract_doc, ctx):
    """valid_from, valid_to and published_at come from current_timestamp() inside the MERGE."""
    uc.upsert_definitions([contract_record(contract_doc)], ctx)
    sql, params = next((s, p) for s, p in executor.calls if s.startswith("MERGE INTO"))
    assert "current_timestamp(), CAST(NULL AS TIMESTAMP), true, current_timestamp()" in sql
    assert not {"valid_from", "valid_to", "published_at"} & set(params)


def test__reads_and_upkeep_statements(uc, executor):
    """Listing ids, reading quality rules, cleaning orphans over all kinds, and purging use the expected statements."""
    executor.on(r"^SELECT entity_id FROM", [{"entity_id": "dc_a"}, {"entity_id": "dc_b"}])
    executor.on(
        r"FROM `main_cat`.`platform_meta`.`md_quality_rule` r",
        [{"contract_id": "dc_a", "record_version": 2, "rule_id": "r", "asset_id": "s.t"}],
    )
    executor.on(r"WHERE NOT EXISTS", [{"num_affected_rows": 1}])
    executor.on(r"^DELETE FROM .*recorded_at", [{"num_affected_rows": 4}])
    assert uc.list_current_entity_ids("data_contract", SQUAD) == ["dc_a", "dc_b"]
    assert "status <> 'retired'" in executor.calls[-1][0]
    assert uc.current_quality_rules(SQUAD)[0].record_version == 2
    assert uc.cleanup_orphans() == 10
    assert uc.purge_runtime(400) == 32  # 4 rows from each of the 8 runtime tables
    assert "INTERVAL 400 DAYS" in executor.calls[-1][0]


class _FakeCursor:
    """A DB-API cursor stand-in that records statements and returns one row."""

    description = [("a",), ("b",)]

    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        """Record the statement."""
        self.log.append((sql, params))

    def fetchall(self):
        """Return one row."""
        return [(1, "x")]


def test__the_databricks_connector_executor_returns_dict_rows_and_targets_the_warehouse():
    """Rows come back as dicts, parameters are passed natively, and http_path is derived from the warehouse id."""
    log, connected = [], {}

    class Connection:
        """A connection stand-in."""

        def cursor(self):
            """Return a recording cursor."""
            return _FakeCursor(log)

        def close(self):
            """Record that the connection was closed."""
            connected["closed"] = True

    def connect(**kwargs):
        connected.update(kwargs)
        return Connection()

    executor = DatabricksSqlConnectorExecutor(
        "host.example", "wh1", connection_options={"access_token": "t"}, connect=connect
    )
    assert executor.execute("SELECT :a", {"a": 1}) == [{"a": 1, "b": "x"}]
    executor.execute("SELECT 2")
    assert connected["http_path"] == "/sql/1.0/warehouses/wh1" and connected["access_token"] == "t"
    assert log == [("SELECT :a", {"a": 1}), ("SELECT 2", None)]
    executor.close()
    assert connected["closed"] is True


def test__the_spark_executor_uses_named_parameters():
    """spark.sql receives args= only when there are parameters, and rows become dicts."""
    calls = []

    class Frame:
        """A DataFrame stand-in."""

        def collect(self):
            """Return one row."""
            return [SimpleNamespace(asDict=lambda: {"n": 1})]

    class Spark:
        """A SparkSession stand-in."""

        def sql(self, sql, **kwargs):
            """Record the call and return a frame."""
            calls.append((sql, kwargs))
            return Frame()

    executor = SparkSqlExecutor(Spark())
    assert executor.execute("SELECT :x", {"x": 2}) == [{"n": 1}]
    executor.execute("SELECT 1")
    assert calls == [("SELECT :x", {"args": {"x": 2}}), ("SELECT 1", {})]
