#!/usr/bin/env bash
# infrastructure/modules/_shared/scripts/model_armor_floor_off.sh
#
# Turns the Vertex AI Model Armor integration off in one project's floor setting.
#
# A Model Armor floor setting is a singleton: deleting the Terraform resource
# (google_model_armor_floorsetting) only removes it from state and leaves the last setting active.
# Layer 4 governance calls this script from a destroy-time provisioner when
# governance.model_armor.model_calls goes back to "off" (or a project leaves the agent list), so
# "off" really means the Gemini calls in that project are no longer screened.
#
# Required env:
#   PROJECT_ID   project whose floor setting is updated
set -euo pipefail

: "${PROJECT_ID:?}"

if [[ ! "${PROJECT_ID}" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]]; then
  echo "❌ PROJECT_ID must be a plain project id" >&2
  exit 1
fi

TOKEN="$(gcloud auth print-access-token)"
URL="https://modelarmor.googleapis.com/v1/projects/${PROJECT_ID}/locations/global/floorSetting"

echo "🛡️  Turning the Vertex AI Model Armor integration off in ${PROJECT_ID}..."
curl -sS --fail-with-body -X PATCH \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -H "x-goog-user-project: ${PROJECT_ID}" \
  -d '{"integratedServices": [], "enableFloorSettingEnforcement": false}' \
  "${URL}?updateMask=integratedServices,enableFloorSettingEnforcement" >/dev/null
echo "✅ Vertex AI Model Armor integration off in ${PROJECT_ID}"
