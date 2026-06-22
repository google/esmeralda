resource "google_apigee_organization" "apigee_org" {
  analytics_region   = var.region
  project_id         = var.project_id
  authorized_network = var.vpc_id
}

resource "google_apigee_environment" "apigee_env" {
  name         = var.environment
  org_id       = google_apigee_organization.apigee_org.id
  description  = "Esmeralda ${var.environment} Apigee Environment"
  display_name = var.environment
}

resource "google_apigee_envgroup" "apigee_envgroup" {
  name      = "esmeralda-group-${var.environment}"
  org_id    = google_apigee_organization.apigee_org.id
  hostnames = ["*.esmeralda.internal"]
}

resource "google_apigee_envgroup_attachment" "env_to_group" {
  envgroup_id = google_apigee_envgroup.apigee_envgroup.id
  environment = google_apigee_environment.apigee_env.name
}

resource "google_apigee_instance" "apigee_instance" {
  name                 = "apigee-instance-${var.environment}"
  org_id               = google_apigee_organization.apigee_org.id
  location             = var.region
  peering_cidr_range   = "10.12.0.0/22"
}

resource "google_apigee_instance_attachment" "env_to_instance" {
  instance_id = google_apigee_instance.apigee_instance.id
  environment = google_apigee_environment.apigee_env.name
}

# Key Value Map to store logical-to-dynamic engine endpoint mappings
resource "google_apigee_keyvaluemap" "agent_routes" {
  org_id = google_apigee_organization.apigee_org.id
  env_id = google_apigee_environment.apigee_env.id
  name   = "agent-routes"
}

# Dynamically populate KVM entries using local-exec (since the Google API doesn't expose standard keyvaluemap entries as separate Terraform resources)
resource "null_resource" "populate_apigee_kvm" {
  for_each = var.agent_endpoints

  triggers = {
    engine_id    = each.value.engine_id
    endpoint_url = each.value.endpoint_url
  }

  provisioner "local-exec" {
    command = <<EOT
      curl -X POST -H "Authorization: Bearer $(gcloud auth print-access-token)" \
        -H "Content-Type: application/json" \
        "https://apigee.googleapis.com/v1/organizations/${google_apigee_organization.apigee_org.name}/environments/${google_apigee_environment.apigee_env.name}/keyvaluemaps/agent-routes/entries" \
        -d '{"name": "${each.value.logical_name}", "value": "${each.value.endpoint_url}"}' \
        || curl -X PUT -H "Authorization: Bearer $(gcloud auth print-access-token)" \
        -H "Content-Type: application/json" \
        "https://apigee.googleapis.com/v1/organizations/${google_apigee_organization.apigee_org.name}/environments/${google_apigee_environment.apigee_env.name}/keyvaluemaps/agent-routes/entries/${each.value.logical_name}" \
        -d '{"name": "${each.value.logical_name}", "value": "${each.value.endpoint_url}"}'
    EOT
  }
}
