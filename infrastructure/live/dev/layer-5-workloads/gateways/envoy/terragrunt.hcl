# infrastructure/live/dev/layer-5-workloads/gateways/envoy/terragrunt.hcl
# Layer 5 gateway: Envoy on Cloud Run + serverless NEG + regional backend service (backend_service_id).
# It owns no load balancer, certificate or DNS record: the layer-5 entrypoint routes hosts to it.
# Applied after the MCP services and agents, so routes get the real engine IDs on the first apply.
# Mocks below only serve `validate`/`plan` of a not-yet-deployed env; `apply` requires real outputs.
include "root" {
  path = find_in_parent_folders("root.hcl")
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))

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

dependency "ai_coe_mortgage_specialist" {
  config_path = "../../agents/ai-coe-mortgage-specialist"
  mock_outputs = {
    engine_id    = "mock-engine-id"
    endpoint_url = "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/mock/locations/us-central1/reasoningEngines/0"
  }
  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
}

dependency "cx_mortgage_orchestrator" {
  config_path = "../../agents/cx-mortgage-orchestrator"
  mock_outputs = {
    engine_id    = "mock-engine-id"
    endpoint_url = "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/mock/locations/us-central1/reasoningEngines/0"
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

  invoker_service_accounts = [
    dependency.security.outputs.test_vm_sa_email,
    dependency.security.outputs.ai_coe_mortgage_specialist_sa_email,
    dependency.security.outputs.cx_mortgage_orchestrator_sa_email,
    dependency.security.outputs.mcp_invoker_sa_email
  ]

  agent_endpoints = {
    # Agents
    ai-coe-mortgage-specialist = {
      logical_name = "ai-coe-mortgage-specialist"
      engine_id    = dependency.ai_coe_mortgage_specialist.outputs.engine_id
      endpoint_url = "${dependency.ai_coe_mortgage_specialist.outputs.endpoint_url}/a2a"
      audience     = "https://${local.env_vars.locals.region}-aiplatform.googleapis.com"
      probe        = "a2a"
    }
    cx-mortgage-orchestrator = {
      logical_name = "cx-mortgage-orchestrator"
      engine_id    = dependency.cx_mortgage_orchestrator.outputs.engine_id
      endpoint_url = dependency.cx_mortgage_orchestrator.outputs.endpoint_url
      audience     = "https://${local.env_vars.locals.region}-aiplatform.googleapis.com"
      # Same path `esmeralda serve` exposes locally; Agent Runtime serves it as :streamQuery over SSE.
      path_map  = { "/api/stream_reasoning_engine" = ":streamQuery?alt=sse" }
      streaming = true
      probe     = "query" # a real query through /api/stream_reasoning_engine
    }
    # MCP Servers
    corporate-email = {
      logical_name = "corporate-email"
      engine_id    = dependency.corporate_email.outputs.service_name
      endpoint_url = dependency.corporate_email.outputs.service_uri
      audience     = dependency.corporate_email.outputs.service_uri
      probe        = "mcp"
    }
    income-verification = {
      logical_name = "income-verification"
      engine_id    = dependency.income_verification.outputs.service_name
      endpoint_url = dependency.income_verification.outputs.service_uri
      audience     = dependency.income_verification.outputs.service_uri
      probe        = "mcp"
    }
    legacy-dms = {
      logical_name = "legacy-dms"
      engine_id    = dependency.legacy_dms.outputs.service_name
      endpoint_url = dependency.legacy_dms.outputs.service_uri
      audience     = dependency.legacy_dms.outputs.service_uri
      probe        = "mcp"
    }
  }
}
