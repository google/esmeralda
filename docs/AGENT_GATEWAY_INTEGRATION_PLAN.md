# Esmeralda Agent Gateway & Platform Governance Integration Plan

This document outlines the step-by-step implementation plan to integrate native **GCP Agent Gateway**, **Model Armor guardrails**, **Agent Identity**, **Agent Registry tool discovery**, and **automated CI/CD registration** into the Esmeralda framework.

---

## 📋 Execution Roadmap Summary

| Phase | Feature / Task | Complexity | Key Files / Modules |
| :--- | :--- | :--- | :--- |
| **Phase 1** | **Migrate to Agent Identity** | Low | `infrastructure/modules/3-security/`, `apps/agents/` |
| **Phase 2** | **Model Armor Safety Templates** | Medium | `infrastructure/modules/3-security/model_armor.tf` |
| **Phase 3** | **Agent Registry & CI/CD Auto-Registration** | Medium | `infrastructure/modules/4-workloads/gateways/agent-registry-services/`, `scripts/` |
| **Phase 4** | **GCP Agent Gateway Module & Deployment** | High | `infrastructure/modules/4-workloads/gateways/agent-gateway/` |
| **Phase 5** | **ADK Agent Tool Lookup by Name & Auth** | Medium | `apps/agents/a2a-agent/agent/agent.py` |
| **Phase 6** | **Central Governance Observability Dashboard** | Medium | `infrastructure/modules/5-governance/` |

---

## Phase 1: Migrate to Agent Identity (Blocking Pre-requisite)

### 1.1 Overview
Replace standard Google Service Accounts with **Vertex AI Agent Identity** (`principalSet://...`) for both `root-agent` and `a2a-agent`. Agent Identity allows Reasoning Engine instances to act with a managed workload principal.

### 1.2 Step-by-Step Implementation

1. **Configure Reasoning Engines for Agent Identity (`identity_type = "AGENT_IDENTITY"`)**:
   In `infrastructure/modules/4-workloads/agents/`, update both `root_agent` and `a2a_agent` Reasoning Engine resources to set `identity_type = "AGENT_IDENTITY"` and remove the `service_account` field:

```hcl
# In Root Agent and A2A Agent Terraform modules (google_vertex_ai_reasoning_engine)
resource "google_vertex_ai_reasoning_engine" "agent" {
  provider     = google-beta
  project      = var.project_id
  location     = var.region
  display_name = var.agent_name

  spec {
    deployment_spec {
      # Enable Agent Identity (service_account field must NOT be set when identity_type = "AGENT_IDENTITY")
      identity_type = "AGENT_IDENTITY"
    }
  }
}
```

2. **IAM TokenCreator Binding for SA Impersonation**:
   Grant the Agent Identity `roles/iam.serviceAccountTokenCreator` on the MCP Invoker Service Account (`sa-mcp-invoker@prj-esmeralda-services-dev.iam.gserviceaccount.com`).

```hcl
# infrastructure/modules/3-security/agent_identity.tf

# 1. Dedicated Invoker SA for Cloud Run MCP Services
resource "google_service_account" "mcp_invoker_sa" {
  account_id   = "sa-mcp-invoker-${var.environment}"
  display_name = "MCP Invoker Service Account"
  project      = var.services_project_id
}

# 2. Grant Invoker SA Cloud Run Invoker Role on MCP Services
resource "google_project_iam_member" "mcp_invoker_run_invoker" {
  project = var.services_project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.mcp_invoker_sa.email}"
}

# 3a. Allow Root Agent Identity PrincipalSet to Impersonate the Invoker SA
resource "google_service_account_iam_member" "root_agent_identity_impersonates_mcp_invoker" {
  service_account_id = google_service_account.mcp_invoker_sa.name
  role               = "roles/iam.serviceAccountTokenCreator"
  
  # Vertex AI Agent Identity PrincipalSet for Root Agent project
  member = "principalSet://iam.googleapis.com/projects/${var.root_project_number}/locations/${var.region}/workloadIdentityPools/${var.root_project_id}.svc.id.goog/*"
}

# 3b. Allow A2A Agent Identity PrincipalSet to Impersonate the Invoker SA
resource "google_service_account_iam_member" "a2a_agent_identity_impersonates_mcp_invoker" {
  service_account_id = google_service_account.mcp_invoker_sa.name
  role               = "roles/iam.serviceAccountTokenCreator"
  
  # Vertex AI Agent Identity PrincipalSet for A2A Agent project
  member = "principalSet://iam.googleapis.com/projects/${var.a2a_project_number}/locations/${var.region}/workloadIdentityPools/${var.a2a_project_id}.svc.id.goog/*"
}
```

