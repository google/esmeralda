import base64
import json
import logging
import os
from flask import Flask, request

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("esmeralda.circuit_breaker")

@app.route("/pubsub/push", methods=["POST"])
def handle_pubsub_push():
    """Handle Pub/Sub alert push notifications from Cloud Monitoring."""
    envelope = request.get_json()
    if not envelope or "message" not in envelope:
        logger.error("Invalid Pub/Sub message envelope")
        return "Bad Request", 400

    pubsub_message = envelope["message"]
    if "data" in pubsub_message:
        try:
            raw_data = base64.b64decode(pubsub_message["data"]).decode("utf-8")
            alert_payload = json.loads(raw_data)
            incident = alert_payload.get("incident", {})
            policy_name = incident.get("policy_name", "Unknown Alert")
            
            logger.warning(f"🚨 CIRCUIT BREAKER TRIGGERED by policy: {policy_name}")
            logger.info(f"Details: {json.dumps(incident)}")
            
            # Action: Revoke API keys or temporary service account roles
            revoke_compromised_credentials(incident)
            return "OK", 200
        except Exception as e:
            logger.error(f"Failed to process circuit breaker payload: {e}")
            return "Internal Error", 500

    return "No Data", 200

def revoke_compromised_credentials(incident: dict):
    """Business logic to trigger secret revocation or IAM policy stripping."""
    logger.info("🔒 Circuit Breaker: Revoking roles/aiplatform.user and API key credentials.")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
