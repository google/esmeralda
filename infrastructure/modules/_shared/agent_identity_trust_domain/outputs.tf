output "project_number" {
  description = "Numeric project number."
  value       = data.google_project.this.number
}

output "org_id" {
  description = "Organization ID the trust domain is scoped to (empty for a project with no organization)."
  value       = module.resolve.org_id
}

output "trust_domain" {
  description = "Agent Identity trust domain, scoped to org-<ORG_ID> (or to proj-<NUMBER> for a project with no organization)."
  value       = module.resolve.trust_domain
}

output "platform_container_principal_set" {
  description = "PrincipalSet of every Agent Runtime engine of the project."
  value       = "principalSet://${module.resolve.trust_domain}/attribute.platformContainer/aiplatform/projects/${data.google_project.this.number}"
}

output "container_principal_set" {
  description = "PrincipalSet of every Agent Identity whose container is the project."
  value       = "principalSet://${module.resolve.trust_domain}/attribute.container/projects/${data.google_project.this.number}"
}