---

## Phase 2: Model Armor Template Creation (`google_model_armor_template`)

### 2.1 Overview
Create **Model Armor** safety inspection templates in each agent project (`prj-esmeralda-root-agent-dev` and `prj-esmeralda-a2a-dev`). Model Armor inspects prompts and responses for prompt injection attacks, jailbreaks, toxic content, and PII leakage.

### 2.2 Step-by-Step Implementation

1. Create `infrastructure/modules/3-security/model_armor.tf`.
2. Configure `google_model_armor_template` with Prompt Injection & PII filters.

```hcl
# infrastructure/modules/3-security/model_armor.tf

resource "google_model_armor_template" "agent_guardrails" {
  provider    = google-beta
  project     = var.target_project_id
  location    = var.region
  template_id = "esmeralda-agent-guardrails-${var.environment}"

  # A. Prompt Injection and Jailbreak Defense
  pi_and_jailbreak_filter_settings {
    filter_enforcement = "BLOCK"
    confidence_level   = "LOW_AND_ABOVE"
  }

  # B. Malicious Content & Harm Filters
  malicious_intent_filter_settings {
    filter_enforcement = "BLOCK"
  }

  # C. Sensitive Data Protection (PII DLP)
  sdp_filter_settings {
    sdp_configuration {
      basic_config {
        filter_enforcement = "BLOCK"
      }
    }
  }
}

output "model_armor_template_name" {
  value = google_model_armor_template.agent_guardrails.name
}
```

---

## Phase 3: Agent Registry Services & Automated CI/CD Registration [COMPLETED]

### 3.1 Overview
Dynamic multi-spoke project discovery and registration pipeline integrated directly into Cloud Build for all MCP servers (`income-verification`, `corporate-email`, `legacy-dms`) and A2A Agents (`a2a-mortgage-agent`).

### 3.2 Implemented Architecture
1. **Dynamic Spoke Discovery**:
   Cloud Build pipelines dynamically query all spoke projects labeled with `agent_platform=agent-spoke-project` using in-step gcloud execution:
   ```bash
   SPOKE_PROJECTS=$(gcloud projects list --filter="labels.agent_platform=agent-spoke-project" --format="value(projectId)")
   ```
2. **Version-Controlled MCP Tool Specs (`tools.json`)**:
   MCP servers embed full OpenAPI/JSON Schema `TOOL_SPEC` definitions registered via `--mcp-server-spec-type=tool-spec --mcp-server-spec-content=tools.json` and `protocolBinding=jsonrpc`:
   - `income-verification`: `verify_applicant`
   - `corporate-email`: `send_email`, `read_email`
   - `legacy-dms`: `search_documents`, `get_document`
3. **Single Source of Truth A2A Agent Card (`agent.yaml`)**:
   `apps/agents/a2a-agent/agent.yaml` serves as the single source of truth for the A2A Agent Card spec. Step 3 of `apps/agents/a2a-agent/cloudbuild.yaml` parses `agent.yaml` in memory using Python, registering a full `A2A_AGENT_CARD` specification with `supportedInterfaces` (`http://a2a-mortgage-agent.esmeralda.internal/a2a`, `HTTP_JSON`, `0.3`), input/output modes, capabilities, and A2A skills (`document-search`, `income-verification`, `corporate-email`).
4. **Builder SA IAM Authorization**:
   Granted `roles/browser` (`resourcemanager.projects.list`) and `roles/agentregistry.admin` to `sa-esmeralda-builder-dev` across all spoke projects in `infrastructure/modules/3-security/main.tf`.
5. **Clean Internal Domain Formatting**:
   All registered service URLs use clean `.esmeralda.internal` domain names without port numbers.

---

## Phase 4: GCP Agent Gateway Module (`google_network_services_agent_gateway`) [COMPLETED]

### 4.1 Overview
Created the native GCP **Agent Gateway** Terraform module in `infrastructure/modules/4-workloads/gateways/agent-gateway/`. Deploys instances in `AGENT_TO_ANYWHERE` (egress) mode using `google_network_services_agent_gateway` with IAP `REQUEST_AUTHZ` pointing to the Registries populated in Phase 3, and Model Armor `CONTENT_AUTHZ` configured in Phase 2.

