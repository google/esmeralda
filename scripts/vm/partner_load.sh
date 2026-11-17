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
# Partner load test, run ON the test VM (`make load-test-partner`).
#
# `esmeralda.ops.partner_load` resolves the test VM, entry point, internal CA and partner key on the
# workstation and sends this file over `gcloud compute ssh --tunnel-through-iap` on stdin, after:
#   PARTNER_ENTRY_IP PARTNER_HOST PARTNER_API_KEY CA_PEM   the partner front door and its credentials
#   PARTNER_INCLUDE_QUOTA PARTNER_LOAD_RUN_ID               read by tests/load_test/partner_load_test.py
#   REMOTE_DIR                                              run directory under $HOME
#   LOCUST_VERSION UV_VERSION                               Locust from the workspace lock, installed with uv
#   USERS SPAWN_RATE DURATION STOP_TIMEOUT                  Locust run parameters
#   SCENARIOS_B64 LOCUSTFILE_B64                            tests/partner/scenarios.py and the Locust file
#
# On first use it installs Locust in a cached venv with uv (needs outbound HTTPS to PyPI and the uv
# installer, e.g. through Cloud NAT), runs it headless, writes the CSV + HTML reports to
# $HOME/$REMOTE_DIR/results and prints `LOCUST_EXIT=<code>` last. The workstation copies the reports
# back and removes the run directory.
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
