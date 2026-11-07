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
# Promote a verified dev build to a release, by digest:
#   1. resolve the digest of every image tagged <source> (dev-<gitsha>) in the shared dev repository;
#   2. copy each digest into the immutable release repository as <version> (refuses if <version>
#      already exists there with a different digest; re-running the same promotion is a no-op);
#   3. write the release manifest release/<env>.yaml (image => digest, source commit, verify result).
# Never deploys and never edits env.yaml: merge the manifest by PR, then deploy prd.
# Repositories come from the shared layer-0 Terragrunt outputs.
#
#   promote_release.sh status
#   promote_release.sh promote --version vX.Y.Z --source dev-<sha> --verified YYYY-MM-DD [--env prd]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CICD_DIR="${REPO_ROOT}/infrastructure/live/shared/layer-0-cicd"
MANIFEST_TOOL=(python3 "${SCRIPT_DIR}/release_manifest.py")

ACTION="promote"
ENV_NAME="prd"
VERSION=""
SOURCE=""
VERIFIED=""

usage() {
  echo "Usage: $0 status [--env prd]"
  echo "       $0 promote --version vX.Y.Z --source dev-<sha> --verified YYYY-MM-DD [--env prd]"
}

while [[ $# -gt 0 ]]; do
  case $1 in
    status|promote) ACTION="$1"; shift ;;
    --version|--tag) VERSION="$2"; shift 2 ;;
    --version=*|--tag=*) VERSION="${1#*=}"; shift ;;
    --source|--source-tag) SOURCE="$2"; shift 2 ;;
    --source=*|--source-tag=*) SOURCE="${1#*=}"; shift ;;
    --verified) VERIFIED="$2"; shift 2 ;;
    --verified=*) VERIFIED="${1#*=}"; shift ;;
    --env) ENV_NAME="$2"; shift 2 ;;
    --env=*) ENV_NAME="${1#*=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

ENV_YAML="${REPO_ROOT}/infrastructure/live/${ENV_NAME}/env.yaml"
MANIFEST="${REPO_ROOT}/release/${ENV_NAME}.yaml"
[[ -f "${ENV_YAML}" ]] || { echo "❌ No ${ENV_YAML}" >&2; exit 1; }

# Every Layer 5 image built by `make build-images` (Envoy gateway + 3 MCP services + 2 agents).
IMAGES=(
  "envoy-gateway"
  "legacy-dms"
  "income-verification-api"
  "corporate-email"
  "ai-coe-mortgage-specialist"
  "cx-mortgage-orchestrator"
)

manifest_get() { "${MANIFEST_TOOL[@]}" get --manifest "${MANIFEST}" "$@" 2>/dev/null || true; }

CICD_JSON="$(cd "${CICD_DIR}" && terragrunt output -json)"
DEV_REPO="$(jq -r .dev_repository_url.value <<<"${CICD_JSON}")"
RELEASE_REPO="$(jq -r .release_repository_url.value <<<"${CICD_JSON}")"

if [[ "${ACTION}" == "status" ]]; then
  echo "========================================================================"
  echo "👑 ESMERALDA RELEASE STATUS (${ENV_NAME})"
  echo "========================================================================"
  if [[ -f "${MANIFEST}" ]]; then
    echo "• Manifest           : release/${ENV_NAME}.yaml"
    echo "• Version            : $(manifest_get --key version)"
    echo "• Source             : $(manifest_get --key source_tag) ($(manifest_get --key source_commit))"
    echo "• Promoted at        : $(manifest_get --key promoted_at)"
    echo "• Verified           : $(manifest_get --key verified.environment) $(manifest_get --key verified.date) $(manifest_get --key verified.result)"
    for IMG in "${IMAGES[@]}"; do
      printf '    %-28s %s\n' "${IMG}" "$(manifest_get --image "${IMG}")"
    done
  else
    echo "• Manifest           : <none> (units fall back to container_tag in ${ENV_NAME}/env.yaml)"
  fi
  echo "• Dev repository     : ${DEV_REPO}  (mutable, every build)"
  echo "• Release repository : ${RELEASE_REPO}  (immutable, promoted by digest)"
  echo "------------------------------------------------------------------------"
  echo "Promote: make promote VERSION=vX.Y.Z SOURCE=dev-<sha> VERIFIED=YYYY-MM-DD"
  echo "========================================================================"
  exit 0
fi

