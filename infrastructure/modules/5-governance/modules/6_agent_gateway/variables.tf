# infrastructure/modules/5-governance/modules/6_agent_gateway/variables.tf

variable "governance_project_id" {
  type        = string
  description = "The GCP Project ID of the Central Governance project."
}

variable "environment" {
  type        = string
  description = "Target deployment environment (dev, prd, etc.)."
  default     = "dev"
}

variable "region" {
  type        = string
  description = "Google Cloud Region for the Agent Gateway."
  default     = "us-central1"
}

variable "subnet_self_link" {
  type        = string
  description = "The self-link of the Shared VPC subnet to connect the Agent Gateway PSC Network Attachment."
  default     = ""
}

variable "net_host_project_id" {
  type        = string
  description = "The GCP Project ID of the Shared VPC host project."
  default     = ""
}

variable "vpc_name" {
  type        = string
  description = "The name of the Shared VPC network."
  default     = ""
}

variable "model_armor_template_name" {
  type        = string
  description = "Full resource name of the Model Armor template in Governance project for CONTENT_AUTHZ."
  default     = ""
}

variable "agent_invoker_sa_emails" {
  type        = list(string)
  description = "List of agent/invoker service accounts and principalSets granted access to the Gateway CA certificate secret."
  default     = []
}

variable "agent_project_ids" {
  type        = list(string)
  description = "List of agent project IDs (A2A and Root) that run AI agents and Reasoning Engines."
  default     = []
}

variable "enable_agent_gateway" {
  type        = bool
  description = "Toggle to enable or disable the Central Agent Gateway."
  default     = true
}

variable "internal_root_ca_pem" {
  type        = string
  description = "PEM-encoded Root CA certificate for internal *.esmeralda.internal endpoints."
  default     = ""
}

variable "gateway_project_id" {
  type        = string
  description = "The GCP Project ID of the Kong API Gateway project."
  default     = ""
}


