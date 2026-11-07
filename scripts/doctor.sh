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
# scripts/doctor.sh: an extended, non-fatal workstation check, one line per check (✅ / ❌ / ➖ + fix).
#
# Complements scripts/preflight.sh (fail-fast: python, uv, terraform, terragrunt, gcloud, project,
# ADC, billing), which it runs last and reduces to a single line. On top of it, it checks:
#   tool versions (incl. jq and OpenTofu), the active gcloud account, ADC validity + quota project,
#   the env vars env.yaml reads with get_env() (set/unset only, values are never printed), the git
#   hooks, the Terraform/uv caches and free disk space.
#
# Usage: scripts/doctor.sh [env]        (`make doctor [ENV=dev]`). Exit 1 if any ❌.
set -uo pipefail

ENV_NAME="${1:-dev}"
export PATH="$PATH:$HOME/.local/bin:$HOME/bin:/usr/local/bin:$HOME/google-cloud-sdk/bin:$HOME/.terraform/bin:$HOME/.terragrunt/bin"

FAILS=0
ok() { printf '✅ %-30s %s\n' "$1" "$2"; }
bad() { printf '❌ %-30s %s\n' "$1" "$2"; FAILS=$((FAILS + 1)); }
info() { printf '➖ %-30s %s\n' "$1" "$2"; }

# version_ge <have> <min>: true when have >= min (dotted numeric)
version_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" = "$2" ]; }
first_version() { grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1; }

# --- Tools: name, min version, version command, install hint -------------------------------------
check_tool() {
  local name="$1" min="$2" cmd="$3" hint="$4" have
  if ! command -v "${name}" >/dev/null 2>&1; then
    bad "${name}" "not found. ${hint}"
    return
  fi
  have="$(eval "${cmd}" 2>/dev/null | first_version)"
  if [ -z "${have}" ]; then
    info "${name}" "installed (version unknown)"
  elif version_ge "${have}" "${min}"; then
    ok "${name}" "${have} (>= ${min})"
  else
    bad "${name}" "${have} < ${min}. ${hint}"
  fi
}
check_tool terraform 1.5 "terraform version" "Install Terraform >= 1.5 (https://developer.hashicorp.com/terraform/install)"
check_tool terragrunt 0.80 "terragrunt --version" "Install Terragrunt (https://terragrunt.gruntwork.io/docs/getting-started/install/)"
check_tool gcloud 450.0 "gcloud version" "Install / update the Google Cloud SDK: gcloud components update"
check_tool uv 0.5 "uv --version" "curl -LsSf https://astral.sh/uv/install.sh | sh"
check_tool jq 1.6 "jq --version" "Install jq (e.g. apt install jq / brew install jq)"
if command -v tofu >/dev/null 2>&1; then
  info tofu "$(tofu version 2>/dev/null | first_version) present (the Makefile uses terraform)"
fi

# --- gcloud account, project, ADC -----------------------------------------------------------------
if command -v gcloud >/dev/null 2>&1; then
  ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -1)"
  if [ -z "${ACCOUNT}" ]; then
    bad "gcloud account" "no active account. Run: gcloud auth login"
  elif gcloud auth print-access-token >/dev/null 2>&1; then
    ok "gcloud account" "active, token valid"
  else
    bad "gcloud account" "token refresh failed (expired session). Run: gcloud auth login"
  fi

  PROJECT="$(gcloud config get-value project 2>/dev/null)"
  if [ -z "${PROJECT}" ] || [ "${PROJECT}" = "(unset)" ]; then
    bad "active project" "unset. Run: gcloud config set project <PROJECT_ID>"
  else
    ok "active project" "${PROJECT}"
  fi

  ADC_FILE="${GOOGLE_APPLICATION_CREDENTIALS:-$HOME/.config/gcloud/application_default_credentials.json}"
  if ! gcloud auth application-default print-access-token >/dev/null 2>&1; then
    bad "ADC" "missing or expired. Run: gcloud auth application-default login"
  else
    QUOTA=""
    [ -f "${ADC_FILE}" ] && command -v jq >/dev/null 2>&1 && QUOTA="$(jq -r '.quota_project_id // empty' "${ADC_FILE}" 2>/dev/null)"
    if [ -n "${QUOTA}" ]; then
      ok "ADC" "valid (quota project set)"
    else
      info "ADC" "valid, no quota project (optional: gcloud auth application-default set-quota-project <PROJECT_ID>)"
    fi
  fi
fi

