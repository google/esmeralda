<div align="center">
  <img src="assets/esmeralda_logo.png" alt="Esmeralda logo" width="350" />
  <h1><code>esmeralda</code></h1>
  <p>An opinionated, commercial-grade blueprint designed to accelerate the path to production for AI Agents.</p>

  <p>
    <a href="#-monorepo-repository-structure">Structure</a> &nbsp;&nbsp;|&nbsp;&nbsp;
    <a href="#-the-zero-to-deployed-developer-journey">Onboarding</a> &nbsp;&nbsp;|&nbsp;&nbsp;
    <a href="#-developer-guidelines">Guidelines</a>
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
*   **ME – Multi-agent Engine:** The core orchestration layer utilizing Kong API Gateway or Apigee to govern inter-agent communication and secure egress via Private Service Connect (PSC).
*   **RAL – Reasoning & Action Layer:** Powered by Gemini 2.5 Flash and Model Context Protocol (MCP) standards for complex reasoning and structured JSON execution.
*   **DA – Deployment Accelerator:** A "business-in-a-box" toolkit featuring pre-packaged SOWs, cost simulators, and a Terraform/Terragrunt-driven CI/CD pipeline.

---

Esmeralda is a state-of-the-art, "application-first" monorepo designed to build, test, and run serverless AI Agents and Model Context Protocol (MCP) servers on Google Cloud Platform. By separating runtime application logic from cloud infrastructure, Esmeralda lets software developers focus purely on agent reasoning while platform engineers manage secure, declarative infrastructure with Terragrunt.

---

## 🗺️ Monorepo Repository Structure

Esmeralda is organized into decoupled, clear boundaries separating pure application logic, platform infrastructure, tests, and documentation:

```text
esmeralda/
├── apps/                                    # 🟢 PURE APPLICATION CODE (No IaC)
│   ├── agents/                              # AI reasoning engine ADK packages
│   │   ├── base-adk-agent/                  # Root Orchestrator Python package (mortgage-agent)
│   │   └── a2a-agent/                       # Downstream A2A Specialist Python package (a2a-mortgage-agent)
│   └── services/                            # Reusable tool API servers & gateway services
│       ├── corporate-email/                 # Email integration MCP server
│       ├── income-verification/             # Income verifier MCP server
│       ├── legacy-dms/                      # File archive integration MCP server
│       ├── kong/                            # Kong API Gateway container & configuration
│       ├── mcp_registry.json                # Service registry tracking deployed MCP endpoints
│       └── register_mcp.py                  # Utility script to register MCP endpoints
│
├── docs/                                    # 📖 ARCHITECTURE & DEVELOPER DOCUMENTATION
│   ├── 1-platform-foundations/              # Landing zone, Shared VPC & security docs
│   ├── 2-workloads-and-catalog/             # Workloads & MCP server catalog docs
│   ├── 3-agentops-and-lifecycle/            # AgentOps, evaluation & lifecycle docs
│   ├── developer-quickstart.md              # Step-by-step developer onboarding guide
│   ├── contributing.md                      # Contribution rules & instructions
│   └── code-of-conduct.md                   # Community code of conduct
│
├── infrastructure/                          # 🔵 DECOUPLED PLATFORM IAC STACK (Terragrunt & Terraform)
│   ├── modules/                             # Reusable Terraform modules (The Shelf)
│   │   ├── 1-projects/                      # Stage 1: Seeds projects & enables APIs
│   │   ├── 2-networking/                    # Stage 2: Shared VPC, subnets, private DNS, NAT
│   │   ├── 3-security/                      # Stage 3: KMS keys, secrets, audit log sinks
│   │   └── 4-workloads/                     # Stage 4: Workload runtime specs (agents, services, apihub)
│   └── live/                                # Live Environment Wiring (The Cart)
│       ├── terragrunt.hcl                   # Global state bucket & providers definition
│       ├── dev/                             # Dev environment (stages 1 through 4)
│       └── prd/                             # Production environment
│
├── tests/                                   # 🧪 TEST SUITES
│   ├── unit/                                # Unit test implementations
│   ├── integration/                         # Cross-service integration tests
│   └── load_test/                           # Locust performance & stress tests
│
├── .cloudbuild/                             # ⚙️ CI/CD BUILD PIPELINES
├── assets/                                  # 🖼️ MONOREPO ASSETS & LOGOS
├── Makefile                                 # Unified task runner (local commands & GCP deployments)
├── README.md                                # Root developer guide and setup manual
├── preflight.sh                             # GCP authentication & credentials check
└── pyproject.toml                           # Central monorepo workspace dependencies via uv
```

---

## 🚀 The "Zero-to-Deployed" Developer Journey

To go from a freshly cloned repository to a fully-deployed secure application in Google Cloud, follow these steps:

### Phase 1: Local Onboarding & Bootstrapping

1. **Verify Prerequisites**:
   Make sure you have `gcloud SDK`, `terraform`, `terragrunt`, and `uv` installed, then run the preflight script:
   ```bash
   make preflight
   ```

