# Copyright 2026 Google LLC
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

"""Documented limits of Gemini model families, checked when an agent starts."""

import logging

logger = logging.getLogger(__name__)

MODEL_LIMITS = {
    # https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-7-flash
    "gemini-3.7-flash": {
        "max_output_tokens": 65536,
        "thinking_levels": ("LOW", "MEDIUM", "HIGH"),
        "locations": ("global", "us", "eu"),
    },
}


def check_model_settings(model, thinking=None, max_output_tokens=None, location=None):
    limits = MODEL_LIMITS.get(model)
    if limits is None:
        logger.warning("No documented limits for model %r: settings not checked", model)
        return
    level = (thinking or {}).get("thinking_level")
    if level is not None and level not in limits["thinking_levels"]:
        raise ValueError(f"{model} does not support thinking level {level}")
    if max_output_tokens is not None and max_output_tokens > limits["max_output_tokens"]:
        raise ValueError(f"{model} supports at most {limits['max_output_tokens']} output tokens")
    if location is not None and location not in limits["locations"]:
        raise ValueError(f"{model} does not support location {location!r}")
