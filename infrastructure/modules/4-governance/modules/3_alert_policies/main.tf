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
  display_name = "Esmeralda PubSub Remediation Alert"
  type         = "pubsub"
  labels = {
    topic = google_pubsub_topic.monitoring_alerts_topic.id
  }
}

# 3. Log-Based Metrics: esmeralda.usage records (the FinOps source of truth)
# Every metric keys on jsonPayload.schema = "esmeralda.usage" (contracts/usage-event.schema.json), never on
# a log name or a text pattern, so any producer (esmeralda library, Apigee, Envoy, OTel collectors) feeds
# them. Log-based metrics are per project: they are created in the governance project and every spoke,
# and the governance metrics scope aggregates them. Field names with dots are quoted, as in the contract
# (gen_ai."usage.input_tokens"). Labels:
#   caller_team / verified   who pays (verified = "true" only when the identity came from authentication)
#   workload / workload_team the agent, MCP server or gateway target that served the call
#   model / source / event   gen_ai."request.model", gateway | workload, llm.call | gateway.request | ...
# Token metrics are distributions of the per-record count; PromQL reads their totals as
# logging_googleapis_com:user_usage_<metric>_sum.
locals {
  usage_metric_projects = toset(concat([var.governance_project_id], var.spoke_project_ids))
  usage_record_filter   = "jsonPayload.schema=\"esmeralda.usage\""

  # A log-based metric is written against the monitored resource of each log entry (Reasoning Engine,
  # Cloud Run revision, GKE container, global...), and Cloud Monitoring PromQL requires every selector to
  # name one exact monitored_resource. So each usage term is the union (`or`) of one selector per
  # producer resource type: usage_promql["<function>|<metric suffix>|<range>"]. The _json variant is
  # for PromQL embedded in dashboard JSON (quotes escaped).
  usage_promql_terms = [
    "increase|input_tokens_sum|1m", "increase|output_tokens_sum|1m", "increase|cached_tokens_sum|1m",
    "increase|reasoning_tokens_sum|1m", "increase|tool_calls|1m",
    "increase|input_tokens_sum|5m", "increase|output_tokens_sum|5m", "increase|unattributed_guarded_records|5m",
    "rate|input_tokens_bucket|5m",
  ]
  usage_promql = {
    for term in local.usage_promql_terms : term => format("(%s)", join(" or ", [
      for t in var.usage_metric_resource_types :
      format("%s(logging_googleapis_com:user_usage_%s{monitored_resource=\"%s\"}[%s])", split("|", term)[0], split("|", term)[1], t, split("|", term)[2])
    ]))
  }
  usage_promql_json = { for term, q in local.usage_promql : term => replace(q, "\"", "\\\"") }

  usage_token_metrics = {
    input_tokens = {
      field       = "jsonPayload.gen_ai.\"usage.input_tokens\""
      description = "Input tokens per esmeralda.usage record (includes cached tokens), by caller team, workload and model"
    }
    output_tokens = {
      field       = "jsonPayload.gen_ai.\"usage.output_tokens\""
      description = "Output tokens per esmeralda.usage record (includes reasoning tokens), by caller team, workload and model"
    }
    cached_tokens = {
      field       = "jsonPayload.gen_ai.\"usage.cached_tokens\""
      description = "Context-cache hits (subset of input tokens) per esmeralda.usage record"
    }
    reasoning_tokens = {
      field       = "jsonPayload.gen_ai.\"usage.reasoning_tokens\""
      description = "Reasoning / thinking tokens (subset of output tokens) per esmeralda.usage record"
    }
  }

  usage_token_metric_instances = {
    for pair in setproduct(keys(local.usage_token_metrics), local.usage_metric_projects) :
    "${pair[0]}/${pair[1]}" => { metric = pair[0], project = pair[1] }
  }

  usage_labels = {
    caller_team   = "EXTRACT(jsonPayload.caller.team)"
    verified      = "EXTRACT(jsonPayload.caller.verified)"
    workload      = "EXTRACT(jsonPayload.workload.name)"
    workload_team = "EXTRACT(jsonPayload.workload.team)"
    model         = "EXTRACT(jsonPayload.gen_ai.\"request.model\")"
    source        = "EXTRACT(jsonPayload.source)"
    event         = "EXTRACT(jsonPayload.event)"
  }
}

