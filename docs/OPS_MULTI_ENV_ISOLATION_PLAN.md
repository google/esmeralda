# Esmeralda Ops & Multi-Environment Isolation Plan (GitOps Refactoring)

> [!CAUTION]
> **FRIDAY DEMO IMMUTABILITY GUARANTEE**:
> The existing active GCP project suite (`esmeralda-net-host-3a3d`, `esmeralda-governance-3a3d`, `esmeralda-mcps-3a3d`, `esmeralda-root-agent-3a3d`, `esmeralda-a2a-3a3d`, `esmeralda-gateway-3a3d`) **MUST NOT BE TOUCHED OR MUTATED**. It serves as our live, end-to-end verified backup environment for Friday's customer presentation.
>
> All new `DEV` and `PRD` infrastructure provisions brand-new project suites (`esmeralda-dev-*` and `esmeralda-prd-*`). The **only** project reused across environments is `esmeralda-cicd-artifacts-3a3d`, which acts as the organization-wide CI/CD builder, Artifact Registry host, and Terragrunt remote state hub.

---

## 1. Executive Summary & Architectural Motivation

To scale Esmeralda from a single-project sandbox into an enterprise-grade Google Cloud reference platform, we must separate **Runtime Cloud Infrastructure** into strictly isolated environment project suites (`DEV` and `PRD`) while maintaining a **Single Trunk-Based Git Monorepo** (`main` branch).

### Why Branch-per-Environment (`dev` vs `prd` Git branches) Fails in a Monorepo
When multiple microservices (`legacy-dms`, `income-verification`), agents (`root-agent`, `a2a-agent`), and infrastructure modules live inside one repository:
* Merging a `dev` branch into a `prd` branch merges **all** unreleased microservices and half-finished Terraform changes across the entire monorepo simultaneously.
* You cannot promote a single verified agent or tool without dragging unrelated experimental commits into production.

### The Esmeralda GitOps Solution
1. **Folder-Scoped Environment Landing Zones**:
   * `infrastructure/live/dev/` $\rightarrow$ deploys to fresh `esmeralda-dev-*` GCP projects.
   * `infrastructure/live/prd/` $\rightarrow$ deploys to fresh `esmeralda-prd-*` GCP projects.
2. **Immutable Container Version Pinning**:
   * `apps/` microservices and agent containers are versioned by Docker tags (`:latest` / `:git-sha` for DEV; `:v1.0.0` / `:demo-stable` for PRD).
   * Promoting an app from DEV to PRD is a single line change in `live/prd/.../terragrunt.hcl` updating the container tag.
3. **Environment-Parameterized Ops CLI (`Makefile`)**:
   * All `make` targets support `ENV ?= dev`, allowing engineers to deploy or build for any environment (`make deploy-agents ENV=prd`).

---

## 2. Multi-Environment GCP Project Allocation Matrix

| Project Role | Shared Org Hub | `DEV` Suite (`live/dev/`) | `PRD` Suite (`live/prd/`) |
| :--- | :--- | :--- | :--- |
| **CI/CD & Artifacts** | `esmeralda-cicd-artifacts-3a3d` *(Reused)* | `byo_cicd_project = true` | `byo_cicd_project = true` |
| **Shared VPC Host** | — | `esmeralda-dev-net-host-xxxx` | `esmeralda-prd-net-host-yyyy` |
| **Governance & Registry** | — | `esmeralda-dev-governance-xxxx` | `esmeralda-prd-governance-yyyy` |
| **API Gateway Ingress** | — | `esmeralda-dev-gateway-xxxx` | `esmeralda-prd-gateway-yyyy` |
| **MCP Tool Servers** | — | `esmeralda-dev-mcps-xxxx` | `esmeralda-prd-mcps-yyyy` |
| **LOB Root Agent** | — | `esmeralda-dev-root-agent-xxxx` | `esmeralda-prd-root-agent-yyyy` |
| **A2A Mortgage Specialist**| — | `esmeralda-dev-a2a-xxxx` | `esmeralda-prd-a2a-yyyy` |

### Why Reusing `esmeralda-cicd-artifacts-3a3d` is Critical
* **Build Once, Run Everywhere**: Containers compiled by Cloud Build push to `gcr.io/.../esmeralda-cicd-artifacts-3a3d/esmeralda-containers/...`. Both DEV and PRD pull from this identical registry, guaranteeing binary parity.
* **Two-Vault Pointer Resolution**: In future Agent Gateway steps, CI/CD steps read `secret-esmeralda-governance-id-${ENV}` from this shared vault to locate the target environment's Agent Registry.

---

## 3. Container Tagging & Promotion Architecture

