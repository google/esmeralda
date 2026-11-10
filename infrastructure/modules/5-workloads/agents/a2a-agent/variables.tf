variable "project_id" {
  description = "The GCP project ID allocated for downstream agents (prj-esmeralda-agents)"
  type        = string
}

variable "region" {
  description = "The region where the Reasoning Engine is deployed"
  type        = string
  default     = "us-central1"
}

variable "vpc_id" {
  description = "The self-link of the central Shared VPC network"
  type        = string
}

variable "enable_agent_identity" {
  description = "Whether to enable Vertex AI Agent Identity instead of standard custom Service Account"
  type        = bool
  default     = true
}

variable "enable_native_spiffe_auth" {
  description = "When true, omit SERVICE_ACCOUNT_EMAIL from container env vars so the agent authenticates with its native GEAP SPIFFE identity instead of impersonating mcp_invoker_sa_email"
  type        = bool
  default     = false
}

variable "subnet_id" {
  description = "The self-link of the backend workload subnet inside the Shared VPC"
  type        = string
}

variable "net_host_project_id" {
  description = "The host project ID hosting the central Shared VPC and Cloud DNS"
  type        = string
  default     = ""
}

variable "vpc_name" {
  description = "The name of the Shared VPC network"
  type        = string
  default     = ""
}

variable "agent_name" {
  description = "The registered display name of the AI CoE mortgage specialist reasoning engine"
  type        = string
  default     = "ai-coe-mortgage-specialist"
}

variable "agent_service_account" {
  description = "The email address of the dedicated A2A Agent service account created in Layer 3"
  type        = string
}

variable "invoker_service_accounts" {
  description = "List of service account emails authorized to invoke this agent"
  type        = list(string)
  default     = []
}

variable "mcp_invoker_sa_email" {
  description = "The email address of the shared MCP invoker service account for IAM impersonation"
  type        = string
  default     = ""
}

# BYOC Container Image URI
variable "agent_image_uri" {
  description = "Artifact Registry URI of the pre-built BYOC image: <image>:<tag> (resolved to its digest at plan time) or <image>@sha256:... (used as-is, e.g. from release/prd.yaml)"
  type        = string
}


variable "network_attachment" {
  description = "Optional Private Service Connect Network Attachment ID for Vertex AI Reasoning Engine VPC attachment"
  type        = string
  default     = ""
}

variable "psc_subnet_id" {
  description = "The self-link of the PSC interface subnetwork inside the Shared VPC"
  type        = string
  default     = ""
}

variable "enable_psc_network" {
  description = "Whether to create a per-agent PSC Network Attachment and enable PSC-Interface"
  type        = bool
  default     = true
}

variable "environment" {
  description = "The active deployment environment name"
  type        = string
  default     = "dev"
}

variable "agent_config_path" {
  description = "Absolute path to the agent.yaml file defining agent resources, metadata, and environment variables"
  type        = string
  default     = ""
}

variable "agent_card_json" {
  description = "The JSON string of the exported A2A agent card"
  type        = string
  default     = ""
}

variable "agent_gateway_id" {
  description = "The resource ID of the Central Agent Gateway in Governance project (AGENT_TO_ANYWHERE)"
  type        = string
  default     = ""
}

variable "agent_gateway_root_certificates" {
  description = "PEM bundle the agent must trust (Agent Gateway TLS-inspection roots + internal Root CA), from layer 4 governance. Injected as the AGENT_GATEWAY_ROOT_CERTIFICATES env var so images stay env-neutral."
  type        = string
  default     = ""
}

variable "governance_project_id" {
  description = "Central governance project hosting the Agent Registry where the A2A agent card is published"
  type        = string
  default     = ""
}

variable "central_events_dataset_id" {
  description = "Central BigQuery dataset ID in the governance project for agent event logs"
  type        = string
  default     = ""
}

variable "access_token_hosts" {
  description = "Entry point hosts this agent calls with an OAuth2 access token instead of an ID token: catalog agents served by the built-in direct backend (Agent Runtime only takes access tokens). Set as ACCESS_TOKEN_HOSTS; empty sets nothing"
  type        = list(string)
  default     = []
}
