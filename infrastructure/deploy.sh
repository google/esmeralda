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

    # Special check for gke-gcloud-auth-plugin
    if ! command -v gke-gcloud-auth-plugin &> /dev/null; then
        log_error "gke-gcloud-auth-plugin not found. Please install it using: sudo apt-get install google-cloud-sdk-gke-gcloud-auth-plugin"
    fi
}

# Finds the root context file (.env) reliably using git
find_context() {
    echo "$(git rev-parse --show-toplevel)/.env"
}

load_context() {
    local env_file=$(find_context)
    if [[ -n "$env_file" ]]; then
        log_info "Loading context from $env_file"
        export $(grep -v '^#' "$env_file" | xargs)
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

# --- Load Config
load_context

# --- Auto-Install Missing critical binaries ---
if ! command -v kubectl &> /dev/null; then
    log_info "kubectl not found. Auto-installing to ~/.local/bin..."
    OS="$(uname | tr '[:upper:]' '[:lower:]')"
    ARCH="$(uname -m)"
    if [ "$ARCH" = "x86_64" ]; then ARCH="amd64"; fi
    if [ "$ARCH" = "aarch64" ]; then ARCH="arm64"; fi
    curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/${OS}/${ARCH}/kubectl"
    chmod +x kubectl
    mkdir -p "$HOME/.local/bin"
    mv kubectl "$HOME/.local/bin/"
fi

# --- Validate Dependencies
check_dependencies terraform gcloud kubectl envsubst

# --- Validate Input
ORG_ID=${ORG_ID}
BILLING_ACCOUNT=${BILLING_ACCOUNT}
PROJECT_ID_BASE=${PROJECT_ID_BASE:-agent-ops-foundation}
AGENT_NAME=${AGENT_NAME:-base-adk-agent}
REGION=${REGION:-us-central1}

if [[ -z "$ORG_ID" ]] || [[ -z "$BILLING_ACCOUNT" ]]; then
    log_error "ORG_ID and BILLING_ACCOUNT must be set in .env or as environment variables."
fi

# --- Run terraform
cd terraform

log_info "Creating terraform.tfvars file..."
cat > terraform.tfvars << EOL
org_id                    = "$ORG_ID"
billing_account           = "$BILLING_ACCOUNT"
project_id                = "$PROJECT_ID_BASE"
agent_name                = "$AGENT_NAME"
region                    = "$REGION"
enable_trace_logging_link = ${ENABLE_TRACE_LOGGING_LINK:-false}
enable_iam_user           = ${ENABLE_IAM_USER:-false}
EOL

log_info "Initializing Terraform..."
terraform init

log_info "Importing existing resources (safe re-run)..."
ACTUAL_PROJECT=$(terraform output -raw project_id 2>/dev/null || echo "")
if [[ -n "$ACTUAL_PROJECT" ]]; then
    RANDOM_SUFFIX=$(terraform output -raw random_suffix 2>/dev/null || echo "")
    if [[ -n "$RANDOM_SUFFIX" ]]; then
        terraform import "module.apihub.google_apihub_api_hub_instance.main" \
          "projects/${ACTUAL_PROJECT}/locations/${REGION}/apiHubInstances/default-instance-${RANDOM_SUFFIX}" 2>/dev/null || true
    fi
fi

log_info "Applying Terraform..."
terraform apply -auto-approve

# --- Update Context
log_info "Updating deployment context..."
PROJECT_ID=$(terraform output -raw project_id 2>/dev/null || echo "")
GCS_BUCKET=$(terraform output -raw agent_logs_offload_bucket_name 2>/dev/null || echo "")
REGION=$(terraform output -raw region 2>/dev/null || echo "")
PSC_NETWORK_ATTACHMENT=$(terraform output -raw psc_interface_network_attachment_id 2>/dev/null || echo "")
cd ..

set_context "PROJECT_ID" "$PROJECT_ID"
set_context "GCS_OFFLOAD_BUCKET_NAME" "$GCS_BUCKET"
set_context "REGION" "$REGION"
if [ -n "$PSC_NETWORK_ATTACHMENT" ]; then
    set_context "PSC_NETWORK_ATTACHMENT" "$PSC_NETWORK_ATTACHMENT"
fi

log_success "Terraform deployment complete and context updated."

# --- Gateway removed - skipping K8s deployment ---
log_info "K8s gateway deployment skipped as the architecture now uses ILB."
