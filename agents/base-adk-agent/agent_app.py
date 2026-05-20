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
import os
import sys
import logging
import json
import traceback
from typing import Any
import google.auth
from google.adk.plugins.bigquery_agent_analytics_plugin import BigQueryAgentAnalyticsPlugin, BigQueryLoggerConfig
from google.adk.apps import App
from vertexai.agent_engines.templates.adk import AdkApp

# Delay these imports to ensure they see the instrumented environment
from agent.agent import root_agent
from utils.base_agent_engine_app import BaseAgentEngineApp

# Configure Logging to stdout
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

print("🚀 Loading app.py module...", flush=True)

try:
    # --- Configuration ---
    try:
        # Resolve identity for BQ logging and telemetry identity
        credentials, project_id_detected = google.auth.default()
        GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", project_id_detected)
    except Exception as e:
        print(f"⚠️ Failed to get default auth: {e}", flush=True)
        GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")

    EVENTS_DATASET_ID = os.getenv("EVENTS_DATASET_ID")
    EVENTS_TABLE_ID = os.getenv("EVENTS_TABLE_ID")
    GCS_BUCKET = os.getenv("GCS_BUCKET")

    if not GOOGLE_CLOUD_PROJECT:
        raise ValueError("Please set GOOGLE_CLOUD_PROJECT environment variable.")
    if not EVENTS_DATASET_ID:
        raise ValueError("Please set EVENTS_DATASET_ID environment variable.")
    if not EVENTS_TABLE_ID:
        raise ValueError("Please set EVENTS_TABLE_ID environment variable.")

    # --- BigQuery Analytics Setup ---
    bq_config = BigQueryLoggerConfig(
        enabled=True,
        gcs_bucket_name=GCS_BUCKET,
        log_multi_modal_content=True,
        max_content_length=500 * 1024,
        batch_size=1,
        shutdown_timeout=10.0
    )

    bq_logging_plugin = BigQueryAgentAnalyticsPlugin(
        project_id=GOOGLE_CLOUD_PROJECT,
        dataset_id=EVENTS_DATASET_ID,
        table_id=EVENTS_TABLE_ID,
        config=bq_config,
        location='US'
    )

    # --- ADK Application Setup ---
    # BaseAgentEngineApp handles the Reasoning Engine specific endpoints
    adk_app = BaseAgentEngineApp(
        agent=root_agent,
        plugins=[bq_logging_plugin],
    )

    # Standard ADK App instance
    app = App(
        name="adk_app",
        root_agent=root_agent,
        plugins=[bq_logging_plugin],
    )

    logger.info("✨ App initialized successfully with Pipeline View telemetry active.")

except Exception as e:
    logger.error(f"❌ CRITICAL ERROR initializing app.py: {e}")
    traceback.print_exc(file=sys.stdout)
    raise e