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

import base64
import datetime
import json

import pytest

import esmeralda
from esmeralda import models

GATEWAY = "https://apigee.esmeralda.internal/llm"


def _jwt(exp: int) -> str:
    def part(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return (
        f"{part({'alg': 'RS256', 'typ': 'JWT'})}.{part({'exp': exp, 'aud': 'https://apigee.esmeralda.internal'})}.c2ln"
    )


@pytest.fixture(autouse=True)
def _no_gateway(monkeypatch):
    monkeypatch.delenv(models.MODEL_GATEWAY_URL_ENV, raising=False)
    monkeypatch.delenv("MODEL_LOCATION", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "example-project")


def _tree():
    from google.adk.agents import Agent

    leaf = Agent(name="leaf", model="gemini-2.5-flash")
    inherits = Agent(name="inherits")  # empty model: inherited from the parent
    root = Agent(name="root", model="gemini-2.5-pro", sub_agents=[leaf, inherits])
    return root, leaf, inherits


def test_no_gateway_is_a_noop():
    root, leaf, inherits = _tree()
    assert models.gateway_url() is None
    assert models.route_models(root) is root
    assert root.model == "gemini-2.5-pro" and leaf.model == "gemini-2.5-flash" and inherits.model == ""
    assert models.gemini("gemini-2.5-flash") == "gemini-2.5-flash"


def test_create_app_without_gateway_keeps_models():
    root, leaf, _ = _tree()
    esmeralda.create_app(root)
    assert root.model == "gemini-2.5-pro" and leaf.model == "gemini-2.5-flash"


def test_gateway_routes_every_gemini_model(monkeypatch):
    from google.adk.models.google_llm import Gemini

    monkeypatch.setenv(models.MODEL_GATEWAY_URL_ENV, GATEWAY + "/")
    root, leaf, inherits = _tree()
    esmeralda.create_app(root)

    for agent, name in ((root, "gemini-2.5-pro"), (leaf, "gemini-2.5-flash")):
        assert isinstance(agent.model, Gemini)
        assert agent.model.model == name
        assert agent.model.base_url == GATEWAY
        credentials = agent.model.client_kwargs["credentials"]
        assert isinstance(credentials, models.GatewayIdTokenCredentials)
        assert credentials.audience == "https://apigee.esmeralda.internal"
    assert inherits.model == ""


def test_non_gemini_and_model_objects_are_left_alone(monkeypatch):
    from google.adk.agents import Agent
    from google.adk.models.google_llm import Gemini

    monkeypatch.setenv(models.MODEL_GATEWAY_URL_ENV, GATEWAY)
    custom = Gemini(model="gemini-2.5-flash")
    a = Agent(name="a", model=custom)
    b = Agent(name="b", model="claude-sonnet-4")
    root = Agent(name="root", model="projects/p/locations/l/publishers/google/models/gemini-2.5-pro", sub_agents=[a, b])
    models.route_models(root)
    assert a.model is custom
    assert b.model == "claude-sonnet-4"
    assert isinstance(root.model, Gemini)


@pytest.mark.parametrize("url", ["http://apigee.esmeralda.internal/llm", "apigee.esmeralda.internal/llm"])
def test_gateway_url_must_be_https(monkeypatch, url):
    monkeypatch.setenv(models.MODEL_GATEWAY_URL_ENV, url)
    with pytest.raises(ValueError, match="https"):
        models.gateway_url()


def test_gateway_client_targets_vertex_paths_on_the_gateway(monkeypatch):
    """The genai client keeps Vertex AI paths under the gateway base URL and sends the ID token."""
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")  # agent region; models use MODEL_LOCATION/global
    model = models.gemini("gemini-2.5-flash", url=GATEWAY)
    client = model.api_client._api_client
    request = client._build_request("post", "publishers/google/models/gemini-2.5-flash:generateContent", {})
    assert request.url == (
        f"{GATEWAY}/v1beta1/projects/example-project/locations/global"
        "/publishers/google/models/gemini-2.5-flash:generateContent"
    )

    credentials = model.client_kwargs["credentials"]
    credentials.token = "id-token-for-gateway"
    credentials.expiry = datetime.datetime.now(datetime.UTC).replace(tzinfo=None) + datetime.timedelta(hours=1)
    assert client._access_token() == "id-token-for-gateway"


def test_gateway_uses_model_location(monkeypatch):
    monkeypatch.setenv("MODEL_LOCATION", "us-central1")
    model = models.gemini("gemini-2.5-flash", url=GATEWAY)
    assert model.client_kwargs["location"] == "us-central1"
    assert model.client_kwargs["project"] == "example-project"
    assert model.client_kwargs["vertexai"] is True


def test_gateway_without_project_fails_loudly(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT")
    with pytest.raises(ValueError, match="GOOGLE_CLOUD_PROJECT"):
        models.gemini("gemini-2.5-flash", url=GATEWAY)


def test_id_token_credentials_refresh_from_runtime_identity(monkeypatch):
    exp = int(datetime.datetime.now(datetime.UTC).timestamp()) + 3600
    token = _jwt(exp)
    calls = []
    binds = []

    class FakeSource:
        token = None

        def refresh(self, request):
            calls.append(request)
            self.token = token

    def fake_fetch(audience, request, bind_id_token=True):
        assert audience == "https://apigee.esmeralda.internal"
        binds.append(bind_id_token)
        return FakeSource()

    monkeypatch.setenv("SERVICE_ACCOUNT_EMAIL", "sa-mcp-invoker@example-project.iam.gserviceaccount.com")
    monkeypatch.setattr("google.oauth2.id_token.fetch_id_token_credentials", fake_fetch)
    credentials = models.GatewayIdTokenCredentials("https://apigee.esmeralda.internal")
    assert not credentials.valid
    credentials.refresh("request")
    assert credentials.valid and credentials.token == token
    assert credentials.expiry == datetime.datetime.fromtimestamp(exp, datetime.UTC).replace(tzinfo=None)
    # Unbound: the Agent Gateway and the entry point re-terminate TLS before the model gateway.
    # Runtime identity even with SERVICE_ACCOUNT_EMAIL set: the gateway meters each agent's principal.
    assert binds == [False]

    headers: dict[str, str] = {}
    credentials.before_request("request", "POST", GATEWAY, headers)
    assert headers == {"authorization": f"Bearer {token}"}
    assert len(calls) == 1  # still valid: no second mint


def test_id_token_credentials_expire_before_the_token(monkeypatch):
    credentials = models.GatewayIdTokenCredentials("https://apigee.esmeralda.internal")
    credentials.token = "t"
    credentials.expiry = datetime.datetime.now(datetime.UTC).replace(tzinfo=None) + datetime.timedelta(minutes=2)
    assert credentials.expired  # inside google-auth's refresh threshold


def test_trace_headers_follow_the_current_span():
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    tracer = TracerProvider().get_tracer("test")
    assert "traceparent" not in models.trace_headers()  # no span: nothing to propagate
    with tracer.start_as_current_span("call_llm") as span:
        headers = models.trace_headers()
        trace_id = format(span.get_span_context().trace_id, "032x")
    assert headers["traceparent"].split("-")[1] == trace_id
    assert set(headers) <= {"traceparent", "tracestate"}
    assert trace.get_current_span().get_span_context().is_valid is False


def test_gateway_model_sends_the_trace_context(monkeypatch):
    """The routed model adds traceparent to each request, so gateway and agent records share trace_id."""
    import asyncio

    from google.adk.models.google_llm import Gemini
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types

    seen: list[dict[str, str]] = []

    async def fake_super(self, llm_request, stream=False):
        seen.append(dict(llm_request.config.http_options.headers or {}))
        yield "response"

    monkeypatch.setattr(Gemini, "generate_content_async", fake_super)
    monkeypatch.setattr(models, "trace_headers", lambda: {"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01"})
    model = models.gemini("gemini-2.5-flash", url=GATEWAY)
    assert isinstance(model, Gemini)
    request = LlmRequest(
        model="gemini-2.5-flash",
        config=types.GenerateContentConfig(http_options=types.HttpOptions(headers={"x-keep": "1"})),
    )

    async def run():
        return [r async for r in model.generate_content_async(request)]

    assert asyncio.run(run()) == ["response"]
    assert seen == [{"x-keep": "1", "traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01"}]
