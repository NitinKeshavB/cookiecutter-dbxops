"""
Recommend how each bronze table should be ingested, preferring the method that is cheapest to run and maintain.

Each bundle that writes a bronze layer declares, per table, what it ingests and how, in
``bundles/<bundle>/src/config/ingestion.yml``. ``recommend`` runs the decision tree below on that profile; the rule
``ingestion-method`` (``make validate-metadata``) fails a table whose declared ``method`` is not the recommended one
unless it carries a ``justification``, and ``make recommend`` prints the whole table for review.

Preference order (stop at the first that fits): a Lakeflow Connect managed connector; Lakehouse Federation (no
pipeline at all) for small, rarely read data; Auto Loader in a streaming table for files; ``COPY INTO`` for a one-off
or tiny SQL-only batch; custom landing code only when nothing else can reach the source.
"""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)
from pathlib import Path
from typing import Any

import yaml

from dbxops.findings import Finding
from dbxops.metadata.repo import RepoContext
from dbxops.metadata.specs import (
    SpecError,
    schema_name,
)

INGESTION_FILE = Path("src") / "config" / "ingestion.yml"
METHODS = ("lakeflow_connect", "federation", "autoloader", "copy_into", "custom")
SOURCES = ("files", "database", "saas", "sftp", "api")
TINY_BATCH_FILES = 10  # fewer files a day than this, in batches, is cheaper with COPY INTO than a stream


@dataclass(frozen=True)
class SourceFacts:
    """What the decision tree needs to know about a source beyond its kind."""

    managed_connector: bool = False
    needs_cdc: bool = False
    arrival: str = "batch"
    files_per_day: int = 0
    query_frequency: str = "high"


@dataclass(frozen=True)
class IngestionProfile:
    """What one bronze table ingests, the method chosen for it, and why if that is not the recommended one."""

    table: str
    source: str
    method: str
    facts: SourceFacts = field(default_factory=SourceFacts)
    justification: str = ""


@dataclass(frozen=True)
class Recommendation:
    """The cheapest method that fits, the next best, and why."""

    method: str
    runner_up: str
    reason: str


def _system_source(profile: IngestionProfile) -> Recommendation | None:
    facts = profile.facts
    if profile.source not in ("database", "saas"):
        return None
    if facts.managed_connector:
        return Recommendation(
            "lakeflow_connect", "custom", "a managed connector exists: Databricks owns CDC, schema drift and retries"
        )
    if facts.query_frequency == "low" and not facts.needs_cdc:
        return Recommendation("federation", "custom", "small, rarely read and no history needed: query it in place")
    return None


def _file_source(profile: IngestionProfile) -> Recommendation | None:
    facts = profile.facts
    if profile.source != "files":
        return None
    if facts.arrival == "one_off" or (facts.arrival == "batch" and 0 < facts.files_per_day < TINY_BATCH_FILES):
        return Recommendation("copy_into", "autoloader", "a one-off or tiny batch: idempotent COPY INTO is enough")
    return Recommendation(
        "autoloader", "copy_into", "files keep arriving: Auto Loader tracks them incrementally and evolves schema"
    )


def recommend(profile: IngestionProfile) -> Recommendation:
    """Return the recommended ingestion method for ``profile``."""
    found = _system_source(profile) or _file_source(profile)
    if found is not None:
        return found
    return Recommendation(
        "custom",
        "federation" if profile.source == "database" else "lakeflow_connect",
        "no managed or declarative option reaches this source: land files with a job, then Auto Loader takes over",
    )


def _profile(table: str, raw: dict[str, Any]) -> IngestionProfile:
    return IngestionProfile(
        table=table,
        source=str(raw.get("source", "")),
        method=str(raw.get("method", "")),
        facts=SourceFacts(
            managed_connector=bool(raw.get("managed_connector", False)),
            needs_cdc=bool(raw.get("needs_cdc", False)),
            arrival=str(raw.get("arrival", "batch")),
            files_per_day=int(raw.get("files_per_day", 0) or 0),
            query_frequency=str(raw.get("query_frequency", "high")),
        ),
        justification=str(raw.get("justification", "") or ""),
    )


def load_profiles(bundle_dir: Path) -> dict[str, IngestionProfile]:
    """Return the bundle's ingestion profiles by table, or an empty mapping when it declares none."""
    path = bundle_dir / INGESTION_FILE
    if not path.exists():
        return {}
    tables = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("tables") or {}
    return {str(table): _profile(str(table), raw or {}) for table, raw in tables.items()}


def _bronze_targets(ctx: RepoContext) -> dict[Path, dict[str, tuple[str, bool]]]:
    """Map each bundle to its bronze tables: the spec path and whether the spec reads with Auto Loader."""
    found: dict[Path, dict[str, tuple[str, bool]]] = {}
    for info in ctx.specs:
        nodes = {node.get("name", ""): node for node in info.spec.raw.get("nodes", [])}
        for target in info.spec.targets:
            try:
                schema = schema_name(target.database, info.tokens)
            except SpecError:
                continue
            if schema == f"{ctx.project_id}_bronze":
                autoloader = any(nodes.get(s, {}).get("source_type") == "cloud_files" for s in target.sources)
                found.setdefault(info.bundle_dir, {})[target.table] = (str(info.spec.path), autoloader)
    return found


def check_ingestion(ctx: RepoContext) -> list[Finding]:
    """Return every bronze table without a profile, with a profile the spec contradicts, or with a costlier method."""
    findings: list[Finding] = []
    for bundle_dir, tables in _bronze_targets(ctx).items():
        profiles = load_profiles(bundle_dir)
        where = str((bundle_dir / INGESTION_FILE).relative_to(ctx.root))
        for table, (spec_path, autoloader) in sorted(tables.items()):
            profile = profiles.get(table)
            if profile is None:
                findings.append(Finding("ingestion-method", spec_path, f"{table}: no entry in {where}"))
                continue
            if autoloader and profile.method != "autoloader":
                message = f"{table}: the spec reads with Auto Loader but {where} declares {profile.method!r}"
                findings.append(Finding("ingestion-method", spec_path, message))
            findings += profile_findings(profile, where)
    return findings


def profile_findings(profile: IngestionProfile, where: str) -> list[Finding]:
    """Return what is wrong with one profile: unknown values, or a costlier method without a justification."""
    if profile.source not in SOURCES:
        return [Finding("ingestion-method", where, f"{profile.table}: source must be one of {', '.join(SOURCES)}")]
    if profile.method not in METHODS:
        return [Finding("ingestion-method", where, f"{profile.table}: method must be one of {', '.join(METHODS)}")]
    best = recommend(profile)
    if profile.method == best.method or profile.justification:
        return []
    message = (
        f"{profile.table}: method {profile.method!r} but {best.method!r} fits ({best.reason}); "
        "change it, or add a justification"
    )
    return [Finding("ingestion-method", where, message)]
