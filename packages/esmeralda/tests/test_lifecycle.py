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

import pytest
from google.genai import Client, _api_client

from esmeralda import lifecycle


@pytest.fixture
def restore_genai():
    use_aiohttp, client_init = _api_client.BaseApiClient._use_aiohttp, Client.__init__
    yield
    _api_client.BaseApiClient._use_aiohttp, Client.__init__ = use_aiohttp, client_init


def test_prepare_runs_steps_in_order():
    calls = []
    lifecycle.prepare([lambda: calls.append(1), lambda: calls.append(2)])
    assert calls == [1, 2]


def test_prepare_fails_loudly():
    def broken():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        lifecycle.prepare([broken])


def test_default_environment_keeps_explicit_values(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "explicit-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    lifecycle.default_environment()
    import os

    assert os.environ["GOOGLE_CLOUD_PROJECT"] == "explicit-project"
    assert os.environ["GOOGLE_CLOUD_LOCATION"] == "us-central1"
    assert os.environ["GOOGLE_GENAI_USE_VERTEXAI"] == "True"


def test_genai_patches_are_idempotent(restore_genai):
    lifecycle.genai_over_httpx()
    lifecycle.genai_client_defaults()
    patched = (_api_client.BaseApiClient._use_aiohttp, Client.__init__)

    lifecycle.genai_over_httpx()
    lifecycle.genai_client_defaults()

    assert (_api_client.BaseApiClient._use_aiohttp, Client.__init__) == patched
    assert _api_client.BaseApiClient._use_aiohttp(None) is False


def test_genai_client_uses_model_location(restore_genai, monkeypatch):
    seen = {}

    def fake_init(self, *args, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(Client, "__init__", fake_init)
    monkeypatch.setenv("MODEL_LOCATION", "global")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "agents-project")
    lifecycle.genai_client_defaults()

    Client(vertexai=True, location="us-central1")

    assert seen == {"vertexai": True, "location": "global", "project": "agents-project"}


def test_finalize_registers_span_processor_once(monkeypatch):
    from opentelemetry import trace

    added = []

    class Provider:
        def add_span_processor(self, processor):
            added.append(processor)

    provider = Provider()
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    lifecycle.finalize()
    lifecycle.finalize()
    assert len(added) == 1
