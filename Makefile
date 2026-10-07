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

ENV ?= dev
LIVE_DIR = infrastructure/live/$(ENV)
CICD_DIR = infrastructure/live/shared/stage-0-cicd

# Image builds (env-neutral, shared dev repository)
BUILD_TAG ?= dev-latest
GIT_SHA := $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)

# Promotion (make promote TAG=vX.Y.Z [SOURCE_TAG=...])
TAG ?=
SOURCE_TAG ?= dev-latest

# Terraform / Terragrunt from the standard per-user install locations, if present
export PATH := $(HOME)/.terraform/bin:$(HOME)/.terragrunt/bin:$(PATH)

.PHONY: help bootstrap test test-all test-agents test-terraform run-mcp-local test-a2a-local test-root-local \
	test-a2a-remote test-root-remote test-e2e deploy-cicd deploy-projects deploy-networking deploy-security \
	deploy-foundations deploy-governance deploy-governance-views build-agent-a2a build-agent-root build-agents \
	build-service-income-verification build-service-corporate-email build-service-legacy-dms build-service-kong \
	build-service-circuit-breaker build-services build-images deploy-workloads deploy-services deploy-agent-a2a \
	deploy-agent-root deploy-agents deploy-gateway deploy-iap-egress deploy-all destroy-all status-release \
	promote-patch promote-minor promote test-governance-chaos load-test-root-agent clean preflight

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
	@uv run --package a2a-mortgage-agent --extra dev pytest apps/agents/a2a-agent/tests/
	@echo "✅ Agent tests passed!"

test-terraform: ## Run syntax validation for all Terraform modules
	@echo "🧪 Validating Terraform syntax across all infrastructure modules..."
	@set -e; for d in $$(find infrastructure/modules -name main.tf -not -path '*/.terraform/*' -exec dirname {} \; | sort); do \
		echo "→ $$d"; \
		(cd $$d && terraform init -upgrade -backend=false -input=false >/dev/null && terraform validate -no-color); \
	done
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

# ==============================================================================
# Remote integration tests (resolve project + engine IDs from Terragrunt outputs)
# ==============================================================================

test-root-remote: ## Run remote Root Coordinator integration test against Vertex AI Reasoning Engine
	@echo "👑 Running Root Agent remote integration test on Vertex AI ($(ENV))..."
	@ROOT_PROJ=$$(cd $(LIVE_DIR)/stage-1-projects && terragrunt output -raw root_project_id) && \
	ROOT_ID=$$(cd $(LIVE_DIR)/stage-5-workloads/agents/base-adk-agent && terragrunt output -raw engine_id | awk -F'/' '{print $$NF}') && \
	REGION=$$(awk -F'"' '/^  region[[:space:]]*=/ {print $$2; exit}' $(LIVE_DIR)/env.yaml) && \
	ROOT_AGENT_PROJECT_ID="$$ROOT_PROJ" ROOT_REASONING_ENGINE_ID="$$ROOT_ID" GOOGLE_CLOUD_LOCATION="$$REGION" \
	uv run --package mortgage-agent python apps/agents/base-adk-agent/scripts/test_remote.py "$(QUERY)"

test-a2a-remote: ## Run remote A2A Specialist integration test against Vertex AI Reasoning Engine
	@echo "🤖 Running A2A Agent remote integration test on Vertex AI ($(ENV))..."
	@A2A_PROJ=$$(cd $(LIVE_DIR)/stage-1-projects && terragrunt output -raw a2a_project_id) && \
	A2A_ID=$$(cd $(LIVE_DIR)/stage-5-workloads/agents/a2a-agent && terragrunt output -raw engine_id | awk -F'/' '{print $$NF}') && \
	REGION=$$(awk -F'"' '/^  region[[:space:]]*=/ {print $$2; exit}' $(LIVE_DIR)/env.yaml) && \
	GOOGLE_CLOUD_PROJECT="$$A2A_PROJ" REASONING_ENGINE_ID="$$A2A_ID" GOOGLE_CLOUD_LOCATION="$$REGION" \
	uv run --package a2a-mortgage-agent python apps/agents/a2a-agent/scripts/test_remote.py "$(QUERY)"

