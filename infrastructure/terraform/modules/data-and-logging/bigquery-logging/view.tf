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
  -- =================================================================
  -- 1. TABELA USER MESSAGE
  -- =================================================================
  SELECT 
    timestamp,
    trace,
    -- JSON_VALUE extrai o valor como STRING direto do caminho JSON
    UPPER(JSON_VALUE(jsonPayload, '$.content.role')) as log_role,
    'SOURCE_USER_MSG' as source_table,
    
    -- Extrai Texto (Pega o item 0 do array parts)
    JSON_VALUE(jsonPayload, '$.content.parts[0].text') as text_content,
    
    -- Extrai Function Call Name
    JSON_VALUE(jsonPayload, '$.content.parts[0].function_call.name') as fc_name,
    
    -- Extrai Function Call Args (Usa JSON_QUERY pois args é um objeto, depois converte para string)
    TO_JSON_STRING(JSON_QUERY(jsonPayload, '$.content.parts[0].function_call.args')) as fc_args,
    
    -- Extrai Function Response Name
    JSON_VALUE(jsonPayload, '$.content.parts[0].function_response.name') as fr_name,
    
    -- Extrai Function Response Result (Objeto complexo)
    TO_JSON_STRING(JSON_QUERY(jsonPayload, '$.content.parts[0].function_response.response.result')) as fr_result
  FROM 
    `${var.project_id}.${google_bigquery_dataset.agent_logs.dataset_id}.gen_ai_user_message`

  UNION ALL

  -- =================================================================
  -- 2. TABELA CHOICE
  -- =================================================================
  SELECT 
    timestamp,
    trace,
    'MODEL' as log_role, 
    'SOURCE_CHOICE' as source_table,
    
    -- Extrai Texto
    JSON_VALUE(jsonPayload, '$.content.parts[0].text') as text_content,
    
    -- Extrai Function Call Name
    JSON_VALUE(jsonPayload, '$.content.parts[0].function_call.name') as fc_name,
    
    -- Extrai Function Call Args
    TO_JSON_STRING(JSON_QUERY(jsonPayload, '$.content.parts[0].function_call.args')) as fc_args,
    
    -- Campos nulos para bater a estrutura do UNION
    CAST(NULL AS STRING) as fr_name,
    CAST(NULL AS STRING) as fr_result
  FROM 
    `${var.project_id}.${google_bigquery_dataset.agent_logs.dataset_id}.gen_ai_choice`
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
