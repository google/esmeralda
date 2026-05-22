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

import logging
import os
import sys
import traceback

import google.auth
from google.adk.plugins.bigquery_agent_analytics_plugin import (
    BigQueryAgentAnalyticsPlugin, BigQueryLoggerConfig
)
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.sessions.vertex_ai_session_service import VertexAiSessionService
from google.adk.runners import Runner
from vertexai.preview.reasoning_engines.templates.a2a import A2aAgent, create_agent_card
from google.adk.a2a.executor.a2a_agent_executor_impl import _A2aAgentExecutor
from a2a.types import AgentSkill

if os.getenv("USE_CUSTOM_TELEMETRY", "False").lower() == "true":
    try:
        from utils.telemetry import setup_telemetry
        setup_telemetry(custom_telemetry=True)
    except Exception as e:
        print(f"Telemetry setup failed: {e}", flush=True)

from agent.agent import mortgage_assistant_agent

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- BigQuery Analytics Setup (optional) ---
try:
    credentials, project_id_detected = google.auth.default()
    GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", project_id_detected)
except Exception:
    GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")

EVENTS_DATASET_ID = os.getenv("EVENTS_DATASET_ID")
EVENTS_TABLE_ID = os.getenv("EVENTS_TABLE_ID")
GCS_BUCKET = os.getenv("GCS_BUCKET")

bq_logging_plugin = None
if GOOGLE_CLOUD_PROJECT and EVENTS_DATASET_ID and EVENTS_TABLE_ID:
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
    logger.info("BigQuery analytics plugin initialized.")


def _create_session_service():
    """Use Vertex AI managed sessions on Agent Engine, in-memory locally."""
    agent_engine_id = os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID")
    if agent_engine_id:
        logger.info("Using VertexAiSessionService (managed sessions)")
        return VertexAiSessionService(
            project=GOOGLE_CLOUD_PROJECT,
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
            agent_engine_id=agent_engine_id,
        )
    logger.info("Using InMemorySessionService (local mode)")
    return InMemorySessionService()


class AdkAgentExecutorBuilder:
    """Builder class for the A2A Agent Executor."""
    def __init__(self, agent, plugins=None):
        self.agent = agent
        self.plugins = plugins or []

    def __call__(self):
        return _A2aAgentExecutor(
            runner=Runner(
                agent=self.agent,
                app_name="a2a_agent_app",
                session_service=_create_session_service(),
                plugins=self.plugins
            )
        )


def create_a2a_app():
    card = create_agent_card(
        agent_name="a2a-mortgage-agent",
        description="Mortgage underwriting assistant with document management, "
                    "income verification, and corporate email capabilities.",
        skills=[
            AgentSkill(
                id="document-search",
                name="Document Search",
                description="Search and retrieve mortgage application documents "
                            "from the legacy document management system.",
                tags=["dms", "documents"],
            ),
            AgentSkill(
                id="income-verification",
                name="Income Verification",
                description="Verify applicant income against employer records "
                            "and tax filings.",
                tags=["income", "verification"],
            ),
            AgentSkill(
                id="corporate-email",
                name="Corporate Email",
                description="Read corporate email inbox for mortgage-related "
                            "communications.",
                tags=["email"],
            ),
        ]
    )
    plugins = [bq_logging_plugin] if bq_logging_plugin else []
    return A2aAgent(
        agent_card=card,
        agent_executor_builder=AdkAgentExecutorBuilder(mortgage_assistant_agent, plugins=plugins)
    )


try:
    adk_app = create_a2a_app()
    logger.info("a2a-mortgage-agent (A2aAgent template) initialized successfully.")
except Exception as e:
    logger.error("CRITICAL ERROR initializing agent_app.py: %s", e)
    traceback.print_exc(file=sys.stdout)
    raise e
