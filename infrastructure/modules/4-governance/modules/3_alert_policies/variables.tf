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

variable "gateway_service_name" {
  type        = string
  description = "Cloud Run service of the Envoy ingress gateway (envoy-gateway-<env>) to alert on (5xx ratio, token fetch failures); empty skips those alerts"
  default     = ""
}

variable "gateway_error_rate_threshold_pct" {
  type        = number
  description = "Alert when more than this percentage of the ingress gateway's requests return 5xx over 5 minutes"
  default     = 2.0
}
