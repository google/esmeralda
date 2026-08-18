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

set -e

GATEWAY_HOST="${1:-a2a-mortgage-agent.esmeralda.internal}"
GATEWAY_URL="http://${GATEWAY_HOST}"
AUDIENCE="${GATEWAY_URL}"

echo "========================================================================="
echo "🧪 Esmeralda A2A Gateway E2E Verification Script (Internal VM)"
echo "Target Gateway: ${GATEWAY_URL}"
echo "========================================================================="

echo -n "[1/4] Fetching OIDC token from GCP Metadata Server for audience ${AUDIENCE}... "
if ! TOKEN=$(curl -s -f -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=${AUDIENCE}"); then
  echo "FAILED!"
  echo "Error: Could not retrieve OIDC token from metadata server. Are you running this script inside a GCP VM (e.g., test-vm-dev)?"
  exit 1
fi
echo "OK (${#TOKEN} chars)"

echo ""
echo "[2/4] Verifying AgentCard at ${GATEWAY_URL}/v1/card..."
CARD_STATUS=$(curl -s -o /tmp/a2a_card.json -w "%{http_code}" -H "Authorization: Bearer ${TOKEN}" "${GATEWAY_URL}/v1/card")
if [ "${CARD_STATUS}" != "200" ]; then
  echo "❌ Error: Failed to fetch AgentCard (HTTP ${CARD_STATUS})"
  cat /tmp/a2a_card.json
  exit 1
fi
AGENT_NAME=$(python3 -c "import json; print(json.load(open('/tmp/a2a_card.json')).get('name', 'UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")
echo "✅ AgentCard fetched successfully (Agent Name: ${AGENT_NAME})"

echo ""
QUERY_TEXT="Can you search documents for Julian Sterling with document_type tax_return?"
MESSAGE_ID="gateway-e2e-$(date +%s)"

echo "[3/4] Submitting 1-turn tool execution prompt to ${GATEWAY_URL}/v1/message:send..."
echo "      Prompt: \"${QUERY_TEXT}\""
echo "      (Note: A2A v0.3 schema uses role='1' for USER and repeated 'content' [{text:...}])"

PAYLOAD=$(cat <<EOF
{
  "message": {
    "messageId": "${MESSAGE_ID}",
    "role": "1",
    "content": [
      {
        "text": "${QUERY_TEXT}"
      }
    ]
  }
}
EOF
)

SEND_STATUS=$(curl -s -o /tmp/a2a_send_res.json -w "%{http_code}" -X POST \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "${PAYLOAD}" \
  "${GATEWAY_URL}/v1/message:send")

if [ "${SEND_STATUS}" != "200" ]; then
  echo "❌ Error: /v1/message:send failed (HTTP ${SEND_STATUS})"
  cat /tmp/a2a_send_res.json
  exit 1
fi

TASK_ID=$(python3 -c "import json; res=json.load(open('/tmp/a2a_send_res.json')); print(res.get('task', {}).get('id', res.get('taskId', '')))" 2>/dev/null)
if [ -z "${TASK_ID}" ]; then
  echo "❌ Error: Could not parse taskId from response:"
  cat /tmp/a2a_send_res.json
  exit 1
fi

echo "✅ Task submitted successfully! Task ID: ${TASK_ID}"

echo ""
echo "[4/4] Polling task status at ${GATEWAY_URL}/v1/tasks/${TASK_ID}..."
ATTEMPT=1
MAX_ATTEMPTS=15
SLEEP_SEC=3

while [ ${ATTEMPT} -le ${MAX_ATTEMPTS} ]; do
  echo -n "      Polling attempt ${ATTEMPT}/${MAX_ATTEMPTS}... "
  TASK_STATUS_CODE=$(curl -s -o /tmp/a2a_task_res.json -w "%{http_code}" -H "Authorization: Bearer ${TOKEN}" "${GATEWAY_URL}/v1/tasks/${TASK_ID}")
  
  if [ "${TASK_STATUS_CODE}" != "200" ]; then
    echo "FAILED (HTTP ${TASK_STATUS_CODE})"
    cat /tmp/a2a_task_res.json
    exit 1
  fi

  STATE=$(python3 -c "import json; res=json.load(open('/tmp/a2a_task_res.json')); print(res.get('status', {}).get('state', 'UNKNOWN'))" 2>/dev/null)
  echo "[${STATE}]"

  if [ "${STATE}" = "TASK_STATE_COMPLETED" ]; then
    echo ""
    echo "🎉 Task Completed!"
    echo "========================================================================="
    echo "📜 Final Agent Response Output:"
    echo "========================================================================="
    python3 -c "
import json
res = json.load(open('/tmp/a2a_task_res.json'))
history = res.get('history', [])
for msg in history:
    role = msg.get('role', 'UNKNOWN')
    if role == 'ROLE_AGENT':
        content = msg.get('content', [])
        for part in content:
            if 'text' in part:
                print(f'\n🤖 AGENT SYNTHESIZED RESPONSE:\n{part[\"text\"]}\n')
            elif 'data' in part:
                func_data = part['data'].get('data', {})
                func_name = func_data.get('name', 'unknown_func')
                if 'args' in func_data:
                    print(f'🛠️  TOOL INVOCATION: {func_name}({func_data[\"args\"]})')
                elif 'response' in func_data:
                    print(f'📬 TOOL RESPONSE ({func_name}): {func_data[\"response\"][\"structuredContent\"]}')
"
    exit 0
  elif [ "${STATE}" = "TASK_STATE_FAILED" ] || [ "${STATE}" = "TASK_STATE_CANCELLED" ]; then
    echo "❌ Task ended in state: ${STATE}"
    cat /tmp/a2a_task_res.json
    exit 1
  fi

  ATTEMPT=$((ATTEMPT + 1))
  sleep ${SLEEP_SEC}
done

echo "⚠️ Timed out waiting for task to complete after $((MAX_ATTEMPTS * SLEEP_SEC)) seconds."
exit 1
