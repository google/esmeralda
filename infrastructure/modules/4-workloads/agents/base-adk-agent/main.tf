terraform {
  required_version = ">= 1.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.0"
    }
  }
}

# -----------------------------------------------------------------------------
# 1. ATOMIC STORAGE & WORKLOAD STAGING (ONE SET OF BUCKETS PER AGENT)
# -----------------------------------------------------------------------------

# Cryptographically unique suffix for bucket naming
resource "random_id" "bucket_suffix" {
  byte_length = 4
}

# 1. Atomic Deployment Dependencies Staging Bucket (Code/Pickle/Deps)
resource "google_storage_bucket" "staging" {
  project                     = var.project_id
  name                        = "${var.project_id}-staging-${var.environment}-${random_id.bucket_suffix.hex}"
  location                    = var.region
  force_destroy               = true # Set to false in sandbox/dev environments
  uniform_bucket_level_access = true
}

# 2. Atomic Runtime Task Artifacts Bucket (Agent operational assets)
resource "google_storage_bucket" "artifacts" {
  project                     = var.project_id
  name                        = "${var.project_id}-artifacts-${var.environment}-${random_id.bucket_suffix.hex}"
  location                    = var.region
  force_destroy               = true
  uniform_bucket_level_access = true
}

# 3. Atomic Logs Offload Bucket (Long-term tracing and logging)
resource "google_storage_bucket" "logs" {
  project                     = var.project_id
  name                        = "${var.project_id}-logs-${var.environment}-${random_id.bucket_suffix.hex}"

  location                    = var.region
  force_destroy               = true
  uniform_bucket_level_access = true
}

# Declaratively define the Vertex AI Reasoning Engine master orchestrator
locals {
  # Read and decode agent.yaml if path is provided
  agent_config = try(yamldecode(file(var.agent_config_path)), {})


  # 1. Metadata
  yaml_name = try(local.agent_config.name, var.agent_name)
  yaml_desc = try(local.agent_config.description, "Root Orchestrator reasoning engine coordinating comopsable multi-agent graph flows")

  # 2. Compute Resources & Scaling
  yaml_min_inst    = try(local.agent_config.resources.min_instances, null)
  yaml_max_inst    = try(local.agent_config.resources.max_instances, null)
  yaml_concurrency = try(local.agent_config.resources.concurrency, null)
  yaml_cpu         = try(tostring(local.agent_config.resources.cpu), null)
  yaml_memory      = try(local.agent_config.resources.memory, null)

  # 3. Framework (Mapping custom aliases like "a2a" to Vertex AI's "google-adk")
  yaml_framework   = try(local.agent_config.framework == "a2a" ? "google-adk" : local.agent_config.framework, "google-adk")

  # 4. Environment Variables & Runtime Infrastructure Overrides
  yaml_env_vars = try(local.agent_config.env, {})

  runtime_overrides = {
    GCS_BUCKET      = try(google_storage_bucket.logs.name, null)
    GATEWAY_MCP_URL = try(var.gateway_mcp_url, null)
    A2A_AGENT_URL   = try(var.a2a_agent_url, null)
  }

  final_env_vars = merge(
    local.yaml_env_vars,
    { for k, v in local.runtime_overrides : k => v if v != "" && v != null }
  )

  # Split the URI to get registry, repository, and image details dynamically
  image_uri_parts = split("/", var.agent_image_uri)
  registry_host   = local.image_uri_parts[0]
  registry_project= local.image_uri_parts[1]
  registry_repo   = local.image_uri_parts[2]
  
  image_name_and_tag = split(":", local.image_uri_parts[3])
  image_name         = local.image_name_and_tag[0]
  image_tag          = length(local.image_name_and_tag) > 1 ? local.image_name_and_tag[1] : "latest"
  
  registry_region = replace(split(".", local.registry_host)[0], "-docker", "")
}


data "google_artifact_registry_docker_image" "agent_image" {
  project       = local.registry_project
  location      = local.registry_region
  repository_id = local.registry_repo
  image_name    = "${local.image_name}:${local.image_tag}"
}

resource "google_vertex_ai_reasoning_engine" "agent" {
  display_name = "${local.yaml_name}-${var.environment}"
  description  = local.yaml_desc
  region       = var.region
  project      = var.project_id
  depends_on   = [google_storage_bucket.staging]

  spec {
    agent_framework = local.yaml_framework

    container_spec {
      image_uri = "${local.registry_host}/${local.registry_project}/${local.registry_repo}/${local.image_name}@${split("@", data.google_artifact_registry_docker_image.agent_image.name)[1]}"
    }



    deployment_spec {
      min_instances         = local.yaml_min_inst
      max_instances         = local.yaml_max_inst
      container_concurrency = local.yaml_concurrency

      resource_limits = (local.yaml_cpu != null || local.yaml_memory != null) ? {
        cpu    = local.yaml_cpu
        memory = local.yaml_memory
      } : null


      dynamic "env" {
        for_each = local.final_env_vars
        content {
          name  = env.key
          value = tostring(env.value)
        }
      }

      dynamic "psc_interface_config" {
        for_each = var.network_attachment != "" ? [1] : []
        content {
          network_attachment = var.network_attachment
        }
      }
    }
  }
}




# Update agent.yaml runtime values on local filesystem or environment parameters 
# after deployment to link runtime endpoints securely
resource "null_resource" "runtime_config_sync" {
  triggers = {
    orchestrator_id = google_vertex_ai_reasoning_engine.agent.id
    gateway_mcp_url = var.gateway_mcp_url
    a2a_agent_url   = var.a2a_agent_url
  }

  provisioner "local-exec" {
    command = <<EOT
      echo "🔗 Syncing runtime gateway and agent dependencies for base-adk-agent..."
      # This mimics updating the local environment config or calling a centralized config service
      echo "GATEWAY_MCP_URL=${var.gateway_mcp_url}" > .env.runtime
      echo "A2A_AGENT_URL=${var.a2a_agent_url}" >> .env.runtime
    EOT
  }
}
