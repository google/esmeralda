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

"""Agent configuration: the agent's ``agent.yaml``.

Fields used by the library:

* ``name``: the agent's deployment name (also used by Terraform).
* ``framework``: ``google-adk`` (served with the ADK API server) or ``a2a`` (served as an A2A
  agent).
* ``env``: environment of the deployed agent (Terraform sets it on the engine). Local runs apply it
  too, so they behave like the deployed agent.
* ``local_env``: overrides for local runs only (for example local MCP server URLs).
* ``agent_card``: the A2A agent card (``a2a`` agents).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ADK = "google-adk"
A2A = "a2a"


@dataclass(frozen=True)
class AgentConfig:
    directory: Path
    name: str
    framework: str
    env: dict[str, str] = field(default_factory=dict)
    local_env: dict[str, str] = field(default_factory=dict)
    agent_card: dict[str, Any] = field(default_factory=dict)

    @property
    def is_a2a(self) -> bool:
        return self.framework == A2A

    @classmethod
    def load(cls, directory: str | os.PathLike = ".") -> AgentConfig:
        import yaml

        directory = Path(directory).resolve()
        path = directory / "agent.yaml"
        if not path.is_file():
            raise FileNotFoundError(f"No agent.yaml in {directory}")
        data = yaml.safe_load(path.read_text()) or {}
        framework = str(data.get("framework") or ADK)
        if framework not in (ADK, A2A):
            raise ValueError(f"{path}: framework must be '{ADK}' or '{A2A}', got '{framework}'")
        return cls(
            directory=directory,
            name=str(data.get("name") or directory.name),
            framework=framework,
            env=_strings(data.get("env")),
            local_env=_strings(data.get("local_env")),
            agent_card=dict(data.get("agent_card") or {}),
        )

    def apply_local_env(self, environ: MutableMapping[str, str] = os.environ) -> None:
        """Sets ``env`` then ``local_env`` in ``environ``, without overriding variables already set."""
        for key, value in {**self.env, **self.local_env}.items():
            environ.setdefault(key, value)


def _strings(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(k): _string(v) for k, v in value.items() if v is not None}


def _string(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
