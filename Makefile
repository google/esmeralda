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

# Execute all commands using interactive bash with alias expansion enabled (critical for remote dev VMs & IDEs)
SHELL := /bin/bash
.SHELLFLAGS := -O expand_aliases -lc

ENV ?= dev

# Every target that touches an environment or an image is a thin alias of the `esmeralda` CLI
# (`uv run esmeralda --help`); a guard test (tests/guards/test_makefile_cli.py) fails if a recipe calls
# terragrunt, `gcloud builds` or `gcloud run deploy` directly.
ESM = uv run esmeralda

# Layer 0 (shared CI/CD) unit. With the local backend its state lives in the checkout that applied it, so a
# git worktree has none: the CLI then reads the outputs from the main checkout (and refuses to deploy layer 0
# from the worktree). Override with CICD_DIR=<checkout>/infrastructure/live/shared/layer-0-cicd.
CICD_DIR ?=
CICD_FLAG = $(if $(CICD_DIR),--cicd-dir $(CICD_DIR))

# Image builds (env-neutral, shared dev repository): :$(BUILD_TAG) and :dev-<gitsha>
BUILD_TAG ?= dev-latest
BUILD_FLAGS = --tag $(BUILD_TAG) $(CICD_FLAG)

# Deploys: ALLOW_DESTROY=1 permits destroy/replace plans (the destroy guard of `esmeralda apply`, per unit)
ALLOW_DESTROY ?=
DEPLOY_FLAGS = --env $(ENV) $(if $(filter 1 true,$(ALLOW_DESTROY)),--allow-destroy,) $(CICD_FLAG)

# Promotion (make promote VERSION=vX.Y.Z SOURCE=dev-<sha> [VERIFIED=YYYY-MM-DD]): see "Releases" below
VERSION ?=
SOURCE ?=
VERIFIED ?= $(shell date -u +%F)

# uv: always install exactly what uv.lock says and never re-resolve locally. The lock is generated
# against public PyPI by `make lock` (scripts/uv_lock.sh, runs in Cloud Build); re-locking on a machine
# with a different package index (e.g. a corporate proxy) would rewrite every URL in uv.lock.
export UV_FROZEN := 1

# Terraform / Terragrunt from the standard per-user install locations, if present
export PATH := $(HOME)/.terraform/bin:$(HOME)/.terragrunt/bin:$(PATH)

# Terragrunt variables (ESMERALDA_BILLING_ACCOUNT, optional ESMERALDA_ORG_ID and ESMERALDA_ALERT_EMAIL) from
# the user-local env file, outside the repo (never commit it). Recipes run in a login shell that doesn't read
# ~/.bashrc, so the file is read here, the same file the `esmeralda` CLI reads. Plain KEY=VALUE lines only: no
# quotes (make would keep them) and only ESMERALDA_* keys. Variables already set in the environment or on the
# command line win.
ESMERALDA_ENV_FILE ?= $(HOME)/.config/esmeralda/env
_esm_env_billing := $(ESMERALDA_BILLING_ACCOUNT)
_esm_env_org := $(ESMERALDA_ORG_ID)
_esm_env_alert := $(ESMERALDA_ALERT_EMAIL)
-include $(ESMERALDA_ENV_FILE)
ifneq ($(strip $(_esm_env_billing)),)
ESMERALDA_BILLING_ACCOUNT := $(_esm_env_billing)
endif
ifneq ($(strip $(_esm_env_org)),)
ESMERALDA_ORG_ID := $(_esm_env_org)
endif
ifneq ($(strip $(_esm_env_alert)),)
ESMERALDA_ALERT_EMAIL := $(_esm_env_alert)
endif
# Export only what is set (exporting an undefined variable would pass an empty string to Terragrunt).
ifneq ($(strip $(ESMERALDA_BILLING_ACCOUNT)),)
export ESMERALDA_BILLING_ACCOUNT
endif
ifneq ($(strip $(ESMERALDA_ORG_ID)),)
export ESMERALDA_ORG_ID
endif
ifneq ($(strip $(ESMERALDA_ALERT_EMAIL)),)
export ESMERALDA_ALERT_EMAIL
endif

# Terraform CLI for validation and the gateway harness: terraform if installed, else OpenTofu.
TF ?= $(shell command -v terraform 2>/dev/null || command -v tofu 2>/dev/null || echo terraform)

