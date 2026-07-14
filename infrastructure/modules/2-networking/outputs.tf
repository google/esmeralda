# infrastructure/modules/2-networking/outputs.tf

output "network_id" {
  description = "The resolved Shared VPC network resource ID"
  value       = local.target_vpc_id
}

output "subnet_id" {
  description = "The resolved backend workload subnet resource ID"
  value       = local.target_subnet_id
}

output "subnet_name" {
  description = "The resolved name of the backend workload subnet"
  value       = local.subnet_parsed_name
}

output "dns_zone_name" {
  description = "The name of the private DNS managed zone"
  value       = google_dns_managed_zone.private_dns.name
}

output "dns_zone_dns_name" {
  description = "The suffix domain name of the private DNS managed zone"
  value       = google_dns_managed_zone.private_dns.dns_name
}

output "psc_network_attachment_id" {
  description = "The URI of the Private Service Connect Network Attachment"
  value       = try(google_compute_network_attachment.psc_interface[0].id, "")
}

output "psc_subnet_id" {
  description = "The resolved PSC interface subnet resource ID"
  value       = try(google_compute_subnetwork.psc_interface[0].id, "")
}

output "secure_web_proxy_ip" {
  description = "The private IP address of the Secure Web Proxy"
  value       = var.enable_secure_web_proxy && !var.byo_networking ? "10.0.1.100" : ""
}
