# 1. Pub/Sub Topic for Monitoring Alerts
resource "google_pubsub_topic" "monitoring_alerts_topic" {
  project = var.governance_project_id
  name    = "esmeralda-monitoring-alerts-${var.environment}"
}

# 2. Notification Channels: Email & Pub/Sub
resource "google_monitoring_notification_channel" "email_alert" {
  count        = var.alert_email_address != "" ? 1 : 0
  project      = var.governance_project_id
  display_name = "Esmeralda SecOps Email Alert"
  type         = "email"
  labels = {
    email_address = var.alert_email_address
  }
}

resource "google_monitoring_notification_channel" "pubsub_alert" {
  project      = var.governance_project_id
  display_name = "Esmeralda PubSub Circuit Breaker Alert"
  type         = "pubsub"
  labels = {
    topic = google_pubsub_topic.monitoring_alerts_topic.id
  }
}

# 3. Log-Based Metric: Real-Time Token Consumption
resource "google_logging_metric" "realtime_token_consumption" {
  for_each    = toset(concat([var.governance_project_id], var.spoke_project_ids))
  name        = "genai/realtime_token_consumption"
  project     = each.value
  filter      = "jsonPayload.event=\"genai_token_consumption\" AND jsonPayload.tokens.total_tokens > 0"
  description = "Real-time delta metric for total token consumption per agent inference request"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "1"
    labels {
      key        = "agent_id"
      value_type = "STRING"
    }
    labels {
      key        = "user_id"
      value_type = "STRING"
    }
  }

  value_extractor = "EXTRACT(jsonPayload.tokens.total_tokens)"
  label_extractors = {
    "agent_id" = "EXTRACT(jsonPayload.agent_id)"
    "user_id"  = "EXTRACT(jsonPayload.user_id)"
  }

  bucket_options {
    exponential_buckets {
      num_finite_buckets = 64
      growth_factor      = 2
      scale              = 1
    }
  }
}

# 3b. Log-Based Metric: Cached Tokens Counter
resource "google_logging_metric" "cached_tokens_counter" {
  for_each    = toset(concat([var.governance_project_id], var.spoke_project_ids))
  name        = "genai/cached_tokens"
  project     = each.value
  filter      = "jsonPayload.event=\"genai_token_consumption\" AND jsonPayload.tokens.cached_tokens > 0"
  description = "Real-time metric for Gemini Context Caching token hits"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "1"
    labels {
      key        = "agent_id"
      value_type = "STRING"
    }
  }

  value_extractor = "EXTRACT(jsonPayload.tokens.cached_tokens)"
  label_extractors = {
    "agent_id" = "EXTRACT(jsonPayload.agent_id)"
  }

  bucket_options {
    exponential_buckets {
      num_finite_buckets = 64
      growth_factor      = 2
      scale              = 1
    }
  }
}

# 3c. Log-Based Metric: Prompt Tokens Counter
resource "google_logging_metric" "prompt_tokens_counter" {
  for_each    = toset(concat([var.governance_project_id], var.spoke_project_ids))
  name        = "genai/prompt_tokens"
  project     = each.value
  filter      = "jsonPayload.event=\"genai_token_consumption\" AND jsonPayload.tokens.prompt_tokens > 0"
  description = "Real-time metric for prompt tokens"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "1"
    labels {
      key        = "agent_id"
      value_type = "STRING"
    }
  }

  value_extractor = "EXTRACT(jsonPayload.tokens.prompt_tokens)"
  label_extractors = {
    "agent_id" = "EXTRACT(jsonPayload.agent_id)"
  }

  bucket_options {
    exponential_buckets {
      num_finite_buckets = 64
      growth_factor      = 2
      scale              = 1
    }
  }
}

# 3d. Log-Based Metric: Completion Tokens Counter
resource "google_logging_metric" "completion_tokens_counter" {
  for_each    = toset(concat([var.governance_project_id], var.spoke_project_ids))
  name        = "genai/completion_tokens"
  project     = each.value
  filter      = "jsonPayload.event=\"genai_token_consumption\" AND jsonPayload.tokens.completion_tokens > 0"
  description = "Real-time metric for response completion tokens"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "1"
    labels {
      key        = "agent_id"
      value_type = "STRING"
    }
  }

  value_extractor = "EXTRACT(jsonPayload.tokens.completion_tokens)"
  label_extractors = {
    "agent_id" = "EXTRACT(jsonPayload.agent_id)"
  }

  bucket_options {
    exponential_buckets {
      num_finite_buckets = 64
      growth_factor      = 2
      scale              = 1
    }
  }
}

