output "gateway_ingress_ip" {
  description = "The regional internal IP address allocated for the Cloud Run Serverless NEG fronting Kong"
  value       = "10.10.0.60" # Internal static IP pointing to the Kong front-end ingress (AUDIT-02 Fix)
}

output "gateway_agent_ingress_host" {
  description = "The base private DNS zone managed by Kong"
  value       = "esmeralda.internal"
}
