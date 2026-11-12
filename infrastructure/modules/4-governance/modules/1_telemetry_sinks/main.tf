locals {
  monitored_projects = toset(var.spoke_project_ids)

  # Usage records come from every spoke (agents, MCP servers, gateways; the Apigee organization lives
  # in the gateway project) and from the governance project (Agent Gateway).
  usage_source_projects = toset(concat([var.governance_project_id], var.spoke_project_ids))
  usage_sink_filter     = "jsonPayload.schema=\"esmeralda.usage\""
}

# Create Central BigQuery Telemetry Dataset
resource "google_bigquery_dataset" "telemetry_logs" {
  dataset_id = "esmeralda_telemetry_logs_${var.environment}"
  project    = var.governance_project_id
  location   = "US"
}

# Deploy Central Log Sinks across all workload spoke projects
resource "google_logging_project_sink" "central_sinks" {
  for_each    = local.monitored_projects
  name        = "esmeralda-central-telemetry-sink-${var.environment}"
  project     = each.value
  destination = "bigquery.googleapis.com/projects/${var.governance_project_id}/datasets/${google_bigquery_dataset.telemetry_logs.dataset_id}"

  # Filter stdout/stderr logs from Reasoning Engines, Cloud Run microservices, Apigee partner gateway interactions, and Cloud Audit logs
  filter = "resource.type=\"aiplatform.googleapis.com/ReasoningEngine\" OR logName=~\"gen_ai\" OR logName=~\"reasoning_engine_stdout\" OR logName=~\"reasoning_engine_stderr\" OR logName=~\"esmeralda-apigee-interactions\" OR resource.type=\"cloud_run_revision\" OR logName=~\"cloudaudit.googleapis.com\""

  bigquery_options {
    use_partitioned_tables = true
  }

  exclusions {
    name        = "exclude-debug-logs"
    description = "Exclude verbose debug log entries to minimize BigQuery ingestion costs"
    # Never drop usage records: the esmeralda.usage contract, the legacy agent token events and the
    # legacy Apigee interaction records that the views still read for historical data.
    filter = "severity < INFO AND NOT jsonPayload.schema=\"esmeralda.usage\" AND NOT jsonPayload.event=\"genai_token_consumption\" AND NOT jsonPayload.event=\"apigee.gateway.interaction\""
  }

  unique_writer_identity = true
}

# Authorize each Logging sink writer identity to insert rows into BigQuery
resource "google_bigquery_dataset_iam_member" "dataset_writers" {
  for_each   = google_logging_project_sink.central_sinks
  dataset_id = google_bigquery_dataset.telemetry_logs.dataset_id
  project    = var.governance_project_id
  role       = "roles/bigquery.dataEditor"
  member     = each.value.writer_identity
}

# ------------------------------------------------------------------------------
# USAGE EVENTS SINK (esmeralda.usage contract: the FinOps source of truth)
# ------------------------------------------------------------------------------
# Every usage record, from any producer (esmeralda library on Agent Engine / Cloud Run / GKE, the
# Apigee ML-interaction template, Envoy access logs, OTel collectors from other clouds), carries
# jsonPayload.schema = "esmeralda.usage". The sink keys on that field, never on a log name, so a new
# producer needs no sink change.
#
# A BigQuery sink can't write into one named table: it writes one table per log ID, and BigQuery only
# clusters on top-level columns while every usage field sits under jsonPayload. So:
#   - the landing tables below (one per producer log ID, typed, partitioned by day) receive the records;
#   - the usage_events view (modules/4_finops_analytics) unions them into flat, typed columns.
# A producer writing under a new log ID: add its table name to var.usage_landing_tables. Until then
# the sink auto-creates that table and its rows are stored but not yet visible in usage_events. (An
# explicit list, not a `dataset.*` wildcard: a wildcard takes the schema of the newest table, so one
# auto-created table that lacks a column would break the view.)
#
# The dataset is separate from esmeralda_telemetry_logs_<env> because the central sink above already
# writes the Reasoning Engine stdout log there under the same table name.
resource "google_bigquery_dataset" "usage_events" {
  dataset_id  = "esmeralda_usage_${var.environment}"
  project     = var.governance_project_id
  location    = google_bigquery_dataset.telemetry_logs.location
  description = "esmeralda.usage records (contracts/usage-event.schema.json), one landing table per producer log ID."
}

