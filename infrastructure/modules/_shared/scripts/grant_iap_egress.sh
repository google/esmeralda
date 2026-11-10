#!/usr/bin/env bash
# infrastructure/modules/_shared/scripts/grant_iap_egress.sh
#
# Grants roles/iap.egressor on the central Agent Registry so agents may egress through the
# Agent Gateway:
#   - On the registry root, as a CONDITIONAL binding that only matches system endpoint entries
#     (Google APIs such as aiplatform, telemetry, logging). A registry-root grant is inherited by
#     every entry (agents, MCP servers, endpoints) and IAM allow policies only add, so an
#     unconditioned root binding would let its members reach every agent and MCP server no
#     matter what the per-target policies say. The script refuses to write one.
#   - Per-target on each registered MCP server (--mcp-server) and A2A agent (--agent) when
#     IAP_TARGET_POLICIES_JSON is provided. These policies decide who may call each target.
#
# Idempotent (set-iam-policy). Called by:
#   - layer 4 governance (registry-root system-endpoint policy, right after the gateway exists)
#   - layer 5 workloads `iap-egress` unit (same root policy + per-target least-privilege
#     policies after MCP services and agents have registered themselves)
#
# Required env:
#   GOVERNANCE_PROJECT_ID      central governance project hosting the Agent Registry
#   GOVERNANCE_PROJECT_NUMBER  its project number (IAP evaluates resource names by number)
#   REGION                     registry location
#   IAP_MEMBERS_JSON           JSON list of IAM members allowed to reach system endpoints
# Optional env:
#   IAP_TARGET_POLICIES_JSON   JSON object mapping per-target member lists:
#                              {"mcp_servers": {"<id>": [...]}, "agents": {"<id>": [...]}}
set -euo pipefail

: "${GOVERNANCE_PROJECT_ID:?}" "${GOVERNANCE_PROJECT_NUMBER:?}" "${REGION:?}" "${IAP_MEMBERS_JSON:?}"

if [[ ! "${GOVERNANCE_PROJECT_NUMBER}" =~ ^[0-9]+$ ]]; then
  echo "❌ GOVERNANCE_PROJECT_NUMBER must be the numeric project number" >&2
  exit 1
fi
if [[ ! "${REGION}" =~ ^[a-z0-9-]+$ ]]; then
  echo "❌ REGION must be a plain location id (e.g. us-central1)" >&2
  exit 1
fi

IAP_ROLE="roles/iap.egressor"
ENDPOINTS_PREFIX="projects/${GOVERNANCE_PROJECT_NUMBER}/locations/${REGION}/iap_web/agentRegistry/endpoints/"
ROOT_CONDITION_TITLE="system-endpoints-only"
ROOT_CONDITION_DESCRIPTION="Registry-root egress applies to system endpoint entries only; agents and MCP servers are authorized by their own per-target policies."
ROOT_CONDITION_EXPRESSION="resource.name.startsWith(\"${ENDPOINTS_PREFIX}\")"

POLICY_FILE="$(mktemp)"
trap 'rm -f "${POLICY_FILE}"' EXIT

run_set_iam_policy() {
  gcloud iap web set-iam-policy "${POLICY_FILE}" \
    --project="${GOVERNANCE_PROJECT_ID}" \
    --resource-type=agent-registry \
    --region="${REGION}" \
    --quiet "$@" >/dev/null
}

# Registry root: conditional binding scoped to system endpoint entries (policy version 3).
set_root_policy() {
  local members_json="$1"
  jq -n \
    --arg role "${IAP_ROLE}" \
    --argjson members "${members_json}" \
    --arg title "${ROOT_CONDITION_TITLE}" \
    --arg description "${ROOT_CONDITION_DESCRIPTION}" \
    --arg expression "${ROOT_CONDITION_EXPRESSION}" \
    '{bindings: [{role: $role, members: $members,
                  condition: {title: $title, description: $description, expression: $expression}}],
      version: 3}' >"${POLICY_FILE}"

  # Guard: never write a registry-root binding that isn't scoped to system endpoints.
  if ! jq -e --arg expression "${ROOT_CONDITION_EXPRESSION}" \
    '.version == 3 and (.bindings | length > 0)
     and all(.bindings[]; (.condition.expression // "") == $expression)' \
    "${POLICY_FILE}" >/dev/null; then
    echo "❌ Refusing to write an unconditioned registry-root IAP egress binding" >&2
    exit 1
  fi

  run_set_iam_policy
}

# Per-target entry (--mcp-server=<uid> or --agent=<uid>): unconditioned member list.
set_target_policy() {
  local members_json="$1"
  local target_flag="${2:-}"
  if [[ "${target_flag}" != --mcp-server=?* && "${target_flag}" != --agent=?* ]]; then
    echo "❌ Per-target IAP egress policy needs --mcp-server=<id> or --agent=<id>" >&2
    exit 1
  fi
  jq -n --arg role "${IAP_ROLE}" --argjson members "${members_json}" \
    '{bindings: [{role: $role, members: $members}]}' >"${POLICY_FILE}"
  run_set_iam_policy "${target_flag}"
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

echo "🔒 Registry-root IAP egress policy (system endpoints only) on ${GOVERNANCE_PROJECT_ID}/${REGION}..."
set_root_policy "${IAP_MEMBERS_JSON}"

if [[ -n "${IAP_TARGET_POLICIES_JSON:-}" ]] && jq -e '((.mcp_servers // {}) | length > 0) or ((.agents // {}) | length > 0)' >/dev/null 2>&1 <<<"${IAP_TARGET_POLICIES_JSON}"; then
  echo "🎯 Applying per-target least-privilege IAP egress policies..."

  for MCP_ID in $(jq -r '.mcp_servers // {} | keys[]' <<<"${IAP_TARGET_POLICIES_JSON}"); do
    TARGET_MEMBERS="$(jq -c --arg id "${MCP_ID}" '.mcp_servers[$id]' <<<"${IAP_TARGET_POLICIES_JSON}")"
    RESOLVED_MCP_UID="$(resolve_registry_uid "${MCP_ID}")"
    echo "  -> mcp-server ${MCP_ID} (${RESOLVED_MCP_UID})"
    set_target_policy "${TARGET_MEMBERS}" --mcp-server="${RESOLVED_MCP_UID}"
  done

  for AGENT_ID in $(jq -r '.agents // {} | keys[]' <<<"${IAP_TARGET_POLICIES_JSON}"); do
    TARGET_MEMBERS="$(jq -c --arg id "${AGENT_ID}" '.agents[$id]' <<<"${IAP_TARGET_POLICIES_JSON}")"
    RESOLVED_AGENT_UID="$(resolve_registry_uid "${AGENT_ID}")"
    echo "  -> agent ${AGENT_ID} (${RESOLVED_AGENT_UID})"
    set_target_policy "${TARGET_MEMBERS}" --agent="${RESOLVED_AGENT_UID}"
  done
fi

echo "✅ IAP egress policies applied."
