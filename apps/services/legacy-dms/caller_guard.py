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

"""Verified caller attribution: map the platform-verified caller to a catalog caller and team.

The in-process second layer behind the gateway and IAM grants (both compiled from the catalog).
It runs where the platform hands the workload a verified caller identity:

* **Cloud Run with IAM invoker auth** validates the caller's Google ID token before the request
  reaches the container (``X-Serverless-Authorization`` when sent, else ``Authorization``), and
  ingress is internal. The Terraform unit that sets the allowlist knows that and says so with
  ``"token_validated_by": "cloud_run_iam"``: the payload of that header (``email`` / ``sub``) is
  then the verified caller, whether or not the platform stripped the signature (behind the internal
  load balancer the token arrives signed). Trust assumption: the unit sets it only for a service
  with internal ingress and IAM invoker auth required (no ``allUsers``). The first identity header
  present decides: when ``X-Serverless-Authorization`` is present, ``Authorization`` (which Cloud
  Run passes through unvalidated) is never read, even if the former is empty or unparseable.
  Without that setting only a token whose signature the platform replaced with
  ``SIGNATURE_REMOVED_BY_GOOGLE`` counts: nothing else proves the token was validated.
* **Agent Runtime** never forwards the caller's credential to the container, so the guard can't
  run there: those hops are attributed from the Agent Gateway record only.

The allowlist (``ESMERALDA_CALLER_ALLOWLIST``, JSON) is compiled from the catalog
(``catalog/.resolved/<env>.json`` ``attribution``) and written by the workload's Terraform unit::

    {"policy": {"unmapped_caller": "deny", "declared_mismatch": "reject"},
     "token_validated_by": "cloud_run_iam",
     "callers": [{"principal": "sa@...", "caller": "test-vm", "team": "platform",
                  "cost_centers": ["cc-platform"]}, ...]}

Per request (:func:`decide`):

* no verified identity (header missing, malformed, or not validated by the platform): the caller is
  unverified: ``401`` under ``unmapped_caller: deny``, allowed and unattributed under ``flag``.
  Self-asserted headers (``X-Agent-Spiffe-Id``, ``X-API-Key``, caller context) are never used.
* a verified principal missing from the allowlist: ``403`` under ``deny``; allowed, unattributed
  and logged under ``flag``.
* a mapped principal declaring a cost centre (``X-Esmeralda-Cost-Center``) its catalog grant
  doesn't allow: ``403`` under ``declared_mismatch: reject``; under ``bill_own_team`` the call is
  billed to the caller's own team and the record says ``declared_mismatch``.

``caller.verified`` is true only for a mapped principal with a team. This module uses the standard
library only, so services that don't depend on the ``esmeralda`` package can vendor it unchanged
(``apps/services/*/caller_guard.py``, kept byte-identical by a repository test).
"""

from __future__ import annotations

import base64
import contextvars
import json
import logging
import os
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("esmeralda.attribution")

ALLOWLIST_ENV = "ESMERALDA_CALLER_ALLOWLIST"
DECLARED_COST_CENTER_HEADER = "X-Esmeralda-Cost-Center"
BILLED_TEAM_HEADER = "X-Esmeralda-Billed-Team"
STRIPPED_SIGNATURE = "SIGNATURE_REMOVED_BY_GOOGLE"
IDENTITY_HEADERS = ("x-serverless-authorization", "authorization")
CLOUD_RUN_IAM = "cloud_run_iam"
"""``token_validated_by`` value: Cloud Run IAM validated the identity header before the request arrived."""
DEFAULT_EXEMPT_PATHS = ("/health", "/healthz")

DENY, FLAG = "deny", "flag"
REJECT, BILL_OWN_TEAM = "reject", "bill_own_team"

_current: contextvars.ContextVar[Decision | None] = contextvars.ContextVar("esmeralda_caller_decision", default=None)


