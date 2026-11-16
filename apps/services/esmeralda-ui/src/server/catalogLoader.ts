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

import fs from "node:fs";
import path from "node:path";
import type {
  RuntimeCatalogDocument,
  RuntimeCatalogTarget,
  TargetCallerGrant,
} from "../contracts/schemas.ts";
import { buildSyntheticRuntimeCatalog } from "./fixtures.ts";
import { fetchAccessToken } from "./gcpAuth.ts";

/**
 * Runtime Catalog Loader (`/api/catalog`) with a 60-second in-memory TTL cache.
 *
 * Resolution priority:
 * 1. Explicit `ESMERALDA_RUNTIME_CATALOG_JSON` environment variable (mounted Secret Manager payload or test injection).
 * 2. Governance Secret Manager API (`projects/<governance_project>/secrets/esmeralda-runtime-catalog-<env>/versions/latest:access`).
 * 3. Local resolved catalog file (`ESMERALDA_RESOLVED_CATALOG_PATH` or `catalog/.resolved/<env>.json`), adapting the
 *    Terraform-compiled shape into a `RuntimeCatalogDocument`.
 * 4. Built-in synthetic catalog fixture (`buildSyntheticRuntimeCatalog`) for offline execution and unit tests.
 */

const CACHE_TTL_MS = 60_000;

interface CacheEntry {
  expiresAt: number;
  document: RuntimeCatalogDocument;
}

let catalogCache: CacheEntry | null = null;

export function clearCatalogCache(): void {
  catalogCache = null;
}

/**
 * Adapts either an already-bound `esmeralda.runtime_catalog` document (written by Layer 5 `iap-egress`)
 * or the repository's `catalog/.resolved/<env>.json` document into a normalized `RuntimeCatalogDocument`.
 */
