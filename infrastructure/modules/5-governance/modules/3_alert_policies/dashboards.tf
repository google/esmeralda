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
        "title": "Ingress Gateway Synthetic Uptime Check Status",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "timeSeriesFilter": {
                  "filter": "resource.type=\"uptime_url\" AND metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\"",
                  "aggregation": {
                    "alignmentPeriod": "60s",
                    "perSeriesAligner": "ALIGN_NEXT_OLDER"
                  }
                }
              },
              "plotType": "LINE"
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
resource "google_monitoring_dashboard" "finops_token_analytics" {
  project        = var.governance_project_id
  dashboard_json = <<EOF
{
  "displayName": "[Esmeralda ${var.environment}] FinOps - Real-Time Token Budget & Usage",
  "gridLayout": {
    "columns": "2",
    "widgets": [
      {
        "title": "Total LLM Token Consumption Volume over Time",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "timeSeriesQueryLanguage": "fetch aiplatform.googleapis.com/ReasoningEngine | metric 'logging.googleapis.com/user/genai/realtime_token_consumption' | align delta(1m) | sum"
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
                "timeSeriesQueryLanguage": "fetch aiplatform.googleapis.com/ReasoningEngine | metric 'logging.googleapis.com/user/genai/cached_tokens' | align delta(1m) | sum"
              },
              "plotType": "LINE"
            }
          ]
        }
      },
      {
        "title": "Gemini 2.5 Reasoning (Thoughts) Tokens over Time",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "timeSeriesQueryLanguage": "fetch aiplatform.googleapis.com/ReasoningEngine | metric 'logging.googleapis.com/user/genai/thoughts_tokens' | align delta(1m) | sum"
              },
              "plotType": "LINE"
            }
          ]
        }
      },
      {
        "title": "MCP Tool Executions Count over Time",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "timeSeriesFilter": {
                  "filter": "resource.type=\"aiplatform.googleapis.com/ReasoningEngine\" AND metric.type=\"logging.googleapis.com/user/genai/mcp_tool_execution_count\"",
                  "aggregation": {
                    "alignmentPeriod": "60s",
                    "perSeriesAligner": "ALIGN_DELTA",
                    "crossSeriesReducer": "REDUCE_SUM",
                    "groupByFields": [
                      "metric.label.tool_name"
                    ]
                  }
                }
              },
              "plotType": "LINE"
            }
          ]
        }
      },
      {
        "title": "P99 Token Consumption Spike (Runaway Loop Detector)",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "timeSeriesFilter": {
                  "filter": "resource.type=\"aiplatform.googleapis.com/ReasoningEngine\" AND metric.type=\"logging.googleapis.com/user/genai/realtime_token_consumption\"",
                  "aggregation": {
                    "alignmentPeriod": "60s",
                    "perSeriesAligner": "ALIGN_PERCENTILE_99",
                    "crossSeriesReducer": "REDUCE_PERCENTILE_99"
                  }
                }
              },
              "plotType": "LINE"
            }
          ]
        }
      },
      {
        "title": "MCP Tool Execution Frequency per Microservice",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "timeSeriesFilter": {
                  "filter": "resource.type=\"aiplatform.googleapis.com/ReasoningEngine\" AND metric.type=\"logging.googleapis.com/user/genai/mcp_tool_execution_count\"",
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
      }
    ]
  }
}
EOF
}
