# Contracts

Esmeralda publishes schemas, not implementations. Any producer (the `esmeralda` library, a
gateway, an OpenTelemetry Collector processor, code in another language or cloud) can emit records
that follow them, and every consumer (log sinks, BigQuery views, metrics, alerts) relies only on
them.

| File | Contract |
|---|---|
| [`usage-event.schema.json`](usage-event.schema.json) | `esmeralda.usage`: one usage record (model call, tool call, agent invocation, gateway request) |
| [`examples/`](examples/) | One valid record per event type, with synthetic data |

`catalog.schema.json` (`esmeralda.catalog`) joins this folder in a later step.

## Versioning

Each record carries its contract name and an integer version, like CloudEvents `specversion`:

```json
{"schema": "esmeralda.usage", "schema_version": 1, "...": "..."}
```

There are no `v1/` folders or modules. The rules:

1. **Additive changes keep the version.** New optional fields, new enum values and looser limits
   are added to the existing schema with the same `schema_version`.
2. **Breaking changes bump the version** by one: removing or renaming a field, changing its type
   or meaning, making an optional field required, or tightening a limit. A bump ships as a new
   `const` in the schema, and producers keep emitting the old version until every consumer reads
   the new one.
3. **Producers emit only fields the schema defines** (it sets `additionalProperties: false`).
   Extra business attributes go in `dimensions`, and consumers accept a dimension key only if the
   catalog registers it.
4. **Consumers select records by `schema`** (`jsonPayload.schema = "esmeralda.usage"`), never by
   log name, and tolerate fields newer than the schema they were built against.

## Field conventions (`esmeralda.usage`)

- Names follow the [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
  wherever they exist, without the `gen_ai.` prefix inside the `gen_ai` object
  (`gen_ai["usage.input_tokens"]` is `gen_ai.usage.input_tokens`).
- `usage.input_tokens` includes cached tokens and `usage.output_tokens` includes reasoning tokens;
  `usage.cached_tokens` and `usage.reasoning_tokens` are subsets, never added on top.
- `agent.invocation` token counts are a roll-up of the invocation's `llm.call` records. Chargeback
  sums `llm.call` (or gateway) records; the roll-up is for per-invocation budgets and alerts.
- `caller.verified` is `true` only when `principal` and `team` come from authentication.
  `caller.declared` holds caller-supplied context and is never trusted for billing on its own.
- Records never contain secrets: no credentials, tokens or the delegated user credential, in any
  field, including `dimensions` and `caller.declared`.

## Validation

`packages/esmeralda/tests/test_usage_contract.py` validates every file in `examples/` and every
record the `esmeralda` library produces against the schema, on each test run.

The `esmeralda` package ships a copy of the schema
(`packages/esmeralda/src/esmeralda/contracts/usage-event.schema.json`), read at runtime by
`esmeralda.usage.schema()` / `validate()`. The same test fails if the copy differs from this
folder: after changing a schema here, copy it there.
