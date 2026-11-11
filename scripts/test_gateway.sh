#!/usr/bin/env bash
# Probes every catalog host through the internal entry point, from the test VM.
#
#   1. Reads, from Terragrunt outputs (nothing hardcoded):
#        - test VM name / zone / project       (layer-5-workloads/test-vm, fallback services/test-vm)
#        - entry point IP and private domain   (layer-2-networking)
#        - internal Root CA secret (public)    (layer-3-security)
#        - catalog hosts: probe, token kind    (`routes` output of layer-5-workloads/entrypoint,
#          and backend                          built from env.yaml catalog + gateways)
#   2. Runs the probes on the test VM over `gcloud compute ssh --tunnel-through-iap`. Each request
#      carries a token from the VM metadata server of the kind the host takes: an ID token
#      (audience https://<host>) for Cloud Run and adapters, an OAuth2 access token for agents served
#      by direct (Agent Runtime). It verifies TLS against the internal Root CA and is pinned to the
#      entry point IP with --resolve, so the entry point itself is tested even while DNS caches still
#      hold an older address.
#        mcp   -> POST /mcp (MCP initialize)              expects 2xx
#        a2a   -> GET  /v1/card (agent card)              expects 2xx
#        query -> POST /api/stream_reasoning_engine with   expects HTTP 200 and a stream with model
#                 an ADK async_stream_query body (same      text and no error event; prints an
#                 path + body as `esmeralda query --url`;   answer excerpt and the elapsed time
#                 timeout 120 s)
#        none  -> GET  /                                   TLS + routing only, any HTTP status
#      A gateway "no route" answer fails every kind. Non-query probes time out after 20 s.
#   3. Verified attribution (catalog `attribution`):
#        unregistered caller -> the layer-3 probe identity (in no catalog entry) calls every mcp/a2a
#                               host with its own token                     expects 403 (IAM rejects
#                                                                           it first; should it reach
#                                                                           a caller guard: 403 under
#                                                                           deny, 2xx unattributed
#                                                                           under flag, with a warning)
#        declared mismatch   -> the test VM declares another team's cost centre on every MCP host
#                               whose caller guard is on                    expects 403 (reject) or
#                                                                           2xx billed to its team
#                                                                           (bill_own_team)
#
# Usage: scripts/test_gateway.sh <env>     (run from the repo root; `make test-gateway ENV=<env>`)
#   TEST_GATEWAY_QUERY  message sent by `query` probes (default: the `make query` default question)
set -euo pipefail

ENV_NAME="${1:?usage: $0 <env>}"
LIVE_DIR="infrastructure/live/${ENV_NAME}"
L5_DIR="${LIVE_DIR}/layer-5-workloads"

tg_output() { (cd "$1" && terragrunt output -raw "$2" 2>/dev/null) || true; }
tg_output_json() { (cd "$1" && terragrunt output -json "$2" 2>/dev/null) || true; }
state_output() { jq -r --arg k "$2" '.outputs[$k].value // empty' "$1" 2>/dev/null || true; }
die() { echo "❌ $*" >&2; exit 1; }

# Prefer the flattened layer-5-workloads/test-vm unit; fall back for one release to the legacy
# layer-5-workloads/services/test-vm directory or local state file.
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
[ -n "${VM_NAME}" ] && [ -n "${VM_ZONE}" ] || die "No test VM outputs in ${VM_DIR}. Deploy it first (make deploy-workloads ENV=${ENV_NAME})."
[ -n "${VM_PROJECT}" ] || die "No project_id output in ${VM_DIR}. Re-apply the test-vm unit to publish it."

ENTRY_IP="$(tg_output "${LIVE_DIR}/layer-2-networking" entrypoint_address)"
DOMAIN="$(tg_output "${LIVE_DIR}/layer-2-networking" entrypoint_domain)"
[ -n "${ENTRY_IP}" ] && [ -n "${DOMAIN}" ] || die "No entry point address in layer 2. Run make deploy-networking ENV=${ENV_NAME}."

