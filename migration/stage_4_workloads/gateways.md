# Stage 4 Workloads: Swappable Ingress Gateways

This directory houses swappable entry points for external and internal traffic, including Apigee proxies, Kong on private Cloud Run, or an Internal Load Balancer.

### 7.4 Stage 4: `modules/4-workloads/` Specification

Stage 4 transitions Esmeralda from foundational infrastructure (projects, networking, security) into the **Composable Workloads Space**. It acts as a modular "Product Catalog Shelf", allowing platform operators to selectively deploy gateways, MCP tool API servers, and ADK reasoning engine agents onto the pre-existing foundational projects.

This design enforces the **Gateway Adapter Pattern**: downstream agents (the reasoning engine workloads) remain completely agnostic of *how* ingress is routed or which API gateway is active. They simply interact with a standard set of interface variables, allowing seamless toggling between different gateway products.

---

#### 7.4.1 Swappable Gateway Ingress Adapters

We define three distinct gateway options under `/modules/4-workloads/gateways/`. Platform engineers can select their desired adapter by changing the `source` path of their live gateway Terragrunt configuration:

```text
infrastructure/modules/4-workloads/gateways/
├── apigee/                 # Option A: Enterprise-grade Apigee X Ingress
├── kong/                   # Option B: Lightweight, serverless Kong Gateway on Cloud Run
└── ilb/                    # Option C: Direct GCP Regional L7 Internal HTTP(S) Load Balancer
```

##### The Swappable Gateway Contract

To maintain complete interchangeability, all three gateway sub-modules **must accept the exact same input variables** and **expose the exact same output variables**. This contract enforces the **Gateway Adapter Pattern**: downstream agents (the reasoning engine workloads) remain completely agnostic of *how* ingress is routed or which API gateway is active.

```hcl
# --- INPUT VARIABLES CONTRACT ---
variable "project_id" {
  description = "The GCP project ID allocated for gateway ingress (prj-gateway)"
  type        = string
}

variable "region" {
  description = "The region where gateway workloads are deployed"
  type        = string
}

variable "vpc_id" {
  description = "The self-link of the central Shared VPC network"
  type        = string
}

variable "subnet_id" {
  description = "The self-link of the gateway/backend subnetwork"
  type        = string
}

variable "agent_endpoints" {
  description = "A map of logical agent names to their dynamic Vertex AI Reasoning Engine configuration"
  type = map(object({
    logical_name = string
    engine_id    = string
    endpoint_url = string
  }))
}

variable "environment" {
  description = "The active deployment environment (e.g., dev, qa, prod)"
  type        = string
  default     = "dev"
}

# --- OUTPUTS CONTRACT ---
output "gateway_ingress_ip" {
  description = "The internal private VIP of the gateway load balancer or proxy endpoint"
  value       = string
}

output "gateway_agent_ingress_host" {
  description = "The base private DNS zone managed by the gateway (e.g., esmeralda.internal)"
  value       = string
}
```

---

##### A. Option A: Apigee X Enterprise Gateway (`gateways/apigee/`)

The Apigee X adapter implements an enterprise-grade API management plane. It provisions an Apigee Organization, binds an Apigee Environment to the gateway project, creates an Environment Group to register hostnames (`*.esmeralda.internal`), and hooks up the Apigee runtime plane to the Shared VPC via Private Service Connect (PSC).

To handle dynamic Vertex AI Reasoning Engine IDs (which change on every deployment), the Apigee adapter populates an **Apigee Key Value Map (KVM)** using Terraform's `null_resource` local-exec trigger. At runtime, an Apigee Proxy intercepts `*.esmeralda.internal`, extracts the logical agent name from the host header, looks up the target endpoint URL in the KVM, performs Google Service Account token exchange, and proxies the query to Vertex AI.

###### 1. Variables Specification (`variables.tf`)
Includes the standard Swappable Gateway variables contract defined above.

