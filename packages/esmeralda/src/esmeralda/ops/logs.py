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

"""Agent logs from Cloud Logging, reduced for an LLM reader.

Entries of the agent's Reasoning Engine (``resource.type=aiplatform.googleapis.com/ReasoningEngine``)
are fetched newest first, then:

* a traceback is collapsed to its last line (the exception), whether it arrives as one multi-line
  entry or as one entry per line (frames are dropped and counted);
* repeated messages (same severity and text once numbers/ids are masked) become one entry with a
  ``count``;
* every message is cut to 300 characters.

Uses ``google-cloud-logging`` when it is installed, else ``gcloud logging read --format=json``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from esmeralda.ops import repo
from esmeralda.ops.repo import OpsError

SEVERITIES = ["DEFAULT", "DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]
MESSAGE_LIMIT = 300
_ENGINE = re.compile(r"^projects/(?P<project>[^/]+)/locations/(?P<location>[^/]+)/reasoningEngines/(?P<id>[^/]+)$")
_SINCE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$")
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}

_TRACEBACK_HEAD = re.compile(r"^\s*Traceback \(most recent call last\):")
_FRAME = re.compile(
    r"^\s*(File \".*\", line \d+|Traceback \(most recent call last\):|During handling of the above exception"
    r"|The above exception was the direct cause|\^+\s*$|~+\^*~*\s*$)"
)
_EXCEPTION = re.compile(r"^[A-Za-z_][\w.]*(:|$)")  # "ValueError: ...", "grpc._channel._InactiveRpcError: ..."
_MASKS = [
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<hex>"),
    (re.compile(r"\b[0-9a-f]{16,}\b", re.I), "<hex>"),
    (re.compile(r"\d+(\.\d+)?"), "#"),
]


def parse_since(since: str) -> timedelta:
    match = _SINCE.match(since or "")
    if not match:
        raise OpsError(f"since must look like 30m, 2h, 1d or 45s, got '{since}'")
    return timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})


def parse_severity(severity: str) -> str:
    value = (severity or "DEFAULT").upper()
    if value == "WARN":
        value = "WARNING"
    if value not in SEVERITIES:
        raise OpsError(f"severity must be one of {', '.join(SEVERITIES)}, got '{severity}'")
    return value


def build_filter(engine_id: str, severity: str, start: datetime) -> tuple[str, str]:
    """(project, Cloud Logging filter) for one Reasoning Engine."""
    match = _ENGINE.match(engine_id or "")
    if not match:
        raise OpsError(f"Not a Reasoning Engine resource name: '{engine_id}'")
    clauses = [
        'resource.type="aiplatform.googleapis.com/ReasoningEngine"',
        f'resource.labels.reasoning_engine_id="{match["id"]}"',
        f'timestamp>="{start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}"',
    ]
    if severity != "DEFAULT":
        clauses.append(f"severity>={severity}")
    return match["project"], " AND ".join(clauses)


# ------------------------------------------------------------------------------------------
# reduction (pure)
# ------------------------------------------------------------------------------------------


def message_of(payload: Any) -> str:
    """The human message of a log payload (text, JSON with message/msg, or anything else)."""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in ("message", "msg", "textPayload", "error", "status"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict) and value.get("message"):
                return str(value["message"])
        return json.dumps(payload, default=str, separators=(",", ":"))
    return str(payload)


def collapse(message: str) -> tuple[str, bool]:
    """(message with any traceback collapsed to its last line, whether it was a traceback)."""
    lines = [line for line in message.splitlines() if line.strip()]
    if not lines:
        return "", False
    if any(_TRACEBACK_HEAD.match(line) for line in lines):
        # The exception is the last unindented, non-frame line that starts with a (dotted) name; the
        # lines after it continue its message (e.g. grpc's "status = ...", "details = ...").
        candidates = [i for i, x in enumerate(lines) if not _FRAME.match(x) and not x.startswith((" ", "\t"))]
        if not candidates:
            return lines[-1].strip(), True
        index = ([i for i in candidates if _EXCEPTION.match(lines[i])] or candidates)[-1]
        exception = " ".join(x.strip() for x in lines[index:])
        head = next((x.strip() for x in lines if not _TRACEBACK_HEAD.match(x)), "")
        prefix = "" if head == lines[index].strip() or head.startswith("File ") else f"{_cut(head, 120)} → "
        return f"{prefix}{exception}", True
    return " ".join(line.strip() for line in lines), False


def is_frame_only(message: str) -> bool:
    """A log entry that is just a piece of a traceback split one line per entry."""
    lines = [line for line in message.splitlines() if line.strip()]
    if not lines:
        return True
    return all(_FRAME.match(line) or line.startswith(("    ", "\t")) for line in lines)


def _cut(text: str, limit: int = MESSAGE_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _key(severity: str, message: str) -> tuple[str, str]:
    masked = message
    for pattern, replacement in _MASKS:
        masked = pattern.sub(replacement, masked)
    return severity, masked


def reduce_entries(entries: Iterable[dict[str, Any]], limit: int = 50) -> dict[str, Any]:
    """Collapses, filters and deduplicates entries ``{time, severity, payload}`` (newest first)."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    seen = frames = tracebacks = 0
    for entry in entries:
        seen += 1
        raw = message_of(entry.get("payload"))
        if is_frame_only(raw):
            frames += 1
            continue
        message, was_traceback = collapse(raw)
        tracebacks += was_traceback
        severity = str(entry.get("severity") or "DEFAULT")
        key = _key(severity, message)
        group = groups.get(key)
        if group is None:
            groups[key] = {"time": entry.get("time"), "severity": severity, "message": _cut(message), "count": 1}
        else:
            group["count"] += 1
            group["first"] = entry.get("time")
    reduced = list(groups.values())[:limit]
    for group in reduced:
        if group["count"] == 1:
            del group["count"]
    result: dict[str, Any] = {"scanned": seen, "returned": len(reduced), "entries": reduced}
    if len(groups) > limit:
        result["more_distinct"] = len(groups) - limit
    if tracebacks:
        result["tracebacks_collapsed"] = tracebacks
    if frames:
        result["frame_lines_dropped"] = frames
    return result