# 3e. Log-Based Metric: Thoughts / Reasoning Tokens Counter
resource "google_logging_metric" "thoughts_tokens_counter" {
  for_each    = toset(concat([var.governance_project_id], var.spoke_project_ids))
  name        = "genai/thoughts_tokens"
  project     = each.value
  filter      = "jsonPayload.event=\"genai_token_consumption\" AND jsonPayload.tokens.thoughts_tokens > 0"
  description = "Real-time metric for Gemini 2.5 internal reasoning/thoughts tokens"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "1"
    labels {
      key        = "agent_id"
      value_type = "STRING"
    }
  }

  value_extractor = "EXTRACT(jsonPayload.tokens.thoughts_tokens)"
  label_extractors = {
    "agent_id" = "EXTRACT(jsonPayload.agent_id)"
  }

  bucket_options {
    exponential_buckets {
      num_finite_buckets = 64
      growth_factor      = 2
      scale              = 1
    }
  }
}

# 4. Alert Policy: Runaway Loop Single-Request Token Cap
resource "google_monitoring_alert_policy" "runaway_loop_token_cap" {
  project      = var.governance_project_id
  display_name = "[Esmeralda ${var.environment}] Runaway Agent Loop - Token Budget Exceeded"
  combiner     = "OR"

  documentation {
    content   = <<EOF
### 🚨 RUNAWAY AGENT REASONING LOOP DETECTED
**Alert Trigger**: A single inference request exceeded the configured token budget cap.

#### 🛠️ Immediate On-Call Action Plan:
1. Open Cloud Logging in `prj-esmeralda-governance` and query the flagged session ID:
   `jsonPayload.event="genai_token_consumption" AND jsonPayload.tokens.total_tokens > ${var.runaway_loop_token_threshold}`
2. Identify the `execution_path` and `user_id` breaching the threshold.
3. If an API key or service account is malfunctioning, run the Gateway revocation command or check circuit breaker status.
EOF
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "Single Request Token Budget Limit"
    condition_threshold {
      filter          = "resource.type = \"global\" AND metric.type = \"logging.googleapis.com/user/genai/realtime_token_consumption\""
      duration        = "60s"
      comparison      = "COMPARISON_GT"
      threshold_value = var.runaway_loop_token_threshold
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_PERCENTILE_99"
      }
    }
  }

  notification_channels = [
    try(google_monitoring_notification_channel.email_alert[0].name, ""),
    google_monitoring_notification_channel.pubsub_alert.name
  ]

  depends_on = [google_logging_metric.realtime_token_consumption]
}

# 5. Alert Policy: Reasoning Engine Quota & 429 Rate Limit
resource "google_monitoring_alert_policy" "reasoning_engine_quota" {
  project      = var.governance_project_id
  display_name = "[Esmeralda ${var.environment}] Reasoning Engine High Query Rate / 429 Quota"
  combiner     = "OR"

  documentation {
    content   = <<EOF
### 🚨 REASONING ENGINE QUOTA / 429 RATE LIMIT EXHAUSTION DETECTED
**Alert Trigger**: Agent query request rate approaching regional limit (90 reqs/min) or HTTP 429 quota errors occurring.

#### 🛠️ Immediate On-Call Action Plan:
1. Check active Reasoning Engine concurrency in `prj-esmeralda-root-agent` or `prj-esmeralda-a2a`:
   `metric.type="aiplatform.googleapis.com/reasoning_engine/request_count"`
2. Request a regional quota increase via GCP Console:
   `https://console.cloud.google.com/iam-admin/quotas?project=${var.governance_project_id}`
3. Enable client-side exponential backoff jitter in A2A callers.
EOF
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "Reasoning Engine Query Requests"
    condition_threshold {
      filter          = "resource.type = \"aiplatform.googleapis.com/ReasoningEngine\" AND metric.type = \"aiplatform.googleapis.com/reasoning_engine/request_count\""
      duration        = "180s"
      comparison      = "COMPARISON_GT"
      threshold_value = 80
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_RATE"
      }
    }
  }

  notification_channels = [
    try(google_monitoring_notification_channel.email_alert[0].name, ""),
    google_monitoring_notification_channel.pubsub_alert.name
  ]
}

