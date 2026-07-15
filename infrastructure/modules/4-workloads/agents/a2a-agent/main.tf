terraform {
  required_version = ">= 1.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.0"
    }
  }
}

# -----------------------------------------------------------------------------
# 1. ATOMIC DATA PLANE: PRIVATE SERVICES ACCESS & CLOUD SQL POSTGRESQL
# -----------------------------------------------------------------------------

# Provisions a secure, private PostgreSQL instance with IAM authentication enabled
resource "google_sql_database_instance" "task_store" {
  name             = "${var.agent_name}-db-${var.environment}"
  project          = var.project_id
  region           = var.region
  database_version = "POSTGRES_15"

  settings {
    tier              = var.sql_tier
    availability_type = "ZONAL"
    disk_size         = 15

    ip_configuration {
      ipv4_enabled    = false # Absolute private network isolation
      private_network = var.vpc_id
    }

    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }
  }

  deletion_protection = false # Configured for elastic dev/sandbox environments
}

# Provisions the task store database
resource "google_sql_database" "tasks_db" {
  name     = var.database_name
  instance = google_sql_database_instance.task_store.name
  project  = var.project_id
}

# Standard random password generator for local superuser postgres login
resource "random_password" "postgres_pwd" {
  length  = 24
  special = false
}

# Superuser root account
resource "google_sql_user" "postgres_user" {
  name     = "postgres"
  instance = google_sql_database_instance.task_store.name
  project  = var.project_id
  password = random_password.postgres_pwd.result
}

# Create IAM User mapped to the agent's service account to leverage Cloud IAM Db Authentication
resource "google_sql_user" "agent_iam_user" {
  name     = trimsuffix(var.agent_service_account, ".gserviceaccount.com")
  instance = google_sql_database_instance.task_store.name
  project  = var.project_id
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"
  depends_on = [google_sql_user.postgres_user]
}

# Null Resource to wait for database engine to be fully runnable before executing grants
resource "null_resource" "db_ready" {
  depends_on = [
    google_sql_database_instance.task_store,
    google_sql_database.tasks_db,
    google_sql_user.postgres_user,
    google_sql_user.agent_iam_user
  ]

  provisioner "local-exec" {
    command = <<EOT
      echo "⏳ Waiting for Cloud SQL Instance ${google_sql_database_instance.task_store.name} to start..."
      for i in {1..30}; do
        STATE=$(gcloud sql instances describe ${google_sql_database_instance.task_store.name} --project=${var.project_id} --format="value(state)" 2>/dev/null || echo "UNKNOWN")
        if [ "$STATE" = "RUNNABLE" ]; then
          echo "✅ Cloud SQL is ONLINE. Allowing 10 seconds for service stabilization..."
          sleep 10
          exit 0
        fi
        echo "🔄 DB state is $STATE. Retrying (Attempt $i/30)..."
        sleep 10
      done
      exit 1
    EOT
  }
}

# Assign roles/cloudsql.client to the agent service account at project level
resource "google_project_iam_member" "cloudsql_client_role" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${var.agent_service_account}"
}

# Assign roles/cloudsql.instanceUser to enable IAM database token injection
resource "google_project_iam_member" "cloudsql_user_role" {
  project = var.project_id
  role    = "roles/cloudsql.instanceUser"
  member  = "serviceAccount:${var.agent_service_account}"
}

# -----------------------------------------------------------------------------
# 2. PRIVILEGES BOOTSTRAP: VPC-BOUND CLOUD RUN JOB (REPLACES POSTGRESQL PROVIDER)
# -----------------------------------------------------------------------------

# Deploys a lightweight, standard PostgreSQL administrative job inside the Shared VPC.
# This connects privately via direct VPC IP and executes administrative SQL GRANT queries,
# completely eliminating the need for a local-exec postgresql client or provider.
resource "google_cloud_run_v2_job" "schema_bootstrap" {
  name     = "${var.agent_name}-db-bootstrap-${var.environment}"
  location = var.region
  project  = var.project_id
  depends_on = [
    null_resource.db_ready,
    google_project_iam_member.cloudsql_client_role,
    google_project_iam_member.cloudsql_user_role
  ]

  template {
    template {
      # Runs under a standard service account that has access to execute VPC jobs
      service_account = var.agent_service_account
      
      containers {
        # Light, standard alpine-postgres client image
        image = "alpine:latest"
        command = ["/bin/sh", "-c"]
        args = [
          "apk add --no-cache postgresql-client && psql \"postgresql://postgres:${random_password.postgres_pwd.result}@${google_sql_database_instance.task_store.private_ip_address}/${var.database_name}\" -c \"GRANT ALL PRIVILEGES ON DATABASE ${var.database_name} TO \\\"${google_sql_user.agent_iam_user.name}\\\"; GRANT ALL PRIVILEGES ON SCHEMA public TO \\\"${google_sql_user.agent_iam_user.name}\\\";\""
        ]
      }

      # VPC Access config mapping schema runner container to the private Shared VPC
      vpc_access {
        network_interfaces {
          network    = var.vpc_id
          subnetwork = var.subnet_id
        }
        egress = "ALL_TRAFFIC"
      }
    }
  }
}