if [[ ! "${VERSION}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]]; then
  echo "❌ --version must look like vX.Y.Z (got '${VERSION}')" >&2
  exit 1
fi
if [[ ! "${SOURCE}" =~ ^dev-([0-9a-f]{7,40})$ ]]; then
  echo "❌ --source must be an immutable build tag dev-<gitsha> (got '${SOURCE}'); dev-latest moves on every build" >&2
  exit 1
fi
SOURCE_SHA="${BASH_REMATCH[1]}"
SOURCE_COMMIT="$(git -C "${REPO_ROOT}" rev-parse --verify --quiet "${SOURCE_SHA}^{commit}" 2>/dev/null || echo "${SOURCE_SHA}")"
VERIFIED="${VERIFIED:-$(date -u +%F)}"
if [[ ! "${VERIFIED}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "❌ --verified must be the date (YYYY-MM-DD) dev passed 'make verify ENV=dev' on ${SOURCE}" >&2
  exit 1
fi

echo "========================================================================"
echo "🚀 PROMOTING ${SOURCE} -> ${VERSION} (copy by digest, manifest release/${ENV_NAME}.yaml)"
echo "   from ${DEV_REPO}"
echo "   to   ${RELEASE_REPO}"
echo "========================================================================"

# 1. Resolve every source digest and check every release tag BEFORE copying anything,
#    so a promotion is all-or-nothing.
declare -A DIGESTS
declare -A ALREADY
for IMG in "${IMAGES[@]}"; do
  DIGEST="$(gcloud artifacts docker images describe "${DEV_REPO}/${IMG}:${SOURCE}" --format='value(image_summary.digest)' 2>/dev/null || true)"
  [[ "${DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "❌ ${IMG}:${SOURCE} not found in the dev repository" >&2; exit 1; }
  DIGESTS["${IMG}"]="${DIGEST}"
  EXISTING="$(gcloud artifacts docker images describe "${RELEASE_REPO}/${IMG}:${VERSION}" --format='value(image_summary.digest)' 2>/dev/null || true)"
  if [[ -n "${EXISTING}" && "${EXISTING}" != "${DIGEST}" ]]; then
    echo "❌ ${IMG}:${VERSION} already exists in the release repository as ${EXISTING}," >&2
    echo "   not ${DIGEST}. Release tags are immutable: bump the version." >&2
    exit 1
  fi
  [[ -n "${EXISTING}" ]] && ALREADY["${IMG}"]=1
  echo "🔎 ${IMG}:${SOURCE} = ${DIGEST}"
done

# 2. Copy by digest (the release image is byte-identical to what was tested in dev).
for IMG in "${IMAGES[@]}"; do
  if [[ -n "${ALREADY[${IMG}]:-}" ]]; then
    echo "✔️  ${IMG}:${VERSION} already points at ${DIGESTS[${IMG}]} (no copy)"
    continue
  fi
  echo "📦 ${IMG}@${DIGESTS[${IMG}]} -> ${IMG}:${VERSION}"
  gcloud container images add-tag --quiet \
    "${DEV_REPO}/${IMG}@${DIGESTS[${IMG}]}" \
    "${RELEASE_REPO}/${IMG}:${VERSION}"
  COPIED="$(gcloud artifacts docker images describe "${RELEASE_REPO}/${IMG}:${VERSION}" --format='value(image_summary.digest)')"
  [[ "${COPIED}" == "${DIGESTS[${IMG}]}" ]] || { echo "❌ ${IMG}:${VERSION} is ${COPIED} after the copy, expected ${DIGESTS[${IMG}]}" >&2; exit 1; }
done

# 3. Write the manifest (no registry URL or project id: units add the layer-0 release repository).
IMAGE_ARGS=()
for IMG in "${IMAGES[@]}"; do IMAGE_ARGS+=(--image "${IMG}=${DIGESTS[${IMG}]}"); done
"${MANIFEST_TOOL[@]}" write --out "${MANIFEST}" --version "${VERSION}" --source-tag "${SOURCE}" \
  --source-commit "${SOURCE_COMMIT}" --repository release --verified-env dev \
  --verified-date "${VERIFIED}" --verified-result PASS "${IMAGE_ARGS[@]}"

echo ""
echo "🎉 Promoted ${VERSION}. Nothing was deployed. Next:"
echo "   1. open a PR with release/${ENV_NAME}.yaml and merge it"
echo "   2. make deploy-workloads ENV=${ENV_NAME}   (then make verify-images ENV=${ENV_NAME})"
echo "   Roll back = revert that PR and deploy again."
echo "========================================================================"
