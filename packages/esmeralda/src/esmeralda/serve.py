# Copyright 2026 Google LLC
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

"""Serve an agent: the same server locally and in the container.

* ``google-adk`` agents: the ADK API server for the agent directory, with the Agent Runtime
  endpoints (``--gemini_enterprise_app_name agent``), or the ADK dev UI with ``web=True``.
* ``a2a`` agents: an A2A REST server for the package's ``app``, built from the ``agent_card`` in
  ``agent.yaml``, mounted at ``/``, ``/a2a`` and ``/api/a2a`` (gateway path compatibility).

Agent-specific A2A options come from the agent package: an optional ``a2a_task_store_builder``
(callable returning an A2A ``TaskStore``, for example Cloud SQL). Without it, tasks live in memory.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from esmeralda.config import AgentConfig

logger = logging.getLogger(__name__)

APP_NAME = "agent"
A2A_MOUNTS = ("/a2a", "/api/a2a", "/")


def default_port() -> int:
    return int(os.environ.get("PORT", "8080"))


# ------------------------------------------------------------------------------------------
# ADK
# ------------------------------------------------------------------------------------------


def adk_command(
    config: AgentConfig, *, host: str, port: int, web: bool = False, otel_to_cloud: bool = False
) -> list[str]:
    """The ``adk`` command line that serves the agent directory."""
    command = ["adk", "web" if web else "api_server", str(config.directory), "--host", host, "--port", str(port)]
    command.append("--no-reload")
    if not web:
        command += ["--gemini_enterprise_app_name", APP_NAME]
    if otel_to_cloud:
        command.append("--otel_to_cloud")
    return command


# ------------------------------------------------------------------------------------------
# A2A
# ------------------------------------------------------------------------------------------


def agent_card(config: AgentConfig) -> Any:
    """The A2A agent card from ``agent.yaml`` ``agent_card``; ``A2A_AGENT_URL`` overrides its url."""
    from a2a.types import AgentCapabilities, AgentCard, AgentSkill, TransportProtocol

    data = config.agent_card
    capabilities = data.get("capabilities") or {}
    return AgentCard(
        name=data.get("name") or config.name,
        description=data.get("description") or config.name,
        version=str(data.get("version") or "1.0.0"),
        url=os.environ.get("A2A_AGENT_URL") or data.get("url") or f"https://{config.name}.esmeralda.internal",
        preferred_transport=data.get("preferred_transport") or TransportProtocol.http_json,
        capabilities=AgentCapabilities(**capabilities) if isinstance(capabilities, dict) else capabilities,
        default_input_modes=data.get("default_input_modes") or ["text/plain"],
        default_output_modes=data.get("default_output_modes") or ["application/json"],
        supports_authenticated_extended_card=data.get("supports_authenticated_extended_card", True),
        skills=[AgentSkill(**skill) for skill in data.get("skills") or []],
    )


def session_service() -> Any:
    """Vertex AI managed sessions on Agent Runtime; in memory locally or with USE_IN_MEMORY_SESSIONS=1."""
    from google.adk.sessions import InMemorySessionService, VertexAiSessionService

    engine_id = os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID")
    if os.environ.get("USE_IN_MEMORY_SESSIONS", "0") == "1" or not engine_id:
        logger.info("A2A sessions: in memory")
        return InMemorySessionService()
    logger.info("A2A sessions: Vertex AI managed sessions (engine %s)", engine_id)
    return VertexAiSessionService(
        project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        agent_engine_id=engine_id,
    )


class _ExecutorBuilder:
    """Builds the ADK A2A executor for the app (called by the A2A template's set_up)."""

    def __init__(self, app: Any):
        self.app = app

    def __call__(self) -> Any:
        from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
        from google.adk.runners import Runner

        runner = Runner(
            agent=self.app.root_agent,
            app_name=APP_NAME,
            session_service=session_service(),
            plugins=list(self.app.plugins),
        )
        return A2aAgentExecutor(runner=runner, use_legacy=False)


def _enable_otel_to_cloud() -> None:
    """The A2A equivalent of adk api_server --otel_to_cloud: Cloud Trace and Cloud Logging exporters."""
    from google.adk.telemetry.google_cloud import get_gcp_exporters, get_gcp_resource
    from google.adk.telemetry.setup import maybe_set_otel_providers
    from opentelemetry.sdk.resources import OTELResourceDetector

    resource = get_gcp_resource(os.environ.get("GOOGLE_CLOUD_PROJECT")).merge(OTELResourceDetector().detect())
    hooks = get_gcp_exporters(enable_cloud_tracing=True, enable_cloud_logging=True)
    maybe_set_otel_providers(otel_hooks_to_setup=[hooks], otel_resource=resource)
    logger.info("OpenTelemetry Cloud Trace and Cloud Logging exporters enabled.")


def a2a_server(config: AgentConfig, *, otel_to_cloud: bool = False) -> Any:
    """The FastAPI application serving the agent over A2A (REST transport)."""
    from a2a.server.apps import A2ARESTFastAPIApplication
    from fastapi import FastAPI
    from vertexai.preview.reasoning_engines.templates.a2a import A2aAgent

    import esmeralda
    from esmeralda.query import load_app, load_package

    app = load_app(config)
    package = load_package(config)
    card = agent_card(config)
    url = card.url
    template = A2aAgent(
        agent_card=card,
        agent_executor_builder=_ExecutorBuilder(app),
        task_store_builder=getattr(package, "a2a_task_store_builder", None),
    )
    template.set_up()
    template.agent_card.url = url  # set_up points the card at the engine; keep the gateway address

    if otel_to_cloud:
        _enable_otel_to_cloud()
    esmeralda.finalize()  # the tracer provider exists now

    rest = A2ARESTFastAPIApplication(
        agent_card=template.agent_card,
        extended_agent_card=template.agent_card,
        http_handler=template.request_handler,
    ).build()
    server = FastAPI(title=f"{card.name} (A2A)")
    for path in A2A_MOUNTS:
        server.mount(path, rest)
    return server


# ------------------------------------------------------------------------------------------
# entry point
# ------------------------------------------------------------------------------------------


def serve(
    config: AgentConfig,
    *,
    host: str = "0.0.0.0",
    port: int | None = None,
    local: bool = False,
    web: bool = False,
    otel_to_cloud: bool = False,
) -> None:
    """Serves the agent. ADK agents replace this process with ``adk``; A2A agents run uvicorn."""
    port = port or default_port()
    if local:
        config.apply_local_env()
    else:
        for key, value in config.env.items():  # already set on Agent Runtime; defaults elsewhere
            os.environ.setdefault(key, value)

    if config.is_a2a:
        if web:
            raise ValueError("--web is only available for ADK agents")
        import uvicorn

        uvicorn.run(a2a_server(config, otel_to_cloud=otel_to_cloud), host=host, port=port)
        return

    command = adk_command(config, host=host, port=port, web=web, otel_to_cloud=otel_to_cloud)
    logger.info("exec %s", " ".join(command))
    os.chdir(config.directory)
    os.execvp(command[0], command)
