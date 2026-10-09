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

"""CX mortgage orchestrator agent package.

``adk api_server`` (the container command) loads ``app`` from here. ``esmeralda.prepare()``
must run before the agent definition is imported, so its client patches apply to the clients
the agent creates.
"""

import esmeralda

esmeralda.prepare()

from .agent import root_agent  # noqa: E402

app = esmeralda.create_app(root_agent)

__all__ = ["app", "root_agent"]
