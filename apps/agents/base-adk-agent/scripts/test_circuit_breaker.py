#!/usr/bin/env python3
"""
Automated Test for IAM Circuit Breaker Microservice.
Simulates a Pub/Sub alert push notification envelope from Cloud Monitoring.
"""

import base64
import json
import urllib.request
import sys

def test_circuit_breaker_endpoint(target_url: str):
    print(f"⚡ Testing Circuit Breaker endpoint: {target_url}")

    # Simulated Cloud Monitoring Incident Envelope
    alert_payload = {
        "incident": {
            "incident_id": "test-incident-9999",
            "policy_name": "[Esmeralda dev] Runaway Agent Loop - 50k Token Cap Exceeded",
            "condition_name": "Single Request Token Budget Limit",
            "resource_name": "projects/esmeralda-governance-3a3d",
            "summary": "Single inference request breached 50,000 token budget cap.",
            "state": "open"
        }
    }

    # Encode payload into Pub/Sub message format
    encoded_data = base64.b64encode(json.dumps(alert_payload).encode("utf-8")).decode("utf-8")
    pubsub_envelope = {
        "message": {
            "data": encoded_data,
            "messageId": "msg-123456789"
        },
        "subscription": "projects/esmeralda-governance-3a3d/subscriptions/esmeralda-circuit-breaker-push-dev"
    }

    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(target_url, data=json.dumps(pubsub_envelope).encode("utf-8"), headers=headers)

    try:
        with urllib.request.urlopen(req) as response:
            status = response.getcode()
            body = response.read().decode("utf-8")
            print(f"✅ Response Status: {status}")
            print(f"✅ Response Body: {body}")
            assert status == 200, f"Expected HTTP 200, got {status}"
            print("🎉 Circuit Breaker Endpoint Test Passed Successfully!")
    except Exception as e:
        print(f"❌ Circuit Breaker Test Failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080/pubsub/push"
    test_circuit_breaker_endpoint(url)