# 3a. Token metrics: usage/input_tokens, usage/output_tokens, usage/cached_tokens, usage/reasoning_tokens
resource "google_logging_metric" "usage_tokens" {
  for_each    = local.usage_token_metric_instances
  name        = "usage/${each.value.metric}"
  project     = each.value.project
  filter      = "${local.usage_record_filter} AND ${local.usage_token_metrics[each.value.metric].field}>0"
  description = local.usage_token_metrics[each.value.metric].description

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "1"
    dynamic "labels" {
      for_each = keys(local.usage_labels)
      content {
        key        = labels.value
        value_type = "STRING"
      }
    }
  }

  value_extractor  = "EXTRACT(${local.usage_token_metrics[each.value.metric].field})"
  label_extractors = local.usage_labels

  bucket_options {
    exponential_buckets {
      num_finite_buckets = 64
      growth_factor      = 2
      scale              = 1
    }
  }
}

# 3b. Tool calls by tool, MCP server and status (usage/tool_calls)
resource "google_logging_metric" "usage_tool_calls" {
  for_each    = local.usage_metric_projects
  name        = "usage/tool_calls"
  project     = each.value
  filter      = "${local.usage_record_filter} AND jsonPayload.event=\"tool.call\""
  description = "Tool calls (esmeralda.usage tool.call records) by tool, MCP server, status, workload and caller team"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key        = "tool"
      value_type = "STRING"
    }
    labels {
      key        = "server"
      value_type = "STRING"
    }
    labels {
      key        = "status"
      value_type = "STRING"
    }
    labels {
      key        = "workload"
      value_type = "STRING"
    }
    labels {
      key        = "caller_team"
      value_type = "STRING"
    }
  }

  label_extractors = {
    "tool"        = "EXTRACT(jsonPayload.tool.name)"
    "server"      = "EXTRACT(jsonPayload.tool.server)"
    "status"      = "EXTRACT(jsonPayload.tool.status)"
    "workload"    = "EXTRACT(jsonPayload.workload.name)"
    "caller_team" = "EXTRACT(jsonPayload.caller.team)"
  }
}

# 3c. Unattributed usage (usage/unattributed_records): records without a verified caller. "Unattributed is
# an alarm": the catalog fails closed, so the expected count is 0 (alert 3d). `NOT ...=true` also matches
# records that omit caller or caller.verified.
# It counts every unverified record (Agent Runtime included, for reference); the alert (3d) and the
# dashboard tile read the guarded variant below.
resource "google_logging_metric" "usage_unattributed_records" {
  for_each    = local.usage_metric_projects
  name        = "usage/unattributed_records"
  project     = each.value
  filter      = "${local.usage_record_filter} AND NOT jsonPayload.caller.verified=true"
  description = "esmeralda.usage records whose caller is missing or not verified (expected: 0)"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key        = "workload"
      value_type = "STRING"
    }
    labels {
      key        = "source"
      value_type = "STRING"
    }
    labels {
      key        = "event"
      value_type = "STRING"
    }
    labels {
      key        = "caller_team"
      value_type = "STRING"
    }
  }

  label_extractors = {
    "workload"    = "EXTRACT(jsonPayload.workload.name)"
    "source"      = "EXTRACT(jsonPayload.source)"
    "event"       = "EXTRACT(jsonPayload.event)"
    "caller_team" = "EXTRACT(jsonPayload.caller.team)"
  }
}

# 3c'. Unattributed usage from guarded workloads (usage/unattributed_guarded_records): the same records
# minus Agent Runtime. Agent Runtime never hands the caller's credential to the agent container, so
# agents there always write caller.verified = false and their hops are attributed from the Agent
# Gateway record instead (the iap-egress unit counts gateway decisions for principals without a
# principal_teams row: attribution/gateway_unattributed_decisions, expected 0 as well). A separate
# metric (not a label on 3c) so the deployed metric is never replaced. Records without
# workload.runtime count (they are not Agent Runtime).
resource "google_logging_metric" "usage_unattributed_guarded_records" {
  for_each    = local.usage_metric_projects
  name        = "usage/unattributed_guarded_records"
  project     = each.value
  filter      = "${local.usage_record_filter} AND NOT jsonPayload.caller.verified=true AND NOT jsonPayload.workload.runtime=\"agent_engine\""
  description = "esmeralda.usage records from guarded workloads (not Agent Runtime) whose caller is missing or not verified (expected: 0)"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key        = "workload"
      value_type = "STRING"
    }
    labels {
      key        = "source"
      value_type = "STRING"
    }
    labels {
      key        = "event"
      value_type = "STRING"
    }
    labels {
      key        = "caller_team"
      value_type = "STRING"
    }
  }

  label_extractors = {
    "workload"    = "EXTRACT(jsonPayload.workload.name)"
    "source"      = "EXTRACT(jsonPayload.source)"
    "event"       = "EXTRACT(jsonPayload.event)"
    "caller_team" = "EXTRACT(jsonPayload.caller.team)"
  }
}

