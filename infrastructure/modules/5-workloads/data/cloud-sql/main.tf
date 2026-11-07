terraform {
  required_version = ">= 1.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.0"
    }
  }
}

locals {
  has_cmek = var.enabled && var.database_key_id != null && var.database_key_id != ""
}

# -----------------------------------------------------------------------------
# 1. CMEK SERVICE IDENTITY & KMS ENCRYPTION BINDING
# -----------------------------------------------------------------------------

resource "google_project_service_identity" "sqladmin" {
  count    = local.has_cmek ? 1 : 0
  provider = google-beta
  project  = var.project_id
  service  = "sqladmin.googleapis.com"
}

resource "google_kms_crypto_key_iam_member" "sqladmin_cmek" {
  count         = local.has_cmek ? 1 : 0
  crypto_key_id = var.database_key_id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_project_service_identity.sqladmin[0].email}"
}

# -----------------------------------------------------------------------------
# 2. STATEFUL DATA PLANE: PRIVATE SERVICES ACCESS & CLOUD SQL POSTGRESQL
# -----------------------------------------------------------------------------

# Provisions a secure, private PostgreSQL instance with IAM authentication and optional CMEK enabled
resource "google_sql_database_instance" "task_store" {
  count               = var.enabled ? 1 : 0
  name                = "${var.agent_name}-db-${var.environment}"
  project             = var.project_id
  region              = var.region
  database_version    = "POSTGRES_15"
  encryption_key_name = local.has_cmek ? var.database_key_id : null

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
  depends_on          = [google_kms_crypto_key_iam_member.sqladmin_cmek]
}

# Provisions the task store database
resource "google_sql_database" "agent_tasks" {
  count    = var.enabled ? 1 : 0
  name     = var.database_name
  instance = google_sql_database_instance.task_store[0].name
  project  = var.project_id
}

# Standard random password generator for local superuser postgres login
resource "random_password" "postgres_pwd" {
  count   = var.enabled ? 1 : 0
  length  = 24
  special = false
}

# Superuser root account
resource "google_sql_user" "postgres_user" {
  count    = var.enabled ? 1 : 0
  name     = "postgres"
  instance = google_sql_database_instance.task_store[0].name
  project  = var.project_id
  password = random_password.postgres_pwd[0].result
}

# Create IAM User mapped to the agent's service account to leverage Cloud IAM Db Authentication
resource "google_sql_user" "agent_user" {
  count      = var.enabled ? 1 : 0
  name       = trimsuffix(var.agent_service_account, ".gserviceaccount.com")
  instance   = google_sql_database_instance.task_store[0].name
  project    = var.project_id
  type       = "CLOUD_IAM_SERVICE_ACCOUNT"
  depends_on = [google_sql_user.postgres_user]
}

# Secret Manager entry publishing the resolved Cloud SQL connection details
resource "google_secret_manager_secret" "db_Connection" {
  count     = var.enabled ? 1 : 0
  secret_id = "${var.agent_name}-db-connection-${var.environment}"
  project   = var.project_id

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "db_Connection_val" {
  count  = var.enabled ? 1 : 0
  secret = google_secret_manager_secret.db_Connection[0].id
  secret_data = jsonencode({
    instance_connection_name = google_sql_database_instance.task_store[0].connection_name
    private_ip               = google_sql_database_instance.task_store[0].private_ip_address
    database_name            = google_sql_database.agent_tasks[0].name
    iam_user                 = google_sql_user.agent_user[0].name
    admin_password_secret_id = var.admin_password_secret_id
  })
}

# Wait for database engine to be fully runnable before executing grants
resource "terraform_data" "db_ready" {
  count = var.enabled ? 1 : 0
  depends_on = [
    google_sql_database_instance.task_store,
    google_sql_database.agent_tasks,
    google_sql_user.postgres_user,
    google_sql_user.agent_user
  ]

  provisioner "local-exec" {
    command = <<EOT
      echo "⏳ Waiting for Cloud SQL Instance ${google_sql_database_instance.task_store[0].name} to start..."
      for i in {1..30}; do
        STATE=$(gcloud sql instances describe ${google_sql_database_instance.task_store[0].name} --project=${var.project_id} --format="value(state)" 2>/dev/null || echo "UNKNOWN")
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
  count   = var.enabled ? 1 : 0
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${var.agent_service_account}"
}

# Assign roles/cloudsql.instanceUser to enable IAM database token injection
resource "google_project_iam_member" "cloudsql_user_role" {
  count   = var.enabled ? 1 : 0
  project = var.project_id
  role    = "roles/cloudsql.instanceUser"
  member  = "serviceAccount:${var.agent_service_account}"
}

# -----------------------------------------------------------------------------
# 3. PRIVILEGES BOOTSTRAP: VPC-BOUND CLOUD RUN JOB
# -----------------------------------------------------------------------------

resource "google_cloud_run_v2_job" "schema_bootstrap" {
  count               = var.enabled ? 1 : 0
  name                = "${var.agent_name}-db-bootstrap-${var.environment}"
  location            = var.region
  project             = var.project_id
  deletion_protection = false
  depends_on = [
    terraform_data.db_ready,
    google_project_iam_member.cloudsql_client_role,
    google_project_iam_member.cloudsql_user_role
  ]

  template {
    template {
      service_account = var.agent_service_account

      containers {
        image   = "alpine:latest"
        command = ["/bin/sh", "-c"]
        args = [
          "apk add --no-cache postgresql-client && psql \"postgresql://postgres:${random_password.postgres_pwd[0].result}@${google_sql_database_instance.task_store[0].private_ip_address}/${var.database_name}\" -c \"GRANT ALL PRIVILEGES ON DATABASE ${var.database_name} TO \\\"${google_sql_user.agent_user[0].name}\\\"; GRANT ALL PRIVILEGES ON SCHEMA public TO \\\"${google_sql_user.agent_user[0].name}\\\";\""
        ]
      }

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

# Trigger the bootstrap job execution during terraform apply
resource "terraform_data" "schema_bootstrap" {
  count      = var.enabled ? 1 : 0
  depends_on = [google_cloud_run_v2_job.schema_bootstrap]

  provisioner "local-exec" {
    command = <<EOT
      echo "🚀 Launching private Cloud Run PostgreSQL bootstrap job..."
      gcloud run jobs execute ${google_cloud_run_v2_job.schema_bootstrap[0].name} \
        --region="${var.region}" \
        --project="${var.project_id}" \
        --wait
    EOT
  }
}
