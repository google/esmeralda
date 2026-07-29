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

import os
import pytest
from utils.resolver import resolve_agent_endpoint


def test_resolve_agent_endpoint_default_path_mode(monkeypatch):
    monkeypatch.delenv("GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("GATEWAY_ROUTING_MODE", raising=False)
    url = resolve_agent_endpoint("a2a-mortgage-agent")
    assert url == "https://gateway.esmeralda.internal/agents/a2a-mortgage-agent/api/a2a"


def test_resolve_agent_endpoint_custom_gateway_url(monkeypatch):
    monkeypatch.setenv("GATEWAY_BASE_URL", "https://api.company.com/v1")
    monkeypatch.setenv("GATEWAY_ROUTING_MODE", "path")
    url = resolve_agent_endpoint("income-verification-agent")
    assert url == "https://api.company.com/v1/agents/income-verification-agent/api/a2a"


def test_resolve_agent_endpoint_host_mode(monkeypatch):
    monkeypatch.setenv("GATEWAY_ROUTING_MODE", "host")
    url = resolve_agent_endpoint("a2a-mortgage-agent")
    assert url == "https://a2a-mortgage-agent.esmeralda.internal/api/a2a"
