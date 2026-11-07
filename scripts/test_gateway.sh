#!/usr/bin/env bash
# Probes every catalog host through the internal entry point, from the test VM.
#
#   1. Reads, from Terragrunt outputs (nothing hardcoded):
#        - test VM name / zone / project       (layer-5-workloads/services/test-vm)
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
#
# Usage: scripts/test_gateway.sh <env>     (run from the repo root; `make test-gateway ENV=<env>`)
#   TEST_GATEWAY_QUERY  message sent by `query` probes (default: the `make query` default question)
set -euo pipefail

ENV_NAME="${1:?usage: $0 <env>}"
LIVE_DIR="infrastructure/live/${ENV_NAME}"
L5_DIR="${LIVE_DIR}/layer-5-workloads"

tg_output() { (cd "$1" && terragrunt output -raw "$2" 2>/dev/null) || true; }
tg_output_json() { (cd "$1" && terragrunt output -json "$2" 2>/dev/null) || true; }
die() { echo "❌ $*" >&2; exit 1; }

VM_DIR="${L5_DIR}/services/test-vm"
VM_NAME="$(tg_output "${VM_DIR}" instance_name)"
VM_ZONE="$(tg_output "${VM_DIR}" instance_zone)"
VM_PROJECT="$(tg_output "${VM_DIR}" project_id)"
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

echo "🔎 Probing $(echo "${ROUTES}" | wc -l) host(s) through the entry point ${ENTRY_IP} (*.${DOMAIN}) from ${VM_NAME}..."

REMOTE="$(mktemp)"
trap 'rm -f "${REMOTE}"' EXIT
{
  printf 'ENTRY_IP=%q\n' "${ENTRY_IP}"
  printf 'ROUTES=%q\n' "${ROUTES}"
  printf 'CA_PEM=%q\n' "${CA_PEM}"
  printf 'QUERY_BODY=%q\n' "${QUERY_BODY}"
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

[ "${failures}" -eq 0 ]
REMOTE_EOF
} > "${REMOTE}"

if gcloud compute ssh "${VM_NAME}" --zone="${VM_ZONE}" --project="${VM_PROJECT}" \
  --tunnel-through-iap --quiet --command='bash -s' < "${REMOTE}"; then
  echo "✅ Every route answered through the entry point."
else
  die "Some routes failed through the entry point (see above)."
fi
