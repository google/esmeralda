terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 5.0.0"
    }
    null = {
      source  = "hashicorp/null"
      version = ">= 3.0.0"
    }
  }
}

# Pin the Cloud Run revision to the image *digest* the tag points to right now.
locals {
  image_input      = var.container_image
  image_resolvable = can(regex("^[a-z0-9-]+-docker\\.pkg\\.dev/[^/]+/[^/]+/[^/@]+:[^/@]+$", local.image_input))
  image_parts      = split("/", local.image_input)
  image_name_tag   = local.image_resolvable ? local.image_parts[3] : ""
}

data "google_artifact_registry_docker_image" "image" {
  count         = local.image_resolvable ? 1 : 0
  project       = local.image_parts[1]
  location      = replace(local.image_parts[0], "-docker.pkg.dev", "")
  repository_id = local.image_parts[2]
  image_name    = local.image_name_tag
}

locals {
  image_by_digest = local.image_resolvable ? "${join("/", slice(local.image_parts, 0, 3))}/${split(":", local.image_name_tag)[0]}@${split("@", data.google_artifact_registry_docker_image.image[0].name)[1]}" : local.image_input

  effective_register_server_name = var.register_server_name != "" ? var.register_server_name : var.service_name
  effective_display_name         = var.display_name != "" ? var.display_name : var.service_name

  default_custom_audiences = [
    "http://${var.internal_hostname}",
    "http://${var.internal_hostname}/mcp",
    "https://${var.internal_hostname}",
    "https://${var.internal_hostname}/mcp",
  ]
  effective_custom_audiences = length(var.custom_audiences) > 0 ? var.custom_audiences : local.default_custom_audiences
}

# Deploy MCP service on Cloud Run v2 with internal-and-load-balancing ingress
resource "google_cloud_run_v2_service" "service" {
  name                = "${var.service_name}-${var.environment}"
  location            = var.region
  project             = var.project_id
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = false

  custom_audiences = local.effective_custom_audiences

  template {
    service_account = var.service_account_email

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    containers {
      image = local.image_by_digest
      ports {
        container_port = 8080
      }
      resources {
        limits = {
          cpu    = var.cpu_limit
          memory = var.memory_limit
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      env {
        name  = "OTEL_EXPORTER_OTLP_ENDPOINT"
        value = "http://collector.telemetry.internal:4317"
      }
      env {
        name  = "ENVIRONMENT"
        value = var.environment
      }

      dynamic "env" {
        for_each = var.extra_env_vars
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = var.secret_env_vars
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value.secret
              version = coalesce(env.value.version, "latest")
            }
          }
        }
      }
    }

    # Direct VPC Egress: Binds Cloud Run container interface inside the Shared VPC
    vpc_access {
      network_interfaces {
        network    = var.network_id
        subnetwork = var.subnet_id
      }
      egress = "ALL_TRAFFIC"
    }
  }
}

# IAM Invoker Binding restricting access to authorized callers only
resource "google_cloud_run_v2_service_iam_binding" "invokers" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.service.name
  role     = "roles/run.invoker"
  members = [
    for sa in var.invoker_service_accounts : "serviceAccount:${sa}"
  ]
}

# Auto-registration of tools into local MCP registry post-deployment
resource "null_resource" "mcp_registration" {
  triggers = {
    service_uri = google_cloud_run_v2_service.service.uri
    image_uri   = local.image_by_digest
  }

  provisioner "local-exec" {
    command = <<EOT
      echo "📡 Registering ${local.effective_display_name} MCP Server with Google Cloud Agent Registry..."
      GIT_ROOT=$(git -C "${path.module}" rev-parse --show-toplevel 2>/dev/null) || { echo "ERROR: cannot locate the repository root from ${path.module}; run Terragrunt from a git checkout of this repository." >&2; exit 1; }
      python3 "$GIT_ROOT/apps/services/register_mcp.py" \
        --project_id="${var.project_id}" \
        --region="${var.region}" \
        --server_name="${local.effective_register_server_name}" \
        --server_url="${google_cloud_run_v2_service.service.uri}"
    EOT
  }
}

# MCP server entry in the central governance Agent Registry. The Agent Gateway only lets agents
# egress to registered hosts, so this is deploy-time, env-specific config (not part of the build).
resource "google_agent_registry_service" "governance_mcp" {
  provider     = google-beta
  project      = var.governance_project_id
  location     = var.region
  service_id   = var.service_name
  display_name = var.service_name
  description  = var.description

  interfaces {
    url              = "https://${var.internal_hostname}/mcp"
    protocol_binding = "JSONRPC"
  }

  mcp_server_spec {
    type    = "TOOL_SPEC"
    content = file(var.tools_spec_path)
  }
}
