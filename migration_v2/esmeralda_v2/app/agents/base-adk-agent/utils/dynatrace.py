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

"""Module to set up Dynatrace OpenTelemetry tracing integration."""

import os
import logging

logger = logging.getLogger(__name__)

def setup_dynatrace_exporter() -> None:
    """Configures the standard OpenTelemetry provider to export traces to Dynatrace.
    
    Expects DYNATRACE_OTEL_TRACES_ENDPOINT and DYNATRACE_API_TOKEN to be set in
    the environment. If missing, it skips configuration gracefully.
    """
    dynatrace_endpoint = os.environ.get("DYNATRACE_OTEL_TRACES_ENDPOINT")
    dynatrace_token = os.environ.get("DYNATRACE_API_TOKEN")

    if not (dynatrace_endpoint and dynatrace_token):
        logger.info("ℹ️ Dynatrace environment variables not set or incomplete. Skipping Dynatrace trace integration.")
        return

    try:
        logger.info("Configuring custom Dynatrace OTLP/HTTP Trace Exporter...")
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        dt_exporter = OTLPSpanExporter(
            endpoint=dynatrace_endpoint,
            headers={"Authorization": f"Api-Token {dynatrace_token}"}
        )
        dt_processor = BatchSpanProcessor(dt_exporter)

        provider = trace.get_tracer_provider()
        if hasattr(provider, "add_span_processor"):
            provider.add_span_processor(dt_processor)
            logger.info("✅ Dynatrace SpanProcessor successfully added to standard TracerProvider.")
        else:
            logger.warning(f"⚠️ Unable to register Dynatrace Exporter: provider {type(provider)} lacks add_span_processor")
    except Exception as ex:
        logger.error(f"❌ Failed to register Dynatrace OTLP Exporter: {ex}")
