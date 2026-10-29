"""
Scaffold a new pipeline bundle that follows the repo's conventions and passes validation from the first commit.

`scaffold_bundle` writes `bundles/<name>/` from the repo's own configuration: its targets, catalogs, host and
framework path come from `conf/environments.yml`; permissions from the squad's AD groups, and the tags from the
registration (`metadata/_platform`). It adds a runnable example (one silver table, its schema, a pipeline, a job
that ends by recording its run, and a product), then generates the substitution files and the table's contract.
The contract and product carry `PLACEHOLDER-<what>` text for what only a person can write, so a deploy is blocked
until they do.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from dbxops.metadata.contracts import (
    contract_id,
    write_contracts,
)
from dbxops.metadata.repo import RepoContext
from dbxops.metadata.substitutions import write_substitutions
from dbxops.registry.domains import DomainError

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,30}$")
EXAMPLE_TABLE = "example"
ROLE_LEVELS = (("admins", "CAN_MANAGE"), ("developers", "CAN_RUN"), ("readers", "CAN_VIEW"))
# tags every job and pipeline carries so cost is attributed to the project, repository and commit that deployed it
PROVENANCE_TAGS = {
    "project_id": "${var.project_id}",
    "bundle": "${bundle.name}",
    "target": "${bundle.target}",
    "git_repo": "${bundle.git.origin_url}",
    "git_ref": "${bundle.git.branch}",
    "git_sha": "${bundle.git.commit}",
}
BUDGET_POLICY_PLACEHOLDER = "<PLACEHOLDER-budget-policy-id>"
# every job tells the squad about failures by email and in Microsoft Teams (a workspace notification destination)
NOTIFICATIONS = {
    "email_notifications": {"on_failure": ["${var.notification_email}"]},
    "webhook_notifications": {
        "on_failure": [{"id": "${var.notification_destination_id}"}],
        "on_duration_warning_threshold_exceeded": [{"id": "${var.notification_destination_id}"}],
    },
}
NOTIFICATION_PLACEHOLDERS = {
    "notification_destination_id": "<PLACEHOLDER-teams-notification-destination-id>",
    "notification_email": "<PLACEHOLDER-notification-email>",
}


class ScaffoldError(ValueError):
    """Raised when a bundle cannot be scaffolded (bad name, domain, or the bundle already exists)."""


def _variables(tag_keys: list[str]) -> dict[str, Any]:
    variables: dict[str, Any] = {
        "project_id": {
            "description": "Project id; every schema is <project_id>_<layer>. Must equal project_id in metadata/_platform/repo.yaml"
        },
        "catalog": {"description": "Unity Catalog catalog of the target (conf/environments.yml)"},
        "logical_env": {
            "description": "Suffix that isolates a developer's schemas in dev; empty elsewhere (starts with '_')",
            "default": "",
        },
        "workspace_host": {"description": "Workspace URL, passed to the framework for API calls"},
        "framework_source_path": {
            "description": "Deployed framework src folder: /Workspace/Shared/.bundle/lakeflow_framework/<target>/<version>/files/src"
        },
        "service_principal_application_id": {
            "description": "Application id the non-dev targets run as (metadata/_platform/repo.yaml)"
        },
    }
    for key in tag_keys:
        variables[key] = {"description": f"{key} tag (metadata/_platform/repo.yaml tags)"}
    variables["budget_policy_id"] = {
        "description": "Serverless budget policy of this project; its tags attribute serverless cost (set by the platform team)"
    }
    variables["notification_destination_id"] = {
        "description": "Workspace notification destination (Microsoft Teams) for failures and duration warnings"
    }
    variables["notification_email"] = {"description": "Email address (or distribution list) told about failed runs"}
    variables["dbxops_wheel_path"] = {
        "description": "Path of the dbxops wheel on a UC volume, set by CI (/Volumes/<catalog>/platform/wheels/<version>/...whl)",
        "default": "",
    }
    variables["performance_target"] = {"description": "Serverless job performance target", "default": "STANDARD"}
    return variables


def _targets(ctx: RepoContext, tags: dict[str, str]) -> dict[str, Any]:
    env = ctx.environments
    host, version = env["workspace"]["host"], env["framework_version"]
    targets: dict[str, Any] = {}
    for name, cfg in env["targets"].items():
        target: dict[str, Any] = {
            "mode": cfg["mode"],
            "workspace": {"host": host},
            "variables": {
                "catalog": cfg["catalog"],
                "workspace_host": host,
                "framework_source_path": f"/Workspace/Shared/.bundle/lakeflow_framework/{name}/{version}/files/src",
                **tags,
                "budget_policy_id": BUDGET_POLICY_PLACEHOLDER,
                **NOTIFICATION_PLACEHOLDERS,
            },
        }
        if name == "dev":
            target["default"] = True
            target["variables"]["logical_env"] = "_${workspace.current_user.short_name}"
        else:
            target["workspace"]["root_path"] = "/Workspace/Shared/.bundle/${bundle.name}/${bundle.target}"
        if cfg.get("run_as_sp"):
            target["variables"]["service_principal_application_id"] = ctx.repo["service_principal"]["application_id"]
            target["run_as"] = {"service_principal_name": "${var.service_principal_application_id}"}
        if name in ("preprod", "prod"):
            target["variables"]["performance_target"] = "PERFORMANCE_OPTIMIZED"
        targets[name] = target
    return targets


def _bundle_config(ctx: RepoContext, name: str) -> dict[str, Any]:
    groups = ctx.teams["teams"][0]["groups"]
    tags = {key: str(value) for key, value in ctx.repo["tags"].items()}
    variables = _variables(list(tags))
    variables["project_id"]["default"] = ctx.project_id
    return {
        "bundle": {"name": name},
        "include": ["resources/*.yml"],
        "permissions": [{"level": level, "group_name": groups[role]} for role, level in ROLE_LEVELS],
        "variables": variables,
        "targets": _targets(ctx, tags),
    }


def _tag_refs(ctx: RepoContext) -> dict[str, str]:
    return {**{key: f"${{var.{key}}}" for key in ctx.repo["tags"]}, **PROVENANCE_TAGS}


def _pipeline(ctx: RepoContext, name: str) -> dict[str, Any]:
    return {
        "resources": {
            "pipelines": {
                f"{name}_silver": {
                    "name": f"[${{var.project_id}}] {name} silver (${{bundle.target}}${{var.logical_env}})",
                    "channel": "CURRENT",
                    "serverless": True,
                    "catalog": "${var.catalog}",
                    "schema": "${var.project_id}_silver${var.logical_env}",
                    "libraries": [{"notebook": {"path": "${var.framework_source_path}/dlt_pipeline"}}],
                    "root_path": "${workspace.file_path}/src",
                    "budget_policy_id": "${var.budget_policy_id}",
                    "tags": _tag_refs(ctx),
                    "configuration": {
                        "bundle.sourcePath": "${workspace.file_path}/src",
                        "bundle.target": "${bundle.target}",
                        "framework.sourcePath": "${var.framework_source_path}",
                        "workspace.host": "${var.workspace_host}",
                        "pipeline.layer": "silver",
                        "logicalEnv": "${var.logical_env}",
                        "pipeline.dataFlowGroupFilter": "silver",
                    },
                }
            }
        }
    }


def _job(ctx: RepoContext, name: str) -> dict[str, Any]:
    return {
        "resources": {
            "jobs": {
                f"{name}_run": {
                    "name": f"[${{var.project_id}}] {name} run (${{bundle.target}}${{var.logical_env}})",
                    "budget_policy_id": "${var.budget_policy_id}",
                    "tags": _tag_refs(ctx),
                    **NOTIFICATIONS,
                    "performance_target": "${var.performance_target}",
                    "queue": {"enabled": True},
                    "environments": [
                        {
                            "environment_key": "platform",
                            "spec": {
                                "client": "4",
                                "dependencies": ["databricks-sql-connector", "${var.dbxops_wheel_path}"],
                            },
                        }
                    ],
                    "tasks": [
                        {
                            "task_key": "silver",
                            "pipeline_task": {"pipeline_id": f"${{resources.pipelines.{name}_silver.id}}"},
                        },
                        {
                            "task_key": "publish_run_metadata",
                            "depends_on": [{"task_key": "silver"}],
                            "run_if": "ALL_DONE",
                            "environment_key": "platform",
                            "python_wheel_task": {
                                "package_name": "cookie-cutter-dbx",
                                "entry_point": "dbxops-publish-run",
                                "parameters": [
                                    "--run-id",
                                    "{{job.run_id}}",
                                    "--target",
                                    "${bundle.target}",
                                    "--repo-id",
                                    "${var.project_id}",
                                    "--config",
                                    "${workspace.file_path}/src/config/metastore.yml",
                                ],
                            },
                        },
                    ],
                }
            }
        }
    }


def _spec(name: str, data_domain: str, product_id: str) -> dict[str, Any]:
    return {
        "data_flow_id": f"{name}_{EXAMPLE_TABLE}",
        "data_flow_group": "silver",
        "data_flow_type": "nodespec",
        "tags": {"data_domain": data_domain, "data_product": product_id},
        "nodes": [
            {
                "name": f"v_{EXAMPLE_TABLE}_source",
                "node_type": "source",
                "source_type": "delta",
                "config": {
                    "mode": "stream",
                    "database": "{bronze_schema}",
                    "table": f"{EXAMPLE_TABLE}_source",
                    "select_exp": ["id", "name", "updated_at"],
                },
            },
            {
                "name": f"target_{EXAMPLE_TABLE}",
                "node_type": "target",
                "config": {
                    "database": "{silver_schema}",
                    "table": EXAMPLE_TABLE,
                    "schema_path": f"{EXAMPLE_TABLE}_schema.json",
                    "cluster_by_auto": True,
                    "table_properties": {"delta.enableChangeDataFeed": "true", "delta.enableRowTracking": "true"},
                    "sources": [f"v_{EXAMPLE_TABLE}_source"],
                },
            },
        ],
    }


EXAMPLE_SCHEMA: dict[str, Any] = {
    "type": "struct",
    "fields": [
        {"name": "id", "type": "string", "nullable": False, "metadata": {}},
        {"name": "name", "type": "string", "nullable": True, "metadata": {}},
        {"name": "updated_at", "type": "timestamp", "nullable": True, "metadata": {}},
    ],
}


def _product(ctx: RepoContext, name: str) -> dict[str, Any]:
    product_id = f"dp_{ctx.project_id}_{name}"
    return {
        "apiVersion": "v1.0.0",
        "kind": "DataProduct",
        "id": product_id,
        "name": product_id.removeprefix("dp_"),
        "version": "1.0.0",
        "status": "draft",
        "domain": ctx.repo["domain"],
        "description": {
            "purpose": "PLACEHOLDER-purpose",
            "usage": "PLACEHOLDER-usage",
            "limitations": "PLACEHOLDER-limitations",
        },
        "outputPorts": [
            {
                "name": EXAMPLE_TABLE,
                "version": "1.0.0",
                "type": "uc_streaming_table",
                "contractId": contract_id(ctx.project_id, "silver", EXAMPLE_TABLE),
                "customProperties": [{"property": "dbx.assetId", "value": f"{ctx.project_id}_silver.{EXAMPLE_TABLE}"}],
            }
        ],
    }


def _dump(document: dict[str, Any]) -> str:
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=140)


def scaffold_bundle(root: Path, name: str, data_domain: str) -> list[Path]:
    """
    Create `bundles/<name>/` and return the files written.

    `data_domain` must be a data-domain path of the taxonomy (it is what the example table's contract rolls up
    to). Raises `ScaffoldError` for an invalid name or domain, or when the bundle already exists.
    """
    ctx = RepoContext.load(root)
    if not NAME_PATTERN.match(name):
        raise ScaffoldError(f"bundle name {name!r} must match {NAME_PATTERN.pattern}")
    bundle = root / "bundles" / name
    if bundle.exists():
        raise ScaffoldError(f"{bundle} already exists")
    try:
        ctx.taxonomy.resolve(data_domain, level="data_domain")
    except DomainError as error:
        raise ScaffoldError(str(error)) from error
    product = _product(ctx, name)
    layer = bundle / "src/dataflows/silver"
    files = {
        bundle / "databricks.yml": _dump(_bundle_config(ctx, name)),
        bundle / f"resources/{name}.pipelines.yml": _dump(_pipeline(ctx, name)),
        bundle / f"resources/{name}.jobs.yml": _dump(_job(ctx, name)),
        layer
        / f"dataflowspec/{EXAMPLE_TABLE}_main.json": json.dumps(_spec(name, data_domain, product["id"]), indent=2)
        + "\n",
        layer / f"schemas/{EXAMPLE_TABLE}_schema.json": json.dumps(EXAMPLE_SCHEMA, indent=2) + "\n",
        bundle / f"src/products/{name}.odps.yaml": _dump(product),
    }
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    generated = [plan.path for plan in write_substitutions(RepoContext.load(root))]
    generated += [plan.path for plan in write_contracts(RepoContext.load(root))]
    return [*files, *generated]
