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
*   **ME – Multi-agent Engine:** The core orchestration layer utilizing Apigee to govern inter-agent communication and secure egress via Private Service Connect (PSC).
*   **RAL – Reasoning & Action Layer:** Powered by Gemini 2.5 Flash and Model Context Protocol (MCP) standards for complex reasoning and structured JSON execution.
*   **DA – Deployment Accelerator:** A "business-in-a-box" toolkit featuring pre-packaged SOWs, cost simulators, and a Terraform-driven CI/CD pipeline.

---

Esmeralda is a state-of-the-art, "application-first" monorepo designed to build, test, and run serverless AI Agents and Model Context Protocol (MCP) servers on Google Cloud Platform. By separating runtime application logic from cloud infrastructure, Esmeralda lets software developers focus purely on agent reasoning while platform engineers manage secure, declarative infrastructure with Terragrunt.

---

## 🗺️ Monorepo Repository Structure

Esmeralda is organized into three strictly-decoupled, clear boundaries:

```text
esmeralda/
├── app/                                     # 🟢 PURE APPLICATION CODE (No IaC)
│   ├── agents/                              # AI reasoning engine ADK codes
│   │   ├── base-adk-agent/                  # Root Orchestrator Python package
│   │   └── a2a-agent/                       # downstream agent
│   └── mcp-servers/                         # Reusable corporate tool API servers
│       ├── corporate-email/                 # Email integration MCP server
│       ├── income-verification/             # Income verifier MCP server
│       └── legacy-dms/                      # File archive integration MCP server
│
├── infrastructure/                          # 🔵 DECOUPLED PLATFORM IAC STACK
│   ├── modules/                             # Reusable Terraform modules (The Shelf)
│   │   ├── 1-projects/                      # Stage 1: Seeds projects & enables APIs
│   │   ├── 2-networking/                    # Stage 2: Shared VPC, subnets, private DNS, NAT
│   │   ├── 3-security/                      # Stage 3: KMS keys, secrets, audit log sinks
│   │   └── 4-workloads/                     # Stage 4: Workload runtime specs
│   └── live/                                # Live Environment Wiring (The Cart)
│       ├── terragrunt.hcl                   # Global state bucket & providers definition
│       └── dev/                             # Environment boundaries (dev, prod)
│           ├── env.yaml                     # Environment config file
│           ├── stage-1-projects/
│           ├── stage-2-networking/
│           ├── stage-3-security/
│           └── stage-4-workloads/
│
├── Makefile                                 # Unified task runner (local commands)
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
   make run-local
   ```
   - **Corporate Email**: `http://localhost:8001`
   - **Income Verification**: `http://localhost:8002`
   - **Legacy DMS**: `http://localhost:8003`

---

### Phase 2: Deploying to Google Cloud (Terragrunt)

Deployments are driven stage-by-stage to preserve clean architectural boundaries and isolate service-level dependencies.

1. **Populate Environment Configuration**:
   Create or verify your target environment variables in `infrastructure/live/dev/env.yaml`:
   ```yaml
   # infrastructure/live/dev/env.yaml
   locals {
     environment    = "dev"
     project_prefix = "esmeralda"
     region         = "us-central1"
     gateway_product = "kong"
   }
   ```

2. **Provision Foundations**:
   Deploy Stages 1, 2, and 3 (Projects, Network, Security SAs, and KMS keys):
   ```bash
   make deploy-foundations
   ```

3. **Provision Runtimes & Workloads**:
   Deploy Stage 4 (Dockerized Cloud Run MCP services, Vertex AI Reasoning Engine zips, and Routing Gateways):
   ```bash
   make deploy-workloads
   ```

---

## 💡 Developer Guidelines

### 🟢 Application Developers (Writing Code in `app/`)
- Avoid mixing infrastructure shell scripts or Terraform definitions inside `app/`.
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
> If you have locust installed in your local environment, you can trigger stress tests after deploying your live workloads by calling:
> `make stress-test`
