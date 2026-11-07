# ==============================================================================
# SUBMODULE 7: GEAP SEMANTIC GOVERNANCE (NATURAL LANGUAGE CONSTRAINTS) & UAPs
# ==============================================================================
# Declares Natural Language Constraints (NLC) and Unified Access Policies (UAPs)
# enforced at the Central Agent Gateway boundary to ensure AI agent tool
# invocations align with user intent and organizational business constraints:
#   - https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/policies/semantic-governance-overview
#   - https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/policies/overview-uap

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

locals {
  default_semantic_governance_policies = {
    "corporate-email-nlc" = {
      policy_id         = "corporate-email-nlc"
      target_mcp_server = "corporate-email"
      tools             = ["send_email", "read_email"]
      principal         = "ai-coe-mortgage-specialist"
      enforcement_mode  = var.enforcement_mode
      natural_language_constraints = [
        "Allow send_email only when the recipient address belongs to an internal underwriting domain (@${var.internal_underwriting_domain}) and the user explicitly requested sending an underwriting notification.",
        "Deny send_email if the subject or body contains unmasked Social Security Numbers (SSNs) matching XXX-XX-XXXX or raw tax return payloads.",
      ]
    }

    "legacy-dms-nlc" = {
      policy_id         = "legacy-dms-nlc"
      target_mcp_server = "legacy-dms"
      tools             = ["search_documents", "get_document"]
      principal         = "ai-coe-mortgage-specialist"
      enforcement_mode  = var.enforcement_mode
      natural_language_constraints = [
        "Allow search_documents and get_document only for the mortgage applicant family or document ID explicitly named in the initiating user request.",
        "Deny bulk retrieval or enumeration of documents across unrelated applicants in a single turn.",
      ]
    }

    "income-verification-nlc" = {
      policy_id         = "income-verification-nlc"
      target_mcp_server = "income-verification"
      tools             = ["verify_applicant"]
      principal         = "ai-coe-mortgage-specialist"
      enforcement_mode  = var.enforcement_mode
      natural_language_constraints = [
        "Allow verify_applicant only when first_name and last_name match the mortgage applicant being evaluated in the active underwriting session.",
      ]
    }
  }

  custom_semantic_governance_policies = {
    for policy_id, policy in var.custom_policies :
    policy_id => {
      policy_id                    = policy_id
      target_mcp_server            = policy.target_mcp_server
      tools                        = policy.tools
      principal                    = policy.principal
      enforcement_mode             = coalesce(policy.enforcement_mode, var.enforcement_mode)
      natural_language_constraints = policy.natural_language_constraints
    }
  }

  semantic_governance_policies = merge(
    local.default_semantic_governance_policies,
    local.custom_semantic_governance_policies,
  )

  # Unified Access Policies (UAPs) pairing deterministic SPIFFE principal
  # allowlists with semantic NLC policies at the Central Agent Gateway.
  uap_bindings = {
    "a2a-ai-coe-mortgage-specialist" = {
      uap_id               = "a2a-ai-coe-mortgage-specialist"
      target_service       = "ai-coe-mortgage-specialist"
      protocol             = "a2a"
      role                 = "roles/iap.egressor"
      allowed_principals   = ["cx-mortgage-orchestrator"]
      denied_principals    = []
      tools                = []
      semantic_policy_refs = []
    }

    "mcp-corporate-email" = {
      uap_id               = "mcp-corporate-email"
      target_service       = "corporate-email"
      protocol             = "mcp"
      role                 = "roles/iap.egressor"
      allowed_principals   = ["ai-coe-mortgage-specialist"]
      denied_principals    = ["cx-mortgage-orchestrator"]
      tools                = ["send_email", "read_email"]
      semantic_policy_refs = ["corporate-email-nlc"]
    }

    "mcp-legacy-dms" = {
      uap_id               = "mcp-legacy-dms"
      target_service       = "legacy-dms"
      protocol             = "mcp"
      role                 = "roles/iap.egressor"
      allowed_principals   = ["ai-coe-mortgage-specialist"]
      denied_principals    = ["cx-mortgage-orchestrator"]
      tools                = ["search_documents", "get_document"]
      semantic_policy_refs = ["legacy-dms-nlc"]
    }

    "mcp-income-verification" = {
      uap_id               = "mcp-income-verification"
      target_service       = "income-verification"
      protocol             = "mcp"
      role                 = "roles/iap.egressor"
      allowed_principals   = ["ai-coe-mortgage-specialist"]
      denied_principals    = ["cx-mortgage-orchestrator"]
      tools                = ["verify_applicant"]
      semantic_policy_refs = ["income-verification-nlc"]
    }
  }

  semantic_governance_active = (
    var.enable_semantic_governance ||
    var.content_safety_provider == "agent_gateway"
  )

  policy_bundle = {
    schema_version               = "geap.semantic-governance/v1"
    environment                  = var.environment
    region                       = var.region
    enabled                      = local.semantic_governance_active
    secret_publication_enabled   = var.enable_semantic_governance
    enforcement_mode             = var.enforcement_mode
    content_safety_provider      = var.content_safety_provider
    egress_allowlist_provider    = var.egress_allowlist_provider
    semantic_governance_policies = local.semantic_governance_policies
    uap_bindings                 = local.uap_bindings
  }

  policy_bundle_json = jsonencode(local.policy_bundle)
}

resource "terraform_data" "semantic_governance_contract" {
  input = local.policy_bundle

  lifecycle {
    precondition {
      condition = alltrue([
        for policy in values(local.semantic_governance_policies) :
        length(policy.tools) > 0 && length(policy.natural_language_constraints) > 0
      ])
      error_message = "Every GEAP Semantic Governance policy must declare at least one target tool and one Natural Language Constraint (NLC)."
    }

    precondition {
      condition = alltrue([
        for policy in values(local.semantic_governance_policies) :
        trimspace(policy.principal) != "" && policy.principal != "*" && ! strcontains(policy.principal, "/*")
      ])
      error_message = "GEAP Semantic Governance policies must bind an explicit workload principal (wildcard principals are forbidden)."
    }

    precondition {
      condition     = !var.enable_semantic_governance || trimspace(var.governance_project_id) != ""
      error_message = "governance_project_id must be non-empty when enable_semantic_governance = true."
    }
  }
}

# Optional Secret Manager artifact publication in the central governance project
# when enable_semantic_governance = true. Defaults to disabled so offline plans
# and unit tests run without requiring live GCP API calls or unreleased provider
# resources.
resource "google_secret_manager_secret" "semantic_governance_bundle" {
  count     = var.enable_semantic_governance ? 1 : 0
  project   = var.governance_project_id
  secret_id = "esmeralda-semantic-governance-policies-${var.environment}"

  replication {
    auto {}
  }

  labels = {
    environment = var.environment
    component   = "semantic-governance"
    managed_by  = "terraform"
  }
}

resource "google_secret_manager_secret_version" "semantic_governance_bundle" {
  count       = var.enable_semantic_governance ? 1 : 0
  secret      = google_secret_manager_secret.semantic_governance_bundle[0].id
  secret_data = local.policy_bundle_json
}
