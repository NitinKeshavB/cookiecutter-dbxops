"""Tests for cost attribution, the burn-rate view, the tuning advisor and who may write operational facts."""

from datetime import (
    date,
    datetime,
    timedelta,
    timezone,
)

import pytest

from dbxops.metastore import job_task
from dbxops.metastore.cli import main as cli_main
from dbxops.metastore.cost import (
    attribute,
    billing_window,
    cost_records,
)
from dbxops.metastore.errors import WriterError
from dbxops.metastore.performance import performance_from_run
from dbxops.metastore.publisher import MetadataPublisher
from dbxops.metastore.records import (
    PLATFORM_REPO_ID,
    CostOwner,
    OperationsBatch,
    PerformanceRecord,
    PipelineUpdateRecord,
    PublishContext,
    RunRecord,
    Timing,
    TuningDecision,
    TuningKey,
)
from dbxops.metastore.sources import read_repo_id
from dbxops.metastore.tuning import (
    TuningPolicy,
    advise,
)
from tests.consts import PROJECT_DIR
from tests.fixtures.metadata_repo import write_yaml
from tests.fixtures.metastore import SQUAD
from tests.unit_tests.metastore.test_sqlalchemy_repository import rows

PLATFORM = PublishContext(PLATFORM_REPO_ID, target="prod")
SQUAD_CTX = PublishContext(SQUAD, target="prod")


def billing_row(day: str, cost: float, **extra) -> dict:
    """Return one priced billing line as the billing reader yields it."""
    return {"usage_date": day, "sku_name": "JOBS_SERVERLESS", "dbus": cost / 0.5, "list_cost": cost, **extra}


# ------------------------------------------------------------------- cost attribution


def test__tags_win_then_the_deployment_record_then_the_job_name_prefix():
    """Each billing line is attributed by the most reliable evidence it carries, and says which one it used."""
    tagged = {"custom_tags": '{"project_id": "claims_fraud", "git_repo": "org/dbx-claims-fraud", "git_sha": "abc"}'}
    assert attribute(tagged, {}) == CostOwner(
        "claims_fraud", git_repo="org/dbx-claims-fraud", git_sha="abc", attribution="tag"
    )
    deployed = {"77": CostOwner("policy_core", target="prod", bundle="dataops", git_sha="def")}
    assert attribute({"pipeline_id": "77"}, deployed).attribution == "deployment"
    named = {"job_id": "5", "job_name": "[dev nitin] [claims_fraud] dataops run (dev_nitin)"}
    assert attribute(named, {}) == CostOwner("claims_fraud", attribution="job_name")
    assert attribute({"job_id": "6", "job_name": "ad hoc"}, {}) == CostOwner()


def test__lines_of_the_same_day_object_and_sku_are_summed_and_dated_by_day_index():
    """Two tag sets on one job and day become one row; day_index is days since 1970-01-01."""
    records = cost_records(
        [
            billing_row("2026-10-01", 2.0, job_id="5", custom_tags='{"project_id": "claims_fraud"}'),
            billing_row("2026-10-01", 3.0, job_id="5", custom_tags='{"project_id": "claims_fraud"}'),
            billing_row("2026-10-01", 1.0, pipeline_id="p1"),
        ]
    )
    by_object = {(r.object_type, r.object_id): r for r in records}
    assert (
        by_object[("job", "5")].amount.list_cost == 5.0 and by_object[("job", "5")].owner.project_id == "claims_fraud"
    )
    assert by_object[("pipeline", "p1")].owner.attribution == "none"
    assert by_object[("job", "5")].day_index == (date(2026, 10, 1) - date(1970, 1, 1)).days
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        cost_records([billing_row("01/10/2026", 1.0)])


def test__the_billing_window_leaves_the_newest_days_to_settle():
    """Billing rows arrive late and loaded days never change, so the window ends lag_days before today."""
    assert billing_window(date(2026, 10, 18), lag_days=2, days=3) == (date(2026, 10, 14), date(2026, 10, 16))


