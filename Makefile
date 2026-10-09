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
CICD_DIR = infrastructure/live/shared/layer-0-cicd

# Image builds (env-neutral, shared dev repository)
BUILD_TAG ?= dev-latest
GIT_SHA := $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)

# Promotion (make promote TAG=vX.Y.Z [SOURCE_TAG=...])
TAG ?=
SOURCE_TAG ?= dev-latest

# uv: always install exactly what uv.lock says and never re-resolve locally. The lock is generated
# against public PyPI by `make lock` (scripts/uv_lock.sh, runs in Cloud Build); re-locking on a machine
# with a different package index (e.g. a corporate proxy) would rewrite every URL in uv.lock.
export UV_FROZEN := 1

# Terraform / Terragrunt from the standard per-user install locations, if present
export PATH := $(HOME)/.terraform/bin:$(HOME)/.terragrunt/bin:$(PATH)

.PHONY: help bootstrap lock lock-check test test-all test-agents test-terraform run-mcp-local test-ai-coe-mortgage-specialist-local test-cx-mortgage-orchestrator-local \
	test-ai-coe-mortgage-specialist-remote test-cx-mortgage-orchestrator-remote test-e2e deploy-cicd deploy-projects deploy-networking deploy-security \
	query deploy-foundations deploy-governance deploy-governance-views build-ai-coe-mortgage-specialist build-cx-mortgage-orchestrator build-agents \
	build-service-income-verification build-service-corporate-email build-service-legacy-dms build-service-kong \
	build-services build-images deploy-workloads verify-images deploy-services deploy-ai-coe-mortgage-specialist \
	deploy-cx-mortgage-orchestrator deploy-agents deploy-gateway deploy-iap-egress deploy-all destroy-all status-release \
	promote-patch promote-minor promote test-governance-chaos load-test-cx-mortgage-orchestrator docs-serve docs-build clean preflight

help: ## Show this help message
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

preflight: ## Run preflight checklist to validate active GCP project, credentials, and billing status
	@chmod +x ./scripts/preflight.sh
	@./scripts/preflight.sh

bootstrap: preflight ## Setup local python virtual environments and sync workspace dependencies via uv
	@echo "📦 Bootstrapping local monorepo environment with uv..."
	@if ! command -v uv &>/dev/null; then \
		echo "❌ uv is not installed. Please install uv first (e.g., 'curl -LsSf https://astral.sh/uv/install.sh | sh')."; \
		exit 1; \
	fi
	@uv sync --all-packages --all-extras
	@git config core.hooksPath .githooks && echo "🪝 Git hooks installed (.githooks: pre-push checks uv.lock)"
	@echo "✅ Environment bootstrapped successfully! To activate the environment, run: source .venv/bin/activate"

test-agents: ## Fast execution for agent unit tests only
	@echo "🧪 Running unit tests for ADK Agents..."
	@uv run --package cx-mortgage-orchestrator --extra dev pytest apps/agents/cx-mortgage-orchestrator/tests/
	@uv run --package ai-coe-mortgage-specialist --extra dev pytest apps/agents/ai-coe-mortgage-specialist/tests/
	@echo "🧪 Running unit tests for the esmeralda library..."
	@uv run --package esmeralda --extra dev pytest packages/esmeralda/tests/
	@echo "✅ Agent tests passed!"

test-terraform: ## Run syntax validation for all Terraform modules (+ workload image digest pinning check)
	@bash scripts/check_image_pinning.sh
	@echo "🧪 Validating Terraform syntax across all infrastructure modules..."
	@set -e; for d in $$(find infrastructure/modules -name main.tf -not -path '*/.terraform/*' -exec dirname {} \; | sort); do \
		echo "→ $$d"; \
		(cd $$d && terraform init -upgrade -backend=false -input=false >/dev/null && terraform validate -no-color); \
	done
	@echo "✅ Terraform validation passed!"

test-all: lock-check test test-terraform ## Run the uv.lock check, all Python unit tests and Terraform validation