test-e2e: ## End-to-end check of a deployed env: A2A agent, then Root -> A2A (fails on any error)
	@$(MAKE) --no-print-directory test-a2a-remote ENV=$(ENV)
	@$(MAKE) --no-print-directory test-root-remote ENV=$(ENV)
	@echo "✅ End-to-end tests passed for $(ENV)!"

# ==============================================================================
# Layer 0: shared CI/CD (env-independent; one project, dev + release repositories)
# ==============================================================================

deploy-cicd: ## Deploy Layer 0: shared CI/CD project, dev + release Artifact Registry repos, builder/promoter SAs
	@echo "🏗️  Deploying Layer 0: shared CI/CD..."
	@cd $(CICD_DIR) && terragrunt --non-interactive apply -auto-approve

# ==============================================================================
# Layers 1-3: foundations (projects, networking, security + internal PKI)
# ==============================================================================

deploy-projects: ## Deploy Layer 1: Projects via Terragrunt for $(ENV)
	@echo "🏗️  Deploying Layer 1: Projects for $(ENV)..."
	@cd $(LIVE_DIR)/stage-1-projects && terragrunt --non-interactive apply -auto-approve

deploy-networking: ## Deploy Layer 2: Networking via Terragrunt for $(ENV)
	@echo "🏗️  Deploying Layer 2: Networking for $(ENV)..."
	@cd $(LIVE_DIR)/stage-2-networking && terragrunt --non-interactive apply -auto-approve

deploy-security: ## Deploy Layer 3: Security + internal Root CA via Terragrunt for $(ENV)
	@echo "🏗️  Deploying Layer 3: Security for $(ENV)..."
	@cd $(LIVE_DIR)/stage-3-security && terragrunt --non-interactive apply -auto-approve

deploy-foundations: deploy-projects deploy-networking deploy-security ## Deploy Layers 1-3 (Projects, Networking, Security)

# ==============================================================================
# Layer 4: governance (Agent Gateway + ACT + IAP, Agent Registry, Model Armor, telemetry)
# ==============================================================================

deploy-governance: ## Deploy Layer 4: Governance, Agent Gateway, Observability & Alerts for $(ENV)
	@echo "🏛️  Deploying Layer 4: Governance for $(ENV)..."
	@cd $(LIVE_DIR)/stage-4-governance && terragrunt --non-interactive apply -auto-approve

deploy-governance-views: ## Deploy Layer 4 BigQuery FinOps & Telemetry SQL Views (after agent traffic generated logs)
	@echo "📊 Deploying BigQuery FinOps & Telemetry SQL Views for $(ENV)..."
	@cd $(LIVE_DIR)/stage-4-governance && ENABLE_ANALYTICS_VIEWS=true terragrunt --non-interactive apply -auto-approve
	@echo "👉 Set enable_analytics_views = true in $(LIVE_DIR)/env.yaml to keep the views on plain re-applies."

# ==============================================================================
# Image builds: env-neutral, always pushed to the shared dev repository
# (:$(BUILD_TAG) and :dev-<gitsha>). Promotion to prd is `make promote TAG=vX.Y.Z`.
# ==============================================================================

# $(1) = build context, $(2) = image name
define build_image
	@echo "🏗️  Building $(2) (:$(BUILD_TAG), :dev-$(GIT_SHA)) in the shared CI/CD project..."
	@CICD_JSON=$$(cd $(CICD_DIR) && terragrunt output -json) && \
	CICD_PROJ=$$(echo "$$CICD_JSON" | jq -r .cicd_project_id.value) && \
	REPO_URL=$$(echo "$$CICD_JSON" | jq -r .dev_repository_url.value) && \
	BUILDER_SA=$$(echo "$$CICD_JSON" | jq -r .builder_sa_email.value) && \
	gcloud builds submit $(1) --config=.cloudbuild/build-image.yaml --project=$$CICD_PROJ \
		--service-account=projects/$$CICD_PROJ/serviceAccounts/$$BUILDER_SA \
		--default-buckets-behavior=REGIONAL_USER_OWNED_BUCKET \
		--substitutions=_IMAGE=$(2),_REPOSITORY_URL=$$REPO_URL,_TAG=$(BUILD_TAG),_SHA_TAG=dev-$(GIT_SHA)
