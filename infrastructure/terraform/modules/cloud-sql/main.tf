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
  project  = var.project_id
  instance = google_sql_database_instance.default.name
  name     = var.agent_service_account
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"

  depends_on = [google_sql_user.postgres]
}

# --- Grants ---

resource "postgresql_grant" "iam_user_database" {
  database    = google_sql_database.a2a_tasks.name
  role        = google_sql_user.iam_user.name
  object_type = "database"
  privileges  = ["ALL"]

  depends_on = [google_sql_user.iam_user, google_sql_database.a2a_tasks]
}

resource "postgresql_grant" "iam_user_schema" {
  database    = google_sql_database.a2a_tasks.name
  role        = google_sql_user.iam_user.name
  schema      = "public"
  object_type = "schema"
  privileges  = ["ALL"]

  depends_on = [google_sql_user.iam_user]
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
