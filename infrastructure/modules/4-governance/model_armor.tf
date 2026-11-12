# ==============================================================================
# MODEL ARMOR SWITCHES (env.yaml governance.model_armor)
# ==============================================================================
# Two independent inspection points, each behind its own switch:
#
#   model_calls: Vertex AI built-in Model Armor on the agents' Gemini calls
#                (generateContent and streamGenerateContent). A Model Armor floor setting
#                with the Vertex AI integration is set on every agent project. It needs a
#                regional model endpoint, so the agents get MODEL_LOCATION = region
#                (output model_armor_model_location, read by the layer-5 agent units).
#   gateway:     Agent Gateway CONTENT_AUTHZ: the gateway sends unary MCP tools/call and
#                prompts/get payloads and agent cards to Model Armor. A2A messages, model
#                calls and streams are not matched (see docs: Central Agent Gateway, Model
#                Armor coverage).
#
# Coverage, limits and the prd recommendations are in
# docs/src/3-agentops-and-lifecycle/01-central-agent-gateway.md (Model Armor section).

variable "model_armor_config" {
  description = <<-EOT
    Model Armor switches (env.yaml governance.model_armor):
      model_calls:             "off" | "inspect" | "enforce". Vertex AI built-in Model Armor on the
                               agents' Gemini calls (floor setting on every agent project). "inspect"
                               only logs findings; "enforce" blocks. Requires a regional model
                               endpoint (agents get MODEL_LOCATION = region).
      gateway:                 "off" | "on". Agent Gateway CONTENT_AUTHZ on unary MCP tools/call and
                               prompts/get and on agent cards. Needs capabilities.content_safety =
                               "agent_gateway".
      gateway_fail_open:       Let traffic through when Model Armor errors or times out (true) or
                               reject it (false). false is the recommended production value.
      gateway_timeout_ms:      Per-message timeout of the gateway callout, 10-10000 ms.
      model_calls_sdp:         Also run the Sensitive Data Protection basic filter on model calls. The
                               Vertex AI integration blocks on an SDP match (it never masks), so
                               this is off by default: data such as account or tax numbers would
                               block legitimate turns.
      enable_cloud_logging:    Log the Vertex AI integration's findings to Cloud Logging.
  EOT
  type = object({
    model_calls          = optional(string, "off")
    model_calls_sdp      = optional(bool, false)
    gateway              = optional(string, "off")
    gateway_fail_open    = optional(bool, false)
    gateway_timeout_ms   = optional(number, 10000)
    enable_cloud_logging = optional(bool, true)
  })
  default  = {}
  nullable = false

  validation {
    condition     = contains(["off", "inspect", "enforce"], var.model_armor_config.model_calls)
    error_message = "model_armor_config.model_calls must be one of: \"off\", \"inspect\", \"enforce\"."
  }

  validation {
    condition     = contains(["off", "on"], var.model_armor_config.gateway)
    error_message = "model_armor_config.gateway must be \"off\" or \"on\"."
  }

  validation {
    condition     = var.model_armor_config.gateway_timeout_ms >= 10 && var.model_armor_config.gateway_timeout_ms <= 10000
    error_message = "model_armor_config.gateway_timeout_ms must be between 10 and 10000 (the authz extension limit)."
  }
}

locals {
  model_armor_model_calls = var.model_armor_config.model_calls
  # Gateway inspection is part of the content_safety capability: only when Agent Gateway serves it.
  model_armor_gateway_enabled = (
    var.model_armor_config.gateway == "on" &&
    try(var.governance_config.capabilities.content_safety, "agent_gateway") == "agent_gateway"
  )
}

output "model_armor_model_location" {
  value       = local.model_armor_model_calls != "off" ? var.region : ""
  description = "Location the agents must call Gemini in (MODEL_LOCATION) for Vertex AI built-in Model Armor; empty when model_calls is off (agents keep their agent.yaml value)."
}

output "model_armor" {
  value = {
    model_calls        = local.model_armor_model_calls
    model_calls_sdp    = var.model_armor_config.model_calls_sdp
    gateway            = local.model_armor_gateway_enabled ? "on" : "off"
    gateway_fail_open  = var.model_armor_config.gateway_fail_open
    gateway_timeout_ms = var.model_armor_config.gateway_timeout_ms
    agent_project_ids  = var.agent_project_ids
    region             = var.region
  }
  description = "Effective Model Armor switches (read by `esmeralda verify --model-armor`)."
}
