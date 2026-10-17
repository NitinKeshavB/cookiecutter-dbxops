"""
Check dataflow spec targets against the repository's table defaults (``conf/table_defaults.yml``).

Required table properties and clustering are errors; change data feed on a table that every reader in this repo
streams as an append source is a warning (it costs extra writes for a feed nobody reads here; a reader outside the
repo, such as a Vector Search delta-sync index, is a valid reason to list it under ``exceptions``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from dbxops.findings import Finding
from dbxops.metadata.repo import (
    RepoContext,
    SpecInfo,
)
from dbxops.metadata.specs import (
    SpecError,
    SpecTarget,
    schema_name,
)

DEFAULTS_FILE = Path("conf") / "table_defaults.yml"
CDF = "delta.enableChangeDataFeed"


def load_defaults(root: Path) -> dict[str, Any]:
    """Return the parsed defaults, or an empty mapping when the repository has none."""
    path = root / DEFAULTS_FILE
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _nodes(info: SpecInfo) -> dict[str, dict[str, Any]]:
    return {node.get("name", ""): node for node in info.spec.raw.get("nodes", [])}


def _asset(info: SpecInfo, database: str, table: str) -> str | None:
    try:
        return f"{schema_name(database, info.tokens)}.{table}"
    except SpecError:
        return None


def _change_feed_readers(ctx: RepoContext) -> dict[str, bool]:
    """Map each asset read by a spec in this repo to whether any reader asks for its change feed."""
    readers: dict[str, bool] = {}
    for info in ctx.specs:
        for node in _nodes(info).values():
            config = node.get("config") or {}
            if node.get("node_type") != "source" or "table" not in config:
                continue
            asset = _asset(info, config.get("database", ""), config["table"])
            if asset:
                options = config.get("reader_options") or {}
                wants = str(options.get("readChangeFeed", "")).lower() == "true"
                readers[asset] = readers.get(asset, False) or wants
    return readers


def _target_findings(
    target: SpecTarget, config: dict[str, Any], defaults: dict[str, Any], where: str
) -> list[Finding]:
    findings = []
    properties = config.get("table_properties") or {}
    for key, value in (defaults.get("required_table_properties") or {}).items():
        if str(properties.get(key, "")).lower() != str(value).lower():
            findings.append(
                Finding("table-defaults", where, f"{target.table}: table property {key} must be {value!r}")
            )
    is_mv = config.get("table_type") == "mv"
    clustered = config.get("cluster_by_auto") is True or bool(config.get("cluster_by"))
    if defaults.get("clustering") == "required" and not is_mv and not clustered:
        message = f"{target.table}: set cluster_by_auto: true (or cluster_by keys) for liquid clustering"
        findings.append(Finding("table-defaults", where, message))
    return findings


def _cdf_finding(asset: str, readers: dict[str, bool], where: str) -> Finding | None:
    if asset not in readers or readers[asset]:
        return None
    message = (
        f"{asset}: change data feed is on but every reader here streams it as an append source; turn it off unless "
        "a consumer outside this repo reads the feed (then list it under exceptions in conf/table_defaults.yml)"
    )
    return Finding("table-defaults", where, message, "warning")


def check_table_defaults(ctx: RepoContext) -> list[Finding]:
    """Return every target that misses a required property or clustering, and every unread change data feed."""
    defaults = load_defaults(ctx.root)
    if not defaults:
        return []
    readers = _change_feed_readers(ctx) if defaults.get("change_data_feed") == "only_when_read" else {}
    findings: list[Finding] = []
    for info in ctx.specs:
        findings += _spec_findings(info, defaults, readers)
    return findings


def _spec_findings(info: SpecInfo, defaults: dict[str, Any], readers: dict[str, bool]) -> list[Finding]:
    exceptions = defaults.get("exceptions") or {}
    nodes, where = _nodes(info), str(info.spec.path)
    findings: list[Finding] = []
    for target in info.spec.targets:
        asset = _asset(info, target.database, target.table)
        if asset is None or asset in exceptions:
            continue
        config = nodes.get(target.node, {}).get("config") or {}
        findings += _target_findings(target, config, defaults, where)
        if str((config.get("table_properties") or {}).get(CDF, "")).lower() == "true":
            cdf = _cdf_finding(asset, readers, where)
            findings += [cdf] if cdf else []
    return findings
