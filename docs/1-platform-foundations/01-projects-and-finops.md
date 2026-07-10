# Stage 1: Foundational Projects, Billing & APIs

### A. Architecture & Business Decisions Guide

Stage 1 manages the isolated creation of multiple GCP projects and the activation of foundational service APIs under a governance structure fully compliant with Google Cloud enterprise Landing Zone standards.

### A. Enterprise Landing Zone Simulation (SoC)
To mirror a real-world enterprise production environment, workloads are divided across **seven distinct projects**:

| Project Domain | Simulated Project ID | Role and Responsibility | Primary Hosted Resources |
| :--- | :--- | :--- | :--- |
| **Network Host** | `prj-net-host` | Managed by NetOps. Controls network traffic routing and security. | Shared VPC, Subnets, Cloud DNS, Cloud NAT, Firewalls. |
| **Traffic Ingress** | `prj-gateway` | Managed by PlatformOps. Controls enterprise API ingress. | Apigee X, Kong on Cloud Run, Internal Load Balancers. |
| **CI/CD & Artifacts** | `prj-esmeralda-cicd-artifacts` | Managed by Platform Engineering. CI/CD pipelines and container image storage. | Cloud Build, Artifact Registry Docker repositories. |
| **MCP Tools** | `prj-esmeralda-mcps` | Managed by AppDev team. Shared enterprise utility APIs. | Cloud Run (Corporate Email, Income Verifier, DMS). |
| **Core AI Platform** | `prj-esmeralda-a2a` | Managed by Core AI team. Hosts reusable cross-domain agents. | Vertex AI Reasoning Engine (A2A), Cloud SQL PostgreSQL. |
| **Business Unit Application** | `prj-esmeralda-root-agent` | Managed by Business Unit team. Client-facing user reasoning engines. | Vertex AI Reasoning Engine (Root), GCS Staging Buckets. |
| **Governance Hub** | `prj-esmeralda-governance` | Managed by SecOps. Centralizes compliance and audit logs. | KMS Keyrings, Secrets, TLS Certificates, BigQuery Analytics, Log Sinks. |

---

### B. FinOps Challenges Solved
1.  **Clean Generative AI Attribution**: Gemini API calls made by the mortgage assistant bill directly to `prj-esmeralda-root-agent`. Sub-process calls bill directly to `prj-esmeralda-a2a`.
2.  **Ephemeral vs. Persistent Cost Separation**: The PostgreSQL database (continuous 24/7 billing) is isolated inside the AI platform project. Serverless MCP tool servers (Cloud Run) scale to zero when idle, eliminating ongoing compute costs.
3.  **Zero Hidden Network Egress Costs**: All inter-service traffic flows internally over private Shared VPC IPs within the same region (`us-central1`), avoiding public NAT transit fees.

---

## Detailed Implementation Specifications & HCL Blueprints

This module manages the automated provisioning of isolated GCP service projects, links them securely to the corporate billing account, and activates standard APIs natively. Under this design, the module handles **up to seven distinct projects**, reflecting a standard enterprise landing zone.

#### B. The FinOps Challenge: Cost Attribution & Billing Isolation
Deploying agentic pipelines across multiple distinct GCP projects solves major enterprise pain points but introduces key FinOps and tracking challenges. Our multi-project structure solves these as follows:

