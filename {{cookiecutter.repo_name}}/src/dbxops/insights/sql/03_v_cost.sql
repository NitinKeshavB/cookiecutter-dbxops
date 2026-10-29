-- Cost per project, repository and commit per day (from md_cost_daily, loaded from system.billing by
-- dbxops-publish-costs), and cost per million rows written for pipelines.
CREATE OR REPLACE VIEW {insights}.v_cost_daily AS
SELECT usage_date, day_index, project_id, target, bundle, git_repo, git_sha, attribution, object_type, object_id,
       sum(dbus) AS dbus, sum(list_cost) AS list_cost, max(currency) AS currency
FROM {meta}.md_cost_daily
GROUP BY ALL;

CREATE OR REPLACE VIEW {insights}.v_cost_per_million_rows AS
WITH cost AS (
  SELECT project_id, object_id AS pipeline_id, usage_date, sum(list_cost) AS list_cost
  FROM {meta}.md_cost_daily
  WHERE object_type = 'pipeline'
  GROUP BY ALL
),
written AS (
  SELECT substring(pipeline_asset_id, 13) AS pipeline_id, CAST(to_date(recorded_at) AS STRING) AS usage_date,
         sum(rows_written) AS rows_written
  FROM {meta}.md_run_performance
  GROUP BY ALL
)
SELECT c.project_id, c.pipeline_id, c.usage_date, c.list_cost, w.rows_written,
       round(c.list_cost * 1000000.0 / nullif(w.rows_written, 0), 4) AS cost_per_million_rows
FROM cost c
LEFT JOIN written w ON w.pipeline_id = c.pipeline_id AND w.usage_date = c.usage_date
