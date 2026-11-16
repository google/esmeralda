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

"""Usage, chargeback, audit and trace data: read-only BigQuery queries over the layer-4 views.

The views (``vw_monthly_agent_chargeback``, ``vw_usage_billable``, ``vw_security_audit_trail``,
``usage_events``) live in the telemetry dataset of the governance project; the project and dataset
come from configuration. Every query:

* is a fixed SQL text: table names are validated identifiers (:mod:`esmeralda_ui.settings`), every
  value is a typed query parameter, never string-formatted into the SQL;
* sets ``maximum_bytes_billed`` (BigQuery refuses the job instead of scanning more) and a timeout;
* is a ``SELECT``: the BFF has no write path and never emits ``esmeralda.usage`` records.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from esmeralda_ui.models import (
    Audit,
    AuditEntry,
    ChargebackRow,
    TeamTotal,
    Trace,
    TraceList,
    TraceSpan,
    TraceSummary,
    Usage,
)
from esmeralda_ui.settings import Settings

log = logging.getLogger(__name__)

QUERY_TIMEOUT_SECONDS = 30
MAX_CHARGEBACK_ROWS = 500
MAX_TRACE_RECORDS = 500
JOB_LABELS = {"app": "esmeralda-ui"}


class DataUnavailable(Exception):
    """No data source is configured, or the query failed (answered with a generic 503)."""


@dataclass(frozen=True)
class Param:
    name: str
    type: str  # BigQuery standard SQL type: STRING, INT64, TIMESTAMP
    value: Any


class DataSource(Protocol):
    def usage(self, month: str, team: str | None) -> Usage: ...
    def audit(self, days: int, limit: int) -> Audit: ...
    def traces(self, days: int, limit: int) -> TraceList: ...
    def trace(self, trace_id: str, days: int) -> Trace | None: ...


def month_start(month: str) -> datetime:
    year, mon = (int(p) for p in month.split("-"))
    return datetime(year, mon, 1, tzinfo=UTC)


def team_totals(rows: Iterable[ChargebackRow]) -> list[TeamTotal]:
    totals: dict[str, TeamTotal] = {}
    for row in rows:
        team = row.billed_team or "unattributed"
        t = totals.get(team) or TeamTotal(
            billed_team=team, total_requests=0, total_tokens=0, net_total_chargeback_usd=0
        )
        totals[team] = TeamTotal(
            billed_team=team,
            total_requests=t.total_requests + row.total_requests,
            total_tokens=t.total_tokens + row.total_tokens,
            net_total_chargeback_usd=round(t.net_total_chargeback_usd + row.net_total_chargeback_usd, 6),
        )
    return sorted(totals.values(), key=lambda t: t.net_total_chargeback_usd, reverse=True)


def _int(value: Any) -> int | None:
    return None if value is None else int(value)


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value))


def build_trace(trace_id: str, records: list[dict[str, Any]], source: str) -> Trace | None:
    """The waterfall of one trace from its usage records (each record ends at its timestamp)."""
    if not records:
        return None
    spans = []
    for r in records:
        end = _timestamp(r["timestamp"])
        duration = max(_float(r.get("latency_ms")) or 0.0, 0.0)
        spans.append((end - timedelta(milliseconds=duration), end, duration, r))
    start = min(s[0] for s in spans)
    finish = max(s[1] for s in spans)
    out = []
    total_tokens = 0
    for begin, end, duration, r in sorted(spans, key=lambda s: (s[0], s[1])):
        tokens_in, tokens_out = _int(r.get("input_tokens")), _int(r.get("output_tokens"))
        total_tokens += (tokens_in or 0) + (tokens_out or 0)
        out.append(
            TraceSpan(
                timestamp=end,
                offset_ms=round((begin - start).total_seconds() * 1000, 3),
                duration_ms=round(duration, 3),
                event=r.get("event"),
                source=r.get("source"),
                span_id=r.get("span_id"),
                workload_name=r.get("workload_name"),
                workload_team=r.get("workload_team"),
                caller_principal=r.get("caller_principal"),
                caller_team=r.get("caller_team"),
                caller_verified=r.get("caller_verified"),
                model=r.get("request_model"),
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                tool_name=r.get("tool_name"),
                tool_server=r.get("tool_server"),
                tool_status=r.get("tool_status"),
                gateway_product=r.get("gateway_product"),
                gateway_name=r.get("gateway_name"),
                http_status_code=_int(r.get("http_status_code")),
                status=r.get("status"),
                error_type=r.get("error_type"),
            )
        )
    return Trace(
        source=source,  # type: ignore[arg-type]
        trace_id=trace_id,
        started_at=start,
        duration_ms=round((finish - start).total_seconds() * 1000, 3),
        total_tokens=total_tokens,
        spans=out,
    )


def chargeback_row(r: dict[str, Any]) -> ChargebackRow:
    return ChargebackRow(
        billing_month=_timestamp(r["billing_month"]),
        agent_id=r.get("agent_id"),
        model=r.get("model"),
        billed_team=r.get("billed_team"),
        verified=r.get("verified"),
        attribution=r.get("attribution"),
        workload_team=r.get("workload_team"),
        total_requests=int(r.get("total_requests") or 0),
        total_llm_calls=_int(r.get("total_llm_calls")),
        total_tokens=int(r.get("total_tokens") or 0),
        cached_prompt_tokens=_int(r.get("cached_prompt_tokens")),
        cache_hit_ratio_pct=_float(r.get("cache_hit_ratio_pct")),
        net_total_chargeback_usd=float(r.get("net_total_chargeback_usd") or 0.0),
        gateway_metered_requests=_int(r.get("gateway_metered_requests")),
    )


# ---------------------------------------------------------------------------------------------
# BigQuery
# ---------------------------------------------------------------------------------------------

USAGE_SQL = """
SELECT billing_month, agent_id, model, billed_team, verified, attribution, workload_team,
  total_requests, total_llm_calls, total_tokens, cached_prompt_tokens, cache_hit_ratio_pct,
  net_total_chargeback_usd, gateway_metered_requests
