# Variables for Layer 4 Governance Stack
variable "governance_project_id" {
  type        = string
  description = "Central Governance GCP Project ID hosting metrics scope, BigQuery logs, and alerts"
}

variable "environment" {
  type        = string
  default     = "dev"
  description = "Target deployment environment (dev, staging, prod)"
}

variable "spoke_project_ids" {
  type        = list(string)
  default     = []
  description = "List of spoke GCP project IDs to attach to central Governance Metrics Scope"
}

variable "alert_email_address" {
  type        = string
  default     = "esmeralda-secops@google.com"
  description = "Email address for Security & SRE notification channel"
}

variable "runaway_loop_token_threshold" {
  type        = number
  default     = 50000
  description = "Single-request token threshold cap triggering runaway loop alert"
}

# ------------------------------------------------------------------------------
# Usage analytics (esmeralda.usage contract) and SLOs
# ------------------------------------------------------------------------------
variable "usage_landing_tables" {
  type = list(string)
  default = [
    "aiplatform_googleapis_com_reasoning_engine_stdout", # esmeralda library on Agent Engine (stdout)
    "aiplatform_googleapis_com_reasoning_engine_stderr", # esmeralda library on Agent Engine (Python logging default stream)
    "run_googleapis_com_stdout",                         # Cloud Run workloads and the Envoy gateway access log
    "run_googleapis_com_stderr",
    "esmeralda_apigee_interactions", # Apigee ML-interaction MessageLogging policy
    "esmeralda_usage",               # producers writing through the Cloud Logging API (OTel collector, other clouds)
  ]
  nullable    = false
  description = "Usage landing tables, one per producer log ID (log ID with non-alphanumerics as underscores). The usage sinks match records by jsonPayload.schema; this list only decides which landing tables the usage_events view reads."

  validation {
    condition     = length(var.usage_landing_tables) > 0 && alltrue([for t in var.usage_landing_tables : can(regex("^[A-Za-z0-9_]+$", t))])
    error_message = "usage_landing_tables needs at least one table, each made of letters, digits and underscores only."
  }
}

variable "principal_teams" {
  type = list(object({
    principal      = string
    principal_type = string
    team           = string
    source         = optional(string, "terraform")
  }))
  default     = []
  nullable    = false
  description = "Principal -> team rows declared in Terraform (served with the principal_teams table through vw_principal_teams). principal_type = gcp_project maps a project ID to the team that owns it: usage logged from that project is billed to it when no verified caller is present."
}

variable "principal_teams_extra_columns" {
  type = list(object({
    name        = string
    type        = string
    mode        = optional(string, "NULLABLE")
    description = optional(string, "")
  }))
  default     = []
  nullable    = false
  description = "Extra principal_teams table columns after (principal, principal_type, team, source); NULLABLE or REPEATED only."
}

variable "slo_mcp_services" {
  type        = list(string)
  default     = ["corporate-email", "income-verification", "legacy-dms"]
  nullable    = false
  description = "MCP servers (catalog names) that get Cloud Run availability and latency SLOs; the Cloud Run service is <name>-<environment>."
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
  default     = {}
  nullable    = false
  description = "SLO goals (fractions) and latency thresholds (ms) for the MCP Cloud Run services and the Agent Engine agents."
}

variable "alert_on_unattributed_usage" {
  type    = bool
  default = false
  # Off until verified attribution lands (observability design, phase 6): until then workloads send
  # caller.verified = false and every record would count as unattributed. Flip the default to true then.
  description = "Alert when any esmeralda.usage record lacks a verified caller (expected count: 0, design principle 'fail closed')."
}

# env.yaml's monitoring_config. Only error_rate_threshold_pct is wired so far (the ingress gateway 5xx
# alert); the other attributes there are dropped on type conversion until something uses them.
variable "monitoring_config" {
  type = object({
    error_rate_threshold_pct = optional(number, 2.0)
  })
  default     = {}
  nullable    = false
  description = "Per-environment monitoring settings (env.yaml monitoring_config)"

  validation {
    condition     = var.monitoring_config.error_rate_threshold_pct > 0 && var.monitoring_config.error_rate_threshold_pct <= 100
    error_message = "monitoring_config.error_rate_threshold_pct is a percentage: above 0, at most 100."
  }
}

variable "ingress_gateway_service_name" {
  type        = string
  default     = ""
  description = "Cloud Run service of the Envoy ingress gateway (envoy-gateway-<env>) when it is enabled; empty skips the gateway 5xx and token-fetch alerts"
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "Target deployment region"
}

variable "subnet_self_link" {
  type        = string
  default     = ""
  description = "The self-link of the Shared VPC subnet to connect the Central Agent Gateway PSC Network Attachment"
}

variable "net_host_project_id" {
  type        = string
  default     = ""
  description = "The GCP Project ID of the Shared VPC host project"
}

variable "vpc_name" {
  type        = string
  default     = ""
  description = "The name of the Shared VPC network"
}

variable "agent_invoker_sa_emails" {
  type        = list(string)
  default     = []
  description = "List of agent/invoker service accounts granted access to the Gateway CA certificate secret"
}

variable "agent_project_ids" {
  type        = list(string)
  default     = []
  description = "List of agent project IDs (A2A and Root) that run AI agents and Reasoning Engines."
}

variable "enable_agent_gateway" {
  type        = bool
  default     = false
  description = "Toggle to deploy the Central Agent Gateway in Governance"
}

variable "internal_root_ca_pem" {
  type        = string
  description = "PEM of the internal Root CA (*.esmeralda.internal), owned by layer 3 security"
}

variable "enable_semantic_governance" {
  type        = bool
  default     = false
  description = "Toggle to publish the compiled GEAP Semantic Governance (Natural Language Constraints) and Unified Access Policy (UAP) bundle to Secret Manager in the governance project"
}

variable "semantic_governance_enforcement_mode" {
  type        = string
  default     = "ENFORCE"
  description = "Default GEAP Semantic Governance enforcement mode across Natural Language Constraints (ENFORCE or DRY_RUN)"

  validation {
    condition     = contains(["ENFORCE", "DRY_RUN"], var.semantic_governance_enforcement_mode)
    error_message = "semantic_governance_enforcement_mode must be either \"ENFORCE\" or \"DRY_RUN\"."
  }
}

variable "custom_semantic_governance_policies" {
  type = map(object({
    target_mcp_server            = string
    tools                        = list(string)
    principal                    = string
    enforcement_mode             = optional(string)
    natural_language_constraints = list(string)
  }))
  default     = {}
  nullable    = false
  description = "Optional custom Natural Language Constraint (NLC) policies merged with the default mortgage underwriting policies"
}


