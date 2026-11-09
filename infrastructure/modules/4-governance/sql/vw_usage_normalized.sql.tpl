-- vw_usage_normalized: one row per usage record, from any producer and any era, in one shape.
--
-- Sources (record_origin):
--   usage_events            esmeralda.usage records (contract), gateway and workload
--   legacy_stdout           pre-contract agent `genai_token_consumption` events (Reasoning Engine stdout)
--   legacy_events_table     pre-contract rows of the genai_telemetry_events envelope table
-- Legacy token fields are mapped onto the OTel GenAI semantics of the contract:
--   input_tokens  = prompt_tokens (includes cached)      output_tokens    = completion + thoughts
--   cached_tokens = cached_tokens                        reasoning_tokens = thoughts
--
-- Attribution ("attribution comes from authentication, never from the payload"):
--   verified         caller.verified = true and the record carries caller.team
--   principal_table  caller.verified = true, team resolved from principal_teams (catalog compiler)
--   declared         a caller team is present but not verified (payload claim): never trusted for billing
--   unattributed     no usable caller (alarm: the expected count is 0)
--   legacy           pre-contract record (no caller identity was recorded)
-- billed_team is the team that pays: the verified team, else 'unattributed'. caller_team keeps the
-- claimed value for investigation. `verified` is the flag the chargeback groups on.
WITH
native AS (
  SELECT
    'usage_events' AS record_origin,
    producer_log,
    record_id,
    timestamp,
    schema_version,
    event,
    source,
    trace_id,
    span_id,
    conversation_id AS session_id,
    CAST(NULL AS STRING) AS user_id,
    CAST(NULL AS STRING) AS execution_path,
    COALESCE(workload_name, agent_name) AS agent_id,
    workload_name,
    workload_team,
    workload_runtime,
    workload_framework,
    workload_cloud,
    caller_principal,
    caller_team,
    caller_verified,
    declared_cost_center,
    declared_country,
    gen_ai_system,
    COALESCE(response_model, request_model) AS model,
    input_tokens,
    output_tokens,
    cached_tokens,
    reasoning_tokens,
    COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0) AS total_tokens,
    tool_name,
    tool_server,
    tool_status,
    tool_duration_ms,
    gateway_product,
    gateway_name,
    http_status_code,
    status,
    error_type,
    latency_ms,
    dimensions
  FROM `${dataset_id}.usage_events`
),

legacy_stdout AS (
  SELECT
    'legacy_stdout' AS record_origin,
    'aiplatform_googleapis_com_reasoning_engine_stdout' AS producer_log,
    CAST(NULL AS STRING) AS record_id,
    timestamp,
    0 AS schema_version,
    'llm.call' AS event,
    'workload' AS source,
    COALESCE(jsonPayload.trace_id, REGEXP_EXTRACT(trace, r'/traces/([0-9a-fA-F]+)$')) AS trace_id,
    CAST(NULL AS STRING) AS span_id,
    jsonPayload.session_id AS session_id,
    jsonPayload.user_id AS user_id,
    jsonPayload.execution_path AS execution_path,
    jsonPayload.agent_id AS agent_id,
    jsonPayload.agent_id AS workload_name,
    CAST(NULL AS STRING) AS workload_team,
    'agent_engine' AS workload_runtime,
    CAST(NULL AS STRING) AS workload_framework,
    'gcp' AS workload_cloud,
    CAST(NULL AS STRING) AS caller_principal,
    CAST(NULL AS STRING) AS caller_team,
    FALSE AS caller_verified,
    CAST(NULL AS STRING) AS declared_cost_center,
    CAST(NULL AS STRING) AS declared_country,
    CAST(NULL AS STRING) AS gen_ai_system,
    jsonPayload.model AS model,
    CAST(jsonPayload.tokens.prompt_tokens AS INT64) AS input_tokens,
    CAST(jsonPayload.tokens.completion_tokens AS INT64) + COALESCE(CAST(jsonPayload.tokens.thoughts_tokens AS INT64), 0) AS output_tokens,
    CAST(jsonPayload.tokens.cached_tokens AS INT64) AS cached_tokens,
    CAST(jsonPayload.tokens.thoughts_tokens AS INT64) AS reasoning_tokens,
    CAST(jsonPayload.tokens.total_tokens AS INT64) AS total_tokens,
    CAST(NULL AS STRING) AS tool_name,
    CAST(NULL AS STRING) AS tool_server,
    CAST(NULL AS STRING) AS tool_status,
    CAST(NULL AS FLOAT64) AS tool_duration_ms,
    CAST(NULL AS STRING) AS gateway_product,
    CAST(NULL AS STRING) AS gateway_name,
    CAST(NULL AS INT64) AS http_status_code,
    CAST(NULL AS STRING) AS status,
    CAST(NULL AS STRING) AS error_type,
    CAST(NULL AS FLOAT64) AS latency_ms,
    CAST(NULL AS JSON) AS dimensions
  FROM `${dataset_id}.aiplatform_googleapis_com_reasoning_engine_stdout`
  WHERE jsonPayload.event = 'genai_token_consumption'
),

