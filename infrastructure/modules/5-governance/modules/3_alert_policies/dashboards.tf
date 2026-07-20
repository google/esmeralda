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
        "title": "Real-Time Token Consumption Rate (Tokens/Min)",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "timeSeriesFilter": {
                  "filter": "resource.type=\"global\" AND metric.type=\"logging.googleapis.com/user/genai/realtime_token_consumption\"",
                  "aggregation": {
                    "alignmentPeriod": "60s",
                    "perSeriesAligner": "ALIGN_DELTA"
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
                  "filter": "resource.type=\"global\" AND metric.type=\"logging.googleapis.com/user/genai/realtime_token_consumption\"",
                  "aggregation": {
                    "alignmentPeriod": "60s",
                    "perSeriesAligner": "ALIGN_PERCENTILE_99"
                  }
                }
              },
              "plotType": "LINE"
            }
          ]
        }
      },
      {
        "title": "Gemini Context Caching Hits (Cached Tokens Rate)",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "timeSeriesFilter": {
                  "filter": "resource.type=\"global\" AND metric.type=\"logging.googleapis.com/user/genai/cached_tokens\"",
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
        "title": "Gemini 2.5 Reasoning / Thoughts Token Consumption Rate",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "timeSeriesFilter": {
                  "filter": "resource.type=\"global\" AND metric.type=\"logging.googleapis.com/user/genai/thoughts_tokens\"",
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
      }
    ]
  }
}
EOF
}
