import pytest
import sys
import os
import asyncio
from httpx import AsyncClient, ASGITransport
import site

# Ensure .venv site-packages comes first before local workspace dirs
venv_site = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), ".venv", "lib", "python3.13", "site-packages")
if os.path.exists(venv_site):
    sys.path.insert(0, venv_site)

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(1, SERVER_DIR)

from server import app
from agent_app import create_a2a_app

@pytest.mark.asyncio
async def test_agent_card_endpoint():
    """Verify GET /api/a2a/v1/card returns 200 OK and valid card."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/a2a/v1/card")
        assert response.status_code == 200, f"Expected 200 OK, got {response.status_code}: {response.text}"
        data = response.json()
        assert "name" in data
        assert data["preferredTransport"] == "JSON-RPC"

@pytest.mark.asyncio
async def test_jsonrpc_message_send_endpoint():
    """Verify POST /api/a2a/v1/message:send handles JSON-RPC payload."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        payload = {
            "jsonrpc": "2.0",
            "method": "SendMessage",
            "id": "test-req-1",
            "params": {
                "message": {
                    "messageId": "msg-1",
                    "role": "ROLE_USER",
                    "content": [{"text": "Hello, local test!"}]
                },
                "configuration": {"blocking": True}
            }
        }
        response = await client.post("/api/a2a/v1/message:send", json=payload)
        assert response.status_code == 200, f"Expected 200 OK, got {response.status_code}: {response.text}"
        res_json = response.json()
        assert "jsonrpc" in res_json or "result" in res_json or "error" in res_json
