"""
The metadata validation rule chain.

Every rule is a function `(RepoContext, Options) -> list[Finding]`; `validate` runs all of them and
returns every finding, so one run reports every problem. Rules never raise for bad input; they report.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft201909Validator

from dbxops.findings import Finding
from dbxops.hashing import content_hash
from dbxops.metadata.contracts import (
    ASSET_ID,
    PRODUCER,
    get_custom,
    plan_contracts,
)
from dbxops.metadata.defaults import check_table_defaults
from dbxops.metadata.ingestion import check_ingestion
from dbxops.metadata.repo import (
    LAYERS,
    Doc,
    RepoContext,
)
from dbxops.metadata.specs import (
    SpecError,
    schema_name,
)
from dbxops.metadata.substitutions import plan_substitutions
from dbxops.registry.domains import (
    DomainError,
    DomainTaxonomy,
)
from dbxops.registry.snapshots import verify_directory

ODCS_VERSION = "v3.1.0"
ODPS_VERSION = "v1.0.0"
SEMANTIC_ASSIGNMENT = "http://databricks.com/ontology/uc/semanticAssignment"
MAX_ID_LENGTH = 128
_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


Baseline = Callable[[str], dict[str, Any] | None]


@dataclass(frozen=True)
class Options:
    """Rule options; `baseline` returns a document as it was on the base ref, for the semver rule."""

    baseline: Baseline | None = None


Rule = Callable[[RepoContext, Options], list[Finding]]


class GitBaseline:
    """Read a document as it was at a git ref (e.g. `origin/main`), or None if it did not exist there."""

    def __init__(self, root: Path, ref: str):
        self.root = root
        self.ref = ref

    def __call__(self, path: str) -> dict[str, Any] | None:
        """Return the parsed YAML of `path` at the ref, or None if the file is absent there."""
        result = subprocess.run(
            ["git", "-C", str(self.root), "show", f"{self.ref}:{path}"], capture_output=True, text=True, check=False
        )
        return yaml.safe_load(result.stdout) if result.returncode == 0 else None


def _schema(name: str) -> dict[str, Any]:
    return json.loads((resources.files("dbxops.metadata") / "schemas" / name).read_text(encoding="utf-8"))


def _format_error(error: Any) -> str:
    where = "/".join(str(p) for p in error.absolute_path) or "<root>"
    return f"{where}: {error.message[:200]}"


def rule_json_schema(ctx: RepoContext, _: Options) -> list[Finding]:
    """Check that contracts satisfy the ODCS schema and products the ODPS schema."""
    findings = []
    for docs, schema_file in (
        (ctx.contracts, "odcs-json-schema-v3.1.0.json"),
        (ctx.products, "odps-json-schema-v1.0.0.json"),
    ):
        validator = Draft201909Validator(_schema(schema_file))
        for doc in docs:
            for error in sorted(validator.iter_errors(doc.data), key=lambda e: list(e.absolute_path)):
                findings.append(Finding("json-schema", str(doc.path), _format_error(error)))
    return findings


def rule_api_version(ctx: RepoContext, _: Options) -> list[Finding]:
    """Check that only the pinned standard versions are used: ODCS v3.1.0 and ODPS v1.0.0."""
    findings = []
    for docs, pinned in ((ctx.contracts, ODCS_VERSION), (ctx.products, ODPS_VERSION)):
        for doc in docs:
            if doc.data.get("apiVersion") != pinned:
                findings.append(Finding("api-version", str(doc.path), f"apiVersion must be {pinned}"))
    return findings


def rule_ids(ctx: RepoContext, _: Options) -> list[Finding]:
    """Check that ids are lowercase slugs with the project prefix, unique, and that a product's name is its id without `dp_`."""
    findings = []
    counts = Counter(doc.doc_id for doc in ctx.contracts + ctx.products)
    for docs, prefix in ((ctx.contracts, "dc_"), (ctx.products, "dp_")):
        wanted = f"{prefix}{ctx.project_id}_"
        for doc in docs:
            doc_id = doc.doc_id
            if not _ID.match(doc_id) or len(doc_id) > MAX_ID_LENGTH:
                findings.append(
                    Finding("id", str(doc.path), f"id {doc_id!r} must match {_ID.pattern}, max {MAX_ID_LENGTH}")
                )
            elif not doc_id.startswith(wanted):
                findings.append(Finding("id", str(doc.path), f"id {doc_id!r} must start with {wanted!r}"))
            if counts[doc_id] > 1:
                findings.append(Finding("id", str(doc.path), f"duplicate id {doc_id!r}"))
    for doc in ctx.products:
        if doc.data.get("name") != doc.doc_id.removeprefix("dp_"):
            findings.append(Finding("id", str(doc.path), "product name must equal its id without the 'dp_' prefix"))
    return findings


