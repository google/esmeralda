# Copyright 2026 Google LLC
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

"""Cloud Logging & Vertex AI Initialization Interceptor."""

import os
import logging
from typing import Any

from interceptors.base import BaseInterceptor

logger = logging.getLogger(__name__)


class CloudLoggingInterceptor(BaseInterceptor):
    """Interceptor that re-initializes Vertex AI and GCP Cloud Logging Client on app startup."""

    def on_startup(self, app: Any) -> None:
        import vertexai
        import google.auth

        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")

        try:
            credentials, project_id = google.auth.default()
            project = os.environ.get("GOOGLE_CLOUD_PROJECT", project_id)
            logger.info(f"Re-initializing vertexai for project={project}, location={location}")
            vertexai.init(project=project, location=location)

            import google.cloud.logging
            app.logging_client = google.cloud.logging.Client(project=project)
            app.logger = app.logging_client.logger(
                name="python",
                labels={"type": "python-logging"},
                resource=google.cloud.logging.Resource(
                    type="aiplatform.googleapis.com/ReasoningEngine",
                    labels={
                        "resource_container": project,
                        "location": location,
                        "reasoning_engine_id": os.environ.get(
                            "GOOGLE_CLOUD_AGENT_ENGINE_ID",
                            os.environ.get("K_SERVICE", "").split("-")[-1]
                        ),
                    },
                ),
            )
            logger.info("✅ Cloud Logging Client re-initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to re-initialize vertexai or Cloud Logging: {e}")
