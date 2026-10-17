# Cost, performance and recommendations

How the platform attributes cost to projects and commits, measures every run, recommends cheaper or faster designs,
and gives each technical owner one place to decide. Nothing here changes a pipeline by itself: recommendations are
accepted or rejected by a person, and accepted changes go through a pull request.

## Cost attribution

Every job and pipeline a bundle deploys carries these tags (`tests/bundles/test_bundles.py` fails without them):

| Tag | From |
|---|---|
| `project_id` | `${var.project_id}` |
| `bundle`, `target` | `${bundle.name}`, `${bundle.target}` |
| `git_repo`, `git_ref`, `git_sha` | `${bundle.git.origin_url}`, `${bundle.git.branch}`, `${bundle.git.commit}` (filled by the CLI from the checkout at deploy time) |
| `cost_center`, `region` | the registration (`metadata/_platform/repo.yaml`) |

Serverless usage only carries tags through a **budget policy**, so every job and pipeline also sets
`budget_policy_id` (one policy per project, created by the platform team with the same tags; a `PLACEHOLDER-` value
blocks deploys until it is set). Pipelines triggered by a job do not inherit the job's policy; both set it.

`dbxops-publish-costs` (platform team, once a day per target, as `_platform`) loads `system.billing.usage` priced with
`system.billing.list_prices` into `md_cost_daily`. Each line is attributed by, in order: its tags, the deployment that
recorded the job or pipeline id (`--meta-schema <catalog>.platform_meta`), the `[<project_id>]` prefix of the job name,
or not at all (`attribution = none`, shown under an empty project id). The window ends `--lag-days` (default 2) before
today because billing rows arrive late and a loaded day is never changed.

```yaml
# a platform bundle's job (sketch; deploy once per target)
tasks:
  - task_key: publish_costs
    environment_key: platform
    python_wheel_task:
      package_name: cookie-cutter-dbx
      entry_point: dbxops-publish-costs
      parameters: [--target, prod, --config, /Volumes/<catalog>/platform/config/metastore.yml,
                   --meta-schema, <catalog>.platform_meta]
  - task_key: deploy_insights
    depends_on: [{task_key: publish_costs}]
    environment_key: platform
    python_wheel_task:
      package_name: cookie-cutter-dbx
      entry_point: dbxops-deploy-insights
      parameters: [--catalog, <catalog>]
```

## Run performance and the tuning advisor

`publish_run_metadata` (the last task of every job) also writes one `md_run_performance` row per pipeline update:
duration, rows written, rows per second and full refresh, all taken from the run's own timings and event log, so
measuring costs no extra scan. It then runs the tuning advisor (`dbxops.metastore.tuning`) over the pipelines' recent
history and records what it finds in `md_tuning_recommendation`:

| Rule | Fires when |
|---|---|
| `slower_than_baseline` | an update took 1.25x the median of the earlier ones |
| `throughput_drop` | rows per second fell below 0.75x the median |
| `repeated_full_refresh` | more than 2 of the last 10 updates were full refreshes |

Answer a recommendation with `python -m dbxops.metastore decide --target <t> --asset <id> --rule <code> --evidence
<update id> --decision accepted|rejected --reason "..."`. `v_open_tuning_recommendations` lists what is waiting.

## Design-time recommendations

- `conf/table_defaults.yml` + rule `table-defaults`: row tracking and liquid clustering on every streaming table; a
  warning for change data feed that nothing in the repo reads. Exceptions are listed with their reason.
- `bundles/<bundle>/src/config/ingestion.yml` + rule `ingestion-method` + `make recommend`: each bronze table declares
  its source and method; a costlier method than the recommended one needs a justification. `python -m
  dbxops.metastore publish --what decisions` records each choice in `md_design_decision`.

## The owner decision model (`platform_insights`)

`python -m dbxops.insights deploy --catalog <c>` (or `render --out <dir>` for a SQL warehouse) creates views that join
`platform_meta` with the system tables:

| View | Shows |
|---|---|
| `v_job_health` | success rate, p50/p95 duration, queue time, last result per job, with project and commit tags |
| `v_pipeline_health` | update outcomes, throughput, rows written and full refreshes per pipeline |
| `v_cost_daily`, `v_cost_per_million_rows` | cost per project, bundle, commit and object per day; cost per million rows for pipelines |
| `v_access_review` | per contracted table: grants, grants to individuals, personal-data columns without a mask, row filters |
| `v_table_usage` | downstream reads, readers and targets over 90 days; predictive-optimization maintenance over 30 days |
| `v_recommendations` | one ranked list: tuning advisor, design choices, unused tables, hot tables without clustering, stale statistics, frequent full refreshes, low success rates, unmasked personal data, grants to individuals, cost spikes |
| `v_owner_summary` | one row per project: cost and growth, failed runs, open and critical recommendations, unused tables, unmasked columns |

Build the owner dashboard (AI/BI) and SQL alerts on `v_owner_summary` and `v_recommendations`.

## Notifications

Every job emails `notification_email` and posts to the `notification_destination_id` workspace destination (Microsoft
Teams) on failure and when it runs past its duration warning threshold. Both are per-target bundle variables that stay
`PLACEHOLDER-` (blocking deploys) until the platform team creates the destination.
