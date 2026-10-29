# Metadata database: data dictionary

Generated from the model by `python -m dbxops.metastore dictionary` (`make metadata-dictionary`). Do not edit by hand:
a test fails when this file differs from the model. Narrative and rules are in `Metadata_Model.md`.

Schema `platform_meta`; 37 tables and 24 views.

### Pattern A: versioned definitions (parents)

SCD2: a changed document closes the current row and inserts the next `record_version`.

#### `md_domain`

Owner: registry repo (`_platform`). Primary key: `entity_id, record_version`.

| column | type | null | key |
|---|---|---|---|
| `entity_id` | TEXT | no | PK |
| `record_version` | INTEGER | no | PK |
| `repo_id` | TEXT | no |  |
| `semver` | TEXT | yes |  |
| `status` | TEXT | no |  |
| `content_hash` | TEXT | no |  |
| `payload` | JSON | no |  |
| `introduced_commit` | TEXT | yes |  |
| `last_seen_commit` | TEXT | yes |  |
| `source_path` | TEXT | yes |  |
| `retired_commit` | TEXT | yes |  |
| `valid_from` | TIMESTAMP (UTC) | no |  |
| `valid_to` | TIMESTAMP (UTC) | yes |  |
| `is_current` | BOOLEAN | no |  |
| `published_at` | TIMESTAMP (UTC) | no |  |
| `published_by` | TEXT | no |  |
| `name` | TEXT | yes |  |
| `parent_domain_id` | TEXT | yes |  |
| `description` | TEXT | yes |  |

#### `md_team`

Owner: registry repo (`_platform`). Primary key: `entity_id, record_version`.

| column | type | null | key |
|---|---|---|---|
| `entity_id` | TEXT | no | PK |
| `record_version` | INTEGER | no | PK |
| `repo_id` | TEXT | no |  |
| `semver` | TEXT | yes |  |
| `status` | TEXT | no |  |
| `content_hash` | TEXT | no |  |
| `payload` | JSON | no |  |
| `introduced_commit` | TEXT | yes |  |
| `last_seen_commit` | TEXT | yes |  |
| `source_path` | TEXT | yes |  |
| `retired_commit` | TEXT | yes |  |
| `valid_from` | TIMESTAMP (UTC) | no |  |
| `valid_to` | TIMESTAMP (UTC) | yes |  |
| `is_current` | BOOLEAN | no |  |
| `published_at` | TIMESTAMP (UTC) | no |  |
| `published_by` | TEXT | no |  |
| `name` | TEXT | yes |  |
| `title` | TEXT | yes |  |
| `domain_id` | TEXT | yes |  |
| `github_team` | TEXT | yes |  |

#### `md_project`

Owner: registry repo (`_platform`). Primary key: `entity_id, record_version`.

| column | type | null | key |
|---|---|---|---|
| `entity_id` | TEXT | no | PK |
| `record_version` | INTEGER | no | PK |
| `repo_id` | TEXT | no |  |
| `semver` | TEXT | yes |  |
| `status` | TEXT | no |  |
| `content_hash` | TEXT | no |  |
| `payload` | JSON | no |  |
| `introduced_commit` | TEXT | yes |  |
| `last_seen_commit` | TEXT | yes |  |
| `source_path` | TEXT | yes |  |
| `retired_commit` | TEXT | yes |  |
| `valid_from` | TIMESTAMP (UTC) | no |  |
| `valid_to` | TIMESTAMP (UTC) | yes |  |
| `is_current` | BOOLEAN | no |  |
| `published_at` | TIMESTAMP (UTC) | no |  |
| `published_by` | TEXT | no |  |
| `title` | TEXT | yes |  |
| `project_type` | TEXT | yes |  |
| `repo_kind` | TEXT | yes |  |
| `owner_team_id` | TEXT | yes |  |
| `domain_id` | TEXT | yes |  |
| `repo_url` | TEXT | yes |  |
| `service_principal_app_id` | TEXT | yes |  |

#### `md_glossary_concept`

Owner: registry repo (`_platform`). Primary key: `entity_id, record_version`.