###### 2. Implementation Blueprint (`main.tf`)
```hcl
resource "google_apigee_organization" "apigee_org" {
  analytics_region   = var.region
  project_id         = var.project_id
  authorized_network = var.vpc_id
}

resource "google_apigee_environment" "apigee_env" {
  name         = var.environment
  org_id       = google_apigee_organization.apigee_org.id
  description  = "Esmeralda ${var.environment} Apigee Environment"
  display_name = var.environment
}

resource "google_apigee_envgroup" "apigee_envgroup" {
  name      = "esmeralda-group-${var.environment}"
  org_id    = google_apigee_organization.apigee_org.id
  hostnames = ["*.esmeralda.internal"]
}

resource "google_apigee_envgroup_attachment" "env_to_group" {
  envgroup_id = google_apigee_envgroup.apigee_envgroup.id
  environment = google_apigee_environment.apigee_env.name
}

resource "google_apigee_instance" "apigee_instance" {
  name                 = "apigee-instance-${var.environment}"
  org_id               = google_apigee_organization.apigee_org.id
  location             = var.region
  peering_cidr_range   = "10.12.0.0/22"
}

resource "google_apigee_instance_attachment" "env_to_instance" {
  instance_id = google_apigee_instance.apigee_instance.id
  environment = google_apigee_environment.apigee_env.name
}

# Key Value Map to store logical-to-dynamic engine endpoint mappings
resource "google_apigee_keyvaluemap" "agent_routes" {
  org_id = google_apigee_organization.apigee_org.id
  env_id = google_apigee_environment.apigee_env.id
  name   = "agent-routes"
}

# Dynamically populate KVM entries using local-exec (since the Google API doesn't expose standard keyvaluemap entries as separate Terraform resources)
resource "null_resource" "populate_apigee_kvm" {
  for_each = var.agent_endpoints

  triggers = {
    engine_id    = each.value.engine_id
    endpoint_url = each.value.endpoint_url
  }

  provisioner "local-exec" {
    command = <<EOT
      curl -X POST -H "Authorization: Bearer $(gcloud auth print-access-token)" \
        -H "Content-Type: application/json" \
        "https://apigee.googleapis.com/v1/organizations/${google_apigee_organization.apigee_org.name}/environments/${google_apigee_environment.apigee_env.name}/keyvaluemaps/agent-routes/entries" \
        -d '{"name": "${each.value.logical_name}", "value": "${each.value.endpoint_url}"}' \
        || curl -X PUT -H "Authorization: Bearer $(gcloud auth print-access-token)" \
        -H "Content-Type: application/json" \
        "https://apigee.googleapis.com/v1/organizations/${google_apigee_organization.apigee_org.name}/environments/${google_apigee_environment.apigee_env.name}/keyvaluemaps/agent-routes/entries/${each.value.logical_name}" \
        -d '{"name": "${each.value.logical_name}", "value": "${each.value.endpoint_url}"}'
    EOT
  }
}
```

###### 3. Dynamic Routing & Auth Policies (`policies/`)

Inside the Apigee API Proxy (`/apiproxy/policies/`), we implement:
*   **KVM-Lookup.xml** (extracts the sub-domain e.g. `a2a-agent` from `request.header.host`, looks up target in KVM):
```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<KeyValueMapOperations async="false" continueOnError="false" enabled="true" name="KVM-Lookup">
    <DisplayName>KVM-Lookup</DisplayName>
    <Get assignTo="target.url">
        <Key>
            <Parameter ref="request.header.host.prefix"/> <!-- Logic inside Proxy Flow extracts prefix e.g. a2a-agent -->
        </Key>
    </Get>
    <Scope>environment</Scope>
</KeyValueMapOperations>
```