# 6. Log-Based Metric: MCP Tool Execution Frequency & Latency
resource "google_logging_metric" "mcp_tool_execution_count" {
  for_each    = toset(concat([var.governance_project_id], var.spoke_project_ids))
  name        = "genai/mcp_tool_execution_count"
  project     = each.value
  filter      = "jsonPayload.event=\"mcp_tool_execution\""
  description = "Real-time delta metric tracking execution frequency per MCP tool microservice"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key        = "tool_name"
      value_type = "STRING"
    }
    labels {
      key        = "mcp_service"
      value_type = "STRING"
    }
    labels {
      key        = "status"
      value_type = "STRING"
    }
  }

  label_extractors = {
    "tool_name"   = "EXTRACT(jsonPayload.tool_name)"
    "mcp_service" = "EXTRACT(jsonPayload.mcp_service)"
    "status"      = "EXTRACT(jsonPayload.status)"
  }
}

# 7. Alert Policy: High P95 Latency (>10,000 ms) with Embedded SRE Runbook
resource "google_monitoring_alert_policy" "p95_latency" {
  project      = var.governance_project_id
  display_name = "[Esmeralda ${var.environment}] High P95 Latency (>10s)"
  combiner     = "OR"

  documentation {
    content   = <<EOF
### 🚨 HIGH P95 LATENCY DEGRADATION DETECTED (>10,000 ms)
**Alert Trigger**: P95 end-to-end latency across Cloud Run MCPs or Reasoning Engines exceeded 10 seconds.

#### 🛠️ Immediate On-Call Action Plan:
1. Check Cloud Run MCP cold starts in `prj-esmeralda-mcps`:
   `metric.type="run.googleapis.com/container/startup_latencies"`
2. Inspect Cloud Trace explorer for bottleneck spans:
   `https://console.cloud.google.com/traces/explorer?project=${var.governance_project_id}`
3. Verify if downstream legacy services (e.g. `legacy-dms`) are timing out.
EOF
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "Cloud Run & Vertex AI Latency Threshold"
    condition_threshold {
      filter          = "resource.type = \"cloud_run_revision\" AND metric.type = \"run.googleapis.com/request_latencies\""
      duration        = "300s"
      comparison      = "COMPARISON_GT"
      threshold_value = 10000
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_PERCENTILE_95"
      }
    }
  }

  notification_channels = [try(google_monitoring_notification_channel.email_alert[0].name, "")]
}

# 8. Synthetic Uptime Probe (Active Ingress Health Check)
resource "google_monitoring_uptime_check_config" "gateway_health" {
  project      = var.governance_project_id
  display_name = "[Esmeralda ${var.environment}] Ingress Gateway Synthetic Uptime Probe"
  timeout      = "10s"
  period       = "60s"

  http_check {
    path         = "/healthz"
    port         = "443"
    use_ssl      = true
    validate_ssl = true
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.governance_project_id
      host       = "gateway.esmeralda.internal"
    }
  }
}

# 9. Alert Policy: Token Rate-of-Change Anomaly Alert (>300% Spike in 5 Mins)
resource "google_monitoring_alert_policy" "token_anomaly_spike" {
  project      = var.governance_project_id
  display_name = "[Esmeralda ${var.environment}] Token Rate-of-Change Anomaly (>300% Spike)"
  combiner     = "OR"

  documentation {
    content   = <<EOF
### 🚨 REAL-TIME TOKEN RATE ANOMALY SPIKE DETECTED
**Alert Trigger**: 5-minute token consumption rate increased by >300% compared to baseline.

#### 🛠️ Immediate On-Call Action Plan:
1. Inspect active user sessions in Cloud Logging:
   `resource.type="global" AND metric.type="logging.googleapis.com/user/genai/realtime_token_consumption"`
2. Check if a high-volume batch job or looping agent identity was launched.
3. Apply gateway rate limit or revoke compromised credentials if unauthorized.
EOF
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "Token Rate of Change 300% Spike"
    condition_threshold {
      filter          = "resource.type = \"global\" AND metric.type = \"logging.googleapis.com/user/genai/realtime_token_consumption\""
      duration        = "300s"
      comparison      = "COMPARISON_GT"
      threshold_value = 500000
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_PERCENTILE_99"
      }
    }
  }

  notification_channels = [
    try(google_monitoring_notification_channel.email_alert[0].name, ""),
    google_monitoring_notification_channel.pubsub_alert.name
  ]
}

