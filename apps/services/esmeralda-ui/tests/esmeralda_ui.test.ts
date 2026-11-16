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

import assert from "node:assert/strict";
import type { AddressInfo } from "node:net";
import test from "node:test";
import {
  redactUserCredential,
  validateUsageEvent,
} from "../src/contracts/schemas.ts";
import { clearCatalogCache, loadRuntimeCatalog } from "../src/server/catalogLoader.ts";
import { resolveCallerIdentity } from "../src/server/identity.ts";
import { createEsmeraldaUiServer } from "../src/server/index.ts";

test("redactUserCredential strips credential keys and masks user assertions by value", () => {
  const secretToken = "eyJhbGciOiJFUzI1NiJ9.SUPER_SECRET_USER_ASSERTION_VALUE.sig";
  const raw = {
    schema: "esmeralda.usage" as const,
    schema_version: 1 as const,
    event: "tool.call" as const,
    timestamp: "2026-01-15T14:03:07.412Z",
    source: "workload" as const,
    workload: {
      name: "legacy-dms",
      team: "ai-coe",
      runtime: "cloud_run" as const,
      framework: "custom",
      cloud: "gcp" as const,
    },
    tool: {
      name: "get_document",
      server: "legacy-dms",
      status: "ok" as const,
      duration_ms: 120,
    },
    authorization: `Bearer ${secretToken}`,
    user_auth_token: secretToken,
    error_type: `PermissionError: failed for token ${secretToken}`,
  };

  const cleaned = redactUserCredential(raw, [secretToken]);
  const serialized = JSON.stringify(cleaned);
  assert.equal(serialized.includes(secretToken), false);
  assert.equal("authorization" in cleaned, false);
  assert.equal("user_auth_token" in cleaned, false);
  assert.equal(validateUsageEvent(cleaned).length, 0);
});

test("resolveCallerIdentity supports Google Workspace IAP and Identity Platform (GCIP) JWTs", () => {
  const gcipPayload = Buffer.from(
    JSON.stringify({
      sub: "gcip-user-123",
      gcip: {
        firebase: {
          identities: {
            email: ["external-partner@customer.example.com"],
          },
        },
      },
    })
  ).toString("base64url");
  const fakeGcipJwt = `eyJhbGciOiJFUzI1NiJ9.${gcipPayload}.sig`;

  const resolved = resolveCallerIdentity(
    { "x-goog-iap-jwt-assertion": fakeGcipJwt },
    undefined,
    { ENVIRONMENT: "prd", ESMERALDA_UI_ALLOW_PERSONA_OVERRIDE: "false" }
  );
  assert.equal(resolved.email, "external-partner@customer.example.com");
  assert.equal(resolved.iap_jwt_present, true);
  assert.equal(resolved.persona_override_active, false);
});

test("esmeralda-ui HTTP server serves /health, /api/catalog, /api/invoke, /api/traces/:id, and /api/admin/summary", async () => {
  clearCatalogCache();
  const server = createEsmeraldaUiServer({
    NODE_ENV: "test",
    ENVIRONMENT: "dev",
    ENTRYPOINT_DOMAIN: "esmeralda.internal",
    ESMERALDA_UI_ALLOW_PERSONA_OVERRIDE: "true",
  });

  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as AddressInfo).port;
  const base = `http://127.0.0.1:${port}`;

  try {
    // 1. /health
    const healthRes = await fetch(`${base}/health`);
    assert.equal(healthRes.status, 200);
    const health = (await healthRes.json()) as { status: string; service: string };
    assert.equal(health.status, "healthy");
    assert.equal(health.service, "esmeralda-ui");

    // 2. /api/catalog
    const catRes = await fetch(`${base}/api/catalog`);
    assert.equal(catRes.status, 200);
    const catData = (await catRes.json()) as {
      catalog: { schema: string; targets: Record<string, unknown> };
      scenarios: Array<{ id: string }>;
    };
    assert.equal(catData.catalog.schema, "esmeralda.runtime_catalog");
    assert.ok("cx-mortgage-orchestrator" in catData.catalog.targets);
    assert.ok("legacy-dms" in catData.catalog.targets);
    assert.ok(catData.scenarios.length >= 4);

    // 3. /api/invoke (Act 1 Happy Path)
    const invRes = await fetch(`${base}/api/invoke`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target: "cx-mortgage-orchestrator",
        ingress: "direct",
        message: "Underwrite Julian Sterling",
        scenario_id: "act-1-happy-path",
      }),
    });
    assert.equal(invRes.status, 200);
    const invData = (await invRes.json()) as {
      ok: boolean;
      trace_id: string;
      usage_event: { schema: string };
    };
    assert.equal(invData.ok, true);
    assert.equal(invData.usage_event.schema, "esmeralda.usage");

    // 4. /api/traces/:traceId
    const traceRes = await fetch(`${base}/api/traces/${invData.trace_id}`);
    assert.equal(traceRes.status, 200);
    const traceData = (await traceRes.json()) as {
      trace_id: string;
      credential_redaction_verified: boolean;
      hops: unknown[];
    };
    assert.equal(traceData.trace_id, invData.trace_id);
    assert.equal(traceData.credential_redaction_verified, true);
    assert.ok(traceData.hops.length >= 4);

    // 5. /api/admin/summary
    const adminRes = await fetch(`${base}/api/admin/summary`);
    assert.equal(adminRes.status, 200);
    const adminData = (await adminRes.json()) as {
      capability_matrix: unknown[];
      targets: unknown[];
      chargeback: unknown[];
      security_audit: unknown[];
    };
    assert.equal(adminData.capability_matrix.length, 6);
    assert.ok(adminData.targets.length >= 5);
    assert.ok(adminData.chargeback.length >= 2);
    assert.ok(adminData.security_audit.length >= 3);
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