lock: ## Regenerate uv.lock against public PyPI (in Cloud Build) from your working copy; PKG="a b==1.2" also upgrades those
	@bash scripts/uv_lock.sh $(PKG)

lock-check: ## Fail if uv.lock is out of date with the pyproject.toml files (offline; also the git pre-push hook)
	@bash scripts/check_lock.sh

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

# ==============================================================================
# Query any agent (ADK or A2A) the same way: in-process, on a running server, or deployed
# ==============================================================================

AGENT ?=
TARGET ?= local

query: ## Query an agent: AGENT=<dir under apps/agents> [QUERY="..."] [TARGET=local|remote|<url>] (remote = the ENV deployment)
	@[ -n "$(AGENT)" ] || { echo "❌ Set AGENT=<agent directory under apps/agents>, e.g. AGENT=cx-mortgage-orchestrator"; exit 1; }
	@case "$(TARGET)" in \
	  local) scripts/with_local_mcp.sh uv run --package $(AGENT) esmeralda query --agent-dir apps/agents/$(AGENT) "$(QUERY)" ;; \
	  remote) ENGINE=$$(cd $(LIVE_DIR)/layer-5-workloads/agents/$(AGENT) && terragrunt output -raw engine_id 2>/dev/null) && [ -n "$$ENGINE" ] || { echo "❌ No engine_id output for $(AGENT) in $(ENV)"; exit 1; }; \
	    uv run --package $(AGENT) esmeralda query --agent-dir apps/agents/$(AGENT) --engine "$$ENGINE" "$(QUERY)" ;; \
	  http://*|https://*) uv run --package $(AGENT) esmeralda query --agent-dir apps/agents/$(AGENT) --url "$(TARGET)" "$(QUERY)" ;; \
	  *) echo "❌ TARGET must be local, remote or a URL (got '$(TARGET)')"; exit 1 ;; \
	esac

test-ai-coe-mortgage-specialist-local: ## Run local AI CoE mortgage specialist (A2A) test (auto-spins up & tears down local MCP servers via run-mcp-local)
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
	uv run --package ai-coe-mortgage-specialist python apps/agents/ai-coe-mortgage-specialist/scripts/test_local.py "$(QUERY)"; \
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

test-cx-mortgage-orchestrator-local: ## Run local multi-agent test (Root -> A2A -> MCP) (auto-spins up & tears down MCP servers via run-mcp-local)
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
	echo "👑 Running cx-mortgage-orchestrator integration test locally (in-memory mock routing)..."; \
	uv run --package cx-mortgage-orchestrator python apps/agents/cx-mortgage-orchestrator/scripts/test_local.py "$(QUERY)"; \
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

test-cx-mortgage-orchestrator-remote: ## Run remote CX mortgage orchestrator integration test against Vertex AI Reasoning Engine
	@echo "👑 Running cx-mortgage-orchestrator remote integration test on Vertex AI ($(ENV))..."
	@ROOT_PROJ=$$(cd $(LIVE_DIR)/layer-1-projects && terragrunt output -raw cx_agents_project_id) && \
	ROOT_ID=$$(cd $(LIVE_DIR)/layer-5-workloads/agents/cx-mortgage-orchestrator && terragrunt output -raw engine_id | awk -F'/' '{print $$NF}') && \
	REGION=$$(awk -F'"' '/^  region[[:space:]]*=/ {print $$2; exit}' $(LIVE_DIR)/env.yaml) && \
	CX_AGENTS_PROJECT_ID="$$ROOT_PROJ" ROOT_REASONING_ENGINE_ID="$$ROOT_ID" GOOGLE_CLOUD_LOCATION="$$REGION" \
	uv run --package cx-mortgage-orchestrator python apps/agents/cx-mortgage-orchestrator/scripts/test_remote.py "$(QUERY)"

