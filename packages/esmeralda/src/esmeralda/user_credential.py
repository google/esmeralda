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

"""Delegated user credential: which targets may receive it, in which header, and redaction.

The end user's credential (a token from Gemini Enterprise ``temp:<USER_AUTH_ID>`` state, from the
A2A ``user_auth_token`` metadata of a calling agent, or brokered by Agent Identity Auth Manager)
is a secret. Rules:

* **Opt-in forwarding.** It is sent only to the hosts listed in
  ``$ESMERALDA_USER_CREDENTIAL_TARGETS``, a JSON object ``{"<host>[:<port>]": "<header>"}``.
  The value is the HTTP header the target reads (``null``, ``true`` or ``""`` mean
  ``X-User-Token``; ``false`` excludes the host). A host that isn't listed never receives it, and
  a malformed variable lists nothing (fail closed). Wildcards are not supported.
* **Never in telemetry.** It never goes into OpenTelemetry baggage, span attributes, log records,
  usage records, exception messages or persisted session state (only ``temp:`` state).
  ``redact`` masks it wherever a value has to be logged.

Mapping from the catalog (generated in a later phase): every target whose entry has
``user_credential: {accept: true, header: <name>}`` becomes ``"<endpoint host>": "<name>"``
(``header`` omitted means ``X-User-Token``); every other target is left out.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

TARGETS_ENV = "ESMERALDA_USER_CREDENTIAL_TARGETS"
DEFAULT_HEADER = "X-User-Token"
REDACTED = "[REDACTED]"

# Headers the credential may never be put in: they carry the caller's own auth, routing or tracing.
_RESERVED_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "host",
        "content-type",
        "content-length",
        "accept",
        "x-api-key",
        "x-agent-spiffe-id",
        "traceparent",
        "tracestate",
        "baggage",
    }
)
_HEADER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")

# Keys whose values are always masked by ``redact`` (compared case-insensitively).
SENSITIVE_KEYS = frozenset(
    {
        "user_auth_token",
        "temp:user_auth_token",
        "user_token",
        "user-auth-token",
        "x-user-token",
        "authorization",
        "proxy-authorization",
        "cookie",
    }
)


@dataclass(frozen=True)
class Targets:
    """The hosts that accept the user credential, each with the header it reads."""

    headers: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: str | Mapping[str, Any] | None) -> Targets:
        """Parses the ``$ESMERALDA_USER_CREDENTIAL_TARGETS`` JSON (or an already-decoded mapping)."""
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return cls()
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                logger.warning("%s is not valid JSON; no target receives the user credential", TARGETS_ENV)
                return cls()
        if not isinstance(raw, Mapping):
            logger.warning("%s must be a JSON object; no target receives the user credential", TARGETS_ENV)
            return cls()

        headers: dict[str, str] = {}
        for key, value in raw.items():
            host = _normalize_host(key)
            if not host:
                logger.warning("%s: ignoring invalid host entry %r", TARGETS_ENV, key)
                continue
            if value is False:
                continue
            header = DEFAULT_HEADER if value in (None, True, "") else value
            if not isinstance(header, str) or not _HEADER_RE.match(header) or header.lower() in _RESERVED_HEADERS:
                logger.warning("%s: ignoring %s, its header name is invalid or reserved", TARGETS_ENV, host)
                continue
            headers[host] = header
        return cls(headers=headers)

    def header_for(self, url: str) -> str | None:
        """The header to send the credential in to ``url``, or None when its host is not listed."""
        try:
            parts = urllib.parse.urlsplit(url)
            host = (parts.hostname or "").rstrip(".").lower()
            port = parts.port
        except ValueError:
            return None
        if not host:
            return None
        if port is not None and f"{host}:{port}" in self.headers:
            return self.headers[f"{host}:{port}"]
        return self.headers.get(host)


def _normalize_host(key: Any) -> str | None:
    if not isinstance(key, str) or not key.strip():
        return None
    key = key.strip().lower()
    if "*" in key:
        return None
    if "://" in key:
        try:
            parts = urllib.parse.urlsplit(key)
            host, port = (parts.hostname or "").rstrip("."), parts.port
        except ValueError:
            return None
        return (f"{host}:{port}" if port else host) or None
    if "/" in key or "@" in key or any(c.isspace() for c in key):
        return None
    return key.rstrip(".")


_cache: tuple[str | None, Targets] | None = None


def targets() -> Targets:
    """The configured targets (from ``$ESMERALDA_USER_CREDENTIAL_TARGETS``, re-read when it changes)."""
    global _cache
    raw = os.environ.get(TARGETS_ENV)
    if _cache is None or _cache[0] != raw:
        _cache = (raw, Targets.parse(raw))
    return _cache[1]


def header_for(url: str) -> str | None:
    """The header ``url``'s host reads the credential from, or None when the host is not listed."""
    return targets().header_for(url)


