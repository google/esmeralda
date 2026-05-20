# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# =================================================================================
# 1. BigQuery Datasets
# =================================================================================

resource "google_bigquery_dataset" "agent_logs" {
  project                     = var.project_id
  dataset_id                  = "agent_logs"
  friendly_name               = "Logs do Agente Vertex AI"
  description                 = "Logs exportados do Vertex AI Reasoning Engine"
  location                    = var.region
  default_table_expiration_ms = 31536000000
  delete_contents_on_destroy  = true
}

resource "google_bigquery_table" "agent_events" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.agent_logs.dataset_id
  table_id            = "agent_events"
  deletion_protection = false

  schema = <<EOF
[
  {
    "name": "timestamp",
    "type": "TIMESTAMP",
    "mode": "REQUIRED",
    "description": "The UTC time at which the event was logged."
  },
  {
    "name": "event_type",
    "type": "STRING",
    "description": "Indicates the type of event being logged (e.g., 'LLM_REQUEST', 'TOOL_COMPLETED')."
  },
  {
    "name": "agent",
    "type": "STRING",
    "description": "The name of the ADK agent or author associated with the event."
  },
  {
    "name": "session_id",
    "type": "STRING",
    "description": "A unique identifier to group events within a single conversation or user session."
  },
  {
    "name": "invocation_id",
    "type": "STRING",
    "description": "A unique identifier for each individual agent execution or turn within a session."
  },
  {
    "name": "user_id",
    "type": "STRING",
    "description": "The identifier of the user associated with the current session."
  },
  {
    "name": "trace_id",
    "type": "STRING",
    "description": "OpenTelemetry trace ID for distributed tracing."
  },
  {
    "name": "span_id",
    "type": "STRING",
    "description": "OpenTelemetry span ID for this specific operation."
  },
  {
    "name": "parent_span_id",
    "type": "STRING",
    "description": "OpenTelemetry parent span ID to reconstruct hierarchy."
  },
  {
    "name": "content",
    "type": "JSON",
    "description": "The event-specific data (payload) stored as JSON."
  },
  {
    "name": "content_parts",
    "type": "RECORD",
    "mode": "REPEATED",
    "description": "Detailed content parts for multi-modal data.",
    "fields": [
      {
        "name": "mime_type",
        "type": "STRING"
      },
      {
        "name": "uri",
        "type": "STRING"
      },
      {
        "name": "object_ref",
        "type": "RECORD",
        "fields": [
          {
            "name": "uri",
            "type": "STRING"
          },
          {
            "name": "version",
            "type": "STRING"
          },
          {
            "name": "authorizer",
            "type": "STRING"
          },
          {
            "name": "details",
            "type": "JSON"
          }
        ]
      },
      {
        "name": "text",
        "type": "STRING"
      },
      {
        "name": "part_index",
        "type": "INTEGER"
      },
      {
        "name": "part_attributes",
        "type": "STRING"
      },
      {
        "name": "storage_mode",
        "type": "STRING"
      }
    ]
  },
  {
    "name": "attributes",
    "type": "JSON",
    "description": "Arbitrary key-value pairs for additional metadata."
  },
  {
    "name": "latency_ms",
    "type": "JSON",
    "description": "Latency measurements (e.g., total_ms)."
  },
  {
    "name": "status",
    "type": "STRING",
    "description": "The outcome of the event, typically 'OK' or 'ERROR'."
  },
  {
    "name": "error_message",
    "type": "STRING",
    "description": "Populated if an error occurs."
  },
  {
    "name": "is_truncated",
    "type": "BOOLEAN",
    "description": "Flag indicates if content was truncated."
  }
]
EOF

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  clustering = ["event_type", "agent", "user_id"]
}

# =================================================================================
# 1.1. Pre-create the gen_ai_choice table
# =================================================================================

resource "google_bigquery_table" "gen_ai_choice" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.agent_logs.dataset_id
  table_id            = "gen_ai_choice"
  deletion_protection = false

  schema = <<EOF
[
  { "name": "timestamp", "type": "TIMESTAMP", "mode": "NULLABLE" },
  { "name": "textPayload", "type": "STRING", "mode": "NULLABLE" },
  { "name": "jsonPayload", "type": "JSON", "mode": "NULLABLE" },
  { "name": "logName", "type": "STRING", "mode": "NULLABLE" },
  { "name": "trace", "type": "STRING", "mode": "NULLABLE" },
  { "name": "spanId", "type": "STRING", "mode": "NULLABLE" },
  { "name": "resource", "type": "JSON", "mode": "NULLABLE" },
  { "name": "labels", "type": "JSON", "mode": "NULLABLE" }
]
EOF

  lifecycle {
    ignore_changes = [
      schema,
      friendly_name,
      description
    ]
  }
}