```mermaid
graph TD
    %% FinOps visualization
    subgraph Billing["GCP Billing Account (Central Treasury)"]
        Export["Cloud Billing BigQuery Export"]
    end

    subgraph "prj-net-host (NetOps Budget)"
        C0["Shared VPC & Network Egress Costs"]
        L0["Label: cost-center=netops-core"]
    end

    subgraph "prj-gateway (PlatformOps Budget)"
        C_GW["API Gateway Appliance / ILB Costs"]
        L_GW["Label: cost-center=platformops-ingress"]
    end

    subgraph "prj-esmeralda-cicd-artifacts (Platform Eng Budget)"
        C_CI["Cloud Build & Artifact Registry Storage"]
        L_CI["Label: cost-center=platform-engineering"]
    end

    subgraph "prj-esmeralda-mcps (AppDev Tools Budget)"
        C1["Cloud Run Compute Costs"]
        L1["Label: cost-center=appdev-tools"]
    end

    subgraph "prj-esmeralda-a2a (Core AI Platform Budget)"
        C2["Vertex AI Model API Calls"]
        C3["Cloud SQL DB (Continuous Run)"]
        L2["Label: cost-center=core-ai-platform"]
    end

    subgraph "prj-esmeralda-root-agent (Business Unit Budget)"
        C4["Vertex AI Orchestrator API Calls"]
        L3["Label: cost-center=bu-mortgage"]
    end

    subgraph "prj-esmeralda-governance (SecOps Budget)"
        C_GOV["KMS Key Rings & BigQuery Telemetry Storage"]
        L_GOV["Label: cost-center=secops-governance"]
    end

    C0 & L0 -. Billing Record .-> Export
    C_GW & L_GW -. Billing Record .-> Export
    C_CI & L_CI -. Billing Record .-> Export
    C1 & L1 -. Billing Record .-> Export
    C2 & C3 & L2 -. Billing Record .-> Export
    C4 & L3 -. Billing Record .-> Export
    C_GOV & L_GOV -. Billing Record .-> Export
```

##### 1. Vertex AI Model API Billing Attribution
When multiple teams call Gemini via Vertex AI, a monolithic project makes it impossible to distinguish which team consumed how many input/output tokens. By splitting workloads:
*   Calls to Gemini made by the Root Orchestrator are charged directly to `prj-esmeralda-root-agent` (Business Unit Budget).
*   Calls to Gemini made by the A2A Agent during sub-task execution are charged to `prj-esmeralda-a2a-agents` (Core AI Budget).
*   *Implementation*: Resource labels (`env=dev`, `cost-center=...`, `team=...`) are systematically applied at the project level and resource level, flowing directly into the **GCP Billing Export to BigQuery** for clean dashboards.

##### 2. Persistent vs. Ephemeral Resource Cost Allocation
*   **Cloud SQL Instance**: Run as a shared state machine for the A2A agent, running 24/7. This represents a fixed cost that is isolated inside the Core AI Platform budget (`prj-esmeralda-a2a-agents`) and is not subsidized by the Business Unit team.
*   **Cloud Run (MCP Tool Servers)**: Run serverless, scaling to zero when there are no requests. This ensures that the AppDev team only incurs compute costs when tools are actively invoked, preventing resource wasting.

##### 3. Cross-Project Network Transit Cost Optimization
Data transferring across VPC subnets and project boundaries can incur inter-zone egress fees.
*   To address this, all projects are systematically locked to a single region (`us-central1`) and use Shared VPC Private IP communication, eliminating public internet NAT gateway egress charges for inter-agent communication.

---

#### C. The BYOInfra (Brownfield) Integration Architecture
In real enterprise deployments, customers will **never** allow a tool to provision a new Shared VPC Host Project (`net_host`) or change their centralized Gateway Ingress Project (`gateway`). 

Esmeralda elegantly handles this by using a dynamic **BYOInfra Fallback Architecture**:

```mermaid
flowchart TD
    %% Decoupling logic
    subgraph Inputs["Terragrunt Input Parameters (env.yaml)"]
        BYO_Net["byo_net_host_project = true"]
        BYO_Gwy["byo_gateway_project = true"]
        Exist_Net["existing_net_host_project = prj-corp-net-host"]
        Exist_Gwy["existing_gateway_project = prj-corp-apigee-ingress"]
    end

    subgraph Stage1["Stage 1: modules/1-projects"]
        Check_Net{byo_net_host_project?}
        Check_Gwy{byo_gateway_project?}
        
        Check_Net -- "True (BYO)" --> Skip_Net["Bypass Creation <br/> Return existing_net_host_project"]
        Check_Net -- "False" --> Create_Net["Create prj-net-host from scratch"]
        
        Check_Gwy -- "True (BYO)" --> Skip_Gwy["Bypass Creation <br/> Return existing_gateway_project"]
        Check_Gwy -- "False" --> Create_Gwy["Create prj-gateway from scratch"]
        
        Create_MCPS["Create prj-esmeralda-mcps <br/> (Always)"]
        Create_A2A["Create prj-esmeralda-a2a-agents <br/> (Always)"]
        Create_Root["Create prj-esmeralda-root-agent <br/> (Always)"]
    end

    Inputs --> Check_Net
    Inputs --> Check_Gwy
```

