#!/usr/bin/env bash
# preflight.sh
set -euo pipefail

echo "🔍 Running preflight checklist..."

# 1. Check Application Default Credentials (ADC)
if ! gcloud auth application-default print-access-token >/dev/null 2>&1; then
    echo "❌ [ERROR] Application Default Credentials are missing or expired. Please run 'gcloud auth application-default login'."
    exit 1
fi

# 2. Check active GCP project
ACTIVE_PROJECT=$(gcloud config get-value project 2>/dev/null || echo "")
if [ -z "$ACTIVE_PROJECT" ] || [ "$ACTIVE_PROJECT" == "(unset)" ]; then
    echo "❌ [ERROR] No active gcloud project set. Please run 'gcloud config set project <PROJECT_ID>'."
    exit 1
fi
echo "👉 Active GCP Project: $ACTIVE_PROJECT"

# 3. Check if the active GCP project is billable (billing enabled)
echo "💳 Checking billing status for project '$ACTIVE_PROJECT'..."
BILLING_ENABLED=$(gcloud billing projects describe "$ACTIVE_PROJECT" --format="value(billingEnabled)" 2>/dev/null || echo "false")

if [ "$BILLING_ENABLED" != "true" ]; then
    echo "❌ [ERROR] Project '$ACTIVE_PROJECT' does not have billing enabled or billing permissions are missing."
    echo "   A billable GCP project is required to provision the necessary services and deploy reasoning engines."
    exit 1
fi

echo "✅ Pre-flight checks passed! Project '$ACTIVE_PROJECT' is billable and authenticated. Proceeding with deployment."
