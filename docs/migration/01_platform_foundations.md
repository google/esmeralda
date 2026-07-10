# Master Guide: Platform Foundations (Stages 1, 2, and 3)

This document unifies all conceptual details, architectural infrastructure decisions, FinOps principles, and production-ready Terraform/HCL blueprints for Esmeralda's foundational platform. It consolidates Stages 1, 2, and 3 into a single comprehensive guide, allowing seamless high-level architectural review and direct code implementation without needing to switch between dispersed files.

---

## 🗺️ Foundations Deployment Index
1. [Stage 1: Foundational Projects, Billing (FinOps), and APIs](#stage-1)
   - [Architectural & FinOps Overview](#s1-concepts)
   - [Technical Specifications & HCL Blueprints](#s1-codes)
2. [Stage 2: Private Networking, DNS, and Private Service Connect (PSC)](#stage-2)
   - [Network Topology & Secure Egress Overview](#s2-concepts)
   - [Technical Specifications & HCL Blueprints](#s2-codes)
3. [Stage 3: Security, CMEK Keys, Secrets, and Identities (Least Privilege)](#stage-3)
   - [Encryption, Service Accounts, and Access Policies Overview](#s3-concepts)
   - [Technical Specifications & HCL Blueprints](#s3-codes)

---

<a name="stage-1"></a>
## 🏢 1. Stage 1: Foundational Projects, Billing & APIs

<a name="s1-concepts"></a>
### A. Architecture & Business Decisions Guide

Stage 1 manages the isolated creation of multiple GCP projects and the activation of foundational service APIs under a governance structure fully compliant with Google Cloud enterprise Landing Zone standards.

### A. Enterprise Landing Zone Simulation (SoC)
To mirror a real-world enterprise production environment, workloads are divided across **six distinct projects**:

| Project Domain | Simulated Project ID | Role and Responsibility | Primary Hosted Resources |
| :--- | :--- | :--- | :--- |
| **Network Host** | `prj-net-host` | Managed by NetOps. Controls network traffic routing and security. | Shared VPC, Subnets, Cloud DNS, Cloud NAT, Firewalls. |
| **Traffic Ingress** | `prj-gateway` | Managed by PlatformOps. Controls enterprise API ingress. | Apigee X, Kong on Cloud Run, Internal Load Balancers. |
| **MCP Tools** | `prj-esmeralda-mcps` | Managed by AppDev team. Shared enterprise utility APIs. | Cloud Run (Corporate Email, Income Verifier, DMS), Artifact Registry. |
| **Core AI Platform** | `prj-esmeralda-a2a-agents` | Managed by Core AI team. Hosts reusable cross-domain agents. | Vertex AI Reasoning Engine (A2A), Cloud SQL PostgreSQL. |
| **Business Unit Application** | `prj-esmeralda-root-agent` | Managed by Business Unit team. Client-facing user reasoning engines. | Vertex AI Reasoning Engine (Root), GCS Staging Buckets. |
| **Governance Hub** | `prj-esmeralda-governance` | Managed by SecOps. Centralizes compliance and audit logs. | KMS Keyrings, Secrets, TLS Certificates, BigQuery Analytics, Log Sinks. |

---

### B. FinOps Challenges Solved
1.  **Clean Generative AI Attribution**: Gemini API calls made by the mortgage assistant bill directly to `prj-esmeralda-root-agent`. Sub-process calls bill directly to `prj-esmeralda-a2a-agents`.
2.  **Ephemeral vs. Persistent Cost Separation**: The PostgreSQL database (continuous 24/7 billing) is isolated inside the AI platform project. Serverless MCP tool servers (Cloud Run) scale to zero when idle, eliminating ongoing compute costs.
3.  **Zero Hidden Network Egress Costs**: All inter-service traffic flows internally over private Shared VPC IPs within the same region (`us-central1`), avoiding public NAT transit fees.

---

---

<a name="s1-codes"></a>
### B. Detailed Implementation Specifications & HCL Blueprints

# Stage 1: Foundational Projects, Billing & APIs

This module manages the automated provisioning of isolated GCP service projects, links them securely to the corporate billing account, and activates standard APIs natively.

## 7. Detailed Module Implementation Specifications

This section defines the precise layout, HCL blueprints, and resources that must be built inside each of the pure reusable directories under `infrastructure/modules/`.

### 7.1 Stage 1: `modules/1-projects/` Specification

This module manages the creation of isolated GCP projects, links them to enterprise billing, activates necessary service APIs, and establishes the foundational service account mappings. Under this design, the module handles **up to six distinct projects**, reflecting a standard enterprise landing zone.

#### A. Enterprise Team & Project Simulation Mapping
To simulate a real-world enterprise multi-tenant landing zone, we split our architecture across six projects, allowing separate business, engineering, and observability units to operate independently:

| Simulated Team | GCP Project ID | Business & Operational Role | Primary Resources Hosted |
| :--- | :--- | :--- | :--- |
| **Network Ops (NetOps)** | `prj-net-host` | Owns the central networking backbones, Shared VPC, Private Service Connect (PSC), and firewall policies. | Shared VPC, Subnets, Cloud NAT, Cloud DNS, PSC Endpoints. |
| **Platform Ops (PlatformOps)** | `prj-gateway` | Manages the public-facing application ingress, corporate domain name registration, and corporate API Gateways. | Apigee X, Kong Gateway on Cloud Run, external HTTPS Load Balancers. |
| **AppDev Tools Team** | `prj-esmeralda-mcps` | Creates, maintains, and packages reusable Model Context Protocol (MCP) tool servers for the whole company. | Cloud Run (DMS Server, Calculator Server), Artifact Registry. |
| **Core AI Platform Team** | `prj-esmeralda-a2a-agents` | Develops reusable, cross-company Assistant-to-Assistant (A2A) agents, handling centralized business domain reasoning. | Cloud SQL PostgreSQL, Cloud Run Database Bootstrapping Job, Vertex AI Reasoning Engine (A2A). |
| **Business Unit Team** | `prj-esmeralda-root-agent` | Develops the final client-facing user reasoning engine, which acts as the frontend orchestrator and orchestrates upstream agents. | Vertex AI Reasoning Engine (Root), client-facing IAM roles, GCS Buckets. |
| **Security & Governance Hub** | `prj-esmeralda-governance` | Consolidates central security/governance (KMS keys, secrets, certs) and central telemetry/observability (BigQuery dataset, trace views, log sinks), establishing strict Separation of Concerns (SoC) between Platform Governance and Workload Runtimes. | KMS Keyrings, secrets, Certificate Manager certificates, BigQuery Audit Sinks, and log buckets. |

---

#### B. The FinOps Challenge: Cost Attribution & Billing Isolation
Deploying agentic pipelines across multiple distinct GCP projects solves major enterprise pain points but introduces key FinOps and tracking challenges. Our multi-project structure solves these as follows:

```mermaid
graph TD
    %% FinOps visualization
    subgraph Billing["GCP Billing Account (Central Treasury)"]
        Export["Cloud Billing BigQuery Export"]
    end

    subgraph "prj-esmeralda-mcps (AppDev Budget)"
        C1["Cloud Run Compute Costs"]
        L1["Label: cost-center=appdev-tools"]
    end

    subgraph "prj-esmeralda-a2a-agents (AI Platform Budget)"
        C2["Vertex AI Model API Calls"]
        C3["Cloud SQL DB (Continuous Run)"]
        L2["Label: cost-center=core-ai-platform"]
    end

    subgraph "prj-esmeralda-root-agent (Business Unit Revenue Center)"
        C4["Vertex AI Orchestrator API Calls"]
        L3["Label: cost-center=bu-mortgage"]
    end

    C1 & L1 -. Billing Record .-> Export
    C2 & C3 & L2 -. Billing Record .-> Export
    C4 & L3 -. Billing Record .-> Export
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

#### D. File Inventory & Blueprints

```text
infrastructure/modules/1-projects/
├── versions.tf          # Mandates HashiCorp Google provider bounds (>= 5.0)
├── variables.tf         # Inputs for billing, organization folders, prefix, and BYO parameters
├── main.tf              # Implements projects, API enablement, billing bindings, and labels
└── outputs.tf           # Exposes project IDs & numbers to downstream stages
```

##### 1. Versions Specification (`versions.tf`)
> [!TIP]
> 📁 **Source Code File Available:**
> The Terraform code defining module provider dependencies and version constraints is available at:
> 👉 [`versions.tf`](./01_platform_foundations/infrastructure/modules/1-projects/versions.tf)


##### 2. Variables Specification (`variables.tf`)
> [!TIP]
> 📁 **Source Code File Available:**
> The Terraform code defining input variables for project provisioning is available at:
> 👉 [`variables.tf`](./01_platform_foundations/infrastructure/modules/1-projects/variables.tf)


##### 3. Implementation Logic (`main.tf`)
> [!TIP]
> 📁 **Source Code Files Available:**
> The Terraform implementation for project creation and API enablement is divided across:
> - 👉 `main.tf` (Core implementation logic): [`main.tf`](./01_platform_foundations/infrastructure/modules/1-projects/main.tf)
> - 👉 `outputs.tf` (Exported project IDs and numbers): [`outputs.tf`](./01_platform_foundations/infrastructure/modules/1-projects/outputs.tf)

*(With this, any client's NetOps/PlatformOps teams can hand over pre-configured net_host and gateway projects, and Esmeralda will automatically provision and deploy the isolated workload projects and attach them securely to their Shared VPC.)*

---

<a name="stage-2"></a>
## 🌐 2. Stage 2: Private Networking, DNS & PSC

<a name="s2-concepts"></a>
### A. Network Architecture & Topology Guide

Stage 2 implements Zero-Trust network infrastructure to ensure that no API or AI agent communicates over public internet channels.

### A. VPC Subnet Topology (`gateway-vpc`)
In the `prj-net-host` project, we allocate the following CIDR ranges:
*   **Core Workload Subnet (`gke-subnet`)**: `10.0.0.0/20` for internal compute runtimes and test VMs.
*   **Regional Envoy Proxy Subnet (`gateway-proxy-subnet`)**: `10.9.0.0/24` dedicated exclusively to Envoy-based internal Application Load Balancers (ILB).
*   **PSC NAT Subnet (`gateway-psc-subnet`)**: `10.10.0.0/24` for outbound Private Service Connect connections.
*   **PSC Interface Subnet (`psc-interface-subnet`)**: `10.11.0.0/28` providing local private endpoints for Vertex AI Reasoning Engines operating within Google-managed VPCs.

### B. Private DNS and Gateway Static Routing
To decouple dynamic Vertex AI service URLs, we establish a private Cloud DNS zone named `internal.gateway.` pointing all tool and agent routes to the internal IP (`10.0.0.5`) of the Internal Load Balancer (ILB):
*   `email.internal.gateway` -> `10.0.0.5`
*   `income-verification.internal.gateway` -> `10.0.0.5`
*   `dms.internal.gateway` -> `10.0.0.5`

---

<a name="s2-codes"></a>
### B. Detailed Implementation Specifications & HCL Blueprints

# Stage 2: Shared VPC Host Networking & Secure Egress

This module handles core network topologies including private IP address allocation, Shared VPC, Cloud NAT gateway egress, Secure Web Proxy (SWP) whitelist policies, and Private Service Connect (PSC).

### 7.2 Stage 2: `modules/2-networking/` Specification

This module establishes the Shared VPC network, configures the internal subnet routing topologies, sets up Private Service Connect (PSC) Network Attachments, deploys Google Cloud's Secure Web Proxy (SWP) for audited internet egress, and handles corporate DNS zones. It is designed to run on the Shared VPC Host Project (`prj-net-host`), but can be toggled to a pure-attachment mode for pre-existing (brownfield) customer networks.

#### A. Network Subnet and Routing Architecture
In Greenfield mode, the module provisions a comprehensive hub network with dedicated subnets for core workloads, proxy-only routing, PSC endpoints, serverless integration, and database peering:

```mermaid
graph TD
    subgraph HostProject["prj-net-host (Shared VPC Host)"]
        subgraph VPC["Shared VPC Network (vpc-esmeralda-shared)"]
            SubnetCore["Core Backend Subnet<br/>(sb-esmeralda-core)<br/>10.0.1.0/24"]
            SubnetProxy["Regional Proxy Subnet<br/>(sb-esmeralda-proxy)<br/>10.9.0.0/24 (Active)"]
            SubnetPSC["PSC Subnet<br/>(sb-esmeralda-psc)<br/>10.10.0.0/24"]
            SubnetPSC_I["PSC Interface Subnet<br/>(sb-esmeralda-psc-interface)<br/>10.11.0.0/28"]
            PSA["Private Services Access Range<br/>(sql-peering-range)<br/>10.130.0.0/16"]
        end
        Router["Cloud Router"]
        NAT["Cloud NAT (External Outbound)"]
        SWP["Secure Web Proxy (SWP)<br/>Explicit Gateway Filter<br/>10.0.1.100"]
    end

    Router --> NAT
    SubnetCore --> SWP
    SWP --> Router
    PSA -. Internal Peering .-> DB["Private Cloud SQL<br/>(Database Project)"]
```

##### 1. Subnet Classifications
*   **Core Backend Subnet (`sb-esmeralda-core`)**: Host IP range `10.0.1.0/24`. All primary internal workloads (Cloud Run instances, VPC connectors, and private VMs) operate inside this range. Private Google Access is enabled to permit calling Vertex AI and Secret Manager without traversing the public internet.
*   **Regional Envoy Proxy Subnet (`sb-esmeralda-proxy`)**: Host IP range `10.9.0.0/24`. Required by regional internal Application Load Balancers or secure regional gateways (Apigee or Kong). Configured with purpose `REGIONAL_MANAGED_PROXY` and role `ACTIVE`.
*   **PSC Endpoint Subnet (`sb-esmeralda-psc`)**: Host IP range `10.10.0.0/24` reserved for private Google services PSC endpoints.
*   **PSC Interface Subnet (`sb-esmeralda-psc-interface`)**: Host IP range `10.11.0.0/28`. A highly specific regular subnet utilized solely to bind Google-managed Serverless runtimes.
*   **Private Services Access (`sql-peering-range`)**: Allocated block `10.130.0.0/16` reserved for Serverless Cloud SQL Private Services peering connections.

---

#### B. Serverless Bridges: Private Service Connect (PSC) Network Attachment
Because Vertex AI Reasoning Engines and serverless Cloud Run agents reside natively in Google-managed tenant projects, they do not have direct access to resources inside custom customer VPCs. Esmeralda bridges this boundary using a **PSC Network Attachment**:

```mermaid
sequenceDiagram
    autonumber
    participant Agent as Vertex AI Reasoning Engine / Cloud Run Agent
    participant Attachment as PSC Network Attachment<br/>(sb-esmeralda-psc-interface)
    participant VPC as Shared VPC Network<br/>(sb-esmeralda-core)
    participant DB as Private Cloud SQL

    Agent->>Attachment: 1. Initiates PSC Interface Connection
    Attachment->>VPC: 2. Bridges traffic privately into VPC via PSC-I subnet
    VPC->>DB: 3. Connects to database over private IP (VPC Peering)
```

1.  **Subnet Isolation**: A dedicated regular subnetwork (`sb-esmeralda-psc-interface`) is configured.
2.  **Compute Network Attachment**: The resource `google_compute_network_attachment` references the PSC-I subnet and is set to `ACCEPT_AUTOMATIC`. This creates a PSC interface point. Serverless agents use this resource link to establish incoming tunnels directly into the VPC.
3.  **Firewall Protection**: Ingress firewalls are restricted to only allow ports `22` (SSH for debugging), `443` (HTTPS for APIs), and `ICMP` originating from the PSC Interface subnet (`10.11.0.0/28`).

---

#### C. Egress Auditing & Filtering: Secure Web Proxy (SWP)
To comply with enterprise security requirements, reasoning engines and corporate AI agents are not permitted to establish arbitrary connections to the public internet. Stage 2 provisions a **Secure Web Proxy (SWP)** to audit and control egress traffic:
*   **The SWP Instance**: Deployed via GCP's `google_network_security_gateway_security_policy` (or Fabric's `net-swp` module) in the workloads subnet with a static internal IP (`10.0.1.100`).
*   **Egress Interception**: Workloads communicating via the PSC Interface are configured with an explicit proxy pointing to `10.0.1.100:443`.
*   **Access Rules**: Configured with strict session matching and rules (e.g., matching corporate whitelisted LLMs and SaaS tools while blocking unauthorized domains, with error logging activated).

---

#### D. Shared VPC Attachments & Network User IAM Bindings
To allow the separate workload projects to send private traffic across the Shared VPC, they must be attached as service projects, and their respective service agents must be granted subnet execution rights:

```mermaid
graph LR
    Host["prj-net-host<br/>Shared VPC Host"]
    Subnet["sb-esmeralda-core Subnet"]

    Host -. Service Project Attachment .-> P_MCP["prj-esmeralda-mcps"]
    Host -. Service Project Attachment .-> P_A2A["prj-esmeralda-a2a-agents"]
    Host -. Service Project Attachment .-> P_Root["prj-esmeralda-root-agent"]

    SA_MCP["Cloud Run Service Agent (mcps)"] -->|roles/compute.networkUser| Subnet
    SA_A2A["Cloud Run Service Agent (a2a)"] -->|roles/compute.networkUser| Subnet
    SA_Root["Vertex AI Service Agent (root)"] -->|roles/compute.networkUser| Subnet
```

For workloads in service projects to bind VPC Connectors or establish PSC attachments in the host subnet, the host project must authorize the following service agents with the `roles/compute.networkUser` role on the specific subnet:
1.  **Google APIs Service Agent**: `service-[PROJECT_NUMBER]@cloudservices.gserviceaccount.com` (Handles backend resources orchestration).
2.  **Serverless VPC Access Robot**: `service-[PROJECT_NUMBER]@serverless-robot-prod.iam.gserviceaccount.com` (Enables Serverless VPC Access Connectors).
3.  **Vertex AI Agent**: `service-[PROJECT_NUMBER]@gcp-sa-aiplatform.iam.gserviceaccount.com` (Allows Direct VPC egress for Vertex AI Reasoning Engines).

---

#### E. DNS Service Discovery Layout
To facilitate service discovery, Stage 2 deploys two central private DNS zones visible inside the Shared VPC:
1.  **`esmeralda.internal` Zone**: Used for internal service-to-service discovery (e.g., `dms.esmeralda.internal`, `calculator.esmeralda.internal`).
2.  **`internal.gateway` Zone**: Used to peering PSC interfaces. Contains:
    *   Wildcard record `*.internal.gateway.` resolving to the Internal Load Balancer VIP.
    *   Static record `swp.internal.gateway.` resolving to the Secure Web Proxy IP (`10.0.1.100`).

---

#### F. Greenfield vs. Brownfield (BYO) Logic
When `byo_networking = true` is supplied:
*   We **bypass** creating the VPC, subnetworks (core, proxy, psc, psc-interface), Cloud Router, NAT, and the service networking connection.
*   We **still execute** Shared VPC Host Project enablement, Service Project Attachments, Subnet IAM bindings, and DNS Zones, hooking the three new service projects directly into the customer's pre-configured Shared VPC and SWP infrastructure.

---

#### G. File Inventory & Blueprints

```text
infrastructure/modules/2-networking/
├── versions.tf          # Mandates HashiCorp Google provider bounds (>= 5.0)
├── variables.tf         # Host project IDs, service project IDs, CIDR overrides, and BYO flags
├── main.tf              # Greenfield VPC, NAT, PSA + PSC Attachments, SWP, IAM network users, Cloud DNS
└── outputs.tf           # Exports VPC network ID, Subnet self-links, and DNS private zone name
```

##### 1. Versions Specification (`versions.tf`)
> [!TIP]
> 📁 **Source Code File Available:**
> The Terraform code defining networking version constraints is available at:
> 👉 [`versions.tf`](./01_platform_foundations/infrastructure/modules/2-networking/versions.tf)


##### 2. Variables Specification (`variables.tf`)
> [!TIP]
> 📁 **Source Code File Available:**
> The Terraform code defining input variables for VPC subnets and routing is available at:
> 👉 [`variables.tf`](./01_platform_foundations/infrastructure/modules/2-networking/variables.tf)


##### 3. Implementation Logic (`main.tf`)
> [!TIP]
> 📁 **Source Code File Available:**
> The main Terraform configuration implementing VPC topologies, Cloud DNS, and PSC attachments is available at:
> 👉 [`main.tf`](./01_platform_foundations/infrastructure/modules/2-networking/main.tf)


##### 4. Outputs Specification (`outputs.tf`)
> [!TIP]
> 📁 **Source Code File Available:**
> The exported network IDs, subnet links, and DNS names are available at:
> 👉 [`outputs.tf`](./01_platform_foundations/infrastructure/modules/2-networking/outputs.tf)


---

<a name="stage-3"></a>
## 🔐 3. Stage 3: Security, CMEK Keys, Secrets, and Identities

<a name="s3-concepts"></a>
### A. Security Architecture & Identity Guide

Stage 3 centralizes compliance barriers, data governance, and encryption controls within the isolated `prj-esmeralda-governance` project.

### A. CMEK Encryption and Secret Manager
We establish a centralized Cloud KMS Keyring to encrypt persistent data at rest:
*   `key-postgresql`: Encrypts the Cloud SQL database disk in `prj-esmeralda-a2a-agents`.
*   `key-gcs-staging`: Encrypts telemetry audit log storage buckets.

We use Secret Manager to store critical credentials without plaintext disk exposure:
*   `postgresql-admin-password`: Administrative master password for database privilege bootstrapping.

---

### B. 🚨 Least Privilege Identity Compliance
**IMPORTANT (Security Compliance Principle):** 
To enforce least privilege, we do not provision permissions for an `agent_repo` in Artifact Registry for ADK Reasoning Engine agents, because Reasoning Engines do not use Docker containers—they are packaged as compressed `.zip` bundles in GCS buckets. We only grant image write permissions to `mcp_repo` (used by Cloud Run tool services).

Furthermore, we avoid any generic centralized service accounts. Each workload operates under an **isolated, project-specific service identity**:

```mermaid
graph TD
    subgraph Governance["Project: prj-esmeralda-governance"]
        KMS["KMS Keys (CMEK)"]
        Secret["Secret Manager"]
    end

    subgraph MCPs["Project: prj-esmeralda-mcps"]
        SA_MCP["sa-mcp-runtimes@...gserviceaccount.com"] -->|Read Only| Registry["Artifact Registry: mcp-repo"]
    end

    subgraph Agents["Project: prj-esmeralda-a2a-agents"]
        SA_A2A["sa-a2a-agent@...gserviceaccount.com"] -->|Exclusive Access| SQL["Cloud SQL (PostgreSQL)"]
        SA_A2A -->|Read Only| GCS_Agent["GCS: staging-agents-bucket"]
    end

    SA_A2A -. Consumes Keys/Secrets .-> Governance
    SA_MCP -. Consumes Secrets .-> Governance
```

---

---

<a name="s3-codes"></a>
### B. Detailed Implementation Specifications & HCL Blueprints

# Stage 3: Security Keys, Secret Management, and Log Sinks

This module provisions KMS Keyrings and CryptoKeys, sets up Secret Manager secrets, and configures centralized Cloud Logging Sinks routed to BigQuery and Log Analytics buckets.

### 7.3 Stage 3: `modules/3-security/` Specification

This module establishes central customer-managed cryptographic keys (CMEK) via Cloud KMS, configures secure secret storage boundaries in Secret Manager, provisions isolated, least-privilege workload Service Accounts for each engineering domain (including a dedicated Test VM service account and full-parity roles from Esmeralda's monolithic `test-vm-sa`), and hooks up enterprise audit and telemetry log sinks.

Under our centralized governance design, all KMS keyrings, keys, and secrets are created in the centralized **`prj-esmeralda-governance`** project during Stage 3. Workload runtimes (e.g. Cloud SQL in `prj-esmeralda-a2a-agents`) merely consume these resources over cross-project IAM bindings.

#### A. Cryptographic, Secrets, and Identity Isolation Architecture
Stage 3 establishes centralized encryption-at-rest keys, credentials, and cryptographic identities to satisfy strict corporate infosec rules:

```mermaid
graph TD
    subgraph "prj-net-host (Shared VPC Host)"
        DNS["Managed DNS Zone<br/>(dns-esmeralda-internal)"]
    end

    subgraph "prj-esmeralda-governance (Governance & Telemetry Hub)"
        TelemetryLogs["BigQuery Dataset<br/>(esmeralda_telemetry_logs)"]
        
        KeyRing["KMS Keyring<br/>(keyring-esmeralda)"]
        KeySQL["Database Key (CMEK)<br/>(key-esmeralda-sql)"]
        KeySecrets["Secrets Key (CMEK)<br/>(key-esmeralda-secrets)"]
        
        SecretDB["Database Password Secret<br/>(secret-pg-admin-password)"]
    end

    subgraph "prj-esmeralda-a2a-agents (AI Platform Project)"
        SQLRobot["Cloud SQL Service Robot<br/>(service-prj-a2a-sql...)"]
    end

    SQLRobot -->|roles/cloudkms.cryptoKeyEncrypterDecrypter <br/> Cross-Project CMEK Grant| KeySQL
    SecRobot["Secret Manager Service Robot<br/>(service-prj-gov-sm...)"] -->|roles/cloudkms.cryptoKeyEncrypterDecrypter| KeySecrets
```

##### 1. Key Refinements and Additions:
*   **Workload Service Account Roles Alignment**:
    In the monolithic setup, the single `test-vm-sa` account accumulated over 11 roles (including `roles/aiplatform.user`, `roles/storage.objectAdmin`, `roles/telemetry.writer`, and `roles/bigquery.jobUser`) because the VM also acted as the execution identity for the Reasoning Engine. In our enterprise multi-project landing zone, we **split and assign these roles to distinct service accounts** according to the principle of least privilege, guaranteeing full feature-parity:
    *   **`sa-esmeralda-mcps`** (Central Tools Project): Authorized with `roles/logging.logWriter`, `roles/monitoring.metricWriter`, and `roles/cloudtrace.agent` to write application telemetry.
    *   **`sa-esmeralda-a2a`** (AI Platform Project): Fully loaded with the transactional database and AI roles: `roles/cloudsql.client`, `roles/cloudsql.instanceUser`, `roles/aiplatform.user`, `roles/logging.logWriter`, `roles/monitoring.metricWriter`, `roles/cloudtrace.agent`, `roles/storage.objectAdmin` (for reasoning templates GCS buckets), `roles/serviceusage.serviceUsageConsumer`, and `roles/telemetry.writer`.
    *   **`sa-esmeralda-root`** (Business Unit App Project): Fully loaded with the customer reasoning and orchestration roles: `roles/aiplatform.user`, `roles/storage.objectAdmin`, `roles/logging.logWriter`, `roles/monitoring.metricWriter`, `roles/cloudtrace.agent`, `roles/serviceusage.serviceUsageConsumer`, and `roles/telemetry.writer`.
*   **Dedicated Test VM Identity (`sa-esmeralda-test-vm`)**:
    To support connectivity testing, local debugging, and tool testing (DMS, Calculator) from a secure jumpbox without over-privileging operators, we introduce a dedicated Test VM Service Account. It resides in the Business Unit project (`prj-esmeralda-root-agent`) or `prj-net-host` and is assigned:
    *   `roles/logging.logWriter` and `roles/monitoring.metricWriter` for VM health logging.
    *   `roles/run.invoker` inside `prj-esmeralda-mcps` (to invoke private Cloud Run MCP server tools).
    *   `roles/run.invoker` inside `prj-esmeralda-a2a-agents` (to invoke private A2A endpoints or bootstrapping runs).
    *   `roles/aiplatform.user` inside `prj-esmeralda-a2a-agents` (to invoke private Vertex AI Reasoning Engines).
    *   `roles/iam.serviceAccountTokenCreator` on **itself** (allowing the VM's operators to generate short-lived, secure OIDC identity tokens programmatically via the IAM API for curling private microservices).

---

#### B. Greenfield vs. Brownfield (BYO) Logic
When `byo_security = true` is declared in `env.yaml`:
*   KMS Keyrings, KMS Crypto Keys, and Secret Manager Secrets are **completely bypassed** during deployment.
*   Workload Service Accounts, the Test VM Service Account, and their precise IAM role bindings are **still created** and linked back to target project boundaries.
*   Downstream modules switch their inputs to point to the static pre-existing key and secret IDs passed via local configurations.

---

#### C. File Inventory & Blueprints

```text
infrastructure/modules/3-security/
├── versions.tf          # Mandates HashiCorp Google provider bounds (>= 5.0)
├── variables.tf         # Multi-project inputs, BYO KMS/Secret overrides, and project numbers
├── main.tf              # Implements Cloud KMS, secrets, SAs, and log sinks
└── outputs.tf           # Exports SAs, KMS Key IDs, and Secret Resource Names
```

##### 1. Versions Specification (`versions.tf`)
> [!TIP]
> 📁 **Source Code File Available:**
> The Terraform code defining security provider dependencies and version constraints is available at:
> 👉 [`versions.tf`](./01_platform_foundations/infrastructure/modules/3-security/versions.tf)


##### 2. Variables Specification (`variables.tf`)
> [!TIP]
> 📁 **Source Code File Available:**
> The Terraform variables configuration for CMEK keys, identities, and secrets is available at:
> 👉 [`variables.tf`](./01_platform_foundations/infrastructure/modules/3-security/variables.tf)


##### 3. Implementation Logic (`main.tf`)
> [!TIP]
> 📁 **Source Code Files Available:**
> The Terraform configurations provisioning CMEK keys, Secret Manager, least-privilege SAs, and BigQuery telemetry sinks are available at:
> - 👉 `main.tf` (Security and Log Sinks): [`main.tf`](./01_platform_foundations/infrastructure/modules/3-security/main.tf)
> - 👉 `outputs.tf` (Exported Secrets and SAs): [`outputs.tf`](./01_platform_foundations/infrastructure/modules/3-security/outputs.tf)


*(With this Stage 3 Security implementation, our three workload service accounts contain complete, high-fidelity permissions strictly scoped to their respective domain boundaries. We also establish a dedicated Test VM service account with tight invocation and token-creation privileges, customized for secure private VPC endpoints.)*

---
