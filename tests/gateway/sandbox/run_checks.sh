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

# Runs checks.sh on the sandbox test VM over IAP SSH, with values from the sandbox Terraform outputs.
# Usage: tests/gateway/sandbox/run_checks.sh   (after `tofu apply` in this directory; TF_BIN to override)
set -euo pipefail

cd "$(dirname "$0")"
TF="${TF_BIN:-$(command -v terraform || command -v tofu)}"
out() { "$TF" output -raw "$1"; }

PROJECT=$(out project_id)
VM=$(out test_vm_name)
ZONE=$(out test_vm_zone)
UPSTREAM_URL=$(out upstream_url)
RATE_LIMIT=$(out rate_limit_per_minute)

echo "Running gateway checks on $VM ($PROJECT, $ZONE)..."
gcloud compute ssh "$VM" --project "$PROJECT" --zone "$ZONE" --tunnel-through-iap --quiet \
  --command "UPSTREAM_URL='$UPSTREAM_URL' RATE_LIMIT='$RATE_LIMIT' bash -s" < checks.sh
