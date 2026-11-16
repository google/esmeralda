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

"""Model egress: send an agent's Gemini calls through a model gateway when one is configured.

With ``ESMERALDA_MODEL_GATEWAY_URL`` set (e.g. ``https://apigee.esmeralda.internal/llm``, written
by the agent's Terraform unit only when ``governance.capabilities.model_gateway = "apigee"``),
``route_models(root_agent)`` gives every ADK ``LlmAgent`` whose model is a Gemini model name a
``Gemini`` model that calls the gateway instead of ``*-aiplatform.googleapis.com``. Request paths
stay the Vertex AI ones (``/v1beta1/projects/<p>/locations/<l>/publishers/google/models/...``), so the
gateway is a pass-through.

The gateway authenticates the caller with an **ID token** (audience: the gateway origin) minted for
the agent's **runtime identity**: on Agent Runtime, the agent's own Agent Identity, so the gateway
verifies and meters each agent as its ``principal://``. Never ``SERVICE_ACCOUNT_EMAIL`` (the
deprecated shared invoker), which would bill every agent's tokens to one identity. The token is
**unbound** (``esmeralda.auth``): the call crosses the Central Agent Gateway and the entry point,
which re-terminate TLS, so a certificate-bound token could not be checked at the gateway.

Each call also carries the W3C ``traceparent`` of the current span, so the gateway's
``esmeralda.usage`` record shares its ``trace_id`` with the agent's own ``llm.call`` record and the
usage views count the tokens once.

Unset (the default), nothing changes: agents call Vertex AI directly.
"""

from __future__ import annotations

import datetime
import logging
import os
import threading
import urllib.parse
from typing import Any

logger = logging.getLogger(__name__)

MODEL_GATEWAY_URL_ENV = "ESMERALDA_MODEL_GATEWAY_URL"
_FALLBACK_LIFETIME = datetime.timedelta(minutes=5)


def gateway_url() -> str | None:
    """The configured model gateway base URL, or ``None`` (direct Vertex AI calls)."""
    url = os.environ.get(MODEL_GATEWAY_URL_ENV, "").strip().rstrip("/")
    if not url:
        return None
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.netloc:
        raise ValueError(f"{MODEL_GATEWAY_URL_ENV} must be an absolute https URL, got {url!r}")
    return url


def is_gemini(model: Any) -> bool:
    """Whether ``model`` is a Gemini model name ADK would resolve to its ``Gemini`` class."""
    if not isinstance(model, str):
        return False
    name = model.rsplit("/models/", 1)[-1] if "/models/" in model else model
    return name.startswith("gemini-")


def _jwt_expiry(token: str) -> datetime.datetime:
    """The token's ``exp`` (naive UTC, as google-auth expects), read without verification."""
    from google.auth import jwt

    try:
        exp = jwt.decode(token, verify=False).get("exp")
        return datetime.datetime.fromtimestamp(int(exp), datetime.UTC).replace(tzinfo=None)
    except Exception:  # noqa: BLE001 - an unreadable exp only shortens the cache lifetime
        return datetime.datetime.now(datetime.UTC).replace(tzinfo=None) + _FALLBACK_LIFETIME


class GatewayIdTokenCredentials:
    """google-auth style credentials whose bearer token is an ID token for the gateway.

    ``google.genai`` refreshes them when they expire and sends ``Authorization: Bearer <token>``.
    The ID token comes from the runtime identity (metadata server or ADC).
    """

    def __init__(self, audience: str) -> None:
        self.audience = audience
        self.token: str | None = None
        self.expiry: datetime.datetime | None = None
        self.quota_project_id = None
        self._source: Any = None
        self._lock = threading.Lock()

    @property
    def expired(self) -> bool:
        if self.expiry is None:
            return False
        skew = datetime.timedelta(minutes=3, seconds=45)  # google-auth's refresh threshold
        return datetime.datetime.now(datetime.UTC).replace(tzinfo=None) >= self.expiry - skew

    @property
    def valid(self) -> bool:
        return self.token is not None and not self.expired

    def refresh(self, request: Any) -> None:
        from esmeralda import auth

        with self._lock:
            if self._source is None:
                self._source = auth.unbound_id_token_credentials(self.audience, request)
            self._source.refresh(request)
            self.token = self._source.token
            self.expiry = _jwt_expiry(self.token)

    def before_request(self, request: Any, method: str, url: str, headers: dict[str, str]) -> None:
        if not self.valid:
            self.refresh(request)
        headers["authorization"] = f"Bearer {self.token}"


