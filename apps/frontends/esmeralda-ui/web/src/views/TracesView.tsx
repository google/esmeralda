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

import { type FormEvent, useState } from "react";
import { api, type Trace, TRACE_ID_RE, type TraceList, type TraceSpan } from "../api/client";
import { Badge, Card, EmptyState, fmt, Loaded, SourceBadge } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { href } from "../hooks/useRoute";

function spanLabel(s: TraceSpan): string {
  if (s.tool_name) return `${s.tool_server ?? "tool"} · ${s.tool_name}`;
  if (s.event === "gateway.request") return `${s.gateway_product ?? "gateway"} → ${s.workload_name ?? "?"}`;
  if (s.model) return `${s.workload_name ?? "?"} · ${s.model}`;
  return `${s.workload_name ?? "?"} · ${s.event ?? "record"}`;
}

function Waterfall({ trace }: { trace: Trace }) {
  const total = Math.max(trace.duration_ms, 1);
  return (
    <Card
      title={
        <>
          Trace <span className="mono">{trace.trace_id}</span> <SourceBadge source={trace.source} />
        </>
      }
      actions={
        <a className="btn" href={href({ view: "traces" })}>
          Close
        </a>
      }
    >
      <p className="muted small">
        Started {fmt.time(trace.started_at)} · {fmt.ms(trace.duration_ms)} · {fmt.int(trace.total_tokens)} tokens ·{" "}
        {trace.spans.length} usage records
      </p>
      <ol className="waterfall" aria-label="Trace waterfall">
        {trace.spans.map((s, i) => (
          <li className="wf-row" key={`${s.span_id ?? "span"}-${i}`}>
            <div className="wf-label">
              <span className="mono">{spanLabel(s)}</span>
              <span className="wf-meta">
                <Badge>{s.event ?? "record"}</Badge>
                {s.caller_verified ? <Badge tone="ok">verified caller</Badge> : null}
                {s.http_status_code && s.http_status_code >= 400 ? <Badge tone="err">{s.http_status_code}</Badge> : null}
                {s.error_type ? <Badge tone="err">{s.error_type}</Badge> : null}
              </span>
            </div>
            <div className="wf-track">
              <span
                className={`wf-bar wf-${s.source ?? "other"}`}
                style={{
                  left: `${(s.offset_ms / total) * 100}%`,
                  width: `${Math.max((s.duration_ms / total) * 100, 0.5)}%`,
                }}
                title={`${fmt.ms(s.duration_ms)} at +${fmt.ms(s.offset_ms)}`}
              />
            </div>
            <div className="wf-numbers">
              {fmt.ms(s.duration_ms)}
              {s.input_tokens != null || s.output_tokens != null ? (
                <span className="muted">
                  {" "}
                  · {fmt.int(s.input_tokens)} in / {fmt.int(s.output_tokens)} out
                </span>
              ) : null}
            </div>
          </li>
        ))}
      </ol>
    </Card>
  );
}

function TraceDetail({ traceId }: { traceId: string }) {
  const trace = useAsync((signal) => api.trace(traceId, signal), [traceId]);
  return <Loaded value={trace}>{(data) => <Waterfall trace={data} />}</Loaded>;
}

function RecentTraces({ list }: { list: TraceList }) {
  if (list.traces.length === 0) return <EmptyState>No traced requests in the last {list.days} day(s).</EmptyState>;
  return (
    <table className="table table-hover">
      <thead>
        <tr>
          <th>Trace</th>
          <th>Started</th>
          <th>Agents</th>
          <th>Billed team</th>
          <th className="num">Tokens</th>
        </tr>
      </thead>
      <tbody>
        {list.traces.map((t) => (
          <tr key={t.trace_id}>
            <td>
              <a className="mono" href={href({ view: "traces", traceId: t.trace_id })}>
                {t.trace_id}
              </a>
            </td>
            <td className="nowrap">{fmt.time(t.started_at)}</td>
            <td>{t.agents.join(", ") || "–"}</td>
            <td>{t.billed_team ?? "–"}</td>
            <td className="num">{fmt.int(t.total_tokens)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function TracesView({ traceId }: { traceId?: string | undefined }) {
  const [days, setDays] = useState(1);
  const [lookup, setLookup] = useState("");
  const [lookupError, setLookupError] = useState<string | null>(null);
  const recent = useAsync((signal) => api.traces(days, signal), [days]);

  const onLookup = (event: FormEvent) => {
    event.preventDefault();
    const id = lookup.trim().toLowerCase();
    if (!TRACE_ID_RE.test(id)) {
      setLookupError("A trace id is 32 hexadecimal characters (W3C traceparent trace-id).");
      return;
    }
    setLookupError(null);
    window.location.hash = href({ view: "traces", traceId: id });
  };

  return (
    <div className="stack">
      <Card
        title={
          <>
            Recent traces {recent.state === "ok" ? <SourceBadge source={recent.data.source} /> : null}
          </>
        }
        actions={
          <>
            <form className="inline-form" onSubmit={onLookup}>
              <input
                className="input mono"
                type="text"
                aria-label="Trace id"
                placeholder="Trace id (32 hex)"
                value={lookup}
                maxLength={64}
                onChange={(e) => setLookup(e.target.value)}
              />
              <button className="btn" type="submit">
                Open
              </button>
            </form>
            <select className="input" aria-label="Trace window" value={days} onChange={(e) => setDays(Number(e.target.value))}>
              <option value={1}>Last 24 hours</option>
              <option value={7}>Last 7 days</option>
            </select>
          </>
        }
      >
        {lookupError ? (
          <p className="field-error" role="alert">
            {lookupError}
          </p>
        ) : null}
        <Loaded value={recent}>{(data) => <RecentTraces list={data} />}</Loaded>
      </Card>
      {traceId ? <TraceDetail traceId={traceId} /> : null}
    </div>
  );
}
