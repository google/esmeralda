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

"""Mock heavy dependencies so unit tests don't require ADK installed."""

import sys
from unittest.mock import MagicMock

for mod in [
    "google.adk",
    "google.adk.agents",
    "google.adk.agents.llm_agent",
    "google.adk.agents.remote_a2a_agent",
    "google.adk.agents.callback_context",
    "google.adk.tools",
    "google.adk.tools.base_tool",
    "google.adk.tools.tool_context",
    "google.adk.tools.mcp_tool",
    "google.genai",
    "google.genai.types",
]:
    sys.modules.setdefault(mod, MagicMock())

class BasePlugin:
    def __init__(self, name: str = "base"):
        self.name = name

base_plugin_mod = MagicMock()
base_plugin_mod.BasePlugin = BasePlugin
sys.modules.setdefault("google.adk.plugins", base_plugin_mod)
sys.modules.setdefault("google.adk.plugins.base_plugin", base_plugin_mod)
