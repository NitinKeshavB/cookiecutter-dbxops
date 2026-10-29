"""
Every bundle under bundles/ agrees with conf/, the platform snapshots and its own dataflow specs.

These are the offline stand-ins for `databricks bundle validate`, which needs a workspace. They check the
bundle configuration against the CLI's own JSON schema, and that the names used in different files line up.
"""

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

from dbxops.findings import has_errors
from dbxops.metadata.repo import RepoContext
from dbxops.metadata.rules import validate
from dbxops.metadata.scaffold import (
    NOTIFICATION_PLACEHOLDERS,
    NOTIFICATIONS,
    PROVENANCE_TAGS,
)
from tests.consts import PROJECT_DIR
from tests.fixtures.bundle_schema import (
    bundle_dirs,
    load_bundle,
)

ENVIRONMENTS = yaml.safe_load((PROJECT_DIR / "conf/environments.yml").read_text(encoding="utf-8"))
TEAM = yaml.safe_load((PROJECT_DIR / "metadata/_platform/teams.yaml").read_text(encoding="utf-8"))["teams"][0]
REPO = yaml.safe_load((PROJECT_DIR / "metadata/_platform/repo.yaml").read_text(encoding="utf-8"))
UPSTREAM_VALIDATOR = PROJECT_DIR / "framework/lakeflow_framework/scripts/validate_dataflows.py"
BUNDLES = [pytest.param(path, id=path.name) for path in bundle_dirs()]
EXPECTED_TAGS = {"cost_center": "${var.cost_center}", "region": "${var.region}", **PROVENANCE_TAGS}


def unbracket(value: str) -> str:
    """Return a placeholder without the angle brackets used inside quoted config values."""
    return value.replace("<", "").replace(">", "")


def test__there_is_at_least_one_bundle():
    """The parametrised tests below would silently test nothing if bundle discovery broke."""
    assert [p.name for p in bundle_dirs()]


@pytest.mark.parametrize("bundle", BUNDLES)
def test__the_bundle_is_valid_against_the_cli_schema(bundle: Path, cli_bundle_validator):
    """databricks.yml plus its resource files satisfy the Databricks CLI's bundle JSON schema."""
    errors = sorted(cli_bundle_validator.iter_errors(load_bundle(bundle)), key=lambda e: list(e.absolute_path))
    assert not errors, [f"{'/'.join(map(str, e.absolute_path))}: {e.message[:160]}" for e in errors]


@pytest.mark.parametrize("bundle", BUNDLES)
def test__targets_catalogs_hosts_and_framework_paths_follow_conf(bundle: Path):
    """The bundle has exactly conf/environments.yml's targets, each with the same catalog, host and framework path."""
    targets = load_bundle(bundle)["targets"]
    assert list(targets) == list(ENVIRONMENTS["targets"])
    for name, target in targets.items():
        assert target["variables"]["catalog"] == ENVIRONMENTS["targets"][name]["catalog"], name
        assert target["workspace"]["host"] == ENVIRONMENTS["workspace"]["host"], name
        assert target["mode"] == ENVIRONMENTS["targets"][name]["mode"], name
        expected = f"/Workspace/Shared/.bundle/lakeflow_framework/{name}/{ENVIRONMENTS['framework_version']}/files/src"
        assert target["variables"]["framework_source_path"] == expected, name
        assert ("run_as" in target) == ENVIRONMENTS["targets"][name]["run_as_sp"], name


@pytest.mark.parametrize("bundle", BUNDLES)
def test__project_identity_permissions_and_tags_match_the_platform_snapshot(bundle: Path):
    """project_id, AD group permissions, tag defaults and the run-as principal come from the registration."""
    doc = load_bundle(bundle)
    assert doc["variables"]["project_id"]["default"] == REPO["project_id"]
    assert {p["group_name"]: p["level"] for p in doc["permissions"]} == {
        TEAM["groups"]["admins"]: "CAN_MANAGE",
        TEAM["groups"]["developers"]: "CAN_RUN",
        TEAM["groups"]["readers"]: "CAN_VIEW",
    }
    prod = doc["targets"]["prod"]
    assert unbracket(prod["variables"]["cost_center"]) == REPO["tags"]["cost_center"]
    assert unbracket(prod["variables"]["region"]) == REPO["tags"]["region"]
    assert (
        unbracket(prod["variables"]["service_principal_application_id"]) == REPO["service_principal"]["application_id"]
    )


