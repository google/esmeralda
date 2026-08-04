# infrastructure/modules/4-workloads/agent_registry/main.tf
# ==============================================================================
# ESMERALDA AGENT REGISTRY AUTO-REGISTRATION MODULE
# ==============================================================================

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 5.0"
    }
  }
}

variable "a2a_project_id" {
  type        = string
  description = "A2A Agent Project ID"
}

variable "mcps_project_id" {
  type        = string
  description = "Corporate MCP Services Project ID"
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "Primary deployment region"
}

# 1. Register Income Verification FastMCP Microservice in Agent Registry
resource "google_agent_registry_service" "income_verification" {
  provider     = google-beta
  project      = var.mcps_project_id
  location     = var.region
  service_id   = "income-verification"
  display_name = "Income Verification Microservice"
  description  = "FastMCP service providing applicant employment and income verification tools"

  interfaces {
    url              = "http://income-verification.esmeralda.internal:8002/mcp"
    protocol_binding = "JSONRPC"
  }

  endpoint_spec {
    type = "NO_SPEC"
  }
}

# 2. Register Corporate Email FastMCP Microservice in Agent Registry
resource "google_agent_registry_service" "corporate_email" {
  provider     = google-beta
  project      = var.mcps_project_id
  location     = var.region
  service_id   = "corporate-email"
  display_name = "Corporate Email Microservice"
  description  = "FastMCP service providing internal email verification tools"

  interfaces {
    url              = "http://corporate-email.esmeralda.internal:8001/mcp"
    protocol_binding = "JSONRPC"
  }

  endpoint_spec {
    type = "NO_SPEC"
  }
}

# 3. Register Legacy DMS FastMCP Microservice in Agent Registry
resource "google_agent_registry_service" "legacy_dms" {
  provider     = google-beta
  project      = var.mcps_project_id
  location     = var.region
  service_id   = "legacy-dms"
  display_name = "Legacy DMS Microservice"
  description  = "FastMCP service providing legacy document management and retrieval tools"

  interfaces {
    url              = "http://legacy-dms.esmeralda.internal:8003/mcp"
    protocol_binding = "JSONRPC"
  }

  endpoint_spec {
    type = "NO_SPEC"
  }
}

# 4. Register Mortgage A2A Agent in Agent Registry
resource "google_agent_registry_service" "mortgage_a2a_agent" {
  provider     = google-beta
  project      = var.a2a_project_id
  location     = var.region
  service_id   = "mortgage-a2a-agent"
  display_name = "Mortgage Assistant A2A Agent"
  description  = "A2A Agent providing domain mortgage processing capabilities"

  interfaces {
    url              = "http://a2a-mortgage-agent.esmeralda.internal:8000/a2a"
    protocol_binding = "HTTP_JSON"
  }

  endpoint_spec {
    type = "NO_SPEC"
  }
}

output "registered_services" {
  value = {
    income_verification = google_agent_registry_service.income_verification.id
    corporate_email     = google_agent_registry_service.corporate_email.id
    legacy_dms          = google_agent_registry_service.legacy_dms.id
    mortgage_a2a_agent  = google_agent_registry_service.mortgage_a2a_agent.id
  }
  description = "IDs of registered Agent Registry services"
}
