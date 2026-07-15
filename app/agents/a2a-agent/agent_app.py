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
from plugins.bq_analytics import create_bq_plugin

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

try:
    _, _detected_project = google.auth.default()
    GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", _detected_project)
except Exception:
    GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")

bq_logging_plugin = create_bq_plugin()


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
                app_name="agent",
                session_service=_create_session_service(),
                plugins=self.plugins
            )
        )


class TelemetryA2aAgent(A2aAgent):
    """A2aAgent template subclass with OpenTelemetry GCP Trace exporter enabled."""
    def set_up(self):
        super().set_up()
        try:
            from opentelemetry.sdk.resources import OTELResourceDetector
            from google.adk.telemetry.google_cloud import get_gcp_exporters, get_gcp_resource
            from google.adk.telemetry.setup import maybe_set_otel_providers

            base_resource = get_gcp_resource(GOOGLE_CLOUD_PROJECT)
            env_resource = OTELResourceDetector().detect()
            otel_resource = base_resource.merge(env_resource)

            hooks = get_gcp_exporters(enable_cloud_tracing=True, enable_cloud_logging=True)
            maybe_set_otel_providers(otel_hooks_to_setup=[hooks], otel_resource=otel_resource)
            logger.info("✅ OpenTelemetry GCP Trace & Logging Exporters initialized with agent.yaml tags for a2a-agent.")
        except Exception as e:
            logger.error("Failed to initialize OpenTelemetry GCP Trace Exporter: %s", e)


def create_a2a_app():
    card = create_agent_card(
        agent_name=os.environ.get("AGENT_NAME", "a2a-mortgage-agent"),
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
    card.preferred_transport = "HTTP+JSON"
    plugins = [bq_logging_plugin] if bq_logging_plugin else []

    task_store_builder = None
    if os.environ.get("USE_CLOUD_SQL", "0") == "1" and os.environ.get("CLOUD_SQL_INSTANCE"):
        from plugins.task_store import build_cloud_sql_taskstore
        task_store_builder = build_cloud_sql_taskstore
        logger.info("Using Cloud SQL DatabaseTaskStore for A2A task persistence.")
    else:
        logger.info("Using InMemoryTaskStore for A2A task persistence.")

    return TelemetryA2aAgent(
        agent_card=card,
        agent_executor_builder=AdkAgentExecutorBuilder(mortgage_assistant_agent, plugins=plugins),
        task_store_builder=task_store_builder,
    )


try:
    adk_app = create_a2a_app()
    logger.info("a2a-mortgage-agent (A2aAgent template) initialized successfully.")
except Exception as e:
    logger.error("CRITICAL ERROR initializing agent_app.py: %s", e)
    traceback.print_exc(file=sys.stdout)
    raise e
