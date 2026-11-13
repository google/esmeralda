#!/usr/bin/env bash
# infrastructure/modules/_shared/scripts/grant_iap_egress.sh
#
# Grants roles/iap.egressor on the entries of the central Agent Registry, so agents may egress
# through the Agent Gateway to exactly the destinations each one is allowed to reach.
#
# Every grant is a policy on one registry entry. The registry root never holds a binding: a root
# binding is inherited by every entry (system endpoints, external endpoints, agents, MCP servers)
# and IAM allow policies only add, so a root binding would open every entry it matches to its
# members whatever the per-entry policies say. The script refuses to write one; it only ever
# clears the root policy. A new entry therefore starts closed (no policy, nobody) until it is granted.
#
# Modes (IAP_MODE):
#   system-endpoints  Layer 4 governance. Writes IAP_MEMBERS_JSON (the agent projects'
#                     principalSets and platform service agents) on every system endpoint entry in
#                     SYSTEM_ENDPOINT_IDS_JSON (Google APIs), reads each policy back and verifies it,
#                     and only then clears the registry root policy. Agents keep their Google API
#                     egress throughout: the per-entry grants exist before the root binding (written
#                     by earlier versions) goes away, and nothing is cleared if any entry fails.
#   targets           Layer 5 iap-egress unit. Writes the per-target lists of
#                     IAP_TARGET_POLICIES_JSON {"agents": {...}, "endpoints": {...}, "mcp_servers": {...}}
#                     (compiled from the service catalog) on each agent, external endpoint and MCP
#                     server entry, then reads them back and verifies them. An empty list writes an
#                     empty policy (nobody). Refuses to grant endpoint entries while the registry
#                     root still holds a binding (apply layer 4 first).
#   check-root        Exits non-zero if the registry root holds any binding. Run before new endpoint
#                     entries are registered: a root binding left by an older layer 4 would open them.
#
# Idempotent: a policy that already matches is not rewritten. Entries are processed by a bounded
# pool of IAP_CONCURRENCY workers (default 8); every gcloud call is retried with exponential backoff
# (IAP_MAX_ATTEMPTS, default 6; IAP_RETRY_DELAY_SECONDS, default 2), which also absorbs quota errors
# (429 / RESOURCE_EXHAUSTED), and each worker spaces its writes by IAP_WRITE_PAUSE_SECONDS
# (default 0.2). Each entry is written, then read back; any failure fails the run (and, in
# system-endpoints mode, leaves the registry root untouched).
#
# Required env: GOVERNANCE_PROJECT_ID (project of the Agent Registry), REGION (registry location),
#               IAP_MODE, and the inputs of the mode above.
#
# Rollback of the root binding removal (layer 4): see docs/src/3-agentops-and-lifecycle/
# 01-central-agent-gateway.md, "Rolling back the per-entry system endpoint grants".
set -euo pipefail

: "${GOVERNANCE_PROJECT_ID:?}" "${REGION:?}" "${IAP_MODE:?IAP_MODE must be system-endpoints, targets or check-root}"

if [[ ! "${GOVERNANCE_PROJECT_ID}" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]]; then
  echo "❌ GOVERNANCE_PROJECT_ID must be a project id" >&2
  exit 1
fi
if [[ ! "${REGION}" =~ ^[a-z0-9-]+$ ]]; then
  echo "❌ REGION must be a plain location id (e.g. us-central1)" >&2
  exit 1
fi

IAP_ROLE="roles/iap.egressor"
MAX_ATTEMPTS="${IAP_MAX_ATTEMPTS:-6}"
RETRY_DELAY="${IAP_RETRY_DELAY_SECONDS:-2}"
WRITE_PAUSE="${IAP_WRITE_PAUSE_SECONDS:-0.2}"
CONCURRENCY="${IAP_CONCURRENCY:-8}"
if [[ ! "${CONCURRENCY}" =~ ^[1-9][0-9]?$ ]]; then
  echo "❌ IAP_CONCURRENCY must be an integer between 1 and 99" >&2
  exit 1
fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

# --- gcloud helpers ---------------------------------------------------------------------------

