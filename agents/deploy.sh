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

# --- Setup Deployment Mode
DEPLOY_MODE="${DEPLOY_MODE:-python}"
log_info "Using deployment mode: $DEPLOY_MODE"

# --- Cloud Build Delegation Check ---
if [[ "${USE_CLOUDBUILD}" == "true" ]]; then
    log_info "Delegating deployment to Cloud Build in mode: ${DEPLOY_MODE}"
    
    CLOUDBUILD_CONFIG="infra/${DEPLOY_MODE}/cloudbuild.yaml"
    if [[ ! -f "$CLOUDBUILD_CONFIG" ]]; then
        log_error "Cloud Build configuration not found at $CLOUDBUILD_CONFIG"
    fi
    
    # Auto-discover variables before submitting if they aren't already set
    if [ -z "$PROJECT_ID" ]; then
        log_info "Attempting to discover PROJECT_ID..."
        PROJECT_ID=$(gcloud config get-value project 2>/dev/null || echo "")
    fi
    
    if [ -z "$GCS_OFFLOAD_BUCKET_NAME" ] && [ -n "$PROJECT_ID" ]; then
        log_info "Attempting to discover GCS Offload Bucket..."
        GCS_OFFLOAD_BUCKET_NAME=$(gcloud storage buckets list --project "$PROJECT_ID" --format="value(name)" 2>/dev/null | grep "agent-logs-offload" | head -n 1 || echo "")
        [[ -n "$GCS_OFFLOAD_BUCKET_NAME" ]] && echo "[*] Discovered GCS Bucket: $GCS_OFFLOAD_BUCKET_NAME"
    fi
    
    if [ -z "$VPC_NAME" ]; then
        VPC_NAME=$(terraform -chdir=../infrastructure/terraform output -raw vpc_name 2>/dev/null || echo "gateway-vpc")
    fi
    
    if [ -z "$CLOUD_SQL_INSTANCE" ]; then
        CLOUD_SQL_INSTANCE=$(terraform -chdir=../infrastructure/terraform output -raw cloud_sql_instance_connection_name 2>/dev/null || echo "")
        [[ -n "$CLOUD_SQL_INSTANCE" ]] && echo "[*] Discovered Cloud SQL Instance: $CLOUD_SQL_INSTANCE"
    fi
    
    [[ -z "$PROJECT_ID" ]] && log_error "PROJECT_ID not set or discovered."
    [[ -z "$GCS_OFFLOAD_BUCKET_NAME" ]] && log_error "GCS_OFFLOAD_BUCKET_NAME not set or discovered."
    
    SUBST_STR="_PROJECT_ID=${PROJECT_ID},_REGION=${REGION:-us-central1}"
    if [[ -n "${AGENT_FILTER}" ]]; then
        SUBST_STR="${SUBST_STR},_AGENT_FILTER=${AGENT_FILTER}"
    fi
    if [[ -n "${PSC_NETWORK_ATTACHMENT}" ]]; then
        SUBST_STR="${SUBST_STR},_PSC_NETWORK_ATTACHMENT=${PSC_NETWORK_ATTACHMENT}"
    fi
    if [[ -n "${VPC_NAME}" ]]; then
        SUBST_STR="${SUBST_STR},_VPC_NAME=${VPC_NAME}"
    fi
    if [[ -n "${CLOUD_SQL_INSTANCE}" ]]; then
        SUBST_STR="${SUBST_STR},_CLOUD_SQL_INSTANCE=${CLOUD_SQL_INSTANCE}"
    fi
    if [[ -n "${GCS_OFFLOAD_BUCKET_NAME}" ]]; then
        SUBST_STR="${SUBST_STR},_GCS_OFFLOAD_BUCKET_NAME=${GCS_OFFLOAD_BUCKET_NAME}"
    fi
    
    log_info "Submitting Cloud Build with substitutions: $SUBST_STR"
    gcloud builds submit . --config="$CLOUDBUILD_CONFIG" --substitutions="$SUBST_STR"
    log_success "Cloud Build deployment completed."
    exit 0
fi

# --- Validate Dependencies
if [[ "$DEPLOY_MODE" == "terraform" ]]; then
    check_dependencies gcloud python3 terraform
else
    check_dependencies gcloud python3
fi

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
log_info "Installing dependencies from infra/requirements.txt..."
uv pip install -r infra/requirements.txt

log_info "Installing all agent dependencies..."
AGENT_REQS=""
for REQ in $(find . -mindepth 2 -maxdepth 3 -name "requirements.txt"); do
    AGENT_REQS="$AGENT_REQS -r $REQ"
done
uv pip install $AGENT_REQS

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

if [ -n "$PSC_NETWORK_ATTACHMENT" ]; then
    log_success "Using PSC Network Attachment from context: $PSC_NETWORK_ATTACHMENT"