export function normalizeCatalogDocument(
  raw: Record<string, unknown>,
  environment: string,
  domain: string,
  source: RuntimeCatalogDocument["source"]
): RuntimeCatalogDocument {
  const fallback = buildSyntheticRuntimeCatalog(environment, domain);

  if (raw.schema === "esmeralda.runtime_catalog" && raw.targets && typeof raw.targets === "object") {
    const targetsMap: Record<string, RuntimeCatalogTarget> = {};
    for (const [name, val] of Object.entries(raw.targets as Record<string, unknown>)) {
      if (!val || typeof val !== "object") continue;
      const t = val as Partial<RuntimeCatalogTarget>;
      const fallbackTarget = fallback.targets[name];
      const kind = t.kind || fallbackTarget?.kind || "mcp";
      targetsMap[name] = {
        name,
        kind,
        description: t.description || fallbackTarget?.description || `${kind.toUpperCase()} target ${name}`,
        owner_team: t.owner_team || fallbackTarget?.owner_team || (name.startsWith("cx-") ? "cx" : "ai-coe"),
        unit: t.unit || fallbackTarget?.unit,
        managed: t.managed ?? true,
        endpoint:
          t.endpoint ||
          fallbackTarget?.endpoint ||
          (kind === "mcp" ? `https://${name}.${domain}/mcp` : `https://${name}.${domain}`),
        partner_endpoint: t.partner_endpoint || fallbackTarget?.partner_endpoint,
        token: t.token || (kind === "mcp" ? "id" : "access"),
        probe: t.probe || (kind === "mcp" ? "mcp" : kind === "a2a" ? "a2a" : "query"),
        timeout: t.timeout,
        gateway: t.gateway || fallbackTarget?.gateway || "agent_gateway",
        user_credential: t.user_credential || fallbackTarget?.user_credential || { accept: false },
        models: t.models || fallbackTarget?.models,
        observability: t.observability || fallbackTarget?.observability,
        callers: Array.isArray((t as Record<string, unknown>).resolved_callers)
          ? ((t as Record<string, unknown>).resolved_callers as TargetCallerGrant[])
          : Array.isArray(t.callers) && typeof t.callers[0] === "object"
          ? (t.callers as TargetCallerGrant[])
          : fallbackTarget?.callers || [],
        registry_synced: t.registry_synced ?? true,
        registry_resource:
          t.registry_resource ||
          fallbackTarget?.registry_resource ||
          `projects/esm-${environment}-governance/locations/us-central1/services/${name}`,
      };
    }

    return {
      schema: "esmeralda.runtime_catalog",
      schema_version: 1,
      environment: typeof raw.environment === "string" ? raw.environment : environment,
      domain: typeof raw.domain === "string" ? raw.domain : domain,
      generated_at: typeof raw.generated_at === "string" ? raw.generated_at : new Date().toISOString(),
      source,
      capabilities:
        raw.capabilities && typeof raw.capabilities === "object"
          ? (raw.capabilities as RuntimeCatalogDocument["capabilities"])
          : fallback.capabilities,
      attribution_policy:
        raw.attribution_policy && typeof raw.attribution_policy === "object"
          ? (raw.attribution_policy as RuntimeCatalogDocument["attribution_policy"])
          : fallback.attribution_policy,
      teams:
        raw.teams && typeof raw.teams === "object"
          ? (raw.teams as RuntimeCatalogDocument["teams"])
          : fallback.teams,
      dimensions:
        raw.dimensions && typeof raw.dimensions === "object"
          ? (raw.dimensions as RuntimeCatalogDocument["dimensions"])
          : fallback.dimensions,
      targets: targetsMap,
      principal_teams: Array.isArray(raw.principal_teams)
        ? (raw.principal_teams as RuntimeCatalogDocument["principal_teams"])
        : fallback.principal_teams,
    };
  }

  // Adapt `catalog/.resolved/<env>.json` (`catalog`, `attribution`, `external_targets`)
  const rawCatalog = (raw.catalog || raw.targets || {}) as Record<string, Record<string, unknown>>;
  const rawAttribution = (raw.attribution || {}) as Record<string, unknown>;
  const allowlists = (rawAttribution.allowlists || {}) as Record<string, Array<Record<string, unknown>>>;
  const principalTeams = Array.isArray(rawAttribution.principal_teams)
    ? (rawAttribution.principal_teams as RuntimeCatalogDocument["principal_teams"])
    : fallback.principal_teams;

  const targetsMap: Record<string, RuntimeCatalogTarget> = {};
  for (const [name, entry] of Object.entries(rawCatalog)) {
    const fallbackTarget = fallback.targets[name];
    const kind = (typeof entry.kind === "string" ? entry.kind : fallbackTarget?.kind || "mcp") as RuntimeCatalogTarget["kind"];
    const allowlistEntries = allowlists[name] || [];
    const callers: TargetCallerGrant[] =
      allowlistEntries.length > 0
        ? allowlistEntries.map((item) => ({
            caller: String(item.caller || ""),
            team: typeof item.team === "string" ? item.team : undefined,
            principal: typeof item.principal === "string" ? item.principal : undefined,
            cost_centers: Array.isArray(item.cost_centers) ? (item.cost_centers as string[]) : undefined,
            gateway: String(item.caller || "").includes("apigee") ? "apigee" : "agent_gateway",
          }))
        : fallbackTarget?.callers || [];

    targetsMap[name] = {
      name,
      kind,
      description:
        (typeof entry.description === "string" ? entry.description : undefined) ||
        fallbackTarget?.description ||
        `${kind.toUpperCase()} target (${String(entry.unit || name)})`,
      owner_team:
        (typeof entry.owner_team === "string" ? entry.owner_team : undefined) ||
        fallbackTarget?.owner_team ||
        (name.startsWith("cx-") ? "cx" : "ai-coe"),
      unit: typeof entry.unit === "string" ? entry.unit : fallbackTarget?.unit,
      managed: true,
      endpoint:
        fallbackTarget?.endpoint ||
        (kind === "mcp" ? `https://${name}.${domain}/mcp` : `https://${name}.${domain}`),
      partner_endpoint: fallbackTarget?.partner_endpoint,
      token: (entry.token === "id" || entry.token === "access" ? entry.token : fallbackTarget?.token || "id"),
      probe: (entry.probe as RuntimeCatalogTarget["probe"]) || fallbackTarget?.probe || "mcp",
      timeout: typeof entry.timeout === "number" ? entry.timeout : fallbackTarget?.timeout,
      gateway: fallbackTarget?.gateway || (kind === "adk" ? "direct" : "agent_gateway"),
      user_credential: fallbackTarget?.user_credential || { accept: false },
      models: fallbackTarget?.models,
      observability: fallbackTarget?.observability,
      callers,
      registry_synced: true,
      registry_resource:
        fallbackTarget?.registry_resource ||
        `projects/esm-${environment}-governance/locations/us-central1/services/${name}`,
    };
  }

  return {
    schema: "esmeralda.runtime_catalog",
    schema_version: 1,
    environment: typeof raw.environment === "string" ? raw.environment : environment,
    domain,
    generated_at: new Date().toISOString(),
    source,
    capabilities: fallback.capabilities,
    attribution_policy:
      rawAttribution.policy && typeof rawAttribution.policy === "object"
        ? (rawAttribution.policy as RuntimeCatalogDocument["attribution_policy"])
        : fallback.attribution_policy,
    teams: fallback.teams,
    dimensions: fallback.dimensions,
    targets: Object.keys(targetsMap).length > 0 ? targetsMap : fallback.targets,
    principal_teams: principalTeams,
  };
}

