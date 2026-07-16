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

"""Client Patch Interceptor to monkeypatch google.genai.Client for global model endpoints and project defaulting."""

import os
import logging
from typing import Any

from interceptors.base import BaseInterceptor

logger = logging.getLogger(__name__)


class ClientPatchInterceptor(BaseInterceptor):
    """Monkeypatches google.genai.Client to respect MODEL_LOCATION and default projects."""

    def on_startup(self, app: Any) -> None:
        try:
            from google.genai import Client as GenAIClient
            original_client_init = GenAIClient.__init__

            def patched_client_init(self, *args, **kwargs):
                if "location" not in kwargs or kwargs["location"] is None:
                    kwargs["location"] = os.environ.get("MODEL_LOCATION", "global")
                if "project" not in kwargs or kwargs["project"] is None:
                    if "GOOGLE_CLOUD_PROJECT" in os.environ:
                        kwargs["project"] = os.environ["GOOGLE_CLOUD_PROJECT"]
                original_client_init(self, *args, **kwargs)

            GenAIClient.__init__ = patched_client_init
            logger.info("✅ Successfully monkeypatched google.genai.Client to respect MODEL_LOCATION=global.")
        except Exception as e:
            logger.error(f"Failed to patch google.genai.Client: {e}")
