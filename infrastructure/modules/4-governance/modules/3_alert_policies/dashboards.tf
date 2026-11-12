# Cloud Monitoring Dashboards (Golden Signals & FinOps Analytics)

# 1. Operational Golden Signals Dashboard
resource "google_monitoring_dashboard" "agent_golden_signals" {
  project        = var.governance_project_id
  dashboard_json = <<EOF
{
  "displayName": "[Esmeralda ${var.environment}] Agent Platform - Golden Signals & Health",
  "gridLayout": {
    "columns": "2",
    "widgets": [
      {
        "title": "Cloud Run & Agent Service Latencies (P50, P95, P99)",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "timeSeriesFilter": {
                  "filter": "resource.type=\"cloud_run_revision\" AND metric.type=\"run.googleapis.com/request_latencies\"",
                  "aggregation": {
                    "alignmentPeriod": "60s",
                    "perSeriesAligner": "ALIGN_PERCENTILE_95"
                  }
                }
              },
              "plotType": "LINE"
            }
          ],
          "timeshiftDuration": "0s"
        }
      },
      {
        "title": "Agent Request Volume & HTTP Status Rates",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "timeSeriesFilter": {
                  "filter": "resource.type=\"cloud_run_revision\" AND metric.type=\"run.googleapis.com/request_count\"",
                  "aggregation": {
                    "alignmentPeriod": "60s",
                    "perSeriesAligner": "ALIGN_RATE"
                  }
                }
              },
              "plotType": "STACKED_BAR"
            }
          ]
        }
      },
      {
        "title": "Vertex AI Reasoning Engine Query Rates & 429 Quotas",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "timeSeriesFilter": {
                  "filter": "resource.type=\"aiplatform.googleapis.com/ReasoningEngine\" AND metric.type=\"aiplatform.googleapis.com/reasoning_engine/request_count\"",
                  "aggregation": {
                    "alignmentPeriod": "60s",
                    "perSeriesAligner": "ALIGN_RATE"
                  }
                }
              },
              "plotType": "LINE"
            }
          ]
        }
      },
      {
        "title": "Cloud Run Container Instance Concurrency",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "timeSeriesFilter": {
                  "filter": "resource.type=\"cloud_run_revision\" AND metric.type=\"run.googleapis.com/container/instance_count\"",
                  "aggregation": {
                    "alignmentPeriod": "60s",
                    "perSeriesAligner": "ALIGN_MEAN"
                  }
                }
              },
              "plotType": "LINE"
            }
          ]
        }
      },
      {
        "title": "Entry Point Requests by Response Code Class",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "timeSeriesFilter": {
                  "filter": "resource.type=\"internal_http_lb_rule\" AND metric.type=\"loadbalancing.googleapis.com/https/internal/request_count\"",
                  "aggregation": {
                    "alignmentPeriod": "60s",
                    "perSeriesAligner": "ALIGN_RATE",
                    "crossSeriesReducer": "REDUCE_SUM",
                    "groupByFields": ["metric.label.response_code_class"]
                  }
                }
              },
              "plotType": "STACKED_AREA"
            }
          ]
        }
      },
      {
        "title": "Agent Engine Query Latency P95 (all agents, any framework)",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "timeSeriesFilter": {
                  "filter": "resource.type=\"aiplatform.googleapis.com/ReasoningEngine\" AND metric.type=\"aiplatform.googleapis.com/reasoning_engine/request_latencies\"",
                  "aggregation": {
                    "alignmentPeriod": "60s",
                    "perSeriesAligner": "ALIGN_PERCENTILE_95",
                    "crossSeriesReducer": "REDUCE_MAX",
                    "groupByFields": ["resource.label.reasoning_engine_id"]
                  }
                }
              },
              "plotType": "LINE"
            }
          ]
        }
      },
      {
        "title": "ADK Agent Invocation Duration P95 by Agent (native OTel)",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "prometheusQuery": "histogram_quantile(0.95, sum by (le, \"gen_ai.agent.name\") (rate({\"__name__\"=\"gen_ai.invoke_agent.duration_bucket\"}[5m])))"
              },
              "plotType": "LINE"
            }
          ]
        }
      },
      {
        "title": "ADK Tool Executions per Second by Agent & Tool (native OTel)",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "prometheusQuery": "sum by (\"gen_ai.agent.name\", \"gen_ai.tool.name\") (rate({\"__name__\"=\"gen_ai.execute_tool.duration_count\"}[5m]))"
              },
              "plotType": "STACKED_BAR"
            }
          ]
        }
      },
      {
        "title": "ADK Model Tokens per Second by Agent (native OTel, ops view; FinOps uses esmeralda.usage)",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "prometheusQuery": "sum by (\"gen_ai.agent.name\") (rate({\"__name__\"=\"adk.experimental.invoke_agent.total_tokens_sum\"}[5m]))"
              },
              "plotType": "LINE"
            }
          ]
        }
      },
      {
        "title": "GenAI Client Tokens per Second by Model & Token Type (native OTel)",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "prometheusQuery": "sum by (\"gen_ai.request.model\", \"gen_ai.token.type\") (rate({\"__name__\"=\"gen_ai.client.token.usage_sum\"}[5m]))"
              },
              "plotType": "STACKED_AREA"
            }
          ]
        }
      }
    ]
  }
}
EOF
}

