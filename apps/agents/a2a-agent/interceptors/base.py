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

"""Base Interceptor contract for application-level lifecycle and execution interception."""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class BaseInterceptor:
    """Base class for application-level lifecycle and execution interception."""

    def on_startup(self, app: Any) -> None:
        """Executed during AdkApp.set_up() to perform initializations or patching."""
        pass

    def before_query(self, kwargs: Dict[str, Any]) -> Optional[Any]:
        """Executed before a query starts. Can modify kwargs in-place and return a context token."""
        return None

    def after_query(self, token: Optional[Any]) -> None:
        """Executed after query completes (inside a guaranteed finally block)."""
        pass

    async def on_shutdown(self) -> None:
        """Executed during App teardown to clean up background resources."""
        pass
