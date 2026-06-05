<div align="center">
  <img src="assets/esmeralda_logo.png" alt="Esmeralda logo" width="350" />
  <h1><code>esmeralda</code></h1>
  <p>An opinionated, commercial-grade blueprint designed to accelerate the path to production for AI Agents.</p>

  <p>
    <a href="#-dont-come-crying-to-me-later-checklist">Checklist</a> &nbsp;&nbsp;|&nbsp;&nbsp;
    <a href="#-quick-start-monorepo-mode">Quick Start</a> &nbsp;&nbsp;|&nbsp;&nbsp;
    <a href="#-modular-deployment-silo-mode">Architecture</a>
  </p>

  <p>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License" /></a>
    <a href="https://cloud.google.com/"><img src="https://img.shields.io/badge/Cloud-Google_Cloud-4285F4?style=flat&logo=google-cloud&logoColor=white" alt="Google Cloud" /></a>
    <a href="https://www.terraform.io/"><img src="https://img.shields.io/badge/IAC-Terraform-623CE4?style=flat&logo=terraform&logoColor=white" alt="Terraform" /></a>
    <a href="https://cloud.google.com/vertex-ai"><img src="https://img.shields.io/badge/AI-Vertex_AI-00C1FF?style=flat&logo=google-cloud&logoColor=white" alt="Vertex AI" /></a>
  </p>
</div>

---

### What does ESMERALDA stand for?
ESMERALDA is an opinionated, commercial-grade blueprint designed to accelerate the path to production for AI Agents. The acronym represents the framework's four core pillars:

*   **ES – Enterprise Standard:** A zero-trust, secure-by-default foundational architecture built for enterprise compliance and scale.
*   **ME – Multi-agent Engine:** The core orchestration layer utilizing Apigee to govern inter-agent communication and secure egress via Private Service Connect (PSC).
*   **RAL – Reasoning & Action Layer:** Powered by Gemini 2.5 Flash and Model Context Protocol (MCP) standards for complex reasoning and structured JSON execution.
*   **DA – Deployment Accelerator:** A "business-in-a-box" toolkit featuring pre-packaged SOWs, cost simulators, and a Terraform-driven CI/CD pipeline.

---

## 📖 Overview

**ESMERALDA** is a code-first deployment blueprint designed to bridge the gap between "prototype" and "production" for AI Agents. It applies software engineering best practices—like modularity, Infrastructure as Code (IaC), and automated discovery—to the **Vertex AI Reasoning Engine** ecosystem. It integrates advanced security mechanisms such as Model Armor for prompt/response safety and Envoy `ext_proc` sidecars for DLP redaction.
## 😭 "Don't come crying to me later" Checklist

To save you time, tears, and deployment errors, we provide an automated preflight checklist script that verifies all prerequisites (CLI tools, authentication state, active project context, and billing status).

1. **Configure**:
   First, create and configure your environment file:
   ```bash
   cp env.example .env
   # Fill in your ORG_ID and BILLING_ACCOUNT in the new .env file.
   ```

2. **Preflight**:
   Once configured, run the preflight check:
   ```bash
   make preflight
   ```

The script will automatically verify and report on:
1. **Python 3.10+** (is installed and meets minimum version requirement)
2. **Terraform 1.5+** (is installed and meets minimum version requirement)
3. **Google Cloud SDK (`gcloud`)** (is installed)
4. **Active Google Cloud Project** (ensures your project context is set correctly)
5. **Application Default Credentials (ADC)** (verifies active authenticated credentials for Terraform)
6. **Project Billing Status** (ensures Google Cloud billing is active for the target project)

> [!TIP]
> If any checks fail, follow the interactive hints printed by the script (e.g. commands to authenticate or configure your context) to quickly resolve them.

## 🚀 Quick Start (Monorepo Mode)

If you are a single developer deploying everything from this repository:

1.  **Deploy All**:
    ```bash
    make all
    ```

## 💻 Local Testing & Inner Loop

For local development and testing, ESMERALDA supports zero-dependency local simulation of MCP servers and agents without requiring OIDC/IAM cloud credentials.

1.  **Bootstrap Local Python Environments**:
    Provision dedicated virtual environments and install all required packages:
    ```bash
    make bootstrap
    ```

2.  **Run Mock MCP Servers Locally**:
    Spin up all three local mock MCP servers concurrently on dedicated loopback ports (`8011`, `8012`, `8013`) with built-in healthchecks:
    ```bash
    make run-mcp
    ```

