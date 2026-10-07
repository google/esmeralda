# infrastructure/modules/5-workloads/apihub/variables.tf

variable "project_id" {
  description = "Project hosting the API Hub instance"
  type        = string
}

variable "region" {
  description = "API Hub location"
  type        = string
}

variable "api_hub_instance_id" {
  description = "API Hub instance ID"
  type        = string
  default     = "esmeralda-apihub"
}
