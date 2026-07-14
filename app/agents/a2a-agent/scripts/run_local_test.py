import asyncio
import os
import sys
from httpx import AsyncClient, ASGITransport

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVER_DIR)

from server import app

async def run_tests():
    print("🧪 Running Local A2A REST Fast Integration Tests...")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Test Agent Card
        resp_card = await client.get("/api/a2a/v1/card")
        assert resp_card.status_code == 200, f"Card endpoint failed: {resp_card.status_code} - {resp_card.text}"
        card_data = resp_card.json()
        print(f"✅ Agent Card Endpoint: 200 OK (preferredTransport: {card_data.get('preferredTransport')})")
        assert card_data.get("preferredTransport") == "HTTP+JSON", f"Expected preferredTransport HTTP+JSON, got {card_data.get('preferredTransport')}"

        # 2. Test REST Message Send Endpoint
        payload = {
            "message": {
                "messageId": "msg-1",
                "role": "ROLE_USER",
                "content": [{"text": "Hello, local fast REST test!"}]
            },
            "configuration": {"blocking": True}
        }
        resp_msg = await client.post("/api/a2a/v1/message:send", json=payload)
        assert resp_msg.status_code == 200, f"Message send endpoint failed: {resp_msg.status_code} - {resp_msg.text}"
        res_json = resp_msg.json()
        print(f"✅ Message Send Endpoint: 200 OK (REST Response: {list(res_json.keys())})")

    print("🎉 ALL LOCAL INTEGRATION TESTS PASSED IN <1 SECOND!")

if __name__ == "__main__":
    asyncio.run(run_tests())
