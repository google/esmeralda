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
# Gateway conformance probes, run ON the test VM (`esmeralda verify`, `make test-gateway`).
#
# `esmeralda.ops.runtime.test_gateway` resolves everything on the workstation (test VM, entry point,
# internal CA, the entry point's `routes` output, attribution policy) and sends this file over
# `gcloud compute ssh --tunnel-through-iap` on stdin, after these variables:
#   ENTRY_IP       entry point IP (every request is pinned to it with --resolve)
#   ROUTES         one "<host> <probe> <token> <backend>" line per catalog host
#   CA_PEM         internal Root CA certificate (PEM)
#   QUERY_BODY     body of `query` probes (the ADK request `esmeralda query --url` sends)
#   PROBE_SA       unregistered-caller probe identity (empty: skip the unregistered-caller probes)
#   MISMATCH_MODE  catalog attribution policy declared_mismatch (reject | bill_own_team)
#   UNMAPPED_MODE  catalog attribution policy unmapped_caller (deny | flag)
#   VM_TEAM        team of the test VM caller
#   FOREIGN_CC     a cost centre of another team (empty: skip the declared-mismatch probes)
#   GUARDED_HOSTS  space-separated MCP hosts whose caller guard is on
#
# Each request carries a token from the VM metadata server of the kind the host takes: an ID token
# (audience https://<host>) for Cloud Run and adapters, an OAuth2 access token for agents served by
# direct (Agent Runtime). TLS is verified against the internal Root CA.
#   mcp   -> POST /mcp (MCP initialize)              expects 2xx
#   a2a   -> GET  /v1/card (agent card)              expects 2xx
#   query -> POST /api/stream_reasoning_engine       expects HTTP 200 and a stream with model text and
#            (timeout 120 s)                         no error event; prints an answer excerpt + time
#   none  -> GET  /                                  TLS + routing only, any HTTP status
# A gateway "no route" answer fails every kind. Non-query probes time out after 20 s. Then the
# verified attribution probes: the unregistered caller must get 403 on every mcp/a2a host, and a
# declared cost centre of another team on a guarded MCP host gets 403 (reject) or 2xx billed to the
# VM's own team (bill_own_team).
#
# One line per probe (`✅`/`❌ <host> <probe> ... HTTP <code>`), parsed by
# `esmeralda.ops.runtime.parse_gateway_output`; exits non-zero if any probe failed.
set -u
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
printf '%s\n' "${CA_PEM}" > "${WORK}/ca.pem"
MCP_INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test-gateway","version":"1.0"}}}'
failures=0

# Checks an ADK :streamQuery body (SSE or newline-delimited JSON): prints "<answer excerpt>" and exits 0 when at least one
# event has non-empty model text and none is an error; prints the reason and exits 1 otherwise.
check_stream() {
  python3 - "$1" <<'PY'
import json, sys
raw = open(sys.argv[1], encoding="utf-8", errors="replace").read()
try:  # errors come back as one JSON document (object or array), not as SSE lines
    doc = json.loads(raw)
    events = doc if isinstance(doc, list) else [doc]
except ValueError:
    events = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
texts, errors = [], []
for e in events:
    if not isinstance(e, dict):
        continue
    if e.get("error") or e.get("error_code"):
        errors.append(e.get("error_message") or e.get("error"))
        continue
    if e.get("partial"):
        continue
    for part in (e.get("content") or {}).get("parts") or []:
        text = part.get("text") if isinstance(part, dict) else None
        if isinstance(text, str) and text.strip():
            texts.append(text)
if errors:
    print("error event: " + json.dumps(errors[0])[:200])
    sys.exit(1)
if not texts:
    print("no model text in %d event(s)" % len(events))
    sys.exit(1)
print(" ".join(texts[-1].split())[:120])
PY
}

