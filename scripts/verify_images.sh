#!/usr/bin/env bash
# Verifies that every Layer 5 workload is *running* the image its tag points to right now.
#
#   expected = digest of <repository>/<image>:<container_tag> in Artifact Registry
#   running  = digest of the Cloud Run revision(s) serving traffic, or of the Agent Runtime
#              (Reasoning Engine) container spec
#
# A mismatch means a rebuilt image was not rolled out (or a workload was changed by hand).
# Usage: scripts/verify_images.sh <env>     (run from the repo root; `make verify-images ENV=<env>`)
set -euo pipefail

ENV_NAME="${1:?usage: $0 <env>}"
LIVE_DIR="infrastructure/live/${ENV_NAME}"
CICD_DIR="infrastructure/live/shared/layer-0-cicd"

env_local() { awk -F'"' -v k="$1" '$0 ~ "^  "k"[[:space:]]*=" {print $2; exit}' "${LIVE_DIR}/env.yaml"; }
tg_output() { (cd "$1" && terragrunt output -raw "$2" 2>/dev/null); }

REGION="$(env_local region)"
TAG="$(env_local container_tag)"
REPO_KIND="$(env_local image_repository)"
GATEWAY_PRODUCT="$(env_local gateway_product)"

echo "🔎 Verifying running images for ${ENV_NAME} (tag :${TAG})..."
if [ "${REPO_KIND:-dev}" = "release" ]; then
  REPO_URL="$(tg_output "${CICD_DIR}" release_repository_url)"
else
  REPO_URL="$(tg_output "${CICD_DIR}" dev_repository_url)"
fi
MCPS_PROJECT="$(tg_output "${LIVE_DIR}/layer-1-projects" mcps_project_id)"
GATEWAY_PROJECT="$(tg_output "${LIVE_DIR}/layer-1-projects" gateway_project_id)"
ORCH_ENGINE="$(tg_output "${LIVE_DIR}/layer-5-workloads/agents/cx-mortgage-orchestrator" engine_id)"
SPEC_ENGINE="$(tg_output "${LIVE_DIR}/layer-5-workloads/agents/ai-coe-mortgage-specialist" engine_id)"
TOKEN="$(gcloud auth print-access-token)"

failures=0

expected_digest() {
  gcloud artifacts docker images describe "${REPO_URL}/$1:${TAG}" --format='value(image_summary.digest)'
}

report() { # name expected running
  if [ "$2" = "$3" ]; then
    printf '  ✅ %-28s %s\n' "$1" "${3:7:12}"
  else
    printf '  ❌ %-28s running %s, expected %s (:%s)\n' "$1" "${3:-none}" "$2" "${TAG}"
    failures=$((failures + 1))
  fi
}

check_cloud_run() { # service project image
  local expected revisions running="" rev digest
  expected="$(expected_digest "$3")"
  # Every revision that currently receives traffic must run the expected digest.
  revisions="$(gcloud run services describe "$1" --project="$2" --region="${REGION}" \
    --format='value(status.traffic.filter(percent>0).revisionName)' 2>/dev/null | tr ';,' '  ')" || true
  if [ -z "${revisions// /}" ]; then
    revisions="$(gcloud run services describe "$1" --project="$2" --region="${REGION}" \
      --format='value(status.latestReadyRevisionName)' 2>/dev/null)" || true
  fi
  for rev in ${revisions}; do
    digest="$(gcloud run revisions describe "${rev}" --project="$2" --region="${REGION}" \
      --format='value(status.imageDigest)')"
    digest="${digest##*@}"
    if [ -n "${running}" ] && [ "${running}" != "${digest}" ]; then running="mixed"; else running="${digest}"; fi
  done
  report "$1" "${expected}" "${running}"
}

check_engine() { # label engine_name image
  local expected uri
  expected="$(expected_digest "$3")"
  uri="$(curl -fsS -H "Authorization: Bearer ${TOKEN}" \
    "https://${REGION}-aiplatform.googleapis.com/v1beta1/$2" | jq -r '.spec.containerSpec.imageUri // ""')"
  report "$1" "${expected}" "$( [[ "${uri}" == *@* ]] && echo "${uri##*@}" )"
}

check_cloud_run "corporate-email-${ENV_NAME}"     "${MCPS_PROJECT}" corporate-email
check_cloud_run "income-verification-${ENV_NAME}" "${MCPS_PROJECT}" income-verification-api
check_cloud_run "legacy-dms-${ENV_NAME}"          "${MCPS_PROJECT}" legacy-dms
if [ "${GATEWAY_PRODUCT}" = "kong" ]; then
  check_cloud_run "kong-gateway-${ENV_NAME}" "${GATEWAY_PROJECT}" kong-gateway
fi
check_engine "cx-mortgage-orchestrator"   "${ORCH_ENGINE}" cx-mortgage-orchestrator
check_engine "ai-coe-mortgage-specialist" "${SPEC_ENGINE}" ai-coe-mortgage-specialist

if [ "${failures}" -gt 0 ]; then
  echo "❌ ${failures} workload(s) are not running :${TAG}. Re-run 'make deploy-workloads ENV=${ENV_NAME}'."
  exit 1
fi
echo "✅ All workloads run the images tagged :${TAG}."
