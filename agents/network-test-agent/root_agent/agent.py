# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import requests
import google.auth
from google.adk.agents import Agent

_, project_id = google.auth.default()

def check_connection_with_proxy() -> str:
    """Checks internet connectivity specifically USING the VPC's Secure Web Proxy.
    
    This tool demonstrates the correct way for an Agent Engine with PSC to reach the internet.
    """
    proxy_url = "http://swp.internal.gateway:443"
    proxies = {
        "http": proxy_url,
        "https": proxy_url,
    }
    try:
        response = requests.get("https://ifconfig.me", proxies=proxies, timeout=10)
        return f"Success via Proxy! Public IP (Cloud NAT): {response.text.strip()}"
    except Exception as e:
        return f"Proxy Connection Failed: {str(e)}"

def check_connection_no_proxy() -> str:
    """Checks internet connectivity WITHOUT using any proxy.
    
    This tool demonstrates that Agent Engine natively blocks direct public IP access.
    """
    try:
        # This is expected to fail because it calls a public IP directly.
        response = requests.get("https://ifconfig.me", timeout=10)
        return f"Unexpected Success! Public IP: {response.text.strip()}"
    except Exception as e:
        return f"Direct Connection Failed (As Expected): {str(e)}"

root_agent = Agent(
    name="root_agent",
    model=os.getenv("MODEL", "gemini-2.5-flash"),
    instruction="""You are a technical AI assistant designed to demonstrate Agent Engine networking.
    
    You have two specialized tools to verify connectivity:
    1. 'check_connection_with_proxy': Use this to show how you can reach the internet by routing through the VPC's Proxy VM.
    2. 'check_connection_no_proxy': Use this to demonstrate that direct internet access is blocked by the private network boundary.
    
    If the user asks to verify the networking setup, run both tools and explain why the proxy version succeeds while the direct version fails.""",
    tools=[check_connection_with_proxy, check_connection_no_proxy],
)
