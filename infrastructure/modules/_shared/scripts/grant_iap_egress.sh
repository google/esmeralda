#!/usr/bin/env bash
# infrastructure/modules/_shared/scripts/grant_iap_egress.sh
#
# Grants roles/iap.egressor on the central Agent Registry so agents may egress through the
# Agent Gateway:
#   - Registry-wide (for system Google API endpoints such as aiplatform, telemetry, logging)
#   - Per-target on each registered MCP server (--mcp-server) and A2A agent (--agent) when
#     IAP_TARGET_POLICIES_JSON is provided.
#
# Idempotent (set-iam-policy). Called by:
#   - layer 4 governance (registry-wide system endpoints, right after the gateway exists)
#   - layer 5 workloads `iap-egress` stack (per-target least-privilege policies after MCP
#     services and agents have registered themselves)
#
# Required env:
#   GOVERNANCE_PROJECT_ID     central governance project hosting the Agent Registry
#   REGION                    registry location
#   IAP_MEMBERS_JSON          JSON list of IAM members for registry-wide system endpoints
# Optional env:
#   IAP_TARGET_POLICIES_JSON  JSON object mapping per-target member lists:
#                             {"mcp_servers": {"<id>": [...]}, "agents": {"<id>": [...]}}
set -euo pipefail

: "${GOVERNANCE_PROJECT_ID:?}" "${REGION:?}" "${IAP_MEMBERS_JSON:?}"

POLICY_FILE="$(mktemp)"
trap 'rm -f "${POLICY_FILE}"' EXIT

write_policy_file() {
  local members_json="$1"
  cat > "${POLICY_FILE}" <<EOF
{
  "bindings": [
    {
      "role": "roles/iap.egressor",
      "members": ${members_json}
    }
  ]
}
EOF
}

set_policy_for_members() {
  local members_json="$1"
  shift
  write_policy_file "${members_json}"
  gcloud iap web set-iam-policy "${POLICY_FILE}" \
    --project="${GOVERNANCE_PROJECT_ID}" \
    --resource-type=agent-registry \
    --region="${REGION}" \
    --quiet "$@" >/dev/null
}

resolve_registry_uid() {
  local service_id="$1"
  if [[ "${service_id}" == agentregistry-* ]]; then
    echo "${service_id}"
    return 0
  fi
  local token
  token="$(gcloud auth print-access-token)"
  local resp
  resp="$(curl -sS -H "Authorization: Bearer ${token}" \
    "https://agentregistry.googleapis.com/v1alpha/projects/${GOVERNANCE_PROJECT_ID}/locations/${REGION}/services/${service_id}")"
  local reg_res
  reg_res="$(jq -r '.registryResource // empty' <<<"${resp}")"
  if [[ -n "${reg_res}" ]]; then
    basename "${reg_res}"
  else
    echo "${service_id}"
  fi
}

echo "🔒 Registry-wide IAP egress policy on ${GOVERNANCE_PROJECT_ID}/${REGION}..."
set_policy_for_members "${IAP_MEMBERS_JSON}"

if [[ -n "${IAP_TARGET_POLICIES_JSON:-}" ]] && jq -e '((.mcp_servers // {}) | length > 0) or ((.agents // {}) | length > 0)' >/dev/null 2>&1 <<<"${IAP_TARGET_POLICIES_JSON}"; then
  echo "🎯 Applying per-target least-privilege IAP egress policies..."

  for MCP_ID in $(jq -r '.mcp_servers // {} | keys[]' <<<"${IAP_TARGET_POLICIES_JSON}"); do
    TARGET_MEMBERS="$(jq -c --arg id "${MCP_ID}" '.mcp_servers[$id]' <<<"${IAP_TARGET_POLICIES_JSON}")"
    RESOLVED_MCP_UID="$(resolve_registry_uid "${MCP_ID}")"
    echo "  -> mcp-server ${MCP_ID} (${RESOLVED_MCP_UID})"
    set_policy_for_members "${TARGET_MEMBERS}" --mcp-server="${RESOLVED_MCP_UID}"
  done

  for AGENT_ID in $(jq -r '.agents // {} | keys[]' <<<"${IAP_TARGET_POLICIES_JSON}"); do
    TARGET_MEMBERS="$(jq -c --arg id "${AGENT_ID}" '.agents[$id]' <<<"${IAP_TARGET_POLICIES_JSON}")"
    RESOLVED_AGENT_UID="$(resolve_registry_uid "${AGENT_ID}")"
    echo "  -> agent ${AGENT_ID} (${RESOLVED_AGENT_UID})"
    set_policy_for_members "${TARGET_MEMBERS}" --agent="${RESOLVED_AGENT_UID}"
  done
fi

echo "✅ IAP egress policies applied."
