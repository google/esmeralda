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

import logging
from typing import Any, Dict, Optional
from interceptors.base import BaseInterceptor

logger = logging.getLogger(__name__)


class CallerContextInterceptor(BaseInterceptor):
    """Handles caller_context popping and distributed tracing context propagation via OTel baggage."""

    def before_query(self, kwargs: Dict[str, Any]) -> Optional[Any]:
        caller_context = kwargs.pop("caller_context", None)
        if not caller_context:
            return None
        try:
            logger.info(f"CallerContextInterceptor: Injecting OTel baggage: {caller_context}")
            from opentelemetry import baggage
            from opentelemetry.context import attach

            ctx = baggage.set_baggage("caller.project_id", caller_context.get("project_id", "unknown"))
            ctx = baggage.set_baggage("caller.agent_name", caller_context.get("agent_name", "unknown"), context=ctx)
            return attach(ctx)
        except Exception as e:
            logger.error(f"CallerContextInterceptor: Failed to inject baggage: {e}")
            return None

    def after_query(self, token: Optional[Any]) -> None:
        if token:
            try:
                from opentelemetry.context import detach
                detach(token)
                logger.info("CallerContextInterceptor: OTel baggage detached.")
            except ValueError as e:
                if "different Context" in str(e):
                    logger.debug(
                        f"CallerContextInterceptor: Baggage detach bypassed "
                        f"due to thread/task context transition: {e}"
                    )
                else:
                    logger.error(f"CallerContextInterceptor: Failed to detach baggage: {e}")
            except Exception as e:
                logger.error(f"CallerContextInterceptor: Failed to detach baggage: {e}")
