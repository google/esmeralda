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

"""Process lifecycle: ``prepare`` (before ADK) and ``finalize`` (after ADK is running).

* ``prepare()`` runs when the agent package is imported, before the agent and its clients are
  created. Its steps adjust the process: environment defaults and client patches needed for
  egress through the Agent Gateway.
* ``finalize()`` runs once ADK and its telemetry providers exist. ``adk api_server`` only builds
  the runner on the first request, so ``EsmeraldaTelemetryPlugin`` calls it from ``before_run_callback``;
  servers that have a set-up hook (the A2A template) can also call it directly.

Every step is idempotent, and a failing step raises: startup fails loudly instead of running
half-configured.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable

logger = logging.getLogger(__name__)

Step = Callable[[], None]

_PATCHED = "_esmeralda_patched"


# --------------------------------------------------------------------------------------------
# prepare steps
# --------------------------------------------------------------------------------------------


def log_egress_proxy() -> None:
    """Logs the forward-proxy env the runtime advertises for Agent Gateway egress."""
    proxies = {k: v for k, v in os.environ.items() if k.lower().endswith("_proxy")}
    logger.info("Egress proxy env: %s", proxies)


def default_environment() -> None:
    """Sets env defaults the ADK/genai clients read (never overrides explicit values)."""
    if "GOOGLE_CLOUD_PROJECT" not in os.environ:
        import google.auth
        from google.auth.exceptions import DefaultCredentialsError

        try:
            _, project_id = google.auth.default()
        except DefaultCredentialsError as exc:
            logger.warning("No Application Default Credentials; GOOGLE_CLOUD_PROJECT left unset: %s", exc)
            project_id = None
        if project_id:
            os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")
    os.environ.setdefault("GRPC_DNS_RESOLVER", "native")


def genai_over_httpx() -> None:
    """Forces google-genai onto httpx.

    The runtime routes egress through an HTTP(S) forward proxy advertised via *_PROXY env vars.
    aiohttp ignores https:// proxies, so google-genai's aiohttp path (Gemini and Vertex sessions)
    would try a direct connection and fail with "Network is unreachable". httpx honors the proxy
    env like requests does.
    """
    from google.genai import _api_client

    if getattr(_api_client.BaseApiClient._use_aiohttp, _PATCHED, False):
        return

    def _use_aiohttp(self) -> bool:
        return False

    setattr(_use_aiohttp, _PATCHED, True)
    _api_client.BaseApiClient._use_aiohttp = _use_aiohttp


def aiohttp_trust_env() -> None:
    """Makes aiohttp sessions honor the proxy env by default (trust_env=True)."""
    try:
        import aiohttp
    except ImportError:
        return

    original = aiohttp.ClientSession.__init__
    if getattr(original, _PATCHED, False):
        return

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("trust_env", True)
        original(self, *args, **kwargs)

    setattr(__init__, _PATCHED, True)
    aiohttp.ClientSession.__init__ = __init__


def genai_client_defaults() -> None:
    """Points google.genai.Client at MODEL_LOCATION (e.g. "global") and GOOGLE_CLOUD_PROJECT.

    Gemini models are served from the global endpoint while the agent runs in a region
    (GOOGLE_CLOUD_LOCATION), so the model location is set separately.
    """
    from google.genai import Client

    original = Client.__init__
    if getattr(original, _PATCHED, False):
        return

    def __init__(self, *args, **kwargs):
        model_location = os.environ.get("MODEL_LOCATION")
        if model_location:
            kwargs["location"] = model_location
        elif kwargs.get("location") is None:
            kwargs["location"] = "global"
        if kwargs.get("project") is None and os.environ.get("GOOGLE_CLOUD_PROJECT"):
            kwargs["project"] = os.environ["GOOGLE_CLOUD_PROJECT"]
        original(self, *args, **kwargs)

    setattr(__init__, _PATCHED, True)
    Client.__init__ = __init__


PREPARE: tuple[Step, ...] = (
    log_egress_proxy,
    default_environment,
    genai_over_httpx,
    aiohttp_trust_env,
    genai_client_defaults,
)


# --------------------------------------------------------------------------------------------
# finalize steps
# --------------------------------------------------------------------------------------------

_span_processor_providers: set[int] = set()
_warned_no_sdk_provider = False


def baggage_span_processor() -> None:
    """Registers BaggageSpanProcessor on the global tracer provider (once per provider)."""
    global _warned_no_sdk_provider
    from opentelemetry import trace

    from esmeralda.telemetry import BaggageSpanProcessor

    provider = trace.get_tracer_provider()
    if not hasattr(provider, "add_span_processor"):
        if not _warned_no_sdk_provider:
            logger.warning("No OpenTelemetry SDK tracer provider; caller attributes will not be added to spans.")
            _warned_no_sdk_provider = True
        return
    if id(provider) in _span_processor_providers:
        return
    provider.add_span_processor(BaggageSpanProcessor())
    _span_processor_providers.add(id(provider))
    logger.info("BaggageSpanProcessor registered on the global tracer provider.")


FINALIZE: tuple[Step, ...] = (baggage_span_processor,)


# --------------------------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------------------------


def prepare(steps: Iterable[Step] = PREPARE) -> None:
    """Runs the prepare steps. Call it before importing the agent definition."""
    for step in steps:
        step()


def finalize(steps: Iterable[Step] = FINALIZE) -> None:
    """Runs the finalize steps. Cheap to call repeatedly: each step is idempotent."""
    for step in steps:
        step()
