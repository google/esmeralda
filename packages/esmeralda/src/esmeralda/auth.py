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

"""Service-to-service authentication: Google-signed ID tokens, cached per audience.

Calls through the gateway (MCP servers, other agents behind Kong) carry an OIDC ID token for the
target's audience. With ``SERVICE_ACCOUNT_EMAIL`` set, the token is minted for that service account
through IAM impersonation; otherwise for the runtime identity (metadata server or ADC).

Tokens are cached per audience and refreshed shortly before they expire (google-auth credentials
handle the expiry), so a tool call or an A2A request doesn't pay one or two IAM round trips.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import urllib.parse
from typing import Any

logger = logging.getLogger(__name__)

CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

_credentials: dict[tuple[str, str], Any] = {}
_lock = threading.Lock()


def audience_for(url: str) -> str:
    """The ID-token audience for a URL: its origin (``scheme://host[:port]``)."""
    parts = urllib.parse.urlsplit(url)
    if not parts.scheme or not parts.netloc:
        raise ValueError(f"Not an absolute URL: {url!r}")
    return f"{parts.scheme}://{parts.netloc}"


def is_local(url: str) -> bool:
    """Local servers (workstation runs) take no ID token."""
    host = urllib.parse.urlsplit(url).hostname or ""
    return host in ("localhost", "127.0.0.1", "::1")


def _new_credentials(audience: str, service_account: str) -> Any:
    import google.auth
    from google.auth import impersonated_credentials
    from google.auth.transport.requests import Request
    from google.oauth2 import id_token

    if service_account:
        source, _ = google.auth.default(scopes=[CLOUD_SCOPE])
        target = impersonated_credentials.Credentials(
            source_credentials=source, target_principal=service_account, target_scopes=[CLOUD_SCOPE]
        )
        return impersonated_credentials.IDTokenCredentials(
            target_credentials=target, target_audience=audience, include_email=True
        )
    return id_token.fetch_id_token_credentials(audience, request=Request())


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


def clear_cache() -> None:
    with _lock:
        _credentials.clear()
