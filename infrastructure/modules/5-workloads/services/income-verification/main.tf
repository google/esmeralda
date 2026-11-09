module "mcp_service" {
  source = "../mcp-service"

  service_name             = "income-verification"
  display_name             = "Income Verification"
  register_server_name     = "income-verification-api"
  description              = "FastMCP service providing applicant employment and income verification tools"
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
    "http://income-verification.internal.gateway",
    "https://income-verification.internal.gateway",
    "https://income-verification.internal.gateway/mcp",
    "http://income-verification.esmeralda.internal",
    "http://income-verification.esmeralda.internal/mcp",
    "https://income-verification.esmeralda.internal",
    "https://income-verification.esmeralda.internal/mcp",
  ]
}

moved {
  from = google_cloud_run_v2_service.income_verification
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

