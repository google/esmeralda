# infrastructure/modules/4-governance/modules/5_model_armor/vertex_floor_settings.tf
# ==============================================================================
# VERTEX AI BUILT-IN MODEL ARMOR (agent -> Gemini calls)
# ==============================================================================
# A project-level Model Armor floor setting with the Vertex AI integration screens every Gemini
# generateContent call made in that project: the prompt before the model runs and the response
# after it. Set on each agent project, because the agents call Gemini with their own project.
#
# Limits (see docs: Central Agent Gateway, Model Armor coverage):
#   * streamGenerateContent is not screened (verified live: no result, no block, even in enforce);
#   * no de-identification: an SDP match blocks the call, it is never masked (hence
#     model_calls_sdp, off by default);
#   * the integration fails open: when Model Armor is unavailable the call goes through unscreened.
# Verified live: calls to the global endpoint (the agents' MODEL_LOCATION) are screened.

locals {
  floor_projects = var.model_calls == "off" ? toset([]) : toset(var.agent_project_ids)
}

resource "google_project_service" "agent_model_armor_api" {
  for_each           = local.floor_projects
  project            = each.value
  service            = "modelarmor.googleapis.com"
  disable_on_destroy = false
}

# Vertex AI calls Model Armor as its service agent: it needs roles/modelarmor.user on the project.
resource "google_project_service_identity" "aiplatform" {
  for_each = local.floor_projects
  provider = google-beta
  project  = each.value
  service  = "aiplatform.googleapis.com"
}

resource "google_project_iam_member" "aiplatform_model_armor_user" {
  for_each = local.floor_projects
  project  = each.value
  role     = "roles/modelarmor.user"
  member   = "serviceAccount:${google_project_service_identity.aiplatform[each.key].email}"
}

resource "google_model_armor_floorsetting" "vertex_ai" {
  for_each = local.floor_projects
  provider = google-beta
  parent   = "projects/${each.value}"
  location = "global"

  enable_floor_setting_enforcement = true
  integrated_services              = ["AI_PLATFORM"]

  # Exactly one of inspect_only / inspect_and_block may be set (false still counts as set).
  ai_platform_floor_setting {
    inspect_only         = var.model_calls == "inspect" ? true : null
    inspect_and_block    = var.model_calls == "enforce" ? true : null
    enable_cloud_logging = var.enable_cloud_logging
  }

  filter_config {
    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = var.pi_confidence # HIGH by default: avoids false positives on agent system prompts
    }

    malicious_uri_filter_settings {
      filter_enforcement = "ENABLED"
    }

    dynamic "sdp_settings" {
      for_each = var.model_calls_sdp ? [1] : []
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

  floor_setting_metadata {
    multi_language_detection {
      enable_multi_language_detection = true
    }
  }

  depends_on = [
    google_project_service.agent_model_armor_api,
    google_project_iam_member.aiplatform_model_armor_user,
  ]
}

# A floor setting is a singleton: deleting the Terraform resource only drops it from state and
# leaves the last setting active. When model_calls goes back to "off" (or a project leaves the
# list), this turns the Vertex AI integration off in that project instead.
resource "terraform_data" "vertex_ai_floor_off" {
  for_each = local.floor_projects
  input    = each.value

  provisioner "local-exec" {
    when    = destroy
    command = "bash ${path.module}/../../../_shared/scripts/model_armor_floor_off.sh"
    environment = {
      PROJECT_ID = self.input
    }
  }

  depends_on = [google_model_armor_floorsetting.vertex_ai]
}

output "vertex_ai_floor_projects" {
  value       = sort(tolist(local.floor_projects))
  description = "Agent projects with the Vertex AI Model Armor floor setting (empty when model_calls is off)."
}
