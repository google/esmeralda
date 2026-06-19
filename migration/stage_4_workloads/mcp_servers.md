# Stage 4 Workloads: Composable MCP Server Tools

This module handles the packaging, containerization, and private execution of corporate backend tools via MCP.

#### 7.4.2 Composable MCP Server Tools (`modules/4-workloads/mcp-servers/`)

To achieve complete modularity and operational flexibility, each Model Context Protocol (MCP) server from the `/tools_mcp/servers/` directory is isolated into a standalone sub-module under `/modules/4-workloads/mcp-servers/`. This allows platform operators to independently update, patch, and redeploy specific tool services without affecting other workloads or gateways.

We define three self-contained sub-modules:
1.  **Corporate Email Tool Server** (`mcp-servers/corporate-email/`)
2.  **Income Verification Tool Server** (`mcp-servers/income-verification/`)
3.  **Legacy DMS Tool Server** (`mcp-servers/legacy-dms/`)

To preserve the zero-trust security paradigm established in Stage 3, each MCP server is deployed to Cloud Run with `no-allow-unauthenticated` status, bound directly to the Shared VPC network via Direct VPC Egress, and protected by Cloud Run IAM invoker bindings. Furthermore, each module incorporates post-deployment registration blocks to dynamically catalog available tools in the GCP Agent Registry and API Hub.

```text
infrastructure/modules/4-workloads/mcp-servers/
├── corporate-email/
│   ├── main.tf
│   ├── variables.tf
│   └── outputs.tf
├── income-verification/
│   ├── main.tf
│   ├── variables.tf
│   └── outputs.tf
└── legacy-dms/
    ├── main.tf
    ├── variables.tf
    └── outputs.tf
```

---

##### A. Sub-Module 1: Corporate Email Server (`mcp-servers/corporate-email/`)

This module deploys the `corporate-email` tool server on Cloud Run. It mounts the service directly inside the Shared VPC to resolve downstream targets privately, locks down the service's HTTP ingress, and grants invoker privileges exclusively to designated agent service accounts.

###### 1. Variables Specification (`variables.tf`)
```hcl
variable "project_id" {
  description = "The GCP project ID allocated for remote MCP servers (prj-esmeralda-mcps)"
  type        = string
}

variable "region" {
  description = "The region where the Cloud Run service will be deployed"
  type        = string
  default     = "us-central1"
}

variable "network_id" {
  description = "The self-link of the Shared VPC"
  type        = string
}

variable "subnet_id" {
  description = "The self-link of the backend subnetwork inside the Shared VPC"
  type        = string
}

variable "container_image" {
  description = "The GCR/Artifact Registry container image URI for corporate-email"
  type        = string
}

variable "invoker_service_accounts" {
  description = "The list of service account emails authorized to invoke this MCP server"
  type        = list(string)
}

variable "environment" {
  description = "The active deployment environment name"
  type        = string
  default     = "dev"
}
```

###### 2. Implementation Blueprint (`main.tf`)
```hcl
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
    "http://email.internal.gateway/mcp"
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
```

###### 3. Outputs Specification (`outputs.tf`)
```hcl
output "service_uri" {
  description = "The baseline Cloud Run system-generated URL of the Corporate Email MCP server"
  value       = google_cloud_run_v2_service.corporate_email.uri
}

output "service_name" {
  description = "The local display name of the deployed Cloud Run service"
  value       = google_cloud_run_v2_service.corporate_email.name
}
```

---

##### B. Sub-Module 2: Income Verification Server (`mcp-servers/income-verification/`)

This module deploys the `income-verification` tool server. It integrates the verification server with regional telemetry logs and enforces strict IAM token-based OIDC protection.

###### 1. Variables Specification (`variables.tf`)
```hcl
variable "project_id" {
  description = "The GCP project ID allocated for remote MCP servers (prj-esmeralda-mcps)"
  type        = string
}

variable "region" {
  description = "The region where the Cloud Run service will be deployed"
  type        = string
  default     = "us-central1"
}

variable "network_id" {
  description = "The self-link of the Shared VPC"
  type        = string
}

variable "subnet_id" {
  description = "The self-link of the backend subnetwork inside the Shared VPC"
  type        = string
}

variable "container_image" {
  description = "The GCR/Artifact Registry container image URI for income-verification"
  type        = string
}

variable "invoker_service_accounts" {
  description = "The list of service account emails authorized to invoke this MCP server"
  type        = list(string)
}

variable "environment" {
  description = "The active deployment environment name"
  type        = string
  default     = "dev"
}
```

###### 2. Implementation Blueprint (`main.tf`)
```hcl
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
    "http://income-verification.internal.gateway/mcp"
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
```

