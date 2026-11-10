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
LIVE_DIR = infrastructure/live/$(ENV)

# Layer 0 (shared CI/CD) unit. With the local backend its state lives in the checkout that applied it, so a
# git worktree has none: when this checkout has no layer-0 local state and the main checkout (resolved via
# `git rev-parse --git-common-dir`) has it, read the outputs from the main checkout. Override with
# CICD_DIR=<checkout>/infrastructure/live/shared/layer-0-cicd.
CICD_UNIT = infrastructure/live/shared/layer-0-cicd
CICD_STATE = infrastructure/live/.local_states/shared/layer-0-cicd/terraform.tfstate
MAIN_CHECKOUT := $(patsubst %/,%,$(dir $(abspath $(shell git rev-parse --git-common-dir 2>/dev/null))))
ifeq ($(origin CICD_DIR),undefined)
ifeq ($(wildcard $(CICD_STATE)),)
ifneq ($(and $(MAIN_CHECKOUT),$(wildcard $(MAIN_CHECKOUT)/$(CICD_STATE))),)
CICD_DIR := $(MAIN_CHECKOUT)/$(CICD_UNIT)
CICD_FROM_MAIN := 1
endif
endif
endif
CICD_DIR ?= $(CICD_UNIT)

# Image builds (env-neutral, shared dev repository)
BUILD_TAG ?= dev-latest
GIT_SHA := $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)

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

# Terragrunt variables (ESMERALDA_BILLING_ACCOUNT, optional ESMERALDA_ORG_ID) from the user-local env file,
# outside the repo (never commit it). Recipes run in a login shell that doesn't read ~/.bashrc, so the file
# is read here, the same file the `esmeralda` CLI reads. Plain KEY=VALUE lines only: no quotes (make would
# keep them) and only ESMERALDA_* keys. Variables already set in the environment or on the command line win.
ESMERALDA_ENV_FILE ?= $(HOME)/.config/esmeralda/env
_esm_env_billing := $(ESMERALDA_BILLING_ACCOUNT)
_esm_env_org := $(ESMERALDA_ORG_ID)
-include $(ESMERALDA_ENV_FILE)
ifneq ($(strip $(_esm_env_billing)),)
ESMERALDA_BILLING_ACCOUNT := $(_esm_env_billing)
endif
ifneq ($(strip $(_esm_env_org)),)
ESMERALDA_ORG_ID := $(_esm_env_org)
endif
# Export only what is set (exporting an undefined variable would pass an empty string to Terragrunt).
ifneq ($(strip $(ESMERALDA_BILLING_ACCOUNT)),)
export ESMERALDA_BILLING_ACCOUNT
endif
ifneq ($(strip $(ESMERALDA_ORG_ID)),)
export ESMERALDA_ORG_ID
endif

# Terraform CLI for validation and the gateway harness: terraform if installed, else OpenTofu.
TF ?= $(shell command -v terraform 2>/dev/null || command -v tofu 2>/dev/null || echo terraform)

.PHONY: help bootstrap lock lock-check test test-all test-agents test-terraform test-gateway-unit test-guards test-apigee test-partner run-mcp-local \
	test-e2e serve deploy-cicd deploy-projects deploy-networking deploy-security \
	query deploy-foundations deploy-governance build-ai-coe-mortgage-specialist build-cx-mortgage-orchestrator build-agents \
	build-service-income-verification build-service-corporate-email build-service-legacy-dms build-service-envoy \
	build-services build-images deploy-workloads verify-images deploy-services deploy-ai-coe-mortgage-specialist \
	deploy-cx-mortgage-orchestrator deploy-agents deploy-gateway deploy-entrypoint test-gateway deploy-iap-egress deploy-all destroy-all status-release \
	verify test-release promote test-governance-chaos load-test docs-serve docs-build clean preflight \
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

test-terraform: ## Run syntax validation for all Terraform modules (+ workload image digest pinning check)
	@bash scripts/check_image_pinning.sh
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
	@TF_BIN=$(TF) uv run --package esmeralda --extra dev pytest tests/apigee -q

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
	@$(MAKE) --no-print-directory test-release
	@echo "✅ All unit tests passed!"

test-release: ## Unit tests for the release manifest writer (scripts/release_manifest.py, stdlib only)
	@echo "🧪 Running unit tests for the release manifest..."
	@python3 -m unittest discover -s scripts/tests -q

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

PORT ?= 8080

serve: ## Serve an agent locally exactly as in the container: AGENT=<dir under apps/agents> [PORT=8080] [WEB=1 for the ADK dev UI]
	@[ -n "$(AGENT)" ] || { echo "❌ Set AGENT=<agent directory under apps/agents>, e.g. AGENT=cx-mortgage-orchestrator"; exit 1; }
	@scripts/with_local_mcp.sh uv run --package $(AGENT) esmeralda serve --agent-dir apps/agents/$(AGENT) --local --port $(PORT) $(if $(WEB),--web)

