variable "environment" {
  type        = string
  description = "Target deployment environment (dev, stg, prod)"
}

variable "governance_project_id" {
  type        = string
  description = "GCP Project ID for central governance and telemetry"
}

variable "alert_email_address" {
  type        = string
  description = "Notification email address for SecOps alerts"
  default     = ""
}

variable "runaway_loop_token_threshold" {
  type        = number
  description = "Single-request token cap threshold to trigger runaway loop alert"
  default     = 50000
}

variable "spoke_project_ids" {
  type        = list(string)
  description = "List of spoke GCP project IDs to deploy log-based metrics to"
  default     = []
}
