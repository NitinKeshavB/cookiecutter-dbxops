"""Command line for the metadata tooling: `python -m dbxops.metadata <validate|contracts|recommend|export-ontos>`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dbxops.findings import (
    Finding,
    has_errors,
)
from dbxops.metadata.contracts import (
    plan_contracts,
    write_contracts,
)
from dbxops.metadata.export_ontos import export_ontos
from dbxops.metadata.ingestion import (
    load_profiles,
    recommend,
)
from dbxops.metadata.repo import RepoContext
from dbxops.metadata.rules import (
    GitBaseline,
    Options,
    validate,
)
from dbxops.metadata.scaffold import (
    ScaffoldError,
    scaffold_bundle,
)
from dbxops.metadata.specs import SpecError
from dbxops.metadata.substitutions import (
    plan_substitutions,
    write_substitutions,
)


def _print(findings: list[Finding]) -> None:
    print("\n".join(str(f) for f in findings))


def _validate(ctx: RepoContext, baseline: str | None) -> int:
    options = Options(baseline=GitBaseline(ctx.root, baseline) if baseline else None)
    findings = validate(ctx, options)
    if findings:
        _print(findings)
    errors = sum(1 for f in findings if f.severity == "error")
    print(f"{len(ctx.contracts)} contracts, {len(ctx.products)} products, {len(ctx.specs)} specs: {errors} error(s)")
    return 1 if has_errors(findings) else 0


def _contracts(ctx: RepoContext, check: bool) -> int:
    try:
        changed = [p for p in plan_contracts(ctx) if p.action != "unchanged"] if check else write_contracts(ctx)
    except SpecError as error:
        print(f"error: {error}")
        return 1
    for plan in changed:
        print(f"{'would ' if check else ''}{plan.action} {plan.path} ({plan.asset_id})")
    if not changed:
        print("contracts are up to date")
    return 1 if (check and changed) else 0


def _substitutions(ctx: RepoContext, check: bool) -> int:
    changed = [p for p in plan_substitutions(ctx) if not p.current] if check else write_substitutions(ctx)
    for plan in changed:
        print(f"{'would write' if check else 'wrote'} {plan.path.relative_to(ctx.root)}")
    if not changed:
        print("substitution files are up to date")
    return 1 if (check and changed) else 0


def _recommend(ctx: RepoContext) -> int:
    rows = []
    for bundle_dir in sorted({info.bundle_dir for info in ctx.specs}):
        for table, profile in sorted(load_profiles(bundle_dir).items()):
            best = recommend(profile)
            status = "ok" if profile.method == best.method else ("justified" if profile.justification else "CHANGE")
            rows.append((bundle_dir.name, table, profile.method, best.method, best.runner_up, status, best.reason))
    if not rows:
        print("no ingestion profiles (bundles/<bundle>/src/config/ingestion.yml)")
        return 0
    print("| bundle | table | declared | recommended | runner-up | status | why |")
    print("|---|---|---|---|---|---|---|")
    for row in rows:
        print("| " + " | ".join(row) + " |")
    return 1 if any(row[5] == "CHANGE" for row in rows) else 0


def _new_bundle(root: Path, name: str, data_domain: str) -> int:
    try:
        files = scaffold_bundle(root, name, data_domain)
    except ScaffoldError as error:
        print(f"error: {error}")
        return 1
    print(
        f"created bundles/{name} ({len(files)} files). Next: fill the PLACEHOLDER text in its contract and product, "
        "point the example spec at your real source, then `make validate-metadata`."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = argparse.ArgumentParser(prog="python -m dbxops.metadata")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root (default: current directory)")
    commands = parser.add_subparsers(dest="command", required=True)
    validate_cmd = commands.add_parser("validate", help="run every metadata rule")
    validate_cmd.add_argument(
        "--baseline", help="git ref to compare contract/product versions against, e.g. origin/main"
    )
    contracts_cmd = commands.add_parser("contracts", help="create or refresh contracts from the dataflow specs")
    contracts_cmd.add_argument(
        "--check", action="store_true", help="report what would change; exit 1 if anything would"
    )
    subs_cmd = commands.add_parser("substitutions", help="write each bundle's <target>_substitutions.json from conf/")
    subs_cmd.add_argument("--check", action="store_true", help="report what would change; exit 1 if anything would")
    new_cmd = commands.add_parser("new-bundle", help="scaffold bundles/<name> with a runnable example")
    new_cmd.add_argument("--name", required=True)
    new_cmd.add_argument(
        "--data-domain",
        required=True,
        help="data-domain path of the example table, e.g. claims__litigation__claim_litigation",
    )
    commands.add_parser("recommend", help="compare each bronze table's ingestion method with the cheapest that fits")
    export_cmd = commands.add_parser("export-ontos", help="write Ontos upload files for one target")
    export_cmd.add_argument("--target", required=True)
    export_cmd.add_argument("--out", type=Path, help="output directory (default: build/ontos/<target>)")
    args = parser.parse_args(argv)
    ctx = RepoContext.load(args.root.resolve())
    if args.command == "validate":
        return _validate(ctx, args.baseline)
    if args.command == "contracts":
        return _contracts(ctx, args.check)
    if args.command == "new-bundle":
        return _new_bundle(args.root.resolve(), args.name, args.data_domain)
    if args.command == "substitutions":
        return _substitutions(ctx, args.check)
    if args.command == "recommend":
        return _recommend(ctx)
    out = args.out or ctx.root / "build" / "ontos" / args.target
    paths = export_ontos(ctx, args.target, out)
    print(f"wrote {len(paths)} files under {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