# 3d. Alert Policy: Unattributed / Unverified Usage (> 0)
resource "google_monitoring_alert_policy" "unattributed_usage" {
  count        = var.alert_on_unattributed_usage ? 1 : 0
  project      = var.governance_project_id
  display_name = "[Esmeralda ${var.environment}] Unattributed Usage - Records Without a Verified Caller"
  combiner     = "OR"

  documentation {
    content   = <<EOF
### 🚨 USAGE WITHOUT A VERIFIED CALLER
**Alert Trigger**: At least one `esmeralda.usage` record from a guarded workload (Cloud Run, GKE, other clouds; not Agent Runtime) in the last 5 minutes had no verified caller (`caller.verified != true`). Chargeback bills only verified teams, so this usage lands in `billed_team = 'unattributed'` (or the workload floor). Agent Runtime hops are covered by the gateway count (`attribution/gateway_unattributed_decisions`).

#### 🛠️ Immediate On-Call Action Plan:
1. Find the records in Cloud Logging (the `workload` and `source` labels of the alert narrow it down):
   `jsonPayload.schema="esmeralda.usage" AND NOT jsonPayload.caller.verified=true AND NOT jsonPayload.workload.runtime="agent_engine"`
2. On an MCP server, its caller guard logged why: `jsonPayload.event="esmeralda.caller_decision"` (reason `unmapped` or `unverified`, which only pass under `unmapped_caller: flag`).
3. In BigQuery: `SELECT * FROM esmeralda_telemetry_logs_${var.environment}.vw_usage_normalized WHERE attribution IN ('declared', 'unattributed') ORDER BY timestamp DESC`
4. `declared` = the caller sent a team that authentication didn't confirm; `unattributed` = no caller identity at all. Register the caller in the catalog (its principal lands in `principal_teams`), or fix the producer / gateway that dropped the verified identity.
EOF
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "Unattributed esmeralda.usage records"
    # PromQL: one query across every producer's monitored resource type (see runaway_loop_token_cap),
    # guarded workloads only (Agent Runtime hops are counted at the gateway).
    condition_prometheus_query_language {
      query               = "sum by (workload, source) (${local.usage_promql["increase|unattributed_guarded_records|5m"]}) > 0"
      duration            = "0s"
      evaluation_interval = "60s"
    }
  }

  notification_channels = [
    try(google_monitoring_notification_channel.email_alert[0].name, ""),
    google_monitoring_notification_channel.pubsub_alert.name
  ]

  depends_on = [google_logging_metric.usage_unattributed_guarded_records]
}

