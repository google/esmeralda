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

import logging
import os
import sys
import uvicorn
from fastapi import FastAPI

# Insert current dir to sys.path to import agent_app
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_app import create_a2a_app
from a2a.server.apps import A2ARESTFastAPIApplication

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

a2a_agent_obj = create_a2a_app()
# Preserve url defined under agent_card in agent.yaml before set_up() overwrites it
yaml_card_url = a2a_agent_obj.agent_card.url
a2a_agent_obj.set_up()
if yaml_card_url:
    a2a_agent_obj.agent_card.url = yaml_card_url

server_app = A2ARESTFastAPIApplication(
    agent_card=a2a_agent_obj.agent_card,
    extended_agent_card=a2a_agent_obj.agent_card,
    http_handler=a2a_agent_obj.request_handler,
)
inner_app = server_app.build()

app = FastAPI(title="A2A Mortgage Agent Server")
# Mount inner app under /a2a, /api/a2a, and / for full proxy compatibility
app.mount("/a2a", inner_app)
app.mount("/api/a2a", inner_app)
app.mount("/", inner_app)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    host = os.environ.get("HOST", "0.0.0.0")
    logger.info(f"Starting A2A Mortgage Agent Server on {host}:{port}...")
    uvicorn.run(app, host=host, port=port)
