output "envoy_yaml" {
  description = "Envoy bootstrap (YAML) for the gateway"
  value       = yamlencode(local.envoy_config)
}

output "hostnames" {
  description = "Every hostname the gateway serves: the apex domain and <logical_name>.<apex_domain> per target"
  value       = local.hostnames
}