### A. CI/CD Tagging Convention
When Cloud Build compiles containers (`apps/services/*`, `apps/agents/*`), images receive multiple semantic tags:
1. **Commit SHA Tag**: `gcr.io/.../esmeralda-containers/a2a-agent:sha-a1b2c3d` (Unique immutable build identifier).
2. **Environment Tag**: `gcr.io/.../esmeralda-containers/a2a-agent:dev-latest` (Rolling tag for `DEV`).
3. **Release Tag**: `gcr.io/.../esmeralda-containers/a2a-agent:v1.0.0` (Pinned production release tag for `PRD`).

### B. Terragrunt Workload Pinning
In `infrastructure/live/dev/stage-4-workloads/agents/a2a-agent/terragrunt.hcl`:
```hcl
inputs = {
  container_image = "${region}-docker.pkg.dev/${cicd_proj}/esmeralda-containers/a2a-agent:dev-latest"
}
```

In `infrastructure/live/prd/stage-4-workloads/agents/a2a-agent/terragrunt.hcl`:
```hcl
inputs = {
  # Strictly pinned to verified production demo release tag
  container_image = "${region}-docker.pkg.dev/${cicd_proj}/esmeralda-containers/a2a-agent:v1.0.0"
}
```

---

## 4. Parameterizing `Makefile` for Multi-Environment Execution

Refactor `Makefile` to dynamically target `infrastructure/live/$(ENV)` instead of hardcoding `dev`:

```makefile
ENV ?= dev
LIVE_DIR = infrastructure/live/$(ENV)

deploy-projects: ## Deploy Stage 1 Projects for $(ENV)
	@echo "🚀 Deploying Stage 1 GCP Projects for environment $(ENV)..."
	@cd $(LIVE_DIR)/stage-1-projects && terragrunt --non-interactive apply -auto-approve

deploy-networking: ## Deploy Stage 2 Networking for $(ENV)
	@echo "🌐 Deploying Stage 2 Shared VPC Networking for environment $(ENV)..."
	@cd $(LIVE_DIR)/stage-2-networking && terragrunt --non-interactive apply -auto-approve

deploy-security: ## Deploy Stage 3 Security & IAM for $(ENV)
	@echo "🔒 Deploying Stage 3 Security & IAM for environment $(ENV)..."
	@cd $(LIVE_DIR)/stage-3-security && terragrunt --non-interactive apply -auto-approve

deploy-mcps: ## Deploy Stage 4 MCP Servers for $(ENV)
	@echo "📡 Deploying MCP Tool Servers for environment $(ENV)..."
	@cd $(LIVE_DIR)/stage-4-workloads/services && terragrunt --non-interactive run --all apply

deploy-agents: ## Deploy Stage 4 Reasoning Engine Agents for $(ENV)
	@echo "🤖 Deploying Reasoning Engine Agents for environment $(ENV)..."
	@cd $(LIVE_DIR)/stage-4-workloads/agents/a2a-agent && terragrunt --non-interactive apply -auto-approve
	@cd $(LIVE_DIR)/stage-4-workloads/agents/base-adk-agent && terragrunt --non-interactive apply -auto-approve

build-agents: ## Build container images and tag for $(ENV)
	@echo "🏗️  Building container images for $(ENV)..."
	@gcloud builds submit apps/agents/a2a-agent --substitutions=_ENV=$(ENV),_TAG=$(ENV)-latest ...
```

---

## 5. Step-by-Step Implementation & Verification Checklist

- [ ] **Step 1: Configure `live/dev/env.yaml`**
  * Set `project_prefix: "esmeralda-dev"`.
  * Set `byo_cicd_project: true` and `existing_cicd_project: "esmeralda-cicd-artifacts-3a3d"`.
- [ ] **Step 2: Configure `live/prd/env.yaml`**
  * Set `project_prefix: "esmeralda-prd"`.
  * Set `byo_cicd_project: true` and `existing_cicd_project: "esmeralda-cicd-artifacts-3a3d"`.
  * Verify `byo_networking = false`, `byo_net_host_project = false` so PRD creates a fresh Shared VPC (`vpc-esmeralda-shared-prd`).
- [ ] **Step 3: Update Container Image References in `live/prd/`**
  * Pin `container_image` in PRD `terragrunt.hcl` files (`a2a-agent`, `base-adk-agent`, `legacy-dms`, `income-verification`, `corporate-email`) to `:v1.0.0` (or `:demo-stable`).
- [ ] **Step 4: Refactor `Makefile` with `ENV ?= dev`**
  * Replace hardcoded `/dev/` paths with `$(LIVE_DIR)`.
  * Add multi-environment build targets (`make build-all ENV=prd TAG=v1.0.0`).
- [ ] **Step 5: Dry-Run Verification (`terragrunt plan`)**
  * Run `make deploy-projects ENV=prd --dry-run` to verify project generation without touching `esmeralda-*-3a3d`.
