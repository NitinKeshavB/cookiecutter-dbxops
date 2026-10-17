-- One row per project for its technical owner: cost and growth, reliability, open recommendations by severity,
-- access findings and unused tables. The starting point of the owner dashboard.
CREATE OR REPLACE VIEW {insights}.v_owner_summary AS
WITH projects AS (
  SELECT DISTINCT project_id, title, owner_team_id, repo_url FROM {meta}.v_project_directory
)
SELECT p.project_id, p.title, p.owner_team_id, p.repo_url,
       b.cost_latest_day, b.growth_pct_vs_7d, b.projected_30d_cost,
       (SELECT sum(failed_30d) FROM {insights}.v_job_health j WHERE j.project_id = p.project_id) AS failed_runs_30d,
       (SELECT count(*) FROM {insights}.v_recommendations r
          WHERE r.project_id = p.project_id AND r.severity = 'critical') AS critical_recommendations,
       (SELECT count(*) FROM {insights}.v_recommendations r WHERE r.project_id = p.project_id) AS open_recommendations,
       (SELECT count(*) FROM {insights}.v_table_usage u
          WHERE u.project_id = p.project_id AND u.reads_90d = 0) AS unused_tables,
       (SELECT sum(unmasked_pii_columns) FROM {insights}.v_access_review a
          WHERE a.project_id = p.project_id) AS unmasked_pii_columns
FROM projects p
LEFT JOIN {meta}.v_burn_rate b ON b.project_id = p.project_id
