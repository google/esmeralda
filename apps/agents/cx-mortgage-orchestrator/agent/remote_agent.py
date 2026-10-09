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

"""The AI CoE mortgage specialist, as the orchestrator's sub-agent.

Deployed: an A2A call through the gateway (``A2A_AGENT_URL``), with ID-token auth, this agent's
caller context and the user's token handled by ``esmeralda.a2a.remote_agent``.

``LOCAL_MODE=true`` (workstation runs): the specialist's agent runs in-process instead.
"""

import logging
import os

from esmeralda import a2a

logger = logging.getLogger(__name__)

A2A_AGENT_URL = os.getenv("A2A_AGENT_URL", "https://ai-coe-mortgage-specialist.esmeralda.internal")
NAME = "mortgage_tools_agent"
DESCRIPTION = (
    "Mortgage underwriting assistant with document management, income verification, and corporate "
    "email capabilities. Delegate all mortgage-related queries to this agent."
)


def _load_local_specialist():
    """Imports the specialist's agent in-process (both agents' packages are named `agent`)."""
    import sys

    specialist_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../ai-coe-mortgage-specialist"))
    saved_modules = {
        name: sys.modules.pop(name) for name in list(sys.modules) if name == "agent" or name.startswith("agent.")
    }
    original_path = list(sys.path)
    sys.path.insert(0, specialist_dir)
    try:
        from agent.agent import mortgage_assistant_agent
    finally:
        sys.path = original_path
        sys.modules.update(saved_modules)
    mortgage_assistant_agent.name = NAME
    return mortgage_assistant_agent


mortgage_tools_agent = None
if os.getenv("LOCAL_MODE") == "true":
    logger.info("LOCAL_MODE: running the AI CoE mortgage specialist in-process.")
    mortgage_tools_agent = _load_local_specialist()
else:
    mortgage_tools_agent = a2a.remote_agent(NAME, url=A2A_AGENT_URL, description=DESCRIPTION)
