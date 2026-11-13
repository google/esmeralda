# infrastructure/live/prd/layer-5-workloads/gateways/envoy/terragrunt.hcl
# Layer 5 gateway: Envoy on Cloud Run + serverless NEG + regional backend service (backend_service_id).
# It owns no load balancer, certificate or DNS record: the layer-5 entrypoint routes hosts to it.
# Applied after the MCP services and agents, so routes get the real engine IDs on the first apply.
# Mocks below only serve `validate`/`plan` of a not-yet-deployed env; `apply` requires real outputs.
include "root" {
  path = find_in_parent_folders("root.hcl")
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  catalog  = local.env_vars.locals.catalog
  gateways = local.env_vars.locals.gateways
  apigee_partner = (
    try(local.env_vars.locals.governance.apigee.enabled, false) &&
    try(local.env_vars.locals.governance.capabilities.partner_access, "none") == "apigee"
  )

  # Envoy re-signs every request it forwards with its own identity, so whoever may invoke Envoy reaches
  # every host routed to it. Its invokers are therefore the catalog callers of those hosts only (not
  # every agent): the same callers that get run.invoker / aiplatform.user on the targets themselves.
  envoy_callers = distinct(flatten([
    for name, entry in local.catalog : try(entry.callers, [])
    if lookup(local.gateways.host_rules, name, local.gateways.default) == "envoy"
  ]))
  envoy_agent_callers   = [for caller in local.envoy_callers : caller if contains(["a2a", "adk"], try(local.catalog[caller].kind, ""))]
  legacy_shared_invoker = try(local.env_vars.locals.legacy_shared_invoker, false)

  # Image: pinned by digest when the env lists it in image_digests (prd: release/prd.yaml,
  # written by `make promote`), otherwise <image>:<container_tag> (resolved to a digest by the module).
  image_name   = "envoy-gateway"
  image_digest = lookup(lookup(local.env_vars.locals, "image_digests", {}), local.image_name, "")
  image_ref    = local.image_digest != "" ? "${local.image_name}@${local.image_digest}" : "${local.image_name}:${local.env_vars.locals.container_tag}"
}

terraform {
  source = "../../../../../modules//5-workloads/gateways/envoy"
}

# Skipped by `run --all` when envoy is not in gateways.enabled (env.yaml).
exclude {
  if      = !contains(local.env_vars.locals.gateways.enabled, "envoy")
  actions = ["all"]
}

dependency "cicd" {
  config_path = "../../../../shared/layer-0-cicd"
}

dependency "projects" {
  config_path = "../../../layer-1-projects"
}

