# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Execute all commands using interactive bash with alias expansion enabled (critical for Cloudtop & IDEs)
SHELL := /bin/bash
.SHELLFLAGS := -O expand_aliases -lc

.PHONY: help bootstrap test run-mcp-local test-a2a-local test-root-local deploy-foundations deploy-projects deploy-networking deploy-security build-agents deploy-workloads build-service-circuit-breaker deploy-governance deploy-all test-governance-chaos clean preflight

help: ## Show this help message
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

preflight: ## Run preflight checklist to validate active GCP project, credentials, and billing status
	@chmod +x ./preflight.sh
	@./preflight.sh

bootstrap: preflight ## Setup local python virtual environments and sync workspace dependencies via uv
	@echo "📦 Bootstrapping local monorepo environment with uv..."
	@if ! command -v uv &>/dev/null; then \
		echo "❌ uv is not installed. Please install uv first (e.g., 'curl -LsSf https://astral.sh/uv/install.sh | sh')."; \
		exit 1; \
	fi
	@uv sync --all-packages --all-extras
	@echo "✅ Environment bootstrapped successfully! To activate the environment, run: source .venv/bin/activate"

test-agents: ## Fast execution for agent unit tests only
	@echo "🧪 Running unit tests for ADK Agents..."
	@uv run --package mortgage-agent --extra dev pytest apps/agents/base-adk-agent/tests/
	@uv run --package a2a-mortgage-agent --extra dev pytest apps/agents/a2a-agent/tests/test_agent.py
	@echo "✅ Agent tests passed!"

test-terraform: ## Run syntax validation for all Terraform modules
	@echo "🧪 Validating Terraform syntax across all infrastructure modules..."
	@find infrastructure/modules -maxdepth 2 -name "main.tf" -execdir sh -c 'terraform init -backend=false -input=false >/dev/null 2>&1 && terraform validate' \;
	@echo "✅ Terraform validation passed!"

test-all: test test-terraform ## Run all Python unit tests and Terraform validation

test: ## Run unit tests across all workspace members
	@echo "🧪 Running unit tests for corporate-email..."
	@uv run --package corporate-email --extra dev pytest apps/services/corporate-email/test_main.py
	@echo "🧪 Running unit tests for income-verification-api..."
	@uv run --package income-verification-api --extra dev pytest apps/services/income-verification/test_main.py
	@echo "🧪 Running unit tests for legacy-dms..."
	@uv run --package legacy-dms --extra dev pytest apps/services/legacy-dms/test_server.py
	@$(MAKE) test-agents
	@echo "✅ All unit tests passed!"

run-mcp-local: ## Launch the 3 MCP servers locally on dedicated localhost ports
	@[ -n "$$(lsof -t -i :8001 -i :8002 -i :8003 2>/dev/null)" ] && kill -9 $$(lsof -t -i :8001 -i :8002 -i :8003 2>/dev/null) 2>/dev/null || true
	@echo "🚀 Launching MCP Servers..."
	@uv run --package corporate-email uvicorn main:app --app-dir apps/services/corporate-email --port 8001 & pid_email=$$! ; \
	 echo "📧 corporate-email running on http://localhost:8001" ; \
	 uv run --package income-verification-api uvicorn main:app --app-dir apps/services/income-verification --port 8002 & pid_income=$$! ; \
	 echo "💰 income-verification-api running on http://localhost:8002" ; \
	 uv run --package legacy-dms uvicorn server:app --app-dir apps/services/legacy-dms --port 8003 & pid_dms=$$! ; \
	 echo "🗄️ legacy-dms running on http://localhost:8003" ; \
	 trap 'echo "🧹 Interrupt caught! Tearing down MCP servers..."; kill $$pid_email $$pid_income $$pid_dms 2>/dev/null || true' INT TERM EXIT; \
	 wait

# Default query used for local agent testing
QUERY ?= Can you verify Julian Sterling's income?

