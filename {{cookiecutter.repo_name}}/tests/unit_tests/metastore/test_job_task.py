"""Tests for the `dbxops-publish-run` job task that records a run in the metadata database."""

from dbxops.metastore import job_task
from dbxops.metastore.event_log import SparkPipelineEventLog
from tests.unit_tests.metastore.test_event_log import (
    FLOW_ROW,
    PIPELINE,
    UPDATE_ROW,
    FakeSpark,
)
from tests.unit_tests.metastore.test_sqlalchemy_repository import rows


class FakeJobs:
    """A Jobs API client returning one live run: generate and the pipeline task succeeded, the run has no result yet."""

    def get_run(self, run_id):
        """Return the run."""
        return {
            "run_id": int(run_id),
            "job_id": 9,
            "state": {"life_cycle_state": "RUNNING"},
            "start_time": 1_000,
            "end_time": 0,
            "trigger": "PERIODIC",
            "tasks": [
                {
                    "task_key": "silver",
                    "state": {"result_state": "SUCCESS"},
                    "pipeline_task": {"pipeline_id": PIPELINE},
                },
                {"task_key": "publish_run_metadata", "state": {"life_cycle_state": "RUNNING"}},
            ],
        }


def test__the_task_records_the_run_its_updates_and_row_counts(repo, sqlite_engine, capsys):
    """Run, pipeline update and dataset metrics land in the database; the live run is recorded as SUCCESS."""
    readers = (FakeJobs(), SparkPipelineEventLog(FakeSpark([UPDATE_ROW], [FLOW_ROW])))
    argv = ["--run-id", "42", "--target", "dev", "--repo-id", "example_claims", "--config", "unused.yml"]
    assert job_task.main(argv, build=lambda config, target: repo, readers=readers) == 0
    assert "run 42:" in capsys.readouterr().out
    run = rows(sqlite_engine, "SELECT status, job_asset_id FROM md_run")
    assert run == [{"status": "SUCCESS", "job_asset_id": "uc_job/example_claims/9"}]
    metrics = rows(sqlite_engine, "SELECT asset_id, rows_written, rows_dropped FROM md_dataset_metrics")
    assert metrics == [{"asset_id": "example_claims_silver.fact_premium", "rows_written": 98, "rows_dropped": 2}]
    events = rows(sqlite_engine, "SELECT run_id, seq, level, message FROM md_run_event ORDER BY seq")
    assert [e["run_id"] for e in events] == ["42", "42"] and events[0]["message"].startswith("run 42:")
    assert rows(sqlite_engine, "SELECT count(*) AS n FROM md_run_performance")[0]["n"] == 1


def test__rerunning_the_task_writes_nothing_twice(repo, sqlite_engine):
    """A retried task is idempotent: one run row, one update row."""
    readers = (FakeJobs(), SparkPipelineEventLog(FakeSpark([UPDATE_ROW], [FLOW_ROW])))
    argv = ["--run-id", "42", "--target", "dev", "--repo-id", "example_claims", "--config", "unused.yml"]
    for _ in range(2):
        assert job_task.main(argv, build=lambda config, target: repo, readers=readers) == 0
    assert rows(sqlite_engine, "SELECT count(*) AS n FROM md_run")[0]["n"] == 1
    assert rows(sqlite_engine, "SELECT count(*) AS n FROM md_pipeline_update")[0]["n"] == 1
