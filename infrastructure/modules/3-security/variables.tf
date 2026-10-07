# infrastructure/modules/3-security/variables.tf

variable "net_host_project_id" {
  description = "The project ID of the Shared VPC Host"
  type        = string
}

variable "gateway_project_id" {
  description = "The project ID of the API Ingress Gateway"
  type        = string
}

variable "cicd_project_id" {
  description = "The shared layer-0 CI/CD project ID hosting the Artifact Registry repositories"
  type        = string
}

variable "artifact_repository_id" {
  description = "Artifact Registry repository this env pulls images from (dev repo for dev, release repo for prd)"
  type        = string
}

variable "artifact_region" {
  description = "Region of the shared Artifact Registry repositories"
  type        = string
}

variable "mcps_project_id" {
  description = "The project ID allocated for corporate MCP servers"
  type        = string
}


variable "ai_coe_agents_project_id" {
  description = "The project ID allocated for Core AI Platform and A2A agents"
  type        = string
}

variable "cx_agents_project_id" {
  description = "The project ID allocated for CX team agents (cx-agents project)"
  type        = string
}

variable "governance_project_id" {
  description = "The project ID allocated for central security, governance, and telemetry"
  type        = string
}

variable "region" {
  description = "The primary region where regional security resources are placed"
  type        = string
  default     = "us-central1"
}

variable "org_id" {
  description = "The GCP Organization ID (numeric string)"
  type        = string
  default     = ""
}

# Workload and Governance project numbers are resolved dynamically in main.tf via data "google_project"

# BYO Security Toggles
variable "byo_security" {
  description = "If true, bypass creation of KMS Keyrings, Keys, and Secrets, and use pre-existing resources instead"
  type        = bool
  default     = false
}

variable "existing_database_key_id" {
  description = "The full resource URI of the existing database KMS key. Required if byo_security is true."
  type        = string
  default     = ""
}

variable "existing_secrets_key_id" {
  description = "The full resource URI of the existing secrets KMS key. Required if byo_security is true."
  type        = string
  default     = ""
}

variable "existing_db_password_secret_id" {
  description = "The full resource name of the existing DB password secret. Required if byo_security is true."
  type        = string
  default     = ""
}

variable "environment" {
  description = "The environment classification (e.g., dev, qa, prod)"
  type        = string
  default     = "dev"
}

variable "project_suffix" {
  description = "The random project suffix generated in Layer 1"
  type        = string
}

variable "backend_subnet_id" {
  description = "The resource ID/name of the backend subnet on the Shared VPC for Direct VPC Egress"
  type        = string
}

variable "gateway_subnet_id" {
  description = "The resource ID/name of the gateway subnet on the Shared VPC for Gateway Egress"
  type        = string
  default     = ""
}

variable "ai_coe_agents_sql_service_agent" {
  description = "The Cloud SQL Service Agent email in the AI CoE agents project"
  type        = string
}

variable "governance_secrets_service_agent" {
  description = "The Secret Manager Service Agent email in Governance project"
  type        = string
}


