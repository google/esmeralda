# ==============================================================================
# OUTPUTS: SUBMODULE 7 — SEMANTIC GOVERNANCE POLICY MANIFEST (not enforced)
# ==============================================================================

output "semantic_governance_policies" {
  value       = terraform_data.semantic_governance_contract.output.semantic_governance_policies
  description = "Map of declarative Semantic Governance (Natural Language Constraint) policies keyed by policy ID. Manifest content only: not enforced by any component."
}

output "uap_bindings" {
  value       = terraform_data.semantic_governance_contract.output.uap_bindings
  description = "Map of access bindings pairing agent principals with MCP/A2A targets and NLC policy references. Descriptive only: access is enforced by the per-target IAP egress grants (iap-egress unit)."
}

output "policy_bundle_json" {
  value       = local.policy_bundle_json
  description = "JSON-serialized Semantic Governance policy manifest (geap.semantic-governance/v1) for a future or external enforcement point. Not consumed by the Central Agent Gateway."
}

output "enabled" {
  value       = terraform_data.semantic_governance_contract.output.enabled
  description = "Manifest 'enabled' flag (true when publication is on or content_safety = agent_gateway). Metadata only: it does not turn on any enforcement."
}

output "policy_secret_id" {
  value       = try(google_secret_manager_secret.semantic_governance_bundle[0].secret_id, "")
  description = "Secret Manager secret ID storing the compiled Semantic Governance policy manifest when enable_semantic_governance = true."
}

output "manifest" {
  value       = terraform_data.semantic_governance_contract.output
  description = "Full compiled Semantic Governance policy manifest (geap.semantic-governance/v1). Manifest only: not enforced."
}
