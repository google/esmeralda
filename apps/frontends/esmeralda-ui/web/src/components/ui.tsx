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

import type { ReactNode } from "react";
import { ApiError } from "../api/client";
import type { Async } from "../hooks/useAsync";

export function DemoBadge() {
  return (
    <span className="badge badge-demo" role="status" title="Synthetic data: demo mode is on">
      Demo data
    </span>
  );
}

/** A "Demo data" badge whenever a response says `source: "demo"`. */
export function SourceBadge({ source }: { source: string }) {
  return source === "demo" ? <DemoBadge /> : <span className="badge badge-live">Live · BigQuery</span>;
}

export function Badge({ tone = "neutral", children }: { tone?: "neutral" | "ok" | "warn" | "err"; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

function describe(error: Error): { title: string; detail: string } {
  if (error instanceof ApiError) {
    if (error.status === 401) return { title: "Not signed in", detail: "Your identity could not be verified." };
    if (error.status === 404) return { title: "Not found", detail: error.message };
    if (error.status === 422) return { title: "Invalid request", detail: error.message };
    if (error.status === 503) {
      return { title: "Data unavailable", detail: `${error.message}. No data is shown rather than made-up data.` };
    }
    return { title: `Error ${error.status}`, detail: error.message };
  }
  return { title: "Request failed", detail: error.message };
}

export function ErrorState({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  const { title, detail } = describe(error);
  return (
    <div className="state state-error" role="alert">
      <strong>{title}</strong>
      <span>{detail}</span>
      {onRetry ? (
        <button type="button" className="btn" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="state state-empty">{children}</div>;
}

/** Renders loading / error states of an async value, else `children(data)`. */
export function Loaded<T>({
  value,
  children,
}: {
  value: Async<T> & { reload: () => void };
  children: (data: T) => ReactNode;
}) {
  if (value.state === "loading") {
    return (
      <div className="state" aria-busy="true">
        Loading…
      </div>
    );
  }
  if (value.state === "error") return <ErrorState error={value.error} onRetry={value.reload} />;
  return <>{children(value.data)}</>;
}

export function Card({ title, actions, children }: { title: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      <header className="card-header">
        <h2>{title}</h2>
        {actions ? <div className="card-actions">{actions}</div> : null}
      </header>
      <div className="card-body">{children}</div>
    </section>
  );
}

export const fmt = {
  int: (n: number | null | undefined) => (n == null ? "–" : new Intl.NumberFormat("en-US").format(n)),
  usd: (n: number | null | undefined) =>
    n == null
      ? "–"
      : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 4 }).format(n),
  pct: (n: number | null | undefined) => (n == null ? "–" : `${n.toFixed(1)}%`),
  ms: (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(2)} s` : `${Math.round(n)} ms`),
  time: (iso: string) => new Date(iso).toLocaleString("en-GB", { timeZone: "UTC", hour12: false }) + " UTC",
};
