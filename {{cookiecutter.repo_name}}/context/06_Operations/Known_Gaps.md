# What is built, and what is not verified

**Nothing in this repository has run against a real workspace, database, GitHub organisation or Ontos instance.** Everything is
verified offline: unit and integration tests with fakes and SQLite, the Databricks CLI's own JSON schema for bundle
configuration, the Lakeflow Framework's own validator for specs, `actionlint` and `shellcheck` for workflows and scripts.

## Believed correct but unverified live

- Unity Catalog SQL of the metadata sink (the one-statement MERGE, VARIANT, parameter style) and the PostgreSQL / Lakebase
  statements; the PostgreSQL integration tests exist and skip unless `DBXOPS_TEST_PG_URL` is set.
- Event-log reading: the event types and JSON paths in `metastore/event_log.py` follow the Databricks documentation as understood;
  `dropped` and `quarantined` counts are not separately available from the event log.
- Knowledge Assistant REST paths and registered-model tag REST paths in `workspace.py` (the SDK has neither).
- The AIOps agent wrapper, evaluation, `agents.deploy`, `ai_parse_document` on a serverless pipeline, PDF generation; the MLOps
  training, scoring and `condition_task` behaviour. The MLOps feature MV declares no Unity Catalog primary key, which
  `FeatureLookup` may require.
- GitHub provisioning (repository from template, environments, ruleset) and the `github-oidc` deploy authentication.
- Ontos accepting the exported shapes unchanged.

- The `platform_insights` views (`dbxops.insights`): the system-table columns follow the documentation;
  `system.lakeflow.pipeline_update_timeline` and the job timeline tables are in preview in some regions, and the
  lineage column names were not all confirmable from the documentation. Run `python -m dbxops.insights render` and
  each view once in a workspace.
- The cost loader's `system.billing` paths (`usage_metadata.job_name`, `list_prices.pricing.effective_list.default`),
  and that `${bundle.git.*}` values are accepted as job and pipeline tag values.
- The Microsoft Teams notification destination and the budget policies: their ids are placeholders until the
  platform team creates them.

## Not built

- Workspace setup: creating schemas, applying the privilege matrix, grants on `platform_meta`, applying tags in Unity Catalog. The
  reconcilers that would do it exist (`MetadataSyncReconciler`) but no job task or command reads `md_tag` and runs them.
- Recording registrations (`md_registration`) from the provisioning workflow.
- A scheduler for the retention purge; SQL Server; observed-lineage reader.
- Consolidating the MLOps and AIOps lifecycle wrappers into the package (a `--catalog` option on `dbxops.lifecycle reconcile` would remove them).
- A model monitor; contracts for inference, quarantine and trace tables.
- A platform bundle that schedules `dbxops-publish-costs` and `dbxops-deploy-insights` (the job is sketched in
  `Cost_And_Performance.md`), the owner AI/BI dashboard and its SQL alerts.
- Table-layout facts (`DESCRIBE DETAIL` file counts and sizes) and the advisor rules that need them (small files,
  clustering keys versus consumer filters); today `v_table_usage` uses lineage and predictive-optimization history.

## Decisions to confirm

The framework is deployed once per target by the platform team, not by each squad. Squad bundles reference its shared path.
