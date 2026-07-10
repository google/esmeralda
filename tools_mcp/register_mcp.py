#!/usr/bin/env python3
"""Registers deployed MCP servers into the local/cloud agent tool registry."""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

def main():
    parser = argparse.ArgumentParser(description="Register MCP Server with Esmeralda Agent Registry")
    parser.add_argument("--project_id", required=True, help="GCP Project ID")
    parser.add_argument("--region", required=True, help="GCP Region")
    parser.add_argument("--server_name", required=True, help="MCP Server Name")
    parser.add_argument("--server_url", required=True, help="Cloud Run Service URI")
    args = parser.parse_args()

    print(f"📡 Registering MCP Server '{args.server_name}' ({args.server_url}) in project '{args.project_id}'...")
    
    # Ensure registry file directory exists
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    registry_path = os.path.join(repo_root, "app", "services", "mcp_registry.json")
    os.makedirs(os.path.dirname(registry_path), exist_ok=True)

    registry_data = {}
    if os.path.exists(registry_path):
        try:
            with open(registry_path, "r", encoding="utf-8") as f:
                registry_data = json.load(f)
        except Exception as e:
            print(f"⚠️ Warning: Could not read existing registry at {registry_path}: {e}")

    registry_data[args.server_name] = {
        "project_id": args.project_id,
        "region": args.region,
        "server_url": args.server_url,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "status": "ONLINE"
    }

    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(registry_data, f, indent=2)

    print(f"✅ Successfully registered '{args.server_name}' into {registry_path}!")
    sys.exit(0)

if __name__ == "__main__":
    main()
