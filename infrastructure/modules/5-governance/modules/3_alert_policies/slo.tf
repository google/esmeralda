# ------------------------------------------------------------------------------
# SERVICE LEVEL OBJECTIVES (SLOs) & ERROR BUDGET GOVERNANCE
# ------------------------------------------------------------------------------

# 1. Custom Monitored Service for Agent Platform Reasoning & A2A
resource "google_monitoring_service" "agent_platform_service" {
  project      = var.governance_project_id
  service_id   = "esmeralda-agent-platform-${var.environment}"
  display_name = "Esmeralda Agent Platform Reasoning Service (${var.environment})"

  basic_service {
    service_type = "CLOUD_RUN"
    service_labels = {
      service_name = "root-agent"
      location     = "us-central1"
    }
  }
}

# 2. SLO 1: Platform Availability (99.5% Success Rate over 30-Day Window)
resource "google_monitoring_slo" "platform_availability_slo" {
  project      = var.governance_project_id
  service      = google_monitoring_service.agent_platform_service.service_id
  slo_id       = "platform-availability-995"
  display_name = "99.5% Platform Availability - 30-Day Rolling Window"

  goal            = 0.995
  rolling_period_days = 30

  basic_sli {
    availability {
      enabled = true
    }
  }
}

# 3. SLO 2: Reasoning Latency (95% Execution Turns < 8,000 ms)
resource "google_monitoring_slo" "reasoning_latency_slo" {
  project      = var.governance_project_id
  service      = google_monitoring_service.agent_platform_service.service_id
  slo_id       = "reasoning-latency-95-8s"
  display_name = "95% End-to-End Latency < 8,000 ms - 30-Day Rolling Window"

  goal            = 0.95
  rolling_period_days = 30

  request_based_sli {
    distribution_cut {
      distribution_filter = "resource.type = \"cloud_run_revision\" AND metric.type = \"run.googleapis.com/request_latencies\""
      range {
        max = 8000
      }
    }
  }
}
