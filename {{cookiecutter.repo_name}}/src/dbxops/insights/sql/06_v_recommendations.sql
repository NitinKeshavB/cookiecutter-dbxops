-- One ranked list of recommendations per project for its technical owner: from run statistics (the tuning advisor),
-- design choices, downstream table usage and layout, access and cost. Nothing here is applied automatically; an owner
-- accepts or rejects (python -m dbxops.metastore decide) and accepted changes go through a pull request.
CREATE OR REPLACE VIEW {insights}.v_recommendations AS
SELECT repo_id AS project_id, 'run_statistics' AS category, asset_id AS subject, rule_code, severity, message,
       to_json(evidence) AS evidence, expected_saving_pct, recorded_at AS raised_at
FROM {meta}.v_open_tuning_recommendations
UNION ALL
SELECT repo_id, 'design', asset_id, 'method_differs_from_recommendation', 'info',
       concat('chosen ', chosen, ' where ', recommended, ' is recommended: ', justification, '. Re-check it still holds.'),
       to_json(named_struct('chosen', chosen, 'recommended', recommended, 'git_sha', git_sha)), NULL, recorded_at
FROM (
  SELECT *, row_number() OVER (PARTITION BY asset_id ORDER BY recorded_at DESC) AS newest
  FROM {meta}.md_design_decision
)
WHERE newest = 1 AND chosen <> recommended
UNION ALL
SELECT project_id, 'downstream', asset_id, 'unused_table', 'warning',
       'Nobody read this table in 90 days but it is still refreshed: slow or stop its refresh, or retire the port.',
       to_json(named_struct('reads_90d', reads_90d)), NULL, current_timestamp()
FROM {insights}.v_table_usage
WHERE reads_90d = 0
UNION ALL
SELECT project_id, 'downstream', asset_id, 'cluster_hot_table', 'warning',
       'Read often but no clustering maintenance in 30 days: set CLUSTER BY AUTO (or keys from the consumers'' filters) '
       || 'and check predictive optimization is enabled on the schema.',
       to_json(named_struct('reads_90d', reads_90d, 'downstream_tables', downstream_tables)), NULL, current_timestamp()
FROM {insights}.v_table_usage
WHERE reads_90d >= 100 AND last_clustering IS NULL
UNION ALL
SELECT project_id, 'downstream', asset_id, 'stale_statistics', 'info',
       'Read downstream but statistics were not refreshed in 30 days: enable predictive optimization or run ANALYZE.',
       to_json(named_struct('reads_90d', reads_90d)), NULL, current_timestamp()
FROM {insights}.v_table_usage
WHERE reads_90d >= 10 AND last_analyze IS NULL
UNION ALL
SELECT project_id, 'pipeline', concat('uc_pipeline/', pipeline_id), 'frequent_full_refresh', 'warning',
       'More than two full refreshes in 30 days: full refreshes recompute everything; find who triggers them.',
       to_json(named_struct('full_refreshes_30d', full_refreshes_30d)), NULL, current_timestamp()
FROM {insights}.v_pipeline_health
WHERE full_refreshes_30d > 2
UNION ALL
SELECT project_id, 'reliability', concat('uc_job/', job_id), 'low_success_rate', 'critical',
       concat('Only ', success_rate_pct, '% of runs succeeded in 30 days.'),
       to_json(named_struct('runs_30d', runs_30d, 'failed_30d', failed_30d)), NULL, current_timestamp()
FROM {insights}.v_job_health
WHERE runs_30d >= 5 AND success_rate_pct < 90
UNION ALL
SELECT project_id, 'access', asset_id, 'unmasked_personal_data', 'critical',
       'Columns classified as personal data have no column mask.',
       to_json(named_struct('unmasked_pii_columns', unmasked_pii_columns)), NULL, current_timestamp()
FROM {insights}.v_access_review
WHERE unmasked_pii_columns > 0
UNION ALL
SELECT project_id, 'access', asset_id, 'grants_to_individuals', 'warning',
       'Privileges are granted to individual users; grant to the project''s AD groups instead.',
       to_json(named_struct('grants_to_individuals', grants_to_individuals)), NULL, current_timestamp()
FROM {insights}.v_access_review
WHERE grants_to_individuals > 0
UNION ALL
SELECT project_id, 'cost', project_id, 'cost_spike', 'warning',
       concat('Latest day cost is ', round(growth_pct_vs_7d, 0), '% above the 7-day average.'),
       to_json(named_struct('cost_latest_day', cost_latest_day, 'avg_daily_cost_prev_7d', avg_daily_cost_prev_7d)),
       NULL, current_timestamp()
FROM {meta}.v_burn_rate
WHERE growth_pct_vs_7d > 50
