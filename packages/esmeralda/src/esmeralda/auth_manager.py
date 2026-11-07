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

"""Agent Identity Auth Manager client and Three-Identity Delegation.

In Gemini Enterprise Agent Platform (GEAP), Agent Identity Auth Manager
(``agentidentitycredentials.googleapis.com``) brokers outbound credentials under the
Three-Identity Delegation Framework:

1. **User identity**: The initiating end user authenticated via OAuth 2.0 / OIDC.
2. **Agent SPIFFE identity**: The reasoning engine's cryptographically attested workload
   identity (``principal://agents.global.org-<ORG_ID>.system.id.goog/...`` or ``spiffe://...``).
3. **Delegated identity**: A scoped 3-legged OAuth (3LO) user-delegated token, 2-legged OAuth
   (2LO) token, or API key credential acquired from an Auth Manager provider and audited
   against both the user and the calling agent's SPIFFE ID.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from esmeralda import auth, context

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://agentidentitycredentials.googleapis.com/v1alpha"
AUTH_PROVIDER_ENV = "AGENT_AUTH_PROVIDER"
AUTH_MANAGER_URL_ENV = "AGENT_AUTH_MANAGER_URL"
SPIFFE_ID_ENV = "AGENT_SPIFFE_ID"
WORKLOAD_CERT_ENV = "AGENT_WORKLOAD_CERT_PATH"
SPIFFE_ID_HEADER = "X-Agent-Spiffe-Id"

_SPIFFE_URI_RE = re.compile(
    r"(spiffe://[^\s\"'<>]+|principal://agents\.global\.[^\s\"'<>]+)"
)


@dataclass(frozen=True)
class DelegatedContext:
    """Three-Identity Delegation tuple: User identity + Agent SPIFFE identity = Delegated identity."""

    user_identity: str | None = None
    agent_spiffe_id: str | None = None
    auth_provider: str | None = None
    scopes: tuple[str, ...] = ()

    @property
    def is_delegated(self) -> bool:
        """True when both an initiating user identity and an agent SPIFFE ID are present."""
        return bool(self.user_identity and self.agent_spiffe_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_identity": self.user_identity,
            "agent_spiffe_id": self.agent_spiffe_id,
            "auth_provider": self.auth_provider,
            "scopes": list(self.scopes),
        }


def spiffe_id() -> str | None:
    """Returns the agent's runtime SPIFFE / principal ID if configured or present in workload certs."""
    env_val = os.environ.get(SPIFFE_ID_ENV, "").strip()
    if env_val:
        return env_val

    cert_path = os.environ.get(WORKLOAD_CERT_ENV, "").strip()
    if cert_path:
        try:
            raw = Path(cert_path).read_text(encoding="utf-8", errors="ignore")
            match = _SPIFFE_URI_RE.search(raw)
            if match:
                return match.group(1)
        except OSError:
            logger.debug("Could not read workload certificate at %s", cert_path)
    return None


def _extract_user_id(ctx: Any) -> str | None:
    if ctx is None:
        return None
    for attr in ("user_id",):
        val = getattr(ctx, attr, None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    session = getattr(ctx, "session", None)
    if session is not None:
        val = getattr(session, "user_id", None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    state = getattr(ctx, "state", None) or getattr(session, "state", None)
    if isinstance(state, Mapping):
        val = state.get("user_id") or state.get("temp:user_id")
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _extract_session_id(ctx: Any) -> str | None:
    if ctx is None:
        return None
    session = getattr(ctx, "session", None)
    if session is not None:
        val = getattr(session, "id", None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    val = getattr(ctx, "session_id", None)
    if isinstance(val, str) and val.strip():
        return val.strip()
    return None


def delegated_context(
    ctx: Any = None,
    *,
    auth_provider: str | None = None,
    scopes: Iterable[str] = (),
) -> DelegatedContext:
    """Builds the ``DelegatedContext`` for an ADK invocation or tool context."""
    provider = (auth_provider or os.environ.get(AUTH_PROVIDER_ENV) or "").strip() or None
    return DelegatedContext(
        user_identity=_extract_user_id(ctx),
        agent_spiffe_id=spiffe_id(),
        auth_provider=provider,
        scopes=tuple(scopes),
    )


def http_client(**kwargs: Any) -> Any:
    """The httpx client used for Auth Manager calls (tests swap in an in-memory transport)."""
    import httpx

    return httpx.Client(**kwargs)


def _runtime_access_token() -> str:
    """OAuth access token for the agent's runtime identity to authenticate to Auth Manager."""
    import google.auth
    import google.auth.transport.requests

    credentials, _ = google.auth.default(scopes=[auth.CLOUD_SCOPE])
    if not credentials.valid:
        credentials.refresh(google.auth.transport.requests.Request())
    if not credentials.token:
        raise RuntimeError("No access token available from runtime credentials")
    return str(credentials.token)


def acquire_token(
    auth_provider: str,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    scopes: Iterable[str] = (),
    base_url: str | None = None,
    timeout: float = 10.0,
) -> str:
    """Acquires a delegated 3LO/2LO token or API key from Agent Identity Auth Manager."""
    provider = auth_provider.strip().lstrip("/")
    if not provider:
        raise ValueError("auth_provider must be a non-empty resource name or provider ID")

    root = (base_url or os.environ.get(AUTH_MANAGER_URL_ENV) or DEFAULT_BASE_URL).rstrip("/")
    url = f"{root}/{provider}:acquireToken"

    headers: dict[str, str] = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_runtime_access_token()}",
    }
    agent_id = spiffe_id()
    if agent_id:
        headers[SPIFFE_ID_HEADER] = agent_id

    payload: dict[str, Any] = {}
    if user_id:
        payload["userId"] = user_id
    if session_id:
        payload["sessionId"] = session_id
    scope_list = [s for s in scopes if s]
    if scope_list:
        payload["scopes"] = scope_list
    if agent_id:
        payload["agentSpiffeId"] = agent_id

    with http_client(timeout=timeout) as client:
        response = client.post(url, json=payload, headers=headers)
        if response.status_code != 200:
            raise RuntimeError(
                f"Auth Manager acquireToken failed with HTTP {response.status_code} from {url}: "
                f"{response.text[:500]}"
            )
        data = response.json()

    if not isinstance(data, Mapping):
        raise RuntimeError(f"Unexpected Auth Manager response payload from {url}")
    token = data.get("accessToken") or data.get("token") or data.get("apiKey")
    if not isinstance(token, str) or not token.strip():
        raise RuntimeError(f"Auth Manager response from {url} did not contain an accessToken")
    return token.strip()


def resolve_user_token(
    ctx: Any = None,
    *,
    auth_provider: str | None = None,
    scopes: Iterable[str] = (),
) -> str | None:
    """Resolves the end-user or delegated credential for the current invocation.

    1. First checks if the invocation already carries a token via ``context.user_token(ctx)``
       (from Gemini Enterprise ``temp:<USER_AUTH_ID>`` session state or A2A ``user_auth_token``).
    2. If ``auth_provider`` (or ``$AGENT_AUTH_PROVIDER``) is configured, brokers a 3LO delegated
       token (or 2LO/API-key credential) from the Agent Identity Credentials API, authenticating
       with the agent's runtime identity and passing the initiating user's subject/session context.
    """
    if ctx is not None:
        existing = context.user_token(ctx)
        if existing:
            return existing

    provider = (auth_provider or os.environ.get(AUTH_PROVIDER_ENV) or "").strip()
    if not provider:
        return None

    return acquire_token(
        provider,
        user_id=_extract_user_id(ctx),
        session_id=_extract_session_id(ctx),
        scopes=scopes,
    )