3.  **Run Agent Integration Tests**:
    With local servers running, you can execute the agent test suite in local mode. Open another terminal and run:
    ```bash
    export DMS_MCP_URL="http://localhost:8013/mcp"
    export INCOME_VERIFICATION_URL="http://localhost:8012/mcp"
    export EMAIL_MCP_URL="http://localhost:8011/mcp"
    export LOCAL_MODE="true"
    ./agents/remotes/a2a-agent/.venv/bin/python agents/remotes/a2a-agent/test_local.py
    ```

## 🏗️ Modular Deployment (Silo Mode)

The true power of this framework is its modularity. Each folder is **self-contained** and can be moved to its own Git repository to be managed by different teams.

The beauty of this modular design is its simplicity. Each folder is a self-contained unit: **just `cd` into the directory you want to deploy and run its specific `make` command.**

### 1. Infrastructure (`infrastructure/`)

Manages the Google Cloud project, IAM, VPC networks, and security guardrails (Model Armor).
*   **Configure**: `cp env.example .env`
*   **To deploy**: `cd infrastructure && make infra`
*   **CI/CD**: See `infrastructure/cloudbuild.yaml`

### 2. Tools (`tools_mcp/`)

Deploys Model Context Protocol servers to Cloud Run, automatically registering them to the Agent Registry and API Hub by dynamically discovering the live endpoints. Includes demo tools such as Corporate Email, Income Verification API, and Legacy DMS.
*   **Configure**: `cp env.example .env`
*   **To deploy**: `cd tools_mcp && make tools`
*   **CI/CD**: See `tools_mcp/cloudbuild.yaml`

### 3. Agents (`agents/`)

Deploys Vertex AI Reasoning Engines with secure Private Service Connect (PSC) attachments.
*   **Important**: Before deploying, set your gcloud project context: `gcloud config set project [YOUR_NEWLY_CREATED_PROJECT_ID]`. This is required for auto-discovery to work.
*   **Configure**: `cp env.example .env`
*   **To deploy**: `cd agents && make deploy-python` (or `make deploy-terraform` / `make deploy-python-cb` / `make deploy-terraform-cb`)
*   **CI/CD**: See `agents/infra/python/cloudbuild.yaml` and `agents/infra/terraform/cloudbuild.yaml`
## 🏗️ Esmeralda Agent Platform Architecture

Esmeralda deploys a commercial-grade, multi-tier private architecture designed to orchestrate secure Vertex AI Reasoning Engines (AI Agents) and Model Context Protocol (MCP) servers.

```mermaid
graph TD
    subgraph "Google Managed Tenant VPC"
        subgraph "Vertex AI Reasoning Engines"
            A2A[a2a-mortgage-agent]
            ADK[base-adk-agent]
            NET[network-test-agent]
        end
    end

    subgraph "Consumer VPC (gateway-vpc)"
        direction TB
        subgraph "Networking Subnets"
            S_GKE[gke-subnet: 10.0.0.0/20]
            S_PROXY[gateway-proxy-subnet: 10.9.0.0/24]
            S_PSC_NAT[gateway-psc-subnet: 10.10.0.0/24]
            S_PSC_INT[psc-interface-subnet: 10.11.0.0/28]
        end

        ILB[Internal Load Balancer]
        DNS[Private DNS Zone: internal.gateway.]
        
        subgraph "Private Compute & Data"
            VM[test-vm]
            DB[("Cloud SQL Postgres DB")]
        end

        subgraph "Cloud Run MCP Servers"
            CR_EMAIL[corporate-email]
            CR_INCOME[income-verification-api]
            CR_DMS[legacy-dms]
        end
    end

    %% Networking & DNS Connections
    A2A -->|PSC Interface| S_PSC_INT
    ADK -->|PSC Interface| S_PSC_INT
    NET -->|PSC Interface| S_PSC_INT
    
    S_PSC_INT -->|Secure Egress Routing| ILB
    ILB -->|HTTP Internal routing| CR_EMAIL
    ILB -->|HTTP Internal routing| CR_INCOME
    ILB -->|HTTP Internal routing| CR_DMS
    
    A2A -->|IAM Auth Connect| DB
    VM -->|Private SQL Connect| DB

    classDef gcp fill:#4285F4,stroke:#333,stroke-width:1px,color:#fff;
    classDef comp fill:#34A853,stroke:#333,stroke-width:1px,color:#fff;
    class A2A,ADK,NET,CR_EMAIL,CR_INCOME,CR_DMS,VM,DB gcp;
```

### 1. Secure VPC Networking Core
* **Private VPC (`gateway-vpc`)**: Isolated virtual private network including:
  * **Primary Subnet (`gke-subnet` - `10.0.0.0/20`)**: Hosts the private GCE `test-vm`.
  * **Managed Proxy Subnet (`10.9.0.0/24`)**: Powers regional/internal managed HTTPS load balancers.
  * **PSC NAT Subnet (`10.10.0.0/24`)**: Manages IPs for Private Service Connect network attachments.
  * **PSC Interface Subnet (`10.11.0.0/28`)**: Connects the Vertex AI Reasoning Engines (agents) to the VPC via a Private Service Connect Network Attachment (`gateway-psc-interface-attachment`).
