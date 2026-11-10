# infrastructure/modules/2-networking/variables.tf

variable "net_host_project_id" {
  description = "The project ID of the Shared VPC Host"
  type        = string
}

variable "gateway_project_id" {
  description = "The project ID of the API Ingress Gateway"
  type        = string
}

variable "mcps_project_id" {
  description = "The project ID allocated for corporate MCP servers"
  type        = string
}

variable "ai_coe_agents_project_id" {
  description = "The project ID allocated for Core AI Platform and A2A agents"
  type        = string
}

variable "cx_agents_project_id" {
  description = "The project ID allocated for CX team agents (cx-agents project)"
  type        = string
}

variable "region" {
  description = "The primary region where subnets and resources are placed"
  type        = string
  default     = "us-central1"
}

# BYO Networking Toggles
variable "byo_networking" {
  description = "If true, skip creating the Shared VPC network and subnets, and attach to existing ones instead."
  type        = bool
  default     = false
}

variable "byo_governance_project" {
  description = "Set to true if the customer is bringing a pre-existing governance/security/telemetry project"
  type        = bool
  default     = false
}

variable "governance_project_id" {
  description = "The project ID allocated for central governance, security, and telemetry"
  type        = string
}

variable "byo_net_host_project" {
  description = "Set to true if the customer is bringing a pre-existing Shared VPC Host Project"
  type        = bool
  default     = false
}

variable "byo_gateway_project" {
  description = "Set to true if the customer is bringing a pre-existing API Gateway/Ingress Project"
  type        = bool
  default     = false
}

variable "existing_vpc_id" {
  description = "The full resource URI of the existing Shared VPC network. Required if byo_networking is true."
  type        = string
  default     = ""
}

variable "existing_subnet_id" {
  description = "The full resource URI of the existing backend workload subnet. Required if byo_networking is true."
  type        = string
  default     = ""
}

# Workload Project Numbers are resolved dynamically in main.tf via data "google_project" to avoid manual inputs and automation locks.

# Explicit Proxy & PSC Options
variable "enable_psc_interface" {
  description = "Set to true to create a PSC Network Attachment for Serverless Agent ingress"
  type        = bool
  default     = true
}

variable "enable_secure_web_proxy" {
  description = "Set to true to deploy the Secure Web Proxy for outbound egress filtering"
  type        = bool
  default     = true
}

variable "enable_googleapis_psc_endpoint" {
  description = "Create a Private Service Connect endpoint for Google APIs (all-apis) at googleapis_psc_ip. Needed by the entry point's hybrid Agent Runtime backend (env.yaml gateways.hybrid_agent_backend). With byo_networking, provide an equivalent endpoint instead"
  type        = bool
  default     = false
}

variable "googleapis_psc_ip" {
  description = "Internal IP of the PSC endpoint for Google APIs; must be outside every subnet and peering range of the Shared VPC"
  type        = string
  default     = "10.12.0.2"
}

variable "entrypoint_ip_address" {
  description = "Optional fixed internal IP for the entry point VIP (must be free in the core subnet); null lets GCP pick one"
  type        = string
  default     = null
}

variable "environment" {
  description = "The environment classification (e.g., dev, qa, prod)"
  type        = string
  default     = "dev"
}

variable "project_suffix" {
  description = "The random project suffix generated in Layer 1"
  type        = string
}

variable "mcps_run_service_agent" {
  description = "The Cloud Run Service Agent email in MCPS project"
  type        = string
}

variable "gateway_run_service_agent" {
  description = "The Cloud Run Service Agent email in Gateway project"
  type        = string
}

variable "ai_coe_agents_run_service_agent" {
  description = "The Cloud Run Service Agent email in the AI CoE agents project"
  type        = string
}

variable "ai_coe_agents_vertex_service_agent" {
  description = "The Vertex AI Service Agent email in the AI CoE agents project"
  type        = string
}

variable "cx_agents_vertex_service_agent" {
  description = "The Vertex AI Service Agent email in CX agents project"
  type        = string
}

