# app/agents/a2a-agent/export_card.py
import json
import sys
import os

# Insert current dir to sys.path to import agent_app
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_app import create_a2a_app

app = create_a2a_app()
card = app.agent_card

# Force jsonrpc transport
card.preferred_transport = "jsonrpc"
card.url = "/a2a/agent"
card.supports_authenticated_extended_card = True

# Also register it in additional interfaces
from a2a.types import AgentInterface
card.additional_interfaces = [
    AgentInterface(transport="jsonrpc", url="/a2a/agent")
]

card_data = card.model_dump(mode="json", exclude_none=True)

target_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent", "agent.json")
with open(target_path, "w") as f:
    json.dump(card_data, f, indent=2)

print(f"Agent card exported to {target_path}")