* **Internal Load Balancer (ILB)**: Serves as the central private ingress gateway inside the VPC, exposing private HTTPS endpoints for all MCP servers.
* **Private DNS Zone (`internal.gateway.`)**: Automatically resolves internal service domain names like `email.internal.gateway`, `income-verification.internal.gateway`, and `dms.internal.gateway` directly to the ILB's private IP.

### 2. Model Context Protocol (MCP) Tier
Three fully managed MCP servers are deployed securely onto **Google Cloud Run**:
1. **Corporate Email (`corporate-email`)**: Exposes mock corporate email operations.
2. **Income Verification API (`income-verification-api`)**: Validates customer income statements.
3. **Legacy DMS (`legacy-dms`)**: Manages mock document repository operations.

* **Zero-Trust Access**: Cloud Run instances are configured with `--no-allow-unauthenticated` and `--ingress=internal-and-cloud-load-balancing` to ensure no public internet access is permitted.
* **Service Discovery**: The `register_mcp.py` utility automatically scans and registers these live server endpoints in both **API Hub** and **Agent Registry** for seamless discovery.

### 3. Vertex AI Reasoning Engines (AI Agents Tier)
Three autonomous reasoning engines are deployed as Vertex AI Reasoning Engines. Since they run in a Google-managed tenant VPC, they use **PSC Interfaces** to peer with the customer VPC and route requests via the `internal.gateway.` DNS zone:
1. **`a2a-agent` (A2A Mortgage Agent)**: An orchestration engine powered by `gemini-2.5-flash` that interacts with the private Cloud SQL database, utilizing the custom-registered MCP tools (Email, DMS, Income Verification) via the private ILB endpoints.
2. **`base-adk-agent`**: A root consumer agent utilizing the Google Agent Development Kit (ADK) that connects to and orchestrates the downstream `a2a-agent`.
3. **`network-test-agent`**: A utility agent used to test the secure private egress networking and OIDC token generation over the PSC interface.

### 4. Enterprise Governance, State, and Telemetry
* **Model Armor**: Provides input and output safety guardrails (prompt injection filtering, toxic content defense).
* **State / Task Store (Cloud SQL PostgreSQL)**: A private Postgres DB instance (`a2a-agent-pg`) within the VPC that persists agent state and transaction histories, utilizing IAM-based database authentication.
* **OpenTelemetry Observability**: Automatically instruments reasoning engines to collect and export GenAI-specific traces and metrics.
* **GCS Log Offloading**: Secure Cloud Storage buckets offload and archive raw trace files and multimodal completion payloads.
* **BigQuery Logging (`agent_logs`)**: Automatically captures structured agent events and spans for real-time compliance audits, dashboards, and BI reports.

## ⚙️ Esmeralda Repository & Framework Features

*   **Zero-Dependency Silos**: Each folder has its own `Makefile`, `env.example`, and deployment logic. No shared script files.
*   **Context Discovery**: Scripts are "intelligent"—if a variable is missing from `.env`, they will attempt to discover it using the `gcloud` CLI.
*   **Safe Resume**: State is automatically saved to `.env`. If a step fails, you can fix it and rerun **only that silo**.
*   **Local Developer Inner Loop**: Concurrent mock MCP runner (`make run-mcp`) and isolated Python virtual environments (`make bootstrap`) let developers build, test, and debug agents offline with zero OIDC/IAM credential overhead.
*   **Automated Preflight Guardrails**: Instant CLI validation (`make preflight`) verifying all prerequisites (Python version, Terraform version, active project, and billing state) to prevent deployment headaches before they start.


## 🔍 Usage

### Curl Query Examples

Once deployed, you can interact with your agent using the following streaming query command (replace the placeholders with your actual values):

```bash
curl -H "Authorization: Bearer \$(gcloud auth application-default print-access-token)" \
-H "Content-Type: application/json" \
https://<YOUR_PROJECT_LOCATION>-aiplatform.googleapis.com/v1/projects/<YOUR_PROJECT_ID>/locations/<YOUR_PROJECT_LOCATION>/reasoningEngines/<YOUR_REASONING_ENGINE_ID>:streamQuery?alt=sse -d '{
  "class_method": "async_stream_query",
  "input": {
    "user_id": "test-user-1",
    "message": "what is your network config with proxy?",
    "caller_context": "observability-context"
  }
}'
```