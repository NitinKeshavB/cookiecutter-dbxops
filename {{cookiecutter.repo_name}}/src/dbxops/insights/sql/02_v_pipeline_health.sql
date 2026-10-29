-- Pipeline health over the last 30 days: update outcomes from system.lakeflow, throughput and full refreshes from the
-- performance facts every job's publish_run_metadata task records. One row per pipeline.
CREATE OR REPLACE VIEW {insights}.v_pipeline_health AS
WITH pipelines AS (
  SELECT workspace_id, pipeline_id, tags
  FROM (
    SELECT *, row_number() OVER (PARTITION BY workspace_id, pipeline_id ORDER BY change_time DESC) AS newest
    FROM system.lakeflow.pipelines
  )
  WHERE newest = 1 AND delete_time IS NULL
),
updates AS (
  SELECT pipeline_id, update_id, result_state, period_end_time
  FROM system.lakeflow.pipeline_update_timeline
  WHERE result_state IS NOT NULL
    AND period_start_time >= current_timestamp() - INTERVAL 30 DAYS
),
performance AS (
  SELECT substring(pipeline_asset_id, 13) AS pipeline_id, update_id, duration_seconds, rows_written,
         rows_per_second, full_refresh
  FROM {meta}.md_run_performance
  WHERE recorded_at >= current_timestamp() - INTERVAL 30 DAYS
)
SELECT p.workspace_id,
       p.pipeline_id,
       p.tags['project_id'] AS project_id,
       p.tags['target'] AS target,
       p.tags['git_repo'] AS git_repo,
       p.tags['git_sha'] AS deployed_git_sha,
       count(DISTINCT u.update_id) AS updates_30d,
       count(DISTINCT CASE WHEN u.result_state = 'FAILED' THEN u.update_id END) AS failed_30d,
       max(u.period_end_time) AS last_update_end,
       percentile_approx(f.duration_seconds, 0.5) AS p50_duration_s,
       percentile_approx(f.rows_per_second, 0.5) AS p50_rows_per_second,
       sum(f.rows_written) AS rows_written_30d,
       count_if(f.full_refresh) AS full_refreshes_30d
FROM pipelines p
LEFT JOIN updates u ON u.pipeline_id = p.pipeline_id
LEFT JOIN performance f ON f.pipeline_id = p.pipeline_id AND f.update_id = u.update_id
GROUP BY ALL
