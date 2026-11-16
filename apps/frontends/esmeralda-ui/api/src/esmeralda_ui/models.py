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

"""Response models: the API contract. ``openapi.json`` and the web's TypeScript types derive from them.

Every data response carries ``source``: ``bigquery`` for live data, ``demo`` for synthetic data
(explicit demo mode only). The web shows a "Demo data" badge whenever it sees ``demo``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DataSourceName = Literal["bigquery", "demo"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorBody(Model):
    error: str = Field(description="Generic error message (no internal detail)")


class Health(Model):
    status: Literal["ok"]


class Me(Model):
    email: str
    subject: str
    auth: Literal["iap", "local-dev"] = Field(description="iap: verified IAP JWT; local-dev: fixed local identity")
    environment: str
    demo: bool = Field(description="True when the BFF serves synthetic data (explicit demo mode)")
    data_source: Literal["bigquery", "demo", "unconfigured"]


# ---------------------------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------------------------


class Payer(Model):
    team: str | None
    cost_centers: list[str]


class CatalogCaller(Model):
    caller: str
    identity: str
    team: str | None
    gateway: str | None
    pays: Payer
    enforced: bool
    tools: list[str] | None = None
    product: str | None = None


class CatalogTarget(Model):
    name: str
    kind: str
    owner_team: str
    description: str | None
    example: bool
    deploy: str | None
    compiled: bool
    registry: str | None
    gateway: str | None
    ingress_backend: str | None
    google_apis: list[str] | None = None
    callers: list[CatalogCaller]


class CatalogTeam(Model):
    name: str
    cost_centers: list[str]
    external: bool


class CatalogPolicy(Model):
    unmapped_caller: str | None = None
    declared_mismatch: str | None = None


class Catalog(Model):
    environment: str
    resolved_file: str = Field(description="Repository path of the resolved catalog the BFF serves")
    resolved_sha256: str
    policy: CatalogPolicy
    teams: list[CatalogTeam]
    targets: list[CatalogTarget]


# ---------------------------------------------------------------------------------------------
# usage, audit
# ---------------------------------------------------------------------------------------------


class ChargebackRow(Model):
    billing_month: datetime
    agent_id: str | None
    model: str | None
    billed_team: str | None
    verified: bool | None
    attribution: str | None
    workload_team: str | None
    total_requests: int
    total_llm_calls: int | None
    total_tokens: int
    cached_prompt_tokens: int | None
    cache_hit_ratio_pct: float | None
    net_total_chargeback_usd: float
    gateway_metered_requests: int | None


class TeamTotal(Model):
    billed_team: str
    total_requests: int
    total_tokens: int
    net_total_chargeback_usd: float


class Usage(Model):
    source: DataSourceName
    month: str = Field(description="YYYY-MM")
    team: str | None
    rows: list[ChargebackRow]
    totals: list[TeamTotal]


class AuditEntry(Model):
    timestamp: datetime
    principal_email: str | None
    method_name: str | None
    resource_name: str | None
    service_name: str | None
    status_message: str | None


class Audit(Model):
    source: DataSourceName
    days: int
    entries: list[AuditEntry]


# ---------------------------------------------------------------------------------------------
# traces
# ---------------------------------------------------------------------------------------------


class TraceSummary(Model):
    trace_id: str
    started_at: datetime
    agents: list[str]
    billed_team: str | None
    total_tokens: int


class TraceList(Model):
    source: DataSourceName
    days: int
    traces: list[TraceSummary]


class TraceSpan(Model):
    timestamp: datetime
    offset_ms: float = Field(description="Start of the span, relative to the start of the trace")
    duration_ms: float = Field(description="latency_ms of the record (0 when the record has none)")
    event: str | None
    source: str | None
    span_id: str | None
    workload_name: str | None
    workload_team: str | None
    caller_principal: str | None
    caller_team: str | None
    caller_verified: bool | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    tool_name: str | None
    tool_server: str | None
    tool_status: str | None
    gateway_product: str | None
    gateway_name: str | None
    http_status_code: int | None
    status: str | None
    error_type: str | None


class Trace(Model):
    source: DataSourceName
    trace_id: str
    started_at: datetime
    duration_ms: float
    total_tokens: int
    spans: list[TraceSpan]
