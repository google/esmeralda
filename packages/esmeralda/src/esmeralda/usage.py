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

"""Usage records (``esmeralda.usage``): one schema for every producer, framework and cloud.

The contract lives in ``contracts/usage-event.schema.json``. This module produces it from Python,
without depending on any agent framework:

* **Manual API**: ``record_llm_call``, ``record_tool_call``, ``record_agent_invocation`` (module
  functions on a process-wide ``UsageRecorder``, or methods on your own).
* **OpenTelemetry GenAI span adapter**: ``UsageSpanProcessor`` turns finished ``gen_ai.*`` spans
  (``generate_content``/``chat``, ``execute_tool``, ``invoke_agent``) into records, so any
  OTel-instrumented framework (LangGraph, LangChain, CrewAI, custom code) emits them.
* **ADK**: ``EsmeraldaTelemetryPlugin`` (``esmeralda.plugin``) calls the manual API from its callbacks.

Records go to a pluggable ``Transport``: one JSON line on stdout by default (Cloud Logging stores
it as ``jsonPayload`` on Agent Runtime, Cloud Run and GKE), or OpenTelemetry logs
(``ESMERALDA_USAGE_TRANSPORT=otel``) for an OTLP collector. Consumers select records by
``jsonPayload.schema = "esmeralda.usage"``, never by log name.

Every record is redacted before it leaves the process: keys that look like credentials or tokens
are dropped, and values that look like bearer tokens, OAuth access tokens or JWTs are masked. The
delegated user credential never appears in a record.

Telemetry never breaks a request: transport errors are logged and swallowed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, TextIO

logger = logging.getLogger(__name__)

SCHEMA = "esmeralda.usage"
SCHEMA_VERSION = 1

EVENT_LLM_CALL = "llm.call"
EVENT_TOOL_CALL = "tool.call"
EVENT_AGENT_INVOCATION = "agent.invocation"
EVENT_GATEWAY_REQUEST = "gateway.request"
EVENTS = (EVENT_LLM_CALL, EVENT_TOOL_CALL, EVENT_AGENT_INVOCATION, EVENT_GATEWAY_REQUEST)

RUNTIMES = ("agent_engine", "cloud_run", "gke", "external")
CLOUDS = ("gcp", "aws", "azure", "on_prem", "other")

TRANSPORT_ENV = "ESMERALDA_USAGE_TRANSPORT"
DIMENSION_ATTRIBUTE_PREFIX = "esmeralda.dimension."
"""Span attributes with this prefix become ``dimensions`` in records made by ``UsageSpanProcessor``."""

REDACTED = "[REDACTED]"
VALIDATE_ENV = "ESMERALDA_USAGE_VALIDATE"
SCHEMA_RESOURCE = "contracts/usage-event.schema.json"
"""Package resource holding the contract (a copy of the repository's ``contracts/`` file)."""

_MAX_MAP_ENTRIES = 32
_MAX_VALUE_LEN = 256
_MAP_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_FRAMEWORK = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_LLM_OPERATIONS = frozenset({"chat", "generate_content", "text_completion"})


# --------------------------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------------------------

_SECRET_KEY = re.compile(
    r"(?i)(passw|secret|credential|authori[sz]ation|api[-_.]?key|cookie|private[-_.]?key|bearer"
    r"|(^|[-_.])token([-_.]|$)"
    r"|(access|refresh|id|auth|oauth|user|session|delegated)[-_.]?token)"
)
_SECRET_VALUE = re.compile(
    r"(?i)(bearer\s+[\w.~+/=-]+"  # Authorization header values
    r"|\bya29\.[\w.-]+"  # Google OAuth access tokens
    r"|\beyJ[\w-]+\.[\w-]+\.[\w-]*"  # JWTs (ID tokens, signed assertions)
    r"|\bgh[pousr]_\w{20,}"  # GitHub tokens
    r"|\bAIza[\w-]{30,})"  # Google API keys
)


def _user_credential_keys() -> set[str]:
    from esmeralda.context import USER_TOKEN_KEY

    keys = {USER_TOKEN_KEY, f"temp:{USER_TOKEN_KEY}"}
    configured = os.environ.get("USER_AUTH_ID")
    if configured:
        keys |= {configured, f"temp:{configured}"}
    return {key.lower() for key in keys}


def is_secret_key(key: str) -> bool:
    """True if a key name looks like it holds a credential, token or the delegated user credential."""
    return bool(_SECRET_KEY.search(key)) or key.lower() in _user_credential_keys()


def redact(value: Any) -> Any:
    """Returns a copy of ``value`` without credentials.

    Mapping entries whose key looks like a credential are dropped; strings have embedded bearer
    tokens, OAuth access tokens, JWTs and API keys masked. Applied to every record before it is
    emitted, so it also protects ``dimensions``, ``caller.declared`` and legacy events.
    """
    if isinstance(value, Mapping):
        return {k: redact(v) for k, v in value.items() if not (isinstance(k, str) and is_secret_key(k))}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _SECRET_VALUE.sub(REDACTED, value)
    return value


# --------------------------------------------------------------------------------------------
# Transports
# --------------------------------------------------------------------------------------------


class Transport(Protocol):
    def emit(self, record: Mapping[str, Any]) -> None: ...


class StdoutTransport:
    """One JSON object per line on stdout (or ``stream``): the Cloud Logging structured format."""

    def __init__(self, stream: TextIO | None = None):
        self._stream = stream
        self._lock = threading.Lock()

    def emit(self, record: Mapping[str, Any]) -> None:
        line = json.dumps(record, default=str) + "\n"
        stream = self._stream or sys.stdout
        with self._lock:
            stream.write(line)
            stream.flush()


class OTelLogsTransport:
    """Emits each record as an OpenTelemetry log record (body = the record, event name = the schema).

    Uses the global ``LoggerProvider`` unless one is given; configure an OTLP log exporter on it to
    ship records through a collector (the multi-cloud path).
    """

    def __init__(self, logger_provider: Any = None, logger_name: str = SCHEMA):
        from opentelemetry import _logs

        provider = logger_provider or _logs.get_logger_provider()
        self._logger = provider.get_logger(logger_name)
        self._severity = _logs.SeverityNumber.INFO

    def emit(self, record: Mapping[str, Any]) -> None:
        self._logger.emit(
            body=json.loads(json.dumps(record, default=str)),
            event_name=SCHEMA,
            severity_number=self._severity,
            severity_text="INFO",
            attributes={"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "event": str(record.get("event", ""))},
        )


class MultiTransport:
    """Fans a record out to several transports; one failing does not stop the others."""

    def __init__(self, *transports: Transport):
        self.transports = transports

    def emit(self, record: Mapping[str, Any]) -> None:
        for transport in self.transports:
            try:
                transport.emit(record)
            except Exception:
                logger.exception("Usage transport %s failed", type(transport).__name__)


def transport_from_env(stream: TextIO | None = None) -> Transport:
    """``ESMERALDA_USAGE_TRANSPORT``: ``stdout`` (default), ``otel``, or both (``stdout,otel``)."""
    names = [n.strip().lower() for n in (os.environ.get(TRANSPORT_ENV) or "stdout").split(",") if n.strip()]
    transports: list[Transport] = []
    for name in dict.fromkeys(names):
        if name == "stdout":
            transports.append(StdoutTransport(stream))
        elif name == "otel":
            try:
                transports.append(OTelLogsTransport())
            except Exception:
                logger.warning("OpenTelemetry logs unavailable; usage records go to stdout", exc_info=True)
        else:
            logger.warning("Unknown %s value %r ignored", TRANSPORT_ENV, name)
    if not transports:
        transports.append(StdoutTransport(stream))
    return transports[0] if len(transports) == 1 else MultiTransport(*transports)


# --------------------------------------------------------------------------------------------
# The contract, shipped with the package
# --------------------------------------------------------------------------------------------


def schema_text() -> str:
    """The ``esmeralda.usage`` JSON Schema, as shipped in the package."""
    from importlib.resources import files

    return files("esmeralda").joinpath(SCHEMA_RESOURCE).read_text(encoding="utf-8")


_schema_cache: dict[str, Any] | None = None
_validator_cache: Any = None


def schema() -> dict[str, Any]:
    """The ``esmeralda.usage`` JSON Schema, parsed (cached)."""
    global _schema_cache
    if _schema_cache is None:
        _schema_cache = json.loads(schema_text())
    return _schema_cache


def validate(record: Mapping[str, Any]) -> list[str]:
    """Validates a record against the contract; returns the violations (empty when valid).

    Needs the ``jsonschema`` package (not a hard dependency of ``esmeralda``); raises
    ``ImportError`` with a hint if it is missing.
    """
    global _validator_cache
    if _validator_cache is None:
        try:
            from jsonschema import Draft202012Validator
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ImportError("esmeralda.usage.validate needs the 'jsonschema' package") from exc
        _validator_cache = Draft202012Validator(schema())
    errors = sorted(_validator_cache.iter_errors(record), key=lambda e: list(e.absolute_path))
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '<record>'}: {e.message}" for e in errors]


# --------------------------------------------------------------------------------------------
# Record parts
# --------------------------------------------------------------------------------------------


def _env(*names: str) -> str | None:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return None


def detect_runtime() -> str:
    """``ESMERALDA_RUNTIME`` if set, else inferred from the platform's environment variables."""
    explicit = _env("ESMERALDA_RUNTIME")
    if explicit in RUNTIMES:
        return explicit
    if _env("GOOGLE_CLOUD_AGENT_ENGINE_ID"):
        return "agent_engine"
    if _env("K_SERVICE"):
        return "cloud_run"
    if _env("KUBERNETES_SERVICE_HOST"):
        return "gke"
    return "external"


@dataclass(frozen=True)
class Workload:
    """The workload that serves the operation (``workload`` in the record)."""

    name: str
    team: str | None = None
    runtime: str | None = None
    framework: str | None = None
    cloud: str | None = None

    @classmethod
    def from_env(cls, *, name: str | None = None, framework: str | None = None) -> Workload:
        """Reads ``ESMERALDA_WORKLOAD_NAME`` (else ``name``, ``AGENT_NAME``, ``OTEL_SERVICE_NAME``,
        ``K_SERVICE``), ``ESMERALDA_TEAM``, ``ESMERALDA_RUNTIME`` (else detected),
        ``ESMERALDA_FRAMEWORK`` (else ``framework``) and ``ESMERALDA_CLOUD`` (default ``gcp``)."""
        cloud = _env("ESMERALDA_CLOUD") or "gcp"
        fw = _env("ESMERALDA_FRAMEWORK") or framework
        return cls(
            name=_env("ESMERALDA_WORKLOAD_NAME") or name or _env("AGENT_NAME", "OTEL_SERVICE_NAME", "K_SERVICE")
            or "unknown_workload",
            team=_env("ESMERALDA_TEAM"),
            runtime=detect_runtime(),
            framework=fw.lower() if fw and _FRAMEWORK.match(fw.lower()) else None,
            cloud=cloud if cloud in CLOUDS else "other",
        )

    def to_dict(self) -> dict[str, str]:
        return _prune(
            {
                "name": _bounded(self.name, 128),
                "team": _bounded(self.team, 128),
                "runtime": self.runtime if self.runtime in RUNTIMES else None,
                "framework": self.framework,
                "cloud": self.cloud if self.cloud in CLOUDS else None,
            }
        )


@dataclass(frozen=True)
class Caller:
    """Who made the call (``caller`` in the record).

    ``verified`` must be True only when ``principal``/``team`` come from authentication.
    ``declared`` is caller-supplied context (cost centre, country, calling project/agent).
    """

    principal: str | None = None
    team: str | None = None
    verified: bool = False
    declared: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_declared(cls, declared: Mapping[str, Any] | None) -> Caller | None:
        """An unverified caller from declared context; None if there is nothing usable."""
        clean = string_map(declared or {})
        return cls(declared=clean) if clean else None

    def to_dict(self) -> dict[str, Any]:
        return _prune(
            {
                "principal": _bounded(self.principal, 512),
                "team": _bounded(self.team, 128),
                "verified": bool(self.verified),
                "declared": string_map(self.declared) or None,
            }
        )


def string_map(values: Mapping[str, Any] | None) -> dict[str, str]:
    """Normalizes ``dimensions`` / ``caller.declared``: lowercase snake_case keys, string values,
    at most 32 entries of 256 characters. Invalid keys, credential-like keys and empty values are
    dropped."""
    out: dict[str, str] = {}
    for key, value in (values or {}).items():
        if len(out) >= _MAX_MAP_ENTRIES:
            break
        if not isinstance(key, str) or not _MAP_KEY.match(key) or is_secret_key(key) or value is None:
            if isinstance(key, str) and not _MAP_KEY.match(key):
                logger.debug("Usage map key %r dropped (must match %s)", key, _MAP_KEY.pattern)
            continue
        text = str(value).strip()
        if text:
            out[key] = text[:_MAX_VALUE_LEN]
    return out


def _bounded(value: Any, limit: int = _MAX_VALUE_LEN) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] or None


