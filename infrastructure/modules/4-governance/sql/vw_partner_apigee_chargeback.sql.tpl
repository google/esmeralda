-- Partner Apigee Gateway Chargeback, Token Metering & Latency Decomposition View
-- Aggregates structured OTel GenAI interaction logs emitted by the Apigee
-- usage-metering shared flow and partner-agents proxy (ML-interaction policy)
-- into monthly partner app chargeback, token consumption, quota exhaustion,
-- and 3-leg gateway vs. backend latency percentiles.

WITH partner_interactions AS (
  SELECT
    FORMAT_TIMESTAMP('%Y-%m', timestamp) AS billing_month,
    COALESCE(NULLIF(jsonPayload.consumer_app, ''), 'anonymous') AS consumer_app,
    COALESCE(NULLIF(jsonPayload.caller_identity, ''), 'anonymous') AS caller_identity,
    COALESCE(NULLIF(jsonPayload.caller_issuer, ''), '') AS caller_issuer,
    COALESCE(NULLIF(jsonPayload.proxy, ''), 'partner-agents') AS proxy_name,
    COALESCE(NULLIF(jsonPayload.gen_ai_request_model, ''), 'gemini-2.5-flash') AS model_name,
    SAFE_CAST(jsonPayload.status AS INT64) AS status_code,
    NULLIF(jsonPayload.fault, '') AS fault_name,
    COALESCE(SAFE_CAST(jsonPayload.gen_ai_usage_input_tokens AS INT64), 0) AS input_tokens,
    COALESCE(SAFE_CAST(jsonPayload.gen_ai_usage_output_tokens AS INT64), 0) AS output_tokens,
    COALESCE(SAFE_CAST(jsonPayload.gen_ai_usage_thinking_tokens AS INT64), 0) AS thinking_tokens,
    COALESCE(SAFE_CAST(jsonPayload.gen_ai_usage_total_tokens AS INT64), 0) AS total_tokens,
    LOWER(COALESCE(jsonPayload.quota.exceeded, 'false')) = 'true'
      OR SAFE_CAST(jsonPayload.status AS INT64) = 429 AS quota_exceeded,
    GREATEST(
      0,
      COALESCE(
        SAFE_CAST(jsonPayload.latency_timestamps.target_sent_start_ms AS INT64)
        - SAFE_CAST(jsonPayload.latency_timestamps.client_received_start_ms AS INT64),
        0
      )
    ) AS request_gateway_latency_ms,
    GREATEST(
      0,
      COALESCE(
        SAFE_CAST(jsonPayload.latency_timestamps.target_received_end_ms AS INT64)
        - SAFE_CAST(jsonPayload.latency_timestamps.target_sent_start_ms AS INT64),
        0
      )
    ) AS target_backend_latency_ms,
    GREATEST(
      0,
      COALESCE(
        SAFE_CAST(jsonPayload.latency_timestamps.gateway_responded_ms AS INT64)
        - SAFE_CAST(jsonPayload.latency_timestamps.target_received_end_ms AS INT64),
        0
      )
    ) AS response_gateway_latency_ms,
    GREATEST(
      0,
      COALESCE(
        SAFE_CAST(jsonPayload.latency_timestamps.gateway_responded_ms AS INT64)
        - SAFE_CAST(jsonPayload.latency_timestamps.client_received_start_ms AS INT64),
        0
      )
    ) AS total_e2e_latency_ms
  FROM `${dataset_id}.esmeralda_apigee_interactions`
  WHERE jsonPayload.event = 'apigee.gateway.interaction'
)

SELECT
  billing_month,
  consumer_app,
  caller_identity,
  caller_issuer,
  proxy_name,
  model_name,
  COUNT(*) AS total_requests,
  COUNTIF(status_code >= 400) AS error_requests,
  COUNTIF(quota_exceeded) AS quota_exceeded_requests,
  SUM(input_tokens) AS total_input_tokens,
  SUM(output_tokens) AS total_output_tokens,
  SUM(thinking_tokens) AS total_thinking_tokens,
  SUM(total_tokens) AS total_billed_tokens,
  ROUND(AVG(request_gateway_latency_ms), 2) AS avg_request_gateway_latency_ms,
  ROUND(AVG(target_backend_latency_ms), 2) AS avg_target_backend_latency_ms,
  ROUND(AVG(response_gateway_latency_ms), 2) AS avg_response_gateway_latency_ms,
  ROUND(AVG(total_e2e_latency_ms), 2) AS avg_total_e2e_latency_ms,
  ROUND(
    (SUM(input_tokens) / 1000.0) * CASE
      WHEN model_name LIKE '%pro%' THEN 0.00125
      ELSE 0.00015
    END +
    (SUM(output_tokens + thinking_tokens) / 1000.0) * CASE
      WHEN model_name LIKE '%pro%' THEN 0.00500
      ELSE 0.00060
    END,
    6
  ) AS estimated_cost_usd
FROM partner_interactions
GROUP BY 1, 2, 3, 4, 5, 6
ORDER BY billing_month DESC, estimated_cost_usd DESC
