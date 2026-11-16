# infrastructure/modules/_shared/agent_identity_trust_domain/main.tf
# ==============================================================================
# AGENT IDENTITY TRUST DOMAIN OF A PROJECT
# ==============================================================================
# Every module that builds an Agent Identity principal (principal://, principalSet://, spiffe://)
# takes the trust domain from here, so the expression exists once (resolve/main.tf).
#
# google_project.org_id is set only when the organization is the project's direct parent. For a
# project under a folder (folder_id in env.yaml) it is empty, so the organization is looked up from
# the folder (lookup_organization walks nested folders up to the organization).

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
}

data "google_project" "this" {
  project_id = var.project_id
}

data "google_folder" "parent" {
  count               = data.google_project.this.org_id == "" && data.google_project.this.folder_id != "" ? 1 : 0
  folder              = "folders/${data.google_project.this.folder_id}"
  lookup_organization = true
}

module "resolve" {
  source = "./resolve"

  project_number      = data.google_project.this.number
  project_org_id      = data.google_project.this.org_id
  project_folder_id   = data.google_project.this.folder_id
  folder_organization = try(data.google_folder.parent[0].organization, "")
  org_id              = var.org_id
}
