# Copyright 2025 Google LLC
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

"""The orchestrator package: the app and its A2A sub-agent (behavior of the A2A client is tested in esmeralda)."""

from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.apps import App


def test_app_serves_the_root_agent_with_the_telemetry_plugin():
    import agent

    assert isinstance(agent.app, App)
    assert agent.app.root_agent.name == "cx_mortgage_orchestrator"
    assert [p.name for p in agent.app.plugins] == ["esmeralda_telemetry"]


def test_specialist_is_an_a2a_sub_agent_through_the_gateway():
    from agent.remote_agent import A2A_AGENT_URL, mortgage_tools_agent

    assert isinstance(mortgage_tools_agent, RemoteA2aAgent)
    assert mortgage_tools_agent.name == "mortgage_tools_agent"
    assert mortgage_tools_agent._agent_card_source == f"{A2A_AGENT_URL}/v1/card"
