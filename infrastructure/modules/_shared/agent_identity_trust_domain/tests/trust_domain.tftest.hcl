# Shared trust domain module with a mocked google provider: which lookup runs, and what comes out.
# Run by tests/security/test_agent_identity_trust_domain.py (terraform test).

mock_provider "google" {}

variables {
  project_id = "esm-dev-cx-agents-sfx"
}

run "project_under_a_folder_uses_the_folder_organization" {
  command = apply

  override_data {
    target = data.google_project.this
    values = {
      number    = "111"
      org_id    = ""
      folder_id = "555"
    }
  }

  override_data {
    target = data.google_folder.parent
    values = {
      organization = "organizations/1234567890"
    }
  }

  assert {
    condition     = length(data.google_folder.parent) == 1
    error_message = "A project under a folder must look up the folder's organization."
  }

  assert {
    condition     = output.trust_domain == "agents.global.org-1234567890.system.id.goog"
    error_message = "Wrong trust domain for a project under a folder: ${output.trust_domain}"
  }

  assert {
    condition     = output.platform_container_principal_set == "principalSet://agents.global.org-1234567890.system.id.goog/attribute.platformContainer/aiplatform/projects/111"
    error_message = "Wrong platformContainer principalSet: ${output.platform_container_principal_set}"
  }

  assert {
    condition     = output.container_principal_set == "principalSet://agents.global.org-1234567890.system.id.goog/attribute.container/projects/111"
    error_message = "Wrong container principalSet: ${output.container_principal_set}"
  }
}

run "project_directly_under_the_organization" {
  command = apply

  override_data {
    target = data.google_project.this
    values = {
      number    = "222"
      org_id    = "1234567890"
      folder_id = ""
    }
  }

  assert {
    condition     = length(data.google_folder.parent) == 0
    error_message = "No folder lookup for a project whose parent is the organization."
  }

  assert {
    condition     = output.trust_domain == "agents.global.org-1234567890.system.id.goog"
    error_message = "Wrong trust domain for a project under the organization: ${output.trust_domain}"
  }
}

run "project_without_organization" {
  command = apply

  override_data {
    target = data.google_project.this
    values = {
      number    = "333"
      org_id    = ""
      folder_id = ""
    }
  }

  assert {
    condition     = length(data.google_folder.parent) == 0
    error_message = "No folder lookup for a project with no parent."
  }

  assert {
    condition     = output.trust_domain == "agents.global.proj-333.system.id.goog"
    error_message = "Wrong trust domain for a project with no organization: ${output.trust_domain}"
  }
}
