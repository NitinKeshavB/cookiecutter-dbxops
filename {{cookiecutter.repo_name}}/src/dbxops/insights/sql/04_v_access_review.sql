-- Access review for every contracted table in the target catalog: who is granted what, which personal-data columns
-- (classified in the contract) have no column mask, and whether a row filter applies. One row per table.
CREATE OR REPLACE VIEW {insights}.v_access_review AS
WITH contracted AS (
  SELECT o.asset_id, c.repo_id AS project_id, o.contract_id
  FROM {meta}.v_contract_object_current o
  JOIN {meta}.v_data_contract_current c ON c.entity_id = o.contract_id
  WHERE o.asset_id <> ''
),
pii AS (
  SELECT o.asset_id, p.property_path AS column_name
  FROM {meta}.v_contract_property_current p
  JOIN {meta}.v_contract_object_current o
    ON o.contract_id = p.contract_id AND o.record_version = p.record_version AND o.object_name = p.object_name
  WHERE p.is_pii OR lower(p.classification) IN ('confidential', 'restricted', 'pii', 'sensitive')
),
masks AS (
  SELECT concat(table_schema, '.', table_name) AS asset_id, column_name
  FROM system.information_schema.column_masks
  WHERE table_catalog = '{catalog_literal}'
),
grants AS (
  SELECT concat(table_schema, '.', table_name) AS asset_id, grantee, privilege_type
  FROM system.information_schema.table_privileges
  WHERE table_catalog = '{catalog_literal}'
),
filters AS (
  SELECT DISTINCT concat(table_schema, '.', table_name) AS asset_id
  FROM system.information_schema.row_filters
  WHERE table_catalog = '{catalog_literal}'
)
SELECT t.project_id,
       t.asset_id,
       t.contract_id,
       (SELECT count(*) FROM pii WHERE pii.asset_id = t.asset_id) AS pii_columns,
       (SELECT count(*) FROM pii
          WHERE pii.asset_id = t.asset_id
            AND NOT EXISTS (SELECT 1 FROM masks m WHERE m.asset_id = pii.asset_id AND m.column_name = pii.column_name)
       ) AS unmasked_pii_columns,
       EXISTS (SELECT 1 FROM filters f WHERE f.asset_id = t.asset_id) AS has_row_filter,
       (SELECT collect_set(concat(g.grantee, ':', g.privilege_type)) FROM grants g WHERE g.asset_id = t.asset_id)
         AS grants,
       (SELECT count(DISTINCT g.grantee) FROM grants g WHERE g.asset_id = t.asset_id AND g.grantee LIKE '%@%')
         AS grants_to_individuals
FROM contracted t
