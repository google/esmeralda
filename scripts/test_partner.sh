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
#      Runs the probes of the scenario catalog (`tests/partner/scenarios.py`, every scenario with
#      a `probe` number) through the partner front door from the test VM, with
#      `tests/partner/probe.py` (stdlib Python, copied to the VM):
#        Probe 1: Unauthenticated request -> HTTP 401
#        Probe 2: Unallowlisted internal specialist (`ai-coe-mortgage-specialist`) -> HTTP 403/404
#        Probe 3: Unallowlisted MCP tool call (`tools/call` for restricted tool) -> HTTP 403
#        Probe 4: Partner key -> `cx-mortgage-orchestrator` `:streamQuery` -> verifies TTFB
#                 streaming (first SSE chunk arrives before stream completion) + real answer
#        Probe 5: Quota exhaustion probe -> HTTP 429 when `LLMTokenQuota` limit is exceeded
#                 (only with ESMERALDA_TEST_QUOTA_EXHAUSTION=1)
#      New probes are added to the catalog only; this script does not change.
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/partner_env.sh
source "${SCRIPT_DIR}/partner_env.sh"
CATALOG_DIR="${SCRIPT_DIR}/../tests/partner"

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
APIGEE_PLATFORM_DIR="infrastructure/live/${ENV_NAME}/layer-5-workloads/apigee/platform"
if [ ! -d "${APIGEE_PLATFORM_DIR}" ]; then
  echo "ℹ️  Apigee platform directory not deployed in ${ENV_NAME}; running offline conformance checks."
  run_offline_conformance
  exit 0
fi

APIGEE_ENV="$(partner_apigee_env "${ENV_NAME}")"
if [ -z "${APIGEE_ENV}" ]; then
  echo "ℹ️  Apigee is disabled in ${ENV_NAME} (no live state in ${APIGEE_PLATFORM_DIR}); running offline conformance checks."
  run_offline_conformance
  exit 0
fi

partner_resolve "${ENV_NAME}"
echo "🔎 Running live partner conformance probes against https://${PARTNER_HOST} (${ENTRY_IP}) from ${VM_NAME}..."

REMOTE="$(mktemp)"
trap 'rm -f "${REMOTE}"' EXIT
chmod 600 "${REMOTE}"
{
  printf 'PARTNER_ENTRY_IP=%q\n' "${ENTRY_IP}"
  printf 'PARTNER_HOST=%q\n' "${PARTNER_HOST}"
  printf 'PARTNER_API_KEY=%q\n' "${PARTNER_KEY}"
  printf 'CA_PEM=%q\n' "${CA_PEM}"
  printf 'ESMERALDA_TEST_QUOTA_EXHAUSTION=%q\n' "${ESMERALDA_TEST_QUOTA_EXHAUSTION:-0}"
  printf 'SCENARIOS_B64=%q\n' "$(b64_file "${CATALOG_DIR}/scenarios.py")"
  printf 'PROBE_B64=%q\n' "$(b64_file "${CATALOG_DIR}/probe.py")"
  cat <<'REMOTE_EOF'
set -euo pipefail
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
printf '%s\n' "${CA_PEM}" > "${WORK}/ca.pem"
printf '%s' "${SCENARIOS_B64}" | base64 -d > "${WORK}/scenarios.py"
printf '%s' "${PROBE_B64}" | base64 -d > "${WORK}/probe.py"
PARTNER_CA_FILE="${WORK}/ca.pem"
export PARTNER_ENTRY_IP PARTNER_HOST PARTNER_API_KEY PARTNER_CA_FILE ESMERALDA_TEST_QUOTA_EXHAUSTION
python3 "${WORK}/probe.py"
REMOTE_EOF
} > "${REMOTE}"

gcloud compute ssh "${VM_NAME}" --zone="${VM_ZONE}" --project="${VM_PROJECT}" \
  --tunnel-through-iap --quiet --command='bash -s' < "${REMOTE}"
echo "✅ All live partner front-door conformance probes passed!"
