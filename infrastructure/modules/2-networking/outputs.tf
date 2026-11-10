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

output "psc_subnet_id" {
  description = "The resolved PSC interface subnet resource ID"
  value       = try(google_compute_subnetwork.psc_interface[0].id, "")
}

output "secure_web_proxy_ip" {
  description = "The private IP address of the Secure Web Proxy"
  value       = var.enable_secure_web_proxy && !var.byo_networking ? "10.0.1.100" : ""
}

output "entrypoint_address_id" {
  description = "Resource ID of the reserved internal IP of the entry point"
  value       = google_compute_address.entrypoint.id
}

output "entrypoint_address" {
  description = "The reserved internal IP that <domain> and *.<domain> resolve to (the layer-5 entrypoint forwarding rule listens on it)"
  value       = google_compute_address.entrypoint.address
}

output "entrypoint_domain" {
  description = "The private domain served by the entry point, without trailing dot (e.g. esmeralda.internal)"
  value       = local.entrypoint_domain
}

output "googleapis_psc_ip" {
  description = "Internal IP of the PSC endpoint for Google APIs (empty when not created); target of the entry point's hybrid Agent Runtime backend"
  value       = try(google_compute_global_address.googleapis_psc[0].address, "")
}