METADATA="http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default"
while read -r host probe kind backend; do
  [ -n "${host}" ] || continue
  resolved="$(getent hosts "${host}" | awk '{print $1; exit}')"
  if [ "${kind}" = "access" ]; then  # Agent Runtime: OAuth2 access token
    token="$(curl -sS --connect-timeout 3 -m 5 -H 'Metadata-Flavor: Google' "${METADATA}/token" |
      python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')" || token=""
  else                               # Cloud Run IAM (direct MCP hosts, adapters): ID token for the host
                                     # (format=full: with the email claim the caller guard maps)
    token="$(curl -sS --connect-timeout 3 -m 5 -H 'Metadata-Flavor: Google' \
      "${METADATA}/identity?audience=https://${host}&format=full")" || token=""
  fi
  args=(-sS -o "${WORK}/body" -w '%{http_code}' --connect-timeout 5 -m 20 --cacert "${WORK}/ca.pem"
    --resolve "${host}:443:${ENTRY_IP}" -H "Authorization: Bearer ${token}")
  started="$(date +%s)"
  case "${probe}" in
    mcp) code="$(curl "${args[@]}" -X POST -H 'Content-Type: application/json' \
           -H 'Accept: application/json, text/event-stream' -d "${MCP_INIT}" "https://${host}/mcp" 2>"${WORK}/err")" ;;
    a2a) code="$(curl "${args[@]}" "https://${host}/v1/card" 2>"${WORK}/err")" ;;
    query) code="$(curl "${args[@]}" -m 120 -X POST -H 'Content-Type: application/json' \
             -d "${QUERY_BODY}" "https://${host}/api/stream_reasoning_engine" 2>"${WORK}/err")" ;;
    *)   code="$(curl "${args[@]}" "https://${host}/" 2>"${WORK}/err")" ;;
  esac
  code="${code:-000}"
  elapsed="$(($(date +%s) - started))s"

  ok=0
  detail=""
  case "${probe}" in
    mcp | a2a) [[ "${code}" == 2?? ]] && ok=1 ;;
    query)
      if [ "${code}" = "200" ]; then
        if detail="$(check_stream "${WORK}/body" 2>&1)"; then ok=1; detail="${elapsed} \"${detail}\""; fi
      fi ;;
    *) [ "${code}" != "000" ] && ok=1 ;;
  esac
  grep -qi 'no Route matched' "${WORK}/body" 2>/dev/null && ok=0

  note=""
  [ -n "${resolved}" ] && [ "${resolved}" != "${ENTRY_IP}" ] && note=" (DNS still resolves to ${resolved})"
  if [ "${ok}" = 1 ]; then
    printf '  ✅ %-45s %-5s %-6s %-7s HTTP %s%s %s\n' "${host}" "${probe}" "${kind}" "${backend}" "${code}" "${note}" "${detail}"
  else
    [ -n "${detail}" ] || detail="$(cat "${WORK}/err" "${WORK}/body" 2>/dev/null | head -c 300 | tr '\n' ' ')"
    printf '  ❌ %-45s %-5s %-6s %-7s HTTP %s%s %s\n' "${host}" "${probe}" "${kind}" "${backend}" "${code}" "${note}" "${detail}"
    failures=$((failures + 1))
  fi
done <<< "${ROUTES}"