test-a2a-local: ## Run local A2A agent test (auto-spins up & tears down local MCP servers via run-mcp-local)
	@already_running=0; \
	if curl -s --connect-timeout 1 http://localhost:8001/health &>/dev/null && curl -s --connect-timeout 1 http://localhost:8002/health &>/dev/null && curl -s --connect-timeout 1 http://localhost:8003/health &>/dev/null; then \
		already_running=1; \
		echo "ℹ️  MCP servers are already running locally. Running tests directly..."; \
	fi; \
	if [ $$already_running -eq 0 ]; then \
		echo "🚀 Launching MCP Servers in background via run-mcp-local..."; \
		make run-mcp-local & make_pid=$$! ; \
		trap 'echo "🧹 Interrupt caught! Tearing down MCP servers..."; kill -TERM -$$make_pid 2>/dev/null || true; pids=$$(ss -tlnp 2>/dev/null | grep -E "8001|8002|8003" | grep -o -E "pid=[0-9]+" | cut -d= -f2 | sort -u); if [ -n "$$pids" ]; then kill -TERM $$pids 2>/dev/null || true; fi; exit 1' INT TERM EXIT; \
		echo "⏳ Waiting for MCP servers to initialize..."; \
		for i in {1..15}; do \
			if curl -s --connect-timeout 1 http://localhost:8001/health &>/dev/null && curl -s --connect-timeout 1 http://localhost:8002/health &>/dev/null && curl -s --connect-timeout 1 http://localhost:8003/health &>/dev/null; then \
				break; \
			fi; \
			sleep 1; \
		done; \
	fi; \
	export EMAIL_MCP_URL="http://localhost:8001/mcp" && \
	export INCOME_VERIFICATION_URL="http://localhost:8002/mcp" && \
	export DMS_MCP_URL="http://localhost:8003/mcp"; \
	echo "🤖 Running A2A Agent test locally..."; \
	uv run --package a2a-mortgage-agent python apps/agents/a2a-agent/scripts/test_local.py "$(QUERY)"; \
	status=$$?; \
	if [ $$already_running -eq 0 ]; then \
		echo "🧹 Tearing down background MCP servers..."; \
		trap - INT TERM EXIT; \
		kill -TERM -$$make_pid 2>/dev/null || true; \
		pids=$$(ss -tlnp 2>/dev/null | grep -E "8001|8002|8003" | grep -o -E "pid=[0-9]+" | cut -d= -f2 | sort -u); \
		if [ -n "$$pids" ]; then \
			kill -TERM $$pids 2>/dev/null || true; \
		fi; \
	fi; \
	disown -a 2>/dev/null || true; \
	exit $$status

test-root-local: ## Run local multi-agent test (Root -> A2A -> MCP) (auto-spins up & tears down MCP servers via run-mcp-local)
	@already_running=0; \
	if curl -s --connect-timeout 1 http://localhost:8001/health &>/dev/null && curl -s --connect-timeout 1 http://localhost:8002/health &>/dev/null && curl -s --connect-timeout 1 http://localhost:8003/health &>/dev/null; then \
		already_running=1; \
		echo "ℹ️  MCP servers are already running locally. Running tests directly..."; \
	fi; \
	if [ $$already_running -eq 0 ]; then \
		echo "🚀 Launching MCP Servers in background via run-mcp-local..."; \
		make run-mcp-local & make_pid=$$! ; \
		trap 'echo "🧹 Interrupt caught! Tearing down MCP servers..."; kill -TERM -$$make_pid 2>/dev/null || true; pids=$$(ss -tlnp 2>/dev/null | grep -E "8001|8002|8003" | grep -o -E "pid=[0-9]+" | cut -d= -f2 | sort -u); if [ -n "$$pids" ]; then kill -TERM $$pids 2>/dev/null || true; fi; exit 1' INT TERM EXIT; \
		echo "⏳ Waiting for MCP servers to initialize..."; \
		for i in {1..15}; do \
			if curl -s --connect-timeout 1 http://localhost:8001/health &>/dev/null && curl -s --connect-timeout 1 http://localhost:8002/health &>/dev/null && curl -s --connect-timeout 1 http://localhost:8003/health &>/dev/null; then \
				break; \
			fi; \
			sleep 1; \
		done; \
	fi; \
	export LOCAL_MODE="true" && \
	export EMAIL_MCP_URL="http://localhost:8001/mcp" && \
	export INCOME_VERIFICATION_URL="http://localhost:8002/mcp" && \
	export DMS_MCP_URL="http://localhost:8003/mcp"; \
	echo "👑 Running Root Agent integration test locally (in-memory mock routing)..."; \
	uv run --package mortgage-agent python apps/agents/base-adk-agent/scripts/test_local.py "$(QUERY)"; \
	status=$$?; \
	if [ $$already_running -eq 0 ]; then \
		echo "🧹 Tearing down background MCP servers..."; \
		trap - INT TERM EXIT; \
		kill -TERM -$$make_pid 2>/dev/null || true; \
		pids=$$(ss -tlnp 2>/dev/null | grep -E "8001|8002|8003" | grep -o -E "pid=[0-9]+" | cut -d= -f2 | sort -u); \
		if [ -n "$$pids" ]; then \
			kill -TERM $$pids 2>/dev/null || true; \
		fi; \
	fi; \
	disown -a 2>/dev/null || true; \
	exit $$status

