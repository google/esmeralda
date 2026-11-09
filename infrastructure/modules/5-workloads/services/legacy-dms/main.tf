module "mcp_service" {
  source = "../mcp-service"

  service_name             = "legacy-dms"
  display_name             = "Legacy DMS"
  description              = "FastMCP service providing legacy document management and retrieval tools"
  project_id               = var.project_id
  governance_project_id    = var.governance_project_id
  region                   = var.region
  environment              = var.environment
  network_id               = var.network_id
  subnet_id                = var.subnet_id
  container_image          = var.container_image
  invoker_service_accounts = var.invoker_service_accounts
  min_instances            = var.min_instances
  max_instances            = var.max_instances
  cpu_limit                = var.cpu_limit
  memory_limit             = var.memory_limit
  tools_spec_path          = var.tools_spec_path
  internal_hostname        = var.internal_hostname
  service_account_email    = var.service_account_email
  custom_audiences = [
    "http://dms.internal.gateway",
    "https://dms.internal.gateway",
    "https://dms.internal.gateway/mcp",
    "http://legacy-dms.esmeralda.internal",
    "http://legacy-dms.esmeralda.internal/mcp",
    "https://legacy-dms.esmeralda.internal",
    "https://legacy-dms.esmeralda.internal/mcp",
  ]
}

moved {
  from = google_cloud_run_v2_service.legacy_dms
  to   = module.mcp_service.google_cloud_run_v2_service.service
}

moved {
  from = google_cloud_run_v2_service_iam_binding.invokers
  to   = module.mcp_service.google_cloud_run_v2_service_iam_binding.invokers
}

moved {
  from = null_resource.mcp_registration
  to   = module.mcp_service.null_resource.mcp_registration
}

moved {
  from = google_agent_registry_service.governance_mcp
  to   = module.mcp_service.google_agent_registry_service.governance_mcp
}

