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

# --- Local Utilities (Zero Dependefncy)
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

# --- Load Configuration
load_context

# --- Validate Dependencies
check_dependencies gcloud python3

# --- Create Virtual Environment using UV
log_info "Creating virtual environment using uv..."
if ! command -v uv &> /dev/null; then
    log_info "uv not found, installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

if [ ! -d ".venv" ]; then
    uv venv .venv
fi
source .venv/bin/activate
log_info "Installing dependencies from deployment/requirements.txt..."
uv pip install -r deployment/requirements.txt

# --- Auto-Discovery
if [ -z "$PROJECT_ID" ]; then
    log_info "Attempting to discover PROJECT_ID..."
    PROJECT_ID=$(gcloud config get-value project 2>/dev/null || echo "")
fi

if [ -z "$GCS_OFFLOAD_BUCKET_NAME" ] && [ -n "$PROJECT_ID" ]; then
    log_info "Attempting to discover GCS Offload Bucket..."
    GCS_OFFLOAD_BUCKET_NAME=$(gcloud storage buckets list --project "$PROJECT_ID" --format="value(name)" 2>/dev/null | grep "agent-logs-offload" | head -n 1 || echo "")
    [[ -n "$GCS_OFFLOAD_BUCKET_NAME" ]] && echo "[*] Discovered GCS Bucket: $GCS_OFFLOAD_BUCKET_NAME"
fi

if [ -z "$PSC_NETWORK_ATTACHMENT" ] && [ -n "$PROJECT_ID" ]; then
    log_info "Attempting to discover PSC Network Attachment..."
    PSC_NETWORK_ATTACHMENT=$(gcloud compute network-attachments list --project "$PROJECT_ID" --region "${REGION:-us-central1}" --format="value(name)" 2>/dev/null | grep "psc-interface-attachment" | head -n 1 || echo "")
    if [ -n "$PSC_NETWORK_ATTACHMENT" ]; then
        PSC_NETWORK_ATTACHMENT="projects/${PROJECT_ID}/regions/${REGION:-us-central1}/networkAttachments/${PSC_NETWORK_ATTACHMENT}"
        echo "[*] Discovered PSC Network Attachment: $PSC_NETWORK_ATTACHMENT"
    fi
fi

if [ -n "$PSC_NETWORK_ATTACHMENT" ]; then
    log_success "Using PSC Network Attachment: $PSC_NETWORK_ATTACHMENT"
else
    log_info "No PSC Network Attachment found. The agent will be deployed without PSC."
fi

if [ -z "$VPC_NAME" ]; then
    VPC_NAME=$(terraform -chdir=../infrastructure/terraform output -raw vpc_name 2>/dev/null || echo "gateway-vpc")
fi

# --- Final Validation
[[ -z "$PROJECT_ID" ]] && log_error "PROJECT_ID not set or discovered."
[[ -z "$GCS_OFFLOAD_BUCKET_NAME" ]] && log_error "GCS_OFFLOAD_BUCKET_NAME not set or discovered."

# --- Main Deployment Loop
if [ -n "$1" ]; then
    log_info "Target agent specified: $1"
    if [ ! -f "$1/agent.yaml" ]; then
        log_error "Agent folder '$1' does not exist or missing agent.yaml"
    fi
    AGENTS="$1"
else
    log_info "Discovering agents to deploy..."
    AGENTS=$(find . -mindepth 2 -maxdepth 2 -name "agent.yaml" | xargs -n1 dirname | sed 's|^\./||')
    if [ -z "$AGENTS" ]; then
        log_error "No agents found! Ensure your agent directories contain an agent.yaml file."
    fi
fi

for AGENT_DIR in $AGENTS; do
    AGENT_NAME=$(grep "^name:" "$AGENT_DIR/agent.yaml" | cut -d':' -f2 | xargs | tr -d '"' | tr -d "'")
    log_info "Preparing deployment for: $AGENT_NAME ($AGENT_DIR)"

    # --- Update agent.yaml ---
    if [ -n "$GCS_OFFLOAD_BUCKET_NAME" ]; then
        sed -i "s|GCS_BUCKET:.*|GCS_BUCKET: $GCS_OFFLOAD_BUCKET_NAME|g" "$AGENT_DIR/agent.yaml"
    fi
    
    # Create .gcloudignore
    cat <<EOF > "$AGENT_DIR/.gcloudignore"
.gcloudignore
.git
.gitignore
.venv
.env
__pycache__
*.pyc
EOF

    # Deploy
    DEPLOY_CMD=(
        "uv" "run" "--active" "../deployment/deploy_agent.py"
        "--project_id=${PROJECT_ID}"
        "--location=${REGION:-us-central1}"
        "--config-file=agent.yaml"
        "--source-packages=."
        "--requirements-file=requirements.txt"
    )

    if [ -n "$PSC_NETWORK_ATTACHMENT" ]; then
        DEPLOY_CMD+=("--network-attachment=${PSC_NETWORK_ATTACHMENT}")
        DEPLOY_CMD+=("--dns-peering-domain=gateway")
        DEPLOY_CMD+=("--dns-peering-target-network=${VPC_NAME}")
    fi

    # Use the dedicated service account instead of Agent Identity so OIDC tokens can be generated
    DEPLOY_CMD+=("--service-account=test-vm-sa@${PROJECT_ID}.iam.gserviceaccount.com")

    (
        export PYTHONPATH="$(pwd)/$AGENT_DIR:$PYTHONPATH"
        cd "$AGENT_DIR"
        "${DEPLOY_CMD[@]}"
    )
    log_success "$AGENT_NAME deployed successfully."
done
