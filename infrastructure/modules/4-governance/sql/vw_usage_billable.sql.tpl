-- vw_usage_billable: one billable unit per (trace_id, agent), built from vw_usage_normalized.
--
-- When gateways are present, their records are the primary source for tokens and identity; workload
-- records are the floor on `direct` hops (observability design, section 7). Records of one request
-- through one agent share trace_id and agent_key (workload.name for both: producers set a gateway
-- record's workload.name to the agent the usage is attributed to, e.g. the target agent behind a
-- partner proxy, or the calling agent on the model hop). Within a unit:
--   * tokens:   gateway records when they carry tokens; else the workload's contract llm.call records;
--               else legacy (pre-contract) token events. While producers emit both the contract and the
--               legacy events (transition), the contract records win and nothing is counted twice;
--   * identity: the first verified gateway caller, else the first verified workload caller, else the
--               best remaining attribution (declared, unattributed, legacy);
--   * model:    the gateway's model when known, else the workload's.
-- agent.invocation records are never billed: they roll up the llm.calls of the invocation.
-- Records without a trace_id cannot be joined: each llm.call / gateway record is its own unit.
-- tool.call records carry no tokens and are not billable units (see usage_events for tool analytics).
WITH
usage AS (
  SELECT
    *,
    CASE
      WHEN source = 'gateway' THEN 'gateway'
      WHEN record_origin = 'usage_events' THEN 'workload'
      ELSE 'legacy'
    END AS token_tier,
    CASE attribution
      WHEN 'verified' THEN 0
      WHEN 'principal_table' THEN 0
      WHEN 'declared' THEN 1
      WHEN 'unattributed' THEN 2
      ELSE 3
    END AS attribution_rank
  FROM `${dataset_id}.vw_usage_normalized`
  WHERE event IN ('llm.call', 'gateway.request')
),

traced AS (
  SELECT
    CONCAT(trace_id, '|', COALESCE(agent_key, '')) AS unit_key,
    MIN(timestamp) AS timestamp,
    ANY_VALUE(trace_id) AS trace_id,
    ANY_VALUE(agent_key) AS agent_key,
    ARRAY_AGG(agent_id IGNORE NULLS ORDER BY IF(source = 'workload', 0, 1), timestamp LIMIT 1)[SAFE_OFFSET(0)] AS agent_id,
    ARRAY_AGG(session_id IGNORE NULLS ORDER BY timestamp LIMIT 1)[SAFE_OFFSET(0)] AS session_id,
    ARRAY_AGG(user_id IGNORE NULLS ORDER BY timestamp LIMIT 1)[SAFE_OFFSET(0)] AS user_id,
    ARRAY_AGG(execution_path IGNORE NULLS ORDER BY timestamp LIMIT 1)[SAFE_OFFSET(0)] AS execution_path,
    ARRAY_AGG(workload_team IGNORE NULLS ORDER BY IF(source = 'workload', 0, 1), timestamp LIMIT 1)[SAFE_OFFSET(0)] AS workload_team,
    ARRAY_AGG(model IGNORE NULLS ORDER BY IF(source = 'gateway', 0, 1), timestamp LIMIT 1)[SAFE_OFFSET(0)] AS model,
    ARRAY_AGG(
      STRUCT(billed_team, verified, attribution, caller_principal, caller_team)
      ORDER BY attribution_rank, IF(source = 'gateway', 0, 1), timestamp
      LIMIT 1
    )[OFFSET(0)] AS who,
    MAX(IF(source = 'gateway', latency_ms, NULL)) AS gateway_latency_ms,
    COUNTIF(source = 'gateway') AS gateway_records,
    COUNTIF(source = 'workload') AS workload_records,
    COUNTIF(event = 'llm.call') AS llm_calls,
    ARRAY_AGG(DISTINCT record_origin) AS record_origins,
    -- Token totals per tier: [input, output, cached, reasoning, total].
    STRUCT(
      SUM(IF(token_tier = 'gateway', input_tokens, 0)) AS input_tokens,
      SUM(IF(token_tier = 'gateway', output_tokens, 0)) AS output_tokens,
      SUM(IF(token_tier = 'gateway', cached_tokens, 0)) AS cached_tokens,
      SUM(IF(token_tier = 'gateway', reasoning_tokens, 0)) AS reasoning_tokens,
      SUM(IF(token_tier = 'gateway', total_tokens, 0)) AS total_tokens
    ) AS gw,
    STRUCT(
      SUM(IF(token_tier = 'workload', input_tokens, 0)) AS input_tokens,
      SUM(IF(token_tier = 'workload', output_tokens, 0)) AS output_tokens,
      SUM(IF(token_tier = 'workload', cached_tokens, 0)) AS cached_tokens,
      SUM(IF(token_tier = 'workload', reasoning_tokens, 0)) AS reasoning_tokens,
      SUM(IF(token_tier = 'workload', total_tokens, 0)) AS total_tokens
    ) AS wl,
    STRUCT(
      SUM(IF(token_tier = 'legacy', input_tokens, 0)) AS input_tokens,
      SUM(IF(token_tier = 'legacy', output_tokens, 0)) AS output_tokens,
      SUM(IF(token_tier = 'legacy', cached_tokens, 0)) AS cached_tokens,
      SUM(IF(token_tier = 'legacy', reasoning_tokens, 0)) AS reasoning_tokens,
      SUM(IF(token_tier = 'legacy', total_tokens, 0)) AS total_tokens
    ) AS leg
  FROM usage
  WHERE trace_id IS NOT NULL
  GROUP BY unit_key
),

