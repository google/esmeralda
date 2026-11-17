# --- INPUT VARIABLES CONTRACT ---

variable "project_id" {
  description = "Project that hosts the console: the gateway project (entry point, front doors). A serverless NEG can only point at a Cloud Run service in its own project, so the service, NEG and backend service all live here"
  type        = string
}

variable "region" {
  description = "Region of the entry point (regional internal Application Load Balancer) and of the console"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, prd). Also the catalog the console shows (catalog/.resolved/<env>.json, baked into the image)"
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,30}$", var.environment))
    error_message = "environment must be a short lowercase name (e.g. dev, prd)."
  }
}

variable "container_image" {
  description = "Console image: <repo>/esmeralda-ui:<tag> (resolved to its digest at plan time) or <repo>/esmeralda-ui@sha256:... (prd: release/prd.yaml)"
  type        = string
}

variable "domain" {
  description = "Private domain of the entry point (layer 2 output entrypoint_domain); the console is served as <host_label>.<domain>"
  type        = string
}

variable "host_label" {
  description = "Host label of the console on the entry point: <host_label>.<domain>. Must not be a catalog name"
  type        = string
  default     = "console"

  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", var.host_label))
    error_message = "host_label is a DNS label: lowercase letters, digits and hyphens."
  }
}

variable "bq_project" {
  description = "Project of the telemetry datasets the console reads (the governance project). Its BigQuery jobs run there too (roles/bigquery.jobUser)"
  type        = string
}

variable "telemetry_dataset_id" {
  description = "Telemetry dataset with the FinOps and audit views (layer 4 output governance_dataset_id). The console gets roles/bigquery.dataViewer on this dataset only"
  type        = string
}

variable "usage_dataset_id" {
  description = "Dataset with the esmeralda.usage landing tables (layer 4 output usage_dataset_id). The views the console reads (usage_events and the views built on it) query these tables and are not authorized views, so the console also needs roles/bigquery.dataViewer here. Empty: no grant"
  type        = string
  default     = ""
}

variable "bq_location" {
  description = "BigQuery location of the datasets (ESMERALDA_UI_BQ_LOCATION). Empty: BigQuery infers it"
  type        = string
  default     = ""
}

variable "iap_accessors" {
  description = "Who may open the console (roles/iap.httpsResourceAccessor on its backend service): existing groups, group:<email>. Group membership is managed outside Terraform by the group owner. domain:<domain> only with allow_domain_accessors. Individual users, service accounts, allUsers and allAuthenticatedUsers are rejected"
  type        = list(string)
  default     = []

  validation {
    condition     = length([for m in var.iap_accessors : m if contains(["allusers", "allauthenticatedusers"], lower(trimspace(m)))]) == 0
    error_message = "iap_accessors must not contain allUsers or allAuthenticatedUsers: the console shows chargeback and audit data."
  }

  validation {
    condition = alltrue([
      for m in var.iap_accessors :
      can(regex("^group:[A-Za-z0-9._%+-]+@[a-z0-9.-]+\\.[a-z]{2,}$", m)) || can(regex("^domain:[a-z0-9.-]+\\.[a-z]{2,}$", m))
    ])
    error_message = "iap_accessors takes existing groups only (group:<email>), or domain:<domain> together with allow_domain_accessors = true. user:, serviceAccount: and principal sets are rejected: add people to the group instead, so access is reviewable and doesn't depend on who applies."
  }

  validation {
    condition     = length(var.iap_accessors) == length(distinct(var.iap_accessors))
    error_message = "iap_accessors has duplicates."
  }
}

variable "allow_domain_accessors" {
  description = "Allow domain:<domain> entries in iap_accessors (every identity of that domain may open the console). Off by default"
  type        = bool
  default     = false
}

variable "invoker_iam_disabled" {
  description = "Turn off the Cloud Run invoker IAM check. Default false: only the IAP service agent holds roles/run.invoker, so the service accepts nothing that didn't pass IAP. Set true only if IAP can't invoke the service in your setup; the app still rejects every request without a valid IAP JWT"
  type        = bool
  default     = false
}

variable "iap_audience_override" {
  description = "Expected aud of the IAP JWT (ESMERALDA_UI_IAP_AUDIENCE). Empty: computed from the backend service (/projects/<number>/<location>/backendServices/<id>, see iap_audience_location). Set it only if the console shows a different Signed Header JWT Audience for the backend service"
  type        = string
  default     = ""
}

variable "iap_audience_location" {
  description = "Location segment of the computed IAP audience: \"regions/<region>\" (regional backend service, default), \"<region>\" or \"global\". Check it against the IAP page (Signed Header JWT Audience) in the first deployment of an environment"
  type        = string
  default     = ""
}

variable "min_instances" {
  description = "Minimum Cloud Run instances (0: scale to zero)"
  type        = number
  default     = 0
}

variable "max_instances" {
  description = "Maximum Cloud Run instances"
  type        = number
  default     = 2
}

variable "cpu_limit" {
  description = "CPU limit of the console container"
  type        = string
  default     = "1"
}

variable "memory_limit" {
  description = "Memory limit of the console container"
  type        = string
  default     = "512Mi"
}
