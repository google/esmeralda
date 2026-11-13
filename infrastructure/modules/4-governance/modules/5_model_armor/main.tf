# infrastructure/modules/4-governance/modules/5_model_armor/main.tf
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

variable "model_calls" {
  type        = string
  default     = "off"
  description = "Vertex AI built-in Model Armor on the agents' Gemini calls: off | inspect | enforce."

  validation {
    condition     = contains(["off", "inspect", "enforce"], var.model_calls)
    error_message = "model_calls must be one of: off, inspect, enforce."
  }
}

variable "model_calls_sdp" {
  type        = bool
  default     = false
  description = "Also run the Sensitive Data Protection basic filter on model calls (blocks on a match, never masks)."
}

variable "pi_confidence" {
  type        = string
  default     = "HIGH"
  description = "Minimum confidence for the prompt injection and jailbreak filter on model calls."
}

variable "enable_cloud_logging" {
  type        = bool
  default     = true
  description = "Log the Vertex AI integration's Model Armor findings to Cloud Logging."
}

variable "agent_project_ids" {
  type        = list(string)
  default     = []
  description = "Projects whose Gemini calls get the Vertex AI floor setting (one per agent team project)."
}

variable "template_sdp" {
  type        = bool
  default     = false
  description = "Sensitive Data Protection basic filter in the gateway prompt/response templates (blocks on a match, never masks)."
}

# Enable Model Armor API in Governance Project
resource "google_project_service" "model_armor_api" {
  project            = var.governance_project_id
  service            = "modelarmor.googleapis.com"
  disable_on_destroy = false
}

# 1. Request / Prompt Guardrails Template (Inbound: Prompt Injection + Jailbreaks + Malicious URIs + optional SDP + RAI + Multi-Language)
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

    # SDP blocks on a match (the gateway never masks): off unless template_sdp (governance.model_armor.gateway_sdp).
    dynamic "sdp_settings" {
      for_each = var.template_sdp ? [1] : []
      content {
        basic_config {
          filter_enforcement = "ENABLED"
        }
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

# 2. Response / Output Guardrails Template (Outbound: Malicious URIs + optional SDP + RAI + Multi-Language; Omits Prompt Injection)
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

    # SDP blocks on a match (the gateway never masks): off unless template_sdp (governance.model_armor.gateway_sdp).
    dynamic "sdp_settings" {
      for_each = var.template_sdp ? [1] : []
      content {
        basic_config {
          filter_enforcement = "ENABLED"
        }
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