# 10. Log-Based Metric: IAM Privilege Changes
resource "google_logging_metric" "iam_privilege_changes" {
  name        = "security/iam_privilege_changes"
  project     = var.governance_project_id
  filter      = "logName:\"cloudaudit.googleapis.com%2Factivity\" AND protoPayload.methodName:\"SetIamPolicy\""
  description = "Real-time count of IAM role bindings and privilege escalations"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

# 11. Log-Based Metric: Secret Manager Access Operations
resource "google_logging_metric" "secret_access_operations" {
  name        = "security/secret_access_operations"
  project     = var.governance_project_id
  filter      = "logName:\"cloudaudit.googleapis.com%2Fdata_access\" AND protoPayload.methodName:\"AccessSecretVersion\""
  description = "Real-time count of Secret Manager version accesses"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

# 12. Log-Based Metric: Reasoning Engine Deployments
resource "google_logging_metric" "reasoning_engine_deployments" {
  name        = "security/reasoning_engine_deployments"
  project     = var.governance_project_id
  filter      = "logName:\"cloudaudit.googleapis.com%2Factivity\" AND protoPayload.methodName:\"ReasoningEngine\""
  description = "Real-time count of Reasoning Engine deployments and modifications"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

# 13. Alert Policy: Real-Time Security & Privilege Escalation Alert
resource "google_monitoring_alert_policy" "privilege_escalation_alert" {
  project      = var.governance_project_id
  display_name = "[Esmeralda ${var.environment}] SecOps - Privilege Escalation & IAM Modification Alert"
  combiner     = "OR"

  documentation {
    content   = <<EOF
### 🚨 PRIVILEGE ESCALATION / IAM POLICY MODIFICATION DETECTED
**Alert Trigger**: An identity modified GCP project IAM policy bindings or granted new administrative roles.

#### 🛠️ Immediate SecOps Action Plan:
1. Open Cloud Audit Logs in `prj-esmeralda-governance`:
   `logName:"cloudaudit.googleapis.com/activity" AND protoPayload.methodName:"SetIamPolicy"`
2. Verify if the principal email and IP address are authorized by SecOps change ticket.
3. If unauthorized, immediately revoke the role binding in GCP Console IAM settings.
EOF
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "IAM Privilege Policy Changes"
    condition_threshold {
      filter          = "resource.type = \"global\" AND metric.type = \"logging.googleapis.com/user/security/iam_privilege_changes\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_DELTA"
      }
    }
  }

  notification_channels = [
    try(google_monitoring_notification_channel.email_alert[0].name, ""),
    google_monitoring_notification_channel.pubsub_alert.name
  ]

  depends_on = [google_logging_metric.iam_privilege_changes]
}

# ------------------------------------------------------------------------------
# TERRAFORM MOVED BLOCKS (Refactoring metrics into for_each maps)
# ------------------------------------------------------------------------------

moved {
  from = google_logging_metric.realtime_token_consumption
  to   = google_logging_metric.realtime_token_consumption["esmeralda-governance-dev"]
}

moved {
  from = google_logging_metric.cached_tokens_counter
  to   = google_logging_metric.cached_tokens_counter["esmeralda-governance-dev"]
}

moved {
  from = google_logging_metric.prompt_tokens_counter
  to   = google_logging_metric.prompt_tokens_counter["esmeralda-governance-dev"]
}

moved {
  from = google_logging_metric.completion_tokens_counter
  to   = google_logging_metric.completion_tokens_counter["esmeralda-governance-dev"]
}

moved {
  from = google_logging_metric.thoughts_tokens_counter
  to   = google_logging_metric.thoughts_tokens_counter["esmeralda-governance-dev"]
}

moved {
  from = google_logging_metric.mcp_tool_execution_count
  to   = google_logging_metric.mcp_tool_execution_count["esmeralda-governance-dev"]
}
