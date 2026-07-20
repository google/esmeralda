# Centralized Observability, Metrics Dashboards & FinOps Guide

## Overview

Esmeralda centralizes all monitoring, alerting, log archival, and token cost analytics in **`prj-esmeralda-governance`** following a Hub-and-Spoke Telemetry pattern.

```
                     ┌───────────────────────────────────────────────┐
                     │          Hub: prj-esmeralda-governance        │
                     ├───────────────────────────────────────────────┤
  Spoke Projects ───►│ • Cloud Monitoring Dashboards (Golden Signals)│
  (Root, A2A,        │ • Real-time FinOps Token Analytics Dashboard   │
   Gateway, MCPs)    │ • BigQuery Telemetry Dataset & Chargeback View│
                     │ • Cloud DLP PII Redaction Template           │
                     │ • Coldline GCS Long-Term Archive Bucket       │
                     └───────────────────────────────────────────────┘
```

---

## Provisioned Cloud Monitoring Dashboards (IaC)

### 1. Agent Platform Golden Signals & Health Dashboard
* **Dashboard ID**: `projects/644727420518/dashboards/9ce0dd33-650e-4c42-acd3-31ab034ab680`
* **Widgets Included**:
  1. **Latency Percentiles (P50, P95, P99)**: Tracks Cloud Run container response time distributions.
  2. **Agent Request Volume & Error Rates**: Monitors HTTP 2xx, 4xx, and 5xx status code rates.
  3. **Vertex AI Reasoning Engine QPS & 429 Quota Errors**: Real-time monitor for API rate limit exhaustion.
  4. **Cloud Run Container Concurrency**: Instance count & active requests per revision.

### 2. FinOps & Real-Time Token Budget Dashboard
* **Dashboard ID**: `projects/644727420518/dashboards/99d5f7a5-fccd-4de2-85c2-d33c68d94b75`
* **Widgets Included**:
  1. **Real-Time Token Consumption Rate (Tokens/Min)**: Delta metric extracting total tokens per request.
  2. **P99 Token Consumption Spike Detector**: Identifies runaway agent reasoning loops exceeding 50,000 tokens/req.

---

## BigQuery FinOps Chargeback Analytics

Raw token consumption events are ingested into partitioned BigQuery table `genai_token_events`. 

### Summary SQL View: `vw_monthly_agent_chargeback`
Calculates net monthly chargeback based on Gemini 2.5 Flash pricing:
- **Uncached Prompt Tokens**: $0.075 per 1M tokens
- **Cached Prompt Tokens**: $0.01875 per 1M tokens (75% savings)
- **Response Tokens**: $0.30 per 1M tokens
- **Reasoning Tokens**: $0.30 per 1M tokens

#### Sample Query:
```sql
SELECT 
  billing_month,
  agent_id,
  model,
  total_requests,
  total_tokens,
  cache_hit_ratio_pct,
  net_total_chargeback_usd
FROM `esmeralda-governance-3a3d.esmeralda_telemetry_logs_dev.vw_monthly_agent_chargeback`
ORDER BY billing_month DESC;
```

---

## Long-Term GCS Archival

All logs routed through central sinks are dual-written to:
- **GCS Archival Bucket**: `esmeralda-telemetry-archive-esmeralda-governance-3a3d`
- **Storage Class**: `COLDLINE`
- **Retention / Lifecycle**: Automated deletion after **365 days**.

---

## Active Alert Policies & Automated Remediation

| Alert Policy | Threshold | Per-Series Aligner | Target Action |
| :--- | :--- | :--- | :--- |
| **Runaway Agent Loop Cap** | `> 50,000 tokens/req` | `ALIGN_PERCENTILE_99` (60s) | Pushes alert to Pub/Sub `esmeralda-monitoring-alerts-dev`. Triggers **Circuit Breaker Service** to isolate session. |
| **Reasoning Engine Quota** | `> 80 QPS` or HTTP 429 | `ALIGN_RATE` (60s) | Notifies SecOps team email (`esmeralda.secops@google.com`). |