else
    log_info "No PSC Network Attachment found in context. The agent will be deployed without PSC."
fi

if [ -z "$VPC_NAME" ]; then
    VPC_NAME=$(terraform -chdir=../infrastructure/terraform output -raw vpc_name 2>/dev/null || echo "gateway-vpc")
fi

if [ -z "$CLOUD_SQL_INSTANCE" ]; then
    CLOUD_SQL_INSTANCE=$(terraform -chdir=../infrastructure/terraform output -raw cloud_sql_instance_connection_name 2>/dev/null || echo "")
    [[ -n "$CLOUD_SQL_INSTANCE" ]] && echo "[*] Discovered Cloud SQL Instance: $CLOUD_SQL_INSTANCE"
fi

# --- Final Validation
[[ -z "$PROJECT_ID" ]] && log_error "PROJECT_ID not set or discovered."
[[ -z "$GCS_OFFLOAD_BUCKET_NAME" ]] && log_error "GCS_OFFLOAD_BUCKET_NAME not set or discovered."

# --- Main Deployment Loop
log_info "Discovering agents to deploy..."
DISCOVERED_AGENTS=$(find . -mindepth 2 -maxdepth 3 -name "agent.yaml" | xargs -n1 dirname | sed 's|^\./||' | sort)

if [ -z "$DISCOVERED_AGENTS" ]; then
    log_error "No agents found! Ensure your agent directories contain an agent.yaml file."
fi

# Ensure a2a-agent is ALWAYS at the beginning of the deployment list to resolve dependencies first
AGENTS=""
if echo "$DISCOVERED_AGENTS" | grep -q "remotes/a2a-agent"; then
    AGENTS="remotes/a2a-agent"
fi
for AGENT in $DISCOVERED_AGENTS; do
    if [[ "$AGENT" != "remotes/a2a-agent" ]]; then
        AGENTS="$AGENTS $AGENT"
    fi
done
AGENTS=$(echo "$AGENTS" | xargs)