def _prune(values: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None and v != {} and v != []}


def _count(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _duration(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(max(number, 0.0), 2)


def _status(value: Any) -> str | None:
    if value is None:
        return None
    return "ok" if str(value).strip().lower() in {"ok", "success", "succeeded", "unset", "true"} else "error"


def _timestamp(value: datetime | None = None) -> str:
    moment = (value or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _span_ids(span: Any = None) -> dict[str, str]:
    """trace_id / span_id of ``span`` (default: the current span); empty when there is none."""
    try:
        if span is None:
            from opentelemetry import trace

            span = trace.get_current_span()
        span_context = span.get_span_context() if hasattr(span, "get_span_context") else span.context
    except Exception:
        return {}
    if span_context is None or not getattr(span_context, "is_valid", False):
        return {}
    return {"trace_id": format(span_context.trace_id, "032x"), "span_id": format(span_context.span_id, "016x")}


def _gen_ai_usage(
    input_tokens: Any = None, output_tokens: Any = None, cached_tokens: Any = None, reasoning_tokens: Any = None
) -> dict[str, int]:
    return _prune(
        {
            "usage.input_tokens": _count(input_tokens),
            "usage.output_tokens": _count(output_tokens),
            "usage.cached_tokens": _count(cached_tokens),
            "usage.reasoning_tokens": _count(reasoning_tokens),
        }
    )


# --------------------------------------------------------------------------------------------
# Recorder: builds and emits records
# --------------------------------------------------------------------------------------------


class UsageRecorder:
    """Builds ``esmeralda.usage`` records and sends them to a transport.

    Args:
        transport: where records go (default: ``transport_from_env()``, i.e. stdout JSON).
        workload: the serving workload, or a callable returning it (default:
            ``Workload.from_env(framework=framework)``, resolved per record so environment changes
            are picked up).
        framework: default ``workload.framework`` when ``workload`` is not given.
        source: ``workload`` (default) or ``gateway``.
        dimensions: dimensions added to every record (per-record dimensions win).
        validate: check every record against the shipped schema before sending it and log a
            warning for violations (the record is still sent). Default: ``ESMERALDA_USAGE_VALIDATE``
            (off). Needs ``jsonschema``; skipped with a warning when it is missing.
    """

    def __init__(
        self,
        transport: Transport | None = None,
        *,
        workload: Workload | Callable[[], Workload] | None = None,
        framework: str | None = None,
        source: str = "workload",
        dimensions: Mapping[str, Any] | None = None,
        validate: bool | None = None,
    ):
        self.transport = transport or transport_from_env()
        self._workload = workload
        self._framework = framework
        self._source = source
        self._dimensions = dict(dimensions or {})
        if validate is None:
            validate = (os.environ.get(VALIDATE_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}
        self._validate = validate

    @property
    def workload(self) -> Workload:
        if isinstance(self._workload, Workload):
            return self._workload
        if callable(self._workload):
            return self._workload()
        return Workload.from_env(framework=self._framework)

    # ---- generic --------------------------------------------------------------------------

    def build(
        self,
        event: str,
        *,
        gen_ai: Mapping[str, Any] | None = None,
        tool: Mapping[str, Any] | None = None,
        caller: Caller | None = None,
        dimensions: Mapping[str, Any] | None = None,
        status: Any = None,
        error_type: str | None = None,
        latency_ms: Any = None,
        timestamp: datetime | None = None,
        span: Any = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        workload: Workload | None = None,
        gateway: Mapping[str, Any] | None = None,
        http: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Builds a record (not emitted). Unknown or empty parts are omitted."""
        if event not in EVENTS:
            raise ValueError(f"unknown usage event {event!r}; expected one of {EVENTS}")
        ids = _span_ids(span)
        if trace_id and re.fullmatch(r"[0-9a-f]{32}", trace_id):
            ids["trace_id"] = trace_id
        if span_id and re.fullmatch(r"[0-9a-f]{16}", span_id):
            ids["span_id"] = span_id
        record: dict[str, Any] = {
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "event": event,
            "timestamp": _timestamp(timestamp),
            **ids,
            "source": self._source,
            "workload": (workload or self.workload).to_dict(),
            "caller": caller.to_dict() if caller else None,
            "gen_ai": _prune(dict(gen_ai or {})) or None,
            "tool": _prune(dict(tool or {})) or None,
            "gateway": _prune(dict(gateway or {})) or None,
            "http": _prune(dict(http or {})) or None,
            "status": _status(status),
            "error_type": _bounded(error_type),
            "dimensions": string_map({**self._dimensions, **(dimensions or {})}) or None,
            "latency_ms": _duration(latency_ms),
        }
        return _prune(record)

    def emit(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Redacts and sends a record; returns what was sent. Never raises."""
        clean = redact(record)
        if self._validate:
            try:
                violations = validate(clean)
            except ImportError:
                logger.warning("%s is set but jsonschema is not installed; validation disabled", VALIDATE_ENV)
                self._validate = False
            else:
                if violations:
                    logger.warning("Usage record violates %s v%s: %s", SCHEMA, SCHEMA_VERSION, "; ".join(violations))
        try:
            self.transport.emit(clean)
        except Exception:
            logger.exception("Failed to emit usage record")
        return clean

    # ---- typed builders -------------------------------------------------------------------

    def record_llm_call(
        self,
        *,
        request_model: str | None = None,
        response_model: str | None = None,
        system: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        finish_reasons: Iterable[str] | None = None,
        operation_name: str = "generate_content",
        agent_name: str | None = None,
        conversation_id: str | None = None,
        status: str = "ok",
        error_type: str | None = None,
        latency_ms: float | None = None,
        caller: Caller | None = None,
        dimensions: Mapping[str, Any] | None = None,
        span: Any = None,
        timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        """Records one model call. ``input_tokens`` includes cached tokens and ``output_tokens``
        includes reasoning tokens (OpenTelemetry GenAI semantics)."""
        if not request_model and not response_model:
            request_model = "unknown"
        gen_ai = {
            "system": _bounded(system, 128),
            "operation.name": _bounded(operation_name, 64),
            "request.model": _bounded(request_model),
            "response.model": _bounded(response_model),
            "response.finish_reasons": [str(r)[:64] for r in finish_reasons][:16] if finish_reasons else None,
            **_gen_ai_usage(input_tokens, output_tokens, cached_tokens, reasoning_tokens),
            "agent.name": _bounded(agent_name),
            "conversation.id": _bounded(conversation_id),
        }
        return self.emit(
            self.build(
                EVENT_LLM_CALL,
                gen_ai=gen_ai,
                caller=caller,
                dimensions=dimensions,
                status=status,
                error_type=error_type,
                latency_ms=latency_ms,
                span=span,
                timestamp=timestamp,
            )
        )

    def record_tool_call(
        self,
        *,
        name: str,
        status: str = "ok",
        duration_ms: float | None = None,
        server: str | None = None,
        call_id: str | None = None,
        error_type: str | None = None,
        agent_name: str | None = None,
        conversation_id: str | None = None,
        caller: Caller | None = None,
        dimensions: Mapping[str, Any] | None = None,
        span: Any = None,
        timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        """Records one tool call (MCP, function or built-in). Pass the error *type*, never its message."""
        normalized = _status(status)
        tool = {
            "name": _bounded(name) or "unknown_tool",
            "server": _bounded(server),
            "call_id": _bounded(call_id),
            "status": normalized,
            "error_type": _bounded(error_type) if normalized == "error" else None,
            "duration_ms": _duration(duration_ms),
        }
        gen_ai = {
            "operation.name": "execute_tool",
            "agent.name": _bounded(agent_name),
            "conversation.id": _bounded(conversation_id),
        }
        return self.emit(
            self.build(
                EVENT_TOOL_CALL,
                gen_ai=gen_ai,
                tool=tool,
                caller=caller,
                dimensions=dimensions,
                status=normalized,
                error_type=tool["error_type"],
                latency_ms=duration_ms,
                span=span,
                timestamp=timestamp,
            )
        )

    def record_agent_invocation(
        self,
        *,
        agent_name: str,
        latency_ms: float | None = None,
        status: str = "ok",
        error_type: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        conversation_id: str | None = None,
        caller: Caller | None = None,
        dimensions: Mapping[str, Any] | None = None,
        span: Any = None,
        timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        """Records one agent invocation. Token counts are a roll-up of its model calls: use them
        for per-invocation budgets, never add them to ``llm.call`` sums."""
        gen_ai = {
            "operation.name": "invoke_agent",
            "agent.name": _bounded(agent_name) or "unknown_agent",
            "conversation.id": _bounded(conversation_id),
            **_gen_ai_usage(input_tokens, output_tokens, cached_tokens, reasoning_tokens),
        }
        return self.emit(
            self.build(
                EVENT_AGENT_INVOCATION,
                gen_ai=gen_ai,
                caller=caller,
                dimensions=dimensions,
                status=status,
                error_type=error_type,
                latency_ms=latency_ms,
                span=span,
                timestamp=timestamp,
            )
        )


# --------------------------------------------------------------------------------------------
# Process-wide recorder and module-level manual API
# --------------------------------------------------------------------------------------------

_default_recorder: UsageRecorder | None = None
_default_lock = threading.Lock()


def get_recorder() -> UsageRecorder:
    """The process-wide recorder (created on first use from the environment)."""
    global _default_recorder
    with _default_lock:
        if _default_recorder is None:
            _default_recorder = UsageRecorder()
        return _default_recorder


def set_recorder(recorder: UsageRecorder | None) -> None:
    """Replaces the process-wide recorder (None resets it to the environment default)."""
    global _default_recorder
    with _default_lock:
        _default_recorder = recorder


def record_llm_call(**kwargs: Any) -> dict[str, Any]:
    """``UsageRecorder.record_llm_call`` on the process-wide recorder."""
    return get_recorder().record_llm_call(**kwargs)


def record_tool_call(**kwargs: Any) -> dict[str, Any]:
    """``UsageRecorder.record_tool_call`` on the process-wide recorder."""
    return get_recorder().record_tool_call(**kwargs)


def record_agent_invocation(**kwargs: Any) -> dict[str, Any]:
    """``UsageRecorder.record_agent_invocation`` on the process-wide recorder."""
    return get_recorder().record_agent_invocation(**kwargs)


# --------------------------------------------------------------------------------------------
# OpenTelemetry GenAI span adapter
# --------------------------------------------------------------------------------------------

try:  # The SDK is a dependency of esmeralda, but keep the module importable without it.
    from opentelemetry.sdk.trace import SpanProcessor as _SpanProcessor
except ImportError:  # pragma: no cover
    _SpanProcessor = object  # type: ignore[assignment,misc]


def _first(attrs: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = attrs.get(key)
        if value is not None and value != "":
            return value
    return None


def usage_from_span(span: Any, recorder: UsageRecorder | None = None) -> dict[str, Any] | None:
    """Builds (does not emit) the usage record for a finished OpenTelemetry GenAI span.

    Maps ``gen_ai.operation.name``: ``generate_content``/``chat``/``text_completion`` →
    ``llm.call``, ``execute_tool`` → ``tool.call``, ``invoke_agent`` → ``agent.invocation``.
    Spans without a GenAI operation name (including ADK's legacy ``call_llm`` span, which
    duplicates ``generate_content``) return None. ``caller.*`` span attributes (set by
    ``BaggageSpanProcessor``) become ``caller.declared``; ``esmeralda.dimension.<key>`` attributes
    become ``dimensions``.
    """
    attrs: Mapping[str, Any] = getattr(span, "attributes", None) or {}
    operation = attrs.get("gen_ai.operation.name")
    if operation in _LLM_OPERATIONS:
        event = EVENT_LLM_CALL
    elif operation == "execute_tool":
        event = EVENT_TOOL_CALL
    elif operation == "invoke_agent":
        event = EVENT_AGENT_INVOCATION
    else:
        return None
    recorder = recorder or get_recorder()

    start, end = getattr(span, "start_time", None), getattr(span, "end_time", None)
    duration_ms = (end - start) / 1e6 if start is not None and end is not None and end >= start else None
    timestamp = datetime.fromtimestamp(end / 1e9, tz=timezone.utc) if end else None

    span_status = getattr(span, "status", None)
    status_code = getattr(getattr(span_status, "status_code", None), "name", "UNSET")
    error_type = _first(attrs, "error.type")
    failed = status_code == "ERROR" or error_type is not None
    status = "error" if failed else "ok"
    if failed and not error_type:
        error_type = "error"

    declared = {
        key[len("caller.") :]: value
        for key, value in attrs.items()
        if key.startswith("caller.") and key.count(".") == 1
    }
    caller = Caller.from_declared(declared)
    dimensions = {
        key[len(DIMENSION_ATTRIBUTE_PREFIX) :]: value
        for key, value in attrs.items()
        if key.startswith(DIMENSION_ATTRIBUTE_PREFIX)
    }

    usage = _gen_ai_usage(
        _first(attrs, "gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens"),
        _first(attrs, "gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens"),
        _first(attrs, "gen_ai.usage.cache_read.input_tokens", "gen_ai.usage.cached_tokens"),
        _first(
            attrs,
            "gen_ai.usage.reasoning.output_tokens",
            "gen_ai.usage.reasoning_tokens",
            "gen_ai.usage.experimental.reasoning_tokens",
        ),
    )
    common = {
        "agent.name": _bounded(attrs.get("gen_ai.agent.name")),
        "conversation.id": _bounded(attrs.get("gen_ai.conversation.id")),
    }
    tool = None
    if event == EVENT_LLM_CALL:
        finish = attrs.get("gen_ai.response.finish_reasons")
        gen_ai = {
            "system": _bounded(_first(attrs, "gen_ai.provider.name", "gen_ai.system"), 128),
            "operation.name": operation,
            "request.model": _bounded(attrs.get("gen_ai.request.model")),
            "response.model": _bounded(attrs.get("gen_ai.response.model")),
            "response.finish_reasons": [str(r)[:64] for r in finish][:16] if finish else None,
            **usage,
            **common,
        }
        if not gen_ai["request.model"] and not gen_ai["response.model"]:
            gen_ai["request.model"] = "unknown"
    elif event == EVENT_TOOL_CALL:
        gen_ai = {"operation.name": operation, **common}
        name = _first(attrs, "gen_ai.tool.name")
        if not name:
            span_name = str(getattr(span, "name", "") or "")
            name = span_name.split(" ", 1)[1] if span_name.startswith("execute_tool ") else span_name
        tool = {
            "name": _bounded(name) or "unknown_tool",
            "server": _bounded(_first(attrs, "mcp.server.name", "server.address")),
            "call_id": _bounded(attrs.get("gen_ai.tool.call.id")),
            "status": status,
            "error_type": _bounded(error_type) if failed else None,
            "duration_ms": _duration(duration_ms),
        }
    else:
        agent_name = common["agent.name"]
        if not agent_name:
            span_name = str(getattr(span, "name", "") or "")
            agent_name = span_name.split(" ", 1)[1] if span_name.startswith("invoke_agent ") else "unknown_agent"
        gen_ai = {"operation.name": operation, **usage, **common, "agent.name": agent_name}

    return recorder.build(
        event,
        gen_ai=gen_ai,
        tool=tool,
        caller=caller,
        dimensions=dimensions,
        status=status,
        error_type=error_type if failed else None,
        latency_ms=duration_ms,
        timestamp=timestamp,
        span=span,
    )


class UsageSpanProcessor(_SpanProcessor):
    """Turns finished OpenTelemetry GenAI spans into ``esmeralda.usage`` records.

    Add it to the ``TracerProvider`` of any OTel-instrumented agent::

        provider.add_span_processor(UsageSpanProcessor(framework="langgraph"))

    Don't combine it with ``EsmeraldaTelemetryPlugin`` in the same ADK process: both would record
    the same calls.
    """

    def __init__(self, recorder: UsageRecorder | None = None, *, framework: str | None = None):
        self._recorder = recorder or (UsageRecorder(framework=framework) if framework else None)

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        return None

    def on_end(self, span: Any) -> None:
        try:
            recorder = self._recorder or get_recorder()
            record = usage_from_span(span, recorder)
            if record is not None:
                recorder.emit(record)
        except Exception:
            logger.exception("Failed to convert span to a usage record")

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