CA_SECRET="$(tg_output "${LIVE_DIR}/layer-3-security" internal_ca_secret_id)"
[ -n "${CA_SECRET}" ] || die "No internal_ca_secret_id output in layer 3."
# projects/<project>/secrets/<name>: gcloud wants the short name plus --project
CA_SECRET_PROJECT="$(echo "${CA_SECRET}" | cut -d/ -f2)"
CA_PEM="$(gcloud secrets versions access latest --secret="${CA_SECRET##*/}" --project="${CA_SECRET_PROJECT}")"

# "<host> <probe> <token> <backend>" for every catalog host (entry point routes output).
ROUTES_JSON="$(tg_output_json "${L5_DIR}/entrypoint" routes)"
[ -n "${ROUTES_JSON}" ] && [ "${ROUTES_JSON}" != "null" ] ||
  die "No routes output in ${L5_DIR}/entrypoint. Deploy the entry point first (make deploy-entrypoint ENV=${ENV_NAME})."
ROUTES="$(echo "${ROUTES_JSON}" | jq -r 'to_entries[] | "\(.value.host) \(.value.probe) \(.value.token) \(.value.backend)"')"
[ -n "${ROUTES}" ] || die "The entry point serves no host (catalog in ${LIVE_DIR}/env.yaml)."

# Body of query probes: the same ADK request `esmeralda query --url` sends.
DEFAULT_QUERY="Can you verify Julian Sterling's income?"
QUERY_MESSAGE="${TEST_GATEWAY_QUERY:-${DEFAULT_QUERY}}"
QUERY_BODY="$(jq -cn --arg m "${QUERY_MESSAGE}" \
  '{class_method: "async_stream_query", input: {user_id: "test-gateway", message: $m}}')"

# Verified attribution probes (catalog/.resolved/<env>.json `attribution`):
#   - unregistered caller: the probe identity (layer 3 probe_unregistered_sa_email, in no catalog
#     entry) calls every mcp/a2a host and must get HTTP 403;
#   - declared mismatch: the test VM declares a cost centre of another team on every MCP host whose
#     caller guard is on: 403 under declared_mismatch: reject, 2xx billed to its own team
#     (X-Esmeralda-Billed-Team) under bill_own_team.
RESOLVED="catalog/.resolved/${ENV_NAME}.json"
PROBE_SA="$(tg_output "${LIVE_DIR}/layer-3-security" probe_unregistered_sa_email)"
[ "${PROBE_SA}" = "null" ] && PROBE_SA=""
MISMATCH_MODE="$(jq -r '.attribution.policy.declared_mismatch // "reject"' "${RESOLVED}" 2>/dev/null || echo reject)"
UNMAPPED_MODE="$(jq -r '.attribution.policy.unmapped_caller // "deny"' "${RESOLVED}" 2>/dev/null || echo deny)"
VM_TEAM="$(jq -r '[.attribution.principal_teams[]? | select(.caller == "test-vm") | .team][0] // empty' "${RESOLVED}" 2>/dev/null || true)"
FOREIGN_CC="$(jq -r '([.attribution.principal_teams[]? | select(.caller != "test-vm") | .cost_centers[]]
  - [.attribution.principal_teams[]? | select(.caller == "test-vm") | .cost_centers[]])[0] // empty' "${RESOLVED}" 2>/dev/null || true)"
GUARDED_HOSTS=""
while read -r host probe _kind _backend; do
  [ "${probe}" = "mcp" ] || continue
  if [ "$(tg_output "${L5_DIR}/services/${host%%.*}" caller_guard)" = "true" ]; then
    GUARDED_HOSTS="${GUARDED_HOSTS} ${host}"
  fi
done <<< "${ROUTES}"

echo "🔎 Probing $(echo "${ROUTES}" | wc -l) host(s) through the entry point ${ENTRY_IP} (*.${DOMAIN}) from ${VM_NAME}..."

REMOTE="$(mktemp)"
trap 'rm -f "${REMOTE}"' EXIT
{
  printf 'ENTRY_IP=%q\n' "${ENTRY_IP}"
  printf 'ROUTES=%q\n' "${ROUTES}"
  printf 'CA_PEM=%q\n' "${CA_PEM}"
  printf 'QUERY_BODY=%q\n' "${QUERY_BODY}"
  printf 'PROBE_SA=%q\n' "${PROBE_SA}"
  printf 'MISMATCH_MODE=%q\n' "${MISMATCH_MODE}"
  printf 'UNMAPPED_MODE=%q\n' "${UNMAPPED_MODE}"
  printf 'VM_TEAM=%q\n' "${VM_TEAM}"
  printf 'FOREIGN_CC=%q\n' "${FOREIGN_CC}"
  printf 'GUARDED_HOSTS=%q\n' "${GUARDED_HOSTS}"
  cat <<'REMOTE_EOF'
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
    token="$(curl -sS --connect-timeout 3 -m 5 -H 'Metadata-Flavor: Google' \
      "${METADATA}/identity?audience=https://${host}")" || token=""
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
      "${METADATA}/identity?audience=https://${host}")" || token=""
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
REMOTE_EOF
} > "${REMOTE}"

if gcloud compute ssh "${VM_NAME}" --zone="${VM_ZONE}" --project="${VM_PROJECT}" \
  --tunnel-through-iap --quiet --command='bash -s' < "${REMOTE}"; then
  echo "✅ Every route answered through the entry point."
else
  die "Some routes failed through the entry point (see above)."
fi