endef

build-agent-a2a: ## Build and push the A2A Agent image
	$(call build_image,apps/agents/a2a-agent,a2a-agent)

build-agent-root: ## Build and push the Root Agent image
	$(call build_image,apps/agents/base-adk-agent,root-agent)

build-agents: test-all ## Run tests, then build both agent images concurrently
	@$(MAKE) -j2 build-agent-a2a build-agent-root
	@echo "✅ All agent images built and pushed!"

build-service-income-verification: ## Build and push the Income Verification MCP image
	$(call build_image,apps/services/income-verification,income-verification-api)

build-service-corporate-email: ## Build and push the Corporate Email MCP image
	$(call build_image,apps/services/corporate-email,corporate-email)

build-service-legacy-dms: ## Build and push the Legacy DMS MCP image
	$(call build_image,apps/services/legacy-dms,legacy-dms)

build-service-kong: ## Build and push the custom Kong Gateway image
	$(call build_image,apps/services/kong,kong-gateway)

build-service-circuit-breaker: ## Build and push the Circuit Breaker image
	$(call build_image,apps/services/circuit-breaker,circuit-breaker)

build-services: ## Build all MCP service + Kong images concurrently
	@$(MAKE) -j4 build-service-income-verification build-service-corporate-email build-service-legacy-dms build-service-kong
	@echo "✅ All service images built and pushed!"

build-images: build-services build-agents ## Build every image deployed by Layer 5

# ==============================================================================
# Layer 5: workloads (MCP services -> agents -> Kong -> IAP egress), Terragrunt DAG
# ==============================================================================

deploy-workloads: ## Deploy Layer 5 in dependency order (MCP services, agents, Kong, IAP egress, test VM)
	@echo "🚀 Deploying Layer 5 workloads for $(ENV)..."
	@cd $(LIVE_DIR)/stage-5-workloads && terragrunt --non-interactive run --all apply
	@echo "✨ Layer 5 workloads deployed!"

deploy-services: ## Deploy the 3 MCP services on Cloud Run (+ their Agent Registry entries)
	@for s in corporate-email income-verification legacy-dms; do \
		echo "🚀 Deploying $$s..."; \
		(cd $(LIVE_DIR)/stage-5-workloads/services/$$s && terragrunt --non-interactive apply -auto-approve) || exit 1; \
	done

deploy-agent-a2a: ## Deploy the A2A Mortgage Specialist Reasoning Engine
	@echo "🚀 Deploying A2A Reasoning Engine Agent..."
	@cd $(LIVE_DIR)/stage-5-workloads/agents/a2a-agent && terragrunt --non-interactive apply -auto-approve

deploy-agent-root: ## Deploy the LOB Root Coordinator Reasoning Engine
	@echo "🚀 Deploying Root Coordinator Reasoning Engine Agent..."
	@cd $(LIVE_DIR)/stage-5-workloads/agents/base-adk-agent && terragrunt --non-interactive apply -auto-approve

deploy-agents: deploy-agent-a2a deploy-agent-root ## Deploy both Reasoning Engine agents

deploy-gateway: ## Deploy Kong API Gateway (re-run after agents are recreated: routes use engine IDs)
	@echo "🚀 Deploying Kong API Gateway..."
	@cd $(LIVE_DIR)/stage-5-workloads/services/kong && terragrunt --non-interactive apply -auto-approve

deploy-iap-egress: ## Grant roles/iap.egressor on every Agent Registry entry (final Layer 5 step)
	@cd $(LIVE_DIR)/stage-5-workloads/services/iap-egress && terragrunt --non-interactive apply -auto-approve