@dataclass(frozen=True)
class Entry:
    """One allowlisted principal: the catalog caller it belongs to and the team that pays."""

    principal: str
    caller: str | None = None
    team: str | None = None
    cost_centers: tuple[str, ...] = ()
    shared: bool = False


@dataclass(frozen=True)
class Allowlist:
    """The compiled per-target allowlist and the attribution policy."""

    entries: Mapping[str, Entry] = field(default_factory=dict)
    unmapped_caller: str = DENY
    declared_mismatch: str = REJECT
    token_validated_by: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Allowlist:
        policy = data.get("policy") or {}
        unmapped = policy.get("unmapped_caller", DENY)
        mismatch = policy.get("declared_mismatch", REJECT)
        if unmapped not in (DENY, FLAG):
            raise ValueError(f"unmapped_caller must be '{DENY}' or '{FLAG}', got {unmapped!r}")
        if mismatch not in (REJECT, BILL_OWN_TEAM):
            raise ValueError(f"declared_mismatch must be '{REJECT}' or '{BILL_OWN_TEAM}', got {mismatch!r}")
        validated_by = data.get("token_validated_by") or None
        if validated_by not in (None, CLOUD_RUN_IAM):
            raise ValueError(f"token_validated_by must be '{CLOUD_RUN_IAM}' or unset, got {validated_by!r}")
        entries: dict[str, Entry] = {}
        for item in data.get("callers") or []:
            principal = normalize_principal(item.get("principal"))
            if not principal:
                continue
            entries[principal] = Entry(
                principal=principal,
                caller=item.get("caller"),
                team=item.get("team"),
                cost_centers=tuple(item.get("cost_centers") or ()),
                shared=bool(item.get("shared")),
            )
        return cls(entries=entries, unmapped_caller=unmapped, declared_mismatch=mismatch,
                   token_validated_by=validated_by)

    @classmethod
    def from_json(cls, text: str) -> Allowlist:
        return cls.from_mapping(json.loads(text))

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Allowlist | None:
        """The allowlist in ``ESMERALDA_CALLER_ALLOWLIST``; None when unset (guard off).

        An invalid value raises: a misconfigured guard must fail at startup, not open.
        """
        text = (environ if environ is not None else os.environ).get(ALLOWLIST_ENV, "").strip()
        return cls.from_json(text) if text else None


@dataclass(frozen=True)
class Decision:
    """What the guard decided for one request, and the attribution it carries."""

    allowed: bool
    status: int
    reason: str  # mapped | unmapped | unverified | declared_mismatch
    principal: str | None = None
    caller: str | None = None
    team: str | None = None
    cost_center: str | None = None
    declared_cost_center: str | None = None
    declared_mismatch: bool = False

    @property
    def verified(self) -> bool:
        """True only when the principal came from authentication and maps to a team."""
        return self.principal is not None and self.team is not None and self.reason in ("mapped", "declared_mismatch")

    def caller_record(self) -> dict[str, Any]:
        """The ``caller`` object of an ``esmeralda.usage`` record."""
        record: dict[str, Any] = {"verified": self.verified}
        if self.principal:
            record["principal"] = self.principal[:512]
        if self.verified and self.team:
            record["team"] = self.team[:128]
        declared = {}
        if self.declared_cost_center:
            declared["cost_center"] = self.declared_cost_center[:256]
        if declared:
            record["declared"] = declared
        return record

    def log_fields(self) -> dict[str, Any]:
        return {
            "decision": "allow" if self.allowed else "deny",
            "reason": self.reason,
            "attribution": "verified" if self.verified else "unattributed",
            "principal": self.principal,
            "caller": self.caller,
            "team": self.team,
            "cost_center": self.cost_center,
            "declared_cost_center": self.declared_cost_center,
            "declared_mismatch": self.declared_mismatch,
        }


