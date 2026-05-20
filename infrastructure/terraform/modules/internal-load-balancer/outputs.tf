output "ilb_ip_address" {
  description = "The IP address of the Internal Load Balancer"
  value       = google_compute_forwarding_rule.default.ip_address
}
