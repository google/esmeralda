output "envoy_yaml" {
  description = "Envoy bootstrap (YAML) for the gateway"
  value       = yamlencode(local.envoy_config)

  precondition {
    # Each target adds about 3.5 KB, so roughly 17 targets fit.
    condition     = length(yamlencode(local.envoy_config)) <= 65536
    error_message = "The rendered Envoy bootstrap is larger than 64 KiB, the most one Secret Manager secret version can hold."
  }
}

output "hostnames" {
  description = "Every hostname the gateway serves: the apex domain and <logical_name>.<apex_domain> per target"
  value       = local.hostnames
}