legacy_events_table AS (
  SELECT
    'legacy_events_table' AS record_origin,
    'genai_telemetry_events' AS producer_log,
    CAST(NULL AS STRING) AS record_id,
    timestamp,
    0 AS schema_version,
    'llm.call' AS event,
    'workload' AS source,
    JSON_VALUE(payload, '$.trace_id') AS trace_id,
    CAST(NULL AS STRING) AS span_id,
    session_id,
    user_id,
    execution_path,
    agent_id,
    agent_id AS workload_name,
    CAST(NULL AS STRING) AS workload_team,
    CAST(NULL AS STRING) AS workload_runtime,
    CAST(NULL AS STRING) AS workload_framework,
    'gcp' AS workload_cloud,
    CAST(NULL AS STRING) AS caller_principal,
    CAST(NULL AS STRING) AS caller_team,
    FALSE AS caller_verified,
    CAST(NULL AS STRING) AS declared_cost_center,
    CAST(NULL AS STRING) AS declared_country,
    CAST(NULL AS STRING) AS gen_ai_system,
    JSON_VALUE(payload, '$.model') AS model,
    CAST(JSON_VALUE(payload, '$.tokens.prompt_tokens') AS INT64) AS input_tokens,
    CAST(JSON_VALUE(payload, '$.tokens.completion_tokens') AS INT64)
      + COALESCE(CAST(JSON_VALUE(payload, '$.tokens.thoughts_tokens') AS INT64), 0) AS output_tokens,
    CAST(JSON_VALUE(payload, '$.tokens.cached_tokens') AS INT64) AS cached_tokens,
    CAST(JSON_VALUE(payload, '$.tokens.thoughts_tokens') AS INT64) AS reasoning_tokens,
    CAST(JSON_VALUE(payload, '$.tokens.total_tokens') AS INT64) AS total_tokens,
    CAST(NULL AS STRING) AS tool_name,
    CAST(NULL AS STRING) AS tool_server,
    CAST(NULL AS STRING) AS tool_status,
    CAST(NULL AS FLOAT64) AS tool_duration_ms,
    CAST(NULL AS STRING) AS gateway_product,
    CAST(NULL AS STRING) AS gateway_name,
    CAST(NULL AS INT64) AS http_status_code,
    CAST(NULL AS STRING) AS status,
    CAST(NULL AS STRING) AS error_type,
    CAST(NULL AS FLOAT64) AS latency_ms,
    CAST(NULL AS JSON) AS dimensions
  FROM `${dataset_id}.genai_telemetry_events`
  WHERE event_type = 'genai_token_consumption'
),

all_records AS (
  SELECT * FROM native
  UNION ALL
  SELECT * FROM legacy_stdout
  UNION ALL
  SELECT * FROM legacy_events_table
),

-- One team per principal, even if the compiler ever wrote a duplicate.
principal_teams AS (
  SELECT principal, ANY_VALUE(team) AS team, ANY_VALUE(verified_by) AS verified_by
  FROM `${dataset_id}.principal_teams`
  GROUP BY principal
),

attributed AS (
  SELECT
    r.*,
    pt.verified_by AS principal_verified_by,
    CASE
      WHEN r.record_origin != 'usage_events' THEN 'legacy'
      WHEN r.caller_verified AND r.caller_team IS NOT NULL THEN 'verified'
      WHEN r.caller_verified AND pt.team IS NOT NULL THEN 'principal_table'
      WHEN r.caller_team IS NOT NULL THEN 'declared'
      ELSE 'unattributed'
    END AS attribution,
    CASE
      WHEN r.record_origin = 'usage_events' AND r.caller_verified THEN COALESCE(r.caller_team, pt.team)
    END AS verified_team
  FROM all_records AS r
  LEFT JOIN principal_teams AS pt
    ON r.caller_verified AND pt.principal = r.caller_principal
)

SELECT
  * EXCEPT (verified_team),
  -- Join key of gateway and workload records of one agent (legacy agent ids use underscores).
  REPLACE(LOWER(agent_id), '_', '-') AS agent_key,
  verified_team IS NOT NULL AS verified,
  COALESCE(verified_team, 'unattributed') AS billed_team
FROM attributed