# Null Resource triggering the bootstrap job execution during terraform apply
resource "null_resource" "trigger_bootstrap" {
  depends_on = [google_cloud_run_v2_job.schema_bootstrap]

  provisioner "local-exec" {
    command = <<EOT
      echo "🚀 Launching private Cloud Run PostgreSQL bootstrap job..."
      gcloud run jobs execute ${google_cloud_run_v2_job.schema_bootstrap.name} \
        --region="${var.region}" \
        --project="${var.project_id}" \
        --wait
    EOT
  }
}

# -----------------------------------------------------------------------------
# 3. ATOMIC STORAGE & WORKLOAD STAGING (ONE SET OF BUCKETS PER AGENT)
# -----------------------------------------------------------------------------

# Cryptographically unique suffix for bucket naming
resource "random_id" "bucket_suffix" {
  byte_length = 4
}

# 1. Atomic Deployment Dependencies Staging Bucket (Code/Pickle/Deps)
resource "google_storage_bucket" "staging" {
  project                     = var.project_id
  name                        = "${var.project_id}-${var.agent_name}-staging-${var.environment}-${random_id.bucket_suffix.hex}"
  location                    = var.region
  force_destroy               = true # Set to false in sandbox/dev environments
  uniform_bucket_level_access = true
}

# 2. Atomic Runtime Task Artifacts Bucket (Agent operational assets)
resource "google_storage_bucket" "artifacts" {
  project                     = var.project_id
  name                        = "${var.project_id}-${var.agent_name}-artifacts-${var.environment}-${random_id.bucket_suffix.hex}"
  location                    = var.region
  force_destroy               = true
  uniform_bucket_level_access = true
}

# 3. Atomic Logs Offload Bucket (Long-term tracing and logging)
resource "google_storage_bucket" "logs" {
  project                     = var.project_id
  name                        = "${var.project_id}-${var.agent_name}-logs-${var.environment}-${random_id.bucket_suffix.hex}"
  location                    = var.region
  force_destroy               = true
  uniform_bucket_level_access = true
}

# 4. Atomic BigQuery Analytics Dataset & IAM Permissions
resource "google_bigquery_dataset" "analytics" {
  dataset_id  = "${replace(var.agent_name, "-", "_")}_logs_${var.environment}"
  project     = var.project_id
  location    = var.region
  description = "Analytics and telemetry dataset for ${var.agent_name}"
}

data "google_project" "current" {
  project_id = var.project_id
}

resource "google_bigquery_dataset_iam_member" "agent_bq_writer" {
  dataset_id = google_bigquery_dataset.analytics.dataset_id
  project    = var.project_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${var.agent_service_account}"
}

resource "google_bigquery_dataset_iam_member" "vertex_re_bq_writer" {
  dataset_id = google_bigquery_dataset.analytics.dataset_id
  project    = var.project_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
}

resource "google_bigquery_dataset_iam_member" "vertex_ai_bq_writer" {
  dataset_id = google_bigquery_dataset.analytics.dataset_id
  project    = var.project_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-aiplatform.iam.gserviceaccount.com"
}

resource "google_project_iam_member" "agent_bq_admin" {
  project = var.project_id
  role    = "roles/bigquery.admin"
  member  = "serviceAccount:${var.agent_service_account}"
}

resource "google_project_iam_member" "invokers_aiplatform_user" {
  for_each = toset(var.invoker_service_accounts)
  project  = var.project_id
  role     = "roles/aiplatform.user"
  member   = "serviceAccount:${each.value}"
}

resource "google_project_iam_member" "vertex_re_dns_peer" {
  count   = var.net_host_project_id != "" ? 1 : 0
  project = var.net_host_project_id
  role    = "roles/dns.peer"
  member  = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
}

resource "google_project_iam_member" "vertex_ai_dns_peer" {
  count   = var.net_host_project_id != "" ? 1 : 0
  project = var.net_host_project_id
  role    = "roles/dns.peer"
  member  = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-aiplatform.iam.gserviceaccount.com"
}

resource "google_project_iam_member" "vertex_re_network_admin" {
  project = var.project_id
  role    = "roles/compute.networkAdmin"
  member  = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
}

resource "google_project_iam_member" "vertex_ai_network_admin" {
  project = var.project_id
  role    = "roles/compute.networkAdmin"
  member  = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-aiplatform.iam.gserviceaccount.com"
}

resource "google_compute_network_attachment" "psc_attachment" {
  count                 = var.enable_psc_network ? 1 : 0
  name                  = "${local.yaml_name}-psc-attachment-${var.environment}"
  project               = var.project_id
  region                = var.region
  connection_preference = "ACCEPT_AUTOMATIC"

  subnetworks = [
    var.psc_subnet_id != "" ? var.psc_subnet_id : var.subnet_id
  ]
}

