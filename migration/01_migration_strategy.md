# Esmeralda Migration Strategy & Greenfield Toggles

This document describes the high-level mapping from legacy resources to the new Terragrunt structures, and details how the Greenfield vs. Brownfield BYOInfra switches are implemented.

## 1. Monolithic to Terragrunt Mapping

## 3. Updated Component Migration Mapping

| Monolithic Component | Target Terragrunt Stage | Target GCP Project | Dependency Inputs |
| :--- | :--- | :--- | :--- |
| `module.foundation` (Core APIs) | **`stage-1-projects`** | *Pre-workloads* | `billing_account`, `byo_net_host_project`, `byo_gateway_project`, `byo_governance_project` |
| `module.networking` | **`stage-2-networking`** | `prj-net-host` | `net_host_project_id` (from stage-1), `governance_project_id` (from stage-1) |
| IAM, SAs & Encryption Keys | **`stage-3-security`** | Split across all | Project IDs from Stage 1 |
| Ingress / Load Balancer | **`stage-4-workloads/gateway`** | `prj-gateway` | `gateway_project_id` (stage-1), `network_id` (stage-2) |
| DMS MCP Service | **`stage-4-workloads/mcp-servers/mcp-dms`** | `prj-esmeralda-mcps` | `mcps_project_id` (stage-1), `subnet_id` (stage-2) |
| Calculator MCP Service | **`stage-4-workloads/mcp-servers/mcp-calculator`** | `prj-esmeralda-mcps` | `mcps_project_id` (stage-1), `subnet_id` (stage-2) |
| Cloud SQL & A2A Agent | **`stage-4-workloads/agents/a2a-agent`** | `prj-esmeralda-a2a-agents` | `a2a_project_id` (stage-1), `vpc_id` (stage-2), `subnet_id` (stage-2) |
| Root / Orchestrator Agent | **`stage-4-workloads/agents/base-adk-agent`** | `prj-esmeralda-root-agent` | `root_project_id` (stage-1), `a2a_agent_endpoint_url` (from `a2a-agent`), tool endpoints |
| Security, Governance, & Telemetry | **`stage-3-security`** | `prj-esmeralda-governance` | `governance_project_id` (stage-1), sinks across all 6 projects |

---



## 2. Greenfield vs. Brownfield Toggle Architecture

## 6. Greenfield vs. Brownfield (BYOInfra) Toggle Design

To allow seamless deployment inside enterprise client environments with pre-existing resources, the architecture implements the **BYOInfra Pattern** natively using Terragrunt's skip parameters and input-fallbacks:

### A. The Client's Environment Parameters (`live/client-prod/env.yaml`)
The client declares their pre-existing resources and toggles the dynamic skip flags:

```yaml
# infrastructure/live/client-prod/env.yaml
locals {
  environment         = "prod"
  project_prefix      = "client"
  region              = "us-central1"

  # 🔌 BYO INFRA TOGGLES: Client already has host network and gateway projects!
  byo_net_host_project = true
  byo_gateway_project  = true
  byo_governance_project = false
  byo_networking       = true
  byo_apigee           = true

  # 1. Existing project links
  existing_net_host_project = "prj-client-shared-net-host"
  existing_governance_project = ""
  existing_gateway_project  = "prj-client-apigee-ingress"
  existing_vpc_id           = "projects/prj-client-shared-net-host/global/networks/vpc-prod-shared"
  existing_subnet_id        = "projects/prj-client-shared-net-host/regions/us-central1/subnetworks/sb-prod-gke"

  # 🛒 THE PRODUCTS THEY WANT ESMERALDA TO CREATE FROM SCRATCH:
  gateway_product     = "apigee" # Point to existing Apigee gateway settings
  database_product    = "cloud-sql"
}
```

### B. Dynamically Skipping Stage 2 (`live/client-prod/stage-2-networking/terragrunt.hcl`)
The networking configuration skips compilation and returns instantly if `byo_networking` is configured:

```hcl
# infrastructure/live/client-prod/stage-2-networking/terragrunt.hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../modules//2-networking"
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
}

# Skips execution entirely if networking is pre-configured
skip = local.env_vars.locals.byo_networking
```

### C. Downstream Fallback Lookup (`live/client-prod/stage-4-workloads/agents/a2a-agent/terragrunt.hcl`)
Downstream persistence components safely switch their inputs between Stage 2 outputs or `env.yaml` static resource IDs based on the active flag:

```hcl
# infrastructure/live/client-prod/stage-4-workloads/agents/a2a-agent/terragrunt.hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../../../modules//4-workloads/agents/a2a-agent"
}

locals {
  env_vars       = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  byo_networking = lookup(local.env_vars.locals, "byo_networking", false)
}

dependency "projects" {
  config_path = "../../../stage-1-projects"
}

dependency "security" {
  config_path = "../../../stage-3-security"
}

dependency "networking" {
  config_path = "../../../stage-2-networking"
  
  # Avoid running output lookups on skipped modules
  skip_outputs = local.byo_networking
  
  # Satisfy parser during evaluation with mock variables
  mock_outputs = {
    network_id = local.env_vars.locals.existing_vpc_id
    subnet_id  = local.env_vars.locals.existing_subnet_id
  }
}

inputs = {
  # Dynamically fetches workload project IDs provisioned dynamically by Esmeralda's Stage 1!
  project_id            = dependency.projects.outputs.a2a_project_id
  region                = local.env_vars.locals.region
  agent_service_account = dependency.security.outputs.a2a_agent_sa_email
  
  # Dynamic Fallback: Use client's existing VPC if BYO is active, else use dependency outputs
  vpc_id                = local.byo_networking ? local.env_vars.locals.existing_vpc_id  : dependency.networking.outputs.network_id
  subnet_id             = local.byo_networking ? local.env_vars.locals.existing_subnet_id : dependency.networking.outputs.subnet_id
  
  database_name         = "a2a_tasks"
  enable_iam_user       = true
}
```

---

