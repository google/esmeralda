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

"""Synthetic data for explicit demo mode (``esmeralda ui --local --demo`` or ``ESMERALDA_UI_DEMO=true``).

Every response built here says ``source = "demo"`` and the web badges it "Demo data". It is never
used as a fallback when BigQuery is unconfigured or failing. Names are fictional (the sample
mortgage use case: Julian Sterling, the cx and ai-coe teams); emails use ``example.com``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from esmeralda_ui.data import build_trace, chargeback_row, month_start, team_totals
from esmeralda_ui.models import Audit, AuditEntry, Trace, TraceList, TraceSummary, Usage

DEMO_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
_SECOND_TRACE_ID = "0af7651916cd43dd8448eb211c80319c"

_CHARGEBACK = [
    # agent, model, billed_team, verified, attribution, requests, llm_calls, tokens, cached, usd
    ("cx-mortgage-orchestrator", "gemini-flash", "cx", True, "verified", 412, 1236, 2_480_000, 610_000, 0.412),
    ("ai-coe-mortgage-specialist", "gemini-flash", "cx", True, "verified", 398, 1990, 3_910_000, 1_220_000, 0.655),
    ("ai-coe-mortgage-specialist", "gemini-flash", "platform", True, "verified", 37, 140, 260_000, 40_000, 0.047),
    ("ai-coe-mortgage-specialist", "gemini-flash", "unattributed", False, "declared", 5, 15, 31_000, 0, 0.006),
]


class DemoSource:
    def usage(self, month: str, team: str | None) -> Usage:
        start = month_start(month)
        rows = [
            chargeback_row(
                {
                    "billing_month": start,
                    "agent_id": agent,
                    "model": model,
                    "billed_team": billed,
                    "verified": verified,
                    "attribution": attribution,
                    "workload_team": "ai-coe" if agent.startswith("ai-coe") else "cx",
                    "total_requests": requests,
                    "total_llm_calls": calls,
                    "total_tokens": tokens,
                    "cached_prompt_tokens": cached,
                    "cache_hit_ratio_pct": round(cached * 100 / tokens, 2),
                    "net_total_chargeback_usd": usd,
                    "gateway_metered_requests": requests if verified else 0,
                }
            )
            for agent, model, billed, verified, attribution, requests, calls, tokens, cached, usd in _CHARGEBACK
            if team is None or billed == team
        ]
        return Usage(source="demo", month=month, team=team, rows=rows, totals=team_totals(rows))

    def audit(self, days: int, limit: int) -> Audit:
        now = datetime.now(UTC).replace(microsecond=0)
        entries = [
            AuditEntry(
                timestamp=now - timedelta(hours=2),
                principal_email="platform-deployer@example.com",
                method_name="google.cloud.aiplatform.v1.ReasoningEngineService.UpdateReasoningEngine",
                resource_name="reasoningEngines/cx-mortgage-orchestrator",
                service_name="aiplatform.googleapis.com",
                status_message="SUCCESS",
            ),
            AuditEntry(
                timestamp=now - timedelta(hours=26),
                principal_email="platform-deployer@example.com",
                method_name="SetIamPolicy",
                resource_name="services/legacy-dms",
                service_name="run.googleapis.com",
                status_message="SUCCESS",
            ),
            AuditEntry(
                timestamp=now - timedelta(days=3),
                principal_email="julian.sterling@example.com",
                method_name="google.cloud.secretmanager.v1.SecretManagerService.AccessSecretVersion",
                resource_name="secrets/partner-api-key/versions/latest",
                service_name="secretmanager.googleapis.com",
                status_message="Permission denied",
            ),
        ]
        cutoff = now - timedelta(days=days)
        return Audit(source="demo", days=days, entries=[e for e in entries if e.timestamp >= cutoff][:limit])

    def _records(self, trace_id: str) -> list[dict]:
        end = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=12)
        if trace_id == _SECOND_TRACE_ID:
            end -= timedelta(hours=5)
        base = {
            "caller_principal": "agents/cx-mortgage-orchestrator",
            "caller_team": "cx",
            "caller_verified": True,
        }
        return [
            {
                **base,
                "timestamp": end - timedelta(milliseconds=6200),
                "event": "llm.call",
                "source": "workload",
                "workload_name": "cx-mortgage-orchestrator",
                "workload_team": "cx",
                "request_model": "gemini-flash",
                "input_tokens": 1830,
                "output_tokens": 120,
                "latency_ms": 900,
            },
            {
                **base,
                "timestamp": end - timedelta(milliseconds=300),
                "event": "gateway.request",
                "source": "gateway",
                "workload_name": "ai-coe-mortgage-specialist",
                "workload_team": "ai-coe",
                "gateway_product": "agent-gateway",
                "gateway_name": "central",
                "http_status_code": 200,
                "status": "ok",
                "latency_ms": 5200,
            },
            {
                "timestamp": end - timedelta(milliseconds=2900),
                "event": "tool.call",
                "source": "workload",
                "workload_name": "ai-coe-mortgage-specialist",
                "workload_team": "ai-coe",
                "caller_principal": "agents/ai-coe-mortgage-specialist",
                "caller_team": "ai-coe",
                "caller_verified": True,
                "tool_name": "verify_income",
                "tool_server": "income-verification",
                "tool_status": "ok",
                "latency_ms": 640,
            },
            {
                "timestamp": end - timedelta(milliseconds=700),
                "event": "llm.call",
                "source": "workload",
                "workload_name": "ai-coe-mortgage-specialist",
                "workload_team": "ai-coe",
                "caller_principal": "agents/ai-coe-mortgage-specialist",
                "caller_team": "ai-coe",
                "caller_verified": True,
                "request_model": "gemini-flash",
                "input_tokens": 2410,
                "output_tokens": 380,
                "latency_ms": 1500,
            },
            {
                **base,
                "timestamp": end,
                "event": "llm.call",
                "source": "workload",
                "workload_name": "cx-mortgage-orchestrator",
                "workload_team": "cx",
                "request_model": "gemini-flash",
                "input_tokens": 2950,
                "output_tokens": 210,
                "latency_ms": 250,
            },
        ]

    def traces(self, days: int, limit: int) -> TraceList:
        summaries = []
        for trace_id in (DEMO_TRACE_ID, _SECOND_TRACE_ID):
            trace = self.trace(trace_id, days)
            if trace is None:
                continue
            summaries.append(
                TraceSummary(
                    trace_id=trace_id,
                    started_at=trace.started_at,
                    agents=sorted({s.workload_name for s in trace.spans if s.workload_name}),
                    billed_team="cx",
                    total_tokens=trace.total_tokens,
                )
            )
        return TraceList(source="demo", days=days, traces=summaries[:limit])

    def trace(self, trace_id: str, days: int) -> Trace | None:
        if trace_id not in (DEMO_TRACE_ID, _SECOND_TRACE_ID):
            return None
        return build_trace(trace_id, self._records(trace_id), "demo")
