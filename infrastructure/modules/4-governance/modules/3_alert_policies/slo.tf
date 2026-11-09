# ------------------------------------------------------------------------------
# SERVICE LEVEL OBJECTIVES (SLOs) & ERROR BUDGET GOVERNANCE
# ------------------------------------------------------------------------------
# Custom services with explicit request-based SLIs, evaluated over the governance metrics scope (the
# workloads run in the spoke projects). The old CLOUD_RUN basic service pointed at
# cx-mortgage-orchestrator, which runs on Agent Engine (not Cloud Run), so its SLOs never had data: the
# agents now get their own SLOs on the Agent Engine platform metrics below.

locals {
  # Catalog name -> Cloud Run service name (modules/5-workloads/services: <name>-<env>).
  mcp_slo_services = { for name in var.slo_mcp_services : name => "${name}-${var.environment}" }

  mcp_requests = {
    for name, service in local.mcp_slo_services : name => join(" AND ", [
      "resource.type = \"cloud_run_revision\"",
      "resource.labels.service_name = \"${service}\"",
      "metric.type = \"run.googleapis.com/request_count\"",
    ])
  }

  agent_requests = join(" AND ", [
    "resource.type = \"aiplatform.googleapis.com/ReasoningEngine\"",
    "metric.type = \"aiplatform.googleapis.com/reasoning_engine/request_count\"",
  ])
}

# 1. MCP servers on Cloud Run: availability (non-5xx share) and latency, one service per MCP server
resource "google_monitoring_custom_service" "mcp" {
  for_each     = local.mcp_slo_services
  project      = var.governance_project_id
  service_id   = "esmeralda-mcp-${each.key}-${var.environment}"
  display_name = "Esmeralda MCP ${each.key} (${var.environment}, Cloud Run ${each.value})"
}

resource "google_monitoring_slo" "mcp_availability" {
  for_each     = local.mcp_slo_services
  project      = var.governance_project_id
  service      = google_monitoring_custom_service.mcp[each.key].service_id
  slo_id       = "availability"
  display_name = "${each.key}: ${var.slo_config.mcp_availability_goal * 100}% non-5xx requests - 30-Day Rolling Window"

  goal                = var.slo_config.mcp_availability_goal
  rolling_period_days = 30

  request_based_sli {
    good_total_ratio {
      bad_service_filter   = "${local.mcp_requests[each.key]} AND metric.labels.response_code_class = \"5xx\""
      total_service_filter = local.mcp_requests[each.key]
    }
  }
}

resource "google_monitoring_slo" "mcp_latency" {
  for_each     = local.mcp_slo_services
  project      = var.governance_project_id
  service      = google_monitoring_custom_service.mcp[each.key].service_id
  slo_id       = "latency"
  display_name = "${each.key}: ${var.slo_config.mcp_latency_goal * 100}% of requests < ${var.slo_config.mcp_latency_ms} ms - 30-Day Rolling Window"

  goal                = var.slo_config.mcp_latency_goal
  rolling_period_days = 30

  request_based_sli {
    distribution_cut {
      distribution_filter = join(" AND ", [
        "resource.type = \"cloud_run_revision\"",
        "resource.labels.service_name = \"${each.value}\"",
        "metric.type = \"run.googleapis.com/request_latencies\"",
      ])
      range {
        max = var.slo_config.mcp_latency_ms
      }
    }
  }
}

# 2. Agents on Agent Engine: availability and latency from the platform's request metrics
# (aiplatform.googleapis.com/reasoning_engine/request_count and request_latencies, emitted for every
# Reasoning Engine whatever the framework; verified in the governance metrics scope). Latency counts the
# whole query, streamed queries included, so the threshold is end-to-end agent time (catalog example:
# observability.slo.latency_p95_ms = 10000).
resource "google_monitoring_custom_service" "agents" {
  project      = var.governance_project_id
  service_id   = "esmeralda-agents-${var.environment}"
  display_name = "Esmeralda Agents on Agent Engine (${var.environment})"
}

resource "google_monitoring_slo" "agent_availability" {
  project      = var.governance_project_id
  service      = google_monitoring_custom_service.agents.service_id
  slo_id       = "availability"
  display_name = "Agents: ${var.slo_config.agent_availability_goal * 100}% non-5xx queries - 30-Day Rolling Window"

  goal                = var.slo_config.agent_availability_goal
  rolling_period_days = 30

  request_based_sli {
    good_total_ratio {
      bad_service_filter   = "${local.agent_requests} AND metric.labels.response_code_class = \"5xx\""
      total_service_filter = local.agent_requests
    }
  }
}

resource "google_monitoring_slo" "agent_latency" {
  project      = var.governance_project_id
  service      = google_monitoring_custom_service.agents.service_id
  slo_id       = "latency"
  display_name = "Agents: ${var.slo_config.agent_latency_goal * 100}% of queries < ${var.slo_config.agent_latency_ms} ms - 30-Day Rolling Window"

  goal                = var.slo_config.agent_latency_goal
  rolling_period_days = 30

  request_based_sli {
    distribution_cut {
      distribution_filter = "resource.type = \"aiplatform.googleapis.com/ReasoningEngine\" AND metric.type = \"aiplatform.googleapis.com/reasoning_engine/request_latencies\""
      range {
        max = var.slo_config.agent_latency_ms
      }
    }
  }
}
