#!/usr/bin/env bash
# Regenerates the root uv.lock against public PyPI, from your local working copy.
#
# Why not just `uv lock`? On corporate machines uv goes through a package proxy (e.g. Corp Airlock)
# that holds back new releases for days and rewrites every URL in the lock. So the resolution runs in
# Cloud Build (CI/CD project, public PyPI) on your *local* pyproject.toml files and uv.lock; the new
# uv.lock is written back into your working copy for you to review and commit. Nothing is pushed.
#
# Usage: scripts/uv_lock.sh [package ...]     (`make lock [PKG="urllib3 google-adk==2.11.0"]`)
#   no packages  -> only bring uv.lock in line with the pyproject.toml files (keeps locked versions)
#   packages     -> also upgrade those packages (optionally pinned with ==version)
set -euo pipefail

UV_VERSION="0.9.30" # keep in sync with the uv image in apps/services/*/Dockerfile
CICD_DIR="infrastructure/live/shared/layer-0-cicd"
cd "$(git rev-parse --show-toplevel)"

uv_args=()
for p in "$@"; do
  if [[ ! "$p" =~ ^[A-Za-z0-9._-]+(==[A-Za-z0-9.+!_-]+)?$ ]]; then
    echo "❌ Invalid package spec: $p" >&2
    exit 1
  fi
  uv_args+=(--upgrade-package "$p")
done

echo "🔐 Locking against public PyPI in Cloud Build${*:+ (upgrade: $*)}..."
CICD_JSON="$(cd "${CICD_DIR}" && terragrunt output -json 2>/dev/null)"
CICD_PROJECT="$(jq -r .cicd_project_id.value <<<"${CICD_JSON}")"
BUILDER_SA="$(jq -r .builder_sa_email.value <<<"${CICD_JSON}")"
SOURCE_BUCKET="$(jq -r .build_source_bucket.value <<<"${CICD_JSON}")"
if [[ -z "${CICD_PROJECT}" || "${CICD_PROJECT}" == "null" ]]; then
  echo "❌ Could not read Layer 0 outputs from ${CICD_DIR} (run 'terragrunt output' there to see why)." >&2
  exit 1
fi
echo "   gcloud account: $(gcloud config get-value account 2>/dev/null)"

# Upload only what uv needs to resolve: the workspace pyproject files and the current lock.
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT
python3 - "${work}" <<'EOF'
import pathlib, shutil, sys, tomllib
dst = pathlib.Path(sys.argv[1])
root = tomllib.loads(pathlib.Path("pyproject.toml").read_text())
files = ["pyproject.toml", "uv.lock", "README.md"]
files += [f"{m}/pyproject.toml" for m in root["tool"]["uv"]["workspace"]["members"]]
for f in files:
    if pathlib.Path(f).exists():
        (dst / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(f, dst / f)
EOF

# The new lock comes back through the build source bucket (Cloud Build "artifacts"), not the build
# log: Cloud Logging returns streamed build logs out of order and truncated, so they can't carry it.
run_id="uvlock-$(date +%s)-${RANDOM}"
yaml_args='"lock"'
for a in "${uv_args[@]}"; do yaml_args+=", \"${a}\""; done

cat >"${work}/cloudbuild.yaml" <<EOF
steps:
  - name: "ghcr.io/astral-sh/uv:${UV_VERSION}-python3.12-bookworm-slim"
    entrypoint: uv
    args: [${yaml_args}]
artifacts:
  objects:
    location: "gs://${SOURCE_BUCKET}/${run_id}/"
    paths: ["uv.lock"]
# Logs go to the same bucket (not Cloud Logging) so gcloud builds submit can stream them in order.
logsBucket: "gs://${SOURCE_BUCKET}/${run_id}/logs"
options:
  logging: GCS_ONLY
EOF

# Synchronous submit: streams the uv output live and fails if the build fails.
if ! gcloud builds submit "${work}" --config="${work}/cloudbuild.yaml" --project="${CICD_PROJECT}" \
  --service-account="projects/${CICD_PROJECT}/serviceAccounts/${BUILDER_SA}" \
  --gcs-source-staging-dir="gs://${SOURCE_BUCKET}/source"; then
  echo "❌ Cloud Build failed (see the output above); uv.lock unchanged." >&2
  exit 1
fi

gcloud storage cp --no-user-output-enabled "gs://${SOURCE_BUCKET}/${run_id}/uv.lock" "${work}/uv.lock.new"
mv "${work}/uv.lock.new" uv.lock

scripts/check_lock.sh
if git diff --quiet -- uv.lock; then
  echo "✅ uv.lock was already up to date."
else
  git --no-pager diff --stat -- uv.lock
  echo "✅ uv.lock updated. Review it (git diff uv.lock) and commit it."
fi
