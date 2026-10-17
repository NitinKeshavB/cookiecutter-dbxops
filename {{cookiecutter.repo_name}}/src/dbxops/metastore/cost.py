"""
Daily cost per project from ``system.billing.usage``, attributed to the repository and commit that deployed it.

The platform team runs this once a day as ``_platform`` (``dbxops-publish-costs``). Each billing line is attributed in
this order, and the method is stored in ``attribution``:

1. ``tag``: the line's ``custom_tags`` carry ``project_id`` (and ``git_repo``, ``git_sha``, ``bundle``, ``target``),
   which every bundle sets on its jobs and pipelines and every project's budget policy carries for serverless;
2. ``deployment``: the job or pipeline id is a workspace object some deployment recorded in ``md_deployed_asset``;
3. ``job_name``: the job name starts with the ``[<project_id>]`` prefix every bundle gives its jobs;
4. ``none``: untagged spend, reported by ``v_cost_by_project`` under an empty project id.

Billing rows keep arriving for a few hours after usage, and a day once loaded is never changed (facts are inserted
if absent), so the default window ends ``lag_days`` before today.

Unverified against a live workspace: the ``system.billing`` column paths below follow the Databricks documentation
(``usage_metadata.job_id``, ``usage_metadata.job_name``, ``usage_metadata.dlt_pipeline_id``,
``list_prices.pricing.effective_list.default``).
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import (
    date,
    timedelta,
)
from typing import (
    Any,
    Iterable,
    Mapping,
    Protocol,
)

from dbxops.metastore.records import (
    CostAmount,
    CostOwner,
    CostRecord,
)

DEFAULT_LAG_DAYS = 2
DEFAULT_WINDOW_DAYS = 3
_PROJECT_IN_NAME = re.compile(r"\[([a-z][a-z0-9_]{2,39})\]")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

BILLING_SQL = """
SELECT CAST(u.usage_date AS STRING) AS usage_date,
       u.sku_name AS sku_name,
       u.usage_metadata.job_id AS job_id,
       u.usage_metadata.job_name AS job_name,
       u.usage_metadata.dlt_pipeline_id AS pipeline_id,
       to_json(u.custom_tags) AS custom_tags,
       lp.currency_code AS currency,
       SUM(u.usage_quantity) AS dbus,
       SUM(u.usage_quantity * lp.pricing.effective_list.default) AS list_cost
FROM system.billing.usage u
JOIN system.billing.list_prices lp
  ON lp.sku_name = u.sku_name
 AND lp.cloud = u.cloud
 AND lp.usage_unit = u.usage_unit
 AND u.usage_end_time >= lp.price_start_time
 AND (lp.price_end_time IS NULL OR u.usage_end_time < lp.price_end_time)
WHERE u.usage_date BETWEEN DATE'{start}' AND DATE'{end}'
  AND u.usage_unit = 'DBU'
