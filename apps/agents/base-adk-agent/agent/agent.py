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

import os
from google.adk.agents import Agent
from agent.remote_agent import mortgage_tools_agent
from agent.telemetry_plugin import EsmeraldaTelemetryPlugin

root_agent = Agent(
    name="root_agent",
    model=os.getenv("MODEL_NAME", "gemini-2.5-flash"),
    instruction="You are a mortgage underwriting assistant coordinator. "
                "Delegate all document search, income verification, and email "
                "operations to the mortgage_tools_agent.",
    sub_agents=[mortgage_tools_agent],
    plugins=[EsmeraldaTelemetryPlugin(agent_name="root_agent")],
)
