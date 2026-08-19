#!/usr/bin/env bash
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

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PRD_ENV_YAML="${REPO_ROOT}/infrastructure/live/prd/env.yaml"

CICD_PROJECT="esmeralda-cicd-artifacts-3a3d"
REGION="us-central1"
SOURCE_TAG="dev-latest"
TARGET_TAG=""
ACTION="promote"

SERVICES=(
  "kong-gateway"
  "legacy-dms"
  "income-verification-api"
  "corporate-email"
  "a2a-agent"
  "root-agent"
)

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    status)
      ACTION="status"
      shift
      ;;
    promote)
      ACTION="promote"
      shift
      ;;
    --tag)
      TARGET_TAG="$2"
      shift 2
      ;;
    --tag=*)
      TARGET_TAG="${1#*=}"
      shift
      ;;
    --source-tag)
      SOURCE_TAG="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [status|promote] [--tag <vX.Y.Z>] [--source-tag <tag>]"
      exit 0
      ;;
    *)
      if [[ -z "${TARGET_TAG}" && "$1" =~ ^v[0-9] ]]; then
        TARGET_TAG="$1"
      fi
      shift
      ;;
  esac
done

# Read current PRD tag from prd/env.yaml
CURRENT_PRD_TAG="v1.0.0"
if [[ -f "${PRD_ENV_YAML}" ]]; then
  EXTRACTED_TAG=$(grep -E 'container_tag\s*:' "${PRD_ENV_YAML}" | sed -E 's/.*"([^"]+)".*/\1/' || true)
  if [[ -n "${EXTRACTED_TAG}" ]]; then
    CURRENT_PRD_TAG="${EXTRACTED_TAG}"
  fi
fi

if [[ "${ACTION}" == "status" ]]; then
  echo "========================================================================"
  echo "👑 ESMERALDA MULTI-ENVIRONMENT RELEASE & PROMOTION STATUS (Shell)"
  echo "========================================================================"
  echo "• Active PRD Pinned Tag    : ${CURRENT_PRD_TAG}"
  echo "• Shared Artifact Registry : ${REGION}-docker.pkg.dev/${CICD_PROJECT}/esmeralda-containers"
  echo "------------------------------------------------------------------------"
  echo "Commands to Promote Release:"
  echo "  ./scripts/promote_release.sh promote --tag v1.0.1"
  echo "  ./scripts/promote_release.sh promote --tag v1.1.0"
  echo "========================================================================"
  exit 0
fi

# Determine Target Tag
if [[ -z "${TARGET_TAG}" ]]; then
  TARGET_TAG="${CURRENT_PRD_TAG}"
fi

echo "========================================================================"
echo "🚀 PROMOTING ESMERALDA WORKLOADS TO PRODUCTION"
echo "========================================================================"
echo "• Target Tag        : ${TARGET_TAG}"
echo "• Source Build Tag  : ${SOURCE_TAG}"
echo "• Registry          : ${REGION}-docker.pkg.dev/${CICD_PROJECT}/esmeralda-containers"
echo "========================================================================"

REGISTRY_BASE="${REGION}-docker.pkg.dev/${CICD_PROJECT}/esmeralda-containers"

for SVC in "${SERVICES[@]}"; do
  echo "📦 Tagging ${SVC}:${SOURCE_TAG} -> ${SVC}:${TARGET_TAG}..."
  gcloud artifacts docker tags add \
    "${REGISTRY_BASE}/${SVC}:${SOURCE_TAG}" \
    "${REGISTRY_BASE}/${SVC}:${TARGET_TAG}" \
    --quiet
done

# Update container_tag in prd/env.yaml
if [[ -f "${PRD_ENV_YAML}" ]]; then
  if grep -q "container_tag:" "${PRD_ENV_YAML}"; then
    sed -i -E "s/(container_tag\s*:\s*)\"[^\"]+\"/\1\"${TARGET_TAG}\"/" "${PRD_ENV_YAML}"
  else
    # Insert container_tag before monitoring config
    sed -i "s/  # 📊 OBSERVABILITY & MONITORING CONFIGURATION:/  container_tag       = \"${TARGET_TAG}\"\n\n  # 📊 OBSERVABILITY & MONITORING CONFIGURATION:/" "${PRD_ENV_YAML}"
  fi
  echo "✅ Updated ${PRD_ENV_YAML} with container_tag: \"${TARGET_TAG}\""
fi

echo ""
echo "🎉 Promotion to ${TARGET_TAG} successfully completed!"
echo "👉 To apply workloads in production, run:"
echo "   make deploy-services ENV=prd"
echo "   make deploy-agents ENV=prd"
echo "   make deploy-gateway ENV=prd"
echo "========================================================================"
