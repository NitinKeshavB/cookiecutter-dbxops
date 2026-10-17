"""Tests of the command line, document discovery, and the post-run task."""

import subprocess
from types import SimpleNamespace

import pytest
import yaml

from dbxops.metastore.cli import main
from dbxops.metastore.errors import ConfigError
from dbxops.metastore.records import PublishContext
from dbxops.metastore.sources import (
    SubprocessBundleRunner,
    discover,
    read_repo_id,
)
from dbxops.metastore.sqlalchemy_repository import SqlAlchemyRepository
from dbxops.metastore.tasks import (
    WorkspaceJobsClient,
    publish_run,
)
from tests.consts import PROJECT_DIR
from tests.fixtures.metadata_repo import (
    ENVIRONMENTS,
    write_platform,
    write_yaml,
)
from tests.fixtures.metastore import (
    CONTRACT_DOC,
    CONTRACT_ID,
    PRODUCT_DOC,
    SQUAD,
    FakeEventLog,
    FakeJobs,
    contract_record,
    write_spec_bundle,
)
from tests.unit_tests.metastore.test_sqlalchemy_repository import rows

SILVER = "bundles/dataops/src/dataflows/claims/silver"


@pytest.fixture
def repo_root(tmp_path):
    """Return a repo tree with a contract, a product, a dataflow spec and the repo identity file."""
    write_platform(tmp_path)
    (tmp_path / "metadata/_platform/repo.yaml").write_text(yaml.safe_dump({"project_id": SQUAD, "kind": "project"}))
    write_yaml(tmp_path / "conf/environments.yml", ENVIRONMENTS)
    silver = tmp_path / SILVER
    (silver / "contracts").mkdir(parents=True)
    (silver / "contracts/fraud_scores.odcs.yaml").write_text(yaml.safe_dump(CONTRACT_DOC))
    write_spec_bundle(tmp_path)
    products = tmp_path / "bundles/dataops/src/products"
    products.mkdir(parents=True)
    (products / "dp.odps.yaml").write_text(yaml.safe_dump(PRODUCT_DOC))
    skipped = tmp_path / "framework/x"
    skipped.mkdir(parents=True)
    (skipped / "bad.odcs.yaml").write_text("not: a contract")
    return tmp_path


class FakeRunner:
    """A bundle runner stand-in."""

    def summary(self, _target):
        """Return a summary with one job."""
        return {"bundle": {"name": "dataops"}, "resources": {"jobs": {"main": {"id": "1", "name": "main"}}}}


def test__discovery_finds_documents_and_skips_framework_and_unrelated_json(repo_root):
    """Contracts, products and dataflow specs are found; the framework directory and non-spec json are not."""
    found = discover(repo_root)
    assert [path for path, _ in found.contracts] == [f"{SILVER}/contracts/fraud_scores.odcs.yaml"]
    assert len(found.products) == 1 and [info.spec.spec_id for info in found.specs] == ["spec_scores"]
    assert read_repo_id(repo_root) == SQUAD


def test__the_repo_id_file_is_required(tmp_path):
    """Without repo.yaml or a project_id the repo id cannot be inferred."""
    with pytest.raises(ConfigError, match="not found"):
        read_repo_id(tmp_path)
    (tmp_path / "metadata/_platform").mkdir(parents=True)
    (tmp_path / "metadata/_platform/repo.yaml").write_text("kind: project")
    with pytest.raises(ConfigError, match="no project_id"):
        read_repo_id(tmp_path)


def test__publish_runs_definitions_lineage_and_deployments(repo_root, repo, sqlite_engine, capsys):
    """One command publishes everything idempotently and a rerun reports unchanged rows."""
    argv = ["publish", "--target", "dev", "--root", str(repo_root), "--git-sha", "abc", "--published-by", "ci"]
    assert main(argv, build=lambda config, target: repo, runner=FakeRunner()) == 0
    assert "definitions: PublishCounts(inserted=5" in capsys.readouterr().out
    assert rows(sqlite_engine, "SELECT count(*) AS n FROM md_data_contract")[0]["n"] == 1
    assert rows(sqlite_engine, "SELECT bundle FROM md_deployment") == [{"bundle": "dataops"}]
    edges = rows(sqlite_engine, "SELECT source_asset_id, target_asset_id FROM md_lineage_edge")
    assert edges == [{"source_asset_id": "claims_fraud_silver.x", "target_asset_id": "claims_fraud_silver.y"}]
    assert rows(sqlite_engine, "SELECT count(*) AS n FROM md_tag WHERE is_active")[0]["n"] == 6
    assert main(argv, build=lambda config, target: repo, runner=FakeRunner()) == 0
    assert "definitions: PublishCounts(inserted=0, closed=0, unchanged=5" in capsys.readouterr().out


