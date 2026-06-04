# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

terraform {
  required_version = ">= 1.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Upload serialized agent pickle to GCS staging bucket
resource "google_storage_bucket_object" "agent_pickle" {
  name   = "agents/${var.agent_name}/agent.pkl"
  bucket = var.staging_bucket_name
  source = var.pickle_object_path
}

# Upload requirements.txt to GCS staging bucket
resource "google_storage_bucket_object" "requirements" {
  name   = "agents/${var.agent_name}/requirements.txt"
  bucket = var.staging_bucket_name
  source = var.requirements_path
}

# Upload dependencies.tar.gz to GCS staging bucket
resource "google_storage_bucket_object" "dependencies" {
  name   = "agents/${var.agent_name}/dependencies.tar.gz"
  bucket = var.staging_bucket_name
  source = var.dependencies_path
}

# Declaratively define the Vertex AI Reasoning Engine agent
resource "google_vertex_ai_reasoning_engine" "agent" {
  display_name = var.agent_name
  description  = "Vertex AI Reasoning Engine deployed declaratively via Terraform"
  region       = var.region

  spec {
    agent_framework = "google-adk"
    service_account = var.service_account

    package_spec {
      python_version           = "3.12"
      pickle_object_gcs_uri    = "gs://${var.staging_bucket_name}/${google_storage_bucket_object.agent_pickle.name}"
      requirements_gcs_uri     = "gs://${var.staging_bucket_name}/${google_storage_bucket_object.requirements.name}"
      dependency_files_gcs_uri = "gs://${var.staging_bucket_name}/${google_storage_bucket_object.dependencies.name}"
    }

    # Include Private Service Connect Network Attachment if configured
    dynamic "deployment_spec" {
      for_each = var.network_attachment != "" ? [1] : []
      content {
        psc_interface_config {
          network_attachment = var.network_attachment
        }
      }
    }
  }
}

