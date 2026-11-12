-- Partner Apigee Gateway Chargeback, Token Metering & Latency Decomposition View
-- Monthly partner app chargeback, token consumption, quota exhaustion and gateway vs. backend latency
-- for traffic through the Apigee partner proxies (partner-agents, partner-mcp, usage-metering).
--
-- Two sources, one shape:
--   * contract records: vw_usage_normalized rows with record_origin = 'usage_events',
--     source = 'gateway', gateway_product = 'apigee' and event = 'gateway.request' (the partner
--     proxies' ML-interaction template writing esmeralda.usage). caller.principal is the verified
--     Apigee app (or, for a cross-cloud caller, the federated subject the gateway verified). The
--     contract records only the end-to-end latency_ms, so the per-leg latency columns are NULL.
--     llm.call records from an Apigee model gateway are internal agent usage, not partner traffic,
--     and stay in vw_usage_billable only;
--   * legacy records: the pre-contract ML-interaction shape (event = 'apigee.gateway.interaction')
--     in esmeralda_apigee_interactions, so historical partner traffic keeps counting.
-- A request is written by exactly one template version, so the two sources never overlap. Workload
-- records (the agent behind the proxy) are never read here: tokens come from the gateway record only,
-- so nothing is counted twice.
--
-- Attribution ("attribution comes from authentication, never from the payload"), same tiers and order
-- as vw_usage_normalized:
--   * contract records keep vw_usage_normalized's billed_team / verified / attribution
--     (verified -> principal_table -> declared -> unattributed; gateway records never use the
--     workload floor);
--   * legacy records: the Apigee app was authenticated by its API key, so its team in
--     vw_principal_teams (principal_type = 'apigee_app', filled by the catalog compiler from
--     external/partners) is verified attribution ('principal_table'); an app without a team is
--     'unattributed'; a record without an app (rejected before key verification) is 'legacy'.
-- Every token column is INT64.

WITH contract_interactions AS (
  SELECT
    FORMAT_TIMESTAMP('%Y-%m', timestamp) AS billing_month,
    COALESCE(NULLIF(caller_principal, ''), 'anonymous') AS consumer_app,
    -- The contract carries one verified principal; it has no separate issuer field.
    COALESCE(NULLIF(caller_principal, ''), 'anonymous') AS caller_identity,
    '' AS caller_issuer,
    COALESCE(NULLIF(gateway_name, ''), 'apigee') AS proxy_name,
    COALESCE(NULLIF(model, ''), 'gemini-2.5-flash') AS model_name,
    billed_team,
    verified,
    attribution,
    http_status_code AS status_code,
    NULLIF(error_type, '') AS fault_name,
    COALESCE(input_tokens, 0) AS input_tokens,
    -- Contract output_tokens include reasoning (OTel GenAI); the view reports them apart.
    GREATEST(COALESCE(output_tokens, 0) - COALESCE(reasoning_tokens, 0), 0) AS output_tokens,
    COALESCE(reasoning_tokens, 0) AS thinking_tokens,
    COALESCE(total_tokens, 0) AS total_tokens,
    COALESCE(http_status_code = 429, FALSE) AS quota_exceeded,
    CAST(NULL AS INT64) AS request_gateway_latency_ms,
    CAST(NULL AS INT64) AS target_backend_latency_ms,
    CAST(NULL AS INT64) AS response_gateway_latency_ms,
    CAST(ROUND(latency_ms) AS INT64) AS total_e2e_latency_ms
  FROM `${dataset_id}.vw_usage_normalized`
  WHERE record_origin = 'usage_events'
    AND source = 'gateway'
    AND gateway_product = 'apigee'
    AND event = 'gateway.request'
),

-- One team per Apigee app, even if two writers ever disagree.
apigee_app_teams AS (
  SELECT principal, ANY_VALUE(team) AS team
  FROM `${dataset_id}.vw_principal_teams`
  WHERE principal_type = 'apigee_app'
  GROUP BY principal
),

legacy_records AS (
  SELECT *
  FROM `${dataset_id}.esmeralda_apigee_interactions`
  WHERE jsonPayload.event = 'apigee.gateway.interaction'
  -- Log sinks may export one entry more than once: keep one row per Apigee message.
  QUALIFY ROW_NUMBER() OVER (PARTITION BY jsonPayload.apigee_message_id, timestamp) = 1
),

legacy_interactions AS (
  SELECT
    FORMAT_TIMESTAMP('%Y-%m', a.timestamp) AS billing_month,
    COALESCE(NULLIF(a.jsonPayload.consumer_app, ''), 'anonymous') AS consumer_app,
    COALESCE(NULLIF(a.jsonPayload.caller_identity, ''), 'anonymous') AS caller_identity,
    COALESCE(NULLIF(a.jsonPayload.caller_issuer, ''), '') AS caller_issuer,
    COALESCE(NULLIF(a.jsonPayload.proxy, ''), 'partner-agents') AS proxy_name,
    COALESCE(NULLIF(a.jsonPayload.gen_ai_request_model, ''), 'gemini-2.5-flash') AS model_name,
    COALESCE(pt.team, 'unattributed') AS billed_team,
    pt.team IS NOT NULL AS verified,
    CASE
      WHEN NULLIF(a.jsonPayload.consumer_app, '') IS NULL THEN 'legacy'
      WHEN pt.team IS NOT NULL THEN 'principal_table'
      ELSE 'unattributed'
    END AS attribution,
    SAFE_CAST(a.jsonPayload.status AS INT64) AS status_code,
    NULLIF(a.jsonPayload.fault, '') AS fault_name,
    -- Strings in the legacy shape; a value may be rendered as a double ("336.0").
    COALESCE(CAST(SAFE_CAST(a.jsonPayload.gen_ai_usage_input_tokens AS FLOAT64) AS INT64), 0) AS input_tokens,
    COALESCE(CAST(SAFE_CAST(a.jsonPayload.gen_ai_usage_output_tokens AS FLOAT64) AS INT64), 0) AS output_tokens,
    COALESCE(CAST(SAFE_CAST(a.jsonPayload.gen_ai_usage_thinking_tokens AS FLOAT64) AS INT64), 0) AS thinking_tokens,
    COALESCE(CAST(SAFE_CAST(a.jsonPayload.gen_ai_usage_total_tokens AS FLOAT64) AS INT64), 0) AS total_tokens,
    -- quota.exceeded is the LLMTokenQuota exceed count ("0", "1"...) or a boolean string.
    LOWER(COALESCE(a.jsonPayload.quota.exceeded, 'false')) = 'true'
      OR COALESCE(SAFE_CAST(a.jsonPayload.quota.exceeded AS INT64), 0) > 0
      OR COALESCE(SAFE_CAST(a.jsonPayload.status AS INT64) = 429, FALSE) AS quota_exceeded,
    GREATEST(
      0,
      COALESCE(
        SAFE_CAST(a.jsonPayload.latency_timestamps.target_sent_start_ms AS INT64)
        - SAFE_CAST(a.jsonPayload.latency_timestamps.client_received_start_ms AS INT64),
        0
      )
    ) AS request_gateway_latency_ms,
    GREATEST(
      0,
      COALESCE(
        SAFE_CAST(a.jsonPayload.latency_timestamps.target_received_end_ms AS INT64)
        - SAFE_CAST(a.jsonPayload.latency_timestamps.target_sent_start_ms AS INT64),
        0
      )
    ) AS target_backend_latency_ms,
    GREATEST(
      0,
      COALESCE(
        SAFE_CAST(a.jsonPayload.latency_timestamps.gateway_responded_ms AS INT64)
        - SAFE_CAST(a.jsonPayload.latency_timestamps.target_received_end_ms AS INT64),
        0
      )
    ) AS response_gateway_latency_ms,
    GREATEST(
      0,
      COALESCE(
        SAFE_CAST(a.jsonPayload.latency_timestamps.gateway_responded_ms AS INT64)
        - SAFE_CAST(a.jsonPayload.latency_timestamps.client_received_start_ms AS INT64),
        0
      )
    ) AS total_e2e_latency_ms
  FROM legacy_records AS a
  LEFT JOIN apigee_app_teams AS pt
    ON pt.principal = a.jsonPayload.consumer_app
),

partner_interactions AS (
  SELECT * FROM contract_interactions
  UNION ALL
  SELECT * FROM legacy_interactions
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
  ) AS estimated_cost_usd,
  billed_team,
  verified,
  attribution
FROM partner_interactions
GROUP BY 1, 2, 3, 4, 5, 6, billed_team, verified, attribution
ORDER BY billing_month DESC, estimated_cost_usd DESC
