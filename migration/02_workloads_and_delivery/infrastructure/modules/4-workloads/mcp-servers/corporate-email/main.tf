# Deploy Corporate Email on Cloud Run with internal-and-load-balancing ingress
resource "google_cloud_run_v2_service" "corporate_email" {
  name     = "corporate-email-${var.environment}"
  location = var.region
  project  = var.project_id
  ingress  = "INGRESS_TRAFFIC_INTERNAL_AND_CLOUD_LIMITING" # Allows ILB & Internal Shared VPC calls

  template {
    containers {
      image = var.container_image
      ports {
        container_port = 8080
      }
      
      # Inject tracing and telemetry endpoints
      env {
        name  = "OTEL_EXPORTER_OTLP_ENDPOINT"
        value = "http://collector.telemetry.internal:4317"
      }
      env {
        name  = "ENVIRONMENT"
        value = var.environment
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

# Explicitly set Custom Audience for gateway domain mapping
resource "google_cloud_run_v2_service_custom_audiences" "audiences" {
  name     = google_cloud_run_v2_service.corporate_email.name
  location = var.region
  project  = var.project_id
  audiences = [
    "http://email.internal.gateway",
    "https://email.internal.gateway",
    "https://email.internal.gateway/mcp"
  ]
}

# IAM Invoker Binding restricting access to authorized callers only
resource "google_cloud_run_v2_service_iam_binding" "invokers" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.corporate_email.name
  role     = "roles/run.invoker"
  members  = [
    for sa in var.invoker_service_accounts : "serviceAccount:${sa}"
  ]
}

# Auto-registration of tools into Agent Registry & API Hub post-deployment
resource "null_resource" "mcp_registration" {
  triggers = {
    service_uri = google_cloud_run_v2_service.corporate_email.uri
    image_uri   = var.container_image
  }

  provisioner "local-exec" {
    command = <<EOT
      echo "📡 Registering Corporate Email MCP Server with Google Cloud Agent Registry..."
      python3 ../../../../tools_mcp/register_mcp.py \
        --project_id="${var.project_id}" \
        --region="${var.region}" \
        --server_name="corporate-email" \
        --server_url="${google_cloud_run_v2_service.corporate_email.uri}"
    EOT
  }
}