# =================================================================================
# 1.2. Pre-create the gen_ai_user_message table
# =================================================================================

resource "google_bigquery_table" "gen_ai_user_message" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.agent_logs.dataset_id
  table_id            = "gen_ai_user_message"
  deletion_protection = false

  schema = <<EOF
[
  { "name": "timestamp", "type": "TIMESTAMP", "mode": "NULLABLE" },
  { "name": "textPayload", "type": "STRING", "mode": "NULLABLE" },
  { "name": "jsonPayload", "type": "JSON", "mode": "NULLABLE" },
  { "name": "logName", "type": "STRING", "mode": "NULLABLE" },
  { "name": "trace", "type": "STRING", "mode": "NULLABLE" },
  { "name": "spanId", "type": "STRING", "mode": "NULLABLE" }
]
EOF

  lifecycle {
    ignore_changes = [
      schema,
      friendly_name,
      description
    ]
  }
}

resource "google_bigquery_dataset" "trace_export" {
  project                    = var.project_id
  dataset_id                 = "cloud_trace_export"
  friendly_name              = "Exportação de Spans do Cloud Trace"
  location                   = "US"
  delete_contents_on_destroy = true
}

# =================================================================================
# 2. Logging Sink (Standard Logs)
# =================================================================================

resource "google_logging_project_sink" "to_bigquery" {
  project                = var.project_id
  name                   = "agent-logs-sink"
  destination            = "bigquery.googleapis.com/projects/${var.project_id}/datasets/${google_bigquery_dataset.agent_logs.dataset_id}"
  filter                 = "resource.type=\"aiplatform.googleapis.com/ReasoningEngine\" AND logName=~\"gen_ai\""
  unique_writer_identity = true
  bigquery_options {
    use_partitioned_tables = true
  }
}

# =================================================================================
# 3. Cloud Trace Export
# =================================================================================

resource "null_resource" "create_trace_sink" {
  triggers = {
    dataset_id = google_bigquery_dataset.trace_export.dataset_id
  }

  provisioner "local-exec" {
    command = <<EOT
      # 1. Create Sink (Safe to run multiple times)
      gcloud alpha trace sinks create trace-to-bq \
        bigquery.googleapis.com/projects/${var.project_number}/datasets/${google_bigquery_dataset.trace_export.dataset_id} \
        --project=${var.project_id} || echo "Sink exists, continuing..."

      # 2. Get the Writer Identity
      WRITER_IDENTITY=$(gcloud alpha trace sinks describe trace-to-bq --project=${var.project_id} --format="value(writer_identity)")
      echo "Identity found: $WRITER_IDENTITY"

      # 3. UPDATE DATASET ACL (Classic Method)
      # This avoids the "allowlisting" error of 'bq add-iam-policy-binding'
      
      # a. Download current dataset config to JSON
      bq show --format=json ${var.project_id}:${google_bigquery_dataset.trace_export.dataset_id} > current_access.json

      # b. Use jq to append the new Service Account to the access list
      # We filter to ensure we don't add duplicates, then add the new one
      jq --arg email "$WRITER_IDENTITY" \
         '.access += [{"role":"WRITER", "userByEmail": $email}]' \
         current_access.json > new_access.json

      # c. Upload the updated config
      bq update --source new_access.json ${var.project_id}:${google_bigquery_dataset.trace_export.dataset_id}

      # Cleanup temporary files
      rm current_access.json new_access.json
    EOT
  }

  depends_on = [
    google_bigquery_dataset.trace_export
  ]
}

# =================================================================================
# 4. Permissions
# =================================================================================

resource "google_project_iam_member" "bigquery_writer" {
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = google_logging_project_sink.to_bigquery.writer_identity
}

resource "google_project_iam_audit_config" "vertex_ai_audit" {
  project = var.project_id
  service = "aiplatform.googleapis.com"
  audit_log_config {
    log_type = "DATA_READ"
  }
  audit_log_config {
    log_type = "DATA_WRITE"
  }
}

resource "time_sleep" "wait_30_seconds" {
  depends_on = [
    google_bigquery_table.gen_ai_choice,
    google_bigquery_table.gen_ai_user_message
  ]

  create_duration = "30s"
}