def accepts(url: str) -> bool:
    """True when ``url``'s host is configured to receive the user credential."""
    return header_for(url) is not None


def host_of(url: str) -> str:
    """The host of ``url``, safe to log."""
    try:
        return urllib.parse.urlsplit(url).hostname or "?"
    except ValueError:
        return "?"


def outgoing_headers(url: str, ctx: Any = None, *, auth_provider: str | None = None) -> dict[str, str]:
    """The credential header for one request to ``url``: ``{}`` unless the host is listed.

    The credential comes from ``esmeralda.auth_manager.resolve_user_token``: Auth Manager (3LO)
    when configured, else the invocation's own token. Auth Manager is not called for a host that
    isn't listed. A failure is logged without the value and the request goes out without it.
    """
    from esmeralda import auth_manager

    header = header_for(url)
    if header is None:
        if auth_provider or os.environ.get(auth_manager.AUTH_PROVIDER_ENV):
            logger.debug(
                "Auth Manager is configured but %s is not in %s; not sending a user credential",
                host_of(url),
                TARGETS_ENV,
            )
        return {}
    try:
        token = auth_manager.resolve_user_token(ctx, auth_provider=auth_provider)
    except Exception as exc:  # never log the message: it is not ours to vouch for
        logger.warning("No user credential for %s (%s); calling without one", host_of(url), type(exc).__name__)
        return {}
    if not token:
        return {}
    # Value-free audit line: which host got the credential, in which header. Never the value.
    logger.info("User credential attached for %s (%s header)", host_of(url), header)
    return {header: token}


# ---- redaction ------------------------------------------------------------------------------


def redact(value: Any, secrets: Iterable[str | None] = ()) -> Any:
    """A copy of ``value`` with every secret string, and every sensitive key's value, masked.

    Strings have each of ``secrets`` replaced by ``[REDACTED]``. Mappings have the values of
    ``SENSITIVE_KEYS``, of the configured credential headers and of the ``temp:<USER_AUTH_ID>``
    key masked, and are redacted recursively, as are lists and tuples.
    """
    needles = sorted({s for s in secrets if isinstance(s, str) and s}, key=len, reverse=True)
    sensitive = _sensitive_keys()
    return _redact(value, needles, sensitive)


def _sensitive_keys() -> frozenset[str]:
    from esmeralda import context

    keys = set(SENSITIVE_KEYS)
    keys.add(context.user_token_state_key().lower())
    keys.update(h.lower() for h in targets().headers.values())
    return frozenset(keys)


def _redact(value: Any, needles: list[str], sensitive: frozenset[str]) -> Any:
    if isinstance(value, str):
        for needle in needles:
            value = value.replace(needle, REDACTED)
        return value
    if isinstance(value, Mapping):
        return {
            k: (REDACTED if isinstance(k, str) and k.lower() in sensitive else _redact(v, needles, sensitive))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return type(value)(_redact(v, needles, sensitive) for v in value)
    return value


def describe_error(exc: BaseException, secrets: Iterable[str | None] = ()) -> str:
    """``"<Type>: <message>"`` for ``exc``, with ``secrets`` masked; safe to log or raise."""
    return f"{type(exc).__name__}: {redact(str(exc), secrets)}"