2. **Local Environment Setup**:
   Install all Python packages and sync dependencies across the monorepo using Astral `uv`:
   ```bash
   make bootstrap
   ```
   *This creates an isolated virtual environment and automatically links all workspace members.*

3. **Verify Local Tests**:
   Run the full unit test suite across all agents and MCP servers to ensure parity:
   ```bash
   make test
   ```

4. **Run MCP Servers Locally**:
   Run the MCP servers concurrently on separate localhost ports for local development:
   ```bash
   make run-mcp-local
   ```
   - **Corporate Email**: `http://localhost:8001`
   - **Income Verification**: `http://localhost:8002`
   - **Legacy DMS**: `http://localhost:8003`

5. **Local Agent Integration Testing (Automated Server Management)**:
   You can run local integration tests for the downstream (A2A) and root coordinator agents. These targets automatically spin up the 3 local MCP servers in the background (using `make run-mcp-local`), wait for them to initialize, execute the integration query, and cleanly tear down all background servers on completion or interruption (e.g. `Ctrl+C`).

   Run Downstream Agent (A2A) integration test:
   ```bash
   make test-a2a-local
   ```

   Run Root Coordinator Agent integration test (in-memory routing):
   ```bash
   make test-root-local
   ```

---

### Phase 2: Deploying to Google Cloud (Terragrunt)

Deployments are driven stage-by-stage to preserve clean architectural boundaries and isolate service-level dependencies.

1. **Populate Environment Configuration**:
   Create or verify your target environment variables in `infrastructure/live/dev/env.yaml`:
   ```yaml
   # infrastructure/live/dev/env.yaml
   locals {
     environment     = "dev"
     project_prefix  = "esmeralda"
     region          = "us-central1"
     gateway_product = "kong"
   }
   ```

2. **Provision Foundations**:
   You can deploy all foundational stages (Stages 1, 2, and 3) together using:
   ```bash
   make deploy-foundations
   ```

   Alternatively, for better step-by-step control, inspection, and verification, you can deploy each foundational stage individually:
   ```bash
   # Stage 1: Provision GCP projects, enable service APIs, and force-bootstrap service agents
   make deploy-projects

   # Stage 2: Establish Shared VPC hosting, private subnets, and subnet-level network user IAM permissions
   make deploy-networking

   # Stage 3: Create workload service accounts, KMS keyrings/keys, and grant CMEK caller permissions
   make deploy-security
   ```
   *Note: If 'state_project' and 'state_bucket' are left blank in `env.yaml`, Terragrunt automatically provisions and manages all resource state locally in `.local_states/` (which is git-ignored), allowing fast, zero-dependency local sandboxing! If you provide a pre-existing GCP Project and GCS Bucket in `env.yaml`, Terragrunt dynamically switches to GCS remote state management.*

3. **Build Workload Container Images**:
   Build and push container images to Artifact Registry via Cloud Build before deploying workloads:
   ```bash
   # Build all agent containers (A2A & Root Agent) concurrently
   make build-agents

   # Build all service containers (income-verification, corporate-email, legacy-dms, kong) concurrently
   make build-services
   ```

4. **Provision Runtimes & Workloads**:
   Deploy Stage 4 workloads (Cloud Run MCP services, Kong Gateway, and Reasoning Engine agents):
   ```bash
   # Full automated build and deployment of all Stage 4 workloads
   make deploy-workloads
   ```

   Alternatively, deploy Stage 4 components individually:
   ```bash
   # Step 4.1: Deploy Artifact Registry Docker repository
   make deploy-repo

   # Step 4.2: Deploy Cloud Run services
   make deploy-services

   # Step 4.3: Deploy Kong API Gateway
   make deploy-gateway

   # Step 4.4: Deploy A2A Reasoning Engine Agent
   make deploy-agent-a2a

   # Step 4.5: Deploy Root Coordinator Reasoning Engine Agent
   make deploy-agent-root
   ```

---

## 💡 Developer Guidelines

### 🟢 Application Developers (Writing Code in `apps/`)
- Avoid mixing infrastructure shell scripts or Terraform definitions inside `apps/`.
- All Python requirements must be added to individual `pyproject.toml` files inside subdirectories, or at the root if shared.
- Run `make test` before pushing to verify you haven't introduced regressions.

### 🔵 Platform Engineers (Writing IaC in `infrastructure/`)
- Maintain structural encapsulation. Never hardcode workspace names or project IDs inside `modules/`.
- All environment configurations must reside strictly in `infrastructure/live/<env>/env.yaml`.
- Ensure new resources are bound to Stage 3 KMS keys and use Stage 2 subnets for Private Service Connect.

---

> [!IMPORTANT]
> **Credential Security Checklist**
> - Never commit your `.env` file or any `*.tfvars` files.
> - Ensure you have logged in via Application Default Credentials (ADC) to permit Terragrunt to authorize operations:
>   `gcloud auth application-default login`

> [!TIP]
> **Locust Load Testing**
> Performance and stress test scripts are available in `tests/load_test/`. If you have locust installed in your local environment, you can run performance tests against deployed endpoints:
> `locust -f tests/load_test/locustfile.py`

