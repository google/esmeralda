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

"""Operational runtime for Esmeralda agents on Agent Runtime.

An agent package uses it like this (``agent/__init__.py``)::

    import esmeralda

    esmeralda.prepare()

    from .agent import root_agent  # noqa: E402

    app = esmeralda.create_app(root_agent)

The container starts through ``esmeralda run -- <server command>``, which installs the Agent
Gateway root CA before the server starts.
"""

from esmeralda.app import create_app
from esmeralda.lifecycle import finalize, prepare
from esmeralda.plugin import EsmeraldaTelemetryPlugin

__all__ = ["EsmeraldaTelemetryPlugin", "create_app", "finalize", "prepare"]
