# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Adapted from github.com/ricardosmh/adk-apigee-multiagent-demo (Apache-2.0),
# replacing the imperative YAML provisioner with declarative Terraform for
# API products, server-side MCP tool scoping, LLM token quota attributes,
# developer apps, and Secret Manager consumer key synchronization.

locals {
  unique_developers = {
    for email in toset([for c in values(var.consumers) : lower(c.developer_email)]) :
    email => email
  }

  secret_access_grants = merge([
    for consumer_name, consumer in var.consumers : {
      for sa in toset(concat(var.secret_accessor_service_accounts, consumer.secret_accessor_service_accounts)) :
      "${consumer_name}:${sa}" => {
        consumer_name = consumer_name
        sa_email      = sa
      }
    }
  ]...)
}

# ====================================================================
# 1. API Products (Server-Side MCP Tool Scoping + LLM Token Quotas)
# ====================================================================

resource "google_apigee_api_product" "product" {
  for_each = var.products

  org_id        = var.org_id
  name          = each.key
  display_name  = each.value.display_name
  description   = each.value.description
  approval_type = each.value.approval_type
  environments  = [var.apigee_env_name]
  proxies       = length(each.value.proxies) > 0 ? each.value.proxies : null
  api_resources = each.value.allowed_paths

  attributes {
    name  = "access"
    value = "private"
  }

  attributes {
    name  = "allowed_mcp_tools"
    value = join(",", each.value.allowed_mcp_tools)
  }

  attributes {
    name  = "llm_quota_limit"
    value = tostring(each.value.llm_quota_limit)
  }

  attributes {
    name  = "llm_quota_interval"
    value = tostring(each.value.llm_quota_interval)
  }

  attributes {
    name  = "llm_quota_time_unit"
    value = each.value.llm_quota_time_unit
  }
}

# ====================================================================
# 2. Developers & Consumer Apps (Key-to-Identity Binding)
# ====================================================================

resource "google_apigee_developer" "developer" {
  for_each = local.unique_developers

  org_id     = var.org_id
  email      = each.value
  first_name = "Esmeralda"
  last_name  = "Partner"
  user_name  = replace(split("@", each.value)[0], ".", "-")
}

resource "google_apigee_developer_app" "consumer" {
  for_each = var.consumers

  org_id          = var.org_id
  name            = each.key
  developer_email = google_apigee_developer.developer[lower(each.value.developer_email)].email
  callback_url    = each.value.callback_url
  api_products    = [for p in each.value.products : google_apigee_api_product.product[p].name]
  status          = "approved"

  attributes {
    name  = "owner_identity"
    value = each.value.owner_identity
  }

  attributes {
    name  = "display_name"
    value = each.value.display_name
  }
}

# ====================================================================
# 3. Secret Manager Bridge for Consumer API Keys
# ====================================================================

resource "google_secret_manager_secret" "consumer_key" {
  for_each = var.consumers

  project   = var.project_id
  secret_id = "apigee-consumer-key-${each.key}-${var.environment}"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "consumer_key" {
  for_each = var.consumers

  secret      = google_secret_manager_secret.consumer_key[each.key].id
  secret_data = sensitive(google_apigee_developer_app.consumer[each.key].credentials[0].consumer_key)
}

resource "google_secret_manager_secret_iam_member" "consumer_key_accessor" {
  for_each = local.secret_access_grants

  project   = var.project_id
  secret_id = google_secret_manager_secret.consumer_key[each.value.consumer_name].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${each.value.sa_email}"
}
