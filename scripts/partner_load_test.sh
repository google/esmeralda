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
# Locust load test THROUGH the Apigee partner front door (`make load-test-partner`).
#
# The partner host (partner.<domain>) is private, so Locust runs on the test VM (IAP SSH):
#   1. resolves the VM, entry point, internal CA and partner key exactly like scripts/test_partner.sh;
#   2. sends tests/load_test/partner_load_test.py + tests/partner/scenarios.py to the VM and, on first
#      use, installs Locust there in a cached venv with uv (needs outbound HTTPS to PyPI and the uv
#      installer, e.g. through Cloud NAT);
#   3. runs Locust headless and copies the CSV + HTML reports back to tests/load_test/.results/partner/.
#
# Usage: scripts/partner_load_test.sh [env] [--users N] [--spawn-rate R] [--duration 5m]
#                                     [--stop-timeout 90] [--include-quota]
# --include-quota adds the destructive quota-exhaustion scenario: it burns the API product's LLM
# token quota, after which every orchestrator call gets HTTP 429 until the quota window resets.
set -euo pipefail

ENV_NAME="dev"
USERS=2
SPAWN_RATE=1
DURATION="5m"
STOP_TIMEOUT=90
INCLUDE_QUOTA=0

usage() {
  echo "Usage: $0 [env] [--users N] [--spawn-rate R] [--duration 5m] [--stop-timeout 90] [--include-quota]"
}
while [ $# -gt 0 ]; do
  case "$1" in
    --users) USERS="$2"; shift 2 ;;
    --spawn-rate) SPAWN_RATE="$2"; shift 2 ;;
    --duration) DURATION="$2"; shift 2 ;;
    --stop-timeout) STOP_TIMEOUT="$2"; shift 2 ;;
    --include-quota) INCLUDE_QUOTA=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) usage >&2; exit 2 ;;
    *) ENV_NAME="$1"; shift ;;
  esac
done
[[ "${USERS}" =~ ^[0-9]+$ ]] && [[ "${SPAWN_RATE}" =~ ^[0-9.]+$ ]] && [[ "${STOP_TIMEOUT}" =~ ^[0-9]+$ ]] || \
  { echo "❌ --users, --spawn-rate and --stop-timeout must be numbers" >&2; exit 2; }
[[ "${DURATION}" =~ ^[0-9]+[smh]?$ ]] || { echo "❌ --duration must look like 90s, 5m or 1h" >&2; exit 2; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"
# shellcheck source=scripts/partner_env.sh
source scripts/partner_env.sh

[ -n "$(partner_apigee_env "${ENV_NAME}")" ] || die "Apigee is not deployed in ${ENV_NAME}: nothing to load test."
partner_resolve "${ENV_NAME}"

# Same Locust as the workspace lock, so the VM and `make load-test` agree.
LOCUST_VERSION="$(awk '/^name = "locust"$/ { getline; gsub(/version = |"/, ""); print; exit }' uv.lock 2>/dev/null || true)"
LOCUST_VERSION="${LOCUST_VERSION:-2.46.7}"
UV_VERSION="0.9.30"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$(od -An -N3 -tx1 /dev/urandom | tr -d ' \n')"
RESULTS_DIR="tests/load_test/.results/partner"
REMOTE_DIR="esmeralda-partner-load/${RUN_ID}"

echo "⚡ Partner load test ${RUN_ID}: ${USERS} user(s), ${DURATION}, quota exhaustion $([ "${INCLUDE_QUOTA}" = 1 ] && echo ON || echo off)"
echo "   https://${PARTNER_HOST} from ${VM_NAME} (Locust ${LOCUST_VERSION})"

