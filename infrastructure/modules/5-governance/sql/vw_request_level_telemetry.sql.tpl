-- Per-Request Telemetry Detail SQL View
-- Lists every individual inference turn, session ID, trace ID, user ID, token count, and cost breakdown
WITH combined_requests AS (
  -- 1. Unified Telemetry Events Table
  SELECT
    timestamp,
    session_id,
    JSON_VALUE(payload, '$.trace_id') AS trace_id,
    user_id,
    agent_id,
    execution_path,
    JSON_VALUE(payload, '$.model') AS model,
    CAST(JSON_VALUE(payload, '$.tokens.prompt_tokens') AS INT64) AS prompt_tokens,
    CAST(JSON_VALUE(payload, '$.tokens.completion_tokens') AS INT64) AS completion_tokens,
    CAST(JSON_VALUE(payload, '$.tokens.thoughts_tokens') AS INT64) AS thoughts_tokens,
    CAST(JSON_VALUE(payload, '$.tokens.cached_tokens') AS INT64) AS cached_tokens,
    CAST(JSON_VALUE(payload, '$.tokens.total_tokens') AS INT64) AS total_tokens
  FROM
    `${dataset_id}.genai_telemetry_events`
  WHERE
    event_type = 'genai_token_consumption'

  UNION ALL

  -- 2. Real-Time Raw Log Stream Extraction
  SELECT
    timestamp,
    jsonPayload.session_id,
    COALESCE(jsonPayload.trace_id, REGEXP_EXTRACT(trace, r'projects/[^/]+/traces/(.+)'), trace) AS trace_id,
    jsonPayload.user_id,
    jsonPayload.agent_id,
    jsonPayload.execution_path,
    jsonPayload.model,
    CAST(jsonPayload.tokens.prompt_tokens AS INT64) AS prompt_tokens,
    CAST(jsonPayload.tokens.completion_tokens AS INT64) AS completion_tokens,
    CAST(jsonPayload.tokens.thoughts_tokens AS INT64) AS thoughts_tokens,
    CAST(jsonPayload.tokens.cached_tokens AS INT64) AS cached_tokens,
    CAST(jsonPayload.tokens.total_tokens AS INT64) AS total_tokens
  FROM
    `${dataset_id}.aiplatform_googleapis_com_reasoning_engine_stdout`
  WHERE
    jsonPayload.event = 'genai_token_consumption'
)
SELECT
  timestamp,
  session_id,
  trace_id,
  user_id,
  agent_id,
  execution_path,
  model,
  prompt_tokens,
  completion_tokens,
  thoughts_tokens,
  cached_tokens,
  total_tokens,
  -- Per-Request Cost Calculation in USD
  (((prompt_tokens - COALESCE(cached_tokens, 0)) / 1000000.0) * 0.075) +
  ((COALESCE(cached_tokens, 0) / 1000000.0) * 0.01875) +
  ((completion_tokens / 1000000.0) * 0.30) +
  ((COALESCE(thoughts_tokens, 0) / 1000000.0) * 0.30) AS request_cost_usd
FROM
  combined_requests
ORDER BY
  timestamp DESC;
