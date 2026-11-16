# infrastructure/modules/5-workloads/iap-egress/runtime_catalog.tf
# ====================================================================
# Canonical Runtime Catalog Store (Governance Secret Manager + BigQuery)
# --------------------------------------------------------------------
# In Esmeralda, catalog/.resolved/<env>.json in Git contains symbolic `<unit>:<output>` references
# and `{domain}` placeholders. Only Layer 5 `iap-egress` + Layer 4 `governance` has the complete
# `identity_refs` map, resolved `{domain}`, and active `governance_capabilities`.
#
# This file constructs the fully bound `esmeralda.runtime_catalog` document and:
#   1. Publishes it to Secret Manager (`esmeralda-runtime-catalog-<env>`) in `governance_project_id`
#      so runtime consumers (such as `esmeralda-ui`) can read the bound catalog without touching
#      Git YAML or unresolved refs.
#   2. Loads the `source = 'catalog'` target rows into the Layer 4 `catalog_targets` BigQuery table
#      using the same transactional `random_id` + `sha256` DML pattern as `attribution.tf`.
# ====================================================================

locals {
  raw_catalog          = try(var.catalog_resolved.catalog, {})
  raw_attribution      = try(var.catalog_resolved.attribution, {})
  raw_allowlists       = try(local.raw_attribution.allowlists, {})
  raw_principal_teams  = length(var.principal_teams_rows) > 0 ? var.principal_teams_rows : try(local.raw_attribution.principal_teams, [])
  raw_external_targets = length(var.external_targets) > 0 ? var.external_targets : try(var.catalog_resolved.external_targets, {})

  # Caller lookup map from principal_teams rows: caller name -> { principal, principal_type, team, cost_centers }
  caller_lookup = {
    for r in local.raw_principal_teams : r.caller => {
      caller         = r.caller
      principal      = lookup(var.identity_refs, r.principal, r.principal)
      principal_type = r.principal_type
      team           = r.team
      cost_centers   = try(r.cost_centers, [])
    }
    if try(r.caller, "") != ""
  }

  # Target owner_team inferred when not explicitly present in the compiled catalog entry:
  # agent targets have a matching caller row in principal_teams; otherwise check owner_team on the entry.
  managed_runtime_targets = {
    for name, entry in local.raw_catalog : name => {
      target     = name
      kind       = entry.kind
      managed    = true
      owner_team = try(entry.owner_team, lookup(local.caller_lookup, name, { team = null }).team)
      unit       = try(entry.unit, null)
      endpoint   = var.domain != "" ? "https://${name}.${var.domain}" : "https://${name}"
      token      = try(entry.token, null)
      probe      = try(entry.probe, null)
      timeout    = try(entry.timeout, null)
      callers    = try(entry.callers, [])
      resolved_callers = try(
        [
          for a in local.raw_allowlists[name] : {
            caller       = a.caller
            principal    = lookup(var.identity_refs, a.principal, a.principal)
            team         = a.team
            cost_centers = try(a.cost_centers, [])
          }
        ],
        [
          for c in try(entry.callers, []) : {
            caller       = c
            principal    = try(local.caller_lookup[c].principal, c)
            team         = try(local.caller_lookup[c].team, null)
            cost_centers = try(local.caller_lookup[c].cost_centers, [])
          }
        ]
      )
    }
  }

  external_runtime_targets = {
    for name, t in local.raw_external_targets : name => {
      target           = name
      kind             = t.kind
      managed          = false
      owner_team       = try(t.owner_team, "") != "" ? t.owner_team : null
      unit             = null
      endpoint         = replace(replace(t.url, "{domain}", var.domain), "{region}", var.region)
      token            = null
      probe            = null
      timeout          = null
      callers          = []
      resolved_callers = [
        for m in lookup(
          local.resolved_target_iap_members[lookup({ endpoint = "endpoints", mcp_server = "mcp_servers", agent = "agents" }, t.registry, "endpoints")],
          name,
          []
        ) : {
          caller       = m
          principal    = m
          team         = null
          cost_centers = []
        }
      ]
    }
  }

  runtime_catalog_targets = merge(local.external_runtime_targets, local.managed_runtime_targets)

  unresolved_runtime_catalog_refs = distinct(flatten([
    for name, t in local.runtime_catalog_targets : [
      for c in t.resolved_callers : c.principal
      if c.principal != null && can(regex(local.ref_pattern, c.principal))
    ]
  ]))

  runtime_catalog_principal_rows = [
    for r in local.raw_principal_teams : {
      principal      = lookup(var.identity_refs, r.principal, r.principal)
      principal_type = r.principal_type
      team           = r.team
    }
  ]

  runtime_catalog_payload = {
    schema         = "esmeralda.runtime_catalog"
    schema_version = 1
    environment    = var.environment
    domain         = var.domain
    capabilities   = var.governance_capabilities
    targets        = local.runtime_catalog_targets
    attribution = {
      policy          = try(local.raw_attribution.policy, { unmapped_caller = "deny", declared_mismatch = "reject" })
      principal_teams = local.runtime_catalog_principal_rows
    }
  }

  catalog_targets_load_enabled = var.runtime_catalog_enabled && var.catalog_targets_table != ""

  catalog_targets_rows = [
    for name in sort(keys(local.runtime_catalog_targets)) : {
      target       = name
      kind         = local.runtime_catalog_targets[name].kind
      owner_team   = local.runtime_catalog_targets[name].owner_team
      unit         = local.runtime_catalog_targets[name].unit
      endpoint     = local.runtime_catalog_targets[name].endpoint
      token        = local.runtime_catalog_targets[name].token
      probe        = local.runtime_catalog_targets[name].probe
      callers_json = jsonencode(local.runtime_catalog_targets[name].resolved_callers)
    }
  ]

  catalog_targets_values = join(",\n  ", [
    for r in local.catalog_targets_rows :
    "(${jsonencode(r.target)}, ${jsonencode(r.kind)}, ${r.owner_team == null ? "CAST(NULL AS STRING)" : jsonencode(r.owner_team)}, ${r.unit == null ? "CAST(NULL AS STRING)" : jsonencode(r.unit)}, ${r.endpoint == null ? "CAST(NULL AS STRING)" : jsonencode(r.endpoint)}, ${r.token == null ? "CAST(NULL AS STRING)" : jsonencode(r.token)}, ${r.probe == null ? "CAST(NULL AS STRING)" : jsonencode(r.probe)}, ${jsonencode(r.callers_json)}, 'catalog')"
  ])

  catalog_targets_sql = join("\n", compact([
    "BEGIN TRANSACTION;",
    "DELETE FROM `${var.catalog_targets_table}` WHERE source = 'catalog';",
    length(local.catalog_targets_rows) == 0 ? "" : join("\n", [
      "INSERT INTO `${var.catalog_targets_table}` (target, kind, owner_team, unit, endpoint, token, probe, callers_json, source)",
      "SELECT * FROM UNNEST(ARRAY<STRUCT<target STRING, kind STRING, owner_team STRING, unit STRING, endpoint STRING, token STRING, probe STRING, callers_json STRING, source STRING>>[",
      "  ${local.catalog_targets_values}",
      "]);",
    ]),
    "COMMIT TRANSACTION;",
  ]))
}

