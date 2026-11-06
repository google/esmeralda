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

# Gateway sandbox checks. Runs ON the test VM (inside the VPC), started by run_checks.sh:
#   UPSTREAM_URL=https://... RATE_LIMIT=20 bash -s < checks.sh
# Calls go through the real path: private DNS -> internal HTTPS LB (private-CA TLS) -> Cloud Run IAM
# -> Envoy -> upstream. The VM's service account is the caller; Envoy swaps in its own token.
set -uo pipefail

: "${UPSTREAM_URL:?UPSTREAM_URL is required}"
RATE_LIMIT="${RATE_LIMIT:-20}"
APEX="esmeralda.internal"
MD="http://metadata.google.internal/computeMetadata/v1/instance"
CA=/tmp/esmeralda-root-ca.pem
curl -sf -H "Metadata-Flavor: Google" "$MD/attributes/esmeralda-root-ca" > "$CA" || { echo "FAIL no root CA in instance metadata"; exit 1; }

PASS=0
FAIL=0
pass() { echo "PASS  $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL  $1: $2"; FAIL=$((FAIL + 1)); }
token() { curl -sf -H "Metadata-Flavor: Google" "$MD/service-accounts/default/identity?audience=$1"; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)" 2>/dev/null; }

# gw HOST PATH [curl args...]: request through the gateway with an ID token for https://HOST.
gw() {
  local host=$1 path=$2
  shift 2
  curl -sS --cacert "$CA" -H "Authorization: Bearer $(token "https://$host")" "$@" "https://$host$path"
}

# A new internal HTTPS LB takes a few minutes to program its proxies; until then TLS handshakes fail
# intermittently. Wait until /healthz answers 5 times in a row (up to 10 minutes).
READY=0
for _ in $(seq 1 120); do
  if [[ "$(gw "$APEX" /healthz 2>/dev/null)" == "ok" ]]; then READY=$((READY + 1)); else READY=0; fi
  ((READY >= 5)) && break
  sleep 5
done
((READY >= 5)) || { echo "FAIL gateway not ready after 10 minutes"; exit 1; }

# 1. Private DNS resolves every name to the load balancer.
VIP=$(getent hosts "legacy-dms.$APEX" | awk '{print $1}')
[[ -n "$VIP" && "$(getent hosts "$APEX" | awk '{print $1}')" == "$VIP" ]] && pass "dns: *.$APEX and $APEX -> $VIP" || fail "dns" "got '$VIP'"

# 2. MCP route: path kept, caller headers passed, caller token replaced by an ID token for the upstream.
BODY=$(gw "legacy-dms.$APEX" /mcp -X POST -H "X-API-Key: sandbox-agent" -H "User-Auth-Token: user-1" -d '{}')
[[ "$(echo "$BODY" | json 'd["path"]')" == "/mcp" ]] && pass "mcp route keeps /mcp" || fail "mcp path" "$BODY"
[[ "$(echo "$BODY" | json 'd["headers"].get("x-api-key")')" == "sandbox-agent" && "$(echo "$BODY" | json 'd["headers"].get("user-auth-token")')" == "user-1" ]] && pass "caller headers passed through" || fail "headers" "$BODY"
AUD=$(echo "$BODY" | json 'd["authorization"].get("aud")')
EMAIL=$(echo "$BODY" | json 'd["authorization"].get("email")')
[[ "$AUD" == "$UPSTREAM_URL" ]] && pass "upstream got an ID token for its own audience ($EMAIL)" || fail "id token" "aud=$AUD"

# 3. Cloud Run IAM still gates the gateway.
CODE=$(curl -s -o /dev/null -w '%{http_code}' --cacert "$CA" "https://legacy-dms.$APEX/mcp")
[[ "$CODE" == "403" || "$CODE" == "401" ]] && pass "no token -> $CODE" || fail "no token" "got $CODE"
CODE=$(curl -s -o /dev/null -w '%{http_code}' --cacert "$CA" -H "Authorization: Bearer $(token https://wrong.example)" "https://legacy-dms.$APEX/mcp")
[[ "$CODE" == "401" || "$CODE" == "403" ]] && pass "wrong audience -> $CODE" || fail "wrong audience" "got $CODE"

# 4. Vertex AI route: a real OAuth2 access token minted by gcp_authn from the metadata server.
CODE=$(gw "ai-coe-mortgage-specialist.$APEX" /reasoningEngines -o /tmp/vertex.json -w '%{http_code}')
[[ "$CODE" == "200" ]] && pass "vertex route: access token accepted by aiplatform (200)" || fail "vertex route" "got $CODE: $(head -c 300 /tmp/vertex.json)"

# 5. A blocking call longer than Envoy's 15 s default route timeout.
START=$(date +%s)
CODE=$(gw "legacy-dms.$APEX" "/mcp?delay=20" -X POST -d '{}' -o /dev/null -w '%{http_code}' --max-time 60)
ELAPSED=$(($(date +%s) - START))
[[ "$CODE" == "200" && $ELAPSED -ge 20 ]] && pass "20 s blocking call -> 200 after ${ELAPSED}s" || fail "long call" "code=$CODE after ${ELAPSED}s"

# 6. SSE through the LB and Envoy: events arrive as produced and outlive 15 s (times in ms).
START_NS=$(date +%s%N)
TIMES=$(gw "cx-mortgage-orchestrator.$APEX" /api/stream_reasoning_engine -X POST -d '{}' -N --max-time 60 |
  while IFS= read -r line; do [[ "$line" == data:* ]] && echo $((($(date +%s%N) - START_NS) / 1000000)); done)
COUNT=$(echo "$TIMES" | grep -c . || true)
FIRST=$(echo "$TIMES" | head -1)
LAST=$(echo "$TIMES" | tail -1)
if [[ "$COUNT" == "3" ]] && ((FIRST < 5000 && LAST > 15000)); then
  pass "sse: 3 events, first at ${FIRST}ms, last at ${LAST}ms"
else
  fail "sse" "count=$COUNT first=${FIRST}ms last=${LAST}ms"
fi

# 7. Apex path route and health endpoint.
[[ "$(gw "$APEX" /legacy-dms/mcp -X POST -d '{}' | json 'd["path"]')" == "/mcp" ]] && pass "apex /legacy-dms/mcp -> /mcp" || fail "apex route" ""
[[ "$(gw "$APEX" /healthz)" == "ok" ]] && pass "apex /healthz -> ok" || fail "healthz" ""

# 8. Unknown host behind a valid token: Envoy answers 404.
CODE=$(curl -s -o /dev/null -w '%{http_code}' --cacert "$CA" -H "Authorization: Bearer $(token "https://$APEX")" "https://nope.$APEX/")
[[ "$CODE" == "404" ]] && pass "unknown host -> 404" || fail "unknown host" "got $CODE"

# 9. Rate limit per X-API-Key (sandbox runs one instance, so the per-instance limit is exact).
CODES=$(for _ in $(seq 1 $((RATE_LIMIT + 1))); do gw "legacy-dms.$APEX" /mcp -X POST -d '{}' -H "X-API-Key: rl-check-$$" -o /dev/null -w '%{http_code}\n'; done)
[[ "$(echo "$CODES" | tail -1)" == "429" && "$(echo "$CODES" | head -n "$RATE_LIMIT" | sort -u)" == "200" ]] && pass "rate limit: request $((RATE_LIMIT + 1)) -> 429" || fail "rate limit" "$(echo "$CODES" | sort | uniq -c | tr '\n' ' ')"

echo "----"
echo "$PASS passed, $FAIL failed"
exit "$FAIL"
