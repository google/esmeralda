#!/usr/bin/env bash
# preflight.sh
set -euo pipefail

# Colors for premium CLI aesthetics
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}==================================================${NC}"
echo -e "${CYAN}🔍 Running Esmeralda Preflight Checklist...${NC}"
echo -e "${CYAN}==================================================${NC}"

# --- Environment Setup ---
# Safely append common developer paths to ensure tools like terraform and gcloud are found
# even in non-interactive shells where .bashrc might exit early.
export PATH="$PATH:$HOME/.local/bin:$HOME/bin:/usr/local/bin:/opt/homebrew/bin:$HOME/google-cloud-sdk/bin:$HOME/.terraform/bin"

# Explicitly check direct home path for Terraform if not found in default PATH
if ! command -v terraform &>/dev/null; then
    if [ -f "$HOME/.terraform/bin/terraform" ]; then
        export PATH="$PATH:$HOME/.terraform/bin"
    fi
fi

# 1. Check Python 3 & version >= 3.10
echo -e "${BLUE}[*] Checking Python installation...${NC}"
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}❌ [ERROR] Python 3 is not installed. Please install Python 3.10+.${NC}"
    exit 1
fi

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    py_version=$(python3 --version 2>&1)
    echo -e "${RED}❌ [ERROR] Python 3.10+ is required. Found: $py_version${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Python is ready (${SYS_PY_VER:-$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')})${NC}"

# 2. Check Terraform & version >= 1.5
echo -e "${BLUE}[*] Checking Terraform installation...${NC}"
if ! command -v terraform &>/dev/null; then
    echo -e "${RED}❌ [ERROR] Terraform is not installed or not in PATH. Please install Terraform >= 1.5.${NC}"
    exit 1
fi

tf_ver_str=$(terraform -version | head -n1)
if [[ "$tf_ver_str" =~ [vV]([0-9]+)\.([0-9]+) ]]; then
    major="${BASH_REMATCH[1]}"
    minor="${BASH_REMATCH[2]}"
    if [ "$major" -lt 1 ] || { [ "$major" -eq 1 ] && [ "$minor" -lt 5 ]; }; then
         echo -e "${RED}❌ [ERROR] Terraform >= 1.5 is required. Found: $tf_ver_str${NC}"
         exit 1
    fi
fi
echo -e "${GREEN}✅ Terraform is ready ($tf_ver_str)${NC}"

# 3. Check Google Cloud SDK (gcloud)
echo -e "${BLUE}[*] Checking Google Cloud SDK...${NC}"
if ! command -v gcloud &>/dev/null; then
    echo -e "${RED}❌ [ERROR] Google Cloud SDK ('gcloud') is not installed. Please install it first.${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Google Cloud SDK is ready${NC}"

# 4. Check active GCP project
echo -e "${BLUE}[*] Checking active gcloud project context...${NC}"
ACTIVE_PROJECT=$(gcloud config get-value project 2>/dev/null || echo "")
if [ -z "$ACTIVE_PROJECT" ] || [ "$ACTIVE_PROJECT" == "(unset)" ]; then
    echo -e "${RED}❌ [ERROR] No active gcloud project set. Please run 'gcloud config set project <PROJECT_ID>'.${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Active GCP Project: $ACTIVE_PROJECT${NC}"

# 5. Check Application Default Credentials (ADC)
echo -e "${BLUE}[*] Checking Application Default Credentials...${NC}"
if ! gcloud auth application-default print-access-token >/dev/null 2>&1; then
    echo -e "${RED}❌ [ERROR] Application Default Credentials (ADC) are missing or expired.${NC}"
    echo -e "${YELLOW}👉 Run the following commands to log in and authenticate correctly:${NC}"
    echo -e "   gcloud auth login"
    echo -e "   gcloud auth application-default login"
    exit 1
fi
echo -e "${GREEN}✅ Application Default Credentials (ADC) are active${NC}"

# 6. Check if the active GCP project is billable (billing enabled)
echo -e "${BLUE}[*] Checking billing status for project '$ACTIVE_PROJECT'...${NC}"
if ! BILLING_OUT=$(gcloud billing projects describe "$ACTIVE_PROJECT" --format="value(billingEnabled)" 2>&1); then
    echo -e "${YELLOW}⚠️  [WARNING] Could not verify billing status automatically. Details: ${BILLING_OUT}${NC}"
    echo -e "${YELLOW}👉 Make sure billing is linked to project '$ACTIVE_PROJECT'.${NC}"
    echo -e "${YELLOW}👉 If you see 'Reauthentication required', run 'gcloud auth login' and try again.${NC}"
else
    # Case-insensitive comparison
    billing_status="${BILLING_OUT,,}"
    if [ "$billing_status" != "true" ]; then
        echo -e "${RED}❌ [ERROR] Project '$ACTIVE_PROJECT' does not have billing enabled.${NC}"
        echo -e "   A billable GCP project is required to provision the necessary services and deploy reasoning engines."
        exit 1
    else
        echo -e "${GREEN}✅ Project '$ACTIVE_PROJECT' has billing enabled${NC}"
    fi
fi

echo -e "${CYAN}==================================================${NC}"
echo -e "${GREEN}🎉 All preflight checks passed!${NC}"
echo -e "${CYAN}==================================================${NC}"