def trace_headers() -> dict[str, str]:
    """W3C trace context (``traceparent``, ``tracestate``) of the current span; empty without one."""
    try:
        from opentelemetry import propagate
    except ImportError:  # pragma: no cover - opentelemetry is a dependency
        return {}
    carrier: dict[str, str] = {}
    try:
        propagate.inject(carrier)
    except Exception:  # noqa: BLE001 - tracing must never break a model call
        logger.debug("Could not inject the trace context into a model call", exc_info=True)
        return {}
    return {k: v for k, v in carrier.items() if k in ("traceparent", "tracestate")}


_gateway_gemini_class: Any = None


def _gateway_gemini() -> Any:
    """ADK ``Gemini`` that adds the current trace context to every request it sends."""
    global _gateway_gemini_class
    if _gateway_gemini_class is None:
        from google.adk.models.google_llm import Gemini
        from google.genai import types

        class GatewayGemini(Gemini):
            """``Gemini`` through a model gateway: requests carry ``traceparent``."""

            async def generate_content_async(self, llm_request: Any, stream: bool = False) -> Any:
                headers = trace_headers()
                if headers:
                    if llm_request.config is None:
                        llm_request.config = types.GenerateContentConfig()
                    if not llm_request.config.http_options:
                        llm_request.config.http_options = types.HttpOptions()
                    llm_request.config.http_options.headers = {
                        **(llm_request.config.http_options.headers or {}),
                        **headers,
                    }
                async for response in super().generate_content_async(llm_request, stream=stream):
                    yield response

        _gateway_gemini_class = GatewayGemini
    return _gateway_gemini_class


def gemini(model: str, *, url: str | None = None) -> Any:
    """A model for ``LlmAgent(model=...)``: ``model`` itself without a gateway, else an ADK ``Gemini``
    that calls the gateway with an ID token for it.

    google-genai drops the project and location from the request path when a custom base URL comes
    without them, so both are passed explicitly: ``GOOGLE_CLOUD_PROJECT``, and the model location
    ``esmeralda.prepare()`` gives direct calls (``MODEL_LOCATION``, else ``global``).
    """
    url = url or gateway_url()
    if not url:
        return model

    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project:
        raise ValueError(f"{MODEL_GATEWAY_URL_ENV} is set but GOOGLE_CLOUD_PROJECT is not (run esmeralda.prepare())")
    location = os.environ.get("MODEL_LOCATION", "").strip() or "global"
    parts = urllib.parse.urlsplit(url)
    audience = f"{parts.scheme}://{parts.netloc}"
    return _gateway_gemini()(
        model=model,
        base_url=url,
        client_kwargs={
            "vertexai": True,
            "project": project,
            "location": location,
            "credentials": GatewayIdTokenCredentials(audience),
        },
    )


def _children(agent: Any) -> list[Any]:
    return list(getattr(agent, "sub_agents", None) or [])


def route_models(root_agent: Any) -> Any:
    """Routes the Gemini models of ``root_agent`` and its sub-agents through the model gateway.

    No-op when ``ESMERALDA_MODEL_GATEWAY_URL`` is unset. Only string Gemini model names are
    replaced; model objects (``Gemini``, ``LiteLlm``...) and inherited (empty) models are left as is.
    Returns ``root_agent``.
    """
    url = gateway_url()
    if not url:
        return root_agent
    seen: set[int] = set()
    stack = [root_agent]
    routed = 0
    while stack:
        agent = stack.pop()
        if id(agent) in seen:
            continue
        seen.add(id(agent))
        model = getattr(agent, "model", None)
        if is_gemini(model):
            agent.model = gemini(model, url=url)
            routed += 1
        stack.extend(_children(agent))
    logger.info("Model gateway %s: routed %d Gemini model(s).", url, routed)
    return root_agent
