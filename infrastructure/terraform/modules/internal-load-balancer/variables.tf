variable "project_id" {
  type        = string
  description = "Project ID"
}

variable "region" {
  type        = string
  description = "GCP Region"
}

variable "network_self_link" {
  type        = string
  description = "VPC network self link"
}

variable "subnetwork_self_link" {
  type        = string
  description = "Primary subnetwork self link"
}

variable "ip_address" {
  type        = string
  description = "The static IP address to assign to the ILB"
}
