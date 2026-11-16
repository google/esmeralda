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

import { useMemo, useState } from "react";
import { api, type Catalog, type CatalogTarget } from "../api/client";
import { Badge, Card, EmptyState, Loaded } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { href } from "../hooks/useRoute";

function TargetDetail({ target }: { target: CatalogTarget }) {
  return (
    <Card
      title={
        <>
          {target.name} <Badge>{target.kind}</Badge>
        </>
      }
      actions={
        <a className="btn" href={href({ view: "catalog" })}>
          Close
        </a>
      }
    >
      {target.description ? <p className="muted">{target.description}</p> : null}
      <dl className="facts">
        <dt>Owner team</dt>
        <dd>{target.owner_team}</dd>
        <dt>Deploys as</dt>
        <dd className="mono">{target.deploy ?? "–"}</dd>
        <dt>Gateway</dt>
        <dd>{target.gateway ?? "–"}</dd>
        <dt>Entry-point backend</dt>
        <dd>{target.ingress_backend ?? "–"}</dd>
        <dt>Agent Registry</dt>
        <dd>{target.registry ?? (target.compiled ? "per-target entry" : "–")}</dd>
        {target.google_apis && target.google_apis.length > 0 ? (
          <>
            <dt>Google APIs</dt>
            <dd>{target.google_apis.join(", ")}</dd>
          </>
        ) : null}
      </dl>
      <h3>Who may call it, and who pays</h3>
      {target.callers.length === 0 ? (
        <EmptyState>No callers: nothing can reach this target.</EmptyState>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Caller</th>
              <th>Identity</th>
              <th>Via</th>
              <th>Pays</th>
              <th>Enforced</th>
            </tr>
          </thead>
          <tbody>
            {target.callers.map((c) => (
              <tr key={c.caller}>
                <td className="mono">{c.caller}</td>
                <td>{c.identity}</td>
                <td>{c.gateway ?? "–"}</td>
                <td>
                  {c.pays.team ?? "–"}
                  {c.pays.cost_centers.length > 0 ? (
                    <span className="muted"> ({c.pays.cost_centers.join(", ")})</span>
                  ) : null}
                </td>
                <td>{c.enforced ? <Badge tone="ok">enforced</Badge> : <Badge tone="warn">declared</Badge>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function CatalogTable({ catalog, selected }: { catalog: Catalog; selected: string | undefined }) {
  const [filter, setFilter] = useState("");
  const [team, setTeam] = useState("");
  const targets = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return catalog.targets.filter(
      (t) =>
        (!team || t.owner_team === team) &&
        (!needle || t.name.includes(needle) || (t.description ?? "").toLowerCase().includes(needle)),
    );
  }, [catalog, filter, team]);
  const current = selected ? catalog.targets.find((t) => t.name === selected) : undefined;

  return (
    <div className="stack">
      <Card
        title={`Targets (${targets.length})`}
        actions={
          <>
            <input
              className="input"
              type="search"
              placeholder="Filter targets"
              aria-label="Filter targets"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            <select className="input" aria-label="Owner team" value={team} onChange={(e) => setTeam(e.target.value)}>
              <option value="">All teams</option>
              {catalog.teams.map((t) => (
                <option key={t.name} value={t.name}>
                  {t.name}
                </option>
              ))}
            </select>
          </>
        }
      >
        <p className="muted small">
          From <span className="mono">{catalog.resolved_file}</span> (sha256 {catalog.resolved_sha256.slice(0, 12)}),
          policy: unmapped callers {catalog.policy.unmapped_caller ?? "–"}, declared mismatch{" "}
          {catalog.policy.declared_mismatch ?? "–"}.
        </p>
        {targets.length === 0 ? (
          <EmptyState>No target matches.</EmptyState>
        ) : (
          <table className="table table-hover">
            <thead>
              <tr>
                <th>Target</th>
                <th>Kind</th>
                <th>Owner</th>
                <th>Gateway</th>
                <th>Callers</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {targets.map((t) => (
                <tr key={t.name} className={t.name === selected ? "selected" : undefined}>
                  <td>
                    <a href={href({ view: "catalog", target: t.name })} className="mono">
                      {t.name}
                    </a>
                  </td>
                  <td>{t.kind}</td>
                  <td>{t.owner_team}</td>
                  <td>{t.gateway ?? "–"}</td>
                  <td>{t.callers.length}</td>
                  <td>
                    {t.example ? <Badge>example</Badge> : null}
                    {!t.compiled && !t.registry ? <Badge tone="warn">not compiled</Badge> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {selected && !current ? <EmptyState>Unknown target: {selected}</EmptyState> : null}
      {current ? <TargetDetail target={current} /> : null}
    </div>
  );
}

export function CatalogView({ selected }: { selected?: string | undefined }) {
  const catalog = useAsync((signal) => api.catalog(signal), []);
  return <Loaded value={catalog}>{(data) => <CatalogTable catalog={data} selected={selected} />}</Loaded>;
}
