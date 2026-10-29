"""
Command line: ``python -m dbxops.insights render|deploy``; also the ``dbxops-deploy-insights`` job task.

``render --catalog <c> [--out <dir>]`` writes (or prints) the view statements, for review or a SQL warehouse.
``deploy --catalog <c>`` creates the views through Spark; the platform team runs it as a job task once per target after
each release, so the views always match the library version that writes ``platform_meta``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import (
    Any,
    Sequence,
)

from dbxops.insights import (
    DEFAULT_INSIGHTS_SCHEMA,
    DEFAULT_META_SCHEMA,
    deploy,
    render,
)


def _spark_executor() -> Any:
    from pyspark.sql import SparkSession  # pylint: disable=import-outside-toplevel

    from dbxops.metastore.uc_repository import SparkSqlExecutor  # pylint: disable=import-outside-toplevel

    return SparkSqlExecutor(SparkSession.builder.getOrCreate())


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser."""
    parser = argparse.ArgumentParser(prog="python -m dbxops.insights", description="The owner decision views.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("render", "deploy"):
        command = commands.add_parser(name)
        command.add_argument("--catalog", required=True, help="the target's catalog (conf/environments.yml)")
        command.add_argument("--meta-schema", default=DEFAULT_META_SCHEMA)
        command.add_argument("--insights-schema", default=DEFAULT_INSIGHTS_SCHEMA)
    commands.choices["render"].add_argument("--out", type=Path, help="write one <view>.sql per view here")
    return parser


def main(argv: Sequence[str] | None = None, *, executor: Any = None) -> int:
    """Run the command line and return the process exit code."""
    args = build_parser().parse_args(argv)
    if args.command == "deploy":
        names = deploy(
            executor or _spark_executor(), args.catalog, args.meta_schema, insights_schema=args.insights_schema
        )
        print(f"deployed {len(names)} views to {args.catalog}.{args.insights_schema}: {', '.join(names)}")
        return 0
    views = render(args.catalog, args.meta_schema, args.insights_schema)
    if args.out is None:
        print(";\n\n".join(view.sql for view in views) + ";")
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    for view in views:
        (args.out / f"{view.name}.sql").write_text(view.sql + ";\n", encoding="utf-8")
    print(f"wrote {len(views)} views under {args.out}")
    return 0


def deploy_main() -> int:
    """Entry point of the ``dbxops-deploy-insights`` job task."""
    return main(["deploy", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
