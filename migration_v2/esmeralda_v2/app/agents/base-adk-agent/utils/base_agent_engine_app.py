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
import vertexai
import logging
from typing import Any
from vertexai import agent_engines
from google.cloud import logging as google_cloud_logging
from opentelemetry import trace

class BaseAgentEngineApp(agent_engines.AdkApp):
    """
    A reusable base class that minimizes footprint to avoid 
    breaking internal ADK event listeners.
    """

    def set_up(self) -> None:
        if os.getenv("USE_CUSTOM_TELEMETRY", "False").lower() == "true":
            try:
                from utils.telemetry import setup_telemetry
                setup_telemetry(custom_telemetry=True)
            except Exception as e:
                print(f"Telemetry setup failed: {e}")

        super().set_up()