.PHONY: help bootstrap lock lock-check test test-all test-agents test-terraform test-gateway-unit test-guards test-apigee test-partner run-mcp-local \
	test-e2e serve deploy-cicd deploy-projects deploy-networking deploy-security \
	query deploy-foundations deploy-governance build-ai-coe-mortgage-specialist build-cx-mortgage-orchestrator build-agents \
	build-service-income-verification build-service-corporate-email build-service-legacy-dms build-service-envoy \
	build-services build-images deploy-workloads verify-images deploy-services deploy-ai-coe-mortgage-specialist \
	deploy-cx-mortgage-orchestrator deploy-agents deploy-gateway deploy-entrypoint test-gateway deploy-iap-egress deploy-all destroy-all status-release \
	verify promote test-governance-chaos load-test load-test-partner docs-serve docs-build clean preflight \
	status doctor units plan apply logs guard hooks build-package

help: ## Show this help message
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(firstword $(MAKEFILE_LIST)) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

preflight: ## Run preflight checklist to validate active GCP project, credentials, and billing status
	@chmod +x ./scripts/preflight.sh
	@./scripts/preflight.sh

hooks: ## Install the git hooks (.githooks: pre-commit sensitive scan, pre-push uv.lock check)
	@git config core.hooksPath .githooks && echo "🪝 Git hooks installed (.githooks: pre-commit sensitive scan, pre-push uv.lock check)"

bootstrap: preflight ## Setup local python virtual environments and sync workspace dependencies via uv
	@echo "📦 Bootstrapping local monorepo environment with uv..."
	@if ! command -v uv &>/dev/null; then \
		echo "❌ uv is not installed. Please install uv first (e.g., 'curl -LsSf https://astral.sh/uv/install.sh | sh')."; \
		exit 1; \
	fi
	@uv sync --all-packages --all-extras
	@$(MAKE) --no-print-directory hooks
	@echo "✅ Environment bootstrapped successfully! To activate the environment, run: source .venv/bin/activate"

test-agents: ## Fast execution for agent unit tests only
	@echo "🧪 Running unit tests for ADK Agents..."
	@uv run --package cx-mortgage-orchestrator --extra dev pytest apps/agents/cx-mortgage-orchestrator/tests/
	@uv run --package ai-coe-mortgage-specialist --extra dev pytest apps/agents/ai-coe-mortgage-specialist/tests/
	@echo "🧪 Running unit tests for the esmeralda library..."
	@uv run --package esmeralda --extra dev pytest packages/esmeralda/tests/
	@echo "✅ Agent tests passed!"

test-terraform: ## Run syntax validation for all Terraform modules (+ workload image digest pinning guard, tests/guards/test_image_pinning.py)
	@uv run --package esmeralda --extra dev pytest tests/guards/test_image_pinning.py -q
	@echo "🧪 Validating Terraform syntax across all infrastructure modules..."
	@set -e; for d in $$(find infrastructure/modules -name main.tf -not -path '*/.terraform/*' -exec dirname {} \; | sort); do \
		echo "→ $$d"; \
		(cd $$d && $(TF) init -upgrade -backend=false -input=false >/dev/null && $(TF) validate -no-color); \
	done
	@echo "✅ Terraform validation passed!"

test-gateway-unit: ## Envoy gateway harness (offline): renders the config module and runs it in a real envoy (needs envoy + terraform/tofu)
	@echo "🧪 Running the Envoy gateway harness..."
	@TF_BIN=$(TF) uv run --package esmeralda --extra dev pytest tests/gateway -q

test-guards: ## Run repository hygiene, manifest parity, governance capability contract and security policy guards
	@echo "🛡️  Running repository, governance and security contract guards..."
	@TF_BIN=$(TF) uv run --package esmeralda --extra dev pytest tests/guards tests/governance tests/security -q
	@echo "✅ Repository guards passed!"

test-apigee: ## Offline unit and policy conformance tests for Apigee capability bundles and modules
	@echo "🧪 Running offline Apigee policy and JS conformance tests..."
	@TF_BIN=$(TF) uv run --package esmeralda --extra dev pytest tests/apigee tests/partner -q

test-all: lock-check test test-guards test-terraform test-gateway-unit test-apigee ## Run the uv.lock check, Python unit tests, repo guards, Terraform validation, Envoy gateway harness, and Apigee policy tests

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

