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

if "GOOGLE_CLOUD_PROJECT" not in os.environ:
    os.environ["GOOGLE_CLOUD_PROJECT"] = "esmeralda-a2a-918f"

a2a_agent_obj = create_a2a_app()
a2a_agent_obj.set_up()

rest_builder = A2ARESTFastAPIApplication(
    agent_card=a2a_agent_obj.agent_card,
    http_handler=a2a_agent_obj.request_handler,
)

# Build main app at /api/a2a prefix
app = rest_builder.build(rpc_url="/api/a2a")

# Mount sub-apps at /a2a and root to handle any proxy path variation
for prefix in ["/a2a", ""]:
    sub_app = rest_builder.build(rpc_url=prefix)
    app.mount(prefix if prefix else "/root_a2a", sub_app)

@app.get("/health")
@app.get("/")
def health_check():
    return {"status": "ok", "agent": a2a_agent_obj.agent_card.name}

logger.info("A2A REST Server initialized on port 8080 with /health probes.")

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