# --- Env vars read by env.yaml (get_env without a default = required). Values never printed. -------
ENV_FILES=()
for f in "infrastructure/live/${ENV_NAME}/env.yaml" infrastructure/live/shared/env.yaml; do
  [ -f "${f}" ] && ENV_FILES+=("${f}")
done
# "<VAR> required|optional", required wins when files disagree
ENV_VARS="$(
  [ "${#ENV_FILES[@]}" -gt 0 ] && grep -ohE 'get_env\("[A-Za-z0-9_]+"[^)]*\)' "${ENV_FILES[@]}" |
    sed -E 's/get_env\("([A-Za-z0-9_]+)"(,?).*/\1 \2/' | awk '{ if ($2 == ",") { if (!($1 in r)) r[$1] = "optional" } else r[$1] = "required" }
      END { for (v in r) print v, r[v] }' | sort
)"
while read -r var kind; do
  [ -n "${var}" ] || continue
  if [ -n "${!var:-}" ]; then
    ok "env ${var}" "set"
  elif [ "${kind}" = "optional" ]; then
    info "env ${var}" "unset (optional)"
  else
    bad "env ${var}" "unset, required by env.yaml. export ${var}=... (e.g. in ~/.bashrc, never in a repo file)"
  fi
done <<<"${ENV_VARS}"

# --- Repository wiring ----------------------------------------------------------------------------
if [ "$(git config core.hooksPath 2>/dev/null)" = ".githooks" ]; then
  ok "git hooks" ".githooks (pre-commit sensitive scan, pre-push lock check)"
else
  bad "git hooks" "not installed. Run: make hooks"
fi
PATTERNS="${ESMERALDA_SENSITIVE_PATTERNS_FILE:-$(git rev-parse --git-common-dir 2>/dev/null)/info/sensitive-patterns}"
if [ -s "${PATTERNS}" ]; then
  ok "sensitive patterns" "$(grep -cvE '^\s*(#|$)' "${PATTERNS}") local pattern(s)"
else
  info "sensitive patterns" "no local patterns file (optional, see docs: Operating with agents)"
fi

# --- Disk space for Terraform providers, Terragrunt and uv caches ---------------------------------
check_disk() {
  local label="$1" dir="$2" min_gb="$3" free_gb
  [ -d "${dir}" ] || return 0
  free_gb="$(df -Pk "${dir}" | awk 'NR==2 {printf "%d", $4/1048576}')"
  if [ "${free_gb}" -ge "${min_gb}" ]; then
    ok "disk ${label}" "${free_gb} GB free"
  else
    bad "disk ${label}" "${free_gb} GB free (< ${min_gb} GB). Free space, or point TF_PLUGIN_CACHE_DIR / TG_DOWNLOAD_DIR / UV_CACHE_DIR at a larger disk"
  fi
}
check_disk "repo" "." 5
for v in TF_PLUGIN_CACHE_DIR TG_DOWNLOAD_DIR UV_CACHE_DIR; do
  [ -n "${!v:-}" ] && check_disk "${v}" "${!v}" 2
done
if [ -n "${TF_PLUGIN_CACHE_DIR:-}" ] && [ "${TG_PROVIDER_CACHE:-}" != "true" ] && [ "${TG_PROVIDER_CACHE:-}" != "1" ]; then
  info "provider cache" "TF_PLUGIN_CACHE_DIR is not safe with Terragrunt's parallel dependency reads (checksum errors); prefer TG_PROVIDER_CACHE=1 + TG_PROVIDER_CACHE_DIR"
fi
TG_CACHES="$(find infrastructure -type d -name .terragrunt-cache -prune 2>/dev/null | wc -l)"
[ "${TG_CACHES}" -gt 0 ] && info "terragrunt caches" "${TG_CACHES} .terragrunt-cache dir(s) in the repo (make clean frees them; set TG_DOWNLOAD_DIR to move them)"

# --- Existing preflight (billing etc.), reduced to one line ----------------------------------------
if PF_OUT="$(bash scripts/preflight.sh 2>&1)"; then
  ok "preflight" "all checks passed (incl. billing)"
else
  bad "preflight" "$(sed -E 's/\x1b\[[0-9;]*m//g' <<<"${PF_OUT}" | grep -E '❌|👉' | head -2 | tr '\n' ' ')"
fi

echo
if [ "${FAILS}" -eq 0 ]; then
  echo "🩺 doctor: all checks passed"
else
  echo "🩺 doctor: ${FAILS} check(s) failed (see the hints above)"
  exit 1
fi
