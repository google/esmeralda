# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

resource "google_bigquery_table" "chat_view" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.agent_logs.dataset_id
  table_id            = "v_chat_history"
  deletion_protection = false

  view {
    query          = <<SQL
WITH all_logs AS (
  SELECT 
    timestamp,
    trace,
    UPPER(JSON_VALUE(jsonPayload, '$.content.role')) as log_role,
    CASE 
      WHEN JSON_VALUE(labels, '$.event_name') = 'gen_ai.choice' THEN 'SOURCE_CHOICE'
      ELSE 'SOURCE_USER_MSG'
    END as source_table,
    
    -- Extrai Texto (Pega o item 0 do array parts)
    JSON_VALUE(jsonPayload, '$.content.parts[0].text') as text_content,
    
    -- Extrai Function Call Name
    JSON_VALUE(jsonPayload, '$.content.parts[0].function_call.name') as fc_name,
    
    -- Extrai Function Call Args (Usa TO_JSON_STRING para o struct args)
    TO_JSON_STRING(jsonPayload.content.parts[0].function_call.args) as fc_args,
    
    -- Extrai Function Response Name
    JSON_VALUE(jsonPayload, '$.content.parts[0].function_response.name') as fr_name,
    
    -- Extrai Function Response Result (Usa TO_JSON_STRING para o struct response)
    TO_JSON_STRING(jsonPayload.content.parts[0].function_response.response) as fr_result
  FROM 
    `${var.project_id}.${google_bigquery_dataset.agent_logs.dataset_id}.aiplatform_googleapis_com_reasoning_engine_stdout`
  WHERE 
    JSON_VALUE(labels, '$.event_name') IN ('gen_ai.user.message', 'gen_ai.choice')
),

classified_events AS (
  SELECT
    timestamp,
    trace,
    
    CASE 
      WHEN fc_name IS NOT NULL THEN 'TOOL_INPUT'
      WHEN fr_name IS NOT NULL THEN 'TOOL_OUTPUT'
      WHEN source_table = 'SOURCE_CHOICE' THEN 'MODEL'
      WHEN log_role = 'MODEL' THEN 'MODEL'
      ELSE 'USER'
    END as role,

    CASE 
      WHEN fc_name IS NOT NULL THEN fc_args
      WHEN fr_name IS NOT NULL THEN fr_result
      ELSE text_content
    END as content,

    COALESCE(fc_name, fr_name) as tool_name
  FROM all_logs
)

SELECT 
  timestamp,
  REGEXP_EXTRACT(trace, r'/([a-zA-Z0-9]+)$') as trace,
  role,
  CASE 
    WHEN role = 'USER' THEN content
    WHEN role = 'MODEL' THEN content
    WHEN role = 'SYSTEM' THEN content
    WHEN role = 'TOOL_INPUT' THEN CONCAT('➡️ INPUT (', tool_name, '): ', content)
    WHEN role = 'TOOL_OUTPUT' THEN CONCAT('⬅️ OUTPUT (', tool_name, '): ', SUBSTR(content, 0, 100), '...')
    ELSE CONCAT(role, ': ', content)
  END as chat_display
FROM classified_events
WHERE 
  content IS NOT NULL 
  AND content != 'null'
ORDER BY timestamp ASC
SQL
    use_legacy_sql = false
  }

  labels = {
    "created_by" = "terraform"
  }

  depends_on = [
    time_sleep.wait_30_seconds
  ]
}

