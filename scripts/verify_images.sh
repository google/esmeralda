#!/usr/bin/env bash
# Verifies that every Layer 5 workload is *running* the image it should.
#
#   expected = the digest listed in release/<env>.yaml (the release manifest) when that file
#              exists, otherwise the digest of <repository>/<image>:<container_tag> in Artifact Registry
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
# Gateways listed in `gateways = { enabled = [...] }` (env.yaml)
ENABLED_GATEWAYS="$(awk '/^  gateways[[:space:]]*=/{f=1} f && /enabled/{print; exit}' "${LIVE_DIR}/env.yaml")"

MANIFEST="release/${ENV_NAME}.yaml"
if [ -f "${MANIFEST}" ]; then
  RELEASE_VERSION="$(python3 scripts/release_manifest.py get --manifest "${MANIFEST}" --key version || true)"
  EXPECTED_DESC="${MANIFEST} (${RELEASE_VERSION:-unknown version})"
else
  EXPECTED_DESC="tag :${TAG}"
fi

echo "🔎 Verifying running images for ${ENV_NAME} (${EXPECTED_DESC})..."
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
  if [ -f "${MANIFEST}" ]; then
    # Every deployed image must be in the manifest; an unlisted one prints nothing and fails.
    python3 scripts/release_manifest.py get --manifest "${MANIFEST}" --image "$1" || true
  else
    gcloud artifacts docker images describe "${REPO_URL}/$1:${TAG}" --format='value(image_summary.digest)'
  fi
}

report() { # name expected running
  if [ -n "$2" ] && [ "$2" = "$3" ]; then
    printf '  ✅ %-28s %s\n' "$1" "${3:7:12}"
  else
    printf '  ❌ %-28s running %s, expected %s (%s)\n' "$1" "${3:-none}" "${2:-<not listed>}" "${EXPECTED_DESC}"
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
if [[ "${ENABLED_GATEWAYS}" == *'"kong"'* ]]; then
  check_cloud_run "kong-gateway-${ENV_NAME}" "${GATEWAY_PROJECT}" kong-gateway
fi
if [[ "${ENABLED_GATEWAYS}" == *'"envoy"'* ]]; then
  check_cloud_run "envoy-gateway-${ENV_NAME}" "${GATEWAY_PROJECT}" envoy-gateway
fi
check_engine "cx-mortgage-orchestrator"   "${ORCH_ENGINE}" cx-mortgage-orchestrator
check_engine "ai-coe-mortgage-specialist" "${SPEC_ENGINE}" ai-coe-mortgage-specialist

if [ "${failures}" -gt 0 ]; then
  echo "❌ ${failures} workload(s) are not running ${EXPECTED_DESC}. Re-run 'make deploy-workloads ENV=${ENV_NAME}'."
  exit 1
fi
echo "✅ All workloads run ${EXPECTED_DESC}."