def test__publish_can_be_limited_with_what_and_retire_missing(repo_root, repo, sqlite_engine):
    """--what selects the parts; --retire-missing retires contracts that left the repo."""
    base = ["publish", "--target", "dev", "--root", str(repo_root)]
    assert main([*base, "--what", "definitions"], build=lambda c, t: repo) == 0
    assert rows(sqlite_engine, "SELECT count(*) AS n FROM md_deployment")[0]["n"] == 0
    (repo_root / SILVER / "contracts/fraud_scores.odcs.yaml").unlink()
    assert main([*base, "--what", "definitions", "--retire-missing"], build=lambda c, t: repo) == 0
    assert repo.get_current_definition("data_contract", CONTRACT_ID).status == "retired"


def test__bad_arguments_and_errors_give_exit_codes(repo_root, repo, capsys, tmp_path):
    """An unknown --what is exit 2; a metastore error is exit 1 with a message."""
    assert (
        main(["publish", "--target", "dev", "--root", str(repo_root), "--what", "nope"], build=lambda c, t: repo) == 2
    )
    assert "unknown --what" in capsys.readouterr().err
    assert main(["publish", "--target", "dev", "--root", str(tmp_path)], build=lambda c, t: repo) == 1
    assert "error:" in capsys.readouterr().err


def test__ensure_schema_command(sqlite_engine, capsys):
    """ensure-schema builds the repository for the target and creates the schema."""
    assert main(["ensure-schema", "--target", "dev"], build=lambda c, t: SqlAlchemyRepository(sqlite_engine)) == 0
    assert "schema ready" in capsys.readouterr().out
    assert rows(sqlite_engine, "SELECT version FROM md_schema_version") == [{"version": 1}]


def test__the_subprocess_bundle_runner_parses_json_and_reports_failures(tmp_path, monkeypatch):
    """`databricks bundle summary -o json -t T` output is parsed; a non-zero exit becomes ConfigError."""
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs["cwd"]))
        code = 0 if len(calls) == 1 else 1
        return subprocess.CompletedProcess(command, code, stdout='{"bundle": {"name": "b"}}', stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = SubprocessBundleRunner(tmp_path)
    assert runner.summary("dev") == {"bundle": {"name": "b"}}
    assert calls[0][0] == ["databricks", "bundle", "summary", "-o", "json", "-t", "dev"]
    with pytest.raises(ConfigError, match="boom"):
        runner.summary("dev")


def test__publish_run_records_the_run_and_pins_quality_to_the_current_rule(repo, sqlite_engine, contract_doc, ctx):
    """The post-run task needs the contract published, records facts once, and cleans orphans."""
    repo.upsert_definitions([contract_record(contract_doc)], ctx)
    context = PublishContext(SQUAD, git_commit="abc", target="dev")
    counts, collection = publish_run("42", repository=repo, context=context, readers=(FakeJobs(), FakeEventLog()))
    assert counts.inserted == 5 and counts.orphans_cleaned == 0
    assert [item.name for item in collection.unmapped] == ["unknown_expectation"]
    performance = rows(sqlite_engine, "SELECT update_id, run_id, full_refresh FROM md_run_performance")
    update = collection.run.pipeline_updates[0]
    assert performance == [{"update_id": update.update_id, "run_id": "42", "full_refresh": int(update.full_refresh)}]
    stored = rows(sqlite_engine, "SELECT contract_id, rule_record_version, dropped FROM md_quality_result")
    assert stored == [{"contract_id": CONTRACT_ID, "rule_record_version": 1, "dropped": 2}]
    again, _ = publish_run("42", repository=repo, context=context, readers=(FakeJobs(), FakeEventLog()))
    assert again.inserted == 0


def test__the_workspace_jobs_client_fetches_a_run_as_a_dict():
    """The adapter calls jobs.get_run with an integer id and returns as_dict()."""
    seen = {}

    def get_run(run_id):
        seen["id"] = run_id
        return SimpleNamespace(as_dict=lambda: {"run_id": run_id})

    client = WorkspaceJobsClient(SimpleNamespace(jobs=SimpleNamespace(get_run=get_run)))
    assert client.get_run("7") == {"run_id": 7} and seen["id"] == 7


def test__the_committed_data_dictionary_is_what_the_model_generates(capsys):
    """context/03_Architecture/Metadata_Dictionary.md equals the generated dictionary (run make metadata-dictionary)."""
    assert main(["dictionary"]) == 0
    committed = (PROJECT_DIR / "context/03_Architecture/Metadata_Dictionary.md").read_text(encoding="utf-8")
    assert capsys.readouterr().out == committed