# Landing tables, pre-created with the contract's columns so the usage_events view can be created
# before the first record arrives and the column types are fixed. Cloud Logging exports field names
# with dots as underscores (gen_ai."usage.input_tokens" -> gen_ai.usage_input_tokens) and every JSON
# number as FLOAT; the view casts. The sinks add the remaining LogEntry columns (resource, labels,
# caller.declared / dimensions keys...) on write, hence ignore_changes on the schema.
resource "google_bigquery_table" "usage_landing" {
  for_each            = toset(var.usage_landing_tables)
  dataset_id          = google_bigquery_dataset.usage_events.dataset_id
  table_id            = each.value
  project             = var.governance_project_id
  deletion_protection = false
  description         = "esmeralda.usage landing table (log ID ${each.value}); read through the usage_events view."

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  schema = jsonencode(local.usage_landing_schema)

  lifecycle {
    ignore_changes = [schema]
  }
}

resource "google_logging_project_sink" "usage_events" {
  for_each    = local.usage_source_projects
  name        = "esmeralda-usage-sink-${var.environment}"
  project     = each.value
  destination = "bigquery.googleapis.com/projects/${var.governance_project_id}/datasets/${google_bigquery_dataset.usage_events.dataset_id}"
  description = "Routes esmeralda.usage records (any producer, any log name) to the usage dataset."

  filter = local.usage_sink_filter

  # Day-partitioned tables on the LogEntry timestamp, matching the pre-created landing tables.
  bigquery_options {
    use_partitioned_tables = true
  }

  unique_writer_identity = true

  # The landing tables must exist before the first record arrives: otherwise the sink creates them
  # first with an inferred schema, and Terraform's create then fails with "already exists".
  depends_on = [google_bigquery_table.usage_landing]
}

resource "google_bigquery_dataset_iam_member" "usage_writers" {
  for_each   = google_logging_project_sink.usage_events
  dataset_id = google_bigquery_dataset.usage_events.dataset_id
  project    = var.governance_project_id
  role       = "roles/bigquery.dataEditor"
  member     = each.value.writer_identity
}

# ------------------------------------------------------------------------------
# GCS LONG-TERM TELEMETRY ARCHIVAL BUCKET (Coldline 7-Year Regulatory Audit Retention)
# ------------------------------------------------------------------------------
resource "google_storage_bucket" "telemetry_archive" {
  name                        = "esmeralda-telemetry-archive-${var.governance_project_id}"
  project                     = var.governance_project_id
  location                    = "us-central1"
  storage_class               = "COLDLINE"
  uniform_bucket_level_access = true

  retention_policy {
    is_locked        = false
    retention_period = 220898400 # 7 Years (in seconds)
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = 2555 # 7 Years (365 * 7)
    }
  }
}

# GCS Storage Object Creator IAM binding for sink writer identities
resource "google_storage_bucket_iam_member" "gcs_sink_writers" {
  for_each = google_logging_project_sink.central_sinks
  bucket   = google_storage_bucket.telemetry_archive.name
  role     = "roles/storage.objectCreator"
  member   = each.value.writer_identity
}

# ------------------------------------------------------------------------------
# DEFAULT LOGGING BUCKET OBSERVABILITY ANALYTICS & AUDIT LOGS (Required for Agent Gateway dashboards)
# ------------------------------------------------------------------------------
resource "google_logging_project_bucket_config" "default_analytics" {
  project          = var.governance_project_id
  location         = "global"
  bucket_id        = "_Default"
  enable_analytics = true
}

resource "google_logging_project_bucket_config" "spoke_default_analytics" {
  for_each         = local.monitored_projects
  project          = each.value
  location         = "global"
  bucket_id        = "_Default"
  enable_analytics = true
}

resource "google_logging_linked_dataset" "default_logs_linked_dataset" {
  link_id     = "default_logs_analytics"
  bucket      = google_logging_project_bucket_config.default_analytics.id
  parent      = "projects/${var.governance_project_id}"
  location    = "global"
  description = "Linked BigQuery dataset for _Default bucket Observability Analytics"
}

resource "google_project_iam_audit_config" "agent_gateway_observability_audit" {
  for_each = toset(["iap.googleapis.com", "networkservices.googleapis.com", "agentregistry.googleapis.com"])
  project  = var.governance_project_id
  service  = each.value

  audit_log_config {
    log_type = "ADMIN_READ"
  }
  audit_log_config {
    log_type = "DATA_READ"
  }
  audit_log_config {
    log_type = "DATA_WRITE"
  }
}