run-mcp-local: ## Launch the 3 MCP servers locally on 127.0.0.1:8001-8003 until Ctrl+C (servers listed in esmeralda.local_mcp)
	@uv run --package esmeralda python -m esmeralda.local_mcp

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
	  local) uv run --package $(AGENT) esmeralda query --agent-dir apps/agents/$(AGENT) --local-mcp "$(QUERY)" ;; \
	  remote) ENGINE=$$($(ESM) status --env $(ENV) --unit layer-5-workloads/agents/$(AGENT) --output engine_id 2>/dev/null) && [ -n "$$ENGINE" ] || { echo "❌ No engine_id output for $(AGENT) in $(ENV)"; exit 1; }; \
	    uv run --package $(AGENT) esmeralda query --agent-dir apps/agents/$(AGENT) --engine "$$ENGINE" "$(QUERY)" ;; \
	  http://*|https://*) uv run --package $(AGENT) esmeralda query --agent-dir apps/agents/$(AGENT) --url "$(TARGET)" "$(QUERY)" ;; \
	  *) echo "❌ TARGET must be local, remote or a URL (got '$(TARGET)')"; exit 1 ;; \
	esac

PORT ?= 8080

serve: ## Serve an agent locally exactly as in the container, with its local MCP servers up: AGENT=<dir under apps/agents> [PORT=8080] [WEB=1 for the ADK dev UI]
	@[ -n "$(AGENT)" ] || { echo "❌ Set AGENT=<agent directory under apps/agents>, e.g. AGENT=cx-mortgage-orchestrator"; exit 1; }
	@uv run --package $(AGENT) esmeralda serve --agent-dir apps/agents/$(AGENT) --local-mcp --port $(PORT) $(if $(WEB),--web)

# ==============================================================================
# End-to-end tests of a deployed environment (engine IDs from the unit outputs)
# ==============================================================================

test-e2e: ## End-to-end check of a deployed env: specialist, then orchestrator -> specialist (fails on any error)
	@$(MAKE) --no-print-directory query AGENT=ai-coe-mortgage-specialist TARGET=remote ENV=$(ENV)
	@$(MAKE) --no-print-directory query AGENT=cx-mortgage-orchestrator TARGET=remote ENV=$(ENV)
	@echo "✅ End-to-end tests passed for $(ENV)!"

# ==============================================================================
# Deploys: aliases of `esmeralda deploy` (plan to a planfile, destroy guard per unit, apply; stops at the
# first failure). Layer 0: shared CI/CD (env-independent; one project, dev + release repositories).
# ==============================================================================

deploy-cicd: ## Deploy Layer 0: shared CI/CD project, dev + release Artifact Registry repos, builder/promoter SAs
	@$(ESM) deploy --layer 0 $(DEPLOY_FLAGS)

# ==============================================================================
# Layers 1-3: foundations (projects, networking, security + internal PKI)
# ==============================================================================

deploy-projects: ## Deploy Layer 1: Projects for $(ENV)
	@$(ESM) deploy --layer 1 $(DEPLOY_FLAGS)

deploy-networking: ## Deploy Layer 2: Networking for $(ENV)
	@$(ESM) deploy --layer 2 $(DEPLOY_FLAGS)

deploy-security: ## Deploy Layer 3: Security + internal Root CA for $(ENV)
	@$(ESM) deploy --layer 3 $(DEPLOY_FLAGS)

deploy-foundations: ## Deploy Layers 1-3 (Projects, Networking, Security)
	@$(ESM) deploy --layer 1 --layer 2 --layer 3 $(DEPLOY_FLAGS)

# ==============================================================================
# Layer 4: governance (Agent Gateway + ACT + IAP, Agent Registry, Model Armor, telemetry)
# ==============================================================================

deploy-governance: ## Deploy Layer 4: Governance, Agent Gateway, Observability & Alerts for $(ENV)
	@$(ESM) deploy --layer 4 $(DEPLOY_FLAGS)

# ==============================================================================
# Image builds: aliases of `esmeralda build`. Env-neutral, always pushed to the shared dev repository
# (:$(BUILD_TAG) and :dev-<gitsha>). Promotion to prd is `make promote VERSION=vX.Y.Z SOURCE=dev-<sha>`.
# ==============================================================================

build-ai-coe-mortgage-specialist: ## Build and push the AI CoE mortgage specialist (A2A) image
	@$(ESM) build ai-coe-mortgage-specialist --skip-tests $(BUILD_FLAGS)

build-cx-mortgage-orchestrator: ## Build and push the CX mortgage orchestrator image
	@$(ESM) build cx-mortgage-orchestrator --skip-tests $(BUILD_FLAGS)

build-agents: $(if $(SKIP_TESTS),,lock-check test test-terraform) ## Run tests (not the gateway harness: no envoy needed), then build both agent images concurrently (SKIP_TESTS=1 if you just ran them)
	@$(ESM) build agents --skip-tests $(BUILD_FLAGS)

build-service-income-verification: ## Build and push the Income Verification MCP image
	@$(ESM) build income-verification-api $(BUILD_FLAGS)