def normalize_principal(value: Any) -> str | None:
    """Canonical principal: e-mails lower-cased; an Agent Identity ``spiffe://`` id in its
    ``principal://`` form (same path), the form the catalog and the gateway records use."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.startswith("spiffe://"):
        text = "principal://" + text[len("spiffe://"):]
    if "@" in text and "://" not in text:
        text = text.lower()
    return text


def _claims(token: str) -> dict[str, Any] | None:
    parts = token.split(".")
    if len(parts) != 3 or not parts[1]:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)).decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def verified_principal(
    headers: Mapping[str, str] | Iterable[tuple[str, str]], *, validated_by: str | None = None
) -> str | None:
    """The caller principal from the Google ID token the platform validated.

    Reads ``X-Serverless-Authorization`` first (Cloud Run validates that one when both are sent),
    then ``Authorization``. With ``validated_by="cloud_run_iam"`` the platform's IAM check already
    validated that header, so its payload counts as is; otherwise only a signature-stripped token
    counts (the platform's proof that it validated it). Returns None when there is no such token or
    it is malformed. ``email`` wins when present and not marked unverified, else ``sub``.
    """
    items = headers.items() if isinstance(headers, Mapping) else headers
    found = {k.lower(): v for k, v in items if isinstance(k, str) and isinstance(v, str)}
    for name in IDENTITY_HEADERS:
        if name not in found:
            continue
        # The first identity header present decides, even when empty or unparseable: Cloud Run
        # validates X-Serverless-Authorization when it is sent and passes Authorization through
        # untouched, so falling back to Authorization would trust an unvalidated token.
        value = found[name].strip()
        if not value:
            return None
        token = value[7:].strip() if value[:7].lower() == "bearer " else value
        if validated_by != CLOUD_RUN_IAM and token.rsplit(".", 1)[-1] != STRIPPED_SIGNATURE:
            return None
        claims = _claims(token)
        if claims is None:
            return None
        email = claims.get("email")
        if isinstance(email, str) and email and claims.get("email_verified", True) is not False:
            return normalize_principal(email)
        return normalize_principal(claims.get("sub"))
    return None


def identity_evidence(headers: Mapping[str, str] | Iterable[tuple[str, str]]) -> dict[str, str]:
    """Why a request is unverified, without any credential: the shape of each identity header.

    ``absent``, ``opaque`` (not a JWT), ``jwt_signed`` (a JWT the platform did not validate),
    ``jwt_stripped`` (validated by the platform) or ``malformed``. Logged with unverified decisions.
    """
    items = headers.items() if isinstance(headers, Mapping) else headers
    found = {k.lower(): v for k, v in items if isinstance(k, str) and isinstance(v, str)}
    out: dict[str, str] = {}
    for name in IDENTITY_HEADERS:
        value = found.get(name, "").strip()
        token = value[7:].strip() if value[:7].lower() == "bearer " else value
        parts = token.split(".") if token else []
        if not token:
            out[name] = "absent"
        elif len(parts) != 3:
            out[name] = "opaque"
        elif _claims(token) is None:
            out[name] = "malformed"
        else:
            out[name] = "jwt_stripped" if parts[2] == STRIPPED_SIGNATURE else "jwt_signed"
    return out


def decide(
    allowlist: Allowlist,
    principal: str | None,
    declared_cost_center: str | None = None,
) -> Decision:
    """Applies the attribution policy to a (verified or missing) principal."""
    declared = (declared_cost_center or "").strip() or None
    flag = allowlist.unmapped_caller == FLAG
    if principal is None:
        return Decision(allowed=flag, status=200 if flag else 401, reason="unverified",
                        declared_cost_center=declared)
    entry = allowlist.entries.get(principal)
    if entry is None:
        return Decision(allowed=flag, status=200 if flag else 403, reason="unmapped", principal=principal,
                        declared_cost_center=declared)
    default_cc = entry.cost_centers[0] if entry.cost_centers else None
    if declared and declared not in entry.cost_centers:
        if allowlist.declared_mismatch == REJECT:
            return Decision(allowed=False, status=403, reason="declared_mismatch", principal=principal,
                            caller=entry.caller, team=entry.team, declared_cost_center=declared,
                            declared_mismatch=True)
        return Decision(allowed=True, status=200, reason="declared_mismatch", principal=principal,
                        caller=entry.caller, team=entry.team, cost_center=default_cc,
                        declared_cost_center=declared, declared_mismatch=True)
    return Decision(allowed=True, status=200, reason="mapped", principal=principal, caller=entry.caller,
                    team=entry.team, cost_center=declared or default_cc, declared_cost_center=declared)


def current() -> Decision | None:
    """The guard's decision for the request being served (None outside a guarded request)."""
    return _current.get()


def _log(decision: Decision, path: str | None, evidence: Mapping[str, str] | None = None) -> None:
    allowed_verified = decision.allowed and decision.verified
    fields: dict[str, Any] = {
        "severity": "INFO" if allowed_verified else "WARNING",
        "event": "esmeralda.caller_decision",
        "path": path,
        **decision.log_fields(),
    }
    if evidence:
        fields["identity_headers"] = dict(evidence)
    line = json.dumps(fields)
    if allowed_verified:
        logger.info(line)
    else:
        logger.warning(line)


def _ensure_decision_logging() -> None:
    """Every decision (INFO included) reaches the logs even when nobody configured logging: then one
    JSON line per decision goes to stdout, which Cloud Logging stores as jsonPayload (with severity)."""
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)
    if not logger.handlers and not logging.getLogger().handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.propagate = False


