# Deploy Income Verification on Cloud Run with internal-and-load-balancing ingress
resource "google_cloud_run_v2_service" "income_verification" {
  name     = "income-verification-${var.environment}"
  location = var.region
  project  = var.project_id
  ingress  = "INGRESS_TRAFFIC_INTERNAL_AND_CLOUD_LIMITING"

  template {
    containers {
      image = var.container_image
      ports {
        container_port = 8080
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

# Explicitly set Custom Audience for gateway domain mapping
resource "google_cloud_run_v2_service_custom_audiences" "audiences" {
  name     = google_cloud_run_v2_service.income_verification.name
  location = var.region
  project  = var.project_id
  audiences = [
    "http://income-verification.internal.gateway",
    "https://income-verification.internal.gateway",
    "https://income-verification.internal.gateway/mcp"
  ]
}

# IAM Invoker Binding restricting access to authorized callers only
resource "google_cloud_run_v2_service_iam_binding" "invokers" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.income_verification.name
  role     = "roles/run.invoker"
  members  = [
    for sa in var.invoker_service_accounts : "serviceAccount:${sa}"
  ]
}

# Auto-registration of tools into Agent Registry & API Hub post-deployment
resource "null_resource" "mcp_registration" {
  triggers = {
    service_uri = google_cloud_run_v2_service.income_verification.uri
    image_uri   = var.container_image
  }

  provisioner "local-exec" {
    command = <<EOT
      echo "📡 Registering Income Verification MCP Server with Google Cloud Agent Registry..."
      python3 ../../../../tools_mcp/register_mcp.py \
        --project_id="${var.project_id}" \
        --region="${var.region}" \
        --server_name="income-verification-api" \
        --server_url="${google_cloud_run_v2_service.income_verification.uri}"
    EOT
  }
}
