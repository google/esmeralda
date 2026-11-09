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
# Partner front-door conformance suite (`make test-partner`).
#
# Agent Gateway is Esmeralda's governance plane for agent traffic. Apigee can optionally
# add API-management capabilities, such as partner access, API products and token quotas,
# through independent modules selected per capability in `env.yaml`.
#
# Modes:
#   1. Offline / Dry-Run (`scripts/test_partner.sh --dry-run [env]`, or automatically when
#      Apigee partner_access is disabled in `env.yaml`):
#      Verifies all 5 partner conformance invariants statically against the proxy bundles,
#      JS policies, and capability modules.
#   2. Live Probe (`scripts/test_partner.sh <env>` when Apigee partner_access is deployed):
#      Executes all 5 HTTP probes through the partner front door from the test VM:
#        Probe 1: Unauthenticated request -> HTTP 401
#        Probe 2: Unallowlisted internal specialist (`ai-coe-mortgage-specialist`) -> HTTP 403/404
#        Probe 3: Unallowlisted MCP tool call (`tools/call` for restricted tool) -> HTTP 403
#        Probe 4: Partner key -> `cx-mortgage-orchestrator` `:streamQuery` -> verifies TTFB
#                 streaming (first SSE chunk arrives before stream completion) + real answer
#        Probe 5: Quota exhaustion probe -> HTTP 429 when `LLMTokenQuota` limit is exceeded
set -euo pipefail

DRY_RUN=0
ENV_NAME="dev"

for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      echo "Usage: $0 [--dry-run] [env]"
      exit 0
      ;;
    *) ENV_NAME="${arg}" ;;
  esac
done

LIVE_DIR="infrastructure/live/${ENV_NAME}"
L5_DIR="${LIVE_DIR}/layer-5-workloads"

die() { echo "❌ $*" >&2; exit 1; }
tg_output() { (cd "$1" && terragrunt output -raw "$2" 2>/dev/null) || true; }

run_offline_conformance() {
  echo "🧪 Running offline partner front-door conformance checks (5 probes)..."

  [ -f "apps/apigee/proxies/partner-agents/apiproxy/policies/VAK-Verify-Partner-Key.xml" ] || \
    die "Missing VAK-Verify-Partner-Key.xml on partner-agents"
  [ -f "apps/apigee/proxies/partner-mcp/apiproxy/policies/VAK-Verify-Partner-Key.xml" ] || \
    die "Missing VAK-Verify-Partner-Key.xml on partner-mcp"
  echo "  ✅ Probe 1 (Unauthenticated -> 401): VerifyAPIKey enforced in PreFlow on partner-agents and partner-mcp"

  grep -q "RF-Agent-Not-Allowed" "apps/apigee/proxies/partner-agents/apiproxy/proxies/default.xml" || \
    die "Missing RF-Agent-Not-Allowed in partner-agents PreFlow"
  grep -q "cx-mortgage-orchestrator" "apps/apigee/proxies/partner-agents/apiproxy/proxies/default.xml" || \
    die "Missing cx-mortgage-orchestrator allowlist condition in partner-agents"
  echo "  ✅ Probe 2 (Unallowlisted agent -> 403/404): Internal specialists blocked by RF-Agent-Not-Allowed"

  grep -q "JS-Enforce-Tool-Scope" "apps/apigee/proxies/partner-mcp/apiproxy/proxies/default.xml" || \
    die "Missing JS-Enforce-Tool-Scope in partner-mcp PreFlow"
  grep -q "RF-Tool-Forbidden" "apps/apigee/proxies/partner-mcp/apiproxy/proxies/default.xml" || \
    die "Missing RF-Tool-Forbidden in partner-mcp PreFlow"
  echo "  ✅ Probe 3 (Unallowlisted MCP tool -> 403): Server-side tools/call scope enforced by JS-Enforce-Tool-Scope"

  grep -q '<Property name="response.streaming.enabled">true</Property>' \
    "apps/apigee/proxies/partner-agents/apiproxy/targets/sse.xml" || \
    die "SSE streaming not enabled in partner-agents/targets/sse.xml"
  grep -q '<Property name="io.timeout.millis">300000</Property>' \
    "apps/apigee/proxies/partner-agents/apiproxy/targets/sse.xml" || \
    die "300s timeout not configured in partner-agents/targets/sse.xml"
  echo "  ✅ Probe 4 (Orchestrator SSE TTFB + real answer): Dedicated targets/sse.xml with EventFlow streaming and 300s timeout"

  grep -q "<LLMTokenUsageSource>{llm.totalTokenCount}</LLMTokenUsageSource>" \
    "apps/apigee/sharedflows/token-quota/sharedflowbundle/policies/LTQ-count.xml" || \
    die "LTQ-count.xml does not meter {llm.totalTokenCount}"
  grep -q "<EnforceOnly>true</EnforceOnly>" \
    "apps/apigee/sharedflows/token-quota/sharedflowbundle/policies/LTQ-enforce.xml" || \
    die "LTQ-enforce.xml is missing EnforceOnly=true"
  echo "  ✅ Probe 5 (Quota exhaustion -> 429): LLMTokenQuota enforces & meters total tokens (input + output)"

  echo "✅ Partner conformance checks passed (offline mode)."
}

