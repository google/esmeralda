# BigQuery schema of the esmeralda.usage landing tables.
#
# Mirrors contracts/usage-event.schema.json (schema_version 1) as Cloud Logging exports it to
# BigQuery:
#   - field names with dots become underscores: gen_ai."usage.input_tokens" -> gen_ai.usage_input_tokens,
#     http."response.status_code" -> http.response_status_code;
#   - every JSON number is a FLOAT (schema_version, token counts, durations); the usage_events view casts;
#   - JSON arrays are REPEATED fields.
# Free-form maps (caller.declared, dimensions) start with their documented keys; the sinks add any
# other key as a new column on write.
# Producers must send the contract's JSON types (numbers as numbers, not strings): an entry whose
# field type doesn't match its column fails to export (Logging reports it as a sink error).
locals {
  usage_landing_schema = [
    { name = "timestamp", type = "TIMESTAMP", mode = "NULLABLE", description = "LogEntry timestamp (partition column)" },
    { name = "receiveTimestamp", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "logName", type = "STRING", mode = "NULLABLE" },
    { name = "insertId", type = "STRING", mode = "NULLABLE" },
    { name = "severity", type = "STRING", mode = "NULLABLE" },
    { name = "trace", type = "STRING", mode = "NULLABLE", description = "projects/<p>/traces/<trace_id>, when the producer set it" },
    { name = "spanId", type = "STRING", mode = "NULLABLE" },
    {
      name = "jsonPayload", type = "RECORD", mode = "NULLABLE",
      fields = [
        { name = "schema", type = "STRING", mode = "NULLABLE", description = "Always esmeralda.usage" },
        { name = "schema_version", type = "FLOAT", mode = "NULLABLE" },
        { name = "event", type = "STRING", mode = "NULLABLE", description = "llm.call | tool.call | agent.invocation | gateway.request" },
        { name = "trace_id", type = "STRING", mode = "NULLABLE" },
        { name = "span_id", type = "STRING", mode = "NULLABLE" },
        { name = "source", type = "STRING", mode = "NULLABLE", description = "gateway | workload" },
        {
          name = "workload", type = "RECORD", mode = "NULLABLE",
          fields = [
            { name = "name", type = "STRING", mode = "NULLABLE" },
            { name = "team", type = "STRING", mode = "NULLABLE" },
            { name = "runtime", type = "STRING", mode = "NULLABLE" },
            { name = "framework", type = "STRING", mode = "NULLABLE" },
            { name = "cloud", type = "STRING", mode = "NULLABLE" },
          ]
        },
        {
          name = "caller", type = "RECORD", mode = "NULLABLE",
          fields = [
            { name = "principal", type = "STRING", mode = "NULLABLE" },
            { name = "team", type = "STRING", mode = "NULLABLE" },
            { name = "verified", type = "BOOLEAN", mode = "NULLABLE" },
            {
              name = "declared", type = "RECORD", mode = "NULLABLE",
              fields = [
                { name = "cost_center", type = "STRING", mode = "NULLABLE" },
                { name = "country", type = "STRING", mode = "NULLABLE" },
              ]
            },
          ]
        },
        {
          name = "gen_ai", type = "RECORD", mode = "NULLABLE",
          fields = [
            { name = "system", type = "STRING", mode = "NULLABLE" },
            { name = "operation_name", type = "STRING", mode = "NULLABLE" },
            { name = "request_model", type = "STRING", mode = "NULLABLE" },
            { name = "response_model", type = "STRING", mode = "NULLABLE" },
            { name = "response_finish_reasons", type = "STRING", mode = "REPEATED" },
            { name = "usage_input_tokens", type = "FLOAT", mode = "NULLABLE", description = "Includes cached tokens" },
            { name = "usage_output_tokens", type = "FLOAT", mode = "NULLABLE", description = "Includes reasoning tokens" },
            { name = "usage_cached_tokens", type = "FLOAT", mode = "NULLABLE" },
            { name = "usage_reasoning_tokens", type = "FLOAT", mode = "NULLABLE" },
            { name = "agent_name", type = "STRING", mode = "NULLABLE" },
            { name = "conversation_id", type = "STRING", mode = "NULLABLE" },
          ]
        },
        {
          name = "tool", type = "RECORD", mode = "NULLABLE",
          fields = [
            { name = "name", type = "STRING", mode = "NULLABLE" },
            { name = "server", type = "STRING", mode = "NULLABLE" },
            { name = "call_id", type = "STRING", mode = "NULLABLE" },
            { name = "status", type = "STRING", mode = "NULLABLE" },
            { name = "error_type", type = "STRING", mode = "NULLABLE" },
            { name = "duration_ms", type = "FLOAT", mode = "NULLABLE" },
          ]
        },
        {
          name = "gateway", type = "RECORD", mode = "NULLABLE",
          fields = [
            { name = "product", type = "STRING", mode = "NULLABLE", description = "apigee | envoy | agent_gateway | direct | other" },
            { name = "name", type = "STRING", mode = "NULLABLE" },
          ]
        },
        {
          name = "http", type = "RECORD", mode = "NULLABLE",
          fields = [
            { name = "request_method", type = "STRING", mode = "NULLABLE" },
            { name = "response_status_code", type = "FLOAT", mode = "NULLABLE" },
            { name = "route", type = "STRING", mode = "NULLABLE" },
          ]
        },
        { name = "status", type = "STRING", mode = "NULLABLE" },
        { name = "error_type", type = "STRING", mode = "NULLABLE" },
        {
          name = "dimensions", type = "RECORD", mode = "NULLABLE",
          fields = [
            { name = "loan_product", type = "STRING", mode = "NULLABLE", description = "Sample registered dimension" },
          ]
        },
        { name = "latency_ms", type = "FLOAT", mode = "NULLABLE" },
      ]
    },
  ]
}