def test__the_cost_job_loads_costs_once_and_the_burn_rate_view_compares_with_the_week_before(repo, sqlite_engine):
    """Costs are loaded as _platform, a rerun inserts nothing, and v_burn_rate shows growth against the 7-day mean."""

    class Billing:
        """Ten days of 10.0 a day, then 20.0 on the latest day."""

        def daily_usage(self, _start, _end):
            """Return the billing lines."""
            days = [date(2026, 10, 1) + timedelta(days=n) for n in range(11)]
            return [
                billing_row(day.isoformat(), 20.0 if n == 10 else 10.0, job_id="5", custom_tags='{"project_id": "cf"}')
                for n, day in enumerate(days)
            ] + [billing_row("2026-10-11", 4.0, job_id="9")]

    argv = ["--target", "prod", "--config", "unused.yml"]

    def run() -> int:
        return job_task.costs_main(argv, build=lambda c, t: repo, reader=Billing(), deployed={})

    assert run() == 0 and run() == 0
    assert rows(sqlite_engine, "SELECT count(*) AS n FROM md_cost_daily")[0]["n"] == 12
    burn = {r["project_id"]: r for r in rows(sqlite_engine, "SELECT * FROM v_burn_rate")}
    assert burn["cf"]["cost_latest_day"] == 20.0 and burn["cf"]["avg_daily_cost_prev_7d"] == 10.0
    assert burn["cf"]["growth_pct_vs_7d"] == 100.0 and burn["cf"]["projected_30d_cost"] == 300.0
    assert burn[""]["cost_latest_day"] == 4.0  # untagged spend is visible under an empty project id


def test__only_the_registry_writes_costs_and_only_squads_write_operational_facts(repo):
    """The writer guard keeps cross-project cost loads with _platform and per-run facts with the squad."""
    record = cost_records([billing_row("2026-10-01", 1.0, job_id="5")])
    with pytest.raises(WriterError, match="daily costs"):
        MetadataPublisher(repo, SQUAD_CTX).publish_costs(record)
    with pytest.raises(WriterError, match="operational facts"):
        MetadataPublisher(repo, PLATFORM).publish_operations(OperationsBatch())


# ------------------------------------------------------------------- performance and tuning


def perf(update: str, seconds: float, rows_written: int = 1000, full_refresh: bool = False) -> PerformanceRecord:
    """Return one performance fact of pipeline p1."""
    return PerformanceRecord("prod", update, "r", "uc_pipeline/p1", seconds, rows_written, full_refresh)


def test__performance_comes_from_the_runs_own_timings_and_row_counts():
    """Duration is end minus start, rows include upserts, and an update without an end has no duration."""
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    run = RunRecord(
        "prod",
        "42",
        "uc_job/x/1",
        "SUCCESS",
        pipeline_updates=[
            PipelineUpdateRecord(
                "u1", "uc_pipeline/p1", "COMPLETED", timing=Timing(start, start + timedelta(seconds=40))
            ),
            PipelineUpdateRecord("u2", "uc_pipeline/p2", "RUNNING", timing=Timing(start, None)),
        ],
    )
    first, second = performance_from_run(run)
    assert first.duration_seconds == 40.0 and second.duration_seconds is None and second.rows_per_second is None


def test__the_advisor_flags_slow_updates_throughput_drops_and_repeated_full_refreshes():
    """Each rule compares the newest update with the median of the earlier ones and keys on that update."""
    history = [perf("u9", 300, rows_written=1000, full_refresh=True)]
    history += [perf(f"u{n}", 100, rows_written=10_000, full_refresh=n < 3) for n in range(8, 0, -1)]
    found = {item.key.rule_code: item for item in advise(history)}
    assert set(found) == {"slower_than_baseline", "throughput_drop", "repeated_full_refresh"}
    assert found["slower_than_baseline"].key == TuningKey("prod", "uc_pipeline/p1", "slower_than_baseline", "u9")
    assert found["slower_than_baseline"].expected_saving_pct == pytest.approx(66.7)
    assert not advise(history[:3])  # two earlier updates are too few to compare speed against
    assert not advise([perf("u2", 110), perf("u1", 100), perf("u0", 100), perf("um", 100)])
    assert not advise(history, TuningPolicy(slower_ratio=4.0, throughput_ratio=0.01, full_refresh_limit=9))


