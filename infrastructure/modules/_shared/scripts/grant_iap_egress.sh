#!/usr/bin/env bash
# infrastructure/modules/_shared/scripts/grant_iap_egress.sh
#
# Grants roles/iap.egressor on the central Agent Registry so agents may egress through the
# Agent Gateway: registry-wide, plus on every registered MCP server and A2A agent.
#
# Idempotent (set-iam-policy). Called by:
#   - layer 4 governance (registry-wide + system endpoints, right after the gateway exists)
#   - layer 5 workloads `iap-egress` stack (after MCP services and agents registered themselves)
#
# Required env:
#   GOVERNANCE_PROJECT_ID  central governance project hosting the Agent Registry
#   REGION                 registry location
#   IAP_MEMBERS_JSON       JSON list of IAM members (serviceAccount:/principalSet:/principal:)
set -euo pipefail

: "${GOVERNANCE_PROJECT_ID:?}" "${REGION:?}" "${IAP_MEMBERS_JSON:?}"

POLICY_FILE="$(mktemp)"
trap 'rm -f "${POLICY_FILE}"' EXIT

cat > "${POLICY_FILE}" <<EOF
{
  "bindings": [
    {
      "role": "roles/iap.egressor",
      "members": ${IAP_MEMBERS_JSON}
    }
  ]
}
EOF

set_policy() {
  gcloud iap web set-iam-policy "${POLICY_FILE}" \
    --project="${GOVERNANCE_PROJECT_ID}" \
    --resource-type=agent-registry \
    --region="${REGION}" \
    --quiet "$@" >/dev/null
}

echo "🔒 Registry-wide IAP egress policy on ${GOVERNANCE_PROJECT_ID}/${REGION}..."
set_policy

TOKEN="$(gcloud auth print-access-token)"
API="https://agentregistry.googleapis.com/v1alpha/projects/${GOVERNANCE_PROJECT_ID}/locations/${REGION}"

for MCP_ID in $(curl -sf -H "Authorization: Bearer ${TOKEN}" "${API}/mcpServers" | jq -r '.mcpServers[]?.name | split("/") | last'); do
  echo "  -> mcp-server ${MCP_ID}"
  set_policy --mcp-server="${MCP_ID}"
done

for AGENT_ID in $(curl -sf -H "Authorization: Bearer ${TOKEN}" "${API}/agents" | jq -r '.agents[]?.name | split("/") | last'); do
  echo "  -> agent ${AGENT_ID}"
  set_policy --agent="${AGENT_ID}"
done

echo "✅ IAP egress policies applied."