*   **Conditional Project Seed**: In `main.tf`, `google_project.net_host` and `google_project.gateway` use a `count` conditional (`count = var.byo_net_host_project ? 0 : 1`).
*   **API Enablement Isolation**: Service API enablement is also conditional. If `byo_net_host_project` is true, Esmeralda skips enabling APIs in that project to prevent permission conflicts with corporate security policies (which restrict IAM permissions to create/enable APIs on shared host projects).
*   **Dynamic Outputs Mapping**: Regardless of whether a project is created from scratch or provided as pre-existing, `outputs.tf` resolves the correct active project IDs, ensuring downstream modules (networking, security, and workloads) consume them transparently.

---

### D. Exhaustive Resource & Identity Provisioning Breakdown

A granular inspection of `infrastructure/modules/1-projects/` reveals exactly how Esmeralda orchestrates project creation, API enablements, and service identities:

```mermaid
flowchart TD
    subgraph Inputs["Stage 1 Inputs (variables.tf)"]
        Prefix["project_suffix (2 hex bytes)"]
        BYO["BYO Toggles & Existing IDs"]
    end

    subgraph Projects["7 Enterprise Landing Zone Projects"]
        Cond["4 Conditional Projects<br/>(net_host, gateway, cicd, governance)"]
        Uncond["3 Unconditional Workloads<br/>(mcps, a2a, root_agent)"]
    end

    subgraph APIs["Service API Enablements"]
        APIList["Least-Privilege APIs<br/>(aiplatform, run, sqladmin, secretmanager, etc.)"]
    end

    subgraph Sleep["Propagation Buffer"]
        Delay["time_sleep.api_propagation<br/>(30 Seconds Convergence Delay)"]
    end

    subgraph Identities["9 Bootstrapped Service Identities"]
        SA_Build["cicd_build (Cloud Build)"]
        SA_Run["mcps_run, gateway_run, a2a_run, root_run"]
        SA_Vertex["a2a_vertex, root_vertex"]
        SA_Data["a2a_sql, governance_secrets"]
    end

    Inputs -->|Resolve IDs & Labels| Projects
    Projects -->|for_each conditional enablement| APIs
    APIs -->|depends_on| Sleep
    Sleep -->|Request Google-Managed Robots| Identities
```

#### 1. Dynamic Project ID & Label Resolution
*   **Hex Suffix**: A 2-byte random hex string (`random_id.project_suffix`) ensures global project ID uniqueness across Google Cloud.
*   **ID Resolution**: For conditional projects (`net_host`, `gateway`, `governance`, `cicd`), ternary lookups (`var.byo_* ? var.existing_* : "..."`) dynamically select either the existing customer ID or generate a new one using `var.project_prefix`.
*   **Cost & FinOps Labels**: Every project receives systematic attribution metadata: `"env" = var.environment` and `"managed-by" = "terragrunt-esmeralda"`, merged with project-specific `cost-center` and `team` tags.

#### 2. Four Conditional Infrastructure & Governance Projects
Created with `count = var.byo_* ? 0 : 1` to respect enterprise brownfield environments:
1.  **`google_project.net_host`**: Tagged with `cost-center = networking-infrastructure`, `team = netops`.
2.  **`google_project.gateway`**: Tagged with `cost-center = ingress-gateways`, `team = platformops`.
3.  **`google_project.cicd`**: Tagged with `cost-center = shared-cicd-and-artifacts`, `team = platform-engineering`.
4.  **`google_project.governance`**: Tagged with `cost-center = central-governance-and-telemetry`, `team = security-and-platformops`.

#### 3. Three Unconditional Workload Projects
Always created from scratch by Esmeralda for runtime isolation:
1.  **`google_project.mcps`**: Tagged with `cost-center = central-developer-tools`, `team = appdev-tools`.
2.  **`google_project.a2a`**: Tagged with `cost-center = enterprise-ai-platform`, `team = core-ai-agents`.
3.  **`google_project.root_agent`**: Tagged with `cost-center = lob-business-solutions`, `team = lob-root-agent`.