# ==============================================================================
# Whole environment
# ==============================================================================

deploy-all: ## Build an env from zero: Layer 0 -> 1-3 -> 4 -> images -> 5 (single pass)
	@$(MAKE) --no-print-directory deploy-cicd
	@$(MAKE) --no-print-directory deploy-foundations ENV=$(ENV)
	@$(MAKE) --no-print-directory deploy-governance ENV=$(ENV)
	@$(MAKE) --no-print-directory build-images
	@$(MAKE) --no-print-directory deploy-workloads ENV=$(ENV)
	@echo "🎉 $(ENV) deployed. Verify with: make test-e2e ENV=$(ENV)"

destroy-all: ## Destroy an env's Layers 5 -> 1 (dev only; never touches the shared Layer 0)
	@if [ "$(ENV)" = "prd" ]; then echo "❌ destroy-all refuses ENV=prd."; exit 1; fi
	@read -p "⚠️  Destroy ALL of $(ENV) (layers 5 -> 1)? Type the env name to confirm: " c && [ "$$c" = "$(ENV)" ]
	@cd $(LIVE_DIR)/stage-5-workloads && terragrunt --non-interactive run --all destroy
	@cd $(LIVE_DIR)/stage-4-governance && terragrunt --non-interactive destroy -auto-approve
	@cd $(LIVE_DIR)/stage-3-security && terragrunt --non-interactive destroy -auto-approve
	@cd $(LIVE_DIR)/stage-2-networking && terragrunt --non-interactive destroy -auto-approve
	@cd $(LIVE_DIR)/stage-1-projects && terragrunt --non-interactive destroy -auto-approve
	@echo "🧹 $(ENV) destroyed. The shared Layer 0 CI/CD was not touched."

# ==============================================================================
# Releases: copy dev images by digest into the immutable release repository
# ==============================================================================

status-release: ## Show the prd release tag and the shared repositories
	@bash scripts/promote_release.sh status

promote-patch: ## Promote dev-latest as v1.0.1 (copy by digest, update prd/env.yaml, no deploy)
	@bash scripts/promote_release.sh promote --tag v1.0.1

promote-minor: ## Promote dev-latest as v1.1.0 (copy by digest, update prd/env.yaml, no deploy)
	@bash scripts/promote_release.sh promote --tag v1.1.0

promote: ## Promote dev images as TAG (e.g. make promote TAG=v1.2.0 [SOURCE_TAG=dev-<sha>]); no deploy
	@if [ -z "$(TAG)" ]; then echo "❌ Usage: make promote TAG=vX.Y.Z [SOURCE_TAG=dev-latest]"; exit 1; fi
	@bash scripts/promote_release.sh promote --tag $(TAG) --source-tag $(SOURCE_TAG)

test-governance-chaos: ## Run local chaos simulation test for governance telemetry and alerts
	@echo "🧪 Running Esmeralda Governance Pipeline Chaos Test..."
	@uv run python apps/agents/base-adk-agent/scripts/chaos_telemetry_test.py

load-test-root-agent: ## Run Locust load test against the Root Agent on Vertex AI Reasoning Engines
	@echo "⚡ Running Locust load test for Root Agent on Vertex AI..."
	@uv run locust -f apps/agents/base-adk-agent/scripts/locustfile.py --headless -u 5 -r 1 --run-time 1m --host https://us-central1-aiplatform.googleapis.com

clean: ## Clean python virtual environments, caches, and terragrunt cache files recursively
	@echo "🧹 Cleaning up local caches and environments..."
	@rm -rf .venv .uv .pytest_cache
	@find . -type d -name "__pycache__" -exec rm -rf {} +
	@find . -type d -name ".terraform" -exec rm -rf {} +
	@find . -type d -name ".terragrunt-cache" -exec rm -rf {} +
	@find . -type f -name "*.tfstate*" -exec rm -f {} +
	@echo "✨ Clean complete!"
