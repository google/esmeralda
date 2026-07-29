terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  alias                 = "dlp_provider"
  user_project_override = true
  billing_project       = var.governance_project_id
}

# Cloud DLP Inspection Template for Telemetry PII Redaction
resource "google_data_loss_prevention_inspect_template" "pii_redaction" {
  provider     = google.dlp_provider
  parent       = "projects/${var.governance_project_id}/locations/global"
  display_name = "Esmeralda Telemetry PII Inspection Template"
  description  = "Redacts SSNs, credit card numbers, and emails from log sinks before BigQuery ingestion"

  inspect_config {
    info_types { name = "EMAIL_ADDRESS" }
    info_types { name = "CREDIT_CARD_NUMBER" }
    info_types { name = "US_SOCIAL_SECURITY_NUMBER" }
    min_likelihood = "LIKELY"
  }
}