dependency "networking" {
  config_path = "../../../layer-2-networking"
  mock_outputs = {
    network_id        = "projects/mock/global/networks/mock"
    subnet_id         = "projects/mock/regions/us-central1/subnetworks/mock"
    entrypoint_domain = "esmeralda.internal"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
  mock_outputs_merge_strategy_with_state  = "shallow"
}

# Shallow merge: real outputs win; the mocks fill in what layer 3 has not output yet (e.g.
# envoy_sa_email before layer 3 is re-applied with the Envoy service account). Plan only.
dependency "security" {
  config_path = "../../../layer-3-security"
  mock_outputs = {
    envoy_sa_email                      = "sa-esmeralda-envoy-mock@mock.iam.gserviceaccount.com"
    test_vm_sa_email                    = "sa-test-vm-mock@mock.iam.gserviceaccount.com"
    ai_coe_mortgage_specialist_sa_email = "sa-spec-mock@mock.iam.gserviceaccount.com"
    cx_mortgage_orchestrator_sa_email   = "sa-orch-mock@mock.iam.gserviceaccount.com"
    mcp_invoker_sa_email                = "sa-mcp-invoker-mock@mock.iam.gserviceaccount.com"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
  mock_outputs_merge_strategy_with_state  = "shallow"
}

# Apigee partner front door (partner_access = "apigee"): its proxy runtime service account is the
# identity of the catalog caller apigee-partner-mcp.
dependency "apigee" {
  config_path = "../../apigee/platform"
  enabled     = local.apigee_partner
  mock_outputs = {
    proxy_runtime_sa_email = "sa-apigee-proxy-mock@mock.iam.gserviceaccount.com"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "ai_coe_mortgage_specialist" {
  config_path = "../../agents/ai-coe-mortgage-specialist"
  mock_outputs = {
    engine_id                = "mock-engine-id"
    endpoint_url             = "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/mock/locations/us-central1/reasoningEngines/0"
    agent_identity_principal = "principal://agents.global.org-0000000000.system.id.goog/resources/aiplatform/projects/000000000000/locations/us-central1/reasoningEngines/0"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "cx_mortgage_orchestrator" {
  config_path = "../../agents/cx-mortgage-orchestrator"
  mock_outputs = {
    engine_id                = "mock-engine-id"
    endpoint_url             = "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/mock/locations/us-central1/reasoningEngines/0"
    agent_identity_principal = "principal://agents.global.org-0000000000.system.id.goog/resources/aiplatform/projects/000000000000/locations/us-central1/reasoningEngines/0"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "corporate_email" {
  config_path = "../../services/corporate-email"
  mock_outputs = {
    service_name = "mock-service"
    service_uri  = "https://mock-service.a.run.app"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "income_verification" {
  config_path = "../../services/income-verification"
  mock_outputs = {
    service_name = "mock-service"
    service_uri  = "https://mock-service.a.run.app"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "legacy_dms" {
  config_path = "../../services/legacy-dms"
  mock_outputs = {
    service_name = "mock-service"
    service_uri  = "https://mock-service.a.run.app"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

inputs = {
  project_id    = dependency.projects.outputs.gateway_project_id
  environment   = local.env_vars.locals.environment
  region        = local.env_vars.locals.region
  vpc_id        = dependency.networking.outputs.network_id
  subnet_id     = dependency.networking.outputs.subnet_id
  apex_domain   = dependency.networking.outputs.entrypoint_domain
  gateway_image = "${(lookup(local.env_vars.locals, "image_repository", "dev") == "release" ? dependency.cicd.outputs.release_repository_url : dependency.cicd.outputs.dev_repository_url)}/${local.image_ref}"

  # Identity upstreams see (layer 3): invoker on the MCP services, aiplatform.user on the agents.
  gateway_sa_email = dependency.security.outputs.envoy_sa_email

  # roles/run.invoker on Envoy: the catalog callers of the hosts routed to it. Agent callers call with
  # their own Agent Identity (principal://); the others with their service account (apigee-partner-mcp:
  # the Apigee proxy runtime service account, only with partner_access = "apigee"). With the deprecated
  # legacy_shared_invoker fallback, agents call as the shared MCP invoker service account instead.
  # Nothing when no host is routed to Envoy.
  invoker_service_accounts = distinct(compact(concat(
    [for caller in local.envoy_callers : (
      contains(local.envoy_agent_callers, caller)
      ? (local.legacy_shared_invoker ? "" : {
        "cx-mortgage-orchestrator"   = dependency.cx_mortgage_orchestrator.outputs.agent_identity_principal
        "ai-coe-mortgage-specialist" = dependency.ai_coe_mortgage_specialist.outputs.agent_identity_principal
      }[caller])
      : caller == "apigee-partner-mcp"
      ? (local.apigee_partner ? dependency.apigee.outputs.proxy_runtime_sa_email : "")
      : dependency.security.outputs["${replace(caller, "-", "_")}_sa_email"]
    )],
    local.legacy_shared_invoker && length(local.envoy_agent_callers) > 0 ? [dependency.security.outputs.mcp_invoker_sa_email] : [],
  )))

  # Every catalog entry (env.yaml catalog), built from its unit's outputs (catalog unit => dependency
  # below). Envoy swaps the caller's ID token for the upstream credential: an ID token for Cloud Run
  # (audience = service URL), an access token for Agent Runtime (audience = the aiplatform API).
  agent_endpoints = {
    for name, entry in local.env_vars.locals.catalog : name => [
      for unit in [{
        "agents/ai-coe-mortgage-specialist" = dependency.ai_coe_mortgage_specialist.outputs
        "agents/cx-mortgage-orchestrator"   = dependency.cx_mortgage_orchestrator.outputs
        "services/corporate-email"          = dependency.corporate_email.outputs
        "services/income-verification"      = dependency.income_verification.outputs
        "services/legacy-dms"               = dependency.legacy_dms.outputs
        }[entry.unit]] : {
        logical_name = name
        engine_id    = entry.kind == "mcp" ? try(unit.service_name, "") : try(unit.engine_id, "")
        # A2A: card and RPC paths live under <engine>/a2a. ADK: path_map below.
        endpoint_url = {
          mcp = try(unit.service_uri, "")
          a2a = "${try(unit.endpoint_url, "")}/a2a"
          adk = try(unit.endpoint_url, "")
        }[entry.kind]
        audience = entry.kind == "mcp" ? try(unit.service_uri, "") : "https://${local.env_vars.locals.region}-aiplatform.googleapis.com"
        # ADK: the path `esmeralda serve` exposes locally; Agent Runtime serves it as :streamQuery over SSE.
        path_map = {
          mcp = {}
          a2a = {}
          adk = { "/api/stream_reasoning_engine" = ":streamQuery?alt=sse" }
        }[entry.kind]
        streaming = entry.kind == "adk"
        probe     = try(entry.probe, "none")
      }
    ][0]
  }
}
