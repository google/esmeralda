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
  name        = "genai/realtime_token_consumption"
  project     = var.governance_project_id
  filter      = "jsonPayload.event=\"genai_token_consumption\" AND jsonPayload.tokens.total_tokens > 0"
  description = "Real-time delta metric for total token consumption per agent inference request"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "1"
    labels {
      key         = "agent_id"
      value_type  = "STRING"
    }
    labels {
      key         = "user_id"
      value_type  = "STRING"
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
  name        = "genai/cached_tokens"
  project     = var.governance_project_id
  filter      = "jsonPayload.event=\"genai_token_consumption\" AND jsonPayload.tokens.cached_tokens > 0"
  description = "Real-time metric for Gemini Context Caching token hits"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "1"
    labels {
      key         = "agent_id"
      value_type  = "STRING"
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
  name        = "genai/prompt_tokens"
  project     = var.governance_project_id
  filter      = "jsonPayload.event=\"genai_token_consumption\" AND jsonPayload.tokens.prompt_tokens > 0"
  description = "Real-time metric for prompt tokens"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "1"
    labels {
      key         = "agent_id"
      value_type  = "STRING"
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
  name        = "genai/completion_tokens"
  project     = var.governance_project_id
  filter      = "jsonPayload.event=\"genai_token_consumption\" AND jsonPayload.tokens.completion_tokens > 0"
  description = "Real-time metric for response completion tokens"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "1"
    labels {
      key         = "agent_id"
      value_type  = "STRING"
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
  name        = "genai/thoughts_tokens"
  project     = var.governance_project_id
  filter      = "jsonPayload.event=\"genai_token_consumption\" AND jsonPayload.tokens.thoughts_tokens > 0"
  description = "Real-time metric for Gemini 2.5 internal reasoning/thoughts tokens"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "1"
    labels {
      key         = "agent_id"
      value_type  = "STRING"
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
### 🚨 REASONING ENGINE QUOTA EXHAUSTION DETECTED
**Alert Trigger**: Agent query request rate approaching regional limit or HTTP 429 quota errors occurring.
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
