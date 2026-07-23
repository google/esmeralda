-- Per-Request Telemetry Detail SQL View
-- Lists every individual inference turn, session ID, user ID, token count, and cost breakdown
WITH combined_requests AS (
  -- 1. Structured Token Events Table
  SELECT
    timestamp,
    session_id,
    user_id,
    agent_id,
    execution_path,
    model,
    tokens.prompt_tokens,
    tokens.completion_tokens,
    tokens.thoughts_tokens,
    tokens.cached_tokens,
    tokens.total_tokens
  FROM
    `${dataset_id}.genai_token_events`

  UNION ALL

  -- 2. Real-Time Raw Log Stream Extraction (handles structured jsonPayload telemetry events)
  SELECT
    timestamp,
    jsonPayload.session_id,
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
    `${dataset_id}.reasoning_engine_stdout_*`
  WHERE
    jsonPayload.event = 'genai_token_consumption'
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
