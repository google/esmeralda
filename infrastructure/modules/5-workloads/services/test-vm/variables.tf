variable "project_id" {
  type        = string
  description = "The project ID to deploy the test VM in (Service project)"
}

variable "region" {
  type        = string
  description = "GCP Region"
  default     = "us-central1"
}

variable "zone" {
  type        = string
  description = "GCP Zone"
  default     = "us-central1-f"
}

variable "subnet_id" {
  type        = string
  description = "The subnetwork self link to attach the network interface to (Shared VPC core subnet)"
}

variable "service_account_email" {
  type        = string
  description = "The service account email to associate with the VM"
}

variable "environment" {
  type        = string
  description = "Deployment environment"
}
