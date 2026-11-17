// Copyright 2026 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

import { useState } from "react";
import { api, MONTH_RE, type Audit, type Usage } from "../api/client";
import { Badge, Card, EmptyState, fmt, Loaded, SourceBadge } from "../components/ui";
import { useAsync } from "../hooks/useAsync";

function currentMonth(): string {
  return new Date().toISOString().slice(0, 7);
}

const TEAM_RE = /^[a-z0-9][a-z0-9_-]{0,62}$/;

function UsageTables({ usage }: { usage: Usage }) {
  const max = Math.max(...usage.totals.map((t) => t.net_total_chargeback_usd), 0);
  if (usage.rows.length === 0) {
    return <EmptyState>No billable usage for {usage.month}{usage.team ? ` and team ${usage.team}` : ""}.</EmptyState>;
  }
  return (
    <>
      <h3>By billed team</h3>
      <div className="bars">
        {usage.totals.map((t) => (
          <div className="bar-row" key={t.billed_team}>
            <span className="bar-label mono">{t.billed_team}</span>
            <span className="bar-track">
              <span
                className="bar-fill"
                style={{ width: `${max > 0 ? Math.max((t.net_total_chargeback_usd / max) * 100, 1) : 0}%` }}
              />
            </span>
            <span className="bar-value">{fmt.usd(t.net_total_chargeback_usd)}</span>
          </div>
        ))}
      </div>
      <h3>By agent and model</h3>
      <table className="table">
        <thead>
          <tr>
            <th>Agent</th>
            <th>Model</th>
            <th>Billed team</th>
            <th>Attribution</th>
            <th className="num">Requests</th>
            <th className="num">Tokens</th>
            <th className="num">Cache hit</th>
            <th className="num">Estimated cost</th>
          </tr>
        </thead>
        <tbody>
          {usage.rows.map((r, i) => (
            <tr key={`${r.agent_id ?? "?"}-${r.model ?? "?"}-${r.billed_team ?? "?"}-${i}`}>
              <td className="mono">{r.agent_id ?? "–"}</td>
              <td>{r.model ?? "–"}</td>
              <td>{r.billed_team ?? "–"}</td>
              <td>
                {r.verified ? <Badge tone="ok">verified</Badge> : <Badge tone="warn">{r.attribution ?? "unknown"}</Badge>}
              </td>
              <td className="num">{fmt.int(r.total_requests)}</td>
              <td className="num">{fmt.int(r.total_tokens)}</td>
              <td className="num">{fmt.pct(r.cache_hit_ratio_pct)}</td>
              <td className="num">{fmt.usd(r.net_total_chargeback_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">Costs are estimates from the view's per-token rates (vw_monthly_agent_chargeback).</p>
    </>
  );
}

function AuditTable({ audit }: { audit: Audit }) {
  if (audit.entries.length === 0) return <EmptyState>No audited changes in the last {audit.days} days.</EmptyState>;
  return (
    <table className="table">
      <thead>
        <tr>
          <th>When</th>
          <th>Principal</th>
          <th>Method</th>
          <th>Resource</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {audit.entries.map((e, i) => (
          <tr key={`${e.timestamp}-${i}`}>
            <td className="nowrap">{fmt.time(e.timestamp)}</td>
            <td>{e.principal_email ?? "–"}</td>
            <td className="mono small">{e.method_name ?? "–"}</td>
            <td className="mono small">{e.resource_name ?? "–"}</td>
            <td>
              {e.status_message === "SUCCESS" ? (
                <Badge tone="ok">success</Badge>
              ) : (
                <Badge tone="err">{e.status_message ?? "unknown"}</Badge>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function FinOpsView() {
  const [month, setMonth] = useState(currentMonth);
  const [team, setTeam] = useState("");
  const [days, setDays] = useState(7);
  const validMonth = MONTH_RE.test(month) ? month : undefined;
  const validTeam = TEAM_RE.test(team) ? team : undefined;
  const usage = useAsync((signal) => api.usage(validMonth, validTeam, signal), [validMonth, validTeam]);
  const audit = useAsync((signal) => api.audit(days, signal), [days]);

  return (
    <div className="stack">
      <Card
        title={
          <>
            Chargeback {usage.state === "ok" ? <SourceBadge source={usage.data.source} /> : null}
          </>
        }
        actions={
          <>
            <input
              className="input"
              type="month"
              aria-label="Billing month"
              value={month}
              onChange={(e) => setMonth(e.target.value)}
            />
            <input
              className="input"
              type="text"
              aria-label="Billed team"
              placeholder="All teams"
              value={team}
              onChange={(e) => setTeam(e.target.value.trim().toLowerCase())}
            />
          </>
        }
      >
        {team && !validTeam ? <p className="field-error">Team names are lowercase letters, digits, - and _.</p> : null}
        <Loaded value={usage}>{(data) => <UsageTables usage={data} />}</Loaded>
      </Card>
      <Card
        title={
          <>
            Security audit trail {audit.state === "ok" ? <SourceBadge source={audit.data.source} /> : null}
          </>
        }
        actions={
          <select className="input" aria-label="Audit window" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={1}>Last 24 hours</option>
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
          </select>
        }
      >
        <Loaded value={audit}>{(data) => <AuditTable audit={data} />}</Loaded>
      </Card>
    </div>
  );
}
