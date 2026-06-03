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

from vertexai import agent_engines
from google.adk.apps import App
from agent import root_agent

# Configure Logging to stdout
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

print("🚀 Loading agent_app.py module...", flush=True)

from opentelemetry.sdk.trace import SpanProcessor, ReadableSpan
from opentelemetry import baggage

class BaggageSpanProcessor(SpanProcessor):
    """
    Custom OpenTelemetry SpanProcessor that automatically copies active Baggage
    attributes (context variables) into Span Attributes (labels), ensuring they
    are exported to Google Cloud Trace and Dynatrace.
    """
    def on_start(self, span: ReadableSpan, parent_context=None) -> None:
        try:
            import google.auth
            import json
            
            # Extract active baggage items using the parent context
            project_id = baggage.get_baggage("caller.project_id", context=parent_context)
            agent_name = baggage.get_baggage("caller.agent_name", context=parent_context)
            
            if project_id or agent_name:
                if project_id:
                    span.set_attribute("caller.project_id", project_id)
                if agent_name:
                    span.set_attribute("caller.agent_name", agent_name)
                
                # Retrieve trace & span IDs from the active span
                span_context = span.get_span_context()
                if span_context and span_context.is_valid:
                    trace_id = format(span_context.trace_id, "032x")
                    span_id = format(span_context.span_id, "016x")
                    
                    # Safely resolve current Google Cloud Project
                    try:
                        _, auth_project = google.auth.default()
                    except Exception:
                        auth_project = None
                    project = os.environ.get("GOOGLE_CLOUD_PROJECT", auth_project or "unknown-project")
                    
                    # Output a structured JSON log to stdout with the valid trace & span context!
                    log_entry = {
                        "message": f"Injecting caller context as OTel baggage: {{'project_id': '{project_id}', 'agent_name': '{agent_name}'}}",
                        "caller_context": {
                            "project_id": project_id,
                            "agent_name": agent_name
                        },
                        "project_id": project_id,
                        "agent_name": agent_name,
                        "logging.googleapis.com/trace": f"projects/{project}/traces/{trace_id}",
                        "logging.googleapis.com/spanId": span_id
                    }
                    print(json.dumps(log_entry), flush=True)
        except Exception as e:
            # Silently pass to ensure tracing never breaks the core application flow
            pass

try:
    # We extend AdkApp to inject custom initialization logic
    # when the Agent Engine instance starts up in the cloud.
    class AgentEngineApp(agent_engines.AdkApp):
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

            # --- Custom Baggage-to-Span-Attribute Linker ---
            try:
                from opentelemetry import trace
                provider = trace.get_tracer_provider()
                if hasattr(provider, "add_span_processor"):
                    provider.add_span_processor(BaggageSpanProcessor())
                    logger.info("✅ BaggageSpanProcessor successfully registered on global TracerProvider.")
            except Exception as e:
                logger.error(f"Failed to register BaggageSpanProcessor: {e}")

            # --- Custom Dynatrace Telemetry Integration (Modularized) ---
            try:
                from utils.dynatrace import setup_dynatrace_exporter
                setup_dynatrace_exporter()
            except Exception as e:
                logger.error(f"Failed to load Dynatrace telemetry module: {e}")

        # --- Synchronous Execution Hooks ---

        def query(self, *args, **kwargs):
            caller_context = kwargs.pop("caller_context", None)
            token = self._inject_baggage(caller_context)
            try:
                return super().query(*args, **kwargs)
            finally:
                self._detach_baggage(token)
                self._flush_telemetry()

        def stream_query(self, *args, **kwargs):
            caller_context = kwargs.pop("caller_context", None)
            token = self._inject_baggage(caller_context)
            try:
                for event in super().stream_query(*args, **kwargs):
                    yield event
            finally:
                self._detach_baggage(token)
                self._flush_telemetry()

        # --- Asynchronous Execution Hooks ---

        async def async_query(self, *args, **kwargs):
            caller_context = kwargs.pop("caller_context", None)
            token = self._inject_baggage(caller_context)
            try:
                return await super().async_query(*args, **kwargs)
            finally:
                self._detach_baggage(token)
                self._flush_telemetry()

        async def async_stream_query(self, *args, **kwargs):
            caller_context = kwargs.pop("caller_context", None)
            token = self._inject_baggage(caller_context)
            try:
                async for event in super().async_stream_query(*args, **kwargs):
                    yield event
            finally:
                self._detach_baggage(token)
                self._flush_telemetry()

        # --- Context Injection Helpers ---

        def _inject_baggage(self, caller_context: dict) -> any:
            if not caller_context:
                return None
            try:
                from opentelemetry import baggage
                from opentelemetry.context import attach

                ctx = baggage.set_baggage("caller.project_id", caller_context.get("project_id", "unknown"))
                ctx = baggage.set_baggage("caller.agent_name", caller_context.get("agent_name", "unknown"), context=ctx)
                return attach(ctx)
            except Exception as e:
                logger.error(f"Failed to inject OTel baggage: {e}")
                return None

        def _detach_baggage(self, token):
            if token:
                try:
                    from opentelemetry.context import detach
                    detach(token)
                    logger.info("OTel baggage detached successfully.")
                except Exception as e:
                    logger.error(f"Failed to detach OTel baggage: {e}")

        # --- Private Telemetry Flush Helper ---

        def _flush_telemetry(self):
            try:
                logger.info("Executing post-execution telemetry flush to Google Cloud...")
                from opentelemetry import trace, _logs
                
                # 1. Flush Traces (GCP Trace)
                trace_provider = trace.get_tracer_provider()
                if hasattr(trace_provider, "force_flush"):
                    trace_provider.force_flush()
                    logger.info("✅ Trace telemetry successfully flushed.")
                else:
                    logger.warning("Tracer provider does not support force_flush.")
                    
                # 2. Flush Logs (GCP Logs)
                log_provider = _logs.get_logger_provider()
                if hasattr(log_provider, "force_flush"):
                    log_provider.force_flush()
                    logger.info("✅ Log telemetry successfully flushed.")
                else:
                    logger.warning("Logger provider does not support force_flush.")
                    
            except Exception as e:
                logger.error(f"⚠️ Failed to flush telemetry: {e}", exc_info=True)

    # Wrap the root agent for Vertex AI Agent Engine deployment
    adk_app = AgentEngineApp(
        agent=root_agent
    )

    # Standard ADK App instance
    app = App(
        name="base_adk_agent",
        root_agent=root_agent,
    )

    logger.info("✨ Agent Engine App initialized successfully.")

except Exception as e:
    logger.error(f"❌ CRITICAL ERROR initializing agent_app.py: {e}")
    traceback.print_exc(file=sys.stdout)
    raise e