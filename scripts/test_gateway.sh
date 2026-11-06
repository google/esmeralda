#!/usr/bin/env bash
# Probes every gateway route host through the internal entry point, from the test VM.
#
#   1. Reads, from Terragrunt outputs (nothing hardcoded):
#        - test VM name / zone / project       (layer-5-workloads/services/test-vm)
#        - entry point IP and private domain   (layer-2-networking)
#        - internal Root CA secret (public)    (layer-3-security)
#        - route hosts + probe kind            (`routes` output of every gateways/* unit)
#   2. Runs the probes on the test VM over `gcloud compute ssh --tunnel-through-iap`. Each request
#      carries an ID token from the VM metadata server (audience https://<host>), verifies TLS
#      against the internal Root CA, and is pinned to the entry point IP with --resolve, so the
#      entry point itself is tested even while DNS caches still hold an older address.
#        mcp  -> POST /mcp (MCP initialize)   expects 2xx
#        a2a  -> GET  /v1/card (agent card)   expects 2xx
#        none -> GET  /                       TLS + routing only, any HTTP status
#      A gateway "no route" answer fails every kind.
#
# Usage: scripts/test_gateway.sh <env>     (run from the repo root; `make test-gateway ENV=<env>`)
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
CA_PEM="$(gcloud secrets versions access latest --secret="${CA_SECRET}")"

# "<host> <probe>" for every route of every deployed gateway (deduplicated by host).
ROUTES=""
for gw_dir in "${L5_DIR}"/gateways/*/; do
  json="$(tg_output_json "${gw_dir}" routes)"
  [ -n "${json}" ] && [ "${json}" != "null" ] || continue
  ROUTES+="$(echo "${json}" | jq -r 'to_entries[] | "\(.value.host) \(.value.probe)"')"$'\n'
done
ROUTES="$(echo "${ROUTES}" | awk 'NF && !seen[$1]++')"
[ -n "${ROUTES}" ] || die "No gateway routes found (routes output of ${L5_DIR}/gateways/*). Deploy a gateway first."

echo "🔎 Probing $(echo "${ROUTES}" | wc -l) host(s) through the entry point ${ENTRY_IP} (*.${DOMAIN}) from ${VM_NAME}..."

REMOTE="$(mktemp)"
trap 'rm -f "${REMOTE}"' EXIT
{
  printf 'ENTRY_IP=%q\n' "${ENTRY_IP}"
  printf 'ROUTES=%q\n' "${ROUTES}"
  printf 'CA_PEM=%q\n' "${CA_PEM}"
  cat <<'REMOTE_EOF'
set -u
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
printf '%s\n' "${CA_PEM}" > "${WORK}/ca.pem"
MCP_INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test-gateway","version":"1.0"}}}'
failures=0

while read -r host probe; do
  [ -n "${host}" ] || continue
  resolved="$(getent hosts "${host}" | awk '{print $1; exit}')"
  token="$(curl -sS --connect-timeout 3 -m 5 -H 'Metadata-Flavor: Google' \
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=https://${host}")" || token=""
  args=(-sS -o "${WORK}/body" -w '%{http_code}' --connect-timeout 5 -m 20 --cacert "${WORK}/ca.pem"
    --resolve "${host}:443:${ENTRY_IP}" -H "Authorization: Bearer ${token}")
  case "${probe}" in
    mcp) code="$(curl "${args[@]}" -X POST -H 'Content-Type: application/json' \
           -H 'Accept: application/json, text/event-stream' -d "${MCP_INIT}" "https://${host}/mcp" 2>"${WORK}/err")" ;;
    a2a) code="$(curl "${args[@]}" "https://${host}/v1/card" 2>"${WORK}/err")" ;;
    *)   code="$(curl "${args[@]}" "https://${host}/" 2>"${WORK}/err")" ;;
  esac
  code="${code:-000}"

  ok=0
  case "${probe}" in
    mcp | a2a) [[ "${code}" == 2?? ]] && ok=1 ;;
    *) [ "${code}" != "000" ] && ok=1 ;;
  esac
  grep -qi 'no Route matched' "${WORK}/body" 2>/dev/null && ok=0

  note=""
  [ -n "${resolved}" ] && [ "${resolved}" != "${ENTRY_IP}" ] && note=" (DNS still resolves to ${resolved})"
  if [ "${ok}" = 1 ]; then
    printf '  ✅ %-50s %-5s HTTP %s%s\n' "${host}" "${probe}" "${code}" "${note}"
  else
    printf '  ❌ %-50s %-5s HTTP %s%s %s\n' "${host}" "${probe}" "${code}" "${note}" \
      "$(cat "${WORK}/err" "${WORK}/body" 2>/dev/null | head -c 300 | tr '\n' ' ')"
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