| column | type | null | key |
|---|---|---|---|
| `entity_id` | TEXT | no | PK |
| `record_version` | INTEGER | no | PK |
| `repo_id` | TEXT | no |  |
| `semver` | TEXT | yes |  |
| `status` | TEXT | no |  |
| `content_hash` | TEXT | no |  |
| `payload` | JSON | no |  |
| `introduced_commit` | TEXT | yes |  |
| `last_seen_commit` | TEXT | yes |  |
| `source_path` | TEXT | yes |  |
| `retired_commit` | TEXT | yes |  |
| `valid_from` | TIMESTAMP (UTC) | no |  |
| `valid_to` | TIMESTAMP (UTC) | yes |  |
| `is_current` | BOOLEAN | no |  |
| `published_at` | TIMESTAMP (UTC) | no |  |
| `published_by` | TEXT | no |  |
| `iri` | TEXT | yes |  |
| `label` | TEXT | yes |  |
| `definition` | TEXT | yes |  |
| `scheme_iri` | TEXT | yes |  |
| `broader_iris` | JSON | yes |  |

#### `md_data_product`

Owner: squad repo (its `project_id`). Primary key: `entity_id, record_version`.

| column | type | null | key |
|---|---|---|---|
| `entity_id` | TEXT | no | PK |
| `record_version` | INTEGER | no | PK |
| `repo_id` | TEXT | no |  |
| `semver` | TEXT | yes |  |
| `status` | TEXT | no |  |
| `content_hash` | TEXT | no |  |
| `payload` | JSON | no |  |
| `introduced_commit` | TEXT | yes |  |
| `last_seen_commit` | TEXT | yes |  |
| `source_path` | TEXT | yes |  |
| `retired_commit` | TEXT | yes |  |
| `valid_from` | TIMESTAMP (UTC) | no |  |
| `valid_to` | TIMESTAMP (UTC) | yes |  |
| `is_current` | BOOLEAN | no |  |
| `published_at` | TIMESTAMP (UTC) | no |  |
| `published_by` | TEXT | no |  |
| `name` | TEXT | yes |  |
| `api_version` | TEXT | yes |  |
| `domain_id` | TEXT | yes |  |
| `project_id` | TEXT | yes |  |
| `owner_team_id` | TEXT | yes |  |

#### `md_data_contract`

Owner: squad repo (its `project_id`). Primary key: `entity_id, record_version`.

| column | type | null | key |
|---|---|---|---|
| `entity_id` | TEXT | no | PK |
| `record_version` | INTEGER | no | PK |
| `repo_id` | TEXT | no |  |
| `semver` | TEXT | yes |  |
| `status` | TEXT | no |  |
| `content_hash` | TEXT | no |  |
| `payload` | JSON | no |  |
| `introduced_commit` | TEXT | yes |  |
| `last_seen_commit` | TEXT | yes |  |
| `source_path` | TEXT | yes |  |
| `retired_commit` | TEXT | yes |  |
| `valid_from` | TIMESTAMP (UTC) | no |  |
| `valid_to` | TIMESTAMP (UTC) | yes |  |
| `is_current` | BOOLEAN | no |  |
| `published_at` | TIMESTAMP (UTC) | no |  |
| `published_by` | TEXT | no |  |
| `name` | TEXT | yes |  |
| `api_version` | TEXT | yes |  |
| `domain_id` | TEXT | yes |  |
| `product_id` | TEXT | yes |  |
| `owner_team_id` | TEXT | yes |  |
| `purpose` | TEXT | yes |  |
| `usage` | TEXT | yes |  |
| `limitations` | TEXT | yes |  |

#### `md_dataflow_spec`

Owner: squad repo (its `project_id`). Primary key: `entity_id, record_version`.