test-ai-coe-mortgage-specialist-remote: ## Run remote AI CoE mortgage specialist (A2A) integration test against Vertex AI Reasoning Engine
	@echo "🤖 Running A2A Agent remote integration test on Vertex AI ($(ENV))..."
	@A2A_PROJ=$$(cd $(LIVE_DIR)/layer-1-projects && terragrunt output -raw ai_coe_agents_project_id) && \
	A2A_ID=$$(cd $(LIVE_DIR)/layer-5-workloads/agents/ai-coe-mortgage-specialist && terragrunt output -raw engine_id | awk -F'/' '{print $$NF}') && \
	REGION=$$(awk -F'"' '/^  region[[:space:]]*=/ {print $$2; exit}' $(LIVE_DIR)/env.yaml) && \
	GOOGLE_CLOUD_PROJECT="$$A2A_PROJ" REASONING_ENGINE_ID="$$A2A_ID" GOOGLE_CLOUD_LOCATION="$$REGION" \
	uv run --package ai-coe-mortgage-specialist python apps/agents/ai-coe-mortgage-specialist/scripts/test_remote.py "$(QUERY)"

test-e2e: ## End-to-end check of a deployed env: specialist, then orchestrator -> specialist (fails on any error)
	@$(MAKE) --no-print-directory query AGENT=ai-coe-mortgage-specialist TARGET=remote ENV=$(ENV)
	@$(MAKE) --no-print-directory query AGENT=cx-mortgage-orchestrator TARGET=remote ENV=$(ENV)
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
	@cd $(LIVE_DIR)/layer-1-projects && terragrunt --non-interactive apply -auto-approve

deploy-networking: ## Deploy Layer 2: Networking via Terragrunt for $(ENV)
	@echo "🏗️  Deploying Layer 2: Networking for $(ENV)..."
	@cd $(LIVE_DIR)/layer-2-networking && terragrunt --non-interactive apply -auto-approve

deploy-security: ## Deploy Layer 3: Security + internal Root CA via Terragrunt for $(ENV)
	@echo "🏗️  Deploying Layer 3: Security for $(ENV)..."
	@cd $(LIVE_DIR)/layer-3-security && terragrunt --non-interactive apply -auto-approve

deploy-foundations: deploy-projects deploy-networking deploy-security ## Deploy Layers 1-3 (Projects, Networking, Security)

# ==============================================================================
# Layer 4: governance (Agent Gateway + ACT + IAP, Agent Registry, Model Armor, telemetry)
# ==============================================================================

deploy-governance: ## Deploy Layer 4: Governance, Agent Gateway, Observability & Alerts for $(ENV)
	@echo "🏛️  Deploying Layer 4: Governance for $(ENV)..."
	@cd $(LIVE_DIR)/layer-4-governance && terragrunt --non-interactive apply -auto-approve

deploy-governance-views: ## Deploy Layer 4 BigQuery FinOps & Telemetry SQL Views (after agent traffic generated logs)
	@echo "📊 Deploying BigQuery FinOps & Telemetry SQL Views for $(ENV)..."
	@cd $(LIVE_DIR)/layer-4-governance && ENABLE_ANALYTICS_VIEWS=true terragrunt --non-interactive apply -auto-approve
	@echo "👉 Set enable_analytics_views = true in $(LIVE_DIR)/env.yaml to keep the views on plain re-applies."

# ==============================================================================
# Image builds: env-neutral, always pushed to the shared dev repository
# (:$(BUILD_TAG) and :dev-<gitsha>). Promotion to prd is `make promote TAG=vX.Y.Z`.
# ==============================================================================

