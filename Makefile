# Copyright 2025 Google LLC
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

.PHONY: all infra tools agent preflight bootstrap run-mcp help

all: preflight bootstrap ## Run the full deployment pipeline and enable trace analytics
	@bash deploy.sh all
	@$(MAKE) stress-test
	@echo "🔄 Re-deploying infrastructure to link Cloud Trace BigQuery dataset..."
	@export ENABLE_TRACE_LOGGING_LINK=true ENABLE_IAM_USER=true && bash deploy.sh infra

infra: ## Deploy infrastructure only (Terraform)
	@bash deploy.sh infra

tools: ## Deploy tools only (MCP Servers)
	@bash deploy.sh tools

agent: ## Deploy agents only (Topological DAG)
	@python3 agents/infra/python/dag_deployer.py

preflight: ## Run preflight checklist to validate active GCP project, credentials, and billing status
	@bash ./preflight.sh

bootstrap: ## Setup local Python virtual environments and install dependencies for all agents
	@bash ./setup_local.sh

run-mcp: ## Run the 3 MCP servers locally on dedicated localhost ports
	@bash ./tools_mcp/run_local.sh

stress-test: ## Run the Locust stress test against the deployed agent
	@echo "🔑 Checking authentication..."
	@gcloud auth print-access-token >/dev/null 2>&1 || (echo "❌ Session expired or not authenticated. Please run 'gcloud auth login' and try again." && exit 1)
	@echo "🚀 Initiating Locust load test..."
	@export _AUTH_TOKEN=$$(gcloud auth print-access-token -q) && \
	.locust_env/bin/locust -f tests/load_test/load_test.py \
		--headless \
		-t 30s -u 5 -r 2 \
		--csv=tests/load_test/.results/results \
		--html=tests/load_test/.results/report.html
	@echo "✅ Load test complete! Results saved in tests/load_test/.results/"

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'