GROUP BY ALL
"""

DEPLOYED_OBJECTS_SQL = """
SELECT workspace_object_id, repo_id, target, bundle, git_sha
FROM (
  SELECT a.workspace_object_id, d.repo_id, d.target, d.bundle, d.git_sha,
         row_number() OVER (PARTITION BY a.workspace_object_id ORDER BY d.deployed_at DESC) AS newest
  FROM {schema}.md_deployed_asset a
  JOIN {schema}.md_deployment d ON d.deployment_id = a.deployment_id
  WHERE a.workspace_object_id <> ''
)
WHERE newest = 1
"""


class BillingReader(Protocol):
    """Reads billing lines, already priced and grouped per day, SKU, workspace object and tag set."""

    def daily_usage(self, start: date, end: date) -> Iterable[Mapping[str, Any]]:
        """Return rows with ``usage_date``, ``sku_name``, ``job_id``, ``job_name``, ``pipeline_id``, ..."""


class SparkBillingReader:
    """A ``BillingReader`` over ``spark.sql`` and the ``system.billing`` tables."""

    def __init__(self, spark: Any):
        self._spark = spark

    def daily_usage(self, start: date, end: date) -> Iterable[Mapping[str, Any]]:
        """Return the priced billing lines between ``start`` and ``end`` (inclusive)."""
        sql = BILLING_SQL.format(start=start.isoformat(), end=end.isoformat())
        return [row.asDict() for row in self._spark.sql(sql).collect()]


def spark_deployed_objects(spark: Any, schema: str) -> dict[str, CostOwner]:
    """Return the newest deployment of every recorded workspace object, read from a Unity Catalog ``platform_meta``."""
    if not re.fullmatch(r"[A-Za-z0-9_`]+\.[A-Za-z0-9_`]+", schema):
        raise ValueError(f"expected <catalog>.<schema>, got {schema!r}")
    rows = spark.sql(DEPLOYED_OBJECTS_SQL.format(schema=schema)).collect()
    return {
        str(row["workspace_object_id"]): CostOwner(
            project_id=row["repo_id"], target=row["target"], bundle=row["bundle"], git_sha=row["git_sha"] or ""
        )
        for row in rows
    }


def billing_window(
    today: date, lag_days: int = DEFAULT_LAG_DAYS, days: int = DEFAULT_WINDOW_DAYS
) -> tuple[date, date]:
    """Return the ``(start, end)`` days to load: ``days`` days ending ``lag_days`` before ``today``."""
    end = today - timedelta(days=lag_days)
    return end - timedelta(days=days - 1), end


def _tags(raw: Any) -> dict[str, str]:
    if isinstance(raw, Mapping):
        return {str(key): str(value) for key, value in raw.items()}
    if isinstance(raw, str) and raw:
        return {str(key): str(value) for key, value in json.loads(raw).items()}
    return {}


def _from_tags(tags: Mapping[str, str]) -> CostOwner | None:
    if not tags.get("project_id"):
        return None
    return CostOwner(
        project_id=tags["project_id"],
        target=tags.get("target", ""),
        bundle=tags.get("bundle", ""),
        git_repo=tags.get("git_repo", ""),
        git_sha=tags.get("git_sha", ""),
        attribution="tag",
    )


def _from_job_name(job_name: Any) -> CostOwner | None:
    found = _PROJECT_IN_NAME.findall(str(job_name or ""))
    return CostOwner(project_id=found[-1], attribution="job_name") if found else None


def attribute(row: Mapping[str, Any], deployed: Mapping[str, CostOwner]) -> CostOwner:
    """Return who a billing line belongs to: tags first, then the deployment record, then the job-name prefix."""
    owner = _from_tags(_tags(row.get("custom_tags")))
    if owner is not None:
        return owner
    object_id = str(row.get("job_id") or row.get("pipeline_id") or "")
    if object_id in deployed:
        return replace(deployed[object_id], attribution="deployment")
    return _from_job_name(row.get("job_name")) or CostOwner()


def _object(row: Mapping[str, Any]) -> tuple[str, str]:
    if row.get("job_id"):
        return "job", str(row["job_id"])
    if row.get("pipeline_id"):
        return "pipeline", str(row["pipeline_id"])
    return "other", ""


def cost_records(
    rows: Iterable[Mapping[str, Any]], deployed: Mapping[str, CostOwner] | None = None
) -> list[CostRecord]:
    """Return one ``CostRecord`` per day, workspace object and SKU, summing lines that share them."""
    merged: dict[tuple[str, str, str, str], CostRecord] = {}
    for row in rows:
        usage_date = str(row["usage_date"])
        if not _DATE.match(usage_date):
            raise ValueError(f"usage_date {usage_date!r} is not YYYY-MM-DD")
        object_type, object_id = _object(row)
        key = (usage_date, object_type, object_id, str(row["sku_name"]))
        record = CostRecord(
            usage_date=usage_date,
            object_type=object_type,
            object_id=object_id,
            sku_name=key[3],
            amount=CostAmount(
                float(row.get("dbus") or 0.0), float(row.get("list_cost") or 0.0), str(row.get("currency") or "")
            ),
            owner=attribute(row, deployed or {}),
        )
        earlier = merged.get(key)
        merged[key] = record if earlier is None else replace(earlier, amount=earlier.amount + record.amount)
    return list(merged.values())