# $(1) = build context, $(2) = image name, $(3) = uv workspace package (optional),
# $(4) = workspace library to ship as a wheel (optional).
# When $(3) is set, the package's exact, hashed dependency set is exported from the committed
# workspace uv.lock into $(1)/requirements.lock (generated, gitignored) so the image installs
# exactly what was locked and audited, instead of re-resolving at build time. Workspace members
# are not in that export; $(4) is built into $(1)/dist/ (gitignored) for the image to install.
define build_image
	@echo "🏗️  Building $(2) (:$(BUILD_TAG), :dev-$(GIT_SHA)) in the shared CI/CD project..."
	$(if $(3),@uv export --frozen --package $(3) --no-dev --no-emit-workspace --quiet -o $(1)/requirements.lock)
	$(if $(4),@rm -rf $(1)/dist && uv build --package $(4) --wheel --out-dir $(1)/dist --quiet)
	@CICD_JSON=$$(cd $(CICD_DIR) && terragrunt output -json) && \
	CICD_PROJ=$$(echo "$$CICD_JSON" | jq -r .cicd_project_id.value) && \
	REPO_URL=$$(echo "$$CICD_JSON" | jq -r .dev_repository_url.value) && \
	BUILDER_SA=$$(echo "$$CICD_JSON" | jq -r .builder_sa_email.value) && \
	SOURCE_BUCKET=$$(echo "$$CICD_JSON" | jq -r .build_source_bucket.value) && \
	gcloud builds submit $(1) --config=.cloudbuild/build-image.yaml --project=$$CICD_PROJ \
		--service-account=projects/$$CICD_PROJ/serviceAccounts/$$BUILDER_SA \
		--gcs-source-staging-dir=gs://$$SOURCE_BUCKET/source \
		--substitutions=_IMAGE=$(2),_REPOSITORY_URL=$$REPO_URL,_TAG=$(BUILD_TAG),_SHA_TAG=dev-$(GIT_SHA)
endef

build-ai-coe-mortgage-specialist: ## Build and push the AI CoE mortgage specialist (A2A) image
	$(call build_image,apps/agents/ai-coe-mortgage-specialist,ai-coe-mortgage-specialist,ai-coe-mortgage-specialist,esmeralda)

build-cx-mortgage-orchestrator: ## Build and push the CX mortgage orchestrator image
	$(call build_image,apps/agents/cx-mortgage-orchestrator,cx-mortgage-orchestrator,cx-mortgage-orchestrator,esmeralda)

build-agents: test-all ## Run tests, then build both agent images concurrently
	@$(MAKE) -j2 build-ai-coe-mortgage-specialist build-cx-mortgage-orchestrator
	@echo "✅ All agent images built and pushed!"

build-service-income-verification: ## Build and push the Income Verification MCP image
	$(call build_image,apps/services/income-verification,income-verification-api,income-verification-api)

build-service-corporate-email: ## Build and push the Corporate Email MCP image
	$(call build_image,apps/services/corporate-email,corporate-email,corporate-email)

build-service-legacy-dms: ## Build and push the Legacy DMS MCP image
	$(call build_image,apps/services/legacy-dms,legacy-dms,legacy-dms)

build-service-kong: ## Build and push the custom Kong Gateway image
	$(call build_image,apps/services/kong,kong-gateway)

build-services: ## Build all MCP service + Kong images concurrently
	@$(MAKE) -j4 build-service-income-verification build-service-corporate-email build-service-legacy-dms build-service-kong
	@echo "✅ All service images built and pushed!"

build-images: build-services build-agents ## Build every image deployed by Layer 5

# ==============================================================================
# Layer 5: workloads (MCP services -> agents -> Kong -> IAP egress), Terragrunt DAG
# ==============================================================================

deploy-workloads: ## Deploy Layer 5 in dependency order (MCP services, agents, Kong, IAP egress, test VM)
	@echo "🚀 Deploying Layer 5 workloads for $(ENV)..."
	@cd $(LIVE_DIR)/layer-5-workloads && terragrunt --non-interactive run --all apply
	@echo "✨ Layer 5 workloads deployed!"
	@$(MAKE) --no-print-directory verify-images ENV=$(ENV)

verify-images: ## Check every Layer 5 workload runs the digest its tag points to (runs after deploy-workloads)
	@bash scripts/verify_images.sh $(ENV)