# ------------------------------------------------------------------------------------------
# fetching
# ------------------------------------------------------------------------------------------


def _time(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = str(value)
    return re.sub(r"\.\d+(?=Z|[+-]\d\d:?\d\d$)", "", text)


def fetch_with_client(project: str, filter_: str, max_entries: int) -> list[dict[str, Any]]:
    from google.cloud import logging as cloud_logging

    client = cloud_logging.Client(project=project)
    entries = client.list_entries(
        resource_names=[f"projects/{project}"],
        filter_=filter_,
        order_by=cloud_logging.DESCENDING,
        max_results=max_entries,
        page_size=min(max_entries, 1000),
    )
    return [{"time": _time(e.timestamp), "severity": e.severity or "DEFAULT", "payload": e.payload} for e in entries]


def fetch_with_gcloud(root: Path, project: str, filter_: str, max_entries: int, timeout: float) -> list[dict[str, Any]]:
    result = repo.run(
        [
            "gcloud",
            "logging",
            "read",
            filter_,
            f"--project={project}",
            f"--limit={max_entries}",
            "--order=desc",
            "--format=json",
        ],
        cwd=root,
        timeout=timeout,
        what="gcloud logging read",
    )
    if result.returncode != 0:
        raise repo.failure("gcloud logging read", result)
    rows = json.loads(result.stdout or "[]")
    return [
        {
            "time": _time(row.get("timestamp")),
            "severity": row.get("severity") or "DEFAULT",
            "payload": row.get("textPayload") or row.get("jsonPayload") or row.get("protoPayload"),
        }
        for row in rows
    ]


def _client_available() -> bool:
    try:
        import google.cloud.logging  # noqa: F401
    except ImportError:
        return False
    return True


def agent_logs(
    root: Path,
    env: str,
    agent: str,
    *,
    since: str = "30m",
    severity: str = "WARNING",
    limit: int = 50,
    timeout: float = 90.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    window = parse_since(since)
    severity = parse_severity(severity)
    limit = max(1, min(int(limit), 200))
    unit = f"layer-5-workloads/agents/{agent}"
    repo.unit_dir(root, env, unit)
    engine = repo.output(root, env, unit, "engine_id")
    if not engine:
        raise OpsError(f"No engine_id output for {agent} in {env}: deploy it (make deploy-{agent} ENV={env})")
    project, filter_ = build_filter(engine, severity, (now or datetime.now(UTC)) - window)
    max_entries = min(limit * 10, 1000)
    source = "google-cloud-logging" if _client_available() else "gcloud"
    if source == "gcloud":
        entries = fetch_with_gcloud(root, project, filter_, max_entries, timeout)
    else:
        try:
            entries = fetch_with_client(project, filter_, max_entries)
        except Exception as exc:  # map auth / permission errors to an actionable message
            text = f"{type(exc).__name__}: {exc}"
            found = repo.hint(text)
            raise OpsError(f"Cloud Logging query failed: {found + '. ' if found else ''}{repo.short(text)}") from exc
    return {
        "env": env,
        "agent": agent,
        "engine": engine.rsplit("/", 1)[-1],
        "since": since,
        "severity": f">={severity}",
        "source": source,
        **reduce_entries(entries, limit),
    }
