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
#
# Promote dev images to a release: copy each image BY DIGEST from the shared dev repository
# into the immutable release repository as <tag>, then pin prd/env.yaml to <tag>.
# Never deploys. Repositories come from the shared layer-0 Terragrunt outputs.
#
#   promote_release.sh status
#   promote_release.sh promote --tag vX.Y.Z [--source-tag dev-latest|dev-<sha>]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PRD_ENV_YAML="${REPO_ROOT}/infrastructure/live/prd/env.yaml"
CICD_DIR="${REPO_ROOT}/infrastructure/live/shared/layer-0-cicd"

SOURCE_TAG="dev-latest"
TARGET_TAG=""
ACTION="promote"

IMAGES=(
  "kong-gateway"
  "legacy-dms"
  "income-verification-api"
  "corporate-email"
  "ai-coe-mortgage-specialist"
  "cx-mortgage-orchestrator"
)

while [[ $# -gt 0 ]]; do
  case $1 in
    status|promote) ACTION="$1"; shift ;;
    --tag) TARGET_TAG="$2"; shift 2 ;;
    --tag=*) TARGET_TAG="${1#*=}"; shift ;;
    --source-tag) SOURCE_TAG="$2"; shift 2 ;;
    --source-tag=*) SOURCE_TAG="${1#*=}"; shift ;;
    -h|--help)
      echo "Usage: $0 [status|promote] --tag <vX.Y.Z> [--source-tag <tag>]"
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

CICD_JSON="$(cd "${CICD_DIR}" && terragrunt output -json)"
DEV_REPO="$(jq -r .dev_repository_url.value <<<"${CICD_JSON}")"
RELEASE_REPO="$(jq -r .release_repository_url.value <<<"${CICD_JSON}")"

CURRENT_PRD_TAG="$(sed -nE 's/^[[:space:]]*container_tag[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "${PRD_ENV_YAML}" | head -1)"

if [[ "${ACTION}" == "status" ]]; then
  echo "========================================================================"
  echo "👑 ESMERALDA RELEASE STATUS"
  echo "========================================================================"
  echo "• PRD pinned tag     : ${CURRENT_PRD_TAG:-<none>}"
  echo "• Dev repository     : ${DEV_REPO}  (mutable, every build)"
  echo "• Release repository : ${RELEASE_REPO}  (immutable, promoted by digest)"
  echo "------------------------------------------------------------------------"
  echo "Promote: make promote TAG=vX.Y.Z [SOURCE_TAG=dev-<sha>]"
  echo "========================================================================"
  exit 0
fi

if [[ ! "${TARGET_TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]]; then
  echo "❌ --tag must look like vX.Y.Z (got '${TARGET_TAG}')" >&2
  exit 1
fi

echo "========================================================================"
echo "🚀 PROMOTING ${SOURCE_TAG} -> ${TARGET_TAG} (copy by digest)"
echo "   from ${DEV_REPO}"
echo "   to   ${RELEASE_REPO}"
echo "========================================================================"

# 1. Resolve every source digest and refuse existing release tags BEFORE copying anything,
#    so a promotion is all-or-nothing.
declare -A DIGESTS
for IMG in "${IMAGES[@]}"; do
  DIGEST="$(gcloud artifacts docker images describe "${DEV_REPO}/${IMG}:${SOURCE_TAG}" --format='value(image_summary.digest)')"
  [[ -n "${DIGEST}" ]] || { echo "❌ ${IMG}:${SOURCE_TAG} not found in the dev repository" >&2; exit 1; }
  DIGESTS["${IMG}"]="${DIGEST}"
  if gcloud artifacts docker images describe "${RELEASE_REPO}/${IMG}:${TARGET_TAG}" --format='value(image_summary.digest)' >/dev/null 2>&1; then
    echo "❌ ${IMG}:${TARGET_TAG} already exists in the release repository (tags are immutable: bump the version)" >&2
    exit 1
  fi
  echo "🔎 ${IMG}:${SOURCE_TAG} = ${DIGEST}"
done

# 2. Copy by digest (the release image is byte-identical to what was tested in dev).
for IMG in "${IMAGES[@]}"; do
  echo "📦 ${IMG}@${DIGESTS[${IMG}]} -> ${IMG}:${TARGET_TAG}"
  gcloud container images add-tag --quiet \
    "${DEV_REPO}/${IMG}@${DIGESTS[${IMG}]}" \
    "${RELEASE_REPO}/${IMG}:${TARGET_TAG}"
done

# 3. Pin prd to the new release.
sed -i -E "s/^([[:space:]]*container_tag[[:space:]]*=[[:space:]]*)\"[^\"]+\"/\1\"${TARGET_TAG}\"/" "${PRD_ENV_YAML}"
echo "✅ ${PRD_ENV_YAML}: container_tag = \"${TARGET_TAG}\" (was \"${CURRENT_PRD_TAG}\")"

echo ""
echo "🎉 Promoted ${TARGET_TAG}. Nothing was deployed. To roll prd forward:"
echo "   make deploy-workloads ENV=prd"
echo "========================================================================"
