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

"""AI CoE mortgage specialist agent package.

``esmeralda.prepare()`` runs on import, before the agent definition (``agent.agent``) creates
its clients, so the Agent Gateway egress patches apply to them.

``app`` is the ADK App (agent + plugins). The A2A server (``agent_app.py``) serves it, and
``esmeralda query`` runs it in-process.
"""

import esmeralda

esmeralda.prepare()

USER_AUTH_TOKEN_KEY = "user_auth_token"  # defined before .agent is imported: agent.agent imports it

from plugins.bq_analytics import create_bq_plugin  # noqa: E402

from .agent import mortgage_assistant_agent  # noqa: E402

_bq_plugin = create_bq_plugin()
app = esmeralda.create_app(mortgage_assistant_agent, plugins=[_bq_plugin] if _bq_plugin else [])

__all__ = ["USER_AUTH_TOKEN_KEY", "app", "mortgage_assistant_agent"]
