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

"""Identity: the IAP JWT, verified on every request.

Behind Identity-Aware Proxy, each request carries ``x-goog-iap-jwt-assertion``: an ES256 JWT signed
by IAP. The BFF verifies the signature against IAP's public keys, the issuer, the audience (the
backend service or Cloud Run service IAP protects, from configuration), and the expiry, then reads
the user's email from it. A missing or invalid assertion is a 401: there is no default identity and
no header that can stand in for the JWT (``x-goog-authenticated-user-email`` is ignored).

``local`` mode (``esmeralda ui --local``, loopback only) uses a fixed development identity instead,
reported as ``auth = local-dev`` so the web labels it.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from google.auth import exceptions as google_auth_exceptions
from google.auth import jwt as google_jwt

from esmeralda_ui.settings import LOCAL_IDENTITY_EMAIL, LOCAL_IDENTITY_SUBJECT, Settings

log = logging.getLogger(__name__)

IAP_HEADER = "x-goog-iap-jwt-assertion"
IAP_ISSUER = "https://cloud.google.com/iap"
IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"
IAP_ALGORITHM = "ES256"
CLOCK_SKEW_SECONDS = 30
MAX_ASSERTION_BYTES = 8192
KEYS_TTL_SECONDS = 3600

KeyProvider = Callable[[], Mapping[str, str]]


class AuthError(Exception):
    """The request has no valid identity (always answered with a generic 401)."""


@dataclass(frozen=True)
class Identity:
    email: str
    subject: str
    auth: str  # "iap" | "local-dev"


class IapPublicKeys:
    """IAP's public keys (``kid`` -> PEM), fetched over HTTPS and cached for an hour.

    ``fetch`` returns the parsed key map; it defaults to an HTTP GET of :data:`IAP_CERTS_URL`.
    """

    def __init__(self, fetch: Callable[[], Mapping[str, str]] | None = None, ttl: float = KEYS_TTL_SECONDS):
        self._fetch = fetch or _fetch_iap_keys
        self._ttl = ttl
        self._lock = threading.Lock()
        self._keys: Mapping[str, str] = {}
        self._expires = 0.0

    def __call__(self) -> Mapping[str, str]:
        with self._lock:
            if not self._keys or time.monotonic() >= self._expires:
                keys = self._fetch()
                if not keys:
                    raise AuthError("no IAP public keys")
                self._keys, self._expires = dict(keys), time.monotonic() + self._ttl
            return self._keys


def _fetch_iap_keys() -> Mapping[str, str]:
    from google.auth.transport import requests as google_requests

    response = google_requests.Request()(url=IAP_CERTS_URL, method="GET", timeout=10)
    if response.status != 200:
        raise AuthError(f"fetching IAP public keys failed with HTTP {response.status}")
    data = json.loads(response.data)
    if not isinstance(data, dict):
        raise AuthError("unexpected IAP public key document")
    return {str(k): str(v) for k, v in data.items()}


def _header(token: str) -> dict[str, Any]:
    segment = token.split(".", 1)[0]
    padded = segment + "=" * (-len(segment) % 4)
    try:
        header = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeError) as exc:
        raise AuthError("malformed assertion") from exc
    if not isinstance(header, dict):
        raise AuthError("malformed assertion")
    return header


def verify_iap_jwt(token: str, *, audience: str, keys: KeyProvider) -> Identity:
    """The identity in a valid IAP assertion; raises :class:`AuthError` otherwise."""
    if not token or len(token) > MAX_ASSERTION_BYTES or token.count(".") != 2:
        raise AuthError("malformed assertion")
    header = _header(token)
    if header.get("alg") != IAP_ALGORITHM or not header.get("kid"):
        raise AuthError("unexpected assertion algorithm or key id")
    try:
        claims = google_jwt.decode(
            token, certs=keys(), verify=True, audience=audience, clock_skew_in_seconds=CLOCK_SKEW_SECONDS
        )
    except (ValueError, google_auth_exceptions.GoogleAuthError) as exc:
        raise AuthError("assertion verification failed") from exc
    if claims.get("iss") != IAP_ISSUER:
        raise AuthError("unexpected issuer")
    email, subject = claims.get("email"), claims.get("sub")
    if not isinstance(email, str) or not email or not isinstance(subject, str) or not subject:
        raise AuthError("assertion without email or subject")
    return Identity(email=email, subject=subject, auth="iap")


LOCAL_IDENTITY = Identity(email=LOCAL_IDENTITY_EMAIL, subject=LOCAL_IDENTITY_SUBJECT, auth="local-dev")


class Authenticator:
    """Decides the identity of a request from its headers (IAP JWT in cloud mode)."""

    def __init__(self, settings: Settings, keys: KeyProvider | None = None):
        self._settings = settings
        self._keys = keys or IapPublicKeys()

    def identify(self, headers: Mapping[str, str]) -> Identity:
        if self._settings.local:
            return LOCAL_IDENTITY
        token = headers.get(IAP_HEADER)
        if not token:
            raise AuthError("missing IAP assertion")
        return verify_iap_jwt(token, audience=str(self._settings.iap_audience), keys=self._keys)