| column | type | null | key |
|---|---|---|---|
| `entity_id` | TEXT | no | PK |
| `record_version` | INTEGER | no | PK |
| `repo_id` | TEXT | no |  |
| `semver` | TEXT | yes |  |
| `status` | TEXT | no |  |
| `content_hash` | TEXT | no |  |
| `payload` | JSON | no |  |
| `introduced_commit` | TEXT | yes |  |
| `last_seen_commit` | TEXT | yes |  |
| `source_path` | TEXT | yes |  |
| `retired_commit` | TEXT | yes |  |
| `valid_from` | TIMESTAMP (UTC) | no |  |
| `valid_to` | TIMESTAMP (UTC) | yes |  |
| `is_current` | BOOLEAN | no |  |
| `published_at` | TIMESTAMP (UTC) | no |  |
| `published_by` | TEXT | no |  |
| `bundle` | TEXT | yes |  |
| `flow_group` | TEXT | yes |  |
| `layer` | TEXT | yes |  |
| `spec_type` | TEXT | yes |  |

#### `md_asset`

Owner: squad repo (its `project_id`). Primary key: `entity_id, record_version`.

| column | type | null | key |
|---|---|---|---|
| `entity_id` | TEXT | no | PK |
| `record_version` | INTEGER | no | PK |
| `repo_id` | TEXT | no |  |
| `semver` | TEXT | yes |  |
| `status` | TEXT | no |  |
| `content_hash` | TEXT | no |  |
| `payload` | JSON | no |  |
| `introduced_commit` | TEXT | yes |  |
| `last_seen_commit` | TEXT | yes |  |
| `source_path` | TEXT | yes |  |
| `retired_commit` | TEXT | yes |  |
| `valid_from` | TIMESTAMP (UTC) | no |  |
| `valid_to` | TIMESTAMP (UTC) | yes |  |
| `is_current` | BOOLEAN | no |  |
| `published_at` | TIMESTAMP (UTC) | no |  |
| `published_by` | TEXT | no |  |
| `asset_type` | TEXT | yes |  |
| `ontos_asset_type` | TEXT | yes |  |
| `fqn_template` | TEXT | yes |  |
| `bundle` | TEXT | yes |  |
| `resource_key` | TEXT | yes |  |
| `product_id` | TEXT | yes |  |

### Pattern A: child tables

A full snapshot of the children per parent version; no version columns of their own.

#### `md_team_member`

 Primary key: `team_id, record_version, member_type, member_identifier, role`.

| column | type | null | key |
|---|---|---|---|
| `team_id` | TEXT | no | PK |
| `record_version` | INTEGER | no | PK |
| `member_type` | TEXT | no | PK |
| `member_identifier` | TEXT | no | PK |
| `role` | TEXT | no | PK |

#### `md_project_contact`

 Primary key: `project_id, record_version, role, email`.

| column | type | null | key |
|---|---|---|---|
| `project_id` | TEXT | no | PK |
| `record_version` | INTEGER | no | PK |
| `role` | TEXT | no | PK |
| `email` | TEXT | no | PK |
| `channel` | TEXT | yes |  |

#### `md_project_group`

 Primary key: `project_id, record_version, access_role`.

| column | type | null | key |
|---|---|---|---|
| `project_id` | TEXT | no | PK |
| `record_version` | INTEGER | no | PK |
| `access_role` | TEXT | no | PK |
| `ad_group` | TEXT | yes |  |

#### `md_product_port`

 Primary key: `product_id, record_version, direction, port_name`.

| column | type | null | key |
|---|---|---|---|
| `product_id` | TEXT | no | PK |
| `record_version` | INTEGER | no | PK |
| `direction` | TEXT | no | PK |
| `port_name` | TEXT | no | PK |
| `port_version` | TEXT | yes |  |
| `contract_id` | TEXT | yes |  |
| `asset_id` | TEXT | yes |  |
| `asset_type` | TEXT | yes |  |
| `contains_pii` | BOOLEAN | yes |  |

#### `md_port_input_contract`

 Primary key: `product_id, record_version, port_name, contract_id`.

| column | type | null | key |
|---|---|---|---|
| `product_id` | TEXT | no | PK |
| `record_version` | INTEGER | no | PK |
| `port_name` | TEXT | no | PK |
| `contract_id` | TEXT | no | PK |
| `contract_semver` | TEXT | yes |  |

#### `md_contract_server`

 Primary key: `contract_id, record_version, environment`.

