-- Job health over the last 30 days: what system.lakeflow saw, labelled with the project, repository and commit tags
-- every bundle puts on its jobs. One row per job.
CREATE OR REPLACE VIEW {insights}.v_job_health AS
WITH jobs AS (
  SELECT workspace_id, job_id, name, tags
  FROM (
    SELECT *, row_number() OVER (PARTITION BY workspace_id, job_id ORDER BY change_time DESC) AS newest
    FROM system.lakeflow.jobs
  )
  WHERE newest = 1 AND delete_time IS NULL
),
runs AS (
  SELECT workspace_id, job_id, run_id, period_end_time, result_state, termination_code,
         run_duration_seconds, queue_duration_seconds
  FROM system.lakeflow.job_run_timeline
  WHERE result_state IS NOT NULL
    AND period_start_time >= current_timestamp() - INTERVAL 30 DAYS
)
SELECT j.workspace_id,
       j.job_id,
       j.name AS job_name,
       j.tags['project_id'] AS project_id,
       j.tags['target'] AS target,
       j.tags['bundle'] AS bundle,
       j.tags['git_repo'] AS git_repo,
       j.tags['git_sha'] AS deployed_git_sha,
       count(r.run_id) AS runs_30d,
       count_if(r.result_state = 'SUCCEEDED') AS succeeded_30d,
       count_if(r.result_state IN ('ERROR', 'FAILED', 'TIMED_OUT')) AS failed_30d,
       round(count_if(r.result_state = 'SUCCEEDED') * 100.0 / nullif(count(r.run_id), 0), 1) AS success_rate_pct,
       percentile_approx(r.run_duration_seconds, 0.5) AS p50_duration_s,
       percentile_approx(r.run_duration_seconds, 0.95) AS p95_duration_s,
       avg(r.queue_duration_seconds) AS avg_queue_s,
       max(r.period_end_time) AS last_run_end,
       max_by(r.result_state, r.period_end_time) AS last_result
FROM jobs j
LEFT JOIN runs r ON r.workspace_id = j.workspace_id AND r.job_id = j.job_id
GROUP BY ALL
