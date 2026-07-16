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
import sys
import logging
import traceback
from contextlib import contextmanager

from vertexai import agent_engines
from google.adk.apps import App
from agent import root_agent

from interceptors import (
    BaseInterceptor,
    BaggageTelemetryInterceptor,
    TelemetryFlushInterceptor,
    DynatraceTelemetryInterceptor,
)

# Configure Logging to stdout
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

print("🚀 Loading agent_app.py module...", flush=True)

try:
    # We extend AdkApp to inject custom initialization logic and interceptors
    # when the Agent Engine instance starts up in the cloud.
    class AgentRuntimeApp(agent_engines.AdkApp):
        def __init__(self, *args, interceptors=None, **kwargs):
            super().__init__(*args, **kwargs)
            self.interceptors = interceptors or []

        def set_up(self) -> None:
            import vertexai
            import google.auth

            # Fetch the desired location from the environment, defaulting to global
            location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")

            try:
                credentials, project_id = google.auth.default()
                project = os.environ.get("GOOGLE_CLOUD_PROJECT", project_id)
                logger.info(f"Re-initializing vertexai for project={project}, location={location}")
                vertexai.init(project=project, location=location)

                # Set up direct Cloud Logging client for detailed debug output
                import google.cloud.logging
                self.logging_client = google.cloud.logging.Client(project=project)
                self.logger = self.logging_client.logger(
                    name="python",  # name (str): the name of the custom log in Cloud Logging.
                    labels={"type": "python-logging"},  # Default labels to attach to logs.
                    resource=google.cloud.logging.Resource(
                        type="aiplatform.googleapis.com/ReasoningEngine",
                        labels={
                            "resource_container": project,
                            "location": location,
                            "reasoning_engine_id": os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID", os.environ.get("K_SERVICE", "").split("-")[-1]),
                        },
                    ),
                )
            except Exception as e:
                logger.error(f"Failed to re-initialize vertexai or Cloud Logging: {e}")

            super().set_up()

            # Execute startup hooks for registered interceptors
            for interceptor in self.interceptors:
                interceptor.on_startup(self)

        @contextmanager
        def _intercept_query(self, kwargs: dict):
            tokens = []
            clean_kwargs = kwargs.copy()
            for interceptor in self.interceptors:
                token = interceptor.before_query(clean_kwargs)
                tokens.append((interceptor, token))
            try:
                yield clean_kwargs
            finally:
                for interceptor, token in reversed(tokens):
                    interceptor.after_query(token)

        # --- Synchronous Execution Hooks ---

        def query(self, message: str = None, user_id: str = None, session_id: str = None, **kwargs):
            with self._intercept_query(kwargs) as clean_kwargs:
                return super().query(
                    message=message,
                    user_id=user_id,
                    session_id=session_id,
                    **clean_kwargs
                )

        def stream_query(self, message: str = None, user_id: str = None, session_id: str = None, **kwargs):
            with self._intercept_query(kwargs) as clean_kwargs:
                yield from super().stream_query(
                    message=message,
                    user_id=user_id,
                    session_id=session_id,
                    **clean_kwargs
                )

        # --- Asynchronous Execution Hooks ---

        async def async_query(self, message: str = None, user_id: str = None, session_id: str = None, **kwargs):
            with self._intercept_query(kwargs) as clean_kwargs:
                return await super().async_query(
                    message=message,
                    user_id=user_id,
                    session_id=session_id,
                    **clean_kwargs
                )

        async def async_stream_query(self, message: str = None, user_id: str = None, session_id: str = None, **kwargs):
            with self._intercept_query(kwargs) as clean_kwargs:
                async for event in super().async_stream_query(
                    message=message,
                    user_id=user_id,
                    session_id=session_id,
                    **clean_kwargs
                ):
                    yield event

        async def async_close(self) -> None:
            """Clean up open resources across registered interceptors."""
            logger.info("Closing open resources inside AgentRuntimeApp...")
            for interceptor in reversed(self.interceptors):
                await interceptor.on_shutdown()

    # Wrap the root agent for Vertex AI Agent Engine deployment with Interceptor Pipeline
    agent_runtime_app = AgentRuntimeApp(
        agent=root_agent,
        interceptors=[
            BaggageTelemetryInterceptor(),
            DynatraceTelemetryInterceptor(),
            TelemetryFlushInterceptor(),
        ]
    )
    adk_app = agent_runtime_app

    # Standard ADK App instance
    app = App(
        name="base_adk_agent",
        root_agent=root_agent,
    )

    logger.info("✨ Agent Engine App initialized successfully with Interceptor Pipeline.")

except Exception as e:
    logger.error(f"❌ CRITICAL ERROR initializing agent_app.py: {e}")
    traceback.print_exc(file=sys.stdout)
    raise e