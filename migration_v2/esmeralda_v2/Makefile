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

.PHONY: help bootstrap test run-mcp-local test-a2a-local test-root-local deploy-foundations deploy-workloads clean preflight

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
	@uv sync
	@echo "✅ Environment bootstrapped successfully! To activate the environment, run: source .venv/bin/activate"

test: ## Run unit tests across all workspace members
	@echo "🧪 Running unit tests for corporate-email..."
	@uv run --package corporate-email --extra dev pytest app/mcp-servers/corporate-email/test_main.py
	@echo "🧪 Running unit tests for income-verification-api..."
	@uv run --package income-verification-api --extra dev pytest app/mcp-servers/income-verification/test_main.py
	@echo "🧪 Running unit tests for legacy-dms..."
	@uv run --package legacy-dms --extra dev pytest app/mcp-servers/legacy-dms/test_server.py
	@echo "🧪 Running unit tests for mortgage-agent..."
	@uv run --package mortgage-agent --extra dev pytest app/agents/base-adk-agent/tests/test_remote_agent.py
	@echo "🧪 Running unit tests for a2a-mortgage-agent..."
	@uv run --package a2a-mortgage-agent --extra dev pytest app/agents/a2a-agent/tests/test_agent.py
	@echo "✅ All unit tests passed!"

run-mcp-local: ## Launch the 3 MCP servers locally on dedicated localhost ports
	@[ -n "$$(lsof -t -i :8001 -i :8002 -i :8003 2>/dev/null)" ] && kill -9 $$(lsof -t -i :8001 -i :8002 -i :8003 2>/dev/null) 2>/dev/null || true
	@echo "🚀 Launching MCP Servers..."
	@uv run --package corporate-email uvicorn main:app --app-dir app/mcp-servers/corporate-email --port 8001 & pid_email=$$! ; \
	 echo "📧 corporate-email running on http://localhost:8001" ; \
	 uv run --package income-verification-api uvicorn main:app --app-dir app/mcp-servers/income-verification --port 8002 & pid_income=$$! ; \
	 echo "💰 income-verification-api running on http://localhost:8002" ; \
	 uv run --package legacy-dms uvicorn server:app --app-dir app/mcp-servers/legacy-dms --port 8003 & pid_dms=$$! ; \
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
	uv run --package a2a-mortgage-agent python app/agents/a2a-agent/test_local.py "$(QUERY)"; \
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
	uv run --package mortgage-agent python app/agents/base-adk-agent/test_local.py "$(QUERY)"; \
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
	exit $$status

deploy-foundations: ## Deploy core foundations (Projects, Networking, Security) via Terragrunt
	@echo "🏗️  Deploying Stage 1: Projects..."
	@cd infrastructure/live/dev/stage-1-projects && terragrunt apply --terragrunt-non-interactive -auto-approve
	@echo "🏗️  Deploying Stage 2: Networking..."
	@cd infrastructure/live/dev/stage-2-networking && terragrunt apply --terragrunt-non-interactive -auto-approve
	@echo "🏗️  Deploying Stage 3: Security..."
	@cd infrastructure/live/dev/stage-3-security && terragrunt apply --terragrunt-non-interactive -auto-approve
	@echo "✅ Foundations deployed successfully!"

deploy-workloads: ## Deploy application workloads (MCP Servers, Agents) via Terragrunt
	@echo "🚀 Deploying Stage 4: Workloads..."
	@cd infrastructure/live/dev/stage-4-workloads && terragrunt apply --terragrunt-non-interactive -auto-approve
	@echo "✅ Workloads deployed successfully!"

clean: ## Clean python virtual environments, caches, and terragrunt cache files recursively
	@echo "🧹 Cleaning up local caches and environments..."
	@rm -rf .venv .uv .pytest_cache
	@find . -type d -name "__pycache__" -exec rm -rf {} +
	@find . -type d -name ".terraform" -exec rm -rf {} +
	@find . -type d -name ".terragrunt-cache" -exec rm -rf {} +
	@find . -type f -name "*.tfstate*" -exec rm -f {} +
	@echo "✨ Clean complete!"