deploy-projects: ## Deploy Stage 1: Projects via Terragrunt
	@echo "🏗️  Deploying Stage 1: Projects..."
	@cd infrastructure/live/dev/stage-1-projects && terragrunt --non-interactive apply -auto-approve

deploy-networking: ## Deploy Stage 2: Networking via Terragrunt
	@echo "🏗️  Deploying Stage 2: Networking..."
	@cd infrastructure/live/dev/stage-2-networking && terragrunt --non-interactive apply -auto-approve

deploy-security: ## Deploy Stage 3: Security via Terragrunt
	@echo "🏗️  Deploying Stage 3: Security..."
	@cd infrastructure/live/dev/stage-3-security && terragrunt --non-interactive apply -auto-approve

deploy-foundations: deploy-projects deploy-networking deploy-security ## Deploy core foundations (Projects, Networking, Security) collectively via Terragrunt

build-agent-a2a: deploy-repo ## Build and push BYOC A2A Agent container
	@echo "🏗️  Building and pushing A2A Agent container via Cloud Build..."
	@export CICD_PROJ=$$(cd infrastructure/live/dev/stage-1-projects && terragrunt output -raw cicd_project_id 2>/dev/null || gcloud config get-value project); \
	export REGION=$$(awk -F'"' '/region[[:space:]]*=/ {print $$2; exit}' infrastructure/live/dev/env.yaml); \
	export BUILDER_SA=$$(cd infrastructure/live/dev/stage-3-security && terragrunt output -raw cicd_builder_sa_email 2>/dev/null || echo "sa-esmeralda-builder-dev@$$CICD_PROJ.iam.gserviceaccount.com"); \
	gcloud builds submit apps/agents/a2a-agent --project=$$CICD_PROJ --service-account=projects/$$CICD_PROJ/serviceAccounts/$$BUILDER_SA --default-buckets-behavior=REGIONAL_USER_OWNED_BUCKET --tag=$$REGION-docker.pkg.dev/$$CICD_PROJ/esmeralda-containers/a2a-agent:latest

build-agent-root: deploy-repo ## Build and push BYOC Root Agent container
	@echo "🏗️  Building and pushing Root Agent container via Cloud Build..."
	@export CICD_PROJ=$$(cd infrastructure/live/dev/stage-1-projects && terragrunt output -raw cicd_project_id 2>/dev/null || gcloud config get-value project); \
	export REGION=$$(awk -F'"' '/region[[:space:]]*=/ {print $$2; exit}' infrastructure/live/dev/env.yaml); \
	export BUILDER_SA=$$(cd infrastructure/live/dev/stage-3-security && terragrunt output -raw cicd_builder_sa_email 2>/dev/null || echo "sa-esmeralda-builder-dev@$$CICD_PROJ.iam.gserviceaccount.com"); \
	gcloud builds submit apps/agents/base-adk-agent --project=$$CICD_PROJ --service-account=projects/$$CICD_PROJ/serviceAccounts/$$BUILDER_SA --default-buckets-behavior=REGIONAL_USER_OWNED_BUCKET --tag=$$REGION-docker.pkg.dev/$$CICD_PROJ/esmeralda-containers/root-agent:latest

build-agents: test-all deploy-repo ## Build all BYOC agent containers concurrently via make -j2
	@echo "🏗️  Building all BYOC agent containers concurrently..."
	@$(MAKE) -j2 build-agent-a2a build-agent-root
	@echo "✅ All agent containers successfully built and pushed!"

deploy-repo: ## Step 4.1: Deploy Artifact Registry Docker repository in CI/CD project
	@echo "📦 Provisioning Artifact Registry repository in Stage 4..."
	@cd infrastructure/live/dev/stage-4-workloads/services/repository && terragrunt apply -- -auto-approve
	@echo "✅ Artifact Registry repository provisioned successfully!"

deploy-mcp-repo: deploy-repo ## Alias for backwards compatibility

