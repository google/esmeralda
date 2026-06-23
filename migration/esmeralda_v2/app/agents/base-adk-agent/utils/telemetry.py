# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import logging
import google.auth
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from google.adk.telemetry.google_cloud import get_gcp_exporters, get_gcp_resource
from google.adk.telemetry.setup import maybe_set_otel_providers
from google.adk.cli.adk_web_server import (
    _setup_telemetry, 
    ApiServerSpanExporter, 
    InMemoryExporter
)

logger = logging.getLogger(__name__)

def setup_telemetry(custom_telemetry: bool = False):
    """
    Initializes telemetry using ADK internals if custom_telemetry is True.
    Otherwise, stays idle to allow Vertex AI managed tracing.
    """
    if not custom_telemetry:
        return
    
    trace_dict = {}
    session_trace_dict = {}
    
    internal_processors = [
        SimpleSpanProcessor(ApiServerSpanExporter(trace_dict)),
        SimpleSpanProcessor(InMemoryExporter(session_trace_dict)),
    ]

    _setup_telemetry(
        otel_to_cloud=True, # This tells ADK to use GCP exporters
        internal_exporters=internal_processors,
    )

    # # 5. IDENTITY & RESOURCE INITIALIZATION
    # credentials, project_id = google.auth.default()
    
    # # Resource attributes link traces specifically to the Vertex AI GenAI dashboard
    # otel_resource = get_gcp_resource(project_id)
    # # otel_resource = otel_resource.merge(Resource.create({
    # #     "gcp.resource_type": "reasoning_engine",
    # #     "gen_ai.system": "vertex_ai",
    # #     "service.name": os.environ.get("K_SERVICE", "adk-sql-agent")
    # # }))

    # # 6. EXPORTER & HOOK CONFIGURATION
    # # Cloud Logging MUST be enabled to carry the GenAI Events for bubbles
    # otel_hooks_to_add = [
    #     get_gcp_exporters(
    #         enable_cloud_tracing=True,
    #         enable_cloud_logging=True,
    #         enable_cloud_metrics=False,
    #         google_auth=(credentials, project_id),
    #     )
    # ]

    # # 7. APPLY PROVIDERS
    # maybe_set_otel_providers(
    #     otel_hooks_to_setup=otel_hooks_to_add,
    #     otel_resource=otel_resource,
    # )

    # # 8. TRIGGER SDK INSTRUMENTATION
    # _setup_instrumentation_lib_if_installed()