| column | type | null | key |
|---|---|---|---|
| `contract_id` | TEXT | no | PK |
| `record_version` | INTEGER | no | PK |
| `environment` | TEXT | no | PK |
| `type` | TEXT | yes |  |
| `catalog` | TEXT | yes |  |
| `schema` | TEXT | yes |  |

#### `md_contract_object`

 Primary key: `contract_id, record_version, object_name`.

| column | type | null | key |
|---|---|---|---|
| `contract_id` | TEXT | no | PK |
| `record_version` | INTEGER | no | PK |
| `object_name` | TEXT | no | PK |
| `physical_name` | TEXT | yes |  |
| `logical_type` | TEXT | yes |  |
| `physical_type` | TEXT | yes |  |
| `business_name` | TEXT | yes |  |
| `asset_id` | TEXT | yes |  |

#### `md_contract_property`

 Primary key: `contract_id, record_version, object_name, property_path`.

| column | type | null | key |
|---|---|---|---|
| `contract_id` | TEXT | no | PK |
| `record_version` | INTEGER | no | PK |
| `object_name` | TEXT | no | PK |
| `property_path` | TEXT | no | PK |
| `parent_path` | TEXT | yes |  |
| `logical_type` | TEXT | yes |  |
| `physical_type` | TEXT | yes |  |
| `required` | BOOLEAN | yes |  |
| `primary_key` | BOOLEAN | yes |  |
| `pk_position` | INTEGER | yes |  |
| `unique` | BOOLEAN | yes |  |
| `partitioned` | BOOLEAN | yes |  |
| `classification` | TEXT | yes |  |
| `critical` | BOOLEAN | yes |  |
| `is_pii` | BOOLEAN | yes |  |
| `business_name` | TEXT | yes |  |
| `description` | TEXT | yes |  |

#### `md_quality_rule`

 Primary key: `contract_id, record_version, rule_id`.

| column | type | null | key |
|---|---|---|---|
| `contract_id` | TEXT | no | PK |
| `record_version` | INTEGER | no | PK |
| `rule_id` | TEXT | no | PK |
| `object_name` | TEXT | yes |  |
| `property_path` | TEXT | yes |  |
| `type` | TEXT | yes |  |
| `dimension` | TEXT | yes |  |
| `expression` | TEXT | yes |  |
| `action` | TEXT | yes |  |
| `quarantine_asset_id` | TEXT | yes |  |
| `origin` | TEXT | yes |  |
| `spec_id` | TEXT | yes |  |

#### `md_dataflow_flow`

 Primary key: `spec_id, record_version, flow_name`.

| column | type | null | key |
|---|---|---|---|
| `spec_id` | TEXT | no | PK |
| `record_version` | INTEGER | no | PK |
| `flow_name` | TEXT | no | PK |
| `flow_type` | TEXT | yes |  |
| `source_asset_id` | TEXT | yes |  |
| `target_asset_id` | TEXT | yes |  |
| `target_kind` | TEXT | yes |  |

### Pattern B: derived current state

Replaced as a set per publisher; rows missing from the next publish become `is_active = false`.

#### `md_semantic_link`

 Primary key: `entity_type, entity_id, iri`.

| column | type | null | key |
|---|---|---|---|
| `entity_type` | TEXT | no | PK |
| `entity_id` | TEXT | no | PK |
| `iri` | TEXT | no | PK |
| `label` | TEXT | yes |  |
| `repo_id` | TEXT | no |  |
| `is_active` | BOOLEAN | no |  |
| `first_seen_at` | TIMESTAMP (UTC) | no |  |
| `last_seen_at` | TIMESTAMP (UTC) | no |  |

#### `md_tag`

 Primary key: `entity_type, entity_id, tag_key`.

| column | type | null | key |
|---|---|---|---|
| `entity_type` | TEXT | no | PK |
| `entity_id` | TEXT | no | PK |
| `tag_key` | TEXT | no | PK |
| `tag_value` | TEXT | yes |  |
| `repo_id` | TEXT | no |  |
| `is_active` | BOOLEAN | no |  |
| `first_seen_at` | TIMESTAMP (UTC) | no |  |
| `last_seen_at` | TIMESTAMP (UTC) | no |  |

#### `md_lineage_edge`

 Primary key: `target, source_asset_id, target_asset_id, origin`.

| column | type | null | key |
|---|---|---|---|
| `target` | TEXT | no | PK |
| `source_asset_id` | TEXT | no | PK |
| `target_asset_id` | TEXT | no | PK |
| `origin` | TEXT | no | PK |
| `via` | TEXT | yes |  |
| `repo_id` | TEXT | no |  |
| `is_active` | BOOLEAN | no |  |
| `first_seen_at` | TIMESTAMP (UTC) | no |  |
| `last_seen_at` | TIMESTAMP (UTC) | no |  |

### Pattern C: append-only facts

Inserted once per idempotency key; never updated.

#### `md_registration`

 Primary key: `project_id, registry_pr_url`.

| column | type | null | key |
|---|---|---|---|
| `project_id` | TEXT | no | PK |
| `registry_pr_url` | TEXT | no | PK |
| `issue_url` | TEXT | yes |  |
| `requested_by` | TEXT | yes |  |
| `approved_by` | TEXT | yes |  |
| `approved_at` | TIMESTAMP (UTC) | yes |  |
| `provisioned_repo_url` | TEXT | yes |  |
| `repo_id` | TEXT | no |  |
| `recorded_at` | TIMESTAMP (UTC) | no |  |

#### `md_deployment`

 Primary key: `target, bundle, ci_run_id, ci_run_attempt`.

| column | type | null | key |
|---|---|---|---|
| `target` | TEXT | no | PK |
| `bundle` | TEXT | no | PK |
| `ci_run_id` | TEXT | no | PK |
| `ci_run_attempt` | INTEGER | no | PK |
| `deployment_id` | TEXT | yes |  |
| `git_sha` | TEXT | yes |  |
| `ci_run_url` | TEXT | yes |  |
| `deployed_by` | TEXT | yes |  |
| `deployed_at` | TIMESTAMP (UTC) | yes |  |
| `status` | TEXT | yes |  |
| `framework_version` | TEXT | yes |  |
| `dbxops_version` | TEXT | yes |  |
| `repo_id` | TEXT | no |  |
| `recorded_at` | TIMESTAMP (UTC) | no |  |

#### `md_deployed_asset`

 Primary key: `deployment_id, asset_id`.

| column | type | null | key |
|---|---|---|---|
| `deployment_id` | TEXT | no | PK |
| `asset_id` | TEXT | no | PK |
| `asset_record_version` | INTEGER | yes |  |
| `physical_name` | TEXT | yes |  |
| `workspace_object_id` | TEXT | yes |  |
| `repo_id` | TEXT | no |  |
| `recorded_at` | TIMESTAMP (UTC) | no |  |

#### `md_run`

Runtime fact, purged after `retention_days`. Primary key: `target, run_id`.

| column | type | null | key |
|---|---|---|---|
| `target` | TEXT | no | PK |
| `run_id` | TEXT | no | PK |
| `job_asset_id` | TEXT | yes |  |
| `status` | TEXT | yes |  |
| `trigger` | TEXT | yes |  |
| `started_at` | TIMESTAMP (UTC) | yes |  |
| `ended_at` | TIMESTAMP (UTC) | yes |  |
| `git_sha` | TEXT | yes |  |
| `repo_id` | TEXT | no |  |
| `recorded_at` | TIMESTAMP (UTC) | no |  |

#### `md_pipeline_update`

Runtime fact, purged after `retention_days`. Primary key: `target, update_id`.

| column | type | null | key |
|---|---|---|---|
| `target` | TEXT | no | PK |
| `update_id` | TEXT | no | PK |
| `run_id` | TEXT | yes |  |
| `pipeline_asset_id` | TEXT | yes |  |
| `status` | TEXT | yes |  |
| `full_refresh` | BOOLEAN | yes |  |
| `started_at` | TIMESTAMP (UTC) | yes |  |
| `ended_at` | TIMESTAMP (UTC) | yes |  |
| `repo_id` | TEXT | no |  |
| `recorded_at` | TIMESTAMP (UTC) | no |  |

#### `md_dataset_metrics`

Runtime fact, purged after `retention_days`. Primary key: `target, update_id, asset_id`.

| column | type | null | key |
|---|---|---|---|
| `target` | TEXT | no | PK |
| `update_id` | TEXT | no | PK |
| `asset_id` | TEXT | no | PK |
| `rows_written` | BIGINT | yes |  |
| `rows_upserted` | BIGINT | yes |  |
| `rows_deleted` | BIGINT | yes |  |
| `rows_dropped` | BIGINT | yes |  |
| `repo_id` | TEXT | no |  |
| `recorded_at` | TIMESTAMP (UTC) | no |  |

#### `md_quality_result`

Runtime fact, purged after `retention_days`. Primary key: `target, update_id, asset_id, rule_id`.

| column | type | null | key |
|---|---|---|---|
| `target` | TEXT | no | PK |
| `update_id` | TEXT | no | PK |
| `asset_id` | TEXT | no | PK |
| `rule_id` | TEXT | no | PK |
| `contract_id` | TEXT | yes |  |
| `rule_record_version` | INTEGER | yes |  |
| `passed` | BIGINT | yes |  |
| `failed` | BIGINT | yes |  |
| `dropped` | BIGINT | yes |  |
| `quarantined` | BIGINT | yes |  |
| `repo_id` | TEXT | no |  |
| `recorded_at` | TIMESTAMP (UTC) | no |  |

#### `md_eval_result`

Runtime fact, purged after `retention_days`. Primary key: `target, run_id, subject_asset_id, subject_version, metric`.

| column | type | null | key |
|---|---|---|---|
| `target` | TEXT | no | PK |
| `run_id` | TEXT | no | PK |
| `subject_asset_id` | TEXT | no | PK |
| `subject_version` | TEXT | no | PK |
| `metric` | TEXT | no | PK |
| `eval_kind` | TEXT | yes |  |
| `value` | DOUBLE | yes |  |
| `threshold` | DOUBLE | yes |  |
| `comparator` | TEXT | yes |  |
| `passed` | BOOLEAN | yes |  |
| `alias` | TEXT | yes |  |
| `gate_source_path` | TEXT | yes |  |
| `repo_id` | TEXT | no |  |
| `recorded_at` | TIMESTAMP (UTC) | no |  |

#### `md_run_performance`

Runtime fact, purged after `retention_days`. Primary key: `target, update_id`.

| column | type | null | key |
|---|---|---|---|
| `target` | TEXT | no | PK |
| `update_id` | TEXT | no | PK |
| `run_id` | TEXT | yes |  |
| `pipeline_asset_id` | TEXT | yes |  |
| `duration_seconds` | DOUBLE | yes |  |
| `rows_written` | BIGINT | yes |  |
| `rows_per_second` | DOUBLE | yes |  |
| `full_refresh` | BOOLEAN | yes |  |
| `repo_id` | TEXT | no |  |
| `recorded_at` | TIMESTAMP (UTC) | no |  |

#### `md_cost_daily`

 Primary key: `usage_date, object_type, object_id, sku_name`.

| column | type | null | key |
|---|---|---|---|
| `usage_date` | TEXT | no | PK |
| `object_type` | TEXT | no | PK |
| `object_id` | TEXT | no | PK |
| `sku_name` | TEXT | no | PK |
| `day_index` | INTEGER | yes |  |
| `project_id` | TEXT | yes |  |
| `target` | TEXT | yes |  |
| `bundle` | TEXT | yes |  |
| `git_repo` | TEXT | yes |  |
| `git_sha` | TEXT | yes |  |
| `attribution` | TEXT | yes |  |
| `dbus` | DOUBLE | yes |  |
| `list_cost` | DOUBLE | yes |  |
| `currency` | TEXT | yes |  |
| `repo_id` | TEXT | no |  |
| `recorded_at` | TIMESTAMP (UTC) | no |  |

#### `md_tuning_recommendation`

Runtime fact, purged after `retention_days`. Primary key: `target, asset_id, rule_code, evidence_key`.