build-service-income-verification: deploy-repo ## Build and push Income Verification API service container
	@echo "🏗️  Building and pushing Income Verification service container..."
	@export CICD_PROJ=$$(cd infrastructure/live/dev/stage-1-projects && terragrunt output -raw cicd_project_id 2>/dev/null || gcloud config get-value project); \
	export REGION=$$(awk -F'"' '/region[[:space:]]*=/ {print $$2; exit}' infrastructure/live/dev/env.yaml); \
	export BUILDER_SA=$$(cd infrastructure/live/dev/stage-3-security && terragrunt output -raw cicd_builder_sa_email 2>/dev/null || echo "sa-esmeralda-builder-dev@$$CICD_PROJ.iam.gserviceaccount.com"); \
	gcloud builds submit apps/services/income-verification --project=$$CICD_PROJ --service-account=projects/$$CICD_PROJ/serviceAccounts/$$BUILDER_SA --default-buckets-behavior=REGIONAL_USER_OWNED_BUCKET --tag=$$REGION-docker.pkg.dev/$$CICD_PROJ/esmeralda-containers/income-verification-api:latest

build-service-corporate-email: deploy-repo ## Build and push Corporate Email service container
	@echo "🏗️  Building and pushing Corporate Email service container..."
	@export CICD_PROJ=$$(cd infrastructure/live/dev/stage-1-projects && terragrunt output -raw cicd_project_id 2>/dev/null || gcloud config get-value project); \
	export REGION=$$(awk -F'"' '/region[[:space:]]*=/ {print $$2; exit}' infrastructure/live/dev/env.yaml); \
	export BUILDER_SA=$$(cd infrastructure/live/dev/stage-3-security && terragrunt output -raw cicd_builder_sa_email 2>/dev/null || echo "sa-esmeralda-builder-dev@$$CICD_PROJ.iam.gserviceaccount.com"); \
	gcloud builds submit apps/services/corporate-email --project=$$CICD_PROJ --service-account=projects/$$CICD_PROJ/serviceAccounts/$$BUILDER_SA --default-buckets-behavior=REGIONAL_USER_OWNED_BUCKET --tag=$$REGION-docker.pkg.dev/$$CICD_PROJ/esmeralda-containers/corporate-email:latest

build-service-legacy-dms: deploy-repo ## Build and push Legacy DMS service container
	@echo "🏗️  Building and pushing Legacy DMS service container..."
	@export CICD_PROJ=$$(cd infrastructure/live/dev/stage-1-projects && terragrunt output -raw cicd_project_id 2>/dev/null || gcloud config get-value project); \
	export REGION=$$(awk -F'"' '/region[[:space:]]*=/ {print $$2; exit}' infrastructure/live/dev/env.yaml); \
	export BUILDER_SA=$$(cd infrastructure/live/dev/stage-3-security && terragrunt output -raw cicd_builder_sa_email 2>/dev/null || echo "sa-esmeralda-builder-dev@$$CICD_PROJ.iam.gserviceaccount.com"); \
	gcloud builds submit apps/services/legacy-dms --project=$$CICD_PROJ --service-account=projects/$$CICD_PROJ/serviceAccounts/$$BUILDER_SA --default-buckets-behavior=REGIONAL_USER_OWNED_BUCKET --tag=$$REGION-docker.pkg.dev/$$CICD_PROJ/esmeralda-containers/legacy-dms:latest

build-service-kong: deploy-repo ## Build and push custom Kong Gateway container
	@echo "🏗️  Building and pushing Kong Gateway service container..."
	@export CICD_PROJ=$$(cd infrastructure/live/dev/stage-1-projects && terragrunt output -raw cicd_project_id 2>/dev/null || gcloud config get-value project); \
	export REGION=$$(awk -F'"' '/region[[:space:]]*=/ {print $$2; exit}' infrastructure/live/dev/env.yaml); \
	export BUILDER_SA=$$(cd infrastructure/live/dev/stage-3-security && terragrunt output -raw cicd_builder_sa_email 2>/dev/null || echo "sa-esmeralda-builder-dev@$$CICD_PROJ.iam.gserviceaccount.com"); \
	gcloud builds submit apps/services/kong --project=$$CICD_PROJ --service-account=projects/$$CICD_PROJ/serviceAccounts/$$BUILDER_SA --default-buckets-behavior=REGIONAL_USER_OWNED_BUCKET --tag=$$REGION-docker.pkg.dev/$$CICD_PROJ/esmeralda-containers/kong-gateway:latest

build-services: deploy-repo ## Build all Cloud Run service containers concurrently via make -j5
	@echo "🏗️  Building all Cloud Run service containers concurrently..."
	@$(MAKE) -j5 build-service-income-verification build-service-corporate-email build-service-legacy-dms build-service-kong build-service-circuit-breaker
	@echo "✅ All service containers successfully built and pushed!"



