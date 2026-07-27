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
import yaml

import google.auth
from google.adk.sessions import InMemorySessionService, VertexAiSessionService
from google.adk.runners import Runner
import a2a.types
try:
    import a2a.utils
    _tp = getattr(a2a.utils, "TransportProtocol", None)
except ImportError:
    _tp = None

if _tp and not hasattr(_tp, "http_json"):
    setattr(_tp, "http_json", getattr(_tp, "HTTP_JSON", "HTTP+JSON"))

if not hasattr(a2a.types, "TransportProtocol"):
    if _tp:
        setattr(a2a.types, "TransportProtocol", _tp)
    else:
        class _TransportProtocolShim:
            http_json = "HTTP+JSON"
            HTTP_JSON = "HTTP+JSON"
            JSONRPC = "JSONRPC"
            def __eq__(self, other):
                return True
        setattr(a2a.types, "TransportProtocol", _TransportProtocolShim)

from a2a.types import AgentCard, AgentCapabilities, AgentSkill
from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
from vertexai.preview.reasoning_engines.templates.a2a import A2aAgent
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
        return A2aAgentExecutor(
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


def load_agent_card_from_yaml():
    yaml_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.yaml")
    card_kwargs = {
        "name": os.environ.get("AGENT_NAME", "a2a-mortgage-agent"),
        "description": "Mortgage underwriting assistant with document management, "
                       "income verification, and corporate email capabilities.",
        "version": "1.0.0",
        "default_input_modes": ["text/plain"],
        "default_output_modes": ["application/json"],
        "capabilities": AgentCapabilities(streaming=False),
        "supports_authenticated_extended_card": True,
        "skills": [
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
    }
    if os.path.exists(yaml_path):
        with open(yaml_path, "r") as f:
            data = yaml.safe_load(f)
        card_data = data.get("agent_card", {})
        if card_data:
            skills = [AgentSkill(**s) for s in card_data.get("skills", [])]
            caps = card_data.get("capabilities", {})
            capabilities = AgentCapabilities(**caps) if isinstance(caps, dict) else caps
            card_kwargs.update({
                "name": card_data.get("name", card_kwargs["name"]),
                "description": card_data.get("description", card_kwargs["description"]),
                "version": card_data.get("version", card_kwargs["version"]),
                "default_input_modes": card_data.get("default_input_modes", card_kwargs["default_input_modes"]),
                "default_output_modes": card_data.get("default_output_modes", card_kwargs["default_output_modes"]),
                "capabilities": capabilities,
                "supports_authenticated_extended_card": card_data.get("supports_authenticated_extended_card", True),
                "skills": skills,
            })
    if "url" in card_data:
        card_kwargs["url"] = card_data["url"]
    if "preferred_transport" in card_data:
        card_kwargs["preferred_transport"] = card_data["preferred_transport"]

    tp = getattr(a2a.types, "TransportProtocol", None)
    pref_tp = getattr(tp, "HTTP_JSON", getattr(tp, "http_json", "HTTP+JSON")) if tp else "HTTP+JSON"

    card_kwargs.setdefault("url", "https://a2a-mortgage-agent.esmeralda.internal/api/a2a")
    card_kwargs.setdefault("preferred_transport", pref_tp)
    card_kwargs.setdefault("supports_authenticated_extended_card", True)

    return AgentCard(**card_kwargs)


def create_a2a_app():
    card = load_agent_card_from_yaml()
    from agent.telemetry_plugin import EsmeraldaTelemetryPlugin
    telemetry_plugin = EsmeraldaTelemetryPlugin(agent_name="a2a_mortgage_agent")
    plugins = [bq_logging_plugin, telemetry_plugin] if bq_logging_plugin else [telemetry_plugin]

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
