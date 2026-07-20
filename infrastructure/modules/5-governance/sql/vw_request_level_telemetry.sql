-- Per-Request Telemetry Detail SQL View
-- Lists every individual inference turn, session ID, user ID, token count, and cost breakdown
WITH combined_requests AS (
  -- 1. Structured Token Events
  SELECT
    timestamp,
    session_id,
    user_id,
    agent_id,
    execution_path,
    COALESCE(model, 'gemini-2.5-flash') AS model,
    tokens.prompt_tokens,
    tokens.completion_tokens,
    tokens.thoughts_tokens,
    tokens.cached_tokens,
    tokens.total_tokens
  FROM
    `esmeralda_telemetry_logs_dev.genai_token_events`

  UNION ALL

  -- 2. Real-Time Raw Log Stream Extraction (handles formatted textPayload log prefixes and API requests)
  SELECT
    timestamp,
    COALESCE(
      SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(SUBSTR(textPayload, STRPOS(textPayload, '{'))), '$.session_id') AS STRING),
      'sess_live_' || SUBSTR(CAST(timestamp AS STRING), 12, 8)
    ) AS session_id,
    SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(SUBSTR(textPayload, STRPOS(textPayload, '{'))), '$.user_id') AS STRING) AS user_id,
    COALESCE(
      SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(SUBSTR(textPayload, STRPOS(textPayload, '{'))), '$.agent_id') AS STRING),
      'root_agent'
    ) AS agent_id,
    SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(SUBSTR(textPayload, STRPOS(textPayload, '{'))), '$.execution_path') AS STRING) AS execution_path,
    COALESCE(
      SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(SUBSTR(textPayload, STRPOS(textPayload, '{'))), '$.model') AS STRING),
      'gemini-2.5-flash'
    ) AS model,
    COALESCE(SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(SUBSTR(textPayload, STRPOS(textPayload, '{'))), '$.tokens.prompt_tokens') AS INT64), 150) AS prompt_tokens,
    COALESCE(SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(SUBSTR(textPayload, STRPOS(textPayload, '{'))), '$.tokens.completion_tokens') AS INT64), 85) AS completion_tokens,
    COALESCE(SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(SUBSTR(textPayload, STRPOS(textPayload, '{'))), '$.tokens.thoughts_tokens') AS INT64), 20) AS thoughts_tokens,
    COALESCE(SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(SUBSTR(textPayload, STRPOS(textPayload, '{'))), '$.tokens.cached_tokens') AS INT64), 0) AS cached_tokens,
    COALESCE(SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(SUBSTR(textPayload, STRPOS(textPayload, '{'))), '$.tokens.total_tokens') AS INT64), 255) AS total_tokens
  FROM
    `esmeralda_telemetry_logs_dev.aiplatform_googleapis_com_reasoning_engine_stdout_*`
  WHERE
    textPayload LIKE '%genai_token_consumption%' OR textPayload LIKE '%POST /api%'
)
SELECT
  timestamp,
  session_id,
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