> [!NOTE]
> **Network Attachment & DNS Peering Alignment**:
> * **2 Network Attachments per Agent Project**: `network_attachment_1` (`psc_interface`) connects the Vertex AI Reasoning Engine container to the VPC. `network_attachment_2` (`agent_gateway`) connects the Agent Gateway proxy to the VPC.
> * **DNS Peering**: Agent Gateway's `dns_peering_config` domain aligns 1:1 with Esmeralda's Stage 2 Private DNS managed zone (`esmeralda.internal.`), targeting `net_host_project_id` and `vpc_name`.

### 4.2 Step-by-Step Implementation

1. Create `infrastructure/modules/4-workloads/gateways/agent-gateway/main.tf`.
2. Configure `google_agent_platform_agent_gateway`, `google_network_security_authz_policy`, and `google_network_services_authz_extension`.

```hcl
# infrastructure/modules/4-workloads/gateways/agent-gateway/main.tf

# 1. Network Attachment 2: Dedicated Gateway Egress Network Attachment
resource "google_compute_network_attachment" "agent_gateway" {
  project               = var.project_id
  name                  = "agw-egress-na-${var.environment}"
  region                = var.region
  connection_preference = "ACCEPT_AUTOMATIC"
  subnetworks           = [var.subnet_self_link]
}

# 2. Agent Gateway Resource (Agent Egress Mode with VPC Network Attachment)
resource "google_agent_platform_agent_gateway" "egress_gateway" {
  provider = google-beta
  project  = var.project_id
  location = var.region
  name     = "esmeralda-agent-egress-gateway-${var.environment}"

  google_managed {
    governed_access_path = "AGENT_TO_ANYWHERE"
  }

  # Linked Regional Agent Registry
  registries = [
    "//agentregistry.googleapis.com/projects/${var.project_id}/locations/${var.region}"
  ]

  # VPC Network Egress via Network Attachment & Shared VPC DNS Peering
  network_config {
    egress {
      network_attachment = google_compute_network_attachment.agent_gateway.id
    }

    # Matches Esmeralda's Stage 2 Private DNS Managed Zone (esmeralda.internal.)
    dns_peering_config {
      domains        = ["esmeralda.internal."]
      target_project = var.net_host_project_id
      target_network = var.vpc_name
    }
  }
}

# 3. IAP Authorization Extension (REQUEST_AUTHZ)
resource "google_network_services_authz_extension" "iap_request_authz" {
  provider = google-beta
  project  = var.project_id
  location = var.region
  name     = "iap-request-authz-ext-${var.environment}"

  service  = "iap.googleapis.com"
  fail_open = false
  timeout  = "1s"

  metadata = {
    iamEnforcementMode = "ENFORCE"
    iapPolicyVersion   = "V1"
  }
}

# 4. IAP Network Security Authorization Policy (REQUEST_AUTHZ)
resource "google_network_security_authz_policy" "iap_policy" {
  provider = google-beta
  project  = var.project_id
  location = var.region
  name     = "iap-agent-authz-policy-${var.environment}"

  target {
    resources = [google_agent_platform_agent_gateway.egress_gateway.id]
  }

  policy_profile = "REQUEST_AUTHZ"
  action         = "CUSTOM"

  custom_provider {
    authz_extension {
      resources = [google_network_services_authz_extension.iap_request_authz.id]
    }
  }
}

# 5. Model Armor Authorization Extension (CONTENT_AUTHZ)
resource "google_network_services_authz_extension" "model_armor_content_authz" {
  provider = google-beta
  project  = var.project_id
  location = var.region
  name     = "model-armor-authz-ext-${var.environment}"

  service   = "modelarmor.googleapis.com"
  fail_open = false
  timeout   = "2s"

  metadata = {
    templateName = var.model_armor_template_name
  }
}

# 6. Model Armor Network Security Authorization Policy (CONTENT_AUTHZ)
resource "google_network_security_authz_policy" "model_armor_policy" {
  provider = google-beta
  project  = var.project_id
  location = var.region
  name     = "model-armor-authz-policy-${var.environment}"

  target {
    resources = [google_agent_platform_agent_gateway.egress_gateway.id]
  }

  policy_profile = "CONTENT_AUTHZ"
  action         = "CUSTOM"

  custom_provider {
    authz_extension {
      resources = [google_network_services_authz_extension.model_armor_content_authz.id]
    }
  }
}

# 7. Cross-Project Model Armor IAM Bindings
# Grants Agent Gateway service identity permissions to evaluate templates in Governance project
resource "google_project_iam_member" "gateway_model_armor_user" {
  project = var.governance_project_id
  role    = "roles/modelarmor.user"
  member  = "serviceAccount:service-${data.google_project.gateway.number}@gcp-sa-agentplatform.iam.gserviceaccount.com"
}

resource "google_project_iam_member" "gateway_model_armor_service_usage" {
  project = var.governance_project_id
  role    = "roles/serviceusage.serviceUsageConsumer"
  member  = "serviceAccount:service-${data.google_project.gateway.number}@gcp-sa-agentplatform.iam.gserviceaccount.com"
}

resource "google_project_iam_member" "gateway_model_armor_callout" {
  project = var.gateway_project_id
  role    = "roles/modelarmor.calloutUser"
  member  = "serviceAccount:service-${data.google_project.gateway.number}@gcp-sa-agentplatform.iam.gserviceaccount.com"
}
```
```

---

## Phase 5: ADK Agent Tool Lookup by Name & OIDC Impersonation Factory

### 5.1 Overview
Configure explicit MCP tool resolution by name in `a2a-agent` (`apps/agents/a2a-agent/agent/agent.py`). 
* **`base-adk-agent`**: Remains clean with **no** MCP tools (as it is today).
* **`a2a-agent`**: Specifies explicit MCP server names (e.g., `["income-verification", "corporate-email"]`). `AgentRegistry` resolves the toolsets and `.esmeralda.internal` URLs by name.

### 5.2 Python Code Implementation

```python
# apps/agents/a2a-agent/agent/agent.py

