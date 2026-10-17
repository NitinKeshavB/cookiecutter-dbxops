"""
Job tasks that write to the metadata database (platform_meta).

``dbxops-publish-run`` records this run. It runs last (`run_if: ALL_DONE`, so it also records failed runs). It reads
the run from the Jobs API and each pipeline's event log, then writes the run, its pipeline updates, per-dataset row
counts, per-rule quality results (pinned to the contract rule versions that are current) and per-update performance,
clears any orphan rows from a crashed publish, asks the tuning advisor about the pipelines' recent performance, and
finally stores the run's own log events. Every line it logs is JSON on stdout with the run's context.

    dbxops-publish-run --run-id {{job.run_id}} --target <target> --repo-id <project_id> --config <metastore.yml>

``dbxops-publish-costs`` loads daily cost per project from ``system.billing.usage`` as the platform writer
(``_platform``); the platform team schedules it once a day per target (see ``context/06_Operations/Cost_And_Performance.md``).

    dbxops-publish-costs --target <target> --config <metastore.yml> [--meta-schema <catalog>.platform_meta]

Bundles run them as ``python_wheel_task`` (package ``cookie-cutter-dbx``), with the dbxops wheel (variable
``dbxops_wheel_path``) in the task's serverless environment. Both are idempotent: a retried task writes nothing twice.
"""

from __future__ import annotations

import argparse
import os
from datetime import date
from typing import Any

from dbxops.metastore.cost import (
    DEFAULT_LAG_DAYS,
    DEFAULT_WINDOW_DAYS,
    SparkBillingReader,
    billing_window,
    cost_records,
    spark_deployed_objects,
)
from dbxops.metastore.event_log import SparkPipelineEventLog
from dbxops.metastore.factory import RepositoryFactory
from dbxops.metastore.publisher import MetadataPublisher
from dbxops.metastore.records import (
    PLATFORM_REPO_ID,
    OperationsBatch,
    PublishContext,
)
from dbxops.metastore.repository import MetadataRepository
from dbxops.metastore.tasks import (
    WorkspaceJobsClient,
    publish_run,
)
from dbxops.metastore.tuning import advise
from dbxops.runlog import (
    RunEventBuffer,
    configure_run_logging,
    logger,
)


def _spark() -> Any:
    from pyspark.sql import SparkSession  # pylint: disable=import-outside-toplevel

    return SparkSession.builder.getOrCreate()


def _live_readers() -> tuple[Any, Any]:
    """Return the Jobs API client and the event-log reader, built from the task's workspace identity and Spark."""
    from databricks.sdk import WorkspaceClient  # pylint: disable=import-outside-toplevel

    return WorkspaceJobsClient(WorkspaceClient()), SparkPipelineEventLog(_spark())


def _record_tuning_and_events(repository: MetadataRepository, context: PublishContext, events: RunEventBuffer) -> None:
    """
    Record the advisor's recommendations and this run's log events.

    It runs after the run, its updates and quality results are recorded, so a failure here fails only this task
    (visibly, with the error in the task log) and never loses the run record.
    """
    recommendations = advise(repository.recent_performance(context.repo_id, context.target))
    for item in recommendations:
        logger.warning("tuning recommendation {}: {}", item.key.rule_code, item.message, asset=item.key.asset_id)
    logger.info("tuning: {} recommendation(s)", len(recommendations))
    if events.dropped:
        logger.warning("{} log events were not kept (limit {})", events.dropped, events.max_events)
    batch = OperationsBatch(recommendations=recommendations, run_events=list(events.events))
    MetadataPublisher(repository, context).publish_operations(batch)


def main(argv: list[str] | None = None, *, build: Any = RepositoryFactory.from_config, readers: Any = None) -> int:
    """Publish one finished (or finishing) run; return a process exit code."""
    parser = argparse.ArgumentParser(description="Record this job run in the metadata database.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--repo-id", required=True, help="the project id this job belongs to")
    parser.add_argument("--config", required=True, help="path of the metastore.yml copied into the bundle")
    args = parser.parse_args(argv)
    git_sha = os.environ.get("GIT_COMMIT")
    events = configure_run_logging(
        {"run_id": args.run_id, "target": args.target, "repo_id": args.repo_id, "git_sha": git_sha or ""}
    )
    context = PublishContext(args.repo_id, "dataops-job", git_sha, args.target)
    repository = build(args.config, args.target)
    counts, collection = publish_run(
        args.run_id, repository=repository, context=context, readers=readers or _live_readers()
    )
    logger.info(
        "run {}: {}; {} quality results, {} unmapped",
        args.run_id,
        counts,
        len(collection.quality),
        len(collection.unmapped),
    )
    _record_tuning_and_events(repository, context, events)
    return 0


def costs_main(
    argv: list[str] | None = None,
    *,
    build: Any = RepositoryFactory.from_config,
    reader: Any = None,
    deployed: Any = None,
    today: date | None = None,
) -> int:
    """Load daily costs for the billing window into ``md_cost_daily`` as ``_platform``; return an exit code."""
    parser = argparse.ArgumentParser(description="Load daily cost per project from system.billing.usage.")
    parser.add_argument("--target", required=True)
    parser.add_argument("--config", required=True, help="path of the metastore.yml")
    parser.add_argument("--days", type=int, default=DEFAULT_WINDOW_DAYS, help="how many days to load")
    parser.add_argument("--lag-days", type=int, default=DEFAULT_LAG_DAYS, help="leave the newest days to settle")
    parser.add_argument("--meta-schema", default="", help="<catalog>.platform_meta, to attribute by deployment")
    args = parser.parse_args(argv)
    configure_run_logging({"run_id": os.environ.get("DATABRICKS_RUN_ID", "local"), "target": args.target})
    start, end = billing_window(today or date.today(), args.lag_days, args.days)
    billing = reader or SparkBillingReader(_spark())
    if deployed is None:
        deployed = spark_deployed_objects(_spark(), args.meta_schema) if args.meta_schema else {}
    records = cost_records(billing.daily_usage(start, end), deployed)
    unattributed = sum(record.amount.list_cost for record in records if not record.owner.project_id)
    context = PublishContext(PLATFORM_REPO_ID, "cost-job", os.environ.get("GIT_COMMIT"), args.target)
    counts = MetadataPublisher(build(args.config, args.target), context).publish_costs(records)
    logger.info(
        "costs {}..{}: {} lines, {}; unattributed list cost {:.2f}", start, end, len(records), counts, unattributed
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
