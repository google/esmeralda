# ==============================================================================
# ESMERALDA STAGE 5 GOVERNANCE & OBSERVABILITY ORCHESTRATOR MODULE
# ==============================================================================

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

locals {
  monitored_projects = toset(var.spoke_project_ids)
}

# 1. Attach Workload Spoke Projects to Central Governance Metrics Scope
resource "google_monitoring_monitored_project" "spoke_projects" {
  for_each      = local.monitored_projects
  metrics_scope = "locations/global/metricsScopes/${var.governance_project_id}"
  name          = each.value
}

# 2. Submodule 1: Telemetry Sinks (Log Sinks & Dataset Permissions)
module "telemetry_sinks" {
  source                = "./modules/1_telemetry_sinks"
  environment           = var.environment
  governance_project_id = var.governance_project_id
  spoke_project_ids     = var.spoke_project_ids
}

# 3. Submodule 2: DLP Inspection Templates
module "dlp_inspection" {
  source                = "./modules/2_dlp_inspection"
  governance_project_id = var.governance_project_id
}

# 4. Submodule 3: Alert Policies & Pub/Sub Circuit Breaker
module "alert_policies" {
  source                       = "./modules/3_alert_policies"
  environment                  = var.environment
  governance_project_id        = var.governance_project_id
  alert_email_address          = var.alert_email_address
  runaway_loop_token_threshold = var.runaway_loop_token_threshold
}

# 5. Submodule 4: FinOps Analytics (Token Events Table & Chargeback View)
module "finops_analytics" {
  source                = "./modules/4_finops_analytics"
  environment           = var.environment
  governance_project_id = var.governance_project_id
}

# ------------------------------------------------------------------------------
# TERRAFORM MOVED BLOCKS (Refactoring root resources into submodules)
# ------------------------------------------------------------------------------

moved {
  from = google_logging_project_sink.central_sinks
  to   = module.telemetry_sinks.google_logging_project_sink.central_sinks
}

moved {
  from = google_bigquery_dataset_iam_member.dataset_writers
  to   = module.telemetry_sinks.google_bigquery_dataset_iam_member.dataset_writers
}

moved {
  from = google_data_loss_prevention_inspect_template.pii_redaction
  to   = module.dlp_inspection.google_data_loss_prevention_inspect_template.pii_redaction
}

moved {
  from = google_pubsub_topic.monitoring_alerts_topic
  to   = module.alert_policies.google_pubsub_topic.monitoring_alerts_topic
}

moved {
  from = google_monitoring_notification_channel.email_alert
  to   = module.alert_policies.google_monitoring_notification_channel.email_alert
}

moved {
  from = google_monitoring_notification_channel.pubsub_alert
  to   = module.alert_policies.google_monitoring_notification_channel.pubsub_alert
}

moved {
  from = google_logging_metric.realtime_token_consumption
  to   = module.alert_policies.google_logging_metric.realtime_token_consumption
}

moved {
  from = google_monitoring_alert_policy.runaway_loop_token_cap
  to   = module.alert_policies.google_monitoring_alert_policy.runaway_loop_token_cap
}

moved {
  from = google_monitoring_alert_policy.reasoning_engine_quota
  to   = module.alert_policies.google_monitoring_alert_policy.reasoning_engine_quota
}

moved {
  from = google_bigquery_table.token_events
  to   = module.finops_analytics.google_bigquery_table.token_events
}

moved {
  from = google_bigquery_table.vw_monthly_chargeback
  to   = module.finops_analytics.google_bigquery_table.vw_monthly_chargeback
}
