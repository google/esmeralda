variable "environment" {
  type        = string
  description = "Target deployment environment (dev, stg, prod)"
}

variable "governance_project_id" {
  type        = string
  description = "GCP Project ID for central governance and telemetry"
}

variable "alert_email_address" {
  type        = string
  description = "Notification email address for SecOps alerts"
  default     = ""
}

variable "runaway_loop_token_threshold" {
  type        = number
  description = "Single-request token cap threshold to trigger runaway loop alert"
  default     = 50000
}

variable "spoke_project_ids" {
  type        = list(string)
  description = "List of spoke GCP project IDs to deploy log-based metrics to"
  default     = []
}

variable "gateway_service_name" {
  type        = string
  description = "Cloud Run service of the Envoy ingress gateway (envoy-gateway-<env>) to alert on (5xx ratio, token fetch failures); empty skips those alerts"
  default     = ""
}

variable "gateway_error_rate_threshold_pct" {
  type        = number
  description = "Alert when more than this percentage of the ingress gateway's requests return 5xx over 5 minutes"
  default     = 2.0
}

variable "slo_mcp_services" {
  type        = list(string)
  description = "MCP servers (catalog names) with Cloud Run SLOs; the Cloud Run service is <name>-<environment>"
  default     = []
}

variable "slo_config" {
  type = object({
    mcp_availability_goal   = optional(number, 0.995)
    mcp_latency_goal        = optional(number, 0.95)
    mcp_latency_ms          = optional(number, 5000)
    agent_availability_goal = optional(number, 0.995)
    agent_latency_goal      = optional(number, 0.95)
    agent_latency_ms        = optional(number, 10000)
  })
  description = "SLO goals (fractions) and latency thresholds (ms) for the MCP Cloud Run services and the Agent Engine agents"
  default     = {}
  nullable    = false
}

variable "alert_on_unattributed_usage" {
  type        = bool
  description = "Alert when a guarded workload's esmeralda.usage record (not Agent Runtime) lacks a verified caller"
  default     = true
}

variable "usage_metric_resource_types" {
  type = list(string)
  default = [
    "aiplatform.googleapis.com/ReasoningEngine", # agents on Agent Runtime (stdout / stderr)
    "cloud_run_revision",                        # Cloud Run workloads, MCP servers, the Envoy gateway
    "k8s_container",                             # GKE workloads
    "global",                                    # entries written through the Logging API without a resource
  ]
  nullable    = false
  description = "Monitored resource types the usage log-based metrics can be written against (PromQL needs a monitored_resource matcher on them)."

  validation {
    condition     = length(var.usage_metric_resource_types) > 0 && alltrue([for t in var.usage_metric_resource_types : can(regex("^[A-Za-z0-9_./-]+$", t))])
    error_message = "usage_metric_resource_types needs at least one monitored resource type (letters, digits, _ . / - only)."
  }
}