# Runs "$@" until it succeeds, up to MAX_ATTEMPTS times, doubling the pause between attempts
# (plus up to one second of jitter, so parallel workers hitting a quota don't retry in lockstep).
with_retry() {
  local attempt=1 delay="${RETRY_DELAY}"
  until "$@"; do
    if ((attempt >= MAX_ATTEMPTS)); then
      return 1
    fi
    echo "  ↻ attempt ${attempt} failed (quota or transient error?), retrying in ${delay}s" >&2
    sleep "${delay}.$((RANDOM % 10))"
    delay=$((delay * 2))
    attempt=$((attempt + 1))
  done
}

# iap_get <out-file> [--agent=<uid>|--endpoint=<uid>|--mcp-server=<uid>]  (no flag: registry root)
iap_get() {
  local out="$1"
  shift
  gcloud iap web get-iam-policy \
    --project="${GOVERNANCE_PROJECT_ID}" \
    --resource-type=agent-registry \
    --region="${REGION}" \
    --format=json "$@" >"${out}"
}

# iap_set <policy-file> [target flag]  (no flag: registry root)
iap_set() {
  local policy="$1"
  shift
  gcloud iap web set-iam-policy "${policy}" \
    --project="${GOVERNANCE_PROJECT_ID}" \
    --resource-type=agent-registry \
    --region="${REGION}" \
    --quiet "$@" >/dev/null
}

# Comparable form of a policy: its bindings, members sorted, etag and version dropped.
normalize() {
  jq -cS '[.bindings[]? | {role, members: ((.members // []) | sort), condition: (.condition // null)}
           | select(.members | length > 0)] | sort_by(.role)' "$1"
}

# Desired per-entry policy for a member list (empty list: no binding, nobody).
desired_policy() {
  local members_json="$1" out="$2"
  jq -n --arg role "${IAP_ROLE}" --argjson members "${members_json}" \
    'if ($members | length) > 0 then {bindings: [{role: $role, members: $members}]} else {bindings: []} end' >"${out}"
}

# Writes the policy of one entry unless it already matches. Never writes the registry root.
# <tag> names this entry's temporary files (entries run in parallel).
apply_entry_policy() {
  local members_json="$1" flag="$2" tag="${3:-entry}"
  if [[ "${flag}" != --agent=?* && "${flag}" != --endpoint=?* && "${flag}" != --mcp-server=?* ]]; then
    echo "❌ Refusing to write a registry-root IAP binding: per-entry policies need --agent, --endpoint or --mcp-server" >&2
    return 1
  fi
  local want="${WORK_DIR}/want-${tag}.json" have="${WORK_DIR}/have-${tag}.json"
  desired_policy "${members_json}" "${want}"
  if with_retry iap_get "${have}" "${flag}" && [[ "$(normalize "${have}")" == "$(normalize "${want}")" ]]; then
    echo "     ${flag#--}: unchanged"
    return 0
  fi
  with_retry iap_set "${want}" "${flag}" || { echo "❌ Could not write the IAP policy on ${flag}" >&2; return 1; }
  sleep "${WRITE_PAUSE}"
  echo "     ${flag#--}: written"
}

# Reads one entry back; prints a mismatch and returns 1 if it is not exactly the desired list.
verify_entry_policy() {
  local members_json="$1" flag="$2" tag="${3:-entry}"
  local want="${WORK_DIR}/want-${tag}.json" have="${WORK_DIR}/have-${tag}.json"
  desired_policy "${members_json}" "${want}"
  if ! with_retry iap_get "${have}" "${flag}"; then
    echo "  ❌ ${flag}: could not read the IAP policy back" >&2
    return 1
  fi
  if [[ "$(normalize "${have}")" != "$(normalize "${want}")" ]]; then
    echo "  ❌ ${flag}: IAP policy is $(normalize "${have}"), expected $(normalize "${want}")" >&2
    return 1
  fi
}

# One entry: write it (if needed), then read it back. Records ok / fail for the pool.
process_entry() {
  local members_json="$1" flag="$2" tag="$3"
  if apply_entry_policy "${members_json}" "${flag}" "${tag}" && verify_entry_policy "${members_json}" "${flag}" "${tag}"; then
    echo ok >"${WORK_DIR}/result-${tag}"
  else
    echo fail >"${WORK_DIR}/result-${tag}"
  fi
}

