"""Tests for the table-defaults and ingestion-method rules, and the ingestion recommender."""

import copy
from pathlib import Path

import pytest

from dbxops.findings import Finding
from dbxops.metadata.__main__ import main as metadata_main
from dbxops.metadata.ingestion import (
    IngestionProfile,
    SourceFacts,
    recommend,
)
from dbxops.metadata.repo import RepoContext
from dbxops.metadata.rules import validate
from tests.fixtures.metadata_repo import (
    SPEC,
    write_json,
    write_yaml,
)

SILVER_SPEC = "bundles/dataops/src/dataflows/silver/dataflowspec/orders_main.json"
BRONZE_SPEC = "bundles/dataops/src/dataflows/bronze/dataflowspec/orders_raw_main.json"
INGESTION = "bundles/dataops/src/config/ingestion.yml"
DEFAULTS = {
    "required_table_properties": {"delta.enableRowTracking": "true"},
    "clustering": "required",
    "change_data_feed": "only_when_read",
    "exceptions": {},
}


def findings(root: Path, rule: str) -> list[Finding]:
    """Return the findings of one rule."""
    return [f for f in validate(RepoContext.load(root)) if f.rule == rule]


def bronze_spec(table_properties: dict) -> dict:
    """Return a bronze Auto Loader spec writing orders_raw, the table the silver example reads."""
    return {
        "data_flow_id": "orders_raw",
        "data_flow_group": "bronze",
        "data_flow_type": "nodespec",
        "tags": SPEC["tags"],
        "nodes": [
            {
                "name": "v_files",
                "node_type": "source",
                "source_type": "cloud_files",
                "config": {"mode": "stream", "path": "/Volumes/x/y/landing/orders"},
            },
            {
                "name": "target_orders_raw",
                "node_type": "target",
                "config": {
                    "database": "{bronze_schema}",
                    "table": "orders_raw",
                    "cluster_by_auto": True,
                    "table_properties": table_properties,
                    "sources": ["v_files"],
                },
            },
        ],
    }


def test__without_a_defaults_file_the_rule_checks_nothing(repo_root: Path):
    """Repositories generated before conf/table_defaults.yml existed are not suddenly invalid."""
    assert findings(repo_root, "table-defaults") == []


def test__a_target_needs_the_required_properties_and_clustering(repo_root: Path):
    """The silver example has neither row tracking nor clustering; adding both clears the errors."""
    write_yaml(repo_root / "conf/table_defaults.yml", DEFAULTS)
    messages = sorted(f.message for f in findings(repo_root, "table-defaults"))
    assert messages == [
        "orders: set cluster_by_auto: true (or cluster_by keys) for liquid clustering",
        "orders: table property delta.enableRowTracking must be 'true'",
    ]
    spec = copy.deepcopy(SPEC)
    spec["nodes"][1]["config"].update(cluster_by_auto=True, table_properties={"delta.enableRowTracking": "true"})
    write_json(repo_root / SILVER_SPEC, spec)
    assert findings(repo_root, "table-defaults") == []


def test__an_unread_change_feed_is_a_warning_and_an_exception_silences_it(repo_root: Path):
    """Silver streams bronze as appends, so CDF on bronze costs writes for nothing; a listed reason allows it."""
    write_yaml(repo_root / "conf/table_defaults.yml", {**DEFAULTS, "required_table_properties": {}, "clustering": ""})
    props = {"delta.enableChangeDataFeed": "true"}
    write_json(repo_root / BRONZE_SPEC, bronze_spec(props))
    found = findings(repo_root, "table-defaults")
    assert [(f.severity, f.message.split(":")[0]) for f in found] == [("warning", "example_claims_bronze.orders_raw")]
    write_yaml(
        repo_root / "conf/table_defaults.yml",
        {
            **DEFAULTS,
            "required_table_properties": {},
            "clustering": "",
            "exceptions": {"example_claims_bronze.orders_raw": "read by the fraud team's CDF stream"},
        },
    )
    assert findings(repo_root, "table-defaults") == []


@pytest.mark.parametrize(
    ("profile", "method"),
    [
        (IngestionProfile("t", "saas", "custom", SourceFacts(managed_connector=True)), "lakeflow_connect"),
        (
            IngestionProfile("t", "database", "custom", SourceFacts(managed_connector=True, needs_cdc=True)),
            "lakeflow_connect",
        ),
        (IngestionProfile("t", "database", "custom", SourceFacts(query_frequency="low")), "federation"),
        (IngestionProfile("t", "database", "custom", SourceFacts(query_frequency="low", needs_cdc=True)), "custom"),
        (IngestionProfile("t", "files", "autoloader", SourceFacts(arrival="continuous")), "autoloader"),
        (IngestionProfile("t", "files", "autoloader", SourceFacts(arrival="one_off")), "copy_into"),
        (IngestionProfile("t", "files", "autoloader", SourceFacts(files_per_day=2)), "copy_into"),
        (IngestionProfile("t", "sftp", "custom"), "custom"),
    ],
)
def test__the_recommender_prefers_managed_then_no_pipeline_then_declarative_then_custom(profile, method):
    """Each profile gets the cheapest method to run and maintain that can reach its source."""
    assert recommend(profile).method == method


def test__every_bronze_table_needs_a_profile_that_matches_its_spec_and_the_recommendation(repo_root: Path):
    """No profile, a profile the spec contradicts, and a costlier method without a reason are each errors."""
    write_json(repo_root / BRONZE_SPEC, bronze_spec({}))
    assert [f.message for f in findings(repo_root, "ingestion-method")] == [
        "orders_raw: no entry in bundles/dataops/src/config/ingestion.yml"
    ]
    write_yaml(repo_root / INGESTION, {"tables": {"orders_raw": {"source": "files", "method": "copy_into"}}})
    messages = [f.message for f in findings(repo_root, "ingestion-method")]
    assert (
        messages[0].startswith("orders_raw: the spec reads with Auto Loader but")
        and "'autoloader' fits" in messages[1]
    )
    write_yaml(repo_root / INGESTION, {"tables": {"orders_raw": {"source": "ftp", "method": "autoloader"}}})
    assert "source must be one of" in findings(repo_root, "ingestion-method")[0].message
    write_yaml(repo_root / INGESTION, {"tables": {"orders_raw": {"source": "files", "method": "autoloader"}}})
    assert findings(repo_root, "ingestion-method") == []


def test__make_recommend_prints_a_table_and_fails_when_a_change_is_due(repo_root: Path, capsys):
    """The command lists every profile with its status; an unjustified costlier method makes it exit 1."""
    write_json(repo_root / BRONZE_SPEC, bronze_spec({}))
    write_yaml(repo_root / INGESTION, {"tables": {"orders_raw": {"source": "files", "method": "custom"}}})
    assert metadata_main(["--root", str(repo_root), "recommend"]) == 1
    assert "| dataops | orders_raw | custom | autoloader | copy_into | CHANGE |" in capsys.readouterr().out
    write_yaml(
        repo_root / INGESTION,
        {"tables": {"orders_raw": {"source": "files", "method": "custom", "justification": "vendor zip format"}}},
    )
    assert metadata_main(["--root", str(repo_root), "recommend"]) == 0
    assert "| justified |" in capsys.readouterr().out