def _resolve_data_domain(taxonomy: DomainTaxonomy, value: Any, path: str) -> tuple[Any, list[Finding]]:
    try:
        return taxonomy.resolve(value, level="data_domain"), []
    except DomainError as error:
        return None, [Finding("domain", path, str(error))]


def rule_domains(ctx: RepoContext, _: Options) -> list[Finding]:
    """Check that a contract's domain is a known data-domain path (inside the repo's domain for a domain repo)."""
    findings = []
    repo_domain = ctx.repo.get("domain")
    for doc in ctx.contracts:
        node, errors = _resolve_data_domain(ctx.taxonomy, doc.data.get("domain"), str(doc.path))
        findings += errors
        if node and ctx.kind == "domain" and not ctx.taxonomy.is_within(node.path, repo_domain):
            findings.append(
                Finding("domain", str(doc.path), f"{node.path!r} is outside this domain repo's {repo_domain!r}")
            )
    for doc in ctx.products:
        value = doc.data.get("domain")
        if ctx.kind == "domain" and value != repo_domain:
            findings.append(
                Finding(
                    "domain", str(doc.path), f"product domain {value!r} must equal the repo domain {repo_domain!r}"
                )
            )
        elif value not in ctx.taxonomy:
            findings.append(Finding("domain", str(doc.path), f"unknown domain path {value!r}"))
    return findings


def rule_restricted(ctx: RepoContext, _: Options) -> list[Finding]:
    """Check that a contract in a Restricted data domain declares `classification: restricted`."""
    findings = []
    for doc in ctx.contracts:
        domain = doc.data.get("domain")
        node = _resolve_data_domain(ctx.taxonomy, domain, str(doc.path))[0]
        restricted = node is not None and node.details.sensitivity == "restricted"
        if restricted and get_custom(doc.data, "classification") != "restricted":
            findings.append(
                Finding(
                    "restricted",
                    str(doc.path),
                    f"domain {domain!r} is Restricted: set customProperties classification: restricted",
                )
            )
    return findings


def _contracts_by_asset(ctx: RepoContext) -> dict[str, list[Doc]]:
    found: dict[str, list[Doc]] = {}
    for doc in ctx.contracts:
        for obj in doc.data.get("schema", []):
            found.setdefault(str(get_custom(obj, ASSET_ID)), []).append(doc)
    return found


def _target_findings(ctx: RepoContext, by_asset: dict[str, list[Doc]]) -> tuple[list[Finding], set[str]]:
    """Check each spec target's schema name and contract count; also return the set of target asset ids."""
    findings: list[Finding] = []
    targets: set[str] = set()
    layer_pattern = f"{re.escape(ctx.project_id)}_({'|'.join(LAYERS)})"
    for info in ctx.specs:
        where = str(info.spec.path)
        for target in info.spec.targets:
            try:
                schema = schema_name(target.database, info.tokens)
            except SpecError as error:
                findings.append(Finding("spec-coverage", where, str(error)))
                continue
            asset = f"{schema}.{target.table}"
            targets.add(asset)
            if not re.fullmatch(layer_pattern, schema):
                message = f"schema {schema!r} must be <project_id>_<layer>, layer in {LAYERS}"
                findings.append(Finding("spec-coverage", where, message))
            count = len(by_asset.get(asset, []))
            if count != 1:
                message = f"{asset} has {count} contracts; expected exactly 1 (run: make contracts)"
                findings.append(Finding("spec-coverage", where, message))
    return findings, targets


def _job_written_assets(ctx: RepoContext) -> set[str]:
    return {
        str(get_custom(obj, ASSET_ID))
        for doc in ctx.contracts
        for obj in doc.data.get("schema", [])
        if get_custom(obj, PRODUCER) == "job"
    }


def rule_spec_coverage(ctx: RepoContext, _: Options) -> list[Finding]:
    """Check that every spec target has exactly one contract, every contract has a target, and schemas follow the naming rule."""
    by_asset = _contracts_by_asset(ctx)
    findings, targets = _target_findings(ctx, by_asset)
    allowed = targets | _job_written_assets(ctx)
    findings += [
        Finding("spec-coverage", str(docs[0].path), f"contract for {asset} has no spec target")
        for asset, docs in by_asset.items()
        if asset not in allowed
    ]
    return findings


