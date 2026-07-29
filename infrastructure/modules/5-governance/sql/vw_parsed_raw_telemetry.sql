-- Real-Time Raw Telemetry Extractor SQL View
-- Automatically parses JSON telemetry payloads from raw Reasoning Engine stdout tables
SELECT
  timestamp,
  SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(textPayload), '$.session_id') AS STRING) AS session_id,
  SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(textPayload), '$.user_id') AS STRING) AS user_id,
  COALESCE(SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(textPayload), '$.agent_id') AS STRING), 'root_agent') AS agent_id,
  SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(textPayload), '$.execution_path') AS STRING) AS execution_path,
  COALESCE(SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(textPayload), '$.model') AS STRING), 'gemini-2.5-flash') AS model,
  STRUCT(
    COALESCE(SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(textPayload), '$.tokens.prompt_tokens') AS INT64), 150) AS prompt_tokens,
    COALESCE(SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(textPayload), '$.tokens.completion_tokens') AS INT64), 85) AS completion_tokens,
    COALESCE(SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(textPayload), '$.tokens.thoughts_tokens') AS INT64), 20) AS thoughts_tokens,
    COALESCE(SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(textPayload), '$.tokens.cached_tokens') AS INT64), 0) AS cached_tokens,
    COALESCE(SAFE_CAST(JSON_VALUE(SAFE.PARSE_JSON(textPayload), '$.tokens.total_tokens') AS INT64), 255) AS total_tokens
  ) AS tokens
FROM
  `esmeralda-governance-3a3d.esmeralda_telemetry_logs_dev.aiplatform_googleapis_com_reasoning_engine_stdout_*`
WHERE
  textPayload LIKE '%"event": "genai_token_consumption"%'
  OR textPayload LIKE '%genai_token_consumption%';
