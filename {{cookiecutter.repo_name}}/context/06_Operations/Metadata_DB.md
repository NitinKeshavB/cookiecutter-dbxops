# The metadata database (`platform_meta`)

One shared schema per target, written by every repository. Tables, keys and views: `../03_Architecture/Metadata_Dictionary.md`.
Rules and reasoning: `../03_Architecture/Metadata_Model.md`.

## Sinks

`conf/metastore.yml` lists the sinks of each target. Backends: `uc` (Delta via a SQL warehouse), `lakebase`, `postgres`. SQL Server is
not built; it needs a MERGE strategy for the SQLAlchemy repository. `{catalog}` resolves from `conf/environments.yml`, and a sink
that still contains a `PLACEHOLDER-...` value is refused, so nothing is ever published to a half-configured sink.

## Commands

```bash
python -m dbxops.metastore ensure-schema   --target dev                       # create tables, views, version row
python -m dbxops.metastore publish          --target dev --what definitions,deployments,lineage
python -m dbxops.metastore publish-registry --target dev --registry ../dbx-platform-registry
python -m dbxops.metastore dictionary                                          # data dictionary, markdown
dbxops-publish-run --run-id <id> --target dev --repo-id <project_id> --config <metastore.yml>   # job task
```

`publish` reads this repository's contracts, products and (template-expanded) specs with the `dbxops.metadata` loaders;
`--retire-missing` retires contracts and products that left git. Everything is idempotent: run it again and nothing changes.
`deployments` needs `databricks bundle summary` (run in `--bundle-dir`). Lineage from the specs is *declared*; Unity Catalog's
observed lineage needs a reader that is not wired yet.

## Operational facts and costs

`publish_run_metadata` also writes `md_run_performance`, `md_tuning_recommendation` and `md_run_event` (structured log
events). `dbxops-publish-costs` writes `md_cost_daily` as `_platform`; `publish --what decisions` writes
`md_design_decision`; `decide` writes `md_tuning_decision`. Views: `v_cost_by_project`, `v_burn_rate`,
`v_open_tuning_recommendations`. The owner views over these and the system tables are in `platform_insights`
(`Cost_And_Performance.md`).

## Retention

Runtime facts (`md_run`, `md_pipeline_update`, `md_dataset_metrics`, `md_quality_result`, `md_eval_result`,
`md_run_performance`, `md_tuning_recommendation`, `md_run_event`) are purged after
`retention_days` (default 400) by `purge_runtime`, callable only by the registry writer. **Nothing schedules it yet.**
Definitions and deployments are kept forever.

## Access (policy)

Registry service principal and platform admins: read everything. Squad service principals: read all but contacts and
registrations; write only their own tables. Squad groups: the `v_*_current` views and `v_project_directory`. The grants are part of
workspace setup, which is not built; the database does not enforce them yet.
