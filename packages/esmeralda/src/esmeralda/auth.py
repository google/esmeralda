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

"""Service-to-service authentication: the right bearer token for each host, cached.

Calls through the gateway entry point (MCP servers, other agents) carry a bearer token of the kind
the host's backend checks. The entry point routes each host either to Cloud Run (an MCP server, or
a bring-your-own gateway adapter), which checks a Google-signed **ID token** whose audience is the
URL's origin, or to Agent Runtime, which checks an OAuth2 **access token** (it rejects ID tokens).
``token_kind(url)`` picks the kind: ``access`` for hosts listed in ``ACCESS_TOKEN_HOSTS`` and for
``*.googleapis.com``, ``id`` otherwise; ``bearer_token(url)`` mints it.

With ``SERVICE_ACCOUNT_EMAIL`` set, tokens are minted for that service account through IAM
impersonation (deprecated opt-in); otherwise for the runtime identity: on Agent Runtime that is the
agent's own Agent Identity (metadata server), elsewhere ADC.

Agent Identity ID tokens and the Agent Gateway: since google-auth 2.61, ID tokens minted on a
runtime with a workload certificate (Agent Runtime) are certificate-bound by default
(``cnf.x5t#S256``). Cloud Run checks that binding against the TLS client certificate it sees, and a
bound token only works over a direct mTLS connection (``*.mtls.run.app``). Calls to private hosts
cross the Agent Gateway and the internal load balancer, which terminate TLS, so Cloud Run rejects a
bound token (401). ID tokens are therefore requested unbound, per call, with the documented opt-out
``fetch_id_token_credentials(audience, bind_id_token=False)``. Access tokens keep the library
default (bound), so calls to Google APIs keep their binding protection; the container-wide
``GOOGLE_API_ENABLE_RUNTIME_BOUND_TOKEN=false`` is never used, because it unbinds those too.
Unbound Agent Identity ID tokens are bearer tokens (valid for hours): they only ever travel to
private hosts behind the gateway, which also authorizes the caller's mTLS identity on every hop.

Tokens are cached (ID tokens per audience) and refreshed shortly before they expire (google-auth
credentials handle the expiry), so a tool call or an A2A request doesn't pay one or two IAM round
trips.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import threading
import urllib.parse
from typing import Any, Literal

logger = logging.getLogger(__name__)

CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
ACCESS_TOKEN_HOSTS_ENV = "ACCESS_TOKEN_HOSTS"
TOKEN_KINDS = ("id", "access")

TokenKind = Literal["id", "access"]

_credentials: dict[tuple[str, str], Any] = {}
_access_credentials: dict[str, Any] = {}
_lock = threading.Lock()


def audience_for(url: str) -> str:
    """The ID-token audience for a URL: its origin (``scheme://host[:port]``)."""
    parts = urllib.parse.urlsplit(url)
    if not parts.scheme or not parts.netloc:
        raise ValueError(f"Not an absolute URL: {url!r}")
    return f"{parts.scheme}://{parts.netloc}"


def is_local(url: str) -> bool:
    """Local servers (workstation runs) take no ID token: ``localhost`` and ``*.localhost``, and loopback
    or unspecified addresses (``esmeralda serve`` prints ``http://0.0.0.0:8080``)."""
    host = (urllib.parse.urlsplit(url).hostname or "").rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def _unbound_id_token_credentials(audience: str, request: Any) -> Any:
    """ID token credentials for the runtime identity, never certificate-bound (see the module docstring).

    ``bind_id_token`` exists since google-auth 2.61, the release that started binding ID tokens;
    older releases don't take it and never bind.
    """
    import inspect

    from google.oauth2 import id_token

    if "bind_id_token" in inspect.signature(id_token.fetch_id_token_credentials).parameters:
        return id_token.fetch_id_token_credentials(audience, request=request, bind_id_token=False)
    return id_token.fetch_id_token_credentials(audience, request=request)


def _new_credentials(audience: str, service_account: str) -> Any:
    import google.auth
    from google.auth import impersonated_credentials
    from google.auth.transport.requests import Request

    if service_account:
        source, _ = google.auth.default(scopes=[CLOUD_SCOPE])
        target = impersonated_credentials.Credentials(
            source_credentials=source, target_principal=service_account, target_scopes=[CLOUD_SCOPE]
        )
        return impersonated_credentials.IDTokenCredentials(
            target_credentials=target, target_audience=audience, include_email=True
        )
    return _unbound_id_token_credentials(audience, Request())


def id_token(audience: str) -> str:
    """An ID token for ``audience`` (cached; refreshed before expiry). Raises if none can be minted.

    If impersonating ``SERVICE_ACCOUNT_EMAIL`` fails, falls back to the runtime identity (as the
    agents did before this library) and logs a warning.
    """
    from google.auth.transport.requests import Request

    service_account = os.environ.get("SERVICE_ACCOUNT_EMAIL", "")
    key = (audience, service_account)
    with _lock:
        credentials = _credentials.get(key)
        if credentials is None:
            credentials = _credentials[key] = _new_credentials(audience, service_account)
        if not credentials.valid:
            try:
                credentials.refresh(Request())
            except Exception as exc:
                if not service_account:
                    raise
                logger.warning(
                    "ID token via impersonation of %s failed (%s); using the runtime identity.", service_account, exc
                )
                credentials = _credentials[key] = _new_credentials(audience, "")
                credentials.refresh(Request())
        return credentials.token


async def id_token_async(audience: str) -> str:
    """``id_token`` off the event loop (minting may do blocking network calls)."""
    return await asyncio.to_thread(id_token, audience)


def _new_access_credentials(service_account: str) -> Any:
    import google.auth
    from google.auth import impersonated_credentials

    source, _ = google.auth.default(scopes=[CLOUD_SCOPE])
    if service_account:
        return impersonated_credentials.Credentials(
            source_credentials=source, target_principal=service_account, target_scopes=[CLOUD_SCOPE]
        )
    return source


def access_token() -> str:
    """An OAuth2 access token (scope ``cloud-platform``; cached, refreshed before expiry).

    Minted for ``SERVICE_ACCOUNT_EMAIL`` through impersonation when set; if that fails, falls back
    to the runtime identity and logs a warning. Raises if no token can be minted.
    """
    from google.auth.transport.requests import Request

    service_account = os.environ.get("SERVICE_ACCOUNT_EMAIL", "")
    with _lock:
        credentials = _access_credentials.get(service_account)
        if credentials is None:
            credentials = _access_credentials[service_account] = _new_access_credentials(service_account)
        if not credentials.valid:
            try:
                credentials.refresh(Request())
            except Exception as exc:
                if not service_account:
                    raise
                logger.warning(
                    "Access token via impersonation of %s failed (%s); using the runtime identity.",
                    service_account,
                    exc,
                )
                credentials = _access_credentials[service_account] = _new_access_credentials("")
                credentials.refresh(Request())
        return credentials.token


async def access_token_async() -> str:
    """``access_token`` off the event loop (minting may do blocking network calls)."""
    return await asyncio.to_thread(access_token)


def _access_token_hosts() -> set[str]:
    raw = os.environ.get(ACCESS_TOKEN_HOSTS_ENV, "")
    return {host.strip().lower().rstrip(".") for host in raw.split(",") if host.strip()}


def token_kind(url: str) -> TokenKind:
    """The bearer token kind the host of ``url`` takes: ``"access"`` or ``"id"``.

    ``"access"`` (OAuth2 access token) for hosts listed in ``ACCESS_TOKEN_HOSTS`` (comma-separated
    hostnames, read at call time; agents on Agent Runtime routed straight through the entry point)
    and for ``*.googleapis.com``; ``"id"`` (ID token, audience = the URL's origin) otherwise.
    """
    host = (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")
    if host and (host in _access_token_hosts() or host.endswith(".googleapis.com")):
        return "access"
    return "id"


def _resolve_kind(url: str, kind: str | None) -> str:
    kind = kind or token_kind(url)
    if kind not in TOKEN_KINDS:
        raise ValueError(f"Unknown token kind {kind!r}; expected one of {TOKEN_KINDS}")
    return kind


def bearer_token(url: str, kind: str | None = None) -> str:
    """The bearer token for a call to ``url``: an ID token for its origin or an access token.

    ``kind`` (``"id"`` or ``"access"``) overrides ``token_kind(url)``.
    """
    if _resolve_kind(url, kind) == "access":
        return access_token()
    return id_token(audience_for(url))


async def bearer_token_async(url: str, kind: str | None = None) -> str:
    """``bearer_token`` off the event loop."""
    if _resolve_kind(url, kind) == "access":
        return await access_token_async()
    return await id_token_async(audience_for(url))


def clear_cache() -> None:
    with _lock:
        _credentials.clear()
        _access_credentials.clear()
