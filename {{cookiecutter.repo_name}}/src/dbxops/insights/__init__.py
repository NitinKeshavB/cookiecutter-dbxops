"""
The owner decision model: a ``platform_insights`` schema of views over ``platform_meta`` and Databricks system tables.

``platform_meta`` holds what the platform declared, deployed, ran and decided; the system tables hold what Databricks
observed (job and pipeline runs, billing, grants, lineage, predictive optimization). The views join the two so a
technical owner sees, per project: job and pipeline health, cost per day, commit and million rows, who can read which
personal data, how tables are used and maintained downstream, and one ranked list of recommendations to accept or
reject (``v_recommendations``, summarised by ``v_owner_summary``).

The SQL lives in ``dbxops/insights/sql`` with three tokens: ``{meta}`` and ``{insights}`` (quoted
``catalog.schema``) and ``{catalog_literal}`` (the catalog as a string literal). ``render`` substitutes them;
``deploy`` creates the schema and the views through any ``SqlExecutor`` (Spark in a job, or a SQL warehouse).

Unverified against a live workspace: the system-table columns follow the Databricks documentation (``system.lakeflow``,
``system.access.table_lineage``, ``system.information_schema``, ``system.storage``); the job and pipeline timeline
tables are in preview in some regions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from typing import (
    Any,
    Mapping,
    Protocol,
)

DEFAULT_META_SCHEMA = "platform_meta"
DEFAULT_INSIGHTS_SCHEMA = "platform_insights"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,254}$")
_VIEW_NAME = re.compile(r"CREATE OR REPLACE VIEW \{insights\}\.(\w+)")


class SqlExecutor(Protocol):
    """Runs one SQL statement (the ``dbxops.metastore.uc_repository`` executors satisfy this)."""

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> Any:
        """Run ``sql``."""


@dataclass(frozen=True)
class View:
    """One rendered view: its name and its ``CREATE OR REPLACE VIEW`` statement."""

    name: str
    sql: str


def _check(name: str, what: str) -> str:
    if not _IDENTIFIER.match(name):
        raise ValueError(f"{what} {name!r} is not a plain identifier")
    return name


def _statements() -> list[str]:
    folder = resources.files("dbxops.insights").joinpath("sql")
    texts = [item.read_text(encoding="utf-8") for item in sorted(folder.iterdir(), key=lambda item: item.name)]
    return [part.strip() for text in texts for part in text.split(";\n") if "CREATE OR REPLACE VIEW" in part]


def render(
    catalog: str, meta_schema: str = DEFAULT_META_SCHEMA, insights_schema: str = DEFAULT_INSIGHTS_SCHEMA
) -> list[View]:
    """Return every view with the catalog and schemas substituted, in dependency order."""
    tokens = {
        "{meta}": f"`{_check(catalog, 'catalog')}`.`{_check(meta_schema, 'schema')}`",
        "{insights}": f"`{catalog}`.`{_check(insights_schema, 'schema')}`",
        "{catalog_literal}": catalog,
    }
    views = []
    for statement in _statements():
        name = _VIEW_NAME.search(statement).group(1)
        for token, value in tokens.items():
            statement = statement.replace(token, value)
        views.append(View(name, statement))
    return views


def deploy(executor: SqlExecutor, catalog: str, meta_schema: str = DEFAULT_META_SCHEMA, **options: str) -> list[str]:
    """Create the insights schema and (re)create every view; return the view names."""
    insights_schema = options.get("insights_schema", DEFAULT_INSIGHTS_SCHEMA)
    views = render(catalog, meta_schema, insights_schema)
    executor.execute(f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{insights_schema}`")
    for view in views:
        executor.execute(view.sql)
    return [view.name for view in views]


__all__ = ["DEFAULT_INSIGHTS_SCHEMA", "DEFAULT_META_SCHEMA", "View", "deploy", "render"]
