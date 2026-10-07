output "gateway_ingress_ip" {
  description = "The internal VIP of the Private Service Connect endpoint routing to Apigee"
  value       = "10.10.0.50" # Reserved IP for Apigee PSC endpoint in Shared VPC (AUDIT-02 Fix)
}

output "gateway_agent_ingress_host" {
  description = "The base private DNS zone managed by Apigee"
  value       = "esmeralda.internal"
}
