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

"""Telemetry Flush Interceptor to ensure trace and log spans are force-flushed after every query."""

import logging
from typing import Optional, Any

from interceptors.base import BaseInterceptor

logger = logging.getLogger(__name__)


class TelemetryFlushInterceptor(BaseInterceptor):
    """Interceptor that triggers force_flush on OpenTelemetry trace and log providers after queries."""

    def after_query(self, token: Optional[Any]) -> None:
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