# run_entries <file of "members-json<TAB>flag" lines>: processes every entry with at most
# CONCURRENCY workers; returns 1 unless every entry was written and verified.
run_entries() {
  local list="$1" line i=0 failed=0
  while IFS= read -r line; do
    [[ -n "${line}" ]] || continue
    process_entry "${line%%$'\t'*}" "${line#*$'\t'}" "${i}" &
    i=$((i + 1))
    while (($(jobs -rp | wc -l) >= CONCURRENCY)); do
      wait -n || true
    done
  done <"${list}"
  wait || true
  local n
  for ((n = 0; n < i; n++)); do
    [[ "$(cat "${WORK_DIR}/result-${n}" 2>/dev/null)" == ok ]] || failed=$((failed + 1))
  done
  if ((failed)); then
    echo "❌ ${failed} of ${i} entries could not be written and verified" >&2
    return 1
  fi
  echo "🔎 ${i} entries written (where needed) and verified."
}

root_has_bindings() {
  local have="${WORK_DIR}/root.json"
  with_retry iap_get "${have}" || { echo "❌ Could not read the registry root IAP policy" >&2; exit 1; }
  [[ "$(normalize "${have}")" != "[]" ]]
}

# Clears the registry root policy (the only root write this script makes).
clear_root() {
  if ! root_has_bindings; then
    echo "🔒 Registry root holds no IAP binding."
    return 0
  fi
  local empty="${WORK_DIR}/empty.json"
  echo '{"bindings": []}' >"${empty}"
  if [[ "$(normalize "${empty}")" != "[]" ]]; then
    echo "❌ Refusing to write a registry-root IAP binding" >&2
    exit 1
  fi
  with_retry iap_set "${empty}" || { echo "❌ Could not clear the registry root IAP policy" >&2; exit 1; }
  if root_has_bindings; then
    echo "❌ The registry root IAP policy still holds a binding after clearing it" >&2
    exit 1
  fi
  echo "🔒 Registry root IAP binding removed: every entry is now governed by its own policy only."
}

# --- registry index ---------------------------------------------------------------------------

# "<service_id> <collection> <uid>" for every entry of the registry (one paginated listing).
# <collection> is the IAP resource collection: endpoints, agents or mcpServers.
INDEX_FILE="${WORK_DIR}/index.tsv"
load_index() {
  local token page="" url resp="${WORK_DIR}/page.json"
  token="$(gcloud auth print-access-token)"
  : >"${INDEX_FILE}"
  while :; do
    url="https://agentregistry.googleapis.com/v1alpha/projects/${GOVERNANCE_PROJECT_ID}/locations/${REGION}/services?pageSize=500"
    [[ -z "${page}" ]] || url="${url}&pageToken=${page}"
    if ! with_retry curl -sS --fail -o "${resp}" -H "Authorization: Bearer ${token}" "${url}"; then
      echo "❌ Could not list the Agent Registry services of ${GOVERNANCE_PROJECT_ID}/${REGION}" >&2
      exit 1
    fi
    jq -r '.services[]? | select(.registryResource)
           | [(.name | split("/") | last), (.registryResource | split("/") | .[-2]), (.registryResource | split("/") | last)]
           | @tsv' "${resp}" >>"${INDEX_FILE}"
    page="$(jq -r '.nextPageToken // empty' "${resp}")"
    [[ -n "${page}" ]] || break
  done
}

# Target flag of a registered service: --endpoint=/--agent=/--mcp-server=<uid>. Fails when the
# service is not registered, or not of the expected collection.
entry_flag() {
  local service_id="$1" expected="$2" collection uid
  read -r collection uid < <(awk -F '\t' -v id="${service_id}" '$1 == id {print $2 "\t" $3; exit}' "${INDEX_FILE}") || true
  if [[ -z "${uid:-}" ]]; then
    echo "❌ ${service_id} is not registered in the Agent Registry of ${GOVERNANCE_PROJECT_ID}/${REGION}" >&2
    exit 1
  fi
  if [[ "${collection}" != "${expected}" ]]; then
    echo "❌ ${service_id} is a registry ${collection} entry, expected ${expected}" >&2
    exit 1
  fi
  case "${collection}" in
    endpoints) echo "--endpoint=${uid}" ;;
    agents) echo "--agent=${uid}" ;;
    mcpServers) echo "--mcp-server=${uid}" ;;
  esac
}