def rule_drift(ctx: RepoContext, _: Options) -> list[Finding]:
    """Check that the machine-owned contract sections equal what the specs generate."""
    try:
        plans = plan_contracts(ctx)
    except (SpecError, KeyError, OSError) as error:
        return [Finding("drift", "specs", f"cannot compute contracts: {error}")]
    return [
        Finding("drift", str(p.path), f"contract is out of date for {p.asset_id} (run: make contracts)")
        for p in plans
        if p.action == "update"
    ]


def _product_ports(doc: Doc, direction: str) -> list[dict[str, Any]]:
    return doc.data.get(f"{direction}Ports", []) or []


def _output_port_findings(doc: Doc, local: set[str]) -> list[Finding]:
    return [
        Finding(
            "product",
            str(doc.path),
            f"output port {port.get('name')!r}: contract {port['contractId']!r} is not in this repo",
        )
        for port in _product_ports(doc, "output")
        if port.get("contractId") and port["contractId"] not in local
    ]


def _input_port_findings(doc: Doc, local: set[str]) -> list[Finding]:
    findings = []
    for port in _product_ports(doc, "input"):
        contract = str(port.get("contractId", ""))
        if not contract.startswith("dc_"):
            findings.append(
                Finding(
                    "product",
                    str(doc.path),
                    f"input port {port.get('name')!r}: contractId must be a contract id (dc_...)",
                )
            )
        elif contract not in local:
            message = f"input port {port.get('name')!r}: {contract} is not in this repo; verified when published"
            findings.append(Finding("product", str(doc.path), message, "warning"))
    return findings


def rule_products(ctx: RepoContext, _: Options) -> list[Finding]:
    """Check product ports: contract ids resolve, each contract is exposed once, and cross-project reads have input ports."""
    local = {doc.doc_id for doc in ctx.contracts}
    exposed = Counter(
        p.get("contractId") for doc in ctx.products for p in _product_ports(doc, "output") if p.get("contractId")
    )
    findings = []
    for doc in ctx.products:
        findings += _output_port_findings(doc, local) + _input_port_findings(doc, local)
    for doc in ctx.contracts:
        if exposed[doc.doc_id] != 1:
            findings.append(
                Finding(
                    "product",
                    str(doc.path),
                    f"{doc.doc_id} is exposed by {exposed[doc.doc_id]} output ports; expected exactly 1",
                )
            )
    return findings + (_cross_project_inputs(ctx) if ctx.kind == "project" else [])


def _cross_project_inputs(ctx: RepoContext) -> list[Finding]:
    findings = []
    products = {doc.doc_id: doc for doc in ctx.products}
    for info in ctx.specs:
        product = products.get(str(info.spec.tags.get("data_product")))
        for source in info.spec.sources.values():
            try:
                reads = schema_name(source.database, info.tokens)
            except SpecError:
                continue
            if (
                not reads.startswith(f"{ctx.project_id}_")
                and product is not None
                and not _product_ports(product, "input")
            ):
                findings.append(
                    Finding(
                        "product",
                        str(product.path),
                        f"{info.spec.spec_id} reads {reads}.{source.table} from another project; the product needs an input port",
                    )
                )
    return findings


def _authoritative(data: dict[str, Any]) -> list[dict[str, Any]]:
    found = list(data.get("authoritativeDefinitions", []) or [])
    for obj in data.get("schema", []) or []:
        found += obj.get("authoritativeDefinitions", []) or []
        for prop in obj.get("properties", []) or []:
            found += prop.get("authoritativeDefinitions", []) or []
    return found


def rule_semantics(ctx: RepoContext, _: Options) -> list[Finding]:
    """Check that every semantic assignment is a concept in the platform glossary."""
    findings = []
    for doc in ctx.contracts + ctx.products:
        for definition in _authoritative(doc.data):
            if definition.get("type") == SEMANTIC_ASSIGNMENT and definition.get("url") not in ctx.glossary_iris:
                findings.append(
                    Finding(
                        "semantics",
                        str(doc.path),
                        f"{definition.get('url')!r} is not a concept in metadata/_platform/glossary.ttl",
                    )
                )
    return findings