# 2. Real-Time FinOps & Token Consumption Dashboard
# Every tile reads the esmeralda.usage log-based metrics (usage/*, main.tf section 3), so it covers every
# producer (agents of any framework, Apigee, Envoy, other clouds). In PromQL a log-based metric is
# logging_googleapis_com:user_<name with / as _>, and a distribution's total is its _sum series.
resource "google_monitoring_dashboard" "finops_token_analytics" {
  project        = var.governance_project_id
  dashboard_json = <<EOF
{
  "displayName": "[Esmeralda ${var.environment}] FinOps - Real-Time Token Budget & Usage",
  "gridLayout": {
    "columns": "2",
    "widgets": [
      {
        "title": "Tokens per Minute by Caller Team (input + output; verified = authenticated)",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "prometheusQuery": "sum by (caller_team, verified) (${local.usage_promql_json["increase|input_tokens_sum|1m"]}) + sum by (caller_team, verified) (${local.usage_promql_json["increase|output_tokens_sum|1m"]})"
              },
              "plotType": "STACKED_AREA"
            }
          ]
        }
      },
      {
        "title": "Tokens per Minute by Workload & Model",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "prometheusQuery": "sum by (workload, model) (${local.usage_promql_json["increase|input_tokens_sum|1m"]}) + sum by (workload, model) (${local.usage_promql_json["increase|output_tokens_sum|1m"]})"
              },
              "plotType": "LINE"
            }
          ]
        }
      },
      {
        "title": "Prompt Cache Hit Token Savings over Time",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "prometheusQuery": "sum by (workload) (${local.usage_promql_json["increase|cached_tokens_sum|1m"]})"
              },
              "plotType": "LINE"
            }
          ]
        }
      },
      {
        "title": "Reasoning (Thinking) Tokens over Time",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "prometheusQuery": "sum by (workload, model) (${local.usage_promql_json["increase|reasoning_tokens_sum|1m"]})"
              },
              "plotType": "LINE"
            }
          ]
        }
      },
      {
        "title": "Tool Calls per Minute by Tool & Status",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "prometheusQuery": "sum by (tool, status) (${local.usage_promql_json["increase|tool_calls|1m"]})"
              },
              "plotType": "STACKED_BAR"
            }
          ]
        }
      },
      {
        "title": "Tool Calls per Minute by MCP Server",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "prometheusQuery": "sum by (server) (${local.usage_promql_json["increase|tool_calls|1m"]})"
              },
              "plotType": "STACKED_BAR"
            }
          ]
        }
      },
      {
        "title": "P99 Input Tokens per Call (Runaway Loop Detector)",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "prometheusQuery": "histogram_quantile(0.99, sum by (le, workload) (${local.usage_promql_json["rate|input_tokens_bucket|5m"]}))"
              },
              "plotType": "LINE"
            }
          ]
        }
      },
      {
        "title": "Unattributed Usage Records (expected: 0)",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "prometheusQuery": "sum by (workload, source) (${local.usage_promql_json["increase|unattributed_records|5m"]})"
              },
              "plotType": "STACKED_BAR"
            }
          ]
        }
      }
    ]
  }
}
EOF
}
