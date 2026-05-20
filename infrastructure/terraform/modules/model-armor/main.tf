# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

/**
 * Copyright 2025 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */


data "google_project" "project" {
  project_id = var.project_id
}

locals {
  # Service Extensions service account format: service-PROJECT_NUMBER@gcp-sa-dep.iam.gserviceaccount.com
  # This account is automatically created when networkservices.googleapis.com is enabled
  # See: https://cloud.google.com/service-extensions/docs/configure-extensions-to-google-services
  service_extensions_sa_email = "service-${data.google_project.project.number}@gcp-sa-dep.iam.gserviceaccount.com"
}

resource "google_model_armor_template" "template" {
  count       = var.enable_model_armor ? 1 : 0
  project     = var.project_id
  location    = var.region
  template_id = var.template_id

  depends_on = [google_project_iam_member.model_armor_admin]

  # Filter Configuration
  filter_config {
    # RAI Settings Filters
    rai_settings {
      dynamic "rai_filters" {
        for_each = var.rai_filters
        content {
          filter_type      = rai_filters.value.filter_type
          confidence_level = rai_filters.value.confidence_level
        }
      }
    }

    # Sensitive Data Protection Settings
    sdp_settings {
      basic_config {
        filter_enforcement = var.sdp_basic_filter_enforcement
      }
    }

    # PI and Jailbreak Filter Settings
    pi_and_jailbreak_filter_settings {
      filter_enforcement = var.pi_jailbreak_enforcement
      confidence_level   = var.pi_jailbreak_confidence_level
    }

    # Malicious URI Filter Settings
    malicious_uri_filter_settings {
      filter_enforcement = var.malicious_uri_enforcement
    }
  }

  # Template Metadata
  template_metadata {
    custom_llm_response_safety_error_code    = var.llm_response_error_code
    custom_llm_response_safety_error_message = var.llm_response_error_message
    custom_prompt_safety_error_code          = var.prompt_error_code
    custom_prompt_safety_error_message       = var.prompt_error_message
    ignore_partial_invocation_failures       = var.ignore_partial_failures
    log_template_operations                  = var.log_template_operations
    log_sanitize_operations                  = var.log_sanitize_operations
  }
}

# =============================================================================
# IAM Bindings for Service Extensions Service Account (gcp-sa-dep)
# Required for GCPTrafficExtension to call Model Armor from GKE Gateway
# See: https://cloud.google.com/service-extensions/docs/configure-extensions-to-google-services#configure-traffic-ma
# =============================================================================

# Grant container.admin role for GKE access (required by Service Extensions)
# See: https://docs.cloud.google.com/service-extensions/docs/configure-extensions-to-google-services
resource "google_project_iam_member" "service_extensions_container_admin" {
  count   = var.enable_model_armor && var.enable_iam_bindings ? 1 : 0
  project = var.project_id
  role    = "roles/container.admin"
  member  = "serviceAccount:${local.service_extensions_sa_email}"
}

# Grant modelarmor.calloutUser role for Model Armor callouts
resource "google_project_iam_member" "service_extensions_callout_user" {
  count   = var.enable_model_armor && var.enable_iam_bindings ? 1 : 0
  project = var.project_id
  role    = "roles/modelarmor.calloutUser"
  member  = "serviceAccount:${local.service_extensions_sa_email}"
}

# Grant serviceusage.serviceUsageConsumer role for API usage
resource "google_project_iam_member" "service_extensions_service_usage" {
  count   = var.enable_model_armor && var.enable_iam_bindings ? 1 : 0
  project = var.project_id
  role    = "roles/serviceusage.serviceUsageConsumer"
  member  = "serviceAccount:${local.service_extensions_sa_email}"
}

# Grant modelarmor.user role for Model Armor usage
resource "google_project_iam_member" "service_extensions_model_armor_user" {
  count   = var.enable_model_armor && var.enable_iam_bindings ? 1 : 0
  project = var.project_id
  role    = "roles/modelarmor.user"
  member  = "serviceAccount:${local.service_extensions_sa_email}"
}

# =============================================================================
# Model Armor Admin IAM Bindings
# Grants modelarmor.admin and modelarmor.floorSettingsAdmin to specified members
# =============================================================================

resource "google_project_iam_member" "model_armor_admin" {
  for_each = var.enable_model_armor ? toset(var.model_armor_admin_members) : toset([])
  project  = var.project_id
  role     = "roles/modelarmor.admin"
  member   = each.value
}

resource "google_project_iam_member" "model_armor_floor_settings_admin" {
  for_each = var.enable_model_armor ? toset(var.model_armor_admin_members) : toset([])
  project  = var.project_id
  role     = "roles/modelarmor.floorSettingsAdmin"
  member   = each.value
}

# =============================================================================
# Model Armor Floor Setting for MCP Server Protection
# Configures project-level floor setting with GOOGLE_MCP_SERVER as integrated
# service using INSPECT_AND_BLOCK enforcement for BigQuery MCP endpoint
# =============================================================================

