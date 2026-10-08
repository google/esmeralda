#!/usr/bin/env bash
# Fails if a Layer 5 workload deploys a container image straight from a variable (i.e. by tag).
# Cloud Run / Agent Runtime resolve a tag only when a revision is created, so a template that keeps
# saying ":dev-latest" never rolls out a rebuilt image. Workloads must deploy the digest resolved at
# plan time (local.image_by_digest in services/*/image.tf, the docker_image data source in agents/*).
set -euo pipefail

matches="$(grep -rnE '^\s*(image|image_uri)\s*=\s*var\.' infrastructure/modules/5-workloads \
  --include='*.tf' --exclude-dir=.terraform || true)"

if [ -n "${matches}" ]; then
  echo "❌ Workload images must be deployed by digest, not by tag variable:"
  echo "${matches}"
  echo "   Use local.image_by_digest (copy services/corporate-email/image.tf)."
  exit 1
fi
echo "✅ All Layer 5 workload images are deployed by digest."
