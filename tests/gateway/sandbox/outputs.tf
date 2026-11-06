output "project_id" {
  description = "The sandbox project"
  value       = local.project
}

output "test_vm_name" {
  description = "Test VM to run checks.sh on"
  value       = google_compute_instance.test_vm.name
}

output "test_vm_zone" {
  description = "Zone of the test VM"
  value       = local.zone
}

output "upstream_url" {
  description = "Cloud Run URL of the upstream (the audience of the gateway's ID tokens)"
  value       = google_cloud_run_v2_service.upstream.uri
}

output "gateway_ip" {
  description = "VIP of the gateway's internal HTTPS load balancer"
  value       = module.gateway.gateway_ingress_ip
}

output "rate_limit_per_minute" {
  description = "Per-key rate limit configured in the sandbox"
  value       = var.rate_limit_per_minute
}