build-service-corporate-email: ## Build and push the Corporate Email MCP image
	@$(ESM) build corporate-email $(BUILD_FLAGS)

build-service-legacy-dms: ## Build and push the Legacy DMS MCP image
	@$(ESM) build legacy-dms $(BUILD_FLAGS)

build-service-envoy: ## Build and push the Envoy gateway image (digest-pinned upstream Envoy, no custom code)
	@$(ESM) build envoy-gateway $(BUILD_FLAGS)

build-services: ## Build all MCP service + Envoy gateway images concurrently
	@$(ESM) build services $(BUILD_FLAGS)

build-images: build-services build-agents ## Build every image deployed by Layer 5

# ==============================================================================
# Layer 5: workloads (agents -> MCP services -> gateways -> entry point -> IAP egress), Terragrunt DAG order
# ==============================================================================

deploy-workloads: ## Deploy Layer 5 in dependency order (every enabled unit, IAP egress last), then verify-images
	@$(ESM) deploy --layer 5 $(DEPLOY_FLAGS)

verify-images: ## Check every Layer 5 workload runs the expected digest: release/<env>.yaml if present, else its tag
	@$(ESM) build --verify --env $(ENV)

deploy-services: ## Deploy the 3 MCP services on Cloud Run (+ their Agent Registry entries)
	@$(ESM) deploy services/corporate-email services/income-verification services/legacy-dms $(DEPLOY_FLAGS)

deploy-ai-coe-mortgage-specialist: ## Deploy the AI CoE mortgage specialist (A2A) Reasoning Engine
	@$(ESM) deploy agents/ai-coe-mortgage-specialist $(DEPLOY_FLAGS)

deploy-cx-mortgage-orchestrator: ## Deploy the CX mortgage orchestrator Reasoning Engine
	@$(ESM) deploy agents/cx-mortgage-orchestrator $(DEPLOY_FLAGS)

deploy-agents: ## Deploy both Reasoning Engine agents (no deploy-time dependency between them)
	@$(ESM) deploy agents/ai-coe-mortgage-specialist agents/cx-mortgage-orchestrator $(DEPLOY_FLAGS)

deploy-gateway: ## Deploy the built-in direct backends (gateways/direct; re-run after agents are recreated: routes use engine IDs)
	@$(ESM) deploy gateways/direct $(DEPLOY_FLAGS)

deploy-entrypoint: ## Deploy the internal entry point (URL map, HTTPS proxy, forwarding rule) after the gateways
	@$(ESM) deploy entrypoint $(DEPLOY_FLAGS)

test-gateway: ## Probe every catalog host through the entry point from the test VM (IAP SSH), incl. the on-VM query probe (esmeralda verify --skip-query): ENV=<env>
	@$(ESM) verify --skip-query --env $(ENV)

test-partner: ## Run partner front-door conformance checks (401 unauth, 403/404 agent/MCP scope, SSE TTFB + answer, 429 quota): ENV=<env>
	@uv run esmeralda verify --partner --env $(ENV)

deploy-iap-egress: ## Grant roles/iap.egressor on every Agent Registry entry (final Layer 5 step)
	@$(ESM) deploy iap-egress $(DEPLOY_FLAGS)

# ==============================================================================
# Operations CLI (delegates to `uv run esmeralda ...`)
# ==============================================================================

UNIT ?=
TARGET_NAME ?=

status: ## Show git branch/SHA, active worktrees, local state units, image tags/digests, and deployed outputs: ENV=<env>
	@uv run esmeralda status --env $(ENV)

doctor: ## Workstation preflight check (tools, billing env var, ADC token, uv.lock, local states): ENV=<env>
	@uv run esmeralda doctor --env $(ENV)

units: ## List Terragrunt units, layer, dependencies, enabled/excluded status, and local state presence: ENV=<env>
	@uv run esmeralda units --env $(ENV)

plan: ## Run read-only terragrunt plan on a unit (or --all) and print a compact summary: [UNIT=<unit>] ENV=<env>
	@uv run esmeralda plan $(or $(UNIT),--all) --env $(ENV)

apply: ## Plan a unit to a planfile, enforce the destroy guard, and apply the saved planfile: UNIT=<unit> ENV=<env> [ALLOW_DESTROY=1]
	@uv run esmeralda apply $(UNIT) --env $(ENV) $(if $(filter 1 true,$(ALLOW_DESTROY)),--allow-destroy,)

verify: ## Run gateway conformance probes and remote specialist + orchestrator queries: ENV=<env>
	@uv run esmeralda verify --env $(ENV)

