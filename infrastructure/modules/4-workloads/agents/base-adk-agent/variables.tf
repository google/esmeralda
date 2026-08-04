variable "project_id" {
  description = "The GCP project ID allocated for orchestrator agents (prj-esmeralda-agents)"
  type        = string
}

variable "region" {
  description = "The region where the Orchestrator Reasoning Engine is deployed"
  type        = string
  default     = "us-central1"
}

variable "agent_name" {
  description = "The registered display name of the Root Orchestrator reasoning engine"
  type        = string
  default     = "base-adk-orchestrator"
}

variable "agent_service_account" {
  description = "The email address of the dedicated Orchestrator Agent service account created in Stage 3"
  type        = string
}

variable "enable_agent_identity" {
  description = "Whether to enable Vertex AI Agent Identity instead of standard custom Service Account"
  type        = bool
  default     = true
}

# Run-time Dependency Injections
variable "gateway_mcp_url" {
  description = "The injected private or public endpoint URI of the active API Ingress Gateway (from Option A, B, or C)"
  type        = string
}

variable "a2a_agent_url" {
  description = "The private gateway ingress URI of the downstream A2A Mortgage Assistant (routed via the swappable gateway)"
  type        = string
}

# BYOC Container Image URI
variable "agent_image_uri" {
  description = "The Artifact Registry URI of the pre-built BYOC container image for the agent"
  type        = string
}

variable "agent_gateway_id" {
  description = "The resource ID of the Agent Gateway instance (AGENT_TO_ANYWHERE egress mode) to bind"
  type        = string
  default     = ""
}


variable "vpc_id" {
  description = "The self-link of the central Shared VPC network"
  type        = string
  default     = ""
}

variable "subnet_id" {
  description = "The self-link of the backend workload subnet inside the Shared VPC"
  type        = string
  default     = ""
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

