-- Per-Request Telemetry Detail SQL View
-- One row per billable unit of vw_usage_billable: a request (trace) through one agent, with its session,
-- trace and user IDs, token counts and cost. Gateway and workload records of the request are joined on
-- trace_id; tokens and caller identity come from the gateway when it recorded them (token_source).
--
-- The historical columns keep their names and meaning (prompt_tokens includes cached tokens;
-- completion_tokens excludes reasoning, reported in thoughts_tokens). New columns follow request_cost_usd.
SELECT
  timestamp,
  session_id,
  trace_id,
  user_id,
  agent_id,
  execution_path,
  model,
  input_tokens AS prompt_tokens,
  GREATEST(output_tokens - reasoning_tokens, 0) AS completion_tokens,
  reasoning_tokens AS thoughts_tokens,
  cached_tokens,
  total_tokens,
  -- Per-Request Cost Calculation in USD
  ((GREATEST(input_tokens - cached_tokens, 0) / 1000000.0) * 0.075) +
  ((cached_tokens / 1000000.0) * 0.01875) +
  ((GREATEST(output_tokens - reasoning_tokens, 0) / 1000000.0) * 0.30) +
  ((reasoning_tokens / 1000000.0) * 0.30) AS request_cost_usd,
  -- Attribution (who pays) and provenance
  billed_team,
  verified,
  attribution,
  caller_principal,
  caller_team,
  workload_team,
  token_source,
  gateway_records,
  workload_records,
  llm_calls,
  gateway_latency_ms,
  record_origins,
  workload_project,
  workload_project_team,
  caller_project_id,
  declared_team
FROM
  `${dataset_id}.vw_usage_billable`
ORDER BY
  timestamp DESC;
