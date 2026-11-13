terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 5.0.0"
    }
    null = {
      source  = "hashicorp/null"
      version = ">= 3.0.0"
    }
  }
}

# Pin the Cloud Run revision to the image *digest* the tag points to right now.
locals {
  image_input      = var.container_image
  image_resolvable = can(regex("^[a-z0-9-]+-docker\\.pkg\\.dev/[^/]+/[^/]+/[^/@]+:[^/@]+$", local.image_input))
  image_parts      = split("/", local.image_input)
  image_name_tag   = local.image_resolvable ? local.image_parts[3] : ""
}

data "google_artifact_registry_docker_image" "image" {
  count         = local.image_resolvable ? 1 : 0
  project       = local.image_parts[1]
  location      = replace(local.image_parts[0], "-docker.pkg.dev", "")
  repository_id = local.image_parts[2]
  image_name    = local.image_name_tag
}

locals {
  image_by_digest = local.image_resolvable ? "${join("/", slice(local.image_parts, 0, 3))}/${split(":", local.image_name_tag)[0]}@${split("@", data.google_artifact_registry_docker_image.image[0].name)[1]}" : local.image_input

  effective_register_server_name = var.register_server_name != "" ? var.register_server_name : var.service_name
  effective_display_name         = var.display_name != "" ? var.display_name : var.service_name

  default_custom_audiences = [
    "http://${var.internal_hostname}",
    "http://${var.internal_hostname}/mcp",
    "https://${var.internal_hostname}",
    "https://${var.internal_hostname}/mcp",
  ]
  effective_custom_audiences = length(var.custom_audiences) > 0 ? var.custom_audiences : local.default_custom_audiences

  # Catalog caller guard (caller_guard.py in the image): the catalog-compiled allowlist of this
  # target (catalog/.resolved/<env>.json attribution.allowlists) with its `<unit>:<output>`
  # references replaced by identity_refs values (agents call out with their own Agent Identity, so
  # their `agents/<name>:agent_identity_principal` entries resolve too). Any reference left unresolved
  # fails the plan.
  ref_pattern = "^[a-z0-9_./-]+:[a-z0-9_]+$"
  caller_guard_callers = var.caller_allowlist == null ? [] : concat(
    [
      for entry in var.caller_allowlist.callers : merge(entry, { principal = lookup(var.identity_refs, entry.principal, entry.principal) })
    ],
    var.extra_allowed_callers,
  )
  caller_guard_unresolved = [for entry in local.caller_guard_callers : entry.principal if can(regex(local.ref_pattern, entry.principal))]

  # roles/run.invoker members: service account emails, full members (serviceAccount:, principal://,
  # principalSet://) or `<unit>:<output>` references resolved through identity_refs (a calling agent's
  # Agent Identity principal).
  invoker_members = distinct([
    for m in [for v in var.invoker_service_accounts : lookup(var.identity_refs, v, v) if v != "" && v != null] :
    startswith(m, "serviceAccount:") || startswith(m, "principal://") || startswith(m, "principalSet://") ? m : "serviceAccount:${m}"
  ])
  invoker_unresolved = [for v in var.invoker_service_accounts : v if can(regex(local.ref_pattern, lookup(var.identity_refs, v, v)))]

  # A Google ID token minted without the email claim (VM metadata server default, Apigee
  # GoogleIDToken without IncludeEmail) names a service account only by its numeric unique id in
  # `sub`: each service-account entry is also listed under that id, with the same caller and team.
  caller_guard_sa_emails = toset([
    for entry in local.caller_guard_callers : entry.principal
    if can(regex("^[^@:/]+@[a-z0-9-]+\\.iam\\.gserviceaccount\\.com$", entry.principal))
  ])
  caller_guard_aliases = [
    for entry in local.caller_guard_callers : merge(entry, { principal = data.google_service_account.caller_guard[entry.principal].unique_id })
    if contains(local.caller_guard_sa_emails, entry.principal)
  ]
  caller_guard_env = var.caller_allowlist == null ? {} : {
    ESMERALDA_CALLER_ALLOWLIST = jsonencode({
      policy  = var.caller_allowlist.policy
      callers = concat(local.caller_guard_callers, local.caller_guard_aliases)
      # This service requires IAM invoker auth (no allUsers binding) and internal ingress, so Cloud
      # Run has validated the caller's identity header before the request reaches the container.
      token_validated_by = "cloud_run_iam"
    })
  }
}

# Unique ids of the caller guard's service-account principals (see caller_guard_aliases).
data "google_service_account" "caller_guard" {
  for_each   = local.caller_guard_sa_emails
  account_id = split("@", each.value)[0]
  project    = regex("@([a-z0-9-]+)\\.iam\\.gserviceaccount\\.com$", each.value)[0]
}