logs: ## Fetch recent Cloud Logging entries for an agent or MCP service: TARGET_NAME=<target> ENV=<env>
	@uv run esmeralda logs $(TARGET_NAME) --env $(ENV)

guard: ## Scan all tracked files in the repository for secrets and forbidden internal strings
	@uv run esmeralda guard --all

build-package: ## Build a standalone wheel and sdist of the esmeralda package (as esmeralda-platform; not published on PyPI) into dist/ and verify contents
	@$(ESM) build package

# ==============================================================================
# Whole environment
# ==============================================================================

deploy-all: ## Build an env from zero: Layer 0 -> 1-3 -> 4 -> images -> 5 (single pass; SKIP_TESTS=1 skips the tests before the agent builds)
	@$(ESM) deploy --all $(DEPLOY_FLAGS) --tag $(BUILD_TAG) $(if $(SKIP_TESTS),--skip-tests)
	@echo "🎉 $(ENV) deployed. Verify with: esmeralda verify --env $(ENV)"

destroy-all: ## Destroy an env's Layers 5 -> 1 (dev only; never touches the shared Layer 0)
	@if [ "$(ENV)" = "prd" ]; then echo "❌ destroy-all refuses ENV=prd."; exit 1; fi
	@read -p "⚠️  Destroy ALL of $(ENV) (layers 5 -> 1)? Type the env name to confirm: " c && [ "$$c" = "$(ENV)" ] && \
		$(ESM) destroy --env $(ENV) --confirm "$$c"
	@echo "🧹 $(ENV) destroyed. The shared Layer 0 CI/CD was not touched."

# ==============================================================================
# Releases: aliases of `esmeralda release` (copy dev images by digest into the immutable release repository)
# ==============================================================================

status-release: ## Show the prd release manifest (version, source, digests) and the shared repositories
	@$(ESM) release status

promote: ## Promote a verified dev build by digest + write release/prd.yaml: VERSION=vX.Y.Z SOURCE=dev-<sha> VERIFIED=YYYY-MM-DD; no deploy
	@if [ -z "$(VERSION)" ] || [ -z "$(SOURCE)" ] || [ -z "$(VERIFIED)" ]; then \
		echo "❌ Usage: make promote VERSION=vX.Y.Z SOURCE=dev-<sha> VERIFIED=YYYY-MM-DD (the date 'make verify ENV=dev' passed)"; exit 1; fi
	@$(ESM) release promote --version $(VERSION) --source $(SOURCE) --verified $(VERIFIED)

test-governance-chaos: ## Run local chaos simulation test for governance telemetry and alerts
	@echo "🧪 Running Esmeralda Governance Pipeline Chaos Test..."
	@uv run python apps/agents/cx-mortgage-orchestrator/scripts/chaos_telemetry_test.py

LOAD_AGENT ?= cx-mortgage-orchestrator
USERS ?= 5
DURATION ?= 1m

load-test: ## Locust load test of a deployed ADK agent: [LOAD_AGENT=cx-mortgage-orchestrator] [USERS=5] [DURATION=1m] ENV=dev (results in tests/load_test/.results)
	@ENGINE=$$($(ESM) status --env $(ENV) --unit layer-5-workloads/agents/$(LOAD_AGENT) --output engine_id 2>/dev/null) && [ -n "$$ENGINE" ] || { echo "❌ No engine_id output for $(LOAD_AGENT) in $(ENV)"; exit 1; }; \
	echo "⚡ Load testing $$ENGINE ($(USERS) users, $(DURATION))..."; \
	REMOTE_AGENT_ENGINE_ID="$$ENGINE" uv run locust -f tests/load_test/load_test.py --headless -u $(USERS) -r 1 --run-time $(DURATION) \
		--csv=tests/load_test/.results/results --html=tests/load_test/.results/report.html

# The partner load test runs on the single-instance specialist: keep its defaults small.
PARTNER_USERS ?= 2
PARTNER_DURATION ?= 5m

load-test-partner: ## Locust load test THROUGH the Apigee partner front door, run from the test VM: [USERS=2] [DURATION=5m] [INCLUDE_QUOTA=1] ENV=dev (results in tests/load_test/.results/partner)
	@uv run --package esmeralda python -m esmeralda.ops.partner_load $(ENV) \
		--users $(if $(filter command line environment,$(origin USERS)),$(USERS),$(PARTNER_USERS)) \
		--duration $(if $(filter command line environment,$(origin DURATION)),$(DURATION),$(PARTNER_DURATION)) \
		$(if $(filter 1 true,$(INCLUDE_QUOTA)),--include-quota,)

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
