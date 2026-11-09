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

variable "usage_landing_tables" {
  type        = list(string)
  description = "BigQuery tables the usage sinks write esmeralda.usage records to: one per producer log ID (the log ID with every non-alphanumeric character replaced by an underscore)."
}
