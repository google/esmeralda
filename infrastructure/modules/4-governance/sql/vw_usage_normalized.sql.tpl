-- vw_usage_normalized: one row per usage record, from any producer and any era, in one shape.
--
-- Sources (record_origin):
--   usage_events            esmeralda.usage records (contract), gateway and workload
--   legacy_stdout           pre-contract agent `genai_token_consumption` events (Reasoning Engine stdout)
--   legacy_events_table     pre-contract rows of the genai_telemetry_events envelope table
-- Legacy token fields are mapped onto the OTel GenAI semantics of the contract:
--   input_tokens  = prompt_tokens (includes cached)      output_tokens    = completion + thoughts
--   cached_tokens = cached_tokens                        reasoning_tokens = thoughts
-- Cloud Logging exports every JSON number as FLOAT64 (and JSON payloads keep 336.0): every token
-- column is cast to INT64 here, so sums and joins downstream are integer.
--
-- Projects: Agent Runtime reports project numbers, not IDs (caller.declared.project_id carries the
-- calling project's number). caller_project_id / workload_project are always project IDs: numbers are
-- mapped through project_numbers (the usage-producing projects, from Terraform); an unknown number is
-- kept as is, and also reported in caller_project_number.
--
-- Attribution ("attribution comes from authentication, never from the payload"), first match wins:
--   verified         caller.verified = true and the record carries caller.team
--   principal_table  caller.verified = true, team resolved from vw_principal_teams on caller.principal
--   workload         workload record (source = workload) without a verified caller: the team that owns
--                    the project the record was logged in (vw_principal_teams, principal_type =
--                    gcp_project). The log project is set by Cloud Logging from the writer's identity,
--                    not by the payload. This is the floor: a verified caller always wins over it.
--                    Gateway records never fall back to it (the gateway's project is not the caller)
--   declared         only payload claims (caller.team, or the team of the declared caller project):
--                    reported in declared_team, never billed
--   unattributed     no usable caller and an unmapped log project
--   legacy           pre-contract record (no caller identity was recorded)
-- billed_team is the team that pays: the team of the first three tiers, else 'unattributed'.
-- `verified` is true only when the caller itself was verified (verified, principal_table).
WITH
project_numbers AS (
%{ if length(project_numbers) == 0 ~}
  SELECT CAST(NULL AS STRING) AS project_number, CAST(NULL AS STRING) AS project_id
  LIMIT 0
%{ else ~}
  SELECT project_number, project_id
  FROM UNNEST([
%{ for i, number in sort(keys(project_numbers)) ~}
    STRUCT(${jsonencode(number)} AS project_number, ${jsonencode(project_numbers[number])} AS project_id)${i < length(project_numbers) - 1 ? "," : ""}
%{ endfor ~}
  ])
%{ endif ~}
),

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
    log_project AS workload_project_raw,
    caller_principal,
    caller_team,
    caller_verified,
    declared_cost_center,
    declared_country,
    declared_project AS declared_project_raw,
    declared_agent_name,
    gen_ai_system,
    COALESCE(response_model, request_model) AS model,
    CAST(input_tokens AS INT64) AS input_tokens,
    CAST(output_tokens AS INT64) AS output_tokens,
    CAST(cached_tokens AS INT64) AS cached_tokens,
    CAST(reasoning_tokens AS INT64) AS reasoning_tokens,
    CAST(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0) AS INT64) AS total_tokens,
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
    REGEXP_EXTRACT(logName, r'^projects/([^/]+)/') AS workload_project_raw,
    CAST(NULL AS STRING) AS caller_principal,
    CAST(NULL AS STRING) AS caller_team,
    FALSE AS caller_verified,
    CAST(NULL AS STRING) AS declared_cost_center,
    CAST(NULL AS STRING) AS declared_country,
    CAST(NULL AS STRING) AS declared_project_raw,
    CAST(NULL AS STRING) AS declared_agent_name,
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
    CAST(NULL AS STRING) AS workload_project_raw,
    CAST(NULL AS STRING) AS caller_principal,
    CAST(NULL AS STRING) AS caller_team,
    FALSE AS caller_verified,
    CAST(NULL AS STRING) AS declared_cost_center,
    CAST(NULL AS STRING) AS declared_country,
    CAST(NULL AS STRING) AS declared_project_raw,
    CAST(NULL AS STRING) AS declared_agent_name,
    CAST(NULL AS STRING) AS gen_ai_system,
    JSON_VALUE(payload, '$.model') AS model,
    -- JSON numbers may be written as doubles (336.0): LAX_INT64 accepts both.
    LAX_INT64(payload.tokens.prompt_tokens) AS input_tokens,
    LAX_INT64(payload.tokens.completion_tokens) + COALESCE(LAX_INT64(payload.tokens.thoughts_tokens), 0) AS output_tokens,
    LAX_INT64(payload.tokens.cached_tokens) AS cached_tokens,
    LAX_INT64(payload.tokens.thoughts_tokens) AS reasoning_tokens,
    LAX_INT64(payload.tokens.total_tokens) AS total_tokens,
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

-- A value is a project number when it is all digits (Logging may also render it as a double).
with_numbers AS (
  SELECT
    r.*,
    IF(
      REGEXP_CONTAINS(r.declared_project_raw, r'^[0-9][0-9.]*([eE][+]?[0-9]+)?$'),
      CAST(CAST(SAFE_CAST(r.declared_project_raw AS FLOAT64) AS INT64) AS STRING),
      NULL
    ) AS caller_project_number,
    IF(
      REGEXP_CONTAINS(r.workload_project_raw, r'^[0-9]+$'),
      r.workload_project_raw,
      NULL
    ) AS workload_project_number
  FROM all_records AS r
),

with_projects AS (
  SELECT
    r.* EXCEPT (declared_project_raw, workload_project_raw, workload_project_number),
    IF(r.caller_project_number IS NULL, r.declared_project_raw, COALESCE(cpn.project_id, r.caller_project_number)) AS caller_project_id,
    IF(r.workload_project_number IS NULL, r.workload_project_raw, COALESCE(wpn.project_id, r.workload_project_number)) AS workload_project
  FROM with_numbers AS r
  LEFT JOIN project_numbers AS cpn ON cpn.project_number = r.caller_project_number
  LEFT JOIN project_numbers AS wpn ON wpn.project_number = r.workload_project_number
),

-- One team per (principal_type, principal), even if two writers ever disagree.
principals AS (
  SELECT principal, principal_type, ANY_VALUE(team) AS team
  FROM `${dataset_id}.vw_principal_teams`
  GROUP BY principal, principal_type
),

caller_principals AS (
  SELECT principal, ANY_VALUE(team) AS team
  FROM principals
  WHERE principal_type != 'gcp_project'
  GROUP BY principal
),

project_teams AS (
  SELECT principal AS project_id, team
  FROM principals
  WHERE principal_type = 'gcp_project'
),

attributed AS (
  SELECT
    r.*,
    wpt.team AS workload_project_team,
    COALESCE(r.caller_team, dpt.team) AS declared_team,
    CASE
      WHEN r.record_origin != 'usage_events' THEN 'legacy'
      WHEN r.caller_verified AND r.caller_team IS NOT NULL THEN 'verified'
      WHEN r.caller_verified AND cpt.team IS NOT NULL THEN 'principal_table'
      WHEN r.source = 'workload' AND wpt.team IS NOT NULL THEN 'workload'
      WHEN r.caller_team IS NOT NULL OR dpt.team IS NOT NULL THEN 'declared'
      ELSE 'unattributed'
    END AS attribution,
    CASE
      WHEN r.record_origin != 'usage_events' THEN NULL
      WHEN r.caller_verified AND COALESCE(r.caller_team, cpt.team) IS NOT NULL THEN COALESCE(r.caller_team, cpt.team)
      WHEN r.source = 'workload' THEN wpt.team
    END AS billing_team,
    r.record_origin = 'usage_events' AND r.caller_verified AND COALESCE(r.caller_team, cpt.team) IS NOT NULL AS caller_is_verified
  FROM with_projects AS r
  LEFT JOIN caller_principals AS cpt
    ON r.caller_verified AND cpt.principal = r.caller_principal
  LEFT JOIN project_teams AS wpt
    ON wpt.project_id = r.workload_project
  LEFT JOIN project_teams AS dpt
    ON dpt.project_id = r.caller_project_id
)

SELECT
  * EXCEPT (billing_team, caller_is_verified),
  -- Join key of gateway and workload records of one agent (legacy agent ids use underscores).
  REPLACE(LOWER(agent_id), '_', '-') AS agent_key,
  caller_is_verified AS verified,
  COALESCE(billing_team, 'unattributed') AS billed_team
FROM attributed
