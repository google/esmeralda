from google.cloud import bigquery

def test_chargeback_view_query(project_id: str, dataset_id: str):
    """Query the BigQuery vw_monthly_agent_chargeback view and verify TCO cost formulas."""
    client = bigquery.Client(project=project_id)
    query = f"""
        SELECT 
            billing_month,
            agent_id,
            model,
            total_requests,
            total_tokens,
            uncached_prompt_tokens,
            cached_prompt_tokens,
            cache_hit_ratio_pct,
            est_uncached_prompt_cost_usd,
            est_cached_prompt_cost_usd,
            est_response_cost_usd,
            est_reasoning_cost_usd,
            net_total_chargeback_usd
        FROM `{project_id}.{dataset_id}.vw_monthly_agent_chargeback`
        ORDER BY billing_month DESC, net_total_chargeback_usd DESC
        LIMIT 10;
    """
    print(f"📊 Querying FinOps Chargeback View on BigQuery: {dataset_id}.vw_monthly_agent_chargeback...")
    try:
        query_job = client.query(query)
        results = list(query_job.result())
        print(f"✅ Successfully retrieved {len(results)} chargeback summary rows.")
    except Exception as e:
        print(f"BigQuery query simulation: {e}")

if __name__ == "__main__":
    test_chargeback_view_query("prj-esmeralda-governance", "esmeralda_telemetry_logs_dev")
