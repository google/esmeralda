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

"""Caller context: which project and agent made the call, for telemetry.

How it travels:

* Direct calls (Agent Runtime query API): the caller sends it as session state,
  ``"state_delta": {"temp:caller_context": {"project_id": ..., "agent_name": ...}}``. The
  ``temp:`` prefix keeps it for the current invocation only; it is never persisted.
* Agent-to-agent calls (A2A): the calling agent adds ``outgoing_metadata()`` to the A2A request
  metadata; ADK exposes it on the receiving side as ``run_config.custom_metadata["a2a_metadata"]``.

``EsmeraldaTelemetryPlugin`` reads it with ``from_invocation`` and puts it in OpenTelemetry baggage, and
``BaggageSpanProcessor`` copies it onto every span.

The values are whatever the caller sends: use them for telemetry only, never for authorization.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

CALLER_CONTEXT_KEY = "caller_context"
STATE_KEY = f"temp:{CALLER_CONTEXT_KEY}"
A2A_METADATA_KEY = "a2a_metadata"

BAGGAGE_PROJECT_ID = "caller.project_id"
BAGGAGE_AGENT_NAME = "caller.agent_name"

_MAX_LEN = 128


@dataclass(frozen=True)
class CallerContext:
    project_id: str
    agent_name: str

    @classmethod
    def from_mapping(cls, value: Any) -> CallerContext | None:
        """Builds a CallerContext from untrusted input; returns None if there is nothing usable."""
        if not isinstance(value, Mapping):
            return None
        project_id = _clean(value.get("project_id"))
        agent_name = _clean(value.get("agent_name"))
        if not project_id and not agent_name:
            return None
        return cls(project_id=project_id or "unknown", agent_name=agent_name or "unknown")

    def to_dict(self) -> dict[str, str]:
        return {"project_id": self.project_id, "agent_name": self.agent_name}


def _clean(value: Any) -> str:
    return value.strip()[:_MAX_LEN] if isinstance(value, str) else ""


def current_identity() -> CallerContext:
    """This agent's own identity, as it should appear to the agents it calls."""
    return CallerContext(
        project_id=os.environ.get("GOOGLE_CLOUD_PROJECT") or "unknown",
        agent_name=os.environ.get("AGENT_NAME") or "unknown",
    )


def outgoing_metadata() -> dict[str, dict[str, str]]:
    """A2A request metadata that identifies this agent to the agent it calls."""
    return {CALLER_CONTEXT_KEY: current_identity().to_dict()}


def from_invocation(invocation_context: Any) -> CallerContext | None:
    """Reads the caller context of an ADK invocation (session state first, then A2A metadata)."""
    session = getattr(invocation_context, "session", None)
    state = getattr(session, "state", None)
    if isinstance(state, Mapping):
        caller = CallerContext.from_mapping(state.get(STATE_KEY))
        if caller:
            return caller

    run_config = getattr(invocation_context, "run_config", None)
    custom_metadata = getattr(run_config, "custom_metadata", None)
    if isinstance(custom_metadata, Mapping):
        a2a_metadata = custom_metadata.get(A2A_METADATA_KEY)
        if isinstance(a2a_metadata, Mapping):
            return CallerContext.from_mapping(a2a_metadata.get(CALLER_CONTEXT_KEY))
    return None


def attach_baggage(caller: CallerContext) -> object:
    """Puts the caller in OpenTelemetry baggage for the current context; returns a detach token."""
    from opentelemetry import baggage, context

    ctx = baggage.set_baggage(BAGGAGE_PROJECT_ID, caller.project_id)
    ctx = baggage.set_baggage(BAGGAGE_AGENT_NAME, caller.agent_name, context=ctx)
    return context.attach(ctx)


def detach_baggage(token: object | None) -> None:
    """Restores the context saved by attach_baggage.

    A streaming response can be closed from a different asyncio task than the one that started
    it; the context is then already gone, and the detach is skipped.
    """
    if token is None:
        return
    from opentelemetry import context

    try:
        context.detach(token)
    except ValueError as exc:
        logger.debug("Baggage detach skipped after a context switch: %s", exc)
