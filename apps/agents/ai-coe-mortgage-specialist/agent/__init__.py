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

``app`` is the ADK App (agent + plugins). ``esmeralda serve`` serves it over A2A (agent card from
agent.yaml), and ``esmeralda query`` runs it in-process. ``a2a_task_store_builder`` persists A2A
tasks in Cloud SQL when USE_CLOUD_SQL=1 (in memory otherwise).
"""

import os

import esmeralda

esmeralda.prepare()

USER_AUTH_TOKEN_KEY = "user_auth_token"  # defined before .agent is imported: agent.agent imports it

from plugins.bq_analytics import create_bq_plugin  # noqa: E402

from .agent import mortgage_assistant_agent  # noqa: E402

_bq_plugin = create_bq_plugin()
app = esmeralda.create_app(mortgage_assistant_agent, plugins=[_bq_plugin] if _bq_plugin else [])

a2a_task_store_builder = None
if os.environ.get("USE_CLOUD_SQL", "0") == "1" and os.environ.get("CLOUD_SQL_INSTANCE"):
    from plugins.task_store import build_cloud_sql_taskstore as a2a_task_store_builder  # noqa: E402

__all__ = ["USER_AUTH_TOKEN_KEY", "a2a_task_store_builder", "app", "mortgage_assistant_agent"]