# ==============================================================================
# End-to-end tests of a deployed environment (engine IDs from Terragrunt outputs)
# ==============================================================================

test-e2e: ## End-to-end check of a deployed env: specialist, then orchestrator -> specialist (fails on any error)
	@$(MAKE) --no-print-directory query AGENT=ai-coe-mortgage-specialist TARGET=remote ENV=$(ENV)
	@$(MAKE) --no-print-directory query AGENT=cx-mortgage-orchestrator TARGET=remote ENV=$(ENV)
	@echo "✅ End-to-end tests passed for $(ENV)!"

# ==============================================================================
# Layer 0: shared CI/CD (env-independent; one project, dev + release repositories)
# ==============================================================================

deploy-cicd: ## Deploy Layer 0: shared CI/CD project, dev + release Artifact Registry repos, builder/promoter SAs
	@echo "🏗️  Deploying Layer 0: shared CI/CD..."
	@if [ -n "$(CICD_FROM_MAIN)" ]; then \
		echo "❌ Layer-0 state is in the main checkout ($(MAIN_CHECKOUT)), not in this worktree: run make deploy-cicd there, or set CICD_DIR explicitly." >&2; \
		exit 1; \
	fi
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

# ==============================================================================
# Image builds: env-neutral, always pushed to the shared dev repository
# (:$(BUILD_TAG) and :dev-<gitsha>). Promotion to prd is `make promote VERSION=vX.Y.Z SOURCE=dev-<sha>`.
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
	$(if $(CICD_FROM_MAIN),@echo "ℹ️  No layer-0 state in this worktree: reading CI/CD outputs from $(CICD_DIR)")
	@CICD_JSON=$$(cd $(CICD_DIR) && terragrunt output -json) && \
	CICD_PROJ=$$(echo "$$CICD_JSON" | jq -r '.cicd_project_id.value // empty') && \
	if [ -z "$$CICD_PROJ" ]; then \
		echo "❌ No layer-0 CI/CD outputs in $(CICD_DIR) (no state there?). Deploy layer 0 (make deploy-cicd), or set CICD_DIR=<checkout with layer-0 state>/$(CICD_UNIT)." >&2; \
		exit 1; \
	fi && \
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

build-agents: $(if $(SKIP_TESTS),,lock-check test test-terraform) ## Run tests (not the gateway harness: no envoy needed), then build both agent images concurrently (SKIP_TESTS=1 if you just ran them)
	@$(MAKE) -j2 build-ai-coe-mortgage-specialist build-cx-mortgage-orchestrator
	@echo "✅ All agent images built and pushed!"

build-service-income-verification: ## Build and push the Income Verification MCP image
	$(call build_image,apps/services/income-verification,income-verification-api,income-verification-api)

build-service-corporate-email: ## Build and push the Corporate Email MCP image
	$(call build_image,apps/services/corporate-email,corporate-email,corporate-email)

build-service-legacy-dms: ## Build and push the Legacy DMS MCP image
	$(call build_image,apps/services/legacy-dms,legacy-dms,legacy-dms)

build-service-envoy: ## Build and push the Envoy gateway image (digest-pinned upstream Envoy, no custom code)
	$(call build_image,apps/services/envoy,envoy-gateway)

build-services: ## Build all MCP service + Envoy gateway images concurrently
	@$(MAKE) -j4 build-service-income-verification build-service-corporate-email build-service-legacy-dms build-service-envoy
	@echo "✅ All service images built and pushed!"

build-images: build-services build-agents ## Build every image deployed by Layer 5

# ==============================================================================
# Layer 5: workloads (MCP services -> agents -> gateways -> entry point -> IAP egress), Terragrunt DAG
# ==============================================================================

deploy-workloads: ## Deploy Layer 5 in dependency order (MCP services, agents, gateways, entry point, IAP egress, test VM)
	@echo "🚀 Deploying Layer 5 workloads for $(ENV)..."
	@cd $(LIVE_DIR)/layer-5-workloads && terragrunt --non-interactive run --all apply
	@echo "✨ Layer 5 workloads deployed!"
	@$(MAKE) --no-print-directory verify-images ENV=$(ENV)

verify-images: ## Check every Layer 5 workload runs the expected digest: release/<env>.yaml if present, else its tag
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

deploy-agents: ## Deploy both Reasoning Engine agents, in parallel (no deploy-time dependency between them)
	@$(MAKE) --no-print-directory -j2 deploy-ai-coe-mortgage-specialist deploy-cx-mortgage-orchestrator

deploy-gateway: ## Deploy the built-in direct backends (gateways/direct; re-run after agents are recreated: routes use engine IDs)
	@echo "🚀 Deploying the direct backends (serverless NEGs + aiplatform PSC NEG)..."
	@cd $(LIVE_DIR)/layer-5-workloads/gateways/direct && terragrunt --non-interactive apply -auto-approve