resource "google_bigquery_table" "finops_view" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.agent_logs.dataset_id
  table_id            = "v_finops_billing"
  deletion_protection = false

  view {
    query          = <<SQL
WITH caller_contexts AS (
  SELECT 
    REGEXP_EXTRACT(trace, r'/([a-zA-Z0-9]+)$') as trace_id,
    COALESCE(
      JSON_VALUE(jsonPayload, '$.project_id'),
      REGEXP_EXTRACT(textPayload, r"['\"]project_id['\"]:\s*['\"]([^'\"]+)['\"]")
    ) as project_id,
    COALESCE(
      JSON_VALUE(jsonPayload, '$.agent_name'),
      REGEXP_EXTRACT(textPayload, r"['\"]agent_name['\"]:\s*['\"]([^'\"]+)['\"]")
    ) as agent_name,
    ROW_NUMBER() OVER (PARTITION BY REGEXP_EXTRACT(trace, r'/([a-zA-Z0-9]+)$') ORDER BY timestamp DESC) as rn
  FROM 
    `${var.project_id}.${google_bigquery_dataset.agent_logs.dataset_id}.aiplatform_googleapis_com_reasoning_engine_stdout`
  WHERE 
    JSON_VALUE(jsonPayload, '$.message') LIKE '%Injecting caller context as OTel baggage%'
    OR textPayload LIKE '%Injecting caller context as OTel baggage%'
),

deduped_caller_contexts AS (
  SELECT trace_id, project_id, agent_name
  FROM caller_contexts
  WHERE rn = 1
),

token_usages AS (
  SELECT 
    trace_id,
    CAST(JSON_VALUE(content.usage.prompt) AS INT64) as input_tokens,
    CAST(JSON_VALUE(content.usage.completion) AS INT64) as output_tokens,
    CAST(JSON_VALUE(content.usage.total) AS INT64) as total_tokens
  FROM 
    `${var.project_id}.${google_bigquery_dataset.agent_logs.dataset_id}.agent_events`
  WHERE 
    event_type = 'LLM_RESPONSE'
)

SELECT 
  COALESCE(c.project_id, 'unknown-team') as team_project_id,
  COALESCE(c.agent_name, 'unknown-agent') as team_agent_name,
  SUM(t.input_tokens) as total_input_tokens,
  SUM(t.output_tokens) as total_output_tokens,
  SUM(t.total_tokens) as total_tokens,
  COUNT(DISTINCT t.trace_id) as total_requests
FROM 
  token_usages t
LEFT JOIN 
  deduped_caller_contexts c
ON 
  t.trace_id = c.trace_id
GROUP BY 
  1, 2
ORDER BY 
  total_tokens DESC
SQL
    use_legacy_sql = false
  }

  labels = {
    "created_by" = "terraform"
  }

  depends_on = [
    time_sleep.wait_30_seconds
  ]
}

resource "google_bigquery_dataset" "trace_analytics" {
  project                     = var.project_id
  dataset_id                  = "trace_analytics"
  friendly_name               = "Esmeralda Trace Analytics"
  description                 = "Analytical views and tables for Esmeralda traces"
  location                    = "US"
  default_table_expiration_ms = 31536000000
  delete_contents_on_destroy  = true
}

