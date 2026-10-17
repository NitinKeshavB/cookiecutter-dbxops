-- How each contracted table in the target catalog is read downstream over the last 90 days (system.access.table_lineage),
-- and how predictive optimization has maintained it over the last 30 days. One row per table.
CREATE OR REPLACE VIEW {insights}.v_table_usage AS
WITH contracted AS (
  SELECT DISTINCT o.asset_id, c.repo_id AS project_id
  FROM {meta}.v_contract_object_current o
  JOIN {meta}.v_data_contract_current c ON c.entity_id = o.contract_id
  WHERE o.asset_id <> ''
),
reads AS (
  SELECT substring(source_table_full_name, length('{catalog_literal}') + 2) AS asset_id,
         count(*) AS reads_90d,
         count(DISTINCT target_table_full_name) AS downstream_tables,
         count(DISTINCT created_by) AS distinct_readers,
         max(event_time) AS last_read
  FROM system.access.table_lineage
  WHERE source_table_full_name LIKE '{catalog_literal}.%'
    AND event_time >= current_timestamp() - INTERVAL 90 DAYS
  GROUP BY 1
),
maintenance AS (
  SELECT concat(schema_name, '.', table_name) AS asset_id,
         max(CASE WHEN operation_type = 'COMPACTION' THEN end_time END) AS last_compaction,
         max(CASE WHEN operation_type IN ('CLUSTERING', 'AUTO_CLUSTERING_COLUMN_SELECTION') THEN end_time END)
           AS last_clustering,
         max(CASE WHEN operation_type = 'ANALYZE' THEN end_time END) AS last_analyze,
         sum(usage_quantity) AS optimization_usage_30d
  FROM system.storage.predictive_optimization_operations_history
  WHERE catalog_name = '{catalog_literal}'
    AND start_time >= current_timestamp() - INTERVAL 30 DAYS
  GROUP BY 1
)
SELECT t.project_id, t.asset_id,
       coalesce(r.reads_90d, 0) AS reads_90d,
       coalesce(r.downstream_tables, 0) AS downstream_tables,
       coalesce(r.distinct_readers, 0) AS distinct_readers,
       r.last_read,
       m.last_compaction, m.last_clustering, m.last_analyze, m.optimization_usage_30d
FROM contracted t
LEFT JOIN reads r ON r.asset_id = t.asset_id
LEFT JOIN maintenance m ON m.asset_id = t.asset_id
