-- FinOps Monthly Agent Chargeback & TCO Summary SQL View
-- Built on vw_usage_billable (esmeralda.usage records joined with the legacy token events; gateway and
-- workload records of one request joined on trace_id, gateway tokens and identity preferred).
--
-- Billing groups by billed_team: a caller's team when it comes from authentication (caller.verified,
-- or vw_principal_teams on the verified caller.principal), else the team that owns the project the
-- workload logged from (attribution = workload: the floor until callers are verified). Anything else
-- lands in billed_team = 'unattributed' with `attribution` saying why (declared = only an unverified
-- team claim, unattributed = no caller and an unmapped project, legacy = pre-contract record).
-- `verified` is true only for a verified caller.
--
-- Columns up to net_total_chargeback_usd keep their historical names and meaning; total_requests now
-- counts billable units (one request trace through one agent) instead of individual model turns
-- (see total_llm_calls). Rates are estimates for the Gemini 3.7 Flash tier: update them to your contract.
WITH raw_token_metrics AS (
  SELECT
    TIMESTAMP_TRUNC(timestamp, MONTH) AS billing_month,
    agent_id,
    COALESCE(model, 'gemini-3.7-flash') AS model,
    billed_team,
    verified,
    attribution,
    workload_team,
    COUNT(1) AS total_requests,
    SUM(llm_calls) AS total_llm_calls,
    SUM(total_tokens) AS total_tokens,
    SUM(GREATEST(input_tokens - cached_tokens, 0)) AS uncached_prompt_tokens,
    SUM(cached_tokens) AS cached_prompt_tokens,
    SUM(GREATEST(output_tokens - reasoning_tokens, 0)) AS response_tokens,
    SUM(reasoning_tokens) AS reasoning_tokens,
    COUNTIF(token_source = 'gateway') AS gateway_metered_requests
  FROM
    `${dataset_id}.vw_usage_billable`
  GROUP BY
    1, 2, 3, 4, 5, 6, 7
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
  -- Cost Calculations based on Gemini 3.7 Flash Tier ($0.075 / 1M uncached prompt, $0.01875 / 1M cached prompt, $0.30 / 1M response)
  (uncached_prompt_tokens / 1000000.0) * 0.075 AS est_uncached_prompt_cost_usd,
  (cached_prompt_tokens / 1000000.0) * 0.01875 AS est_cached_prompt_cost_usd,
  (response_tokens / 1000000.0) * 0.30 AS est_response_cost_usd,
  (reasoning_tokens / 1000000.0) * 0.30 AS est_reasoning_cost_usd,
  -- Total Monthly Net Chargeback
  ((uncached_prompt_tokens / 1000000.0) * 0.075) +
  ((cached_prompt_tokens / 1000000.0) * 0.01875) +
  ((response_tokens / 1000000.0) * 0.30) +
  ((reasoning_tokens / 1000000.0) * 0.30) AS net_total_chargeback_usd,
  -- Attribution (who pays) and provenance
  billed_team,
  verified,
  attribution,
  workload_team,
  total_llm_calls,
  gateway_metered_requests
FROM
  raw_token_metrics;
