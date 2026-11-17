# ====================================================================
# Esmeralda console (esmeralda-ui): read-only web console behind IAP on the private entry point
# --------------------------------------------------------------------
# A caller-side front end, not a catalog target, so it lives under frontends/ and in the gateway
# project, next to the entry point (not in the MCP tools project).
#
#   browser --HTTPS--> entry point (<host_label>.<domain>, internal ALB) --IAP--> backend service
#           --serverless NEG--> Cloud Run (internal-and-load-balancer ingress, invoker = IAP only)
#
# - No public endpoint: ingress INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER. Viewers need private network
#   reach to the entry point (VPN / Interconnect / a host in the VPC).
# - IAP on the regional backend service; access = roles/iap.httpsResourceAccessor for existing
#   groups (iap_accessors). No groups are created here (no Cloud Identity API, no directory rights).
# - The app verifies the IAP JWT (signature + audience) on every request: ESMERALDA_UI_IAP_AUDIENCE is
#   computed from the backend service below. No cycle: the NEG names the service by string, so the
#   NEG and backend service are created first and the Cloud Run revision gets the audience directly.
# - Least privilege: a dedicated service account with roles/bigquery.dataViewer on the telemetry
#   datasets only and roles/bigquery.jobUser on their project. No logging viewer, no Secret Manager.
# Like a gateway adapter, this module owns no load balancer, certificate or DNS record: the layer-5
# entrypoint unit routes <host_label>.<domain> to backend_service_id.
# ====================================================================

locals {
  service_name = "esmeralda-ui-${var.environment}"

  # IAP JWT audience of a load-balancer backend: /projects/<number>/<location>/backendServices/<id>,
  # with the backend service's numeric id (generated_id), never its name.
  iap_audience_location = var.iap_audience_location != "" ? var.iap_audience_location : "regions/${var.region}"
  computed_iap_audience = "/projects/${data.google_project.this.number}/${local.iap_audience_location}/backendServices/${google_compute_region_backend_service.console.generated_id}"
  iap_audience          = var.iap_audience_override != "" ? var.iap_audience_override : local.computed_iap_audience

  domain_accessors = [for m in var.iap_accessors : m if startswith(m, "domain:")]

  bq_datasets = toset(compact([var.telemetry_dataset_id, var.usage_dataset_id]))

  app_env = merge(
    {
      ESMERALDA_UI_MODE         = "cloud"
      ESMERALDA_UI_ENV          = var.environment
      ESMERALDA_UI_IAP_AUDIENCE = local.iap_audience
      ESMERALDA_UI_BQ_PROJECT   = var.bq_project
      ESMERALDA_UI_BQ_DATASET   = var.telemetry_dataset_id
    },
    var.bq_location != "" ? { ESMERALDA_UI_BQ_LOCATION = var.bq_location } : {},
  )
}

data "google_project" "this" {
  project_id = var.project_id
}

# IAP is not among the gateway project's layer-1 APIs (nothing else there uses it). Left enabled on
# destroy: disabling an API can break other users of the project.
resource "google_project_service" "iap" {
  project            = var.project_id
  service            = "iap.googleapis.com"
  disable_on_destroy = false
}

# IAP service agent (service-<number>@gcp-sa-iap.iam.gserviceaccount.com): IAP calls the Cloud Run
# service with this identity, so it is the only roles/run.invoker member.
resource "google_project_service_identity" "iap" {
  provider = google-beta
  project  = var.project_id
  service  = "iap.googleapis.com"

  depends_on = [google_project_service.iap]
}

# --- Identity: dedicated, least privilege ---

resource "google_service_account" "console" {
  project      = var.project_id
  account_id   = "sa-esmeralda-ui-${var.environment}"
  display_name = "Esmeralda console (esmeralda-ui) runtime"
  description  = "Read-only Esmeralda console BFF: reads the telemetry views in BigQuery. Catalog caller esmeralda-ui (team platform)."
}

