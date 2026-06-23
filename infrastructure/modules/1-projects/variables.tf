# infrastructure/modules/1-projects/variables.tf

variable "billing_account" {
  description = "The enterprise billing account ID to bind to all created projects"
  type        = string
}

variable "folder_id" {
  description = "The folder ID under which to create the projects. If omitted, projects are created at organization root."
  type        = string
  default     = ""
}

variable "org_id" {
  description = "The organization ID under which to create the projects. Required if folder_id is omitted and billing account is tied to an organization."
  type        = string
  default     = ""
}

variable "project_prefix" {
  description = "A prefix string appended to the start of all projects to guarantee uniqueness"
  type        = string
  default     = "esmeralda"
}

# BYO Project Toggles
variable "byo_net_host_project" {
  description = "Set to true if the customer is bringing a pre-existing Shared VPC Host Project"
  type        = bool
  default     = false
}

variable "byo_gateway_project" {
  description = "Set to true if the customer is bringing a pre-existing API Gateway/Ingress Project"
  type        = bool
  default     = false
}

variable "byo_governance_project" {
  description = "Set to true if the customer is bringing a pre-existing governance/security/telemetry project"
  type        = bool
  default     = false
}

variable "existing_net_host_project" {
  description = "The project ID of the pre-existing Shared VPC Host Project. Required if byo_net_host_project is true."
  type        = string
  default     = ""
}

variable "existing_gateway_project" {
  description = "The project ID of the pre-existing API Gateway/Ingress Project. Required if byo_gateway_project is true."
  type        = string
  default     = ""
}

variable "existing_governance_project" {
  description = "The project ID of the pre-existing governance/security/telemetry project. Required if byo_governance_project is true."
  type        = string
  default     = ""
}

# Cost Allocation Labels
variable "environment" {
  description = "The environment classification label (e.g., dev, qa, prod)"
  type        = string
  default     = "dev"
}
