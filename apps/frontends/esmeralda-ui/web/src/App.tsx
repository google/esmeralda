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

import { api, type Me } from "./api/client";
import { Badge, DemoBadge, ErrorState } from "./components/ui";
import { useAsync } from "./hooks/useAsync";
import { href, type Route, useRoute } from "./hooks/useRoute";
import { CatalogView } from "./views/CatalogView";
import { FinOpsView } from "./views/FinOpsView";
import { TracesView } from "./views/TracesView";

const NAV: { view: Route["view"]; label: string }[] = [
  { view: "catalog", label: "Service Catalog" },
  { view: "finops", label: "FinOps & Audit" },
  { view: "traces", label: "Traces" },
];

function Identity({ me }: { me: Me }) {
  return (
    <div className="identity">
      {me.auth === "local-dev" ? (
        <Badge tone="warn">Local dev identity (not verified)</Badge>
      ) : (
        <Badge tone="ok">IAP verified</Badge>
      )}
      <span className="identity-email" title={me.subject}>
        {me.email}
      </span>
    </div>
  );
}

export function App() {
  const route = useRoute();
  const me = useAsync((signal) => api.me(signal), []);

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            E
          </span>
          <span className="brand-title">Esmeralda</span>
          {me.state === "ok" ? <span className="env-pill">{me.data.environment}</span> : null}
        </div>
        <nav className="nav" aria-label="Views">
          {NAV.map((item) => (
            <a
              key={item.view}
              href={href({ view: item.view } as Route)}
              className={route.view === item.view ? "nav-item active" : "nav-item"}
              aria-current={route.view === item.view ? "page" : undefined}
            >
              {item.label}
            </a>
          ))}
        </nav>
        <footer className="sidebar-footer">Read-only console</footer>
      </aside>
      <div className="main">
        <header className="topbar">
          <h1>{NAV.find((n) => n.view === route.view)?.label}</h1>
          <div className="topbar-right">
            {me.state === "ok" && me.data.demo ? <DemoBadge /> : null}
            {me.state === "ok" ? <Identity me={me.data} /> : null}
          </div>
        </header>
        <main className="content">
          {me.state === "error" ? (
            <ErrorState error={me.error} onRetry={me.reload} />
          ) : me.state === "loading" ? (
            <div className="state" aria-busy="true">
              Loading…
            </div>
          ) : route.view === "catalog" ? (
            <CatalogView selected={route.target} />
          ) : route.view === "finops" ? (
            <FinOpsView />
          ) : (
            <TracesView traceId={route.traceId} />
          )}
        </main>
      </div>
    </div>
  );
}
