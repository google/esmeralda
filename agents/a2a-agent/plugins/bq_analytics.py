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

"""BigQuery Agent Analytics plugin setup."""

import logging
import os

import google.auth
from google.adk.plugins.bigquery_agent_analytics_plugin import (
    BigQueryAgentAnalyticsPlugin, BigQueryLoggerConfig
)

logger = logging.getLogger(__name__)


def create_bq_plugin():
    """Create BigQuery analytics plugin if configured, otherwise return None."""
    try:
        credentials, project_id_detected = google.auth.default()
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT", project_id_detected)
    except Exception:
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT")

    dataset_id = os.getenv("EVENTS_DATASET_ID")
    table_id = os.getenv("EVENTS_TABLE_ID")
    gcs_bucket = os.getenv("GCS_BUCKET")

    if not (project_id and dataset_id and table_id):
        return None

    config = BigQueryLoggerConfig(
        enabled=True,
        gcs_bucket_name=gcs_bucket,
        log_multi_modal_content=True,
        max_content_length=500 * 1024,
        batch_size=1,
        shutdown_timeout=10.0,
    )

    plugin = BigQueryAgentAnalyticsPlugin(
        project_id=project_id,
        dataset_id=dataset_id,
        table_id=table_id,
        config=config,
        location='US',
    )
    logger.info("BigQuery analytics plugin initialized.")
    return plugin
