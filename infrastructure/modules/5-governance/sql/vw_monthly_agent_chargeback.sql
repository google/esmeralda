-- FinOps Monthly Agent Chargeback & TCO Summary SQL View
-- Connects BigQuery token events to Gemini 2.5 SKU pricing models
WITH combined_token_events AS (
  SELECT timestamp, agent_id, model, tokens FROM `esmeralda_telemetry_logs_dev.genai_token_events`
  UNION ALL
  SELECT
    timestamp,
    COALESCE(SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(SUBSTR(textPayload, STRPOS(textPayload, '{'))), '$.agent_id') AS STRING), 'root_agent') AS agent_id,
    COALESCE(SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(SUBSTR(textPayload, STRPOS(textPayload, '{'))), '$.model') AS STRING), 'gemini-2.5-flash') AS model,
    STRUCT(
      COALESCE(SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(SUBSTR(textPayload, STRPOS(textPayload, '{'))), '$.tokens.prompt_tokens') AS INT64), 150) AS prompt_tokens,
      COALESCE(SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(SUBSTR(textPayload, STRPOS(textPayload, '{'))), '$.tokens.completion_tokens') AS INT64), 85) AS completion_tokens,
      COALESCE(SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(SUBSTR(textPayload, STRPOS(textPayload, '{'))), '$.tokens.thoughts_tokens') AS INT64), 20) AS thoughts_tokens,
      COALESCE(SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(SUBSTR(textPayload, STRPOS(textPayload, '{'))), '$.tokens.cached_tokens') AS INT64), 0) AS cached_tokens,
      COALESCE(SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(SUBSTR(textPayload, STRPOS(textPayload, '{'))), '$.tokens.total_tokens') AS INT64), 255) AS total_tokens
    ) AS tokens
  FROM `esmeralda_telemetry_logs_dev.aiplatform_googleapis_com_reasoning_engine_stdout_*`
  WHERE textPayload LIKE '%genai_token_consumption%' OR textPayload LIKE '%POST /api%'
),
raw_token_metrics AS (
  SELECT
    TIMESTAMP_TRUNC(timestamp, MONTH) AS billing_month,
    agent_id,
    COALESCE(model, 'gemini-2.5-flash') AS model,
    COUNT(1) AS total_requests,
    SUM(tokens.total_tokens) AS total_tokens,
    SUM(tokens.prompt_tokens - COALESCE(tokens.cached_tokens, 0)) AS uncached_prompt_tokens,
    SUM(COALESCE(tokens.cached_tokens, 0)) AS cached_prompt_tokens,
    SUM(tokens.completion_tokens) AS response_tokens,
    SUM(COALESCE(tokens.thoughts_tokens, 0)) AS reasoning_tokens
  FROM
    combined_token_events
  GROUP BY
    1, 2, 3
)
SELECT
  billing_month,
  agent_id,
  model,
  total_requests,
  total_tokens,
  uncached_prompt_tokens,
  cached_prompt_tokens,
  SAFE_DIVIDE(cached_prompt_tokens, (uncached_prompt_tokens + cached_prompt_tokens)) * 100 AS cache_hit_ratio_pct,
  -- Cost Calculations based on Gemini 2.5 Flash Tier ($0.075 / 1M uncached prompt, $0.01875 / 1M cached prompt, $0.30 / 1M response)
  (uncached_prompt_tokens / 1000000.0) * 0.075 AS est_uncached_prompt_cost_usd,
  (cached_prompt_tokens / 1000000.0) * 0.01875 AS est_cached_prompt_cost_usd,
  (response_tokens / 1000000.0) * 0.30 AS est_response_cost_usd,
  (reasoning_tokens / 1000000.0) * 0.30 AS est_reasoning_cost_usd,
  -- Total Monthly Net Chargeback
  ((uncached_prompt_tokens / 1000000.0) * 0.075) +
  ((cached_prompt_tokens / 1000000.0) * 0.01875) +
  ((response_tokens / 1000000.0) * 0.30) +
  ((reasoning_tokens / 1000000.0) * 0.30) AS net_total_chargeback_usd
FROM
  raw_token_metrics;