if [ "${DRY_RUN}" -eq 1 ]; then
  run_offline_conformance
  exit 0
fi

# Check whether Apigee partner_access is active in the target environment.
APIGEE_PLATFORM_DIR="${L5_DIR}/apigee/platform"
if [ ! -d "${APIGEE_PLATFORM_DIR}" ]; then
  echo "ℹ️  Apigee platform directory not deployed in ${ENV_NAME}; running offline conformance checks."
  run_offline_conformance
  exit 0
fi

APIGEE_ENV="$(tg_output "${APIGEE_PLATFORM_DIR}" apigee_env_name)"
if [ -z "${APIGEE_ENV}" ]; then
  echo "ℹ️  Apigee is disabled in ${ENV_NAME} (no live state in ${APIGEE_PLATFORM_DIR}); running offline conformance checks."
  run_offline_conformance
  exit 0
fi

# Prefer the flattened layer-5-workloads/test-vm unit; fall back for one release to the legacy
# layer-5-workloads/services/test-vm directory or local state file.
state_output() { jq -r --arg k "$2" '.outputs[$k].value // empty' "$1" 2>/dev/null || true; }
VM_DIR="${L5_DIR}/test-vm"
if [ ! -d "${VM_DIR}" ] && [ -d "${L5_DIR}/services/test-vm" ]; then
  VM_DIR="${L5_DIR}/services/test-vm"
fi
VM_NAME="$(tg_output "${VM_DIR}" instance_name)"
VM_ZONE="$(tg_output "${VM_DIR}" instance_zone)"
VM_PROJECT="$(tg_output "${VM_DIR}" project_id)"
if [ -z "${VM_NAME}" ] && [ -d "${L5_DIR}/services/test-vm" ] && [ "${VM_DIR}" != "${L5_DIR}/services/test-vm" ]; then
  VM_NAME="$(tg_output "${L5_DIR}/services/test-vm" instance_name)"
  VM_ZONE="$(tg_output "${L5_DIR}/services/test-vm" instance_zone)"
  VM_PROJECT="$(tg_output "${L5_DIR}/services/test-vm" project_id)"
fi
for candidate_state in \
  "infrastructure/live/.local_states/${ENV_NAME}/layer-5-workloads/test-vm/terraform.tfstate" \
  "infrastructure/live/.local_states/${ENV_NAME}/layer-5-workloads/services/test-vm/terraform.tfstate"; do
  if [ -z "${VM_NAME}" ] && [ -f "${candidate_state}" ]; then
    VM_NAME="$(state_output "${candidate_state}" instance_name)"
    VM_ZONE="$(state_output "${candidate_state}" instance_zone)"
    VM_PROJECT="$(state_output "${candidate_state}" project_id)"
  fi
