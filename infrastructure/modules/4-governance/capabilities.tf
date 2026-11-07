# ==============================================================================
# CAPABILITY-BASED GOVERNANCE CONTRACT
# ==============================================================================
# Agent Gateway is Esmeralda's governance plane for agent traffic. Apigee can
# optionally add API-management capabilities, such as partner access, API
# products and token quotas, through independent modules selected per
# capability in env.yaml.

locals {
  allowed_capability_providers = ["none", "agent_gateway", "apigee"]
  allowed_apigee_env_types     = ["BASE", "INTERMEDIATE", "COMPREHENSIVE"]

  apigee_capabilities = [
    for k, v in var.governance_config.capabilities : k
    if v == "apigee"
  ]
}

variable "governance_config" {
  description = <<-EOT
    Per-environment governance plane configuration (env.yaml governance block):
      apigee:       platform toggle (enabled, byo, environment_type)
      capabilities: provider selection per governance capability ("agent_gateway", "apigee", or "none")
  EOT
  type = object({
    apigee = optional(object({
      enabled          = optional(bool, false)
      byo              = optional(bool, false)
      environment_type = optional(string, "BASE")
    }), {})
    capabilities = optional(object({
      egress_allowlist = optional(string, "agent_gateway")
      content_safety   = optional(string, "agent_gateway")
      partner_access   = optional(string, "none")
      token_quota      = optional(string, "none")
      usage_metering   = optional(string, "agent_gateway")
      cross_cloud      = optional(string, "agent_gateway")
    }), {})
  })
  default  = {}
  nullable = false

  validation {
    condition     = contains(["BASE", "INTERMEDIATE", "COMPREHENSIVE"], var.governance_config.apigee.environment_type)
    error_message = "governance_config.apigee.environment_type must be one of: BASE, INTERMEDIATE, COMPREHENSIVE."
  }

  validation {
    condition = alltrue([
      for provider in values(var.governance_config.capabilities) :
      contains(["none", "agent_gateway", "apigee"], provider)
    ])
    error_message = "Every capability in governance_config.capabilities must use an allowed provider: \"none\", \"agent_gateway\", or \"apigee\"."
  }

  validation {
    condition     = var.governance_config.capabilities.egress_allowlist != "none"
    error_message = "governance_config.capabilities.egress_allowlist cannot be \"none\": agent egress must always be governed."
  }

  validation {
    condition = (
      var.governance_config.apigee.enabled ||
      alltrue([for provider in values(var.governance_config.capabilities) : provider != "apigee"])
    )
    error_message = "Capabilities cannot use provider \"apigee\" when governance_config.apigee.enabled is false."
  }

  validation {
    condition = (
      var.governance_config.capabilities.token_quota != "apigee" ||
      contains(["INTERMEDIATE", "COMPREHENSIVE"], var.governance_config.apigee.environment_type)
    )
    error_message = "governance_config.capabilities.token_quota = \"apigee\" requires governance_config.apigee.environment_type to be \"INTERMEDIATE\" or \"COMPREHENSIVE\" (LLMTokenQuota is not supported on BASE environments)."
  }
}

resource "terraform_data" "capabilities_contract" {
  input = {
    apigee       = var.governance_config.apigee
    capabilities = var.governance_config.capabilities
  }

  lifecycle {
    precondition {
      condition     = var.governance_config.apigee.enabled || length(local.apigee_capabilities) == 0
      error_message = "Apigee capability modules [${join(", ", local.apigee_capabilities)}] require governance_config.apigee.enabled = true."
    }

    precondition {
      condition = (
        var.governance_config.capabilities.token_quota != "apigee" ||
        contains(["INTERMEDIATE", "COMPREHENSIVE"], var.governance_config.apigee.environment_type)
      )
      error_message = "token_quota = \"apigee\" requires an INTERMEDIATE or COMPREHENSIVE Apigee environment (current: ${var.governance_config.apigee.environment_type})."
    }
  }
}

output "capabilities" {
  value       = terraform_data.capabilities_contract.output.capabilities
  description = "Active provider map for each governance capability (egress_allowlist, content_safety, partner_access, token_quota, usage_metering, cross_cloud)."
}

output "apigee_enabled" {
  value       = terraform_data.capabilities_contract.output.apigee.enabled
  description = "Whether the optional Apigee API-management platform is enabled in governance_config."
}
