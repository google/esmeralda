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
# Shared helpers for the partner front-door scripts (sourced, not executed):
#   scripts/test_partner.sh       live conformance probes (`esmeralda verify --partner`)
#   scripts/partner_load_test.sh  Locust load test through Apigee (`make load-test-partner`)
#
# Run from the repository root. `partner_resolve <env>` sets, from Terragrunt outputs (or the local
# state files when a worktree points at the main checkout's state):
#   VM_NAME VM_ZONE VM_PROJECT   the private test VM (IAP SSH)
#   ENTRY_IP DOMAIN PARTNER_HOST  partner.<domain> and the address it resolves to inside the VPC
#   CA_PEM                        the internal root CA (PEM)
#   PARTNER_KEY                   ESMERALDA_PARTNER_API_KEY, or the apigee/products consumer key secret
# Secrets stay in shell variables; never echo them.

die() { echo "❌ $*" >&2; exit 1; }
tg_output() { (cd "$1" && terragrunt output -raw "$2" 2>/dev/null) || true; }
state_output() { jq -r --arg k "$2" '.outputs[$k].value // empty' "$1" 2>/dev/null || true; }

# Terragrunt needs ESMERALDA_BILLING_ACCOUNT (and optionally ESMERALDA_ORG_ID). Like the esmeralda
# CLI and the Makefile, read them from the user-local env file when they are not exported.
partner_load_env_file() {
  local file="${ESMERALDA_ENV_FILE:-${HOME}/.config/esmeralda/env}" key value
  [ -f "${file}" ] || return 0
  while IFS='=' read -r key value; do
    case "${key}" in
      ESMERALDA_*) [ -n "${!key:-}" ] || export "${key}=${value}" ;;
    esac
  done < "${file}"
}
partner_load_env_file

# Prints the Apigee environment name, or nothing when Apigee is not deployed in <env>.
partner_apigee_env() {
  local platform_dir="infrastructure/live/$1/layer-5-workloads/apigee/platform"
  [ -d "${platform_dir}" ] || return 0
  tg_output "${platform_dir}" apigee_env_name
}

partner_resolve() {
  local env_name="$1"
  local live_dir="infrastructure/live/${env_name}"
  local l5_dir="${live_dir}/layer-5-workloads"

  # Prefer the flattened layer-5-workloads/test-vm unit; fall back for one release to the legacy
  # layer-5-workloads/services/test-vm directory or local state file.
  local vm_dir="${l5_dir}/test-vm"
  if [ ! -d "${vm_dir}" ] && [ -d "${l5_dir}/services/test-vm" ]; then
    vm_dir="${l5_dir}/services/test-vm"
  fi
  VM_NAME="$(tg_output "${vm_dir}" instance_name)"
  VM_ZONE="$(tg_output "${vm_dir}" instance_zone)"
  VM_PROJECT="$(tg_output "${vm_dir}" project_id)"
  if [ -z "${VM_NAME}" ] && [ -d "${l5_dir}/services/test-vm" ] && [ "${vm_dir}" != "${l5_dir}/services/test-vm" ]; then
    VM_NAME="$(tg_output "${l5_dir}/services/test-vm" instance_name)"
    VM_ZONE="$(tg_output "${l5_dir}/services/test-vm" instance_zone)"
    VM_PROJECT="$(tg_output "${l5_dir}/services/test-vm" project_id)"
  fi
  local candidate_state
  for candidate_state in \
    "infrastructure/live/.local_states/${env_name}/layer-5-workloads/test-vm/terraform.tfstate" \
    "infrastructure/live/.local_states/${env_name}/layer-5-workloads/services/test-vm/terraform.tfstate"; do
    if [ -z "${VM_NAME}" ] && [ -f "${candidate_state}" ]; then
      VM_NAME="$(state_output "${candidate_state}" instance_name)"
      VM_ZONE="$(state_output "${candidate_state}" instance_zone)"
      VM_PROJECT="$(state_output "${candidate_state}" project_id)"
    fi
  done
  [ -n "${VM_NAME}" ] && [ -n "${VM_ZONE}" ] && [ -n "${VM_PROJECT}" ] || \
    die "No test VM outputs in ${vm_dir}. Deploy workloads first."

  ENTRY_IP="$(tg_output "${live_dir}/layer-2-networking" entrypoint_address)"
  DOMAIN="$(tg_output "${live_dir}/layer-2-networking" entrypoint_domain)"
  [ -n "${ENTRY_IP}" ] && [ -n "${DOMAIN}" ] || die "No entrypoint outputs in layer-2-networking."
  PARTNER_HOST="partner.${DOMAIN}"

  local ca_secret ca_secret_project
  ca_secret="$(tg_output "${live_dir}/layer-3-security" internal_ca_secret_id)"
  [ -n "${ca_secret}" ] || die "No internal_ca_secret_id output in layer-3-security."
  ca_secret_project="$(echo "${ca_secret}" | cut -d/ -f2)"
  CA_PEM="$(gcloud secrets versions access latest --secret="${ca_secret##*/}" --project="${ca_secret_project}")"

  PARTNER_KEY="${ESMERALDA_PARTNER_API_KEY:-}"
  if [ -z "${PARTNER_KEY}" ]; then
    local key_secret_id key_project
    key_secret_id="$(tg_output "${l5_dir}/apigee/products" partner_consumer_key_secret_id)"
    if [ -n "${key_secret_id}" ]; then
      key_project="$(echo "${key_secret_id}" | cut -d/ -f2)"
      PARTNER_KEY="$(gcloud secrets versions access latest --secret="${key_secret_id##*/}" --project="${key_project}")"
    fi
  fi
  [ -n "${PARTNER_KEY}" ] || die "Set ESMERALDA_PARTNER_API_KEY or deploy layer-5-workloads/apigee/products."
}

# Prints a file as one base64 line (portable: GNU and BSD base64), to embed it in a remote script.
b64_file() { base64 < "$1" | tr -d '\n'; }