deploy-services: ## Deploy the 3 MCP services on Cloud Run (+ their Agent Registry entries)
	@for s in corporate-email income-verification legacy-dms; do \
		echo "🚀 Deploying $$s..."; \
		(cd $(LIVE_DIR)/layer-5-workloads/services/$$s && terragrunt --non-interactive apply -auto-approve) || exit 1; \
	done

deploy-ai-coe-mortgage-specialist: ## Deploy the AI CoE mortgage specialist (A2A) Reasoning Engine
	@echo "🚀 Deploying ai-coe-mortgage-specialist..."
	@cd $(LIVE_DIR)/layer-5-workloads/agents/ai-coe-mortgage-specialist && terragrunt --non-interactive apply -auto-approve

deploy-cx-mortgage-orchestrator: ## Deploy the CX mortgage orchestrator Reasoning Engine
	@echo "🚀 Deploying cx-mortgage-orchestrator..."
	@cd $(LIVE_DIR)/layer-5-workloads/agents/cx-mortgage-orchestrator && terragrunt --non-interactive apply -auto-approve

deploy-agents: deploy-ai-coe-mortgage-specialist deploy-cx-mortgage-orchestrator ## Deploy both Reasoning Engine agents

deploy-gateway: ## Deploy Kong API Gateway (re-run after agents are recreated: routes use engine IDs)
	@echo "🚀 Deploying Kong API Gateway..."
	@cd $(LIVE_DIR)/layer-5-workloads/services/kong && terragrunt --non-interactive apply -auto-approve

deploy-iap-egress: ## Grant roles/iap.egressor on every Agent Registry entry (final Layer 5 step)
	@cd $(LIVE_DIR)/layer-5-workloads/services/iap-egress && terragrunt --non-interactive apply -auto-approve

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
	@cd $(LIVE_DIR)/layer-5-workloads && terragrunt --non-interactive run --all destroy
	@cd $(LIVE_DIR)/layer-4-governance && terragrunt --non-interactive destroy -auto-approve
	@cd $(LIVE_DIR)/layer-3-security && terragrunt --non-interactive destroy -auto-approve
	@cd $(LIVE_DIR)/layer-2-networking && terragrunt --non-interactive destroy -auto-approve
	@cd $(LIVE_DIR)/layer-1-projects && terragrunt --non-interactive destroy -auto-approve
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
	@uv run python apps/agents/cx-mortgage-orchestrator/scripts/chaos_telemetry_test.py

load-test-cx-mortgage-orchestrator: ## Run Locust load test against the CX mortgage orchestrator on Vertex AI Reasoning Engines
	@echo "⚡ Running Locust load test for cx-mortgage-orchestrator on Vertex AI..."
	@uv run locust -f apps/agents/cx-mortgage-orchestrator/scripts/locustfile.py --headless -u 5 -r 1 --run-time 1m --host https://us-central1-aiplatform.googleapis.com

# ==============================================================================
# Documentation site (MkDocs Material, published to GitHub Pages by .github/workflows/docs.yml)
# ==============================================================================

docs-serve: ## Preview the documentation site locally on http://127.0.0.1:8000 (live reload)
	@uv run --only-group docs mkdocs serve -f docs/mkdocs.yml

docs-build: ## Build the documentation site into site/ in strict mode (fails on broken links)
	@uv run --only-group docs mkdocs build --strict -f docs/mkdocs.yml

clean: ## Clean python virtual environments, caches, and terragrunt cache files recursively
	@echo "🧹 Cleaning up local caches and environments..."
	@rm -rf .venv .uv .pytest_cache
	@find . -type d -name "__pycache__" -exec rm -rf {} +
	@find . -type d -name ".terraform" -exec rm -rf {} +
	@find . -type d -name ".terragrunt-cache" -exec rm -rf {} +
	@find . -type f -name "*.tfstate*" -exec rm -f {} +
	@echo "✨ Clean complete!"