| column | type | null | key |
|---|---|---|---|
| `target` | TEXT | no | PK |
| `asset_id` | TEXT | no | PK |
| `rule_code` | TEXT | no | PK |
| `evidence_key` | TEXT | no | PK |
| `severity` | TEXT | yes |  |
| `message` | TEXT | yes |  |
| `expected_saving_pct` | DOUBLE | yes |  |
| `evidence` | JSON | yes |  |
| `repo_id` | TEXT | no |  |
| `recorded_at` | TIMESTAMP (UTC) | no |  |

#### `md_tuning_decision`

 Primary key: `target, asset_id, rule_code, evidence_key`.

| column | type | null | key |
|---|---|---|---|
| `target` | TEXT | no | PK |
| `asset_id` | TEXT | no | PK |
| `rule_code` | TEXT | no | PK |
| `evidence_key` | TEXT | no | PK |
| `decision` | TEXT | yes |  |
| `decided_by` | TEXT | yes |  |
| `reason` | TEXT | yes |  |
| `repo_id` | TEXT | no |  |
| `recorded_at` | TIMESTAMP (UTC) | no |  |

#### `md_design_decision`

 Primary key: `asset_id, git_sha`.

| column | type | null | key |
|---|---|---|---|
| `asset_id` | TEXT | no | PK |
| `git_sha` | TEXT | no | PK |
| `recommended` | TEXT | yes |  |
| `chosen` | TEXT | yes |  |
| `justification` | TEXT | yes |  |
| `approver` | TEXT | yes |  |
| `repo_id` | TEXT | no |  |
| `recorded_at` | TIMESTAMP (UTC) | no |  |

#### `md_run_event`

Runtime fact, purged after `retention_days`. Primary key: `run_id, seq`.

| column | type | null | key |
|---|---|---|---|
| `run_id` | TEXT | no | PK |
| `seq` | INTEGER | no | PK |
| `level` | TEXT | yes |  |
| `message` | TEXT | yes |  |
| `logged_at` | TIMESTAMP (UTC) | yes |  |
| `context` | JSON | yes |  |
| `repo_id` | TEXT | no |  |
| `recorded_at` | TIMESTAMP (UTC) | no |  |

#### `md_publish_log`

 Primary key: `publish_id, status`.

| column | type | null | key |
|---|---|---|---|
| `publish_id` | TEXT | no | PK |
| `status` | TEXT | no | PK |
| `target` | TEXT | yes |  |
| `what` | TEXT | yes |  |
| `git_sha` | TEXT | yes |  |
| `inserted` | INTEGER | yes |  |
| `closed` | INTEGER | yes |  |
| `unchanged` | INTEGER | yes |  |
| `orphans_cleaned` | INTEGER | yes |  |
| `message` | TEXT | yes |  |
| `started_at` | TIMESTAMP (UTC) | yes |  |
| `finished_at` | TIMESTAMP (UTC) | yes |  |
| `repo_id` | TEXT | no |  |
| `recorded_at` | TIMESTAMP (UTC) | no |  |

### Administration

Schema version bookkeeping.

#### `md_schema_version`

 Primary key: `version`.

| column | type | null | key |
|---|---|---|---|
| `version` | INTEGER | no | PK |
| `description` | TEXT | yes |  |
| `applied_at` | TIMESTAMP (UTC) | no |  |

### Views

Read through these; they join each parent's current row to its children at the matching version.

- `v_asset_current`
- `v_burn_rate`
- `v_contract_object_current`
- `v_contract_property_current`
- `v_contract_server_current`
- `v_cost_by_project`
- `v_data_contract_current`
- `v_data_product_current`
- `v_dataflow_flow_current`
- `v_dataflow_spec_current`
- `v_domain_current`
- `v_glossary_concept_current`
- `v_lineage`
- `v_lineage_drift`
- `v_open_tuning_recommendations`
- `v_port_input_contract_current`
- `v_product_port_current`
- `v_project_contact_current`
- `v_project_current`
- `v_project_directory`
- `v_project_group_current`
- `v_quality_rule_current`
- `v_team_current`
- `v_team_member_current`