deploy-entrypoint: ## Deploy the internal entry point (URL map, HTTPS proxy, forwarding rule) after the gateways
	@echo "🚪 Deploying the internal entry point..."
	@cd $(LIVE_DIR)/layer-5-workloads/entrypoint && terragrunt --non-interactive apply -auto-approve

test-gateway: ## Probe every catalog host through the entry point from the test VM (IAP SSH), incl. a real orchestrator query: ENV=<env>
	@bash scripts/test_gateway.sh $(ENV)

test-partner: ## Run partner front-door conformance checks (401 unauth, 403/404 agent/MCP scope, SSE TTFB + answer, 429 quota): ENV=<env>
	@uv run esmeralda verify --partner --env $(ENV)

deploy-iap-egress: ## Grant roles/iap.egressor on every Agent Registry entry (final Layer 5 step)
	@IAP_DIR="$(LIVE_DIR)/layer-5-workloads/iap-egress"; \
	[ -d "$$IAP_DIR" ] || IAP_DIR="$(LIVE_DIR)/layer-5-workloads/services/iap-egress"; \
	cd "$$IAP_DIR" && terragrunt --non-interactive apply -auto-approve

# ==============================================================================
# Operations CLI (delegates to `uv run esmeralda ...`)
# ==============================================================================

UNIT ?=
TARGET_NAME ?=
ALLOW_DESTROY ?=

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

build-package: ## Build the PyPI wheel and sdist (esmeralda-platform) into dist/ and verify contents
	@rm -rf dist && mkdir -p dist
	@TMP_PKG=$$(mktemp -d) && trap 'rm -rf "$$TMP_PKG"' EXIT && \
		cp -R packages/esmeralda/src packages/esmeralda/README.md "$$TMP_PKG/" && \
		sed 's/^name = "esmeralda"/name = "esmeralda-platform"/' packages/esmeralda/pyproject.toml > "$$TMP_PKG/pyproject.toml" && \
		uv build "$$TMP_PKG" --wheel --sdist --out-dir dist --offline --quiet
	@uv run python -m zipfile -l dist/esmeralda_platform-*.whl
	@uv run python -c 'import glob, zipfile; from esmeralda.ops import guard; whl = glob.glob("dist/esmeralda_platform-*.whl")[0]; names = zipfile.ZipFile(whl).namelist(); assert "esmeralda/cli.py" in names and any(n.startswith("esmeralda/ops/") for n in names), names; bad = [f for n in names for f in guard.check_path(n)]; assert not bad, bad; print("✅ Verified wheel contents:", whl)'

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

status-release: ## Show the prd release manifest (version, source, digests) and the shared repositories
	@bash scripts/promote_release.sh status

promote: ## Promote a verified dev build by digest + write release/prd.yaml: VERSION=vX.Y.Z SOURCE=dev-<sha> VERIFIED=YYYY-MM-DD; no deploy
	@if [ -z "$(VERSION)" ] || [ -z "$(SOURCE)" ] || [ -z "$(VERIFIED)" ]; then \
		echo "❌ Usage: make promote VERSION=vX.Y.Z SOURCE=dev-<sha> VERIFIED=YYYY-MM-DD (the date 'make verify ENV=dev' passed)"; exit 1; fi
	@bash scripts/promote_release.sh promote --version $(VERSION) --source $(SOURCE) --verified $(VERIFIED)

test-governance-chaos: ## Run local chaos simulation test for governance telemetry and alerts
	@echo "🧪 Running Esmeralda Governance Pipeline Chaos Test..."
	@uv run python apps/agents/cx-mortgage-orchestrator/scripts/chaos_telemetry_test.py

LOAD_AGENT ?= cx-mortgage-orchestrator
USERS ?= 5
DURATION ?= 1m

load-test: ## Locust load test of a deployed ADK agent: [LOAD_AGENT=cx-mortgage-orchestrator] [USERS=5] [DURATION=1m] ENV=dev (results in tests/load_test/.results)
	@ENGINE=$$(cd $(LIVE_DIR)/layer-5-workloads/agents/$(LOAD_AGENT) && terragrunt output -raw engine_id 2>/dev/null) && [ -n "$$ENGINE" ] || { echo "❌ No engine_id output for $(LOAD_AGENT) in $(ENV)"; exit 1; }; \
	echo "⚡ Load testing $$ENGINE ($(USERS) users, $(DURATION))..."; \
	REMOTE_AGENT_ENGINE_ID="$$ENGINE" uv run locust -f tests/load_test/load_test.py --headless -u $(USERS) -r 1 --run-time $(DURATION) \
		--csv=tests/load_test/.results/results --html=tests/load_test/.results/report.html

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