resource "google_secret_manager_secret" "runtime_catalog" {
  count     = var.runtime_catalog_enabled ? 1 : 0
  project   = var.governance_project_id
  secret_id = "esmeralda-runtime-catalog-${var.environment}"

  replication {
    auto {}
  }

  labels = {
    environment = var.environment
    component   = "runtime-catalog"
    managed_by  = "terraform"
  }

  lifecycle {
    precondition {
      condition     = trimspace(var.environment) != ""
      error_message = "runtime_catalog_enabled requires a non-empty environment name."
    }
    precondition {
      condition     = length(local.unresolved_runtime_catalog_refs) == 0
      error_message = "catalog_resolved references without a value in identity_refs: ${join(", ", local.unresolved_runtime_catalog_refs)}. Map them to unit outputs in the iap-egress terragrunt unit."
    }
  }
}

resource "google_secret_manager_secret_version" "runtime_catalog" {
  count       = var.runtime_catalog_enabled ? 1 : 0
  secret      = google_secret_manager_secret.runtime_catalog[0].id
  secret_data = jsonencode(local.runtime_catalog_payload)
}

resource "random_id" "catalog_targets_load" {
  count       = local.catalog_targets_load_enabled ? 1 : 0
  byte_length = 4
  keepers = {
    sql = sha256(local.catalog_targets_sql)
  }
}

resource "google_bigquery_job" "catalog_targets_catalog" {
  count    = local.catalog_targets_load_enabled ? 1 : 0
  project  = var.governance_project_id
  location = var.bigquery_location
  job_id   = "esmeralda_catalog_targets_catalog_${substr(sha256(local.catalog_targets_sql), 0, 12)}_${random_id.catalog_targets_load[0].hex}"

  labels = {
    writer = "catalog"
  }

  query {
    query              = local.catalog_targets_sql
    use_legacy_sql     = false
    create_disposition = ""
    write_disposition  = ""
  }

  lifecycle {
    precondition {
      condition     = length(local.unresolved_runtime_catalog_refs) == 0
      error_message = "catalog_targets_rows references without a value in identity_refs: ${join(", ", local.unresolved_runtime_catalog_refs)}. Map them to unit outputs in the iap-egress terragrunt unit."
    }
  }
}
