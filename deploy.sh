#!/bin/bash

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

set -e

# --- Environment Setup ---
# Safely append common developer paths to ensure tools like terraform and gcloud are found
# even in non-interactive shells where .bashrc might exit early.
export PATH="$PATH:$HOME/.local/bin:$HOME/bin:/usr/local/bin:/opt/homebrew/bin:$HOME/google-cloud-sdk/bin:$HOME/.terraform/bin"

# --- Local Utilities
log_info() { printf "[*] %s\n" "$@"; }
log_success() { printf "[+] %s\n" "$@"; }
log_error() { printf "[-] %s\n" "$@"; exit 1; }

check_dependencies() {
    local deps=("$@")
    for dep in "${deps[@]}"; do
        if ! command -v "$dep" &> /dev/null; then
            log_error "Required command not found: '$dep'. Please ensure it is installed and in your PATH."
        fi
    done
}

# --- Load Context from root .env if it exists
if [[ -f ".env" ]]; then
    log_info "Loading root context from .env"
    export $(grep -v '^#' .env | xargs)
fi

ACTION="${1:-all}"

# --- Execution Logic
deploy_infrastructure() {
    log_info "Phase 1: Infrastructure"
    (cd infrastructure && bash deploy.sh)
    # Reload root context after infrastructure deployment might have updated it
    [[ -f ".env" ]] && export $(grep -v '^#' .env | xargs)
}

deploy_tools() {
    log_info "Phase 2: Tools (MCP Servers)"
    (cd tools && bash deploy.sh)
    [[ -f ".env" ]] && export $(grep -v '^#' .env | xargs)
}

deploy_agent() {
    log_info "Phase 3: Vertex AI Agents"
    (cd agents && bash deploy.sh)
}

case "$ACTION" in
    "infrastructure"|"infra") deploy_infrastructure ;;
    "tools"|"mcp") deploy_tools ;;
    "agent") deploy_agent ;;
    "all")
        deploy_infrastructure
        deploy_tools
        deploy_agent
        ;;
    *) log_error "Invalid argument: $ACTION. Use: all, infra, tools, mcp, agent" ;;
esac

log_success "Deployment process finished."
