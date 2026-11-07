# ==============================================================================
# OUTPUTS: SUBMODULE 7 — GEAP SEMANTIC GOVERNANCE & UAPs
# ==============================================================================

output "semantic_governance_policies" {
  value       = terraform_data.semantic_governance_contract.output.semantic_governance_policies
  description = "Map of declarative GEAP Semantic Governance (Natural Language Constraint) policies keyed by policy ID."
}

output "uap_bindings" {
  value       = terraform_data.semantic_governance_contract.output.uap_bindings
  description = "Map of Unified Access Policy (UAP) bindings pairing attested agent principals with MCP/A2A targets and NLC policy references."
}

output "policy_bundle_json" {
  value       = local.policy_bundle_json
  description = "JSON-serialized GEAP Semantic Governance and Unified Access Policy bundle for Central Agent Gateway enforcement."
}

output "enabled" {
  value       = terraform_data.semantic_governance_contract.output.enabled
  description = "Whether GEAP Semantic Governance policy enforcement is active for the Central Agent Gateway."
}

output "policy_secret_id" {
  value       = try(google_secret_manager_secret.semantic_governance_bundle[0].secret_id, "")
  description = "Secret Manager secret ID storing the compiled Semantic Governance policy bundle when enable_semantic_governance = true."
}

output "manifest" {
  value       = terraform_data.semantic_governance_contract.output
  description = "Full compiled GEAP Semantic Governance & Unified Access Policy manifest."
}