traced_units AS (
  SELECT
    * EXCEPT (gw, wl, leg),
    CASE
      WHEN gw.total_tokens > 0 THEN 'gateway'
      WHEN wl.total_tokens > 0 THEN 'workload'
      WHEN leg.total_tokens > 0 THEN 'legacy'
      ELSE 'none'
    END AS token_source,
    CASE
      WHEN gw.total_tokens > 0 THEN gw
      WHEN wl.total_tokens > 0 THEN wl
      ELSE leg
    END AS tokens
  FROM traced
),

untraced_units AS (
  SELECT
    COALESCE(CONCAT('record|', record_id), CONCAT('row|', GENERATE_UUID())) AS unit_key,
    timestamp,
    CAST(NULL AS STRING) AS trace_id,
    agent_key,
    agent_id,
    session_id,
    user_id,
    execution_path,
    workload_team,
    model,
    STRUCT(billed_team, verified, attribution, caller_principal, caller_team) AS who,
    IF(source = 'gateway', latency_ms, NULL) AS gateway_latency_ms,
    IF(source = 'gateway', 1, 0) AS gateway_records,
    IF(source = 'workload', 1, 0) AS workload_records,
    IF(event = 'llm.call', 1, 0) AS llm_calls,
    [record_origin] AS record_origins,
    token_tier AS token_source,
    STRUCT(input_tokens, output_tokens, cached_tokens, reasoning_tokens, total_tokens) AS tokens
  FROM usage
  WHERE trace_id IS NULL
),

units AS (
  SELECT * FROM traced_units
  UNION ALL
  SELECT * FROM untraced_units
)

SELECT
  timestamp,
  unit_key,
  trace_id,
  session_id,
  user_id,
  agent_id,
  agent_key,
  execution_path,
  model,
  workload_team,
  who.billed_team AS billed_team,
  who.verified AS verified,
  who.attribution AS attribution,
  who.caller_principal AS caller_principal,
  who.caller_team AS caller_team,
  token_source,
  gateway_records,
  workload_records,
  llm_calls,
  record_origins,
  gateway_latency_ms,
  COALESCE(tokens.input_tokens, 0) AS input_tokens,
  COALESCE(tokens.output_tokens, 0) AS output_tokens,
  COALESCE(tokens.cached_tokens, 0) AS cached_tokens,
  COALESCE(tokens.reasoning_tokens, 0) AS reasoning_tokens,
  COALESCE(tokens.total_tokens, 0) AS total_tokens
FROM units
