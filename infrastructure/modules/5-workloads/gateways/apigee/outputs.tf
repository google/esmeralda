# --- GATEWAY CONTRACT ---
# Same output shape as every module under modules/5-workloads/gateways/ (see kong/outputs.tf).
#
# PLACEHOLDER: this stub does not build the piece the entrypoint needs yet. To make Apigee
# routable from the entrypoint, add a regional PSC NEG targeting the Apigee instance service
# attachment (google_apigee_instance.apigee_instance.service_attachment) and a regional
# INTERNAL_MANAGED backend service on top of it, then return that backend service ID here.
# Until then the output is null and the entrypoint rejects "apigee" as a backend.

output "backend_service_id" {
  description = "ID of the regional INTERNAL_MANAGED backend service fronting Apigee (null: not implemented in this stub)"
  value       = null
}

output "routes" {
  description = "Hosts this gateway serves and how `make test-gateway` probes them (empty: not implemented in this stub)"
  value       = {}
}