#### 4. Service API Activation Matrix (`google_project_service`)
Each project receives a precise, least-privilege list of Google Cloud service APIs. Conditional projects only enable APIs if created by Esmeralda (`for_each = var.byo_* ? [] : toset(...)`):

| Project | Enabled Service APIs |
| :--- | :--- |
| **`net_host`** | `compute`, `dns`, `servicenetworking`, `networksecurity`, `networkservices`, `certificatemanager`, `logging` |
| **`gateway`** | `compute`, `apigee`, `certificatemanager`, `logging`, `secretmanager`, `run`, `iam` |
| **`cicd`** | `compute`, `artifactregistry`, `cloudbuild`, `logging`, `storage`, `secretmanager` |
| **`mcps`** | `compute`, `run`, `artifactregistry`, `secretmanager`, `logging`, `cloudbuild` |
| **`a2a`** | `compute`, `aiplatform`, `sqladmin`, `storage`, `secretmanager`, `run` *(for DB bootstrap job)*, `artifactregistry`, `logging`, `servicenetworking` |
| **`root_agent`** | `compute`, `aiplatform`, `storage`, `run`, `artifactregistry`, `logging` |
| **`governance`** | `bigquery`, `logging`, `clouderrorreporting`, `cloudtrace`, `monitoring`, `cloudkms`, `secretmanager` |

#### 5. Propagation Buffer & Service Identity Bootstrapping
To avoid race conditions where IAM role bindings fail because Google-managed service identities do not yet exist in newly enabled APIs, Esmeralda implements a sequential bootstrap:
*   **`time_sleep.api_propagation`**: Enforces a 30-second delay after all `google_project_service` resources complete, allowing GCP metadata servers to converge.
*   **Nine Google-Managed Service Identities (`google_project_service_identity`)**: Explicitly requested and created across target projects:
    1.  **`cicd_build`**: `cloudbuild.googleapis.com` in `cicd`.
    2.  **`mcps_run`**: `run.googleapis.com` in `mcps`.
    3.  **`gateway_run`**: `run.googleapis.com` in `gateway`.
    4.  **`a2a_run`**: `run.googleapis.com` in `a2a`.
    5.  **`a2a_vertex`**: `aiplatform.googleapis.com` in `a2a`.
    6.  **`a2a_sql`**: `sqladmin.googleapis.com` in `a2a`.
    7.  **`root_vertex`**: `aiplatform.googleapis.com` in `root_agent`.
    8.  **`root_run`**: `run.googleapis.com` in `root_agent`.
    9.  **`governance_secrets`**: `secretmanager.googleapis.com` in `governance` *(conditional on BYO, fallback via data source)*.

#### 6. Module Outputs (`outputs.tf`)
The module exports precisely 17 values: the 7 active project IDs (`net_host_project_id`, `gateway_project_id`, `cicd_project_id`, `mcps_project_id`, `a2a_project_id`, `root_project_id`, `governance_project_id`), the random suffix (`project_suffix`), and the exact email addresses for all 9 bootstrapped service identities (`cicd_build_service_agent`, `mcps_run_service_agent`, `gateway_run_service_agent`, `a2a_run_service_agent`, `a2a_vertex_service_agent`, `root_vertex_service_agent`, `root_run_service_agent`, `a2a_sql_service_agent`, `governance_secrets_service_agent`).

---

### E. File Inventory & Blueprints

```text
infrastructure/modules/1-projects/
├── versions.tf          # Mandates HashiCorp Google provider bounds (>= 5.0)
├── variables.tf         # Inputs for billing, organization folders, prefix, and BYO parameters
├── main.tf              # Implements projects, API enablement, billing bindings, and labels
└── outputs.tf           # Exposes project IDs, numbers, and service identities
```

*(With this, any client's NetOps/PlatformOps teams can hand over pre-configured net_host and gateway projects, and Esmeralda will automatically provision and deploy the isolated workload projects and attach them securely to their Shared VPC.)*