REMOTE="$(mktemp)"
trap 'rm -f "${REMOTE}"' EXIT
chmod 600 "${REMOTE}"
{
  printf 'PARTNER_ENTRY_IP=%q\n' "${ENTRY_IP}"
  printf 'PARTNER_HOST=%q\n' "${PARTNER_HOST}"
  printf 'PARTNER_API_KEY=%q\n' "${PARTNER_KEY}"
  printf 'CA_PEM=%q\n' "${CA_PEM}"
  printf 'PARTNER_INCLUDE_QUOTA=%q\n' "${INCLUDE_QUOTA}"
  printf 'PARTNER_LOAD_RUN_ID=%q\n' "${RUN_ID}"
  printf 'REMOTE_DIR=%q\n' "${REMOTE_DIR}"
  printf 'LOCUST_VERSION=%q\nUV_VERSION=%q\n' "${LOCUST_VERSION}" "${UV_VERSION}"
  printf 'USERS=%q\nSPAWN_RATE=%q\nDURATION=%q\nSTOP_TIMEOUT=%q\n' "${USERS}" "${SPAWN_RATE}" "${DURATION}" "${STOP_TIMEOUT}"
  printf 'SCENARIOS_B64=%q\n' "$(b64_file tests/partner/scenarios.py)"
  printf 'LOCUSTFILE_B64=%q\n' "$(b64_file tests/load_test/partner_load_test.py)"
  cat <<'REMOTE_EOF'
set -euo pipefail
RUN_DIR="${HOME}/${REMOTE_DIR}"
mkdir -p "${RUN_DIR}/results"
SECRETS="$(mktemp -d)"
trap 'rm -rf "${SECRETS}"' EXIT
printf '%s\n' "${CA_PEM}" > "${SECRETS}/ca.pem"
printf '%s' "${SCENARIOS_B64}" | base64 -d > "${RUN_DIR}/scenarios.py"
printf '%s' "${LOCUSTFILE_B64}" | base64 -d > "${RUN_DIR}/partner_load_test.py"

# Locust in a cached venv (first run only). The test VM image ships python3 without pip/ensurepip,
# so uv (a single static binary) creates the venv and installs Locust from PyPI.
CACHE="${HOME}/.cache/esmeralda"
VENV="${CACHE}/locust-${LOCUST_VERSION}"
if [ ! -x "${VENV}/bin/locust" ]; then
  UV="$(command -v uv || true)"
  [ -n "${UV}" ] || UV="${CACHE}/uv-${UV_VERSION}/uv"
  if [ ! -x "${UV}" ]; then
    echo "📦 Installing uv ${UV_VERSION} on the test VM..."
    curl -LsSf --max-time 120 "https://astral.sh/uv/${UV_VERSION}/install.sh" | \
      env UV_UNMANAGED_INSTALL="${CACHE}/uv-${UV_VERSION}" sh >/dev/null || {
        echo "❌ The test VM cannot download uv (no outbound HTTPS?). Give the subnet Cloud NAT, or" \
             "pre-install Locust ${LOCUST_VERSION} in ${VENV} (python3 -m venv + pip)."
        exit 1
      }
  fi
  echo "📦 Installing Locust ${LOCUST_VERSION} on the test VM (first run only)..."
  "${UV}" venv --quiet --python /usr/bin/python3 "${VENV}"
  "${UV}" pip install --quiet --python "${VENV}/bin/python" "locust==${LOCUST_VERSION}" || {
    rm -rf "${VENV}"
    echo "❌ Could not install Locust from PyPI on the test VM (no outbound HTTPS?)."
    exit 1
  }
fi

PARTNER_CA_FILE="${SECRETS}/ca.pem"
export PARTNER_ENTRY_IP PARTNER_HOST PARTNER_API_KEY PARTNER_CA_FILE PARTNER_INCLUDE_QUOTA PARTNER_LOAD_RUN_ID
cd "${RUN_DIR}"
set +e
"${VENV}/bin/locust" -f partner_load_test.py --headless --only-summary \
  -u "${USERS}" -r "${SPAWN_RATE}" --run-time "${DURATION}" --stop-timeout "${STOP_TIMEOUT}" \
  --csv results/partner --html results/report.html --loglevel WARNING
rc=$?
set -e
echo "LOCUST_EXIT=${rc}"
REMOTE_EOF
} > "${REMOTE}"

set +e
gcloud compute ssh "${VM_NAME}" --zone="${VM_ZONE}" --project="${VM_PROJECT}" \
  --tunnel-through-iap --quiet --command='bash -s' < "${REMOTE}" | tee "${REMOTE}.log"
ssh_rc=${PIPESTATUS[0]}
set -e
locust_rc="$(sed -n 's/^LOCUST_EXIT=\([0-9]*\).*/\1/p' "${REMOTE}.log" | tail -1)"
rm -f "${REMOTE}.log"
[ -n "${locust_rc}" ] || die "The remote run did not finish (ssh exit ${ssh_rc})."

mkdir -p "${RESULTS_DIR}"
rm -f "${RESULTS_DIR}"/partner_*.csv "${RESULTS_DIR}/report.html"
gcloud compute scp --recurse --zone="${VM_ZONE}" --project="${VM_PROJECT}" --tunnel-through-iap --quiet \
  "${VM_NAME}:${REMOTE_DIR}/results/*" "${RESULTS_DIR}/" || \
  die "Could not copy the reports back (gcloud auth expired?); they stay on ${VM_NAME} in ~/${REMOTE_DIR}/results."
gcloud compute ssh "${VM_NAME}" --zone="${VM_ZONE}" --project="${VM_PROJECT}" \
  --tunnel-through-iap --quiet --command="rm -rf ~/${REMOTE_DIR}" >/dev/null 2>&1 || true

echo
echo "📊 Per-scenario results (ms) — ${RESULTS_DIR}/partner_stats.csv"
awk -F, 'NR == 1 { next } { printf "  %-48s %-6s %6s req %5s fail  p50 %7s  p95 %7s\n", $2, $1, $3, $4, $12, $17 }' \
  "${RESULTS_DIR}/partner_stats.csv"
if [ -s "${RESULTS_DIR}/partner_failures.csv" ] && [ "$(wc -l < "${RESULTS_DIR}/partner_failures.csv")" -gt 1 ]; then
  echo "❌ Failures — ${RESULTS_DIR}/partner_failures.csv"
  tail -n +2 "${RESULTS_DIR}/partner_failures.csv" | sed 's/^/  /'
fi
echo "📄 HTML report: ${RESULTS_DIR}/report.html"
echo "   Run ${RUN_ID}: requests carry the header x-esmeralda-load-run=${RUN_ID} (visible in an Apigee Debug session)."
exit "${locust_rc}"
