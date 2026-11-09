variable "environment" {
  type        = string
  description = "Target deployment environment (dev, stg, prod)"
}

variable "governance_project_id" {
  type        = string
  description = "GCP Project ID for central governance and telemetry"
}

variable "usage_dataset_id" {
  type        = string
  description = "BigQuery dataset (same project) holding the esmeralda.usage landing tables (modules/1_telemetry_sinks)"
}

variable "usage_landing_tables" {
  type        = list(string)
  description = "esmeralda.usage landing tables in usage_dataset_id that the usage_events view unions"

  validation {
    condition     = length(var.usage_landing_tables) > 0
    error_message = "usage_events needs at least one landing table."
  }
}
