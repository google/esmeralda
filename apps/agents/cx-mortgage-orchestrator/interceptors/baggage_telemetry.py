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

"""Baggage Telemetry Interceptor to propagate caller context into OpenTelemetry spans and logs."""

import os
import json
import logging
from typing import Any, Dict, Optional

from opentelemetry.sdk.trace import SpanProcessor, ReadableSpan
from opentelemetry import baggage

from interceptors.base import BaseInterceptor

logger = logging.getLogger(__name__)


class BaggageSpanProcessor(SpanProcessor):
    """
    Custom OpenTelemetry SpanProcessor that automatically copies active Baggage
    attributes (context variables) into Span Attributes (labels), ensuring they
    are exported to Google Cloud Trace and Dynatrace.
    """
    def on_start(self, span: ReadableSpan, parent_context=None) -> None:
        try:
            import google.auth

            project_id = baggage.get_baggage("caller.project_id", context=parent_context)
            agent_name = baggage.get_baggage("caller.agent_name", context=parent_context)

            if project_id or agent_name:
                if project_id:
                    span.set_attribute("caller.project_id", project_id)
                if agent_name:
                    span.set_attribute("caller.agent_name", agent_name)

                span_context = span.get_span_context()
                if span_context and span_context.is_valid:
                    trace_id = format(span_context.trace_id, "032x")
                    span_id = format(span_context.span_id, "016x")

                    try:
                        _, auth_project = google.auth.default()
                    except Exception:
                        auth_project = None
                    project = os.environ.get("GOOGLE_CLOUD_PROJECT", auth_project or "unknown-project")

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
        except Exception:
            pass


class BaggageTelemetryInterceptor(BaseInterceptor):
    """Interceptor that manages OTel baggage propagation and span processor registration."""

    def on_startup(self, app: Any) -> None:
        try:
            from opentelemetry import trace
            provider = trace.get_tracer_provider()
            if hasattr(provider, "add_span_processor"):
                provider.add_span_processor(BaggageSpanProcessor())
                logger.info("✅ BaggageSpanProcessor successfully registered on global TracerProvider.")
        except Exception as e:
            logger.error(f"Failed to register BaggageSpanProcessor: {e}")

    def before_query(self, kwargs: Dict[str, Any]) -> Optional[Any]:
        caller_context = kwargs.pop("caller_context", None)
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

    def after_query(self, token: Optional[Any]) -> None:
        if token:
            try:
                from opentelemetry.context import detach
                detach(token)
                logger.info("OTel baggage detached successfully.")
            except Exception as e:
                logger.error(f"Failed to detach OTel baggage: {e}")
