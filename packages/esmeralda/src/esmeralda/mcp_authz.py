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

"""Zero-latency end-user and agent delegation claim guard for MCP tool servers.

Verifies the delegated user credential header (``X-User-Token`` by default, configurable with
``$USER_CREDENTIAL_HEADER``; the legacy ``User-Auth-Token`` is still accepted) and
``X-Agent-Spiffe-Id`` locally without making
synchronous outbound calls to ``oauth2.googleapis.com/tokeninfo`` (which fail inside
VPC Service Controls perimeters) and without hardcoding fictional user allowlists:

* Rejects workload service account tokens (``*.gserviceaccount.com``) presented as
  the user credential so an agent's own service account cannot masquerade as an end user.
* Decodes JWT payload claims locally (or accepts opaque OAuth2 access tokens /
  ``AUTHZ_MOCK_USER`` when configured).
* Fails closed when ``MCP_ENFORCE_USER_AUTH=true`` and the user credential is missing,
  expired, or belongs to a service account.
"""

from __future__ import annotations

import base64
import json
import os
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

USER_TOKEN_HEADER = "X-User-Token"
"""Default header of the delegated user credential (``esmeralda.user_credential.DEFAULT_HEADER``)."""
LEGACY_USER_TOKEN_HEADER = "User-Auth-Token"
"""Header used before the credential became opt-in per target; still accepted."""
USER_TOKEN_HEADER_ENV = "USER_CREDENTIAL_HEADER"
"""Server-side override: the header this server was configured (in the catalog) to read."""
SPIFFE_ID_HEADER = "X-Agent-Spiffe-Id"
ENFORCE_ENV = "MCP_ENFORCE_USER_AUTH"
MOCK_USER_ENV = "AUTHZ_MOCK_USER"
ALLOWED_DOMAINS_ENV = "MCP_ALLOWED_USER_DOMAINS"
SPIFFE_PREFIXES = ("spiffe://", "principal://agents.global.")


class AuthorizationError(PermissionError):
    """Raised when an MCP tool request fails end-user or agent delegation verification."""

    def __init__(self, reason: str, *, status_code: int = 403) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


@dataclass(frozen=True)
class VerifiedCaller:
    """Verified end-user and agent delegation context extracted from MCP request headers."""

    user_identity: str | None = None
    email: str | None = None
    agent_spiffe_id: str | None = None
    claims: Mapping[str, Any] = field(default_factory=dict)
    enforced: bool = False


def is_service_account_email(value: str | None) -> bool:
    """True if ``value`` is a Google Cloud service account identifier or email."""
    if not isinstance(value, str):
        return False
    cleaned = value.strip().lower()
    if cleaned.startswith("serviceaccount:"):
        cleaned = cleaned.removeprefix("serviceaccount:").strip()
    return cleaned.endswith(".gserviceaccount.com") or cleaned.endswith("@gserviceaccount.com")


def decode_jwt_claims(token: str) -> dict[str, Any] | None:
    """Decodes the JSON payload of a 3-part JWT without network I/O; returns None for opaque tokens."""
    parts = token.strip().split(".")
    if len(parts) != 3 or not parts[0] or not parts[1]:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _header(headers: Mapping[str, str] | None, name: str) -> str:
    if not headers:
        return ""
    target = name.lower()
    for k, v in headers.items():
        if k.lower() == target and isinstance(v, str):
            return v.strip()
    return ""


def _is_truthy(val: str | None) -> bool:
    return (val or "").strip().lower() in ("1", "true", "yes", "on")


def user_token_headers() -> list[str]:
    """The headers the user credential is read from, in order: ``$USER_CREDENTIAL_HEADER`` (else
    ``X-User-Token``), then the legacy ``User-Auth-Token``."""
    configured = os.environ.get(USER_TOKEN_HEADER_ENV, "").strip() or USER_TOKEN_HEADER
    return list(dict.fromkeys([configured, USER_TOKEN_HEADER, LEGACY_USER_TOKEN_HEADER]))


def verify_request(
    headers: Mapping[str, str] | None = None,
    *,
    enforce: bool | None = None,
    allowed_domains: Iterable[str] | None = None,
) -> VerifiedCaller:
    """Verifies the user credential header (see ``user_token_headers``) and ``X-Agent-Spiffe-Id``.

    Error messages never contain the credential itself.
    """
    is_enforced = enforce if enforce is not None else _is_truthy(os.environ.get(ENFORCE_ENV))
    spiffe = _header(headers, SPIFFE_ID_HEADER) or None
    if spiffe and not spiffe.startswith(SPIFFE_PREFIXES):
        raise AuthorizationError(f"Invalid {SPIFFE_ID_HEADER}: '{spiffe}'", status_code=403)

    raw_token = ""
    for name in user_token_headers():
        raw_token = _header(headers, name)
        if raw_token:
            break
    if raw_token.lower().startswith("bearer "):
        raw_token = raw_token[7:].strip()

    if not raw_token:
        mock_user = os.environ.get(MOCK_USER_ENV, "").strip()
        if mock_user and not is_enforced:
            if is_service_account_email(mock_user):
                raise AuthorizationError("Service account cannot be used as AUTHZ_MOCK_USER", status_code=403)
            return VerifiedCaller(
                user_identity=mock_user,
                email=mock_user if "@" in mock_user else None,
                agent_spiffe_id=spiffe,
                claims={"sub": mock_user},
                enforced=False,
            )
        if is_enforced:
            raise AuthorizationError(
                f"Missing required user credential header ({user_token_headers()[0]})", status_code=401
            )
        return VerifiedCaller(agent_spiffe_id=spiffe, enforced=False)

    if is_service_account_email(raw_token):
        raise AuthorizationError(
            "Service account identities (*.gserviceaccount.com) cannot be used as User-Auth-Token",
            status_code=403,
        )

    claims = decode_jwt_claims(raw_token)
    if claims is None:
        return VerifiedCaller(
            user_identity="oauth2:access_token",
            agent_spiffe_id=spiffe,
            claims={},
            enforced=is_enforced,
        )

    email = str(claims.get("email") or claims.get("upn") or "").strip() or None
    sub = str(claims.get("sub") or email or "").strip() or None
    if is_service_account_email(email) or is_service_account_email(sub):
        raise AuthorizationError(
            "Service account tokens (*.gserviceaccount.com) cannot be used as User-Auth-Token",
            status_code=403,
        )

    exp = claims.get("exp")
    if isinstance(exp, int | float) and exp < time.time():
        raise AuthorizationError("User-Auth-Token JWT has expired", status_code=401)

    domains = (
        [d.strip().lower().lstrip("@") for d in allowed_domains if d.strip()]
        if allowed_domains is not None
        else [
            d.strip().lower().lstrip("@")
            for d in os.environ.get(ALLOWED_DOMAINS_ENV, "").split(",")
            if d.strip()
        ]
    )
    if domains:
        user_domain = (email or "").rpartition("@")[2].lower()
        if not user_domain or user_domain not in domains:
            raise AuthorizationError(
                f"User email domain '{user_domain or 'unknown'}' is not in allowed domains",
                status_code=403,
            )

    if is_enforced and not (sub or email):
        raise AuthorizationError("User-Auth-Token JWT is missing 'sub' and 'email' claims", status_code=401)

    return VerifiedCaller(
        user_identity=sub or email,
        email=email,
        agent_spiffe_id=spiffe,
        claims=claims,
        enforced=is_enforced,
    )