async function fetchCatalogFromSecretManager(
  governanceProjectId: string,
  secretId: string
): Promise<Record<string, unknown> | null> {
  const token = await fetchAccessToken();
  if (!token) {
    return null;
  }
  const url = `https://secretmanager.googleapis.com/v1/projects/${encodeURIComponent(
    governanceProjectId
  )}/secrets/${encodeURIComponent(secretId)}/versions/latest:access`;
  try {
    const res = await fetch(url, {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(4000),
    });
    if (!res.ok) return null;
    const body = (await res.json()) as { payload?: { data?: string } };
    if (!body?.payload?.data) return null;
    const decoded = Buffer.from(body.payload.data, "base64").toString("utf-8");
    return JSON.parse(decoded) as Record<string, unknown>;
  } catch {
    return null;
  }
}

export async function loadRuntimeCatalog(
  env: NodeJS.ProcessEnv = process.env,
  forceRefresh = false
): Promise<RuntimeCatalogDocument> {
  const now = Date.now();
  if (!forceRefresh && catalogCache && catalogCache.expiresAt > now) {
    return catalogCache.document;
  }

  const environment = (env.ENVIRONMENT || "dev").trim();
  const domain = (env.ENTRYPOINT_DOMAIN || "esmeralda.internal").trim();

  // 1. Direct JSON env var (mounted Secret Manager volume/env or unit test)
  const inlineJson = (env.ESMERALDA_RUNTIME_CATALOG_JSON || "").trim();
  if (inlineJson) {
    try {
      const parsed = JSON.parse(inlineJson) as Record<string, unknown>;
      const doc = normalizeCatalogDocument(parsed, environment, domain, "secret_manager");
      catalogCache = { expiresAt: now + CACHE_TTL_MS, document: doc };
      return doc;
    } catch {
      // Fall through on malformed inline JSON
    }
  }

  // 2. Live Governance Secret Manager API
  const govProject = (env.GOVERNANCE_PROJECT_ID || "").trim();
  const secretId = (env.RUNTIME_CATALOG_SECRET_ID || `esmeralda-runtime-catalog-${environment}`).trim();
  if (govProject) {
    const smPayload = await fetchCatalogFromSecretManager(govProject, secretId);
    if (smPayload) {
      const doc = normalizeCatalogDocument(smPayload, environment, domain, "secret_manager");
      catalogCache = { expiresAt: now + CACHE_TTL_MS, document: doc };
      return doc;
    }
  }

  // 3. Local resolved catalog JSON (`ESMERALDA_RESOLVED_CATALOG_PATH` or `catalog/.resolved/<env>.json`)
  const candidatePaths = [
    env.ESMERALDA_RESOLVED_CATALOG_PATH,
    path.resolve(process.cwd(), `catalog/.resolved/${environment}.json`),
    path.resolve(process.cwd(), `../../../catalog/.resolved/${environment}.json`),
  ].filter((p): p is string => Boolean(p && p.trim()));

  for (const filePath of candidatePaths) {
    try {
      if (fs.existsSync(filePath)) {
        const rawText = fs.readFileSync(filePath, "utf-8");
        const parsed = JSON.parse(rawText) as Record<string, unknown>;
        const doc = normalizeCatalogDocument(parsed, environment, domain, "resolved_file");
        catalogCache = { expiresAt: now + CACHE_TTL_MS, document: doc };
        return doc;
      }
    } catch {
      // Continue to next candidate
    }
  }

  // 4. Built-in synthetic fixture
  const synthetic = buildSyntheticRuntimeCatalog(environment, domain);
  catalogCache = { expiresAt: now + CACHE_TTL_MS, document: synthetic };
  return synthetic;
}