*   **Generate-Bearer-Token.xml** (uses Google Application Default Credentials or the Apigee Service Account's Identity Token to authenticate with Vertex AI):
```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<AssignMessage async="false" continueOnError="false" enabled="true" name="Generate-Bearer-Token">
    <DisplayName>Generate Bearer Token</DisplayName>
    <Set>
        <Headers>
            <Header name="Authorization">Bearer {private.gcp.idToken}</Header>
        </Set>
    </Set>
    <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</AssignMessage>
```

###### 4. Outputs Specification (`outputs.tf`)
```hcl
output "gateway_ingress_ip" {
  description = "The internal VIP of the Private Service Connect endpoint routing to Apigee"
  value       = "10.10.1.50" # Reserved IP for Apigee PSC endpoint in Shared VPC
}

output "gateway_agent_ingress_host" {
  description = "The base private DNS zone managed by Apigee"
  value       = "esmeralda.internal"
}
```

---

##### B. Option B: Lightweight Kong Gateway on Cloud Run (`gateways/kong/`)

The Kong adapter deploys the lightweight, open-source Kong Gateway container in a DB-less serverless mode on Cloud Run inside the gateway project (`prj-gateway`). It uses Secret Manager to load Kong's declarative configuration routing rules and binds to the central Shared VPC via Direct VPC Egress for low-latency, private routing to downstream agents.

To support swappability, we compile the DB-less `kong.yml` dynamically inside Terraform using the `templatefile()` function, mapping each logical name from `var.agent_endpoints` to its dynamic Vertex AI Reasoning Engine URL. We also configure Kong's **GCP Service Account plugin** to transparently inject the Google OIDC tokens required to authorize calls to private Vertex AI reasoning engine endpoints.

###### 1. Variables Specification (`variables.tf`)
Includes the standard Swappable Gateway variables contract defined above, plus:
```hcl
variable "kong_image" {
  description = "The Container Image URL of Kong Gateway"
  type        = string
  default     = "kong:latest"
}
```

###### 2. Implementation Blueprint (`main.tf`)
```hcl
# Compile the declarative kong.yml file dynamically based on agent_endpoints
locals {
  kong_config = templatefile("${path.module}/templates/kong.yml.tpl", {
    agent_endpoints = var.agent_endpoints
  })
}

# Securely store the compiled Kong declarative configuration inside Secret Manager
resource "google_secret_manager_secret" "kong_config" {
  secret_id = "kong-config-${var.environment}"
  project   = var.project_id
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "kong_config" {
  secret      = google_secret_manager_secret.kong_config.id
  secret_data = local.kong_config
}

# Create a dedicated Cloud Run Service Account for Kong
resource "google_service_account" "kong_sa" {
  account_id   = "kong-gateway-sa-${var.environment}"
  display_name = "Kong Gateway Service Account"
  project      = var.project_id
}

# Grant the Service Account permissions to fetch tokens and call Vertex AI Reasoning Engines
resource "google_project_iam_member" "kong_vertex_access" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.kong_sa.email}"
}

# Deploy Kong Gateway on Cloud Run with internal-only ingress
resource "google_cloud_run_v2_service" "kong_gateway" {
  name     = "kong-gateway-${var.environment}"
  location = var.region
  project  = var.project_id
  ingress  = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.kong_sa.email

    containers {
      image = var.kong_image
      ports {
        container_port = 8000
      }
      env {
        name  = "KONG_DATABASE"
        value = "off"
      }
      env {
        name  = "KONG_DECLARATIVE_CONFIG"
        value = "/etc/kong/kong.yml"
      }
      volume_mounts {
        name       = "kong-config"
        mount_path = "/etc/kong"
      }
    }
    
    volumes {
      name = "kong-config"
      secret {
        secret_name = google_secret_manager_secret.kong_config.secret_id
        items {
          version = "latest"
          path    = "kong.yml"
        }
      }
    }
    
    # Direct VPC Egress: Mounts Cloud Run inside the Shared VPC directly
    vpc_access {
      network_interfaces {
        network    = var.vpc_id
        subnetwork = var.subnet_id
      }
      egress = "ALL_TRAFFIC"
    }
  }
}
```

###### 3. Declarative Config Template (`templates/kong.yml.tpl`)
```yaml
_format_version: "3.0"
_transform: true

services:
%{ for key, endpoint in agent_endpoints ~}
  - name: ${endpoint.logical_name}
    url: ${endpoint.endpoint_url}
    routes:
      - name: ${endpoint.logical_name}-route
        hosts:
          - ${endpoint.logical_name}.esmeralda.internal
        strip_path: true
    plugins:
      # Inject the GCP OIDC Identity Token dynamically on upstream calls
      - name: gcp-service-account
        config:
          audience: "https://us-central1-aiplatform.googleapis.com"
%{ endfor ~}
```

###### 4. Outputs Specification (`outputs.tf`)
```hcl
output "gateway_ingress_ip" {
  description = "The regional internal IP address allocated for the Cloud Run Serverless NEG fronting Kong"
  value       = "10.10.1.60" # Internal static IP pointing to the Kong front-end ingress
}

output "gateway_agent_ingress_host" {
  description = "The base private DNS zone managed by Kong"
  value       = "esmeralda.internal"
}
```

---

##### C. Option C: Direct Regional L7 Internal HTTP(S) Load Balancer (`gateways/ilb/`)

The direct L7 Internal Load Balancer (ILB) bypasses API gateway appliances entirely, routing traffic directly using Google Cloud's managed regional L7 load balancer. However, because an ILB lacks a programming engine and cannot natively rewrite paths or dynamically inject Google OIDC tokens to private Vertex AI Reasoning Engine API endpoints, a **Routing Broker proxy container** (Cloud Run + Serverless NEG) is packaged **inside** the ILB module itself.

This preserves the unified interface contract! The ILB routes all `*.esmeralda.internal` traffic to the `routing_broker` Cloud Run service. The Routing Broker container reads the dynamic `agent_endpoints` map via an environment variable (`AGENT_ENDPOINTS_JSON`), intercepts incoming agent requests, matches the host header prefix to obtain the target engine URL, retrieves an IAM ID Token from the metadata server, and proxies the query payload directly to the Vertex AI Reasoning Engine.

###### 1. Variables Specification (`variables.tf`)
Includes the standard Swappable Gateway variables contract defined above, plus:
```hcl
variable "routing_broker_image" {
  description = "The container image URL of the esmeralda-routing-broker proxy service"
  type        = string
  default     = "gcr.io/prj-esmeralda/routing-broker:latest"
}
```

###### 2. Implementation Blueprint (`main.tf`)
```hcl
# 1. Deploy the Internal Routing Broker Cloud Run Service
resource "google_service_account" "broker_sa" {
  account_id   = "routing-broker-sa-${var.environment}"
  display_name = "Routing Broker Service Account"
  project      = var.project_id
}

# Allow Routing Broker to invoke private Vertex AI Reasoning Engines
resource "google_project_iam_member" "broker_vertex_access" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.broker_sa.email}"
}

resource "google_cloud_run_v2_service" "routing_broker" {
  name     = "routing-broker-${var.environment}"
  location = var.region
  project  = var.project_id
  ingress  = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.broker_sa.email

    containers {
      image = var.routing_broker_image
      
      env {
        name  = "AGENT_ENDPOINTS_JSON"
        value = jsonencode(var.agent_endpoints)
      }
      
      env {
        name  = "LOG_LEVEL"
        value = "info"
      }
    }

    # Connect to the Shared VPC for secure backend egress
    vpc_access {
      network_interfaces {
        network    = var.vpc_id
        subnetwork = var.subnet_id
      }
      egress = "ALL_TRAFFIC"
    }
  }
}

# 2. Serverless NEG fronting the Routing Broker Cloud Run service
resource "google_compute_region_network_endpoint_group" "broker_neg" {
  name                  = "neg-broker-${var.environment}"
  project               = var.project_id
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.routing_broker.name
  }
}

# 3. Regional Internal Backend Service
resource "google_compute_region_backend_service" "broker_backend" {
  name                  = "backend-broker-${var.environment}"
  project               = var.project_id
  region                = var.region
  protocol              = "HTTP"
  load_balancing_scheme = "INTERNAL_MANAGED"

  backend {
    group = google_compute_region_network_endpoint_group.broker_neg.id
  }
}

# 4. Regional Internal L7 Load Balancer URL Map routing all *.esmeralda.internal traffic
resource "google_compute_region_url_map" "ilb_url_map" {
  name            = "ilb-gateway-url-map-${var.environment}"
  project         = var.project_id
  region          = var.region
  default_service = google_compute_region_backend_service.broker_backend.id

  host_rule {
    hosts        = ["*.esmeralda.internal"]
    path_matcher = "all-agents"
  }

  path_matcher {
    name            = "all-agents"
    default_service = google_compute_region_backend_service.broker_backend.id
  }
}

# 5. Target HTTP Proxy and Internal Forwarding Rule (VIP inside the Subnet)
resource "google_compute_region_target_http_proxy" "ilb_proxy" {
  name    = "ilb-gateway-proxy-${var.environment}"
  project = var.project_id
  region  = var.region
  url_map = google_compute_region_url_map.ilb_url_map.id
}

resource "google_compute_forwarding_rule" "ilb_forwarding_rule" {
  name                  = "ilb-gateway-rule-${var.environment}"
  project               = var.project_id
  region                = var.region
  ip_protocol           = "TCP"
  port_range            = "80"
  load_balancing_scheme = "INTERNAL_MANAGED"
  network               = var.vpc_id
  subnetwork            = var.subnet_id
  target                = google_compute_region_target_http_proxy.ilb_proxy.id
}
```

###### 3. Outputs Specification (`outputs.tf`)
```hcl
output "gateway_ingress_ip" {
  description = "The regional internal VIP allocated for the regional L7 load balancer fronting the broker"
  value       = google_compute_forwarding_rule.ilb_forwarding_rule.ip_address
}

output "gateway_agent_ingress_host" {
  description = "The base private DNS zone managed by the dynamic routing broker"
  value       = "esmeralda.internal"
}
```

---

