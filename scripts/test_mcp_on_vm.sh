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

set -euo pipefail

SERVICE="legacy-dms"
METHOD="tools/list"
PARAMS=""
VERBOSE=0

usage() {
  cat <<EOF
Usage: $0 [OPTIONS]

Tests FastMCP tool discovery or tool calling over Private DNS (via Kong Gateway ILB).
Intended to be executed on a VM inside the shared VPC (e.g. test-vm-dev).

Options:
  -s, --service <name>   Service hostname prefix (default: legacy-dms)
                         Available options: legacy-dms, income-verification, corporate-email
  -m, --method <method>  JSON-RPC method name (default: tools/list)
                         Common methods: tools/list, tools/call, ping, resources/list
  -p, --params <json>    JSON string for "params" object (required for tools/call)
                         Example: '{"name": "search_documents", "arguments": {"applicant_last_name": "Smith", "document_type": "tax_return"}}'
  -v, --verbose          Enable verbose debug output
  -h, --help             Show this help message

Examples:
  $0 --service legacy-dms --method tools/list
  $0 --service income-verification --method tools/list
  $0 --service legacy-dms --method tools/call --params '{"name": "search_documents", "arguments": {"applicant_last_name": "Smith", "document_type": "tax_return"}}'
EOF
  exit 1
}

while [[ $# -gt 0 ]]; do
  case $1 in
    -s|--service)
      SERVICE="$2"
      shift 2
      ;;
    -m|--method)
      METHOD="$2"
      shift 2
      ;;
    -p|--params)
      PARAMS="$2"
      shift 2
      ;;
    -v|--verbose)
      VERBOSE=1
      shift
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "Unknown option: $1"
      usage
      ;;
  esac
done

HOSTNAME="${SERVICE}.esmeralda.internal"
TARGET_URL="http://${HOSTNAME}/mcp"
AUDIENCE="http://${HOSTNAME}"

if [ "$VERBOSE" -eq 1 ]; then
  echo "🔌 Fetching OIDC Identity Token for audience: ${AUDIENCE} ..."
fi

TOKEN=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=${AUDIENCE}&format=full")

if [ -z "$TOKEN" ] || [[ "$TOKEN" == *"<html>"* ]]; then
  echo "❌ Error: Could not retrieve valid OIDC identity token from Google Compute Engine metadata server."
  echo "Make sure this script is running inside a GCE VM (e.g. test-vm-dev) with an assigned service account."
  exit 1
fi

if [ -n "$PARAMS" ]; then
  PAYLOAD="{\"jsonrpc\": \"2.0\", \"id\": 1, \"method\": \"${METHOD}\", \"params\": ${PARAMS}}"
else
  PAYLOAD="{\"jsonrpc\": \"2.0\", \"id\": 1, \"method\": \"${METHOD}\"}"
fi

if [ "$VERBOSE" -eq 1 ]; then
  echo "🌐 Sending POST ${TARGET_URL}"
  echo "📦 Payload: ${PAYLOAD}"
  echo "----------------------------------------"
fi

RESPONSE=$(curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X POST \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d "${PAYLOAD}" \
  "${TARGET_URL}")

BODY=$(echo "$RESPONSE" | sed -n '1,/HTTP_STATUS:/p' | sed '$d')
STATUS=$(echo "$RESPONSE" | grep -o 'HTTP_STATUS:[0-9]*' | cut -d: -f2)

if [ "$STATUS" -eq 200 ]; then
  echo "✅ HTTP ${STATUS} OK"
  echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
else
  echo "❌ HTTP ${STATUS} FAILED"
  echo "$BODY"
  exit 1
fi
