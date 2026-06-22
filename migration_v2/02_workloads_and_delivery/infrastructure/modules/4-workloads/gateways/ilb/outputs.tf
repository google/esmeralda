output "gateway_ingress_ip" {
  description = "The regional internal VIP allocated for the regional L7 load balancer fronting the broker"
  value       = google_compute_forwarding_rule.ilb_forwarding_rule.ip_address
}

output "gateway_agent_ingress_host" {
  description = "The base private DNS zone managed by the dynamic routing broker"
  value       = "esmeralda.internal"
}
