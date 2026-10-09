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

"""Query an agent, the same way for ADK and A2A agents, on any target.

Targets:

* in-process (default): loads the agent package's ``app`` and runs it through the ADK runner;
* ``url``: a running server (``esmeralda serve``, or any copy of the container);
* ``engine``: the agent deployed on Agent Runtime (``projects/P/locations/L/reasoningEngines/ID``).

ADK agents are called with ``async_stream_query``; A2A agents with ``message:send``. The caller
context is always sent: ``temp:caller_context`` session state for ADK agents, request metadata for
A2A agents.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass
from typing import Any

from esmeralda import context
from esmeralda.config import AgentConfig
from esmeralda.report import Report

_ENGINE = re.compile(r"^projects/[^/]+/locations/(?P<location>[^/]+)/reasoningEngines/[^/]+$")
_CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


@dataclass(frozen=True)
class Query:
    message: str
    user_id: str = "esmeralda-cli"
    session_id: str | None = None
    caller: context.CallerContext | None = None
    timeout: float = 300.0

    def caller_context(self) -> dict[str, str]:
        caller = self.caller or context.CallerContext(project_id=_default_project(), agent_name="esmeralda_cli")
        return caller.to_dict()


def _default_project() -> str:
    """GOOGLE_CLOUD_PROJECT, else the Application Default Credentials project, else "local"."""
    if os.environ.get("GOOGLE_CLOUD_PROJECT"):
        return os.environ["GOOGLE_CLOUD_PROJECT"]
    try:
        import google.auth

        return google.auth.default()[1] or "local"
    except Exception:
        return "local"


def parse_caller(value: str) -> context.CallerContext:
    """Parses ``project/agent`` (as given to ``--caller``)."""
    project_id, _, agent_name = value.partition("/")
    caller = context.CallerContext.from_mapping({"project_id": project_id, "agent_name": agent_name})
    if caller is None:
        raise ValueError(f"--caller must be 'project/agent', got '{value}'")
    return caller


def engine_base_url(engine: str, api_version: str = "v1beta1") -> str:
    match = _ENGINE.match(engine)
    if not match:
        raise ValueError(f"--engine must be 'projects/P/locations/L/reasoningEngines/ID', got '{engine}'")
    return f"https://{match['location']}-aiplatform.googleapis.com/{api_version}/{engine}"


def http_client(**kwargs: Any) -> Any:
    """The httpx client used for HTTP targets (tests swap in an in-memory transport)."""
    import httpx

    return httpx.AsyncClient(**kwargs)


def access_token() -> str:
    """An OAuth access token: Application Default Credentials, else the gcloud CLI."""
    try:
        import google.auth
        import google.auth.transport.requests

        credentials, _ = google.auth.default(scopes=[_CLOUD_SCOPE])
        credentials.refresh(google.auth.transport.requests.Request())
        if credentials.token:
            return credentials.token
    except Exception:  # fall back to the CLI, e.g. when ADC is not configured
        pass
    import subprocess

    return subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()


# ------------------------------------------------------------------------------------------
# in-process
# ------------------------------------------------------------------------------------------


def load_app(config: AgentConfig) -> Any:
    """Imports the agent package (``agent``) from the agent directory and returns its ADK App."""
    directory = str(config.directory)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    package = importlib.import_module("agent")
    app = getattr(package, "app", None)
    if app is None:
        from esmeralda.app import create_app

        app = create_app(package.root_agent)
    return app


async def query_in_process(config: AgentConfig, query: Query, report: Report) -> None:
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    config.apply_local_env()
    app = load_app(config)
    sessions = InMemorySessionService()
    runner = Runner(app=app, session_service=sessions)
    session_id = query.session_id
    if not session_id:
        session_id = (await sessions.create_session(app_name=app.name, user_id=query.user_id)).id
    else:
        await sessions.create_session(app_name=app.name, user_id=query.user_id, session_id=session_id)
    report.kv("session", session_id)
    report.section("📡 Events")
    async for event in runner.run_async(
        user_id=query.user_id,
        session_id=session_id,
        new_message=types.Content(role="user", parts=[types.Part(text=query.message)]),
        state_delta={context.STATE_KEY: query.caller_context()},
    ):
        report.adk_event(event.model_dump(mode="json", exclude_none=True))


# ------------------------------------------------------------------------------------------
# ADK over HTTP (local server or Agent Runtime)
# ------------------------------------------------------------------------------------------


async def query_adk_http(url: str, query: Query, report: Report, headers: dict[str, str]) -> None:
    payload_input: dict[str, Any] = {
        "message": query.message,
        "user_id": query.user_id,
        "state_delta": {context.STATE_KEY: query.caller_context()},
    }
    if query.session_id:
        payload_input["session_id"] = query.session_id
    payload = {"class_method": "async_stream_query", "input": payload_input}

    report.section("📡 Events")
    async with http_client(timeout=query.timeout) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as response:
            if response.status_code != 200:
                await response.aread()
                raise RuntimeError(f"HTTP {response.status_code} from {url}: {response.text[:1000]}")
            async for line in response.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                try:
                    report.adk_event(json.loads(line))
                except json.JSONDecodeError:
                    report.adk_event(line)


# ------------------------------------------------------------------------------------------
# A2A over HTTP (local server or Agent Runtime)
# ------------------------------------------------------------------------------------------


async def _fetch_card(resolver: Any) -> Any:
    """The authenticated card (``/v1/card``: Agent Runtime, servers with an extended card), else the public one."""
    from a2a.client.errors import A2AClientHTTPError

    try:
        return await resolver.get_agent_card(relative_card_path="/v1/card")
    except A2AClientHTTPError as exc:
        if exc.status_code != 404:
            raise
        return await resolver.get_agent_card()


async def query_a2a_http(base_url: str, query: Query, report: Report, headers: dict[str, str]) -> None:
    from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
    from a2a.types import Message, Part, Role, Task, TextPart, TransportProtocol

    async with http_client(timeout=query.timeout, headers=headers) as http:
        card = await _fetch_card(A2ACardResolver(http, base_url))
        # The card advertises the agent's own address (behind the gateway); talk to base_url instead.
        card.url = base_url
        card.preferred_transport = TransportProtocol.http_json
        report.kv("agent card", f"{card.name} {card.version}")

        client = ClientFactory(
            ClientConfig(httpx_client=http, supported_transports=[TransportProtocol.http_json], streaming=False)
        ).create(card)
        message = Message(
            role=Role.user,
            message_id=f"esmeralda-{uuid.uuid4()}",
            context_id=query.session_id,
            parts=[Part(root=TextPart(text=query.message))],
        )
        report.section("📡 Events")
        task = None
        async for item in client.send_message(
            message, request_metadata={context.CALLER_CONTEXT_KEY: query.caller_context()}
        ):
            if isinstance(item, tuple):
                task = item[0]
            elif isinstance(item, Task):
                task = item
            elif isinstance(item, Message):
                texts = [p.root.text for p in item.parts if isinstance(p.root, TextPart)]
                report.adk_event({"author": "agent", "content": {"parts": [{"text": t} for t in texts]}})
        if task is not None:
            report.a2a_task(task)


# ------------------------------------------------------------------------------------------
# entry point
# ------------------------------------------------------------------------------------------


async def run(
    config: AgentConfig,
    query: Query,
    report: Report,
    *,
    url: str | None = None,
    engine: str | None = None,
) -> None:
    """Runs ``query`` against the chosen target and feeds ``report``."""
    if url and engine:
        raise ValueError("Use either --url or --engine, not both")

    report.section(f"🧪 {config.name} ({config.framework})")
    report.kv("target", engine or url or "in-process")
    report.kv("query", query.message)
    report.kv("caller", "/".join(query.caller_context().values()))

    if not url and not engine:
        await query_in_process(config, query, report)
        return

    headers: dict[str, str] = {}
    if engine:
        base = engine_base_url(engine)
        headers["Authorization"] = f"Bearer {access_token()}"
        if config.is_a2a:
            await query_a2a_http(f"{base}/a2a", query, report, headers)
        else:
            await query_adk_http(f"{base}:streamQuery?alt=sse", query, report, headers)
        return

    base = url.rstrip("/")
    if config.is_a2a:
        await query_a2a_http(base, query, report, headers)
    else:
        await query_adk_http(f"{base}/api/stream_reasoning_engine", query, report, headers)
