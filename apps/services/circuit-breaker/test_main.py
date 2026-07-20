import base64
import json
import unittest
from main import app

class TestCircuitBreaker(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        self.client = app.test_client()

    def test_pubsub_push_handling(self):
        alert_payload = {
            "incident": {
                "incident_id": "test-incident-9999",
                "policy_name": "[Esmeralda dev] Runaway Agent Loop - 50k Token Cap Exceeded",
                "summary": "Single inference request breached 50,000 token budget cap."
            }
        }
        encoded_data = base64.b64encode(json.dumps(alert_payload).encode("utf-8")).decode("utf-8")
        pubsub_envelope = {
            "message": {
                "data": encoded_data,
                "messageId": "msg-123456789"
            }
        }

        response = self.client.post("/pubsub/push", json=pubsub_envelope)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.decode("utf-8"), "OK")

if __name__ == "__main__":
    unittest.main()