for AGENT_DIR in $AGENTS; do
    if [ -n "$AGENT_FILTER" ] && [[ "$AGENT_DIR" != *"$AGENT_FILTER"* ]]; then
        log_info "Skipping $AGENT_DIR (does not match AGENT_FILTER=$AGENT_FILTER)"
        continue
    fi
    AGENT_NAME=$(grep "^name:" "$AGENT_DIR/agent.yaml" | cut -d':' -f2 | xargs | tr -d '"' | tr -d "'")
    log_info "Preparing deployment for: $AGENT_NAME ($AGENT_DIR)"

    # --- Update agent.yaml ---
    if [ -n "$CLOUD_SQL_INSTANCE" ]; then
        sed -i "s|CLOUD_SQL_INSTANCE:.*|CLOUD_SQL_INSTANCE: ${CLOUD_SQL_INSTANCE}|g" "$AGENT_DIR/agent.yaml"
        DB_IAM_USER="test-vm-sa@${PROJECT_ID}.iam"
        sed -i "s|DB_IAM_USER:.*|DB_IAM_USER: ${DB_IAM_USER}|g" "$AGENT_DIR/agent.yaml"
    fi
    if [ -n "$GCS_OFFLOAD_BUCKET_NAME" ]; then
        sed -i "s|GCS_BUCKET:.*|GCS_BUCKET: $GCS_OFFLOAD_BUCKET_NAME|g" "$AGENT_DIR/agent.yaml"
    fi

    # --- Auto-resolve A2A Agent URL if deploying base-adk-agent ---
    if [[ "$AGENT_DIR" == "root_agents/base-adk-agent" ]]; then
        log_info "Attempting to auto-resolve latest A2A Agent URL from Vertex AI..."
        RESOLVED_A2A_URL=$(python3 -c "
import os
try:
    import vertexai
    from vertexai import agent_engines
    project = '${PROJECT_ID}'
    location = '${REGION:-us-central1}'
    vertexai.init(project=project, location=location)
    engines = agent_engines.list()
    engines = sorted([e for e in engines if e.display_name == 'a2a-mortgage-agent'], key=lambda e: e.create_time, reverse=True)
    if engines:
        print(f'https://{location}-aiplatform.googleapis.com/v1beta1/projects/{project}/locations/{location}/reasoningEngines/{engines[0].name}/a2a')
except Exception as e:
    pass
" 2>/dev/null)

        if [[ -n "$RESOLVED_A2A_URL" ]]; then
            log_success "Auto-resolved deployed A2A Agent URL: $RESOLVED_A2A_URL"
            sed -i "s|A2A_AGENT_URL:.*|A2A_AGENT_URL: ${RESOLVED_A2A_URL}|g" "$AGENT_DIR/agent.yaml"
            log_info "Updated A2A_AGENT_URL in $AGENT_DIR/agent.yaml"
        else
            log_info "Could not auto-resolve A2A Agent URL from Vertex AI. Will use existing configuration if present."
        fi
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
    if [[ "$DEPLOY_MODE" == "terraform" ]]; then
        log_info "Packaging agent '$AGENT_NAME' for Terraform..."
        mkdir -p "$AGENT_DIR/dist"
        
        # Package agent into serialized pickle
        uv run --active --no-sync python3 infra/terraform/package_agent.py \
            --agent-dir="$AGENT_DIR" \
            --output-dir="$AGENT_DIR/dist"
            
        log_info "Applying Terraform configuration for '$AGENT_NAME'..."
        mkdir -p "infra/terraform/states"
        
        # Deploy with isolated state file per agent
        DEPLOY_OUTPUT=$(
            cd "infra/terraform"
            terraform init -reconfigure >/dev/null
            terraform apply -auto-approve \
                -state="states/${AGENT_NAME}.tfstate" \
                -var="project_id=${PROJECT_ID}" \
                -var="region=${REGION:-us-central1}" \
                -var="staging_bucket_name=${GCS_OFFLOAD_BUCKET_NAME}" \
                -var="agent_name=${AGENT_NAME}" \
                -var="service_account=test-vm-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
                -var="pickle_object_path=../../${AGENT_DIR}/dist/agent.pkl" \
                -var="requirements_path=../../${AGENT_DIR}/dist/requirements.txt" \
                -var="dependencies_path=../../${AGENT_DIR}/dist/dependencies.tar.gz" \
                -var="network_attachment=${PSC_NETWORK_ATTACHMENT:-}" 2>&1
        ) || { echo "$DEPLOY_OUTPUT"; log_error "Terraform deployment of $AGENT_NAME failed."; }
        echo "$DEPLOY_OUTPUT"
        log_success "$AGENT_NAME deployed successfully via Terraform."
    else
        # Standard Python SDK deployer
        DEPLOY_CMD=(
            "uv" "run" "--active" "--no-sync" "../../infra/python/deploy_agent.py"
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

        DEPLOY_CMD+=("--service-account=test-vm-sa@${PROJECT_ID}.iam.gserviceaccount.com")

        DEPLOY_OUTPUT=$(
            export PYTHONPATH="$(pwd)/$AGENT_DIR:$PYTHONPATH"
            cd "$AGENT_DIR"
            "${DEPLOY_CMD[@]}" 2>&1
        ) || { echo "$DEPLOY_OUTPUT"; log_error "Deployment of $AGENT_NAME failed."; }
        echo "$DEPLOY_OUTPUT"
        log_success "$AGENT_NAME deployed successfully."
    fi

    # After A2A agent deploys, capture its Engine URL for base-adk-agent
    if [[ "$AGENT_DIR" == "remotes/a2a-agent" ]]; then
        ENGINE_RESOURCE=$(echo "$DEPLOY_OUTPUT" | grep -o "projects/[^'\"]*reasoningEngines/[0-9]*" | tail -1)
        if [[ -n "$ENGINE_RESOURCE" ]]; then
            LOCATION="${REGION:-us-central1}"
            A2A_URL="https://${LOCATION}-aiplatform.googleapis.com/v1beta1/${ENGINE_RESOURCE}/a2a"
            log_info "A2A agent deployed at: $A2A_URL"
            if [[ -f "root_agents/base-adk-agent/agent.yaml" ]]; then
                sed -i "s|A2A_AGENT_URL:.*|A2A_AGENT_URL: ${A2A_URL}|g" "root_agents/base-adk-agent/agent.yaml"
                log_info "Updated A2A_AGENT_URL in root_agents/base-adk-agent/agent.yaml"
            fi
        fi
    fi

    # After base-adk-agent deploys, capture its Engine resource and update REMOTE_AGENT_ENGINE_ID in root .env
    if [[ "$AGENT_DIR" == "root_agents/base-adk-agent" ]]; then
        ENGINE_RESOURCE=$(echo "$DEPLOY_OUTPUT" | grep -o "projects/[^'\"]*reasoningEngines/[0-9]*" | tail -1)
        if [[ -n "$ENGINE_RESOURCE" ]]; then
            ENV_FILE=$(find_context)
            if [[ -f "$ENV_FILE" ]]; then
                # Support both macOS and Linux sed syntaxes safely by removing first, then appending
                sed -i '/^REMOTE_AGENT_ENGINE_ID=/d' "$ENV_FILE"
                echo "REMOTE_AGENT_ENGINE_ID=\"$ENGINE_RESOURCE\"" >> "$ENV_FILE"
                log_success "Updated REMOTE_AGENT_ENGINE_ID in root .env context: $ENGINE_RESOURCE"
            fi
        fi
    fi
done
