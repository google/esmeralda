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
  name                        = "${var.project_id}-${var.agent_name}-staging-${var.environment}-${random_id.bucket_suffix.hex}"
  location                    = var.region
  force_destroy               = true # Set to false in sandbox/dev environments
  uniform_bucket_level_access = true
}

# 2. Atomic Runtime Task Artifacts Bucket (Agent operational assets)
resource "google_storage_bucket" "artifacts" {
  project                     = var.project_id
  name                        = "${var.project_id}-${var.agent_name}-artifacts-${var.environment}-${random_id.bucket_suffix.hex}"
  location                    = var.region
  force_destroy               = true
  uniform_bucket_level_access = true
}

# 3. Atomic Logs Offload Bucket (Long-term tracing and logging)
resource "google_storage_bucket" "logs" {
  project                     = var.project_id
  name                        = "${var.project_id}-${var.agent_name}-logs-${var.environment}-${random_id.bucket_suffix.hex}"
  location                    = var.region
  force_destroy               = true
  uniform_bucket_level_access = true
}

# Upload serialized agent.pkl to GCS Staging Bucket
resource "google_storage_bucket_object" "agent_pickle" {
  name   = "agents/${var.agent_name}-${var.environment}/agent.pkl"
  bucket = google_storage_bucket.staging.name
  source = var.pickle_object_path
}

# Upload requirements.txt dependencies mapping
resource "google_storage_bucket_object" "requirements" {
  name   = "agents/${var.agent_name}-${var.environment}/requirements.txt"
  bucket = google_storage_bucket.staging.name
  source = var.requirements_path
}

# Upload compiled dependencies tarball
resource "google_storage_bucket_object" "dependencies" {
  name   = "agents/${var.agent_name}-${var.environment}/dependencies.tar.gz"
  bucket = google_storage_bucket.staging.name
  source = var.dependencies_path
}

# Declaratively define the Vertex AI Reasoning Engine master orchestrator
resource "google_vertex_ai_reasoning_engine" "agent" {
  display_name = "${var.agent_name}-${var.environment}"
  description  = "Root Orchestrator reasoning engine coordinating comopsable multi-agent graph flows"
  region       = var.region
  project      = var.project_id
  depends_on   = [google_storage_bucket.staging]

  spec {
    agent_framework = "google-adk"
    service_account = var.agent_service_account

    package_spec {
      python_version           = "3.12"
      pickle_object_gcs_uri    = "gs://${google_storage_bucket.staging.name}/${google_storage_bucket_object.agent_pickle.name}"
      requirements_gcs_uri     = "gs://${google_storage_bucket.staging.name}/${google_storage_bucket_object.requirements.name}"
      dependency_files_gcs_uri = "gs://${google_storage_bucket.staging.name}/${google_storage_bucket_object.dependencies.name}"
    }

    # Connect reasoning engine container inside private VPC via PSC Network Attachment
    deployment_spec {
      psc_interface_config {
        network_attachment = var.network_attachment
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