build-mcp-servers: build-services ## Alias for backwards compatibility

deploy-services: ## Step 4.2: Deploy Cloud Run services (corporate-email, income-verification, legacy-dms, kong)
	@echo "🚀 Deploying Cloud Run Services..."
	@cd infrastructure/live/dev/stage-4-workloads/services && terragrunt --non-interactive run --all apply
	@echo "✅ Cloud Run Services deployed!"

deploy-mcps: deploy-services ## Alias for backwards compatibility

deploy-gateway: ## Step 4.3: Deploy Kong API Gateway individually
	@echo "🚀 Deploying Kong API Gateway..."
	@cd infrastructure/live/dev/stage-4-workloads/services/kong && terragrunt --non-interactive apply -auto-approve
	@echo "✅ Gateway deployed!"

deploy-agent-a2a: ## Step 4.4: Deploy A2A Mortgage Specialist Reasoning Engine
	@echo "🚀 Deploying A2A Reasoning Engine Agent..."
	@cd infrastructure/live/dev/stage-4-workloads/agents/a2a-agent && terragrunt --non-interactive apply -auto-approve
	@echo "✅ A2A Agent deployed!"

deploy-agent-root: ## Step 4.5: Deploy LOB Root Coordinator Reasoning Engine
	@echo "🚀 Deploying Root Coordinator Reasoning Engine Agent..."
	@cd infrastructure/live/dev/stage-4-workloads/agents/base-adk-agent && terragrunt --non-interactive apply -auto-approve
	@echo "✅ Root Coordinator deployed!"

deploy-workloads-step-by-step: ## Deploy all Stage 4 workloads using native Terragrunt dependency DAG graph
	@echo "🚀 Deploying Stage 4 workloads with native Terragrunt DAG..."
	@cd infrastructure/live/dev/stage-4-workloads && terragrunt --non-interactive run --all apply
	@echo "✨ All Stage 4 workloads deployed successfully!"

deploy-workloads: build-agents build-services deploy-workloads-step-by-step ## Full automated build and deploy of all Stage 4 workloads

build-service-circuit-breaker: deploy-repo ## Build and push Circuit Breaker service container
	@echo "🏗️  Building and pushing Circuit Breaker service container..."
	@export CICD_PROJ=$$(cd infrastructure/live/dev/stage-1-projects && terragrunt output -raw cicd_project_id 2>/dev/null || gcloud config get-value project); \
	export REGION=$$(awk -F'"' '/region[[:space:]]*=/ {print $$2; exit}' infrastructure/live/dev/env.yaml); \
	export BUILDER_SA=$$(cd infrastructure/live/dev/stage-3-security && terragrunt output -raw cicd_builder_sa_email 2>/dev/null || echo "sa-esmeralda-builder-dev@$$CICD_PROJ.iam.gserviceaccount.com"); \
	gcloud builds submit apps/services/circuit-breaker --project=$$CICD_PROJ --service-account=projects/$$CICD_PROJ/serviceAccounts/$$BUILDER_SA --default-buckets-behavior=REGIONAL_USER_OWNED_BUCKET --tag=$$REGION-docker.pkg.dev/$$CICD_PROJ/esmeralda-containers/circuit-breaker:latest

deploy-governance: ## Deploy Stage 5: Governance, Observability & Alerts via Terragrunt
	@echo "🏛️  Deploying Stage 5: Governance & Observability Stack..."
	@cd infrastructure/live/dev/stage-5-governance && terragrunt --non-interactive apply -auto-approve
	@echo "✨ Stage 5 Governance Stack deployed successfully!"

deploy-all: deploy-foundations deploy-workloads deploy-governance ## Full automated deploy of all 5 stages of the Esmeralda platform

test-governance-chaos: ## Run local chaos simulation test for governance telemetry and alerts
	@echo "🧪 Running Esmeralda Governance Pipeline Chaos Test..."
	@uv run python apps/agents/base-adk-agent/scripts/chaos_telemetry_test.py

clean: ## Clean python virtual environments, caches, and terragrunt cache files recursively
	@echo "🧹 Cleaning up local caches and environments..."
	@rm -rf .venv .uv .pytest_cache
	@find . -type d -name "__pycache__" -exec rm -rf {} +
	@find . -type d -name ".terraform" -exec rm -rf {} +
	@find . -type d -name ".terragrunt-cache" -exec rm -rf {} +
	@find . -type f -name "*.tfstate*" -exec rm -f {} +
	@echo "✨ Clean complete!"
