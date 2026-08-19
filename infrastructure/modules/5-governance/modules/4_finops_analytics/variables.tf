variable "environment" {
  type        = string
  description = "Target deployment environment (dev, stg, prod)"
}

variable "governance_project_id" {
  type        = string
  description = "GCP Project ID for central governance and telemetry"
}

variable "enable_analytics_views" {
  type        = bool
  default     = false
  description = "Set to true after initial agent inference traffic has generated BigQuery log tables"
}
