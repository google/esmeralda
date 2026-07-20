variable "environment" {
  type        = string
  description = "Target deployment environment (dev, stg, prod)"
}

variable "governance_project_id" {
  type        = string
  description = "GCP Project ID for central governance and telemetry"
}

variable "spoke_project_ids" {
  type        = list(string)
  description = "List of spoke project IDs to attach log sinks"
}
