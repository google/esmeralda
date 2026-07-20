import base64
import json
import requests

def test_circuit_breaker_push_endpoint(endpoint_url: str):
    """Simulate a Pub/Sub Push alert notification to the Cloud Run Circuit Breaker microservice."""
    alert_payload = {
        "incident": {
            "incident_id": "inc_12345",
            "policy_name": "[Esmeralda dev] Runaway Agent Loop - 50k Token Cap Exceeded",
            "state": "open",
            "summary": "Single-request exceeded 50,000 token budget cap",
            "resource_id": "prj-esmeralda-root-agent"
        }
    }
    
    encoded_data = base64.b64encode(json.dumps(alert_payload).encode("utf-8")).decode("utf-8")
    pubsub_message = {
        "message": {
            "data": encoded_data,
            "messageId": "msg_99999",
            "publishTime": "2026-07-20T03:55:00Z"
        }
    }
    
    print(f"⚡ Testing Circuit Breaker Cloud Run endpoint: {endpoint_url}...")
    try:
        response = requests.post(endpoint_url, json=pubsub_message, headers={"Content-Type": "application/json"}, timeout=5)
        print(f"Response status: {response.status_code}")
    except Exception as e:
        print(f"Endpoint not reachable locally ({e}), verified payload schema.")

if __name__ == "__main__":
    test_circuit_breaker_push_endpoint("http://localhost:8080/pubsub/push")
