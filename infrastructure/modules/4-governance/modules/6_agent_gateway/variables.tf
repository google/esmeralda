# infrastructure/modules/4-governance/modules/6_agent_gateway/variables.tf

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
  description = "Full resource name of the Model Armor request (prompt) template in the governance project, used by CONTENT_AUTHZ."
  default     = ""
}

variable "model_armor_response_tpl" {
  type        = string
  description = "Full resource name of the Model Armor response template in the governance project, used by CONTENT_AUTHZ."
  default     = ""
}

variable "model_armor_gateway" {
  type        = bool
  description = "Create the Model Armor CONTENT_AUTHZ extension and policy (unary MCP tools/call, prompts/get and agent cards only)."
  default     = false
}

variable "model_armor_fail_open" {
  type        = bool
  description = "CONTENT_AUTHZ fail_open: let traffic through when Model Armor errors or times out. false is the recommended production value."
  default     = false
}

variable "model_armor_timeout_ms" {
  type        = number
  description = "Per-message timeout of the Model Armor callout, 10-10000 ms."
  default     = 10000

  validation {
    condition     = var.model_armor_timeout_ms >= 10 && var.model_armor_timeout_ms <= 10000
    error_message = "model_armor_timeout_ms must be between 10 and 10000."
  }
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
  description = "PEM of the internal Root CA (*.esmeralda.internal), owned by layer 3 security."
}

