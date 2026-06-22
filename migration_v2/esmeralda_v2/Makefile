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

.PHONY: help bootstrap test run-local deploy-foundations deploy-workloads clean preflight

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

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
	@uv run --package corporate-email pytest app/mcp-servers/corporate-email/test_main.py
	@echo "🧪 Running unit tests for income-verification-api..."
	@uv run --package income-verification-api pytest app/mcp-servers/income-verification/test_main.py
	@echo "🧪 Running unit tests for legacy-dms..."
	@uv run --package legacy-dms pytest app/mcp-servers/legacy-dms/test_server.py
	@echo "🧪 Running unit tests for mortgage-agent..."
	@uv run --package mortgage-agent pytest app/agents/base-adk-agent/tests/test_remote_agent.py
	@echo "🧪 Running unit tests for a2a-mortgage-agent..."
	@uv run --package a2a-mortgage-agent pytest app/agents/a2a-agent/tests/test_agent.py
	@echo "✅ All unit tests passed!"

run-local: ## Launch the 3 MCP servers locally on dedicated localhost ports
	@echo "🚀 Launching MCP Servers..."
	@echo "📧 corporate-email running on http://localhost:8001"
	@uv run --package corporate-email uvicorn main:app --port 8001 & \
	 echo "💰 income-verification-api running on http://localhost:8002" && \
	 uv run --package income-verification-api uvicorn main:app --port 8002 & \
	 echo "🗄️ legacy-dms running on http://localhost:8003" && \
	 uv run --package legacy-dms uvicorn server:app --port 8003 & \
	 wait

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
