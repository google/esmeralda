-- usage_events: every esmeralda.usage record (contracts/usage-event.schema.json), flattened to typed columns.
--
-- The usage sinks (modules/1_telemetry_sinks) route jsonPayload.schema = "esmeralda.usage" from every
-- project into ${usage_dataset_id}, one landing table per producer log ID (Agent Engine stdout/stderr,
-- Cloud Run stdout/stderr, Apigee, the Cloud Logging API...). This view unions those tables.
-- Export details handled here:
--   * Logging writes dotted keys with underscores: gen_ai."usage.input_tokens" -> gen_ai.usage_input_tokens;
--   * JSON numbers arrive as FLOAT: token counts, status codes and schema_version are cast to INT64;
--   * trace_id / span_id fall back to the LogEntry trace / spanId fields when the payload omits them.
-- Token semantics (OTel GenAI): input_tokens includes cached_tokens; output_tokens includes reasoning_tokens.
-- `timestamp` is the LogEntry timestamp, the landing tables' partition column: filter on it to prune.
%{ for i, table in landing_tables ~}
%{ if i > 0 ~}
UNION ALL
%{ endif ~}
SELECT
  '${table}' AS producer_log,
  insertId AS record_id,
  timestamp,
  logName AS log_name,
  REGEXP_EXTRACT(logName, r'^projects/([^/]+)/') AS log_project,
  jsonPayload.schema AS schema,
  SAFE_CAST(jsonPayload.schema_version AS INT64) AS schema_version,
  jsonPayload.event AS event,
  jsonPayload.source AS source,
  COALESCE(jsonPayload.trace_id, REGEXP_EXTRACT(trace, r'/traces/([0-9a-fA-F]+)$')) AS trace_id,
  COALESCE(jsonPayload.span_id, spanId) AS span_id,
  jsonPayload.workload.name AS workload_name,
  jsonPayload.workload.team AS workload_team,
  jsonPayload.workload.runtime AS workload_runtime,
  jsonPayload.workload.framework AS workload_framework,
  jsonPayload.workload.cloud AS workload_cloud,
  jsonPayload.caller.principal AS caller_principal,
  jsonPayload.caller.team AS caller_team,
  COALESCE(jsonPayload.caller.verified, FALSE) AS caller_verified,
  jsonPayload.caller.declared.cost_center AS declared_cost_center,
  jsonPayload.caller.declared.country AS declared_country,
  -- Read through JSON: these keys become columns only once a record carrying them is exported.
  -- On Agent Runtime, project_id is the calling project's number; vw_usage_normalized maps it to an ID.
  JSON_VALUE(TO_JSON(jsonPayload.caller.declared), '$.project_id') AS declared_project,
  JSON_VALUE(TO_JSON(jsonPayload.caller.declared), '$.agent_name') AS declared_agent_name,
  TO_JSON(jsonPayload.caller.declared) AS caller_declared,
  jsonPayload.gen_ai.system AS gen_ai_system,
  jsonPayload.gen_ai.operation_name AS gen_ai_operation_name,
  jsonPayload.gen_ai.request_model AS request_model,
  jsonPayload.gen_ai.response_model AS response_model,
  SAFE_CAST(jsonPayload.gen_ai.usage_input_tokens AS INT64) AS input_tokens,
  SAFE_CAST(jsonPayload.gen_ai.usage_output_tokens AS INT64) AS output_tokens,
  SAFE_CAST(jsonPayload.gen_ai.usage_cached_tokens AS INT64) AS cached_tokens,
  SAFE_CAST(jsonPayload.gen_ai.usage_reasoning_tokens AS INT64) AS reasoning_tokens,
  jsonPayload.gen_ai.agent_name AS agent_name,
  jsonPayload.gen_ai.conversation_id AS conversation_id,
  jsonPayload.tool.name AS tool_name,
  jsonPayload.tool.server AS tool_server,
  jsonPayload.tool.call_id AS tool_call_id,
  jsonPayload.tool.status AS tool_status,
  jsonPayload.tool.error_type AS tool_error_type,
  jsonPayload.tool.duration_ms AS tool_duration_ms,
  jsonPayload.gateway.product AS gateway_product,
  jsonPayload.gateway.name AS gateway_name,
  jsonPayload.http.request_method AS http_method,
  SAFE_CAST(jsonPayload.http.response_status_code AS INT64) AS http_status_code,
  jsonPayload.http.route AS http_route,
  jsonPayload.status AS status,
  jsonPayload.error_type AS error_type,
  TO_JSON(jsonPayload.dimensions) AS dimensions,
  jsonPayload.latency_ms AS latency_ms
FROM `${usage_dataset_id}.${table}`
WHERE jsonPayload.schema = 'esmeralda.usage'
%{ endfor ~}