class CallerGuard:
    """ASGI middleware enforcing the allowlist on every HTTP request but ``exempt_paths``.

    ``allowlist`` defaults to ``Allowlist.from_env()``; with no allowlist configured the guard is a
    pass-through (logged once), so local runs keep working. Denied requests get a JSON body with the
    reason (never the token). Allowed requests carry the decision in ``scope["state"]`` and in
    :func:`current`; when the call is attributed to a team the response says so in
    ``X-Esmeralda-Billed-Team``.
    """

    def __init__(
        self,
        app: Callable[..., Any],
        allowlist: Allowlist | None = None,
        *,
        exempt_paths: Iterable[str] = DEFAULT_EXEMPT_PATHS,
        principal_reader: Callable[[Iterable[tuple[str, str]]], str | None] | None = None,
    ):
        self.app = app
        self.allowlist = allowlist if allowlist is not None else Allowlist.from_env()
        self.exempt_paths = tuple(exempt_paths)
        validated_by = self.allowlist.token_validated_by if self.allowlist is not None else None
        self.principal_reader = principal_reader or (lambda h: verified_principal(h, validated_by=validated_by))
        if self.allowlist is None:
            logger.info("Caller guard off: %s is not set", ALLOWLIST_ENV)
        else:
            _ensure_decision_logging()

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Any], send: Callable[..., Any]) -> None:
        if scope.get("type") != "http" or self.allowlist is None or scope.get("path") in self.exempt_paths:
            await self.app(scope, receive, send)
            return
        headers = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in scope.get("headers", ())]
        declared = next((v for k, v in headers if k.lower() == DECLARED_COST_CENTER_HEADER.lower()), None)
        decision = decide(self.allowlist, self.principal_reader(headers), declared)
        _log(decision, scope.get("path"), identity_evidence(headers) if decision.reason == "unverified" else None)
        if not decision.allowed:
            body = json.dumps({"error": "caller not allowed", "reason": decision.reason}).encode()
            await send({"type": "http.response.start", "status": decision.status,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        scope.setdefault("state", {})["esmeralda.caller"] = decision

        async def send_with_team(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start" and decision.verified and decision.team:
                message = {**message, "headers": [*message.get("headers", []),
                                                  (BILLED_TEAM_HEADER.lower().encode(), decision.team.encode())]}
            await send(message)

        token = _current.set(decision)
        try:
            await self.app(scope, receive, send_with_team)
        finally:
            _current.reset(token)