@pytest.mark.parametrize("bundle", BUNDLES)
def test__every_pipeline_filters_one_flow_group_and_every_flow_group_has_a_pipeline(bundle: Path):
    """A pipeline's dataFlowGroupFilter names a real flow group, and no group is left without a pipeline."""
    ctx = RepoContext.load(PROJECT_DIR)
    groups = {info.spec.flow_group for info in ctx.specs if info.bundle_dir == bundle}
    pipelines = load_bundle(bundle)["resources"]["pipelines"]
    filters = {p["configuration"]["pipeline.dataFlowGroupFilter"] for p in pipelines.values()}
    assert filters == groups


@pytest.mark.parametrize("bundle", BUNDLES)
def test__jobs_only_reference_pipelines_the_bundle_defines_and_are_tagged(bundle: Path):
    """Pipeline tasks point at defined pipelines, every task's dependencies exist, and jobs and pipelines carry tags."""
    resources = load_bundle(bundle)["resources"]
    for job in resources["jobs"].values():
        keys = {t["task_key"] for t in job["tasks"]}
        assert job["tags"] == EXPECTED_TAGS and job["budget_policy_id"] == "${var.budget_policy_id}"
        for task in job["tasks"]:
            assert {d["task_key"] for d in task.get("depends_on", [])} <= keys
            if "pipeline_task" in task:
                name = re.fullmatch(
                    r"\$\{resources\.pipelines\.(\w+)\.id\}", task["pipeline_task"]["pipeline_id"]
                ).group(1)
                assert name in resources["pipelines"]
    for pipeline in resources["pipelines"].values():
        assert pipeline["tags"] == EXPECTED_TAGS and pipeline["budget_policy_id"] == "${var.budget_policy_id}"


@pytest.mark.parametrize("bundle", BUNDLES)
def test__every_job_notifies_teams_and_email_on_failure(bundle: Path):
    """Failures and duration warnings reach the squad's Teams destination; failures also reach its email."""
    for job in load_bundle(bundle)["resources"]["jobs"].values():
        assert {key: job[key] for key in NOTIFICATIONS} == NOTIFICATIONS


@pytest.mark.parametrize("bundle", BUNDLES)
def test__every_target_names_a_budget_policy_so_serverless_cost_is_attributed(bundle: Path):
    """Each target sets the budget policy and the Teams/email destinations; placeholders block deploys until filled."""
    for name, target in load_bundle(bundle)["targets"].items():
        for key in ("budget_policy_id", *NOTIFICATION_PLACEHOLDERS):
            assert target["variables"][key], (name, key)


def test__the_repository_metadata_is_valid():
    """All contracts, products, specs, substitutions and snapshots in this repo pass every metadata rule (make validate-metadata)."""
    findings = validate(RepoContext.load(PROJECT_DIR))
    assert not has_errors(findings), [str(f) for f in findings if f.severity == "error"]


@pytest.mark.skipif(
    not BUNDLES or not UPSTREAM_VALIDATOR.exists(),
    reason="no bundle yet (run make new-bundle), or the framework submodule is not checked out",
)
def test__every_spec_passes_the_frameworks_own_validator():
    """The pinned framework's validate_dataflows.py accepts every dataflow spec under bundles/ (make validate-specs)."""
    result = subprocess.run(
        [sys.executable, "-I", str(UPSTREAM_VALIDATOR), str(PROJECT_DIR / "bundles")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-500:]
    assert "Failed: 0" in re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)


@pytest.mark.parametrize("bundle", BUNDLES)
def test__wheel_tasks_name_an_installed_package_and_a_real_entry_point(bundle: Path):
    """Every python_wheel_task uses this package's distribution name and a console script it declares."""
    project = tomllib.loads((PROJECT_DIR / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    for job in load_bundle(bundle)["resources"]["jobs"].values():
        for task in job["tasks"]:
            wheel = task.get("python_wheel_task")
            if wheel:
                assert wheel["package_name"] == project["name"]
                assert wheel["entry_point"] in project["scripts"]
                assert task["environment_key"] in {e["environment_key"] for e in job["environments"]}


@pytest.mark.parametrize("bundle", BUNDLES)
def test__every_job_records_its_run_after_everything_else_even_on_failure(bundle: Path):
    """Each job ends with a publish_run_metadata task that runs whatever happened before it."""
    for job in load_bundle(bundle)["resources"]["jobs"].values():
        publish = next(t for t in job["tasks"] if t["task_key"] == "publish_run_metadata")
        assert publish["run_if"] == "ALL_DONE"
        assert not any(publish["task_key"] in [d["task_key"] for d in t.get("depends_on", [])] for t in job["tasks"])