# 4. Alert Policy: Runaway Loop Single-Request Token Cap
# A runaway reasoning loop re-sends its growing context on every model call, so the per-call input tokens
# of usage/input_tokens climb past the budget.
resource "google_monitoring_alert_policy" "runaway_loop_token_cap" {
  project      = var.governance_project_id
  display_name = "[Esmeralda ${var.environment}] Runaway Agent Loop - Token Budget Exceeded"
  combiner     = "OR"

  documentation {
    content   = <<EOF
### 🚨 RUNAWAY AGENT REASONING LOOP DETECTED
**Alert Trigger**: A single model call or gateway request exceeded the configured token budget cap (${var.runaway_loop_token_threshold} input tokens).

#### 🛠️ Immediate On-Call Action Plan:
1. Open Cloud Logging in `prj-esmeralda-governance` and query the flagged records:
   `jsonPayload.schema="esmeralda.usage" AND jsonPayload.gen_ai."usage.input_tokens" > ${var.runaway_loop_token_threshold}`
2. Identify the `workload.name`, `trace_id` and `caller.principal` breaching the threshold (`vw_request_level_telemetry` has the whole request).
3. If an API key or service account is malfunctioning, run the Gateway revocation command.
EOF
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "Single Request Token Budget Limit"
    # PromQL: the usage metrics are written against every producer's monitored resource (Reasoning
    # Engine, Cloud Run revision, global...), and a metric-filter condition must pin one resource type.
    condition_prometheus_query_language {
      query               = "histogram_quantile(0.99, sum by (le, workload) (${local.usage_promql["rate|input_tokens_bucket|5m"]})) > ${var.runaway_loop_token_threshold}"
      duration            = "60s"
      evaluation_interval = "60s"
    }
  }

  notification_channels = [
    try(google_monitoring_notification_channel.email_alert[0].name, ""),
    google_monitoring_notification_channel.pubsub_alert.name
  ]

  depends_on = [google_logging_metric.usage_tokens]
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
1. Check active Reasoning Engine concurrency in `esm-dev-cx-agents` or `esm-dev-ai-coe-agents`:
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
      # Not the Envoy ingress gateway (when enabled): its latencies include SSE streams that stay open for minutes.
      filter          = "resource.type = \"cloud_run_revision\" AND metric.type = \"run.googleapis.com/request_latencies\"${local.exclude_gateway_service}"
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

# 8. Entry point health: no synthetic uptime check. A public uptime check can't reach the internal
# entry point (a private one, with VPC checkers and an OIDC token, could: a follow-up). The ingress
# dashboard charts the entry point's request rate by response code class (internal ALB metrics), and
# `make test-gateway` probes every catalog host from inside the VPC.

# 8a. Alert Policy: Ingress Gateway 5xx Ratio (Envoy gateway only; skipped when gateway_service_name is empty)
# Only when the Envoy adapter is enabled (gateways.enabled): the built-in direct backend has no proxy
# service to watch (see the entry point tile on the ingress dashboard). This alert watches what callers see: the share of 5xx among the requests Cloud Run served
# for the gateway service (envoy-gateway-<env>, modules/5-workloads/gateways/envoy) in the last 5 minutes.
# It is silent without traffic, and blind to failures before Cloud Run (LB, certificate, DNS).
locals {
  gateway_alerts          = var.gateway_service_name != ""
  exclude_gateway_service = local.gateway_alerts ? " AND resource.labels.service_name != \"${var.gateway_service_name}\"" : ""
  gateway_requests = join(" AND ", [
    "resource.type = \"cloud_run_revision\"",
    "resource.labels.service_name = \"${var.gateway_service_name}\"",
    "metric.type = \"run.googleapis.com/request_count\"",
  ])
}

resource "google_monitoring_alert_policy" "gateway_5xx_ratio" {
  count        = local.gateway_alerts ? 1 : 0
  project      = var.governance_project_id
  display_name = "[Esmeralda ${var.environment}] Ingress Gateway 5xx Ratio (>${var.gateway_error_rate_threshold_pct}%)"
  combiner     = "OR"

  documentation {
    content   = <<EOF
### 🚨 INGRESS GATEWAY 5XX RATIO ABOVE ${var.gateway_error_rate_threshold_pct}%
**Alert Trigger**: More than ${var.gateway_error_rate_threshold_pct}% of the requests to `${var.gateway_service_name}` (`*.esmeralda.internal`) returned 5xx over 5 minutes.

#### 🛠️ Immediate On-Call Action Plan:
1. Find the failing routes in the Envoy access logs (gateway project):
   `resource.type="cloud_run_revision" AND resource.labels.service_name="${var.gateway_service_name}" AND jsonPayload.status>=500`
   `upstream_cluster` names the target (MCP server or Reasoning Engine).
2. Read `response_flags`: `UF`/`URX`/`UH` = Envoy could not connect (upstream down, DNS, upstream TLS verification);
   `UT` = upstream timeout (120 s per route); `UC` = upstream closed the connection; no flag = the upstream itself
   returned the 5xx (check that MCP server's or Reasoning Engine's logs).
3. 5xx in the Cloud Run request logs without a matching Envoy log line: Cloud Run could not serve the request
   (crash loop, failed revision). Check the revision's logs and the latest gateway deploy.
EOF
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "${var.gateway_service_name}: 5xx / all requests"
    condition_threshold {
      filter             = "${local.gateway_requests} AND metric.labels.response_code_class = \"5xx\""
      denominator_filter = local.gateway_requests
      # A 5-minute window, so one burst of errors stays visible long enough for the 60 s duration: gateway
      # traffic is sparse, and "5 bad 1-minute windows in a row" would rarely happen even during an outage.
      duration                = "60s"
      comparison              = "COMPARISON_GT"
      threshold_value         = var.gateway_error_rate_threshold_pct / 100
      evaluation_missing_data = "EVALUATION_MISSING_DATA_INACTIVE"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }
      denominator_aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }

  notification_channels = [try(google_monitoring_notification_channel.email_alert[0].name, "")]
}

# 8b. Log-Based Metric + Alert Policy: Ingress Gateway Token Fetch Failures (Envoy gateway only)
# When Envoy can't fetch a token from the metadata server it fails open: the request goes on with the
# caller's own Authorization. MCP calls may still succeed (as the caller) and Vertex AI answers 401, a
# 4xx the alert above never counts. Envoy logs every failed fetch ("Failed to fetch token").
resource "google_logging_metric" "gateway_token_fetch_failures" {
  for_each    = local.gateway_alerts ? toset(concat([var.governance_project_id], var.spoke_project_ids)) : toset([])
  name        = "gateway/token_fetch_failures"
  project     = each.value
  filter      = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${var.gateway_service_name}\" AND textPayload:\"Failed to fetch token\""
  description = "Token fetches that failed in the ingress gateway (Envoy forwarded the caller's token instead)"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_monitoring_alert_policy" "gateway_token_fetch_failures" {
  count        = local.gateway_alerts ? 1 : 0
  project      = var.governance_project_id
  display_name = "[Esmeralda ${var.environment}] Ingress Gateway Token Fetch Failures"
  combiner     = "OR"

  documentation {
    content   = <<EOF
### 🚨 INGRESS GATEWAY COULD NOT FETCH GOOGLE TOKENS
**Alert Trigger**: `${var.gateway_service_name}` failed to fetch a token from the metadata server in the last 5 minutes.

The gateway fails open: those requests went upstream with the caller's own `Authorization`. MCP calls may have succeeded as the caller; Vertex AI calls failed with 401.

#### 🛠️ Immediate On-Call Action Plan:
1. Read the failures in the gateway project's logs:
   `resource.type="cloud_run_revision" AND resource.labels.service_name="${var.gateway_service_name}" AND textPayload:"Failed to fetch token"`
2. Check that the gateway's service account (`sa-esmeralda-envoy-${var.environment}`) still exists and is the service's identity.
3. Check the Google Cloud status page for a Compute Engine metadata server incident.
EOF
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "${var.gateway_service_name}: failed token fetches"
    condition_threshold {
      filter                  = "resource.type = \"cloud_run_revision\" AND metric.type = \"logging.googleapis.com/user/gateway/token_fetch_failures\""
      duration                = "60s"
      comparison              = "COMPARISON_GT"
      threshold_value         = 0
      evaluation_missing_data = "EVALUATION_MISSING_DATA_INACTIVE"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }

  notification_channels = [try(google_monitoring_notification_channel.email_alert[0].name, "")]

  depends_on = [google_logging_metric.gateway_token_fetch_failures]
}

# 9. Alert Policy: Token Rate-of-Change Anomaly Alert (>300% Spike in 5 Mins)
# Total tokens (input + output, all producers) over 5 minutes, from the usage/* distributions' sums.
resource "google_monitoring_alert_policy" "token_anomaly_spike" {
  project      = var.governance_project_id
  display_name = "[Esmeralda ${var.environment}] Token Rate-of-Change Anomaly (>300% Spike)"
  combiner     = "OR"

  documentation {
    content   = <<EOF
### 🚨 REAL-TIME TOKEN RATE ANOMALY SPIKE DETECTED
**Alert Trigger**: Tokens consumed in 5 minutes (input + output, all esmeralda.usage producers) exceeded ${local.token_anomaly_threshold}.

#### 🛠️ Immediate On-Call Action Plan:
1. Find who is consuming in Metrics Explorer (PromQL):
   `sum by (workload, caller_team) (increase(logging_googleapis_com:user_usage_input_tokens_sum{monitored_resource="aiplatform.googleapis.com/ReasoningEngine"}[5m]))` (repeat with `cloud_run_revision` for Cloud Run producers)
2. Check if a high-volume batch job or looping agent identity was launched (`vw_request_level_telemetry`, newest first).
3. Apply gateway rate limit or revoke compromised credentials if unauthorized.
EOF
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "Token Rate of Change 300% Spike"
    condition_prometheus_query_language {
      query               = "(sum(${local.usage_promql["increase|input_tokens_sum|5m"]}) or vector(0)) + (sum(${local.usage_promql["increase|output_tokens_sum|5m"]}) or vector(0)) > ${local.token_anomaly_threshold}"
      duration            = "300s"
      evaluation_interval = "60s"
    }
  }

  notification_channels = [
    try(google_monitoring_notification_channel.email_alert[0].name, ""),
    google_monitoring_notification_channel.pubsub_alert.name
  ]

  depends_on = [google_logging_metric.usage_tokens]
}

locals {
  token_anomaly_threshold = 500000
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