###### 3. Outputs Specification (`outputs.tf`)
```hcl
output "service_uri" {
  description = "The baseline Cloud Run system-generated URL of the Income Verification MCP server"
  value       = google_cloud_run_v2_service.income_verification.uri
}

output "service_name" {
  description = "The local display name of the deployed Cloud Run service"
  value       = google_cloud_run_v2_service.income_verification.name
}
```

---

##### C. Sub-Module 3: Legacy DMS Server (`mcp-servers/legacy-dms/`)

This module deploys the `legacy-dms` (Document Management System) tool server on Cloud Run. It secures interactions with the core asset store and registers with API Hub.

###### 1. Variables Specification (`variables.tf`)
```hcl
variable "project_id" {
  description = "The GCP project ID allocated for remote MCP servers (prj-esmeralda-mcps)"
  type        = string
}

variable "region" {
  description = "The region where the Cloud Run service will be deployed"
  type        = string
  default     = "us-central1"
}

variable "network_id" {
  description = "The self-link of the Shared VPC"
  type        = string
}

variable "subnet_id" {
  description = "The self-link of the backend subnetwork inside the Shared VPC"
  type        = string
}

variable "container_image" {
  description = "The GCR/Artifact Registry container image URI for legacy-dms"
  type        = string
}

variable "invoker_service_accounts" {
  description = "The list of service account emails authorized to invoke this MCP server"
  type        = list(string)
}

variable "environment" {
  description = "The active deployment environment name"
  type        = string
  default     = "dev"
}
```

###### 2. Implementation Blueprint (`main.tf`)
```hcl
# Deploy Legacy DMS on Cloud Run with internal-and-load-balancing ingress
resource "google_cloud_run_v2_service" "legacy_dms" {
  name     = "legacy-dms-${var.environment}"
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
  name     = google_cloud_run_v2_service.legacy_dms.name
  location = var.region
  project  = var.project_id
  audiences = [
    "http://dms.internal.gateway",
    "http://dms.internal.gateway/mcp"
  ]
}

# IAM Invoker Binding restricting access to authorized callers only
resource "google_cloud_run_v2_service_iam_binding" "invokers" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.legacy_dms.name
  role     = "roles/run.invoker"
  members  = [
    for sa in var.invoker_service_accounts : "serviceAccount:${sa}"
  ]
}

# Auto-registration of tools into Agent Registry & API Hub post-deployment
resource "null_resource" "mcp_registration" {
  triggers = {
    service_uri = google_cloud_run_v2_service.legacy_dms.uri
    image_uri   = var.container_image
  }

  provisioner "local-exec" {
    command = <<EOT
      echo "📡 Registering Legacy DMS MCP Server with Google Cloud Agent Registry..."
      python3 ../../../../tools_mcp/register_mcp.py \
        --project_id="${var.project_id}" \
        --region="${var.region}" \
        --server_name="legacy-dms" \
        --server_url="${google_cloud_run_v2_service.legacy_dms.uri}"
    EOT
  }
}
```

###### 3. Outputs Specification (`outputs.tf`)
```hcl
output "service_uri" {
  description = "The baseline Cloud Run system-generated URL of the Legacy DMS MCP server"
  value       = google_cloud_run_v2_service.legacy_dms.uri
}

output "service_name" {
  description = "The local display name of the deployed Cloud Run service"
  value       = google_cloud_run_v2_service.legacy_dms.name
}
```

###### 4. Composed Inputs-Outputs Mapping Matrix

To help operators configure their Terragrunt dependency blocks, this matrix maps the variable bindings across the gateway layer and the composable MCP tool servers:

| MCP Sub-module | Input Source (`terragrunt.hcl`) | Authorized Invokers (`invoker_service_accounts`) | Regional Custom Audience Endpoint | Matches Route Path in ILB |
| :--- | :--- | :--- | :--- | :--- |
| **`corporate-email`** | Output of `prj-esmeralda-mcps` and `base-adk-agent` | `base-adk-agent-sa` email, `test-vm-sa` email | `http://email.internal.gateway/mcp` | `/email/*`, `/email/mcp` |
| **`income-verification`** | Output of `prj-esmeralda-mcps` and `base-adk-agent` | `base-adk-agent-sa` email, `test-vm-sa` email | `http://income-verification.internal.gateway/mcp` | `/income-verification/*` |
| **`legacy-dms`** | Output of `prj-esmeralda-mcps` and `base-adk-agent` | `base-adk-agent-sa` email, `test-vm-sa` email | `http://dms.internal.gateway/mcp` | `/dms/*`, `/dms/mcp` |

---

