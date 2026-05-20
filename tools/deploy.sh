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
export PATH="$PATH:$HOME/.local/bin:$HOME/bin:/usr/local/bin:/opt/homebrew/bin:$HOME/google-cloud-sdk/bin:$HOME/.terraform/bin"

# --- Local Utilities (Zero Dependency)
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

# Finds the root context file (.env) reliably using git
find_context() {
    echo "$(git rev-parse --show-toplevel)/.env"
}

load_context() {
    local env_file=$(find_context)
    if [[ -n "$env_file" ]]; then
        log_info "Loading context from $env_file"
        export $(grep -v "^#" "$env_file" | xargs)
    fi
}

set_context() {
    local key="$1"
    local value="$2"
    local env_file=$(find_context)
    
    # If no env file exists, default to local .env
    [[ -z "$env_file" ]] && env_file=".env"
    
    touch "$env_file"
    if grep -q "^${key}=" "$env_file"; then
        sed -i "s|^${key}=.*|${key}=\"${value}\"|" "$env_file"
    else
        echo "${key}=\"${value}\"" >> "$env_file"
    fi
    export "$key"="$value"
}

# --- Load Configuration
load_context

# --- Validate Dependencies
check_dependencies gcloud python3

# --- Auto-Discovery
if [ -z "$PROJECT_ID" ]; then
    log_info "Attempting to discover PROJECT_ID..."
    PROJECT_ID=$(gcloud config get-value project 2>/dev/null || echo "")
fi

if [ -z "$PROJECT_ID" ]; then
    log_error "PROJECT_ID environment variable is not set and could not be discovered."
fi

# --- Setup virtual environment using UV
log_info "Setting up local environment using uv..."
if ! command -v uv &> /dev/null; then
    log_info "uv not found, installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

if [ ! -d ".venv" ]; then
    uv venv .venv
fi
source .venv/bin/activate
log_info "Installing dependencies..."
if [ -f "requirements.txt" ]; then
    uv pip install -r requirements.txt
fi

# --- Build & Deploy
log_info "Starting build on project: $PROJECT_ID"
gcloud builds submit . \
  --config cloudbuild.yaml \
  --project "$PROJECT_ID" \
  --service-account="projects/${PROJECT_ID}/serviceAccounts/cloud-build-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
  --gcs-source-staging-dir="gs://${PROJECT_ID}-cloudbuild-artifacts/source" \
  --substitutions=_TAG=$(date +%s)
  
log_success "Cloud Run deployments completed successfully."
