output "gateway_ingress_ip" {
  description = "The regional internal IP address allocated for the Cloud Run Serverless NEG fronting Kong"
  value       = "10.10.0.60" # Internal static IP pointing to the Kong front-end ingress (AUDIT-02 Fix)
}

output "gateway_agent_ingress_host" {
  description = "The base private DNS zone managed by Kong"
  value       = "esmeralda.internal"
}

output "internal_root_ca_pem" {
  description = "The PEM-encoded Root CA certificate signing *.esmeralda.internal"
  value       = tls_self_signed_cert.esmeralda_ca_cert.cert_pem
}

output "gateway_run_url" {
  description = "The Cloud Run .run.app URI of the Kong API Gateway"
  value       = google_cloud_run_v2_service.kong_gateway.uri
}

