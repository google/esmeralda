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
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { loadRuntimeCatalog } from "./catalogLoader.ts";
import { POLICY_VERIFICATION_SCENARIOS } from "./fixtures.ts";
import {
  isPersonaOverrideAllowed,
  resolveCallerIdentity,
  SYNTHETIC_PERSONAS,
} from "./identity.ts";
import {
  invokeCatalogTarget,
  listRecentTraces,
  type InvokeRequestPayload,
} from "./invoker.ts";
import { buildAdminSummary, resolveTraceById } from "./traceService.ts";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CLIENT_HTML_PATH = path.resolve(__dirname, "../client/index.html");

function sendJson(res: http.ServerResponse, status: number, payload: unknown): void {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
  });
  res.end(body);
}

async function readJsonBody(req: http.IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  const raw = Buffer.concat(chunks).toString("utf-8").trim();
  if (!raw) return {};
  return JSON.parse(raw) as Record<string, unknown>;
}

export function createEsmeraldaUiServer(env: NodeJS.ProcessEnv = process.env): http.Server {
  return http.createServer(async (req, res) => {
    try {
      const method = req.method || "GET";
      const parsedUrl = new URL(req.url || "/", "http://localhost");
      const pathname = parsedUrl.pathname;

      if (method === "GET" && pathname === "/health") {
        sendJson(res, 200, {
          status: "healthy",
          service: "esmeralda-ui",
          runtime: "nodejs-typescript",
          environment: env.ENVIRONMENT || "dev",
        });
        return;
      }

      if (method === "GET" && pathname === "/api/catalog") {
        const refresh = parsedUrl.searchParams.get("refresh") === "true";
        const catalog = await loadRuntimeCatalog(env, refresh);
        const identity = resolveCallerIdentity(
          req.headers,
          parsedUrl.searchParams.get("persona") || undefined,
          env
        );
        sendJson(res, 200, {
          catalog,
          caller_identity: identity,
          persona_override_allowed: isPersonaOverrideAllowed(env),
          personas: Object.values(SYNTHETIC_PERSONAS),
          scenarios: POLICY_VERIFICATION_SCENARIOS,
        });
        return;
      }

      if (method === "GET" && pathname === "/api/scenarios") {
        sendJson(res, 200, {
          scenarios: POLICY_VERIFICATION_SCENARIOS,
          personas: Object.values(SYNTHETIC_PERSONAS),
        });
        return;
      }

      if (method === "POST" && pathname === "/api/invoke") {
        const body = (await readJsonBody(req)) as unknown as InvokeRequestPayload;
        if (!body.target || !body.message) {
          sendJson(res, 400, {
            error: "Missing required fields: 'target' and 'message'.",
          });
          return;
        }
        const catalog = await loadRuntimeCatalog(env);
        const identity = resolveCallerIdentity(req.headers, body.persona_id, env);
        try {
          const result = await invokeCatalogTarget(catalog, body, identity, env);
          sendJson(res, result.http_status, result);
        } catch (err) {
          sendJson(res, 404, {
            error: (err as Error).message,
          });
        }
        return;
      }

      if (method === "GET" && pathname === "/api/traces") {
        sendJson(res, 200, {
          traces: listRecentTraces(),
        });
        return;
      }

      if (method === "GET" && pathname.startsWith("/api/traces/")) {
        const traceId = decodeURIComponent(pathname.slice("/api/traces/".length));
        const trace = await resolveTraceById(traceId, env);
        if (!trace) {
          sendJson(res, 404, { error: `Trace '${traceId}' not found.` });
          return;
        }
        sendJson(res, 200, trace);
        return;
      }

      if (method === "GET" && pathname === "/api/admin/summary") {
        const catalog = await loadRuntimeCatalog(env);
        const summary = await buildAdminSummary(catalog);
        sendJson(res, 200, summary);
        return;
      }

      // Serve SPA UI for `/`, `/chat`, `/trace/*`, `/admin`
      if (
        method === "GET" &&
        (pathname === "/" ||
          pathname === "/chat" ||
          pathname.startsWith("/trace") ||
          pathname === "/admin")
      ) {
        const html = fs.readFileSync(CLIENT_HTML_PATH, "utf-8");
        res.writeHead(200, {
          "content-type": "text/html; charset=utf-8",
          "cache-control": "no-store",
        });
        res.end(html);
        return;
      }

      sendJson(res, 404, { error: "Not found" });
    } catch (err) {
      sendJson(res, 500, {
        error: "Internal server error",
        detail: (err as Error).message,
      });
    }
  });
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const port = Number(process.env.PORT || 8080);
  const host = process.env.HOST || "0.0.0.0";
  const server = createEsmeraldaUiServer(process.env);
  server.listen(port, host, () => {
    process.stdout.write(
      JSON.stringify({
        event: "esmeralda.ui.started",
        host,
        port,
        url: `http://localhost:${port}`,
      }) + "\n"
    );
  });
}