resource "google_bigquery_table" "trace_costs_view" {
  count               = var.enable_trace_logging_link ? 1 : 0
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.trace_analytics.dataset_id
  table_id            = "v_trace_costs"
  deletion_protection = false

  view {
    query          = <<SQL
SELECT 
  trace_id,
  MIN(start_time) AS trace_start_time,
  TIMESTAMP_DIFF(MAX(end_time), MIN(start_time), MILLISECOND) AS trace_duration_ms,
  
  -- Caller Context (Injected via Baggage)
  MAX(JSON_VALUE(attributes, '$."caller.project_id"')) AS caller_project_id,
  MAX(JSON_VALUE(attributes, '$."caller.agent_name"')) AS caller_agent_name,
  MAX(JSON_VALUE(attributes, '$."user.id"')) AS user_id,
  
  -- Model & Telemetry
  ARRAY_AGG(DISTINCT JSON_VALUE(attributes, '$."gen_ai.request.model"') IGNORE NULLS) AS models_used,
  COUNTIF(name LIKE 'generate_content%') AS total_llm_calls,
  
  -- Token Usage
  SUM(CAST(JSON_VALUE(attributes, '$."gen_ai.usage.input_tokens"') AS INT64)) AS total_input_tokens,
  SUM(CAST(JSON_VALUE(attributes, '$."gen_ai.usage.output_tokens"') AS INT64)) AS total_output_tokens,
  SUM(CAST(JSON_VALUE(attributes, '$."gen_ai.usage.experimental.reasoning_tokens"') AS INT64)) AS total_reasoning_tokens,
  SUM(
    COALESCE(CAST(JSON_VALUE(attributes, '$."gen_ai.usage.input_tokens"') AS INT64), 0) + 
    COALESCE(CAST(JSON_VALUE(attributes, '$."gen_ai.usage.output_tokens"') AS INT64), 0)
  ) AS total_tokens,
  
  -- Cost Calculations (Gemini Retail Pricing per 1 Million Tokens: Flash=$0.075/$0.30, Pro=$1.25/$5.00)
  SUM(
    CASE 
      WHEN JSON_VALUE(attributes, '$."gen_ai.request.model"') LIKE '%flash%' THEN 
        COALESCE(CAST(JSON_VALUE(attributes, '$."gen_ai.usage.input_tokens"') AS INT64), 0) * 0.000000075
      WHEN JSON_VALUE(attributes, '$."gen_ai.request.model"') LIKE '%pro%' THEN 
        COALESCE(CAST(JSON_VALUE(attributes, '$."gen_ai.usage.input_tokens"') AS INT64), 0) * 0.00000125
      ELSE 
        COALESCE(CAST(JSON_VALUE(attributes, '$."gen_ai.usage.input_tokens"') AS INT64), 0) * 0.000000075 -- default flash
    END
  ) AS estimated_input_cost_usd,
  
  SUM(
    CASE 
      WHEN JSON_VALUE(attributes, '$."gen_ai.request.model"') LIKE '%flash%' THEN 
        COALESCE(CAST(JSON_VALUE(attributes, '$."gen_ai.usage.output_tokens"') AS INT64), 0) * 0.00000030
      WHEN JSON_VALUE(attributes, '$."gen_ai.request.model"') LIKE '%pro%' THEN 
        COALESCE(CAST(JSON_VALUE(attributes, '$."gen_ai.usage.output_tokens"') AS INT64), 0) * 0.00000500
      ELSE 
        COALESCE(CAST(JSON_VALUE(attributes, '$."gen_ai.usage.output_tokens"') AS INT64), 0) * 0.00000030 -- default flash
    END
  ) AS estimated_output_cost_usd,
  
  SUM(
    CASE 
      -- Input Costs
      WHEN JSON_VALUE(attributes, '$."gen_ai.request.model"') LIKE '%flash%' THEN 
        COALESCE(CAST(JSON_VALUE(attributes, '$."gen_ai.usage.input_tokens"') AS INT64), 0) * 0.000000075
      WHEN JSON_VALUE(attributes, '$."gen_ai.request.model"') LIKE '%pro%' THEN 
        COALESCE(CAST(JSON_VALUE(attributes, '$."gen_ai.usage.input_tokens"') AS INT64), 0) * 0.00000125
      ELSE 
        COALESCE(CAST(JSON_VALUE(attributes, '$."gen_ai.usage.input_tokens"') AS INT64), 0) * 0.000000075
    END +
    CASE 
      -- Output Costs
      WHEN JSON_VALUE(attributes, '$."gen_ai.request.model"') LIKE '%flash%' THEN 
        COALESCE(CAST(JSON_VALUE(attributes, '$."gen_ai.usage.output_tokens"') AS INT64), 0) * 0.00000030
      WHEN JSON_VALUE(attributes, '$."gen_ai.request.model"') LIKE '%pro%' THEN 
        COALESCE(CAST(JSON_VALUE(attributes, '$."gen_ai.usage.output_tokens"') AS INT64), 0) * 0.00000500
      ELSE 
        COALESCE(CAST(JSON_VALUE(attributes, '$."gen_ai.usage.output_tokens"') AS INT64), 0) * 0.00000030
    END
  ) AS estimated_total_cost_usd

FROM 
  `${var.project_id}.esmeralda_linked_traces._AllSpans`
GROUP BY 
  trace_id
HAVING 
  total_tokens > 0
SQL
    use_legacy_sql = false
  }

  labels = {
    "created_by" = "terraform"
  }

  depends_on = [
    time_sleep.wait_30_seconds,
    google_logging_linked_dataset.linked_traces,
    google_bigquery_dataset.trace_analytics
  ]
}