def _semver(text: Any) -> tuple[int, int, int] | None:
    match = _SEMVER.match(str(text))
    return tuple(int(g) for g in match.groups()) if match else None  # type: ignore[return-value]


def _physical(doc: dict[str, Any]) -> dict[tuple[str, str], tuple[str, str]]:
    return {
        (o.get("name", ""), p["name"]): (p.get("logicalType", ""), p.get("physicalType", ""))
        for o in doc.get("schema", []) or []
        for p in o.get("properties", []) or []
    }


def _required_bump(old: dict[str, Any], new: dict[str, Any]) -> str:
    before, after = _physical(old), _physical(new)
    broken = [k for k in before if k not in after or after[k] != before[k]]
    return "major" if broken else "any"


def _without_version(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if k != "version"}


def _semver_findings(doc: Doc, old: dict[str, Any]) -> list[Finding]:
    new = doc.data
    if content_hash(_without_version(old)) == content_hash(_without_version(new)):
        return []
    before, after = _semver(old.get("version")), _semver(new.get("version"))
    if after is None:
        return [Finding("semver", str(doc.path), f"version {new.get('version')!r} is not MAJOR.MINOR.PATCH")]
    if before is None or after <= before:
        return [
            Finding("semver", str(doc.path), f"content changed but version is still {new.get('version')}; bump it")
        ]
    if _required_bump(old, new) == "major" and after[0] <= before[0]:
        return [Finding("semver", str(doc.path), "a column was removed or retyped; this needs a MAJOR version bump")]
    return []


def rule_semver(ctx: RepoContext, options: Options) -> list[Finding]:
    """Check that a changed contract or product has a higher semver (major if a column was removed or retyped)."""
    if options.baseline is None:
        return []
    findings = []
    for doc in ctx.contracts + ctx.products:
        old = options.baseline(str(doc.path))
        if old is not None:
            findings += _semver_findings(doc, old)
    return findings


def rule_platform_integrity(ctx: RepoContext, _: Options) -> list[Finding]:
    """Check that the `metadata/_platform` snapshots are unedited (their hash headers match)."""
    return [
        Finding("platform", f"metadata/_platform/{name}", problem)
        for name, problem in verify_directory(ctx.root / "metadata/_platform")
    ]


def rule_repo_kind(ctx: RepoContext, _: Options) -> list[Finding]:
    """Check that the platform snapshot declares a valid kind and a domain in the taxonomy."""
    findings = []
    if ctx.kind not in ("domain", "project"):
        findings.append(
            Finding("repo", "metadata/_platform/repo.yaml", f"kind must be 'domain' or 'project', got {ctx.kind!r}")
        )
    if ctx.repo.get("domain") not in ctx.taxonomy:
        findings.append(
            Finding(
                "repo", "metadata/_platform/repo.yaml", f"domain {ctx.repo.get('domain')!r} is not in domains.yaml"
            )
        )
    return findings


def rule_substitutions(ctx: RepoContext, _: Options) -> list[Finding]:
    """Check that every bundle's generated substitution files match conf/environments.yml and the project id."""
    return [
        Finding("substitutions", str(plan.path.relative_to(ctx.root)), "out of date (run: make sync-substitutions)")
        for plan in plan_substitutions(ctx)
        if not plan.current
    ]


def rule_table_defaults(ctx: RepoContext, _: Options) -> list[Finding]:
    """Check spec targets against conf/table_defaults.yml: required properties, clustering, unread change feeds."""
    return check_table_defaults(ctx)


def rule_ingestion_method(ctx: RepoContext, _: Options) -> list[Finding]:
    """Check every bronze table declares how it is ingested, and uses the cheapest method that fits or says why not."""
    return check_ingestion(ctx)


DEFAULT_RULES: tuple[Rule, ...] = (
    rule_platform_integrity,
    rule_repo_kind,
    rule_substitutions,
    rule_json_schema,
    rule_api_version,
    rule_ids,
    rule_domains,
    rule_restricted,
    rule_spec_coverage,
    rule_drift,
    rule_products,
    rule_semantics,
    rule_semver,
    rule_table_defaults,
    rule_ingestion_method,
)


def validate(
    ctx: RepoContext, options: Options | None = None, rules: tuple[Rule, ...] = DEFAULT_RULES
) -> list[Finding]:
    """Run every rule and return all findings."""
    opts = options or Options()
    return [finding for rule in rules for finding in rule(ctx, opts)]