# Read the FinOps / audit views and the usage tables they query: dataset-scoped, nothing project-wide.
resource "google_bigquery_dataset_iam_member" "console_data_viewer" {
  for_each   = local.bq_datasets
  project    = var.bq_project
  dataset_id = each.value
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.console.email}"
}

# Run query jobs (the BFF's client runs them in bq_project, with maximum_bytes_billed on each query).
resource "google_project_iam_member" "console_job_user" {
  project = var.bq_project
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.console.email}"
}

# --- Load balancer backend (created before the service: the NEG names it by string) ---

resource "google_compute_region_network_endpoint_group" "console" {
  name                  = "neg-esmeralda-ui-${var.environment}"
  project               = var.project_id
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = local.service_name
  }
}

# No timeout_sec: it can't be set on serverless NEG backends; the Cloud Run request timeout applies.
resource "google_compute_region_backend_service" "console" {
  name                  = "bs-esmeralda-ui-${var.environment}"
  project               = var.project_id
  region                = var.region
  protocol              = "HTTPS"
  load_balancing_scheme = "INTERNAL_MANAGED"

  backend {
    group = google_compute_region_network_endpoint_group.console.id
  }

  # IAP for the regional internal Application Load Balancer. No OAuth client: IAP uses its
  # Google-managed client, which admits identities of the organization only (external viewers need a
  # custom OAuth client or Identity Platform, see the docs).
  iap {
    enabled = true
  }

  log_config {
    enable      = true
    sample_rate = 1.0
  }

  depends_on = [google_project_service.iap]
}

# Who may open the console: existing groups only (validated in variables.tf). Authoritative for the
# role on this backend service, so access is exactly what the configuration says.
resource "google_iap_web_region_backend_service_iam_binding" "accessors" {
  project                    = var.project_id
  region                     = var.region
  web_region_backend_service = google_compute_region_backend_service.console.name
  role                       = "roles/iap.httpsResourceAccessor"
  members                    = var.iap_accessors

  lifecycle {
    # Fail instead of silently revoking everyone: an apply without the accessor group (e.g. an
    # operator whose env file lacks it) must not empty the binding.
    precondition {
      condition     = length(var.iap_accessors) > 0
      error_message = "iap_accessors is empty: set env.yaml frontends.esmeralda_ui.iap_accessors (or ESMERALDA_UI_IAP_ACCESSORS in the user-local env file) to the existing viewer group, group:<email>."
    }
    precondition {
      condition     = var.allow_domain_accessors || length(local.domain_accessors) == 0
      error_message = "iap_accessors has domain: entries (${join(", ", local.domain_accessors)}): set allow_domain_accessors = true to grant a whole domain, or use a group."
    }
  }
}

# --- The console on Cloud Run ---

resource "google_cloud_run_v2_service" "console" {
  name                 = local.service_name
  location             = var.region
  project              = var.project_id
  ingress              = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  invoker_iam_disabled = var.invoker_iam_disabled
  deletion_protection  = false

  template {
    service_account = google_service_account.console.email

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    containers {
      # Non-root (UID 65532) is set in the image; the digest is what runs.
      image = local.image_by_digest
      ports {
        container_port = 8080
      }
      resources {
        limits = {
          cpu    = var.cpu_limit
          memory = var.memory_limit
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = local.app_env
        content {
          name  = env.key
          value = env.value
        }
      }

      startup_probe {
        http_get {
          path = "/healthz"
        }
      }
    }
  }

  depends_on = [
    google_bigquery_dataset_iam_member.console_data_viewer,
    google_project_iam_member.console_job_user,
  ]
}

# Only IAP may invoke the service (authoritative for the role). Skipped when the invoker check is off.
resource "google_cloud_run_v2_service_iam_binding" "iap_invoker" {
  count    = var.invoker_iam_disabled ? 0 : 1
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.console.name
  role     = "roles/run.invoker"
  members  = ["serviceAccount:${google_project_service_identity.iap.email}"]
}