# Dedicated per-service workload Service Account (sa-<service>-<env>)
resource "google_service_account" "service_sa" {
  account_id   = "sa-${var.service_name}-${var.environment}"
  display_name = "${local.effective_display_name} MCP Server Workload Service Account"
  project      = var.project_id
}

# Baseline observability, OTel telemetry export, and DLP permissions for the per-service workload identity
resource "google_project_iam_member" "service_sa_roles" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
    "roles/telemetry.writer",
    "roles/dlp.user",
  ])
  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.service_sa.email}"
}

# Shared VPC backend subnetwork permission for Direct VPC Egress (role parity with legacy mcps_subnet_user)
resource "google_compute_subnetwork_iam_member" "service_subnet_user" {
  project    = can(regex("^projects/[^/]+/regions/[^/]+/subnetworks/[^/]+$", var.subnet_id)) ? split("/", var.subnet_id)[1] : var.project_id
  region     = var.region
  subnetwork = var.subnet_id
  role       = "roles/compute.networkUser"
  member     = "serviceAccount:${google_service_account.service_sa.email}"
}

# Optional Secret Manager access grants for mounted/injected secrets
resource "google_secret_manager_secret_iam_member" "service_secret_access" {
  for_each  = var.secret_env_vars
  project   = var.project_id
  secret_id = each.value.secret
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.service_sa.email}"
}

# Deploy MCP service on Cloud Run v2 with internal-and-load-balancing ingress
resource "google_cloud_run_v2_service" "service" {
  name                = "${var.service_name}-${var.environment}"
  location            = var.region
  project             = var.project_id
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = false

  custom_audiences = local.effective_custom_audiences

  template {
    service_account = google_service_account.service_sa.email

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    containers {
      image = local.image_by_digest
      ports {
        container_port = 8080
      }
      resources {
        limits = {
          cpu    = var.cpu_limit
          memory = var.memory_limit
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      env {
        name  = "OTEL_EXPORTER_OTLP_ENDPOINT"
        value = "http://collector.telemetry.internal:4317"
      }
      env {
        name  = "ENVIRONMENT"
        value = var.environment
      }

      dynamic "env" {
        for_each = merge(var.extra_env_vars, local.caller_guard_env)
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = var.secret_env_vars
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value.secret
              version = coalesce(env.value.version, "latest")
            }
          }
        }
      }
    }

    # Direct VPC Egress: Binds Cloud Run container interface inside the Shared VPC
    vpc_access {
      network_interfaces {
        network    = var.network_id
        subnetwork = var.subnet_id
      }
      egress = "ALL_TRAFFIC"
    }
  }

  lifecycle {
    precondition {
      condition     = length(local.caller_guard_unresolved) == 0
      error_message = "caller_allowlist references without a value in identity_refs: ${join(", ", local.caller_guard_unresolved)}. Map them to unit outputs in the service's terragrunt unit."
    }
    precondition {
      condition     = length(local.invoker_unresolved) == 0
      error_message = "invoker_service_accounts references without a value in identity_refs: ${join(", ", local.invoker_unresolved)}. Map them to unit outputs in the service's terragrunt unit."
    }
  }
}

# IAM Invoker Binding restricting access to authorized callers only (authoritative for the role)
resource "google_cloud_run_v2_service_iam_binding" "invokers" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.service.name
  role     = "roles/run.invoker"
  members  = local.invoker_members
}

# Auto-registration of tools into local MCP registry post-deployment
resource "null_resource" "mcp_registration" {
  triggers = {
    service_uri = google_cloud_run_v2_service.service.uri
    image_uri   = local.image_by_digest
  }

  provisioner "local-exec" {
    command = <<EOT
      echo "📡 Registering ${local.effective_display_name} MCP Server with Google Cloud Agent Registry..."
      GIT_ROOT=$(git -C "${path.module}" rev-parse --show-toplevel 2>/dev/null) || { echo "ERROR: cannot locate the repository root from ${path.module}; run Terragrunt from a git checkout of this repository." >&2; exit 1; }
      python3 "$GIT_ROOT/apps/services/register_mcp.py" \
        --project_id="${var.project_id}" \
        --region="${var.region}" \
        --server_name="${local.effective_register_server_name}" \
        --server_url="${google_cloud_run_v2_service.service.uri}"
    EOT
  }
}

# MCP server entry in the central governance Agent Registry. The Agent Gateway only lets agents
# egress to registered hosts, so this is deploy-time, env-specific config (not part of the build).
resource "google_agent_registry_service" "governance_mcp" {
  provider     = google-beta
  project      = var.governance_project_id
  location     = var.region
  service_id   = var.service_name
  display_name = var.service_name
  description  = var.description

  interfaces {
    url              = "https://${var.internal_hostname}/mcp"
    protocol_binding = "JSONRPC"
  }

  mcp_server_spec {
    type    = "TOOL_SPEC"
    content = file(var.tools_spec_path)
  }
}
