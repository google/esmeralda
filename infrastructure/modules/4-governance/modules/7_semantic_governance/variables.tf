# ==============================================================================
# SUBMODULE 7: GEAP SEMANTIC GOVERNANCE POLICIES (NLC) & UNIFIED ACCESS POLICIES
# ==============================================================================

variable "governance_project_id" {
  type        = string
  default     = ""
  description = "Central Governance GCP Project ID hosting the Central Agent Gateway and policy bundle secrets."
}

variable "environment" {
  type        = string
  default     = "dev"
  description = "Target deployment environment (dev, staging, prd)."
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "Target Google Cloud region for the Central Agent Gateway."
}

variable "enable_semantic_governance" {
  type        = bool
  default     = false
  description = "Toggle to publish the compiled GEAP Semantic Governance (NLC) and Unified Access Policy (UAP) bundle to Secret Manager in the governance project."
}

variable "enforcement_mode" {
  type        = string
  default     = "ENFORCE"
  description = "Default GEAP Semantic Governance enforcement mode across Natural Language Constraints (ENFORCE or DRY_RUN)."

  validation {
    condition     = contains(["ENFORCE", "DRY_RUN"], var.enforcement_mode)
    error_message = "enforcement_mode must be either \"ENFORCE\" or \"DRY_RUN\"."
  }
}

variable "internal_underwriting_domain" {
  type        = string
  default     = "megabank.com"
  description = "Authorized internal recipient email domain for underwriting notifications via corporate-email."
}

variable "content_safety_provider" {
  type        = string
  default     = "agent_gateway"
  description = "Active capability provider for content_safety from Layer 4 governance_config.capabilities."
}

variable "egress_allowlist_provider" {
  type        = string
  default     = "agent_gateway"
  description = "Active capability provider for egress_allowlist from Layer 4 governance_config.capabilities."
}

variable "custom_policies" {
  type = map(object({
    target_mcp_server           = string
    tools                       = list(string)
    principal                   = string
    enforcement_mode            = optional(string)
    natural_language_constraints = list(string)
  }))
  default     = {}
  nullable    = false
  description = "Optional custom Natural Language Constraint (NLC) policies merged with the default mortgage underwriting policies."
}