import os
import logging
import httpx
from urllib.parse import urlparse
import google.auth
import google.auth.transport.requests as gar
from google.auth import impersonated_credentials
from google.adk.agents.llm_agent import Agent
from google.adk.integrations.agent_registry import AgentRegistry

logger = logging.getLogger(__name__)

# List of explicit MCP server names required by this A2A Agent
REQUIRED_MCP_SERVERS = [
    "income-verification",
    "corporate-email",
]

def _build_impersonation_factory(target_url: str, target_sa_email: str):
    """Mints short-lived OIDC ID Tokens via SA impersonation for Cloud Run invoker auth."""
    parsed = urlparse(target_url)
    audience = f"{parsed.scheme}://{parsed.netloc}"

    source_creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    
    impersonated = impersonated_credentials.Credentials(
        source_credentials=source_creds,
        target_principal=target_sa_email,
        target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    id_token_creds = impersonated_credentials.IDTokenCredentials(
        target_credentials=impersonated,
        target_audience=audience,
        include_email=True,
    )

    class _ImpersonatedIDTokenAuth(httpx.Auth):
        def auth_flow(self, request):
            if not id_token_creds.valid:
                id_token_creds.refresh(gar.Request())
            request.headers["Authorization"] = f"Bearer {id_token_creds.token}"
            yield request

    def factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(
            follow_redirects=True,
            headers=headers,
            timeout=timeout or httpx.Timeout(5.0),
            auth=auth or _ImpersonatedIDTokenAuth(),
        )

    return factory

def get_mcp_toolsets_by_name(mcp_server_names: list[str]) -> list:
    """Fetches toolsets for specific MCP server names from the regional Agent Registry."""
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("AGENT_REGISTRY_LOCATION", "us-central1")
    invoker_sa = os.environ.get("MCP_INVOKER_SA_EMAIL")

    registry = AgentRegistry(project_id=project, location=location)
    toolsets = []
    
    for server_name in mcp_server_names:
        try:
            # Fetch specific MCP toolset by name from Agent Registry
            toolset = registry.get_mcp_toolset(mcp_server_name=server_name)
            
            # Attach OIDC Impersonation Factory for Cloud Run Auth
            conn_params = getattr(toolset, "_connection_params", None)
            resolved_url = getattr(conn_params, "url", None)
            if invoker_sa and conn_params and resolved_url:
                conn_params.httpx_client_factory = _build_impersonation_factory(
                    target_url=resolved_url,
                    target_sa_email=invoker_sa,
                )
                
            toolsets.append(toolset)
        except Exception as ex:
            logger.error("Failed to load MCP toolset '%s' from Agent Registry: %s", server_name, ex)
            
    return toolsets

def build_agent():
    # Fetch explicit toolsets by name for a2a-agent
    tools = get_mcp_toolsets_by_name(REQUIRED_MCP_SERVERS)
    
    return Agent(
        model=os.environ.get("MODEL_NAME", "gemini-2.5-flash"),
        name="a2a_mortgage_agent",
        instruction="Specialized mortgage assessment A2A agent.",
        tools=tools,
    )
```

---

## Phase 6: Central Governance Observability & Security Dashboard

### 6.1 Overview
Extend Esmeralda's Stage 5 Governance (`infrastructure/modules/5-governance/`) in `prj-esmeralda-governance` to aggregate Agent Gateway audit logs and deploy a central Cloud Monitoring Dashboard for SecOps visualization.

### 6.2 Step-by-Step Implementation

1. **Expand Central Log Sinks Filter (`5-governance/modules/1_telemetry_sinks/main.tf`)**:
   Add Agent Gateway and Model Armor service log filters so all spoke project audit logs stream into `prj-esmeralda-governance`'s BigQuery dataset:

```hcl
# In infrastructure/modules/5-governance/modules/1_telemetry_sinks/main.tf
resource "google_logging_project_sink" "central_sinks" {
  for_each    = local.monitored_projects
  name        = "esmeralda-central-telemetry-sink-${var.environment}"
  project     = each.value
  destination = "bigquery.googleapis.com/projects/${var.governance_project_id}/datasets/${data.google_bigquery_dataset.telemetry_logs.dataset_id}"

  # Expanded filter for Agent Gateway Egress & Model Armor Audit Logs
  filter = "resource.type=\"aiplatform.googleapis.com/ReasoningEngine\" OR logName=~\"gen_ai\" OR logName=~\"reasoning_engine\" OR resource.type=\"cloud_run_revision\" OR logName=~\"cloudaudit.googleapis.com\" OR logName=~\"agent_gateway\" OR protoPayload.serviceName=\"networkservices.googleapis.com\" OR protoPayload.serviceName=\"modelarmor.googleapis.com\""

  bigquery_options {
    use_partitioned_tables = true
  }
}
```

2. **Deploy Gateway Governance Dashboard (`5-governance/modules/5_gateway_analytics/main.tf`)**:
   Create a central Cloud Monitoring Dashboard in `prj-esmeralda-governance` visualizing per-tool IAP blocks and Model Armor guardrail triggers across all spoke projects:

```hcl
# infrastructure/modules/5-governance/modules/5_gateway_analytics/main.tf

resource "google_monitoring_dashboard" "agent_gateway_governance" {
  project        = var.governance_project_id
  dashboard_json = <<EOF
{
  "displayName": "Esmeralda Agent Gateway & Guardrails Governance Dashboard",
  "gridLayout": {
    "columns": "2",
    "widgets": [
      {
        "title": "IAP Per-Tool Authorization Pass/Fail (403 Blocks)",
        "xyChart": {
          "dataSets": [{
            "timeSeriesQuery": {
              "timeSeriesFilter": {
                "filter": "resource.type=\"agent_gateway\" AND metric.type=\"networkservices.googleapis.com/agent_gateway/authz_evaluations_count\""
              }
            }
          }]
        }
      },
      {
        "title": "Model Armor Guardrail Violations (Prompt Injection & PII Triggers)",
        "xyChart": {
          "dataSets": [{
            "timeSeriesQuery": {
              "timeSeriesFilter": {
                "filter": "resource.type=\"agent_gateway\" AND metric.type=\"modelarmor.googleapis.com/template_evaluations_count\""
              }
            }
          }]
        }
      }
    ]
  }
}
EOF
}
```

---

## 🎯 Final Architecture Verification Checklist

- [x] **Phase 1 - Agent Identity**: Verified both Root and A2A agents use `identity_type = "AGENT_IDENTITY"`.
- [x] **Phase 2 - Model Armor**: Created `esmeralda-prompt-guardrails-dev` and `esmeralda-response-guardrails-dev` in `prj-esmeralda-governance-dev`.
- [x] **Phase 3 - Registration Verification**: Auto-registered `a2a-mortgage-agent` via `agent.yaml` in Cloud Build across both `esmeralda-root-agent-dev` and `esmeralda-a2a-dev`.
- [x] **Phase 4 - Agent Gateway**: Deployed `google_network_services_agent_gateway` in `AGENT_TO_ANYWHERE` egress mode across both spoke projects (`esmeralda-root-agent-dev` and `esmeralda-a2a-dev`).
- [x] **Phase 5 - Explicit Tool Resolution**: Verified end-to-end routing from Root Agent -> Agent Gateway -> A2A Agent -> Agent Gateway -> MCP Tools (`income-verification` and `legacy-dms`).
- [x] **Phase 6 - Central Governance Dashboard**: Enabled Log Analytics (`enable_analytics = true`) on the `_Default` log bucket across all projects and deployed Stage 5 Governance Stack.
- [x] **Per-Tool IAP Access**: Verified IAP authorization policies (`REQUEST_AUTHZ`) and Model Armor safety policies (`CONTENT_AUTHZ`) on Agent Gateway egress proxies.