# --- Verified attribution: unregistered caller (expects 403 on every mcp/a2a host) -------------
IAMCREDS="https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts"
if [ -n "${PROBE_SA}" ]; then
  echo "🔒 Unregistered caller ${PROBE_SA%%@*} (in no catalog entry): every host must answer 403"
  vm_access="$(curl -sS --connect-timeout 3 -m 5 -H 'Metadata-Flavor: Google' "${METADATA}/token" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')" || vm_access=""
  while read -r host probe kind backend; do
    case "${probe}" in mcp | a2a) ;; *) continue ;; esac
    if [ "${kind}" = "access" ]; then
      ptoken="$(curl -sS -m 10 -X POST -H "Authorization: Bearer ${vm_access}" -H 'Content-Type: application/json' \
        -d '{"scope":["https://www.googleapis.com/auth/cloud-platform"]}' "${IAMCREDS}/${PROBE_SA}:generateAccessToken" |
        python3 -c 'import json,sys; print(json.load(sys.stdin).get("accessToken",""))' 2>/dev/null)" || ptoken=""
    else
      ptoken="$(curl -sS -m 10 -X POST -H "Authorization: Bearer ${vm_access}" -H 'Content-Type: application/json' \
        -d "{\"audience\":\"https://${host}\",\"includeEmail\":true}" "${IAMCREDS}/${PROBE_SA}:generateIdToken" |
        python3 -c 'import json,sys; print(json.load(sys.stdin).get("token",""))' 2>/dev/null)" || ptoken=""
    fi
    if [ -z "${ptoken}" ]; then
      printf '  ❌ %-45s %-5s unregistered: could not mint a probe token (test VM needs token creator on the probe identity)\n' "${host}" "${probe}"
      failures=$((failures + 1))
      continue
    fi
    args=(-sS -o "${WORK}/body" -w '%{http_code}' --connect-timeout 5 -m 20 --cacert "${WORK}/ca.pem"
      --resolve "${host}:443:${ENTRY_IP}" -H "Authorization: Bearer ${ptoken}")
    if [ "${probe}" = "mcp" ]; then
      code="$(curl "${args[@]}" -D "${WORK}/headers" -X POST -H 'Content-Type: application/json' \
        -H 'Accept: application/json, text/event-stream' -d "${MCP_INIT}" "https://${host}/mcp" 2>/dev/null)"
    else
      code="$(curl "${args[@]}" -D "${WORK}/headers" "https://${host}/v1/card" 2>/dev/null)"
    fi
    billed="$(grep -i '^x-esmeralda-billed-team:' "${WORK}/headers" 2>/dev/null | head -1 | cut -d: -f2 | tr -d ' \r')"
    guarded=0
    case " ${GUARDED_HOSTS} " in *" ${host} "*) guarded=1 ;; esac
    # IAM / the Agent Gateway reject the probe before any guard (it holds no grant): 403 in both modes.
    # Only if a host let it through to its caller guard does the effective policy decide: 403 under
    # deny; under flag the call is allowed but must stay unattributed (no billed team).
    if [ "${code:-000}" = "403" ]; then
      printf '  ✅ %-45s %-5s unregistered caller denied: HTTP 403\n' "${host}" "${probe}"
    elif [ "${guarded}" = 1 ] && [ "${UNMAPPED_MODE}" = "flag" ] && [[ "${code:-000}" == 2?? ]] && [ -z "${billed}" ]; then
      printf '  ⚠️  %-45s %-5s unregistered caller reached the guard: allowed, unattributed (unmapped_caller: flag). Its IAM grant should not exist\n' "${host}" "${probe}"
    else
      printf '  ❌ %-45s %-5s unregistered caller expected HTTP 403%s, got %s%s\n' "${host}" "${probe}" \
        "$([ "${guarded}" = 1 ] && [ "${UNMAPPED_MODE}" = "flag" ] && echo ' (or 2xx unattributed under flag)')" "${code:-000}" "${billed:+ billed to ${billed}}"
      failures=$((failures + 1))
    fi
  done <<< "${ROUTES}"
fi

# --- Verified attribution: declared cost centre of another team (guarded MCP hosts) -------------
if [ -n "${GUARDED_HOSTS// /}" ] && [ -n "${FOREIGN_CC}" ]; then
  echo "🧾 Declared mismatch (test VM declares ${FOREIGN_CC}; declared_mismatch: ${MISMATCH_MODE})"
  for host in ${GUARDED_HOSTS}; do
    token="$(curl -sS --connect-timeout 3 -m 5 -H 'Metadata-Flavor: Google' \
      "${METADATA}/identity?audience=https://${host}&format=full")" || token=""
    code="$(curl -sS -o "${WORK}/body" -D "${WORK}/headers" -w '%{http_code}' --connect-timeout 5 -m 20 \
      --cacert "${WORK}/ca.pem" --resolve "${host}:443:${ENTRY_IP}" -H "Authorization: Bearer ${token}" \
      -H "X-Esmeralda-Cost-Center: ${FOREIGN_CC}" -X POST -H 'Content-Type: application/json' \
      -H 'Accept: application/json, text/event-stream' -d "${MCP_INIT}" "https://${host}/mcp" 2>/dev/null)"
    billed="$(grep -i '^x-esmeralda-billed-team:' "${WORK}/headers" 2>/dev/null | head -1 | cut -d: -f2 | tr -d ' \r')"
    ok=0
    if [ "${MISMATCH_MODE}" = "reject" ]; then
      [ "${code:-000}" = "403" ] && ok=1
      expected="HTTP 403"
    else
      [[ "${code:-000}" == 2?? ]] && [ "${billed}" = "${VM_TEAM}" ] && ok=1
      expected="2xx billed to ${VM_TEAM}"
    fi
    if [ "${ok}" = 1 ]; then
      printf '  ✅ %-45s mcp   declared %s: HTTP %s%s\n' "${host}" "${FOREIGN_CC}" "${code}" "${billed:+ billed to ${billed}}"
    else
      printf '  ❌ %-45s mcp   declared %s: expected %s, got HTTP %s%s\n' "${host}" "${FOREIGN_CC}" "${expected}" "${code:-000}" "${billed:+ billed to ${billed}}"
      failures=$((failures + 1))
    fi
  done
fi

[ "${failures}" -eq 0 ]