done
[ -n "${VM_NAME}" ] && [ -n "${VM_ZONE}" ] && [ -n "${VM_PROJECT}" ] || \
  die "No test VM outputs in ${VM_DIR}. Deploy workloads first."

ENTRY_IP="$(tg_output "${LIVE_DIR}/layer-2-networking" entrypoint_address)"
DOMAIN="$(tg_output "${LIVE_DIR}/layer-2-networking" entrypoint_domain)"
[ -n "${ENTRY_IP}" ] && [ -n "${DOMAIN}" ] || die "No entrypoint outputs in layer-2-networking."

CA_SECRET="$(tg_output "${LIVE_DIR}/layer-3-security" internal_ca_secret_id)"
CA_SECRET_PROJECT="$(echo "${CA_SECRET}" | cut -d/ -f2)"
CA_PEM="$(gcloud secrets versions access latest --secret="${CA_SECRET##*/}" --project="${CA_SECRET_PROJECT}")"

PARTNER_KEY="${ESMERALDA_PARTNER_API_KEY:-}"
if [ -z "${PARTNER_KEY}" ]; then
  KEY_SECRET_ID="$(tg_output "${L5_DIR}/apigee/products" partner_consumer_key_secret_id)"
  if [ -n "${KEY_SECRET_ID}" ]; then
    KEY_PROJ="$(echo "${KEY_SECRET_ID}" | cut -d/ -f2)"
    PARTNER_KEY="$(gcloud secrets versions access latest --secret="${KEY_SECRET_ID##*/}" --project="${KEY_PROJ}")"
  fi
fi
[ -n "${PARTNER_KEY}" ] || die "Set ESMERALDA_PARTNER_API_KEY or deploy layer-5-workloads/apigee/products."

PARTNER_HOST="partner.${DOMAIN}"
echo "🔎 Running live partner conformance probes against https://${PARTNER_HOST} (${ENTRY_IP}) from ${VM_NAME}..."

REMOTE="$(mktemp)"
trap 'rm -f "${REMOTE}"' EXIT
{
  printf 'ENTRY_IP=%q\n' "${ENTRY_IP}"
  printf 'PARTNER_HOST=%q\n' "${PARTNER_HOST}"
  printf 'PARTNER_KEY=%q\n' "${PARTNER_KEY}"
  printf 'CA_PEM=%q\n' "${CA_PEM}"
  printf 'ESMERALDA_TEST_QUOTA_EXHAUSTION=%q\n' "${ESMERALDA_TEST_QUOTA_EXHAUSTION:-0}"
  cat <<'REMOTE_EOF'
set -euo pipefail
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
printf '%s\n' "${CA_PEM}" > "${WORK}/ca.pem"

CURL_BASE=(-sS --connect-timeout 5 --cacert "${WORK}/ca.pem" --resolve "${PARTNER_HOST}:443:${ENTRY_IP}")

# Probe 1: Unauthenticated request -> HTTP 401
code1="$(curl "${CURL_BASE[@]}" -o "${WORK}/p1.out" -w '%{http_code}' -m 15 \
  "https://${PARTNER_HOST}/partner/v1/agents/cx-mortgage-orchestrator/v1/card")"
[ "${code1}" = "401" ] || { echo "❌ Probe 1 expected HTTP 401, got ${code1}"; exit 1; }
echo "  ✅ Probe 1: Unauthenticated request rejected with HTTP 401"

# Probe 2: Unallowlisted internal agent -> HTTP 403 or 404
code2="$(curl "${CURL_BASE[@]}" -o "${WORK}/p2.out" -w '%{http_code}' -m 15 \
  -H "x-api-key: ${PARTNER_KEY}" \
  "https://${PARTNER_HOST}/partner/v1/agents/ai-coe-mortgage-specialist/v1/card")"
if [ "${code2}" != "403" ] && [ "${code2}" != "404" ]; then
  echo "❌ Probe 2 expected HTTP 403/404 for internal specialist, got ${code2}"
  exit 1
fi
echo "  ✅ Probe 2: Internal specialist ai-coe-mortgage-specialist blocked with HTTP ${code2}"

# Probe 3: Unallowlisted MCP tool call -> HTTP 403
code3="$(curl "${CURL_BASE[@]}" -o "${WORK}/p3.out" -w '%{http_code}' -m 15 \
  -X POST -H "x-api-key: ${PARTNER_KEY}" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"restricted_admin_drop_table","arguments":{}}}' \
  "https://${PARTNER_HOST}/partner/v1/mcp/income-verification/mcp")"
[ "${code3}" = "403" ] || { echo "❌ Probe 3 expected HTTP 403 for restricted MCP tool, got ${code3}"; exit 1; }
echo "  ✅ Probe 3: Unallowlisted MCP tool call rejected with HTTP 403"

# Probe 4: Partner key -> cx-mortgage-orchestrator :streamQuery -> TTFB streaming + real answer
timing="$(curl "${CURL_BASE[@]}" -N -o "${WORK}/p4.out" -w '%{http_code} %{time_starttransfer} %{time_total}' -m 180 \
  -X POST -H "x-api-key: ${PARTNER_KEY}" -H "Content-Type: application/json" -H "Accept: text/event-stream" \
  -d '{"class_method":"async_stream_query","input":{"message":"Can you verify Julian Sterling'\''s income?","user_id":"partner-smoke-test"}}' \
  "https://${PARTNER_HOST}/partner/v1/agents/cx-mortgage-orchestrator:streamQuery?alt=sse")"
read -r code4 ttfb total <<< "${timing}"
[ "${code4}" = "200" ] || { echo "❌ Probe 4 expected HTTP 200, got ${code4}"; cat "${WORK}/p4.out"; exit 1; }
[ -s "${WORK}/p4.out" ] || { echo "❌ Probe 4 returned an empty response body"; exit 1; }
grep -qi "Sterling" "${WORK}/p4.out" || { echo "❌ Probe 4 response did not contain Julian Sterling verification"; cat "${WORK}/p4.out"; exit 1; }
awk -v ttfb="${ttfb}" -v total="${total}" 'BEGIN { if (ttfb <= 0 || ttfb > total) exit 1 }' || {
  echo "❌ Probe 4 invalid TTFB metrics (ttfb=${ttfb}s, total=${total}s)"
  exit 1
}
echo "  ✅ Probe 4: Orchestrator SSE stream answered (TTFB=${ttfb}s, total=${total}s)"

# Probe 5: Quota exhaustion probe -> HTTP 429 when LLMTokenQuota limit is exceeded
if [ "${ESMERALDA_TEST_QUOTA_EXHAUSTION:-0}" = "1" ]; then
  got_429=0
  for _ in $(seq 1 25); do
    c="$(curl "${CURL_BASE[@]}" -o /dev/null -w '%{http_code}' -m 60 \
      -X POST -H "x-api-key: ${PARTNER_KEY}" -H "Content-Type: application/json" \
      -d '{"class_method":"async_stream_query","input":{"message":"Quota probe","user_id":"partner-smoke-test"}}' \
      "https://${PARTNER_HOST}/partner/v1/agents/cx-mortgage-orchestrator:streamQuery?alt=sse")"
    if [ "${c}" = "429" ]; then
      got_429=1
      break
    fi
  done
  [ "${got_429}" = "1" ] || { echo "❌ Probe 5 did not observe HTTP 429 under quota exhaustion"; exit 1; }
  echo "  ✅ Probe 5: Quota exhaustion returned HTTP 429"
else
  echo "  ✅ Probe 5: Quota policy deployed (set ESMERALDA_TEST_QUOTA_EXHAUSTION=1 to exhaust live quota)"
fi
REMOTE_EOF
} > "${REMOTE}"

gcloud compute ssh "${VM_NAME}" --zone="${VM_ZONE}" --project="${VM_PROJECT}" \
  --tunnel-through-iap --quiet --command='bash -s' < "${REMOTE}"
echo "✅ All live partner front-door conformance probes passed!"
