"""Tests for the owner decision views: they render, parse as Databricks SQL and only reference real objects."""

import re
from pathlib import Path

import pytest
import sqlglot
from sqlglot import exp

from dbxops.insights import (
    deploy,
    render,
)
from dbxops.insights.__main__ import main
from dbxops.metastore.model import build_model

META_OBJECTS = set(build_model().tables) | set(build_model().views())
SYSTEM_TABLES = {
    "system.lakeflow.jobs",
    "system.lakeflow.job_run_timeline",
    "system.lakeflow.pipelines",
    "system.lakeflow.pipeline_update_timeline",
    "system.access.table_lineage",
    "system.information_schema.column_masks",
    "system.information_schema.table_privileges",
    "system.information_schema.row_filters",
    "system.storage.predictive_optimization_operations_history",
}
VIEWS = render("prod_cat")


class RecordingExecutor:
    """Keeps every statement it is asked to run."""

    def __init__(self):
        self.statements: list[str] = []

    def execute(self, sql, params=None):
        """Record ``sql``."""
        assert params is None
        self.statements.append(sql)


def tables_read(sql: str) -> set[str]:
    """Return the fully qualified tables a statement reads (CTE names excluded)."""
    tree = sqlglot.parse_one(sql, read="databricks")
    ctes = {cte.alias for cte in tree.find_all(exp.CTE)}
    created = tree.this.name if isinstance(tree, exp.Create) else ""
    found = set()
    for table in tree.find_all(exp.Table):
        parts = [part for part in (table.catalog, table.db, table.name) if part]
        if table.name in ctes or table.name == created:
            continue
        found.add(".".join(parts))
    return found


def test__every_view_renders_with_no_token_left():
    """Eight views, each named, with the catalog and both schemas filled in."""
    assert [view.name for view in VIEWS] == [
        "v_job_health",
        "v_pipeline_health",
        "v_cost_daily",
        "v_cost_per_million_rows",
        "v_access_review",
        "v_table_usage",
        "v_recommendations",
        "v_owner_summary",
    ]
    assert not any(re.search(r"\{\w+\}", view.sql) for view in VIEWS)
    assert all(f"CREATE OR REPLACE VIEW `prod_cat`.`platform_insights`.{view.name} AS" in view.sql for view in VIEWS)


@pytest.mark.parametrize("view", VIEWS, ids=[view.name for view in VIEWS])
def test__each_view_reads_only_system_tables_platform_meta_objects_or_earlier_views(view):
    """A typo in a table name fails here, offline, instead of at deploy time."""
    earlier = {f"prod_cat.platform_insights.{v.name}" for v in VIEWS[: VIEWS.index(view)]}
    for name in tables_read(view.sql):
        if name.startswith("prod_cat.platform_meta."):
            assert name.rsplit(".", 1)[1] in META_OBJECTS, name
        else:
            assert name in SYSTEM_TABLES | earlier, name


def test__catalog_and_schema_names_must_be_plain_identifiers():
    """Names are interpolated into SQL, so anything but a plain identifier is refused."""
    for bad in ("prod; DROP", "a.b", "`x`", ""):
        with pytest.raises(ValueError, match="plain identifier"):
            render(bad)
    with pytest.raises(ValueError, match="schema"):
        render("prod_cat", meta_schema="meta-data")


def test__deploy_creates_the_schema_then_every_view_in_order():
    """The schema comes first, then the views in dependency order."""
    executor = RecordingExecutor()
    names = deploy(executor, "dev_cat", insights_schema="insights")
    assert executor.statements[0] == "CREATE SCHEMA IF NOT EXISTS `dev_cat`.`insights`"
    assert names == [view.name for view in VIEWS] and len(executor.statements) == len(VIEWS) + 1


def test__the_command_line_renders_files_and_deploys(tmp_path: Path, capsys):
    """`render --out` writes one file per view; `deploy` runs them through the given executor."""
    assert main(["render", "--catalog", "dev_cat", "--out", str(tmp_path)]) == 0
    assert sorted(path.stem for path in tmp_path.glob("*.sql")) == sorted(view.name for view in VIEWS)
    assert main(["render", "--catalog", "dev_cat"]) == 0 and "v_owner_summary" in capsys.readouterr().out
    executor = RecordingExecutor()
    assert main(["deploy", "--catalog", "dev_cat"], executor=executor) == 0
    assert "deployed 8 views to dev_cat.platform_insights" in capsys.readouterr().out
