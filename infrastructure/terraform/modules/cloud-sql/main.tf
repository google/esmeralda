# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# --- Private Services Access ---

resource "google_compute_global_address" "private_ip_range" {
  project       = var.project_id
  name          = "${var.name_prefix}-sql-private-ip"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = var.vpc_id
}

resource "google_service_networking_connection" "default" {
  network                 = var.vpc_id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_range.name]
}

# --- Cloud SQL Instance ---

resource "google_sql_database_instance" "default" {
  project          = var.project_id
  name             = "${var.name_prefix}-pg"
  region           = var.region
  database_version = "POSTGRES_15"

  depends_on = [google_service_networking_connection.default]

  settings {
    tier              = var.tier
    availability_type = "ZONAL"
    disk_size         = 10

    ip_configuration {
      ipv4_enabled    = true
      private_network = var.vpc_id
    }

    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }
  }

  deletion_protection = false
}

resource "google_sql_database" "a2a_tasks" {
  project  = var.project_id
  instance = google_sql_database_instance.default.name
  name     = var.database_name
}

# --- Users ---

resource "google_sql_user" "postgres" {
  project  = var.project_id
  instance = google_sql_database_instance.default.name
  name     = "postgres"
  password = random_password.postgres.result
}

resource "random_password" "postgres" {
  length  = 24
  special = false
}

resource "google_sql_user" "iam_user" {
  count    = var.enable_iam_user ? 1 : 0
  project  = var.project_id
  instance = google_sql_database_instance.default.name
  name     = var.agent_service_account
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"

  depends_on = [google_sql_user.postgres]
}

# --- Wait for DB Readiness ---

resource "null_resource" "wait_for_db" {
  count = var.enable_iam_user ? 1 : 0

  depends_on = [
    google_sql_database_instance.default,
    google_sql_database.a2a_tasks,
    google_sql_user.postgres,
    google_sql_user.iam_user[0]
  ]

  provisioner "local-exec" {
    command = <<EOT
      echo "⏳ Waiting for Cloud SQL instance ${google_sql_database_instance.default.name} to be fully online and ready..."
      for i in {1..30}; do
        STATE=$(gcloud sql instances describe ${google_sql_database_instance.default.name} --project=${var.project_id} --format="value(state)" 2>/dev/null || echo "UNKNOWN")
        if [ "$STATE" = "RUNNABLE" ]; then
          echo "✅ Cloud SQL Instance state is RUNNABLE! Sleeping 15s to allow PostgreSQL engine startup..."
          sleep 15
          exit 0
        fi
        echo "🔄 Database state is $STATE. Retrying in 10 seconds (Attempt $i/30)..."
        sleep 10
      done
      echo "❌ Timeout waiting for database engine"
      exit 1
    EOT
  }
}

# --- Grants ---

resource "postgresql_grant" "iam_user_database" {
  count       = var.enable_iam_user ? 1 : 0
  database    = google_sql_database.a2a_tasks.name
  role        = google_sql_user.iam_user[0].name
  object_type = "database"
  privileges  = ["ALL"]

  depends_on = [
    google_sql_user.iam_user,
    google_sql_database.a2a_tasks,
    null_resource.wait_for_db[0]
  ]
}

resource "postgresql_grant" "iam_user_schema" {
  count       = var.enable_iam_user ? 1 : 0
  database    = google_sql_database.a2a_tasks.name
  role        = google_sql_user.iam_user[0].name
  schema      = "public"
  object_type = "schema"
  privileges  = ["ALL"]

  depends_on = [
    google_sql_user.iam_user,
    null_resource.wait_for_db[0]
  ]
}

# --- IAM ---

resource "google_project_iam_member" "cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${var.agent_service_account}.gserviceaccount.com"
}

resource "google_project_iam_member" "cloudsql_instance_user" {
  project = var.project_id
  role    = "roles/cloudsql.instanceUser"
  member  = "serviceAccount:${var.agent_service_account}.gserviceaccount.com"
}
