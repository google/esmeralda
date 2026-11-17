# esmeralda-ui: the Esmeralda console

A read-only web console for the platform: **Service Catalog**, **FinOps & Audit** and **Traces**.
Full documentation: [2.7 Esmeralda Console](../../../docs/src/3-agentops-and-lifecycle/07-esmeralda-console.md).

| Path | What lives there |
|---|---|
| `api/` | The BFF: a read-only FastAPI app (workspace package `esmeralda-ui`, module `esmeralda_ui`) over the `esmeralda` package. IAP JWT verification, security headers, parameterized BigQuery queries. |
| `web/` | The React + TypeScript (strict) + Vite single-page app. Types in `web/src/api/schema.ts` are generated from `openapi.json`. |
| `openapi.json` | The API contract, generated from the FastAPI app. |
| `Dockerfile` | One image: Node builds `web/`, Python runs the BFF as non-root and serves `/api/*` plus the static files. Build from the repository root: `docker build -f apps/frontends/esmeralda-ui/Dockerfile .` |

## Run locally

```bash
npm ci --prefix apps/frontends/esmeralda-ui/web
npm run build --prefix apps/frontends/esmeralda-ui/web
uv run esmeralda ui --local --demo
```

`--demo` serves synthetic data (badged "Demo data" in every view); `--bigquery --env dev` reads the
environment's BigQuery views read-only with your Application Default Credentials.

## Change the API

1. Edit the models or endpoints in `api/src/esmeralda_ui/`.
2. `uv run --package esmeralda-ui python -m esmeralda_ui.openapi` rewrites `openapi.json`.
3. `npm run gen:types --prefix apps/frontends/esmeralda-ui/web` regenerates `web/src/api/schema.ts`.

The BFF tests fail when `openapi.json` is stale, and `npm test` fails when `schema.ts` is stale.

## Test

```bash
make test-ui
```

## Dependencies

* Python: declared in `api/pyproject.toml`, locked in the workspace `uv.lock` (`make lock`). Never add
  web dependencies to the `esmeralda` runtime package.
* Web: exact versions in `web/package.json`, locked in `web/package-lock.json` against the public npm
  registry (`web/.npmrc`), installed with `npm ci` and no install scripts.