# --- modes ------------------------------------------------------------------------------------

mode_system_endpoints() {
  : "${IAP_MEMBERS_JSON:?}" "${SYSTEM_ENDPOINT_IDS_JSON:?}"
  jq -e 'type == "array" and length > 0' >/dev/null <<<"${IAP_MEMBERS_JSON}" ||
    { echo "❌ IAP_MEMBERS_JSON must be a non-empty JSON list" >&2; exit 1; }
  jq -e 'type == "array" and length > 0' >/dev/null <<<"${SYSTEM_ENDPOINT_IDS_JSON}" ||
    { echo "❌ SYSTEM_ENDPOINT_IDS_JSON must be a non-empty JSON list" >&2; exit 1; }
  load_index

  local ids=() flags=() id flag
  mapfile -t ids < <(jq -r '.[]' <<<"${SYSTEM_ENDPOINT_IDS_JSON}")
  for id in "${ids[@]}"; do
    flag="$(entry_flag "${id}" endpoints)"
    flags+=("${flag}")
  done

  echo "🎯 System endpoint IAP egress policies (${#ids[@]} entries, ${CONCURRENCY} workers) on ${GOVERNANCE_PROJECT_ID}/${REGION}..."
  local list="${WORK_DIR}/system-entries.tsv" members
  members="$(jq -c . <<<"${IAP_MEMBERS_JSON}")"
  : >"${list}"
  for flag in "${flags[@]}"; do
    printf '%s\t%s\n' "${members}" "${flag}" >>"${list}"
  done
  if ! run_entries "${list}"; then
    echo "❌ System endpoint policies are not all in place; the registry root policy was left untouched" >&2
    exit 1
  fi
  clear_root
}

mode_targets() {
  : "${IAP_TARGET_POLICIES_JSON:?}"
  jq -e 'type == "object"' >/dev/null <<<"${IAP_TARGET_POLICIES_JSON}" ||
    { echo "❌ IAP_TARGET_POLICIES_JSON must be a JSON object" >&2; exit 1; }
  if jq -e '(.endpoints // {}) | length > 0' >/dev/null <<<"${IAP_TARGET_POLICIES_JSON}" && root_has_bindings; then
    echo "❌ The registry root still holds an IAP binding, which would open the endpoint entries to its members." >&2
    echo "   Apply layer 4 governance first (it moves that grant onto each system endpoint entry)." >&2
    exit 1
  fi
  load_index

  echo "🎯 Per-target IAP egress policies on ${GOVERNANCE_PROJECT_ID}/${REGION}..."
  local group collection id flag members list="${WORK_DIR}/target-entries.tsv"
  : >"${list}"
  for group in agents endpoints mcp_servers; do
    case "${group}" in
      agents) collection=agents ;;
      endpoints) collection=endpoints ;;
      mcp_servers) collection=mcpServers ;;
    esac
    for id in $(jq -r --arg g "${group}" '.[$g] // {} | keys[]' <<<"${IAP_TARGET_POLICIES_JSON}"); do
      members="$(jq -c --arg g "${group}" --arg id "${id}" '.[$g][$id]' <<<"${IAP_TARGET_POLICIES_JSON}")"
      flag="$(entry_flag "${id}" "${collection}")"
      echo "  -> ${group} ${id} (${flag#--})"
      printf '%s\t%s\n' "${members}" "${flag}" >>"${list}"
    done
  done
  run_entries "${list}" || { echo "❌ Per-target IAP egress policies do not match the catalog" >&2; exit 1; }
}

mode_check_root() {
  if root_has_bindings; then
    echo "❌ The registry root holds an IAP binding: new endpoint entries would be open to its members." >&2
    echo "   Apply layer 4 governance first (it moves that grant onto each system endpoint entry)." >&2
    exit 1
  fi
  echo "🔒 Registry root holds no IAP binding."
}

case "${IAP_MODE}" in
  system-endpoints) mode_system_endpoints ;;
  targets) mode_targets ;;
  check-root) mode_check_root ;;
  *)
    echo "❌ IAP_MODE must be system-endpoints, targets or check-root (got ${IAP_MODE})" >&2
    exit 1
    ;;
esac

echo "✅ IAP egress policies applied."