FROM `{table}`
WHERE billing_month = @billing_month
  AND (@team IS NULL OR billed_team = @team)
ORDER BY net_total_chargeback_usd DESC
LIMIT @limit
"""

AUDIT_SQL = """
SELECT timestamp, principal_email, method_name, resource_name, service_name, status_message
FROM `{table}`
WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
ORDER BY timestamp DESC
LIMIT @limit
"""

TRACES_SQL = """
SELECT trace_id, MIN(timestamp) AS started_at, ARRAY_AGG(DISTINCT agent_id IGNORE NULLS) AS agents,
  ANY_VALUE(billed_team) AS billed_team, SUM(total_tokens) AS total_tokens
FROM `{table}`
WHERE trace_id IS NOT NULL
  AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
GROUP BY trace_id
ORDER BY started_at DESC
LIMIT @limit
"""

TRACE_SQL = """
SELECT timestamp, event, source, span_id, workload_name, workload_team, caller_principal, caller_team,
  caller_verified, request_model, input_tokens, output_tokens, tool_name, tool_server, tool_status,
  gateway_product, gateway_name, http_status_code, status, error_type, latency_ms
FROM `{table}`
WHERE trace_id = @trace_id
  AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
ORDER BY timestamp
LIMIT @limit
"""


class BigQuerySource:
    """Runs the fixed, parameterized queries with a BigQuery client (``client_factory`` for tests)."""

    def __init__(self, settings: Settings, client_factory: Callable[[], Any] | None = None):
        if not settings.bigquery_configured:
            raise ValueError("BigQuery project and dataset are not configured")
        self._settings = settings
        self._factory = client_factory or self._default_client
        self._client: Any = None

    def _default_client(self) -> Any:
        from google.cloud import bigquery

        return bigquery.Client(project=self._settings.bq_project, location=self._settings.bq_location)

    def table(self, name: str) -> str:
        return f"{self._settings.bq_project}.{self._settings.bq_dataset}.{name}"

    def run(self, sql_template: str, view: str, params: list[Param]) -> list[dict[str, Any]]:
        from google.cloud import bigquery

        if self._client is None:
            self._client = self._factory()
        config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter(p.name, p.type, p.value) for p in params],
            maximum_bytes_billed=self._settings.bq_max_bytes_billed,
            use_legacy_sql=False,
            labels=JOB_LABELS,
        )
        sql = sql_template.format(table=self.table(view))
        try:
            job = self._client.query(sql, job_config=config)
            return [dict(row.items()) for row in job.result(timeout=QUERY_TIMEOUT_SECONDS)]
        except Exception as exc:  # noqa: BLE001 - logged here, answered with a generic 503
            log.exception("BigQuery query on %s failed", view)
            raise DataUnavailable(f"query on {view} failed") from exc

    def usage(self, month: str, team: str | None) -> Usage:
        rows = self.run(
            USAGE_SQL,
            "vw_monthly_agent_chargeback",
            [
                Param("billing_month", "TIMESTAMP", month_start(month)),
                Param("team", "STRING", team),
                Param("limit", "INT64", MAX_CHARGEBACK_ROWS),
            ],
        )
        parsed = [chargeback_row(r) for r in rows]
        return Usage(source="bigquery", month=month, team=team, rows=parsed, totals=team_totals(parsed))

    def audit(self, days: int, limit: int) -> Audit:
        rows = self.run(
            AUDIT_SQL, "vw_security_audit_trail", [Param("days", "INT64", days), Param("limit", "INT64", limit)]
        )
        entries = [AuditEntry(**{**r, "timestamp": _timestamp(r["timestamp"])}) for r in rows]
        return Audit(source="bigquery", days=days, entries=entries)

    def traces(self, days: int, limit: int) -> TraceList:
        rows = self.run(TRACES_SQL, "vw_usage_billable", [Param("days", "INT64", days), Param("limit", "INT64", limit)])
        traces = [
            TraceSummary(
                trace_id=str(r["trace_id"]),
                started_at=_timestamp(r["started_at"]),
                agents=[str(a) for a in r.get("agents") or []],
                billed_team=r.get("billed_team"),
                total_tokens=int(r.get("total_tokens") or 0),
            )
            for r in rows
        ]
        return TraceList(source="bigquery", days=days, traces=traces)

    def trace(self, trace_id: str, days: int) -> Trace | None:
        rows = self.run(
            TRACE_SQL,
            "usage_events",
            [
                Param("trace_id", "STRING", trace_id),
                Param("days", "INT64", days),
                Param("limit", "INT64", MAX_TRACE_RECORDS),
            ],
        )
        return build_trace(trace_id, rows, "bigquery")


class UnconfiguredSource:
    """No BigQuery configured and demo mode off: every data endpoint is a 503, never fake data."""

    def _fail(self) -> Any:
        raise DataUnavailable("no usage data source is configured")

    def usage(self, month: str, team: str | None) -> Usage:
        return self._fail()

    def audit(self, days: int, limit: int) -> Audit:
        return self._fail()

    def traces(self, days: int, limit: int) -> TraceList:
        return self._fail()

    def trace(self, trace_id: str, days: int) -> Trace | None:
        return self._fail()


def data_source(settings: Settings, client_factory: Callable[[], Any] | None = None) -> DataSource:
    if settings.demo:
        from esmeralda_ui.demo import DemoSource

        return DemoSource()
    if settings.bigquery_configured:
        return BigQuerySource(settings, client_factory)
    return UnconfiguredSource()
