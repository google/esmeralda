# infrastructure/modules/5-governance/modules/5_model_armor/main.tf
# ==============================================================================
# SUBMODULE 5: MODEL ARMOR GUARDRAIL TEMPLATES
# ==============================================================================

terraform {
  required_providers {
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 5.0"
    }
  }
}

variable "governance_project_id" {
  type        = string
  description = "Central Governance GCP Project ID"
}

variable "environment" {
  type        = string
  default     = "dev"
  description = "Target deployment environment"
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "Target region"
}

# Enable Model Armor API in Governance Project
resource "google_project_service" "model_armor_api" {
  project            = var.governance_project_id
  service            = "modelarmor.googleapis.com"
  disable_on_destroy = false
}

# 1. Request / Prompt Guardrails Template (Inbound: Prompt Injection + Jailbreaks + Malicious URIs + SDP + RAI + Multi-Language)
resource "google_model_armor_template" "prompt_guardrails" {
  provider    = google-beta
  depends_on  = [google_project_service.model_armor_api]
  location    = var.region
  project     = var.governance_project_id
  template_id = "esmeralda-prompt-guardrails-${var.environment}"

  filter_config {
    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = "MEDIUM_AND_ABOVE"
    }

    malicious_uri_filter_settings {
      filter_enforcement = "ENABLED"
    }

    sdp_settings {
      basic_config {
        filter_enforcement = "ENABLED"
      }
    }

    rai_settings {
      rai_filters {
        filter_type      = "HATE_SPEECH"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
      rai_filters {
        filter_type      = "HARASSMENT"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
      rai_filters {
        filter_type      = "SEXUALLY_EXPLICIT"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
      rai_filters {
        filter_type      = "DANGEROUS"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
    }
  }

  template_metadata {
    log_template_operations = true
    log_sanitize_operations = true

    multi_language_detection {
      enable_multi_language_detection = true
    }
  }
}

# 2. Response / Output Guardrails Template (Outbound: Malicious URIs + SDP + RAI + Multi-Language; Omits Prompt Injection)
resource "google_model_armor_template" "response_guardrails" {
  provider    = google-beta
  depends_on  = [google_project_service.model_armor_api]
  location    = var.region
  project     = var.governance_project_id
  template_id = "esmeralda-response-guardrails-${var.environment}"

  filter_config {
    malicious_uri_filter_settings {
      filter_enforcement = "ENABLED"
    }

    sdp_settings {
      basic_config {
        filter_enforcement = "ENABLED"
      }
    }

    rai_settings {
      rai_filters {
        filter_type      = "HATE_SPEECH"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
      rai_filters {
        filter_type      = "HARASSMENT"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
      rai_filters {
        filter_type      = "SEXUALLY_EXPLICIT"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
      rai_filters {
        filter_type      = "DANGEROUS"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
    }
  }

  template_metadata {
    log_template_operations = true
    log_sanitize_operations = true

    multi_language_detection {
      enable_multi_language_detection = true
    }
  }
}

output "prompt_template_id" {
  value       = google_model_armor_template.prompt_guardrails.template_id
  description = "Prompt Inbound Guardrails Template ID"
}

output "prompt_template_name" {
  value       = google_model_armor_template.prompt_guardrails.name
  description = "Prompt Inbound Guardrails Template Full Resource Name"
}

output "response_template_id" {
  value       = google_model_armor_template.response_guardrails.template_id
  description = "Response Outbound Guardrails Template ID"
}

output "response_template_name" {
  value       = google_model_armor_template.response_guardrails.name
  description = "Response Outbound Guardrails Template Full Resource Name"
}