# Declaratively define the Vertex AI Reasoning Engine agent
locals {
  # Read and decode agent.yaml if path is provided
  agent_config = try(yamldecode(file(var.agent_config_path)), {})


  # 1. Metadata
  yaml_name = try(local.agent_config.name, var.agent_name)
  yaml_desc = try(local.agent_config.description, "A2A Mortgage Assistant downstream reasoning engine deployed modularly")

  # 2. Compute Resources & Scaling
  yaml_min_inst    = try(local.agent_config.resources.min_instances, null)
  yaml_max_inst    = try(local.agent_config.resources.max_instances, null)
  yaml_concurrency = try(local.agent_config.resources.concurrency, null)
  yaml_cpu         = try(tostring(local.agent_config.resources.cpu), null)
  yaml_memory      = try(local.agent_config.resources.memory, null)

  # 3. Framework
  yaml_framework   = try(local.agent_config.framework, "a2a")

  # 4. Environment Variables & Runtime Infrastructure Overrides
  yaml_env_vars = try(local.agent_config.env, {})

  runtime_overrides = {
    GCS_BUCKET         = try(google_storage_bucket.logs.name, null)
    CLOUD_SQL_INSTANCE = try("${var.project_id}:${var.region}:${google_sql_database_instance.task_store.name}", null)
    DB_IAM_USER        = try(google_sql_user.agent_iam_user.name, null)
    DB_NAME            = try(var.database_name, null)
    USE_CLOUD_SQL      = "0"
    EVENTS_DATASET_ID  = try(google_bigquery_dataset.analytics.dataset_id, null)
    EVENTS_TABLE_ID    = "agent_events"
  }

  final_env_vars = merge(
    local.yaml_env_vars,
    { for k, v in local.runtime_overrides : k => v if v != "" && v != null }
  )

  # Split the URI to get registry, repository, and image details dynamically
  image_uri_parts = split("/", var.agent_image_uri)
  registry_host   = local.image_uri_parts[0]
  registry_project= local.image_uri_parts[1]
  registry_repo   = local.image_uri_parts[2]
  
  image_name_and_tag = split(":", local.image_uri_parts[3])
  image_name         = local.image_name_and_tag[0]
  image_tag          = length(local.image_name_and_tag) > 1 ? local.image_name_and_tag[1] : "latest"
  
  registry_region = replace(split(".", local.registry_host)[0], "-docker", "")

  agent_card_json = var.agent_card_json
}

data "google_artifact_registry_docker_image" "agent_image" {

  project       = local.registry_project
  location      = local.registry_region
  repository_id = local.registry_repo
  image_name    = "${local.image_name}:${local.image_tag}"
}

resource "google_vertex_ai_reasoning_engine" "agent" {
  display_name = "${local.yaml_name}-${var.environment}"
  description  = local.yaml_desc
  region       = var.region
  project      = var.project_id
  depends_on   = [null_resource.trigger_bootstrap, google_storage_bucket.staging]

  spec {
    agent_framework = local.yaml_framework
    service_account = var.agent_service_account

    class_methods = jsonencode([
      {
        name           = "on_message_send"
        description    = "Send a message to the A2A agent"
        api_mode       = "a2a_extension"
        a2a_agent_card = local.agent_card_json
      },
      {
        name           = "handle_authenticated_agent_card"
        description    = "Retrieve the authenticated agent card"
        api_mode       = "a2a_extension"
        a2a_agent_card = local.agent_card_json
      },
      {
        name           = "on_get_task"
        description    = "Get a task by ID"
        api_mode       = "a2a_extension"
        a2a_agent_card = local.agent_card_json
      },
      {
        name           = "on_cancel_task"
        description    = "Cancel a task by ID"
        api_mode       = "a2a_extension"
        a2a_agent_card = local.agent_card_json
      }
    ])

    container_spec {
      image_uri = "${local.registry_host}/${local.registry_project}/${local.registry_repo}/${local.image_name}@${split("@", data.google_artifact_registry_docker_image.agent_image.name)[1]}"
    }



    deployment_spec {
      min_instances         = local.yaml_min_inst
      max_instances         = local.yaml_max_inst
      container_concurrency = local.yaml_concurrency

      resource_limits = (local.yaml_cpu != null || local.yaml_memory != null) ? {
        cpu    = local.yaml_cpu
        memory = local.yaml_memory
      } : null


      dynamic "env" {
        for_each = local.final_env_vars
        content {
          name  = env.key
          value = tostring(env.value)
        }
      }



      dynamic "psc_interface_config" {
        for_each = var.enable_psc_network ? [1] : []
        content {
          network_attachment = google_compute_network_attachment.psc_attachment[0].id

          dynamic "dns_peering_configs" {
            for_each = var.net_host_project_id != "" && var.vpc_name != "" ? [1] : []
            content {
              domain         = "esmeralda.internal."
              target_project = var.net_host_project_id
              target_network = var.vpc_name
            }
          }
        }
      }
    }
  }

  lifecycle {
    replace_triggered_by = [
      null_resource.trigger_bootstrap
    ]
  }
}

