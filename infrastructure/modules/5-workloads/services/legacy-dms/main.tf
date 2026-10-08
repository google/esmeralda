

# Deploy Legacy DMS on Cloud Run with internal-and-load-balancing ingress
resource "google_cloud_run_v2_service" "legacy_dms" {
  name                = "legacy-dms-${var.environment}"
  location            = var.region
  project             = var.project_id
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = false

  custom_audiences = [
    "http://dms.internal.gateway",
    "https://dms.internal.gateway",
    "https://dms.internal.gateway/mcp",
    "http://legacy-dms.esmeralda.internal",
    "http://legacy-dms.esmeralda.internal/mcp",
    "https://legacy-dms.esmeralda.internal",
    "https://legacy-dms.esmeralda.internal/mcp"
  ]

  template {
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
    }

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
  name     = google_cloud_run_v2_service.legacy_dms.name
  role     = "roles/run.invoker"
  members = [
    for sa in var.invoker_service_accounts : "serviceAccount:${sa}"
  ]
}

# Auto-registration of tools into Agent Registry & API Hub post-deployment
resource "null_resource" "mcp_registration" {
  triggers = {
    service_uri = google_cloud_run_v2_service.legacy_dms.uri
    image_uri   = local.image_by_digest
  }

  provisioner "local-exec" {
    command = <<EOT
      echo "📡 Registering Legacy DMS MCP Server with Google Cloud Agent Registry..."
      GIT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$HOME/codigos/esmeralda"); python3 "$GIT_ROOT/apps/services/register_mcp.py" \
        --project_id="${var.project_id}" \
        --region="${var.region}" \
        --server_name="legacy-dms" \
        --server_url="${google_cloud_run_v2_service.legacy_dms.uri}"
    EOT
  }
}

# MCP server entry in the central governance Agent Registry. The Agent Gateway only lets agents
# egress to registered hosts, so this is deploy-time, env-specific config (not part of the build).
resource "google_agent_registry_service" "governance_mcp" {
  provider     = google-beta
  project      = var.governance_project_id
  location     = var.region
  service_id   = "legacy-dms"
  display_name = "legacy-dms"
  description  = "FastMCP service providing legacy document management and retrieval tools"

  interfaces {
    url              = "https://${var.internal_hostname}/mcp"
    protocol_binding = "JSONRPC"
  }

  mcp_server_spec {
    type    = "TOOL_SPEC"
    content = file(var.tools_spec_path)
  }
}