resource "google_model_armor_floorsetting" "mcp_floor_setting" {
  count    = var.enable_model_armor && (var.enable_mcp_floor_setting || var.enable_vertex_ai_integration) ? 1 : 0
  parent   = "projects/${var.project_id}"
  location = "global"

  depends_on = [
    google_project_iam_member.model_armor_admin,
    google_project_iam_member.model_armor_floor_settings_admin,
  ]

  enable_floor_setting_enforcement = true

  integrated_services = compact(concat(
    var.enable_vertex_ai_integration ? ["AI_PLATFORM"] : [],
    var.enable_mcp_floor_setting ? ["GOOGLE_MCP_SERVER"] : [],
  ))

  filter_config {
    rai_settings {
      dynamic "rai_filters" {
        for_each = var.rai_filters
        content {
          filter_type      = rai_filters.value.filter_type
          confidence_level = rai_filters.value.confidence_level
        }
      }
    }
    pi_and_jailbreak_filter_settings {
      filter_enforcement = var.pi_jailbreak_enforcement
      confidence_level   = var.pi_jailbreak_confidence_level
    }
    malicious_uri_filter_settings {
      filter_enforcement = var.malicious_uri_enforcement
    }
  }

  dynamic "ai_platform_floor_setting" {
    for_each = var.enable_vertex_ai_integration && var.vertex_ai_inspect_only ? [1] : []
    content {
      inspect_only         = true
      enable_cloud_logging = var.vertex_ai_enable_cloud_logging
    }
  }

  dynamic "ai_platform_floor_setting" {
    for_each = var.enable_vertex_ai_integration && !var.vertex_ai_inspect_only ? [1] : []
    content {
      inspect_and_block    = true
      enable_cloud_logging = var.vertex_ai_enable_cloud_logging
    }
  }

  dynamic "google_mcp_server_floor_setting" {
    for_each = var.enable_mcp_floor_setting ? [1] : []
    content {
      inspect_and_block    = true
      enable_cloud_logging = var.vertex_ai_enable_cloud_logging
    }
  }
}

# Enable Model Armor content security scanning on the MCP server
# No Terraform resource exists for this, so we use a null_resource with local-exec
resource "null_resource" "mcp_content_security" {
  count = var.enable_model_armor && var.enable_mcp_floor_setting ? 1 : 0

  provisioner "local-exec" {
    command = "gcloud beta services mcp content-security add modelarmor.googleapis.com --project=${var.project_id}"
  }

  depends_on = [google_model_armor_floorsetting.mcp_floor_setting]
}

# =============================================================================
# Vertex AI Service Agent IAM Binding
# Grants roles/modelarmor.user to the AI Platform service agent so Vertex AI
# can invoke Model Armor sanitization
# =============================================================================

resource "google_project_iam_member" "vertex_ai_model_armor_user" {
  count   = var.enable_model_armor && var.enable_vertex_ai_integration ? 1 : 0
  project = var.project_id
  role    = "roles/modelarmor.user"
  member  = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-aiplatform.iam.gserviceaccount.com"
}

# =============================================================================
# Gemini Enterprise Multi-Region Template
# Creates a Model Armor template in a multi-region location (us or eu) for use
# with Discovery Engine / Gemini Enterprise. The template name output is used
# to configure the customerPolicy.modelArmorConfig via REST API.
# =============================================================================

resource "google_model_armor_template" "gemini_enterprise_template" {
  count       = var.enable_model_armor && var.enable_gemini_enterprise_template ? 1 : 0
  project     = var.project_id
  location    = var.gemini_enterprise_template_location
  template_id = var.gemini_enterprise_template_id

  depends_on = [google_project_iam_member.model_armor_admin]

  filter_config {
    rai_settings {
      dynamic "rai_filters" {
        for_each = var.rai_filters
        content {
          filter_type      = rai_filters.value.filter_type
          confidence_level = rai_filters.value.confidence_level
        }
      }
    }

    sdp_settings {
      basic_config {
        filter_enforcement = var.sdp_basic_filter_enforcement
      }
    }

    pi_and_jailbreak_filter_settings {
      filter_enforcement = var.pi_jailbreak_enforcement
      confidence_level   = var.pi_jailbreak_confidence_level
    }

    malicious_uri_filter_settings {
      filter_enforcement = var.malicious_uri_enforcement
    }
  }

  template_metadata {
    custom_llm_response_safety_error_code    = var.llm_response_error_code
    custom_llm_response_safety_error_message = var.llm_response_error_message
    custom_prompt_safety_error_code          = var.prompt_error_code
    custom_prompt_safety_error_message       = var.prompt_error_message
    ignore_partial_invocation_failures       = var.ignore_partial_failures
    log_template_operations                  = var.log_template_operations
    log_sanitize_operations                  = var.log_sanitize_operations
  }
}
