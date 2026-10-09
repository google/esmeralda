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

"""create_app: the ADK App an Esmeralda agent package exports as ``app``.

The ADK agent loader (``adk api_server``, ``adk web``) looks for ``app`` before ``root_agent``,
so exporting it is enough for the plugin to run in every serving mode.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from esmeralda.plugin import EsmeraldaTelemetryPlugin


def create_app(root_agent: Any, *, name: str = "agent", plugins: Iterable[Any] = ()) -> Any:
    """Returns ``google.adk.apps.App`` with EsmeraldaTelemetryPlugin first, then ``plugins``.

    ``name`` must match the agent package directory (``agent``), which is also the
    ``--gemini_enterprise_app_name`` the container passes to ``adk api_server``.
    """
    from google.adk.apps import App

    return App(name=name, root_agent=root_agent, plugins=[EsmeraldaTelemetryPlugin(), *plugins])