def test__a_decision_closes_a_recommendation_in_the_open_view(repo, sqlite_engine):
    """v_open_tuning_recommendations lists recommendations nobody has answered yet."""
    recommendations = advise([perf("u9", 300)] + [perf(f"u{n}", 100) for n in range(5)])
    publisher = MetadataPublisher(repo, SQUAD_CTX)
    publisher.publish_operations(OperationsBatch(recommendations=recommendations))
    assert {r["rule_code"] for r in rows(sqlite_engine, "SELECT * FROM v_open_tuning_recommendations")} == {
        "slower_than_baseline",
        "throughput_drop",
    }
    decision = TuningDecision(recommendations[0].key, "rejected", "nitin", "planned backfill")
    publisher.publish_operations(OperationsBatch(tuning_decisions=[decision]))
    still_open = rows(sqlite_engine, "SELECT rule_code FROM v_open_tuning_recommendations")
    assert still_open == [{"rule_code": recommendations[1].key.rule_code}]


# ------------------------------------------------------------------- command line


def test__publish_decisions_records_each_bronze_tables_method_against_the_recommendation(
    repo, sqlite_engine, repo_root
):
    """`publish --what decisions` writes one md_design_decision per ingestion profile, once per commit."""
    profiles = {
        "orders_raw": {"source": "files", "method": "autoloader"},
        "rates": {"source": "database", "method": "custom", "justification": "no connector for DB2"},
    }
    write_yaml(repo_root / "bundles/dataops/src/config/ingestion.yml", {"tables": profiles})
    argv = ["publish", "--target", "dev", "--root", str(repo_root), "--what", "decisions", "--git-sha", "abc"]
    for _ in range(2):
        assert cli_main(argv, build=lambda c, t: repo) == 0
    stored = rows(sqlite_engine, "SELECT asset_id, recommended, chosen, justification FROM md_design_decision")
    project = read_repo_id(repo_root)
    assert sorted(stored, key=lambda row: row["asset_id"]) == [
        {
            "asset_id": f"{project}_bronze.orders_raw",
            "recommended": "autoloader",
            "chosen": "autoloader",
            "justification": "",
        },
        {
            "asset_id": f"{project}_bronze.rates",
            "recommended": "custom",
            "chosen": "custom",
            "justification": "no connector for DB2",
        },
    ]


def test__decide_closes_an_open_recommendation(repo, sqlite_engine):
    """`decide` records a person's answer, and the recommendation leaves v_open_tuning_recommendations."""
    recommendation = advise([perf("u9", 300, rows_written=100_000)] + [perf(f"u{n}", 100, 100_000) for n in range(5)])
    context = PublishContext(read_repo_id(PROJECT_DIR), target="prod")
    MetadataPublisher(repo, context).publish_operations(OperationsBatch(recommendations=recommendation))
    key = recommendation[0].key
    argv = ["decide", "--target", "prod", "--root", str(PROJECT_DIR), "--asset", key.asset_id, "--rule", key.rule_code]
    argv += ["--evidence", key.evidence_key, "--decision", "accepted", "--by", "nitin", "--reason", "add clustering"]
    assert cli_main(argv, build=lambda c, t: repo) == 0
    assert rows(sqlite_engine, "SELECT decision, decided_by FROM md_tuning_decision") == [
        {"decision": "accepted", "decided_by": "nitin"}
    ]
    still_open = rows(sqlite_engine, "SELECT rule_code FROM v_open_tuning_recommendations")
    assert still_open == [{"rule_code": item.key.rule_code} for item in recommendation[1:]]
