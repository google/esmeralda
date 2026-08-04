variable "project_id" {
  description = "The GCP project ID where the Agent Gateway will be deployed"
  type        = string
}

variable "region" {
  description = "The GCP region for the Agent Gateway deployment"
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "The active deployment environment name"
  type        = string
  default     = "dev"
}

variable "subnet_id" {
  description = "The self-link of the backend subnetwork inside the Shared VPC"
  type        = string
}

variable "net_host_project_id" {
  description = "The GCP project ID of the Shared VPC host project"
  type        = string
}

variable "network_id" {
  description = "The self-link or resource name of the Shared VPC network"
  type        = string
}

variable "governance_project_id" {
  description = "The central governance GCP project ID (hosting Model Armor templates)"
  type        = string
  default     = ""
}

variable "model_armor_template_name" {
  description = "The full resource path or name of the Model Armor floor setting template"
  type        = string
  default     = ""
}
