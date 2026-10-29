"""
Command line: ``python -m dbxops.metastore publish|ensure-schema|publish-registry|decide|dictionary``.

A thin wrapper: parse arguments, build the repository with ``RepositoryFactory``, discover documents with
``sources``, map them with the collectors and hand them to ``MetadataPublisher``.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path
from typing import (
    Callable,
    Sequence,
)

import yaml

from dbxops.metadata.ingestion import (
    INGESTION_FILE,
    load_profiles,
    recommend,
)
from dbxops.metastore import collectors
from dbxops.metastore.dictionary import render_dictionary
from dbxops.metastore.errors import MetastoreError
from dbxops.metastore.factory import RepositoryFactory
from dbxops.metastore.publisher import MetadataPublisher
from dbxops.metastore.records import (
    ORIGIN_SPEC,
    PLATFORM_REPO_ID,
    DefinitionRecord,
    DesignDecisionRecord,
    OperationsBatch,
    PublishContext,
    PublishCounts,
    TuningDecision,
    TuningKey,
)
from dbxops.metastore.repository import MetadataRepository
from dbxops.metastore.runtime_collectors import (
    BundleRunner,
    DeploymentCollector,
    LineageCollector,
)
from dbxops.metastore.sources import (
    Discovered,
    SubprocessBundleRunner,
    ci_context_from_env,
    discover,
    read_repo_id,
)
from dbxops.registry.snapshots import merge_glossary

WHAT_CHOICES = ("definitions", "deployments", "lineage", "decisions")
DEFAULT_CONFIG = "conf/metastore.yml"

RepositoryBuilder = Callable[[str, str], MetadataRepository]


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m dbxops.metastore", description="Publish to the metadata database."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("publish", "ensure-schema", "publish-registry"):
        command = commands.add_parser(name)
        command.add_argument("--target", required=True)
        command.add_argument("--config", default=DEFAULT_CONFIG)
        command.add_argument("--root", default=".", help="repo root holding metadata/ and the bundles")
    commands.add_parser("dictionary", help="print the data dictionary (markdown) generated from the model")
    decide = commands.add_parser("decide", help="record a person's answer to a tuning recommendation")
    decide.add_argument("--target", required=True)
    decide.add_argument("--config", default=DEFAULT_CONFIG)
    decide.add_argument("--root", default=".", help="repo root holding metadata/")
    decide.add_argument("--asset", required=True, help="asset_id of the recommendation, e.g. uc_pipeline/<id>")
    decide.add_argument("--rule", required=True, help="rule_code, e.g. slower_than_baseline")
    decide.add_argument("--evidence", required=True, help="evidence_key (the update id it fired on)")
    decide.add_argument("--decision", required=True, choices=("accepted", "rejected"))
    decide.add_argument("--reason", default="")
    decide.add_argument("--by", default=os.environ.get("GITHUB_ACTOR") or os.environ.get("USER", "unknown"))
    registry = commands.choices["publish-registry"]
    registry.add_argument("--registry", required=True, help="registry checkout: projects/, domains.yaml, glossary/")
    registry.add_argument("--git-sha", default=os.environ.get("GITHUB_SHA"))
    registry.add_argument("--published-by", default=os.environ.get("GITHUB_ACTOR", "dbxops"))
    publish = commands.choices["publish"]
    publish.add_argument("--what", default=",".join(WHAT_CHOICES), help="comma-separated: " + ", ".join(WHAT_CHOICES))
    publish.add_argument("--repo-id", help="default: project_id of metadata/_platform/repo.yaml")
    publish.add_argument("--git-sha", default=os.environ.get("GITHUB_SHA"))
    publish.add_argument("--published-by", default=os.environ.get("GITHUB_ACTOR", "dbxops"))
    publish.add_argument("--bundle", default="", help="bundle name (default: from `bundle summary`)")
    publish.add_argument("--bundle-dir", default=".", help="directory to run `databricks bundle summary` in")
    publish.add_argument("--retire-missing", action="store_true", help="retire contracts/products no longer in git")
    return parser


def _spec_records(found: Discovered, context: PublishContext) -> list[DefinitionRecord]:
    records: list[DefinitionRecord] = []
    for info in found.specs:
        records += collectors.dataflow_spec_records(
            info,
            repo_id=context.repo_id,
            source_path=info.spec.path.relative_to(info.bundle_dir.parent.parent).as_posix(),
            git_commit=context.git_commit,
        )
    return records


def _publish_definitions(publisher: MetadataPublisher, found: Discovered, args: argparse.Namespace) -> PublishCounts:
    options = {"repo_id": publisher.repo_id, "git_commit": args.git_sha}
    records = _spec_records(found, publisher.context)
    links: list = []
    tags: list = []
    for path, doc in found.contracts:
        records += collectors.contract_records(doc, source_path=path, **options)
        records += collectors.asset_records(doc, source_path=path, **options)
    for path, doc in found.products:
        records += collectors.product_records(doc, source_path=path, **options)
        records += collectors.asset_records(doc, source_path=path, **options)
    for _, doc in [*found.contracts, *found.products]:
        links += collectors.semantic_link_rows(doc)
        tags += collectors.tag_rows(doc)
    retire = ("data_contract", "data_product") if args.retire_missing else ()
    return publisher.publish_definitions(records, links=links, tags=tags, retire_missing=retire)


def _publish_deployments(publisher: MetadataPublisher, args: argparse.Namespace, runner: BundleRunner | None):
    runner = runner or SubprocessBundleRunner(Path(args.bundle_dir))
    ci = ci_context_from_env(os.environ, uuid.uuid4().hex)
    record = DeploymentCollector(runner, publisher.repo_id).collect(args.target, ci, bundle=args.bundle)
    return publisher.publish_deployment(record)


def design_decisions(found_root: Path, repo_id: str, git_sha: str, approver: str) -> list[DesignDecisionRecord]:
    """Return one decision per bronze table declared in a bundle's ingestion.yml, with the recommender's choice."""
    records = []
    for bundle_dir in sorted(path.parent.parent.parent for path in found_root.glob(f"bundles/*/{INGESTION_FILE}")):
        for table, profile in sorted(load_profiles(bundle_dir).items()):
            records.append(
                DesignDecisionRecord(
                    asset_id=f"{repo_id}_bronze.{table}",
                    git_sha=git_sha,
                    recommended=recommend(profile).method,
                    chosen=profile.method,
                    justification=profile.justification,
                    approver=approver,
                )
            )
    return records


def run_publish(args: argparse.Namespace, build: RepositoryBuilder, runner: BundleRunner | None = None) -> int:
    """Run the ``publish`` command."""
    what = [item.strip() for item in args.what.split(",") if item.strip()]
    unknown = sorted(set(what) - set(WHAT_CHOICES))
    if unknown:
        print(f"unknown --what value(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    root = Path(args.root)
    context = PublishContext(args.repo_id or read_repo_id(root), args.published_by, args.git_sha, args.target)
    publisher = MetadataPublisher(build(args.config, args.target), context)
    found = discover(root)
    if "definitions" in what:
        print(f"definitions: {_publish_definitions(publisher, found, args)}")
    if "deployments" in what:
        print(f"deployments: {_publish_deployments(publisher, args, runner)}")
    if "lineage" in what:
        edges = LineageCollector.spec_edges(_spec_records(found, context))
        print(f"lineage: {publisher.publish_lineage(edges, ORIGIN_SPEC)}")
    if "decisions" in what:
        decisions = design_decisions(root, context.repo_id, args.git_sha or "", args.published_by)
        print(f"decisions: {publisher.publish_operations(OperationsBatch(design_decisions=decisions))}")
    return 0


def run_decide(args: argparse.Namespace, build: RepositoryBuilder) -> int:
    """Record one accepted or rejected tuning recommendation for this repo."""
    context = PublishContext(read_repo_id(Path(args.root)), args.by, None, args.target)
    key = TuningKey(args.target, args.asset, args.rule, args.evidence)
    decision = TuningDecision(key, args.decision, args.by, args.reason)
    counts = MetadataPublisher(build(args.config, args.target), context).publish_operations(
        OperationsBatch(tuning_decisions=[decision])
    )
    print(f"decision: {counts}")
    return 0


def run_publish_registry(args: argparse.Namespace, build: RepositoryBuilder) -> int:
    """Publish the registry's platform-owned records (projects, squads, domains, glossary) as ``_platform``."""
    root = Path(args.registry)
    context = PublishContext(PLATFORM_REPO_ID, args.published_by, args.git_sha, args.target)
    required = (yaml.safe_load((root / "required_tags.yaml").read_text(encoding="utf-8")) or {}).get("required", [])
    publisher = MetadataPublisher(build(args.config, args.target), context, required_tag_keys=required)
    options = {"repo_id": PLATFORM_REPO_ID, "git_commit": args.git_sha}
    records: list[DefinitionRecord] = []
    tags: list = []
    for path in sorted((root / "projects").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        records += collectors.registration_records(doc, source_path=f"projects/{path.name}", **options)
        tags += collectors.project_tag_rows(doc)
    domains = yaml.safe_load((root / "domains.yaml").read_text(encoding="utf-8"))
    records += collectors.domain_records(domains, source_path="domains.yaml", **options)
    records += collectors.glossary_records(merge_glossary(root / "glossary"), source_path="glossary", **options)
    print(f"registry: {publisher.publish_definitions(records, tags=tags)}")
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    build: RepositoryBuilder | None = None,
    runner: BundleRunner | None = None,
) -> int:
    """Run the command line and return the process exit code."""
    args = build_parser().parse_args(argv)
    if args.command == "dictionary":
        print(render_dictionary(), end="")
        return 0
    builder = build or RepositoryFactory.from_config
    try:
        if args.command == "ensure-schema":
            builder(args.config, args.target).ensure_schema()
            print("schema ready")
            return 0
        if args.command == "publish-registry":
            return run_publish_registry(args, builder)
        if args.command == "decide":
            return run_decide(args, builder)
        return run_publish(args, builder, runner)
    except MetastoreError